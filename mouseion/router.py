"""Capability router: patterns → embeddings → lexical, blended.

Nothing here is exotic. The design claims worth defending:

1. **Patterns run first.** `NCT04631731` should never be resolved by cosine
   similarity; a regex is exact and free.
2. **Lexical is not a fallback, it is half the score.** Embeddings blur
   `CFTC`, `openFDA` and ticker-like tokens. Blending keeps exact matches sharp
   while letting paraphrases through.
3. **The router may say nothing.** A provider index that always returns
   *something* is worse than one that admits it has no source, because the model
   will then answer from the wrong register with a citation attached.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from .cache import Cache, vector_key
from .embedders import Embedder, EmbeddingError
from .envelope import ProviderError
from .regions import detect_regions, region_conflict
from .registry import ProviderDoc, Registry, _tokens

log = logging.getLogger(__name__)

#: Weight on cosine similarity; lexical gets the remainder. Embeddings carry most of
#: the signal, but dropping lexical entirely costs two eval cases (exact identifiers).
ALPHA = 0.8
#: Minimum lexical score (fraction of query tokens matched) to keep a hit.
LEX_FLOOR = 0.25
#: Minimum cosine similarity to keep a hit with no lexical overlap.
#:
#: This number is model-specific and must be re-calibrated whenever the embedding
#: backend changes. bge-small-en-v1.5 scores ~0.45-0.55 cosine for unrelated short
#: text; a floor of 0.34 — sensible for hashed-vector stand-ins — let every negative
#: query through. Measured on eval/routing.jsonl: 0.34 -> 5 false positives, 0.55 -> 1.
COS_FLOOR = 0.55


@dataclass(slots=True)
class Candidate:
    provider: str
    capability: str
    score: float
    lexical: float = 0.0
    cosine: float = 0.0
    pattern: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "capability": self.capability,
            "score": round(self.score, 3),
            "lexical": round(self.lexical, 3),
            "cosine": round(self.cosine, 3),
            "pattern": self.pattern,
        }


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


class Router:
    def __init__(
        self,
        registry: Registry,
        cache: Cache,
        embedder: Embedder | None = None,
        *,
        alpha: float = ALPHA,
        cos_floor: float = COS_FLOOR,
        lex_floor: float = LEX_FLOOR,
        query_prefix: str = "",
        doc_prefix: str = "",
    ):
        self.registry = registry
        self.cache = cache
        self.embedder = embedder
        self.docs = registry.documents()
        self.degraded: str | None = None
        self._vectors: dict[str, list[float]] = {}
        self.alpha = alpha
        self.cos_floor = cos_floor
        self.lex_floor = lex_floor
        self.query_prefix = query_prefix
        self.doc_prefix = doc_prefix

    # -- public ---------------------------------------------------------

    async def route(
        self, query: str, category: str | None = None, limit: int = 8
    ) -> list[Candidate]:
        docs = self.registry.documents(category)
        if not docs:
            return []

        pattern_hits = self._pattern_hits(docs, query)
        if not _tokens(query) and not pattern_hits:
            return []
        lexical = self.registry.lexical_scores(query)
        cosines = await self._cosines(query, docs) if self.embedder else {}
        # Without an embedding signal, do not dilute the score by a weight that
        # contributed nothing: report lexical similarity directly.
        blended = bool(cosines)

        best: dict[str, Candidate] = {}
        for doc in docs:
            key = (doc.provider, doc.capability)
            lex = lexical.get(key, 0.0)
            cos = cosines.get(doc, 0.0)
            hit = key in pattern_hits

            if hit:
                candidate = Candidate(doc.provider, doc.capability, 1.0, lex, cos, True)
            else:
                if cos < self.cos_floor and lex < self.lex_floor:
                    continue
                score = self.alpha * cos + (1.0 - self.alpha) * lex if blended else lex
                candidate = Candidate(doc.provider, doc.capability, score, lex, cos, False)

            current = best.get(doc.provider)
            if current is None or candidate.score > current.score:
                best[doc.provider] = candidate

        ordered = sorted(best.values(), key=lambda c: (-c.score, c.provider))
        query_regions = detect_regions(query)
        if query_regions:
            ordered = [
                candidate
                for candidate in ordered
                if not region_conflict(
                    self.registry.manifests[candidate.provider].regions, query_regions
                )
            ]
        return ordered[:limit]

    async def search(
        self, query: str, category: str | None = None, limit: int = 8
    ) -> list[dict[str, Any]]:
        if not _tokens(query) and not self._any_pattern(category, query):
            return self.registry.search(query, category=category, limit=limit)

        candidates = await self.route(query, category, limit)
        ranked: list[tuple[float, ProviderDoc]] = []
        by_key = {(d.provider, d.capability): d for d in self.docs}
        for candidate in candidates:
            doc = by_key.get((candidate.provider, candidate.capability))
            if doc is not None:
                ranked.append((candidate.score, doc))
        return self.registry.cards(ranked, limit)

    async def prime(self) -> int:
        """Embed every document up front. Returns how many docs are indexed."""
        if self.embedder is None:
            raise ProviderError("no embedding backend configured", hint="set MOUSEION_EMBED_URL")
        await self._ensure_vectors(self.docs)
        return len(self.docs)

    def status(self) -> dict[str, Any]:
        return {
            "documents": len(self.docs),
            "providers": len(self.registry.manifests),
            "embedder": self.embedder.identity if self.embedder else None,
            "vectors_cached": self.cache.vector_count(self.embedder.identity)
            if self.embedder
            else 0,
            "degraded": self.degraded,
            "alpha": self.alpha,
            "lex_floor": self.lex_floor,
            "cos_floor": self.cos_floor,
            "query_prefix": self.query_prefix or None,
            "doc_prefix": self.doc_prefix or None,
        }

    async def aclose(self) -> None:
        if self.embedder is not None:
            await self.embedder.aclose()

    # -- internals ------------------------------------------------------

    def _pattern_hits(self, docs: list[ProviderDoc], query: str) -> set[tuple[str, str]]:
        return {
            (doc.provider, doc.capability)
            for doc in docs
            if any(pattern.search(query) for pattern in doc.patterns)
        }

    def _any_pattern(self, category: str | None, query: str) -> bool:
        return bool(self._pattern_hits(self.registry.documents(category), query))

    def _key(self, text: str) -> str:
        return vector_key(self.embedder.identity, text)  # type: ignore[union-attr]

    async def _ensure_vectors(self, docs: list[ProviderDoc]) -> dict[ProviderDoc, list[float]]:
        assert self.embedder is not None
        identity = self.embedder.identity
        keys = {doc: vector_key(identity, self.doc_prefix + doc.text) for doc in docs}

        unknown = [doc for doc, key in keys.items() if key not in self._vectors]
        if unknown:
            from_cache = self.cache.get_vectors(identity, [keys[doc] for doc in unknown])
            for doc in unknown:
                vector = from_cache.get(keys[doc])
                if vector is not None:
                    self._vectors[keys[doc]] = vector

        fresh = [doc for doc in unknown if keys[doc] not in self._vectors]
        if fresh:
            vectors = await self.embedder.embed([self.doc_prefix + doc.text for doc in fresh])
            if len(vectors) != len(fresh):
                raise EmbeddingError(
                    f"expected {len(fresh)} vectors, got {len(vectors)}"
                )
            normalized = {keys[doc]: _normalize(vector) for doc, vector in zip(fresh, vectors)}
            self.cache.put_vectors(identity, normalized)
            self._vectors.update(normalized)

        return {doc: self._vectors[keys[doc]] for doc in docs}

    async def _cosines(self, query: str, docs: list[ProviderDoc]) -> dict[ProviderDoc, float]:
        if self.degraded or self.embedder is None:
            return {}
        try:
            vectors = await self._ensure_vectors(docs)
            query_vector = _normalize((await self.embedder.embed([self.query_prefix + query]))[0])
        except EmbeddingError as exc:
            self.degraded = str(exc)
            log.warning("router degraded to lexical-only: %s", exc)
            return {}

        out: dict[ProviderDoc, float] = {}
        for doc, vector in vectors.items():
            if len(vector) != len(query_vector):
                self.degraded = f"embedding dim mismatch: {len(vector)} vs {len(query_vector)}"
                log.warning("router degraded: %s", self.degraded)
                return {}
            out[doc] = sum(a * b for a, b in zip(query_vector, vector))
        return out
