"""Tests for the failover engine.

These use synthetic manifests and a fake client, so they assert policy rather than
upstream data: valid empty is a success, failover stays inside a cluster, a region
conflict never calls anything, and a missing Tier 2 key is skipped before the wire.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from mouseion.config import Settings
from mouseion.engine import (
    CanonicalQuery,
    CircuitBreaker,
    Engine,
    TokenBucket,
)
from mouseion.envelope import Envelope, ProviderError, Record, SourceStatus, utcnow
from mouseion.manifest import Manifest
from mouseion.registry import Registry
from mouseion.router import Candidate


def run(coro):
    return asyncio.run(coro)


def manifest(
    pid: str,
    *,
    cluster: str | None = "demo_search",
    primary: bool = True,
    regions: list[str] | None = None,
    tier: int = 1,
    auth_type: str = "none",
    env: str | None = None,
    fields: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    query_map: dict[str, str] | None = None,
) -> Manifest:
    data: dict[str, Any] = {
        "id": pid,
        "title": pid.title(),
        "domain": "developer_infrastructure",
        "sub_category": "demo",
        "base": f"https://{pid}.example.gov",
        "docs_url": f"https://{pid}.example.gov/docs",
        "attribution": "Example Agency",
        "regions": regions or [],
        "cache_ttl": 3600,
        "auth": {"type": auth_type},
        "source": {
            "is_primary": primary,
            "originator": "Example Agency",
            "originator_domain": f"{pid}.example.gov",
            "provenance": "official" if primary else "aggregator_exception",
            "tier": tier,
            "auth_policy": {
                "free": True,
                "credit_card_required": False,
                "trial": False,
                "evidence_url": f"https://{pid}.example.gov/docs",
                "verified_at": "2026-10-29",
            },
            "rate_limit": {"per_min": 6000, "burst": 100, "concurrency": 10},
        },
        "capabilities": {
            "search": {
                "description": "Demo search.",
                "path": "/search",
                "params": params
                or {"q": {"type": "string"}, "limit": {"type": "integer"}},
                "cluster": cluster,
                "query_map": (
                    query_map
                    if query_map is not None
                    else {"text": "q", "limit": "limit"}
                ),
                **({"fields": fields} if fields else {}),
            }
        },
    }
    if not primary:
        data["source"]["exception_reason"] = "secondary index"
    if auth_type == "api_key":
        data["auth"]["env"] = env or "ENGINE_TEST_KEY"
    return Manifest.model_validate(data)


def envelope(provider: str, *, records: int = 1, data: dict[str, Any] | None = None) -> Envelope:
    return Envelope(
        provider=provider,
        capability="search",
        source_status=SourceStatus(
            url=f"https://{provider}.example.gov/search",
            http=200,
            fetched_at=utcnow(),
        ),
        records=[
            Record(retrieved_at=utcnow(), data=data if data is not None else {"title": "x"})
            for _ in range(records)
        ],
    )


class FakeClient:
    def __init__(self, outcomes: dict[str, Any]):
        self.outcomes = outcomes
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call(
        self,
        provider: str,
        capability: str,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
        *,
        refresh: bool = False,
    ):
        self.calls.append((provider, capability, dict(params or {})))
        outcome = self.outcomes[provider]
        if isinstance(outcome, list):
            outcome = outcome.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome(provider, capability, params or {})
        return outcome


class FakeRouter:
    def __init__(self, candidates: list[Candidate]):
        self.candidates = candidates

    async def route(self, query: str, category: str | None = None, limit: int = 8):
        return list(self.candidates)


def make_engine(
    manifests: list[Manifest],
    candidates: list[Candidate],
    outcomes: dict[str, Any],
    *,
    config: Settings | None = None,
    **kwargs: Any,
) -> tuple[Engine, FakeClient]:
    registry = Registry({m.id: m for m in manifests})
    client = FakeClient(outcomes)
    cfg = config or Settings(negative_cache_s=0, rate_limit_max_wait_s=0)
    return (
        Engine(
            registry,
            client,  # type: ignore[arg-type]
            FakeRouter(candidates),  # type: ignore[arg-type]
            config=cfg,
            **kwargs,
        ),
        client,
    )


def test_valid_empty_is_a_success_and_does_not_fail_over():
    alpha, beta = manifest("alpha"), manifest("beta")
    engine, client = make_engine(
        [alpha, beta],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": envelope("alpha", records=0), "beta": envelope("beta")},
    )
    result = run(engine.lookup("anything"))

    assert result.web_search_required is False
    assert result.envelope is not None and result.envelope.provider == "alpha"
    assert [attempt.outcome for attempt in result.attempts] == ["ok"]
    assert [call[0] for call in client.calls] == ["alpha"]


def test_429_fails_over_to_the_next_cluster_member():
    alpha, beta = manifest("alpha"), manifest("beta")
    engine, client = make_engine(
        [alpha, beta],
        [Candidate("alpha", "search", 1.0)],
        {
            "alpha": ProviderError("rate limited", status=429),
            "beta": envelope("beta"),
        },
    )
    result = run(engine.lookup("anything"))

    assert result.web_search_required is False
    assert result.envelope is not None and result.envelope.provider == "beta"
    assert [attempt.outcome for attempt in result.attempts] == ["retryable", "ok"]
    assert [call[0] for call in client.calls] == ["alpha", "beta"]


def test_cross_cluster_failure_does_not_fail_over():
    alpha = manifest("alpha", cluster="alpha_cluster")
    gamma = manifest("gamma", cluster="gamma_cluster")
    engine, client = make_engine(
        [alpha, gamma],
        [Candidate("alpha", "search", 1.0), Candidate("gamma", "search", 0.9)],
        {"alpha": ProviderError("server error", status=500), "gamma": envelope("gamma")},
    )
    result = run(engine.lookup("anything"))

    assert result.web_search_required is True
    assert [attempt.provider for attempt in result.attempts] == ["alpha"]
    assert [call[0] for call in client.calls] == ["alpha", "alpha"]  # one retry


def test_region_conflict_never_calls_a_source():
    alpha = manifest("alpha", regions=["US"])
    engine, client = make_engine(
        [alpha],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": envelope("alpha")},
    )
    result = run(engine.lookup("hospital ratings in Germany"))

    assert result.web_search_required is True
    assert result.reason == "no_candidates"
    assert client.calls == []


def test_missing_tier_two_key_is_skipped_before_the_wire(monkeypatch):
    secure = manifest("secure", tier=2, auth_type="api_key", env="ENGINE_TEST_KEY")
    monkeypatch.delenv("ENGINE_TEST_KEY", raising=False)
    engine, client = make_engine(
        [secure],
        [Candidate("secure", "search", 1.0)],
        {"secure": envelope("secure")},
    )
    result = run(engine.lookup("anything"))

    assert result.web_search_required is True
    assert result.attempts[0].outcome == "missing_credential"
    assert client.calls == []


def test_circuit_breaker_opens_after_repeated_failures():
    alpha = manifest("alpha")
    engine, client = make_engine(
        [alpha],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": ProviderError("server error", status=500)},
        breaker=CircuitBreaker(threshold=1, cooldown_s=60, clock=lambda: 0.0),
    )

    first = run(engine.lookup("anything"))
    second = run(engine.lookup("anything"))

    assert first.attempts[0].outcome == "retryable"
    assert second.attempts[0].outcome == "circuit_open"
    assert len(client.calls) == 2  # first attempt plus its one retry


def test_terminal_parameter_error_stops_failover():
    alpha, beta = manifest("alpha"), manifest("beta")
    engine, client = make_engine(
        [alpha, beta],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": ProviderError("bad param", status=400), "beta": envelope("beta")},
    )
    result = run(engine.lookup("anything"))

    assert result.web_search_required is True
    assert [attempt.outcome for attempt in result.attempts] == ["terminal"]
    assert [call[0] for call in client.calls] == ["alpha"]


def test_schema_break_fails_over():
    alpha = manifest("alpha", fields={"title": "title"})
    beta = manifest("beta")
    engine, _ = make_engine(
        [alpha, beta],
        [Candidate("alpha", "search", 1.0)],
        {
            "alpha": envelope("alpha", records=1, data={}),
            "beta": envelope("beta"),
        },
    )
    result = run(engine.lookup("anything"))

    assert result.web_search_required is False
    assert result.envelope is not None and result.envelope.provider == "beta"
    assert [attempt.outcome for attempt in result.attempts] == ["schema", "ok"]


def test_aggregator_is_not_used_automatically_but_can_be_explicit():
    agg = manifest("agg", cluster=None, primary=False)
    engine, client = make_engine(
        [agg],
        [Candidate("agg", "search", 1.0)],
        {"agg": envelope("agg")},
    )

    automatic = run(engine.lookup("anything"))
    assert automatic.web_search_required is True
    assert automatic.reason == "no_primary_candidates"
    assert automatic.aggregator_available is True
    assert client.calls == []

    explicit = run(engine.lookup("anything", provider="agg"))
    assert explicit.web_search_required is False
    assert explicit.envelope is not None and explicit.envelope.provider == "agg"


def test_no_candidates_returns_a_clean_signal():
    engine, client = make_engine([], [], {})
    result = run(engine.lookup("anything"))

    assert result.web_search_required is True
    assert result.reason == "no_candidates"
    assert result.attempts == []
    assert client.calls == []


def test_missing_required_param_is_reported_without_calling():
    alpha = manifest(
        "alpha",
        params={"required_thing": {"type": "string", "required": True}},
        query_map={},
    )
    engine, client = make_engine(
        [alpha],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": envelope("alpha")},
    )
    result = run(engine.lookup("anything"))

    assert result.web_search_required is True
    assert result.reason == "missing_required_param"
    assert result.attempts[0].outcome == "unmapped_query"
    assert client.calls == []


def test_query_map_translates_canonical_fields():
    alpha = manifest("alpha")
    engine, _ = make_engine(
        [alpha],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": envelope("alpha")},
    )
    query = CanonicalQuery(text="anything", limit=7)
    params, missing = engine._params_for(alpha.capabilities["search"], query)
    assert params == {"q": "anything", "limit": 7}
    assert missing == []


def test_identifier_is_extracted_for_an_exact_capability():
    alpha = manifest(
        "alpha",
        cluster=None,
        params={"nct_id": {"type": "string", "required": True}},
        query_map={},
    )
    engine, client = make_engine(
        [alpha],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": envelope("alpha")},
    )
    result = run(engine.lookup("trial NCT04631731", provider="alpha", capability="search"))

    assert result.web_search_required is False
    assert client.calls == [("alpha", "search", {"nct_id": "NCT04631731"})]


def test_source_specific_params_require_an_explicit_provider():
    alpha = manifest("alpha")
    engine, client = make_engine(
        [alpha],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": envelope("alpha")},
    )
    result = run(engine.lookup("anything", params={"q": "x"}))

    assert result.web_search_required is True
    assert result.reason == "params_require_provider"
    assert client.calls == []


def test_explicit_provider_does_not_fail_over():
    alpha, beta = manifest("alpha"), manifest("beta")
    engine, client = make_engine(
        [alpha, beta],
        [Candidate("alpha", "search", 1.0)],
        {
            "alpha": ProviderError("server error", status=500),
            "beta": envelope("beta"),
        },
    )
    result = run(engine.lookup("anything", provider="alpha"))

    assert result.web_search_required is True
    assert [attempt.provider for attempt in result.attempts] == ["alpha"]
    assert [call[0] for call in client.calls] == ["alpha", "alpha"]  # one retry


def test_negative_cache_short_circuits_repeated_failures():
    alpha = manifest("alpha")
    engine, client = make_engine(
        [alpha],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": ProviderError("rate limited", status=429)},
        config=Settings(negative_cache_s=30, rate_limit_max_wait_s=0),
    )

    first = run(engine.lookup("anything"))
    second = run(engine.lookup("anything"))

    assert first.attempts[0].outcome == "retryable"
    assert second.attempts[0].outcome == "negative_cache:retryable"
    assert len(client.calls) == 1  # 429 is not retried within the source


def test_engine_overhead_stays_under_50ms_per_lookup():
    alpha = manifest("alpha")
    engine, _ = make_engine(
        [alpha],
        [Candidate("alpha", "search", 1.0)],
        {"alpha": envelope("alpha")},
    )

    async def scenario():
        for _ in range(50):
            result = await engine.lookup("anything")
            assert result.web_search_required is False

    started = time.perf_counter()
    run(scenario())
    per_lookup_ms = (time.perf_counter() - started) * 1000 / 50
    assert per_lookup_ms < 50


def test_token_bucket_refuses_a_wait_beyond_its_budget():
    async def scenario():
        bucket = TokenBucket(per_min=60, burst=1, max_wait_s=0.1)
        first = await bucket.acquire()
        bucket.tokens = 0.0
        bucket.updated = bucket.clock()
        second = await bucket.acquire()
        return first, second

    first, second = run(scenario())
    assert first == 0.0
    assert second is None


def test_single_flight_collapses_concurrent_identical_calls():
    alpha = manifest("alpha")

    class SlowClient(FakeClient):
        def __init__(self):
            super().__init__({"alpha": envelope("alpha")})
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def call(self, provider, capability, params=None, cursor=None, *, refresh=False):
            self.calls.append((provider, capability, dict(params or {})))
            self.started.set()
            await self.release.wait()
            return self.outcomes[provider]

    registry = Registry({"alpha": alpha})
    client = SlowClient()
    engine = Engine(
        registry,
        client,  # type: ignore[arg-type]
        FakeRouter([Candidate("alpha", "search", 1.0)]),  # type: ignore[arg-type]
        config=Settings(negative_cache_s=0, rate_limit_max_wait_s=0),
    )

    async def scenario():
        first = asyncio.create_task(engine.lookup("anything"))
        second = asyncio.create_task(engine.lookup("anything"))
        await asyncio.wait_for(client.started.wait(), timeout=1.0)
        await asyncio.sleep(0.01)
        client.release.set()
        return await asyncio.gather(first, second)

    results = run(scenario())
    assert len(client.calls) == 1
    assert all(result.web_search_required is False for result in results)


def test_uncovered_jurisdiction_reports_the_gap():
    from mouseion.civic import Jurisdiction, JurisdictionIndex

    alpha = manifest("alpha")
    engine, client = make_engine([alpha], [], {"alpha": envelope("alpha")})
    engine.router.jurisdictions = JurisdictionIndex(
        [
            Jurisdiction(
                id="US-IL-CHICAGO",
                name="Chicago",
                level="city",
                state="IL",
            )
        ]
    )

    result = run(engine.lookup("who is running for city council in Chicago"))

    assert result.web_search_required is True
    assert result.reason == "no_jurisdiction_coverage"
    assert result.jurisdictions == ("US-IL-CHICAGO",)
    assert client.calls == []


def test_a_weak_primary_does_not_replace_an_aggregator():
    agg = manifest("agg", cluster=None, primary=False)
    primary = manifest("primary")
    engine, client = make_engine(
        [agg, primary],
        [Candidate("agg", "search", 1.0), Candidate("primary", "search", 0.2)],
        {"agg": envelope("agg"), "primary": envelope("primary")},
    )

    result = run(engine.lookup("anything"))

    assert result.web_search_required is True
    assert result.reason == "no_primary_candidates"
    assert result.aggregator_available is True
    assert client.calls == []


def test_a_competitive_primary_can_stand_in_for_an_aggregator():
    agg = manifest("agg", cluster=None, primary=False)
    primary = manifest("primary")
    engine, client = make_engine(
        [agg, primary],
        [Candidate("agg", "search", 0.5), Candidate("primary", "search", 0.45)],
        {"agg": envelope("agg"), "primary": envelope("primary")},
    )

    result = run(engine.lookup("anything"))

    assert result.web_search_required is False
    assert result.envelope is not None and result.envelope.provider == "primary"
    assert client.calls and client.calls[0][0] == "primary"
