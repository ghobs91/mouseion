from __future__ import annotations

import asyncio

import pytest

from mouseion.cache import Cache
from mouseion.embedders import EmbeddingError
from mouseion.evaluate import evaluate, load_cases
from mouseion.registry import Registry
from mouseion.router import Router

TOPIC = {"recall": 0, "medicine": 0, "trial": 1, "rule": 2, "futures": 3}


class TopicEmbedder:
    """Deterministic 4-dim one-hot by topic, so cosine is exactly 0 or 1.

    Not a real model: it exists to prove the router uses cosine at all, and that
    a query with zero lexical overlap can still reach the right provider.
    """

    identity = "fake:topic-v1"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out = []
        for text in texts:
            vector = [0.0, 0.0, 0.0, 0.0]
            lowered = text.lower()
            for word, index in TOPIC.items():
                if word in lowered:
                    vector[index] = 1.0
            out.append(vector)
        return out

    async def aclose(self) -> None:
        return None


class BrokenEmbedder:
    identity = "broken"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingError("backend down")

    async def aclose(self) -> None:
        return None


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def registry() -> Registry:
    from pathlib import Path

    return Registry.load(Path(__file__).resolve().parent.parent / "providers")


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "router.db")


def test_pattern_beats_everything(registry, cache):
    router = Router(registry, cache)
    candidates = run(router.route("what is NCT04631731"))
    assert candidates[0].provider == "clinicaltrials_gov"
    assert candidates[0].capability == "get"
    assert candidates[0].pattern is True
    assert candidates[0].score == 1.0


def test_lexical_misses_a_zero_overlap_paraphrase(registry, cache):
    router = Router(registry, cache)
    providers = [c.provider for c in run(router.route("medicine removed by regulators"))]
    assert "openfda" not in providers


def test_embeddings_rescue_a_zero_overlap_paraphrase(registry, cache):
    router = Router(registry, cache, TopicEmbedder())
    candidates = run(router.route("medicine removed by regulators"))

    assert candidates, "cosine similarity should have surfaced openfda"
    rescued = [c for c in candidates if c.provider == "openfda"]
    assert rescued and rescued[0].cosine == pytest.approx(1.0)
    assert rescued[0].pattern is False


def test_unrelated_query_still_returns_nothing_with_embeddings(registry, cache):
    router = Router(registry, cache, TopicEmbedder())
    assert run(router.route("how do I bake sourdough bread")) == []


def test_vectors_are_cached_by_content_and_model(registry, cache):
    first = TopicEmbedder()
    run(Router(registry, cache, first).route("recall"))
    assert len(first.calls) == 2  # one batch of docs, one query

    second = TopicEmbedder()
    run(Router(registry, cache, second).route("recall"))
    assert len(second.calls) == 1, "documents should come from the vector cache"
    assert len(second.calls[0]) == 1, "only the query should be embedded"


def test_broken_backend_degrades_instead_of_failing(registry, cache):
    router = Router(registry, cache, BrokenEmbedder())
    candidates = run(router.route("drug recalls"))
    assert candidates[0].provider == "openfda"
    assert router.degraded
    assert candidates[0].cosine == 0.0


def test_search_orders_the_matched_capability_first(registry, cache):
    router = Router(registry, cache)
    cards = run(router.search("if there are drug recalls"))
    openfda = next(card for card in cards if card["id"] == "openfda")
    assert openfda["capabilities"][0]["name"] == "drug_recalls"


def test_eval_baseline_holds(registry, cache):
    report = run(evaluate(Router(registry, cache), load_cases()))
    assert report["total"] == 48
    assert report["top1"] >= 38, report["misses"]
    assert report["top3"] >= report["top1"]


class FixedCosineEmbedder:
    """Returns a fixed cosine against every document, to test floor calibration."""

    identity = "fixed-cosine"

    def __init__(self, cosine: float):
        norm = (1.0 - cosine * cosine) ** 0.5
        self.query_vector = [1.0, 0.0]
        self.doc_vector = [cosine, norm]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            self.query_vector if text == "calibration probe" else self.doc_vector
            for text in texts
        ]

    async def aclose(self) -> None:
        return None


def test_cosine_floor_rejects_background_similarity(registry, cache):
    """bge-small scores ~0.45 cosine for unrelated text; that must not count as a match."""
    router = Router(registry, cache, FixedCosineEmbedder(0.45))
    assert run(router.route("calibration probe")) == []


def test_cosine_floor_accepts_a_genuine_match(registry, cache):
    router = Router(registry, cache, FixedCosineEmbedder(0.60))
    assert run(router.route("calibration probe"))


def test_calibrated_defaults_are_the_measured_ones():
    from mouseion.router import ALPHA, COS_FLOOR, LEX_FLOOR

    assert (ALPHA, COS_FLOOR, LEX_FLOOR) == (0.8, 0.55, 0.25)
