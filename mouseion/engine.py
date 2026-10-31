"""Deterministic failover engine over primary-source capabilities.

The router answers "which capabilities might answer this?". The engine answers
"which one can actually answer it now, and what happened when we tried?".

Three rules are deliberately conservative:

1. Failover happens only inside a declared `cluster`. Sharing a category is not
   enough: FRED and the World Bank are not interchangeable.
2. A valid empty result is a success. "No Class I recalls" is an answer, not a
   reason to ask a different register.
3. An aggregator exception is never used automatically. It can be called
   explicitly, or with `allow_aggregators=True`, but it does not inherit the
   primary-source guarantee.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .cache import cache_key
from .client import ProviderClient
from .config import Settings
from .config import settings as default_settings
from .envelope import Envelope, ProviderError
from .manifest import Capability, Manifest
from .registry import Registry
from .regions import detect_regions, region_conflict
from .router import Candidate, Router

log = logging.getLogger(__name__)

#: Params a free-text capability may use when it has no explicit query_map.
TEXT_PARAMS = ("q", "query", "search", "term", "keyword", "search_text")
#: Params an identifier capability may use when it has no explicit query_map.
ID_PARAMS = ("id", "identifier", "cve_id", "nct_id", "doi", "cik", "pmid")

#: Identifier shapes the engine can lift out of a natural-language query.
_IDENTIFIER_PATTERNS = (
    re.compile(r"\bNCT\d{8}\b", re.IGNORECASE),
    re.compile(r"\b10\.\d{4,9}/[^\s,;]+", re.IGNORECASE),
    re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE),
    re.compile(r"\bCIK\s*\d{1,10}\b", re.IGNORECASE),
    re.compile(r"\bPMID[:\s]*\d+\b", re.IGNORECASE),
)


def extract_identifier(text: str) -> str | None:
    """Pull a known identifier out of a query, or return None.

    This is deliberately a small allowlist. A wrong identifier is worse than no
    identifier: it sends the engine to a precise endpoint with a value that cannot
    exist.
    """
    for pattern in _IDENTIFIER_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = match.group(0).rstrip(".,;:)")
        if value.upper().startswith("CIK"):
            return re.sub(r"\D", "", value)
        if value.upper().startswith("PMID"):
            return re.sub(r"\D", "", value)
        return value
    stripped = text.strip()
    return stripped if stripped.isdigit() else None

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
#: A caller-parameter error will not improve by asking a different source.
NO_FAILOVER_STATUS = {400, 422}


@dataclass(frozen=True, slots=True)
class CanonicalQuery:
    """The source-independent shape the engine translates through `query_map`."""

    text: str
    identifier: str | None = None
    entity: str | None = None
    geo: str | None = None
    time_range: tuple[str, str] | None = None
    limit: int = 10

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"text": self.text, "limit": self.limit}
        for key in ("identifier", "entity", "geo"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.time_range is not None:
            out["time_range"] = list(self.time_range)
        return out


@dataclass(frozen=True, slots=True)
class Attempt:
    """One source we tried, and why it did or did not answer."""

    provider: str
    capability: str
    cluster: str | None
    tier: int
    outcome: str
    ms: int
    status: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "provider": self.provider,
            "capability": self.capability,
            "cluster": self.cluster,
            "tier": self.tier,
            "outcome": self.outcome,
            "ms": self.ms,
        }
        if self.status is not None:
            out["http"] = self.status
        if self.error:
            out["error"] = self.error
        return out


@dataclass(slots=True)
class LookupResult:
    query: CanonicalQuery
    envelope: Envelope | None
    attempts: list[Attempt]
    web_search_required: bool
    reason: str | None = None
    aggregator_available: bool = False
    #: Jurisdictions named in the query that no provider covers.
    jurisdictions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "query": self.query.as_dict(),
            "web_search_required": self.web_search_required,
            "attempts": [attempt.as_dict() for attempt in self.attempts],
        }
        if self.envelope is not None:
            out["envelope"] = self.envelope.to_dict()
        if self.reason:
            out["reason"] = self.reason
        if self.aggregator_available:
            out["aggregator_available"] = True
        if self.jurisdictions:
            out["uncovered_jurisdictions"] = list(self.jurisdictions)
        return out


class TokenBucket:
    """Per-provider token bucket. Returns None when the wait exceeds the budget."""

    def __init__(
        self,
        per_min: int,
        burst: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_wait_s: float = 2.0,
    ):
        self.rate = max(per_min, 1) / 60.0
        self.capacity = float(max(burst, 1))
        self.tokens = self.capacity
        self.updated = clock()
        self.clock = clock
        self.max_wait_s = max_wait_s
        self._lock = asyncio.Lock()

    async def acquire(self) -> float | None:
        async with self._lock:
            now = self.clock()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return 0.0

            wait = (1.0 - self.tokens) / self.rate
            if wait > self.max_wait_s:
                return None
            await asyncio.sleep(wait)
            now = self.clock()
            self.tokens = max(
                0.0, self.tokens + (now - self.updated) * self.rate - 1.0
            )
            self.updated = now
            return wait


@dataclass(slots=True)
class _BreakerState:
    failures: int = 0
    opened_until: float = 0.0


class CircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int = 3,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._states: dict[str, _BreakerState] = {}

    def allow(self, provider: str) -> bool:
        state = self._states.get(provider)
        if state is None:
            return True
        now = self.clock()
        if state.opened_until and state.opened_until <= now:
            state.opened_until = 0.0
            state.failures = 0
            return True
        return state.opened_until == 0.0

    def record_success(self, provider: str) -> None:
        self._states.pop(provider, None)

    def record_failure(self, provider: str) -> None:
        state = self._states.setdefault(provider, _BreakerState())
        state.failures += 1
        if state.failures >= self.threshold:
            state.opened_until = self.clock() + self.cooldown_s
            state.failures = 0


class SingleFlight:
    """Collapse concurrent identical calls into one upstream request."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Task[Any]] = {}

    async def run(self, key: str, factory: Callable[[], Awaitable[Any]]) -> Any:
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(factory())
            self._inflight[key] = task
            try:
                return await asyncio.shield(task)
            finally:
                self._inflight.pop(key, None)
        return await asyncio.shield(task)


class Engine:
    def __init__(
        self,
        registry: Registry,
        client: ProviderClient,
        router: Router,
        *,
        config: Settings | None = None,
        max_attempts: int | None = None,
        allow_aggregators: bool | None = None,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.registry = registry
        self.client = client
        self.router = router
        self.config = config or default_settings
        self.max_attempts = max_attempts or self.config.engine_max_attempts
        self.allow_aggregators = (
            self.config.engine_allow_aggregators
            if allow_aggregators is None
            else allow_aggregators
        )
        self.breaker = breaker or CircuitBreaker(
            threshold=self.config.circuit_threshold,
            cooldown_s=self.config.circuit_cooldown_s,
        )
        self.clock = clock
        self._buckets: dict[str, TokenBucket] = {}
        self._negative: dict[tuple[str, str], tuple[float, str]] = {}
        self._single = SingleFlight()
        self._clusters = self._index_clusters()

    # -- public ---------------------------------------------------------

    async def lookup(
        self,
        query: str | CanonicalQuery,
        *,
        category: str | None = None,
        domain: str | None = None,
        provider: str | None = None,
        capability: str | None = None,
        params: dict[str, Any] | None = None,
        limit: int = 10,
        refresh: bool = False,
        allow_aggregators: bool | None = None,
    ) -> LookupResult:
        canonical = (
            query
            if isinstance(query, CanonicalQuery)
            else CanonicalQuery(
                text=query,
                identifier=extract_identifier(query),
                limit=max(1, min(limit, 100)),
            )
        )
        allow = self.allow_aggregators if allow_aggregators is None else allow_aggregators
        if provider is not None:
            # An explicit provider is an operator decision, including an aggregator.
            allow = True

        if params is not None and provider is None:
            # Source-specific params cannot be safely replayed across a cluster.
            return LookupResult(
                canonical,
                None,
                [],
                True,
                reason="params_require_provider",
            )

        candidates, aggregator_available = await self._candidates(
            canonical,
            category=category,
            domain=domain,
            provider=provider,
            capability=capability,
            allow_aggregators=allow,
        )
        if not candidates:
            uncovered = self._uncovered_jurisdictions(canonical.text)
            reason = (
                "no_jurisdiction_coverage"
                if uncovered
                else ("no_primary_candidates" if aggregator_available else "no_candidates")
            )
            return LookupResult(
                canonical,
                None,
                [],
                True,
                reason=reason,
                aggregator_available=aggregator_available,
                jurisdictions=uncovered,
            )

        attempts: list[Attempt] = []
        for candidate in candidates[: self.max_attempts]:
            manifest = self.registry.manifests[candidate.provider]
            cap = manifest.capabilities[candidate.capability]
            attempt, envelope = await self._attempt(
                manifest,
                cap,
                candidate,
                canonical,
                params=params,
                refresh=refresh,
            )
            attempts.append(attempt)
            if attempt.outcome == "ok" and envelope is not None:
                return LookupResult(canonical, envelope, attempts, False)
            if attempt.outcome == "terminal":
                break

        reason = self._reason(attempts)
        if not attempts and aggregator_available:
            reason = "no_primary_candidates"
        return LookupResult(
            canonical,
            None,
            attempts,
            True,
            reason=reason,
            aggregator_available=aggregator_available,
        )

    # -- candidate selection --------------------------------------------

    def _uncovered_jurisdictions(self, text: str) -> tuple[str, ...]:
        """Jurisdictions the query names that no provider claims to cover."""
        index = getattr(self.router, "jurisdictions", None)
        if index is None:
            return ()
        detected = index.detect(text)
        if not detected:
            return ()
        covered = any(
            index.covers(manifest.jurisdictions, detected)
            for manifest in self.registry.manifests.values()
            if manifest.jurisdictions
        )
        return () if covered else tuple(sorted(detected))

    def _index_clusters(self) -> dict[str, list[tuple[str, str]]]:
        clusters: dict[str, list[tuple[str, str]]] = {}
        for manifest in self.registry.manifests.values():
            for name, cap in manifest.capabilities.items():
                if cap.cluster:
                    clusters.setdefault(cap.cluster, []).append((manifest.id, name))
        return clusters

    async def _candidates(
        self,
        query: CanonicalQuery,
        *,
        category: str | None,
        domain: str | None,
        provider: str | None,
        capability: str | None,
        allow_aggregators: bool,
    ) -> tuple[list[Candidate], bool]:
        if provider is not None:
            try:
                manifest = self.registry.get(provider)
            except KeyError:
                return [], False
            cap_name = capability or next(iter(manifest.capabilities), None)
            if cap_name is None or cap_name not in manifest.capabilities:
                return [], False
            # An explicit provider is exact: no cluster expansion, no region filter.
            return [Candidate(manifest.id, cap_name, 1.0)], False
        else:
            candidates = await self.router.route(query.text, category=category, limit=10)

        if not candidates:
            return [], False

        if domain:
            candidates = [
                candidate
                for candidate in candidates
                if self.registry.manifests[candidate.provider].domain == domain
            ]
            if not candidates:
                return [], False

        aggregator_available = (not allow_aggregators) and any(
            not self.registry.manifests[candidate.provider].source.is_primary
            for candidate in candidates
        )

        query_regions = detect_regions(query.text)
        if query_regions:
            candidates = [
                candidate
                for candidate in candidates
                if not region_conflict(
                    self.registry.manifests[candidate.provider].regions, query_regions
                )
            ]
            if not candidates:
                return [], aggregator_available

        top = candidates[0]
        top_manifest = self.registry.manifests[top.provider]
        top_cap = top_manifest.capabilities[top.capability]

        if top_cap.cluster:
            scores = {(c.provider, c.capability): c.score for c in candidates}
            members: list[Candidate] = []
            for pid, cap_name in self._clusters.get(top_cap.cluster, []):
                manifest = self.registry.manifests[pid]
                if not allow_aggregators and not manifest.source.is_primary:
                    continue
                members.append(
                    Candidate(pid, cap_name, scores.get((pid, cap_name), 0.0))
                )
            members.sort(
                key=lambda c: (
                    not self.registry.manifests[c.provider].source.is_primary,
                    self.registry.manifests[c.provider].source.tier,
                    -c.score,
                    c.provider,
                )
            )
            if members:
                return members, aggregator_available
            return [], aggregator_available

        if allow_aggregators or top_manifest.source.is_primary:
            return [top], aggregator_available

        # The top hit is an aggregator with no cluster. Use the first primary hit
        # as a discovery fallback, but do not invent a failover group.
        primary = next(
            (
                candidate
                for candidate in candidates
                if self.registry.manifests[candidate.provider].source.is_primary
            ),
            None,
        )
        return ([primary], aggregator_available) if primary else ([], aggregator_available)

    # -- execution ------------------------------------------------------

    async def _attempt(
        self,
        manifest: Manifest,
        cap: Capability,
        candidate: Candidate,
        query: CanonicalQuery,
        *,
        params: dict[str, Any] | None,
        refresh: bool,
    ) -> tuple[Attempt, Envelope | None]:
        provider = manifest.id

        def result(outcome: str, ms: int = 0, **kwargs: Any) -> Attempt:
            return Attempt(
                provider=provider,
                capability=candidate.capability,
                cluster=cap.cluster,
                tier=manifest.source.tier,
                outcome=outcome,
                ms=ms,
                **kwargs,
            )

        if not self.breaker.allow(provider):
            return result("circuit_open"), None

        negative = self._negative.get((provider, candidate.capability))
        if negative is not None and negative[0] > self.clock():
            return result(f"negative_cache:{negative[1]}"), None

        if manifest.auth.type == "api_key" and not os.getenv(manifest.auth.env or ""):
            return (
                result(
                    "missing_credential",
                    error=f"set {manifest.auth.env}",
                ),
                None,
            )

        call_params: dict[str, Any] | None
        missing: list[str] = []
        if params is not None:
            call_params = dict(params)
        else:
            call_params, missing = self._params_for(cap, query)
        if call_params is None:
            detail = (
                f"missing required param(s): {', '.join(missing)}"
                if missing
                else "no query_map or free-text param can carry this query"
            )
            return (result("unmapped_query", error=detail), None)

        bucket = self._bucket(manifest)
        waited = await bucket.acquire()
        if waited is None:
            return result("rate_limited"), None

        key = (
            cache_key(provider, candidate.capability, {"query": call_params}, None)
            + f":{int(refresh)}"
        )
        started = self.clock()

        async def call() -> Envelope:
            try:
                return await self.client.call(
                    provider,
                    candidate.capability,
                    call_params,
                    refresh=refresh,
                )
            except ProviderError as exc:
                if self._should_retry(exc):
                    await asyncio.sleep(0.2)
                    return await self.client.call(
                        provider,
                        candidate.capability,
                        call_params,
                        refresh=refresh,
                    )
                raise

        try:
            envelope = await self._single.run(key, call)
        except ProviderError as exc:
            ms = int((self.clock() - started) * 1000)
            outcome = self._classify(exc)
            if outcome in {"retryable", "auth"}:
                self.breaker.record_failure(provider)
                self._negative[(provider, candidate.capability)] = (
                    self.clock() + self.config.negative_cache_s,
                    outcome,
                )
            return result(outcome, ms, status=exc.status, error=exc.message), None
        except Exception as exc:  # noqa: BLE001 - a tool must never raise into the transport
            ms = int((self.clock() - started) * 1000)
            self.breaker.record_failure(provider)
            return result("error", ms, error=f"{type(exc).__name__}: {exc}"), None

        ms = int((self.clock() - started) * 1000)
        if self._schema_broken(envelope, cap):
            self.breaker.record_failure(provider)
            return result("schema", ms, error="projection returned no fields"), None

        self.breaker.record_success(provider)
        self._negative.pop((provider, candidate.capability), None)
        return result("ok", ms), envelope

    def _params_for(
        self, cap: Capability, query: CanonicalQuery
    ) -> tuple[dict[str, Any] | None, list[str]]:
        params: dict[str, Any] = {}
        if cap.query_map:
            for canonical, source_param in cap.query_map.items():
                value = getattr(query, canonical, None)
                if value is None and canonical == "entity":
                    value = query.text
                if value is not None:
                    params[source_param] = value
        else:
            text_param = next((name for name in TEXT_PARAMS if name in cap.params), None)
            if text_param and query.text:
                params[text_param] = query.text
            elif query.identifier:
                id_param = next((name for name in ID_PARAMS if name in cap.params), None)
                if id_param:
                    params[id_param] = query.identifier

        if "limit" in cap.params:
            params.setdefault("limit", query.limit)

        missing = [
            name
            for name in cap.required_params()
            if params.get(name) in (None, "", [])
        ]
        return (None, missing) if missing else (params, [])

    def _bucket(self, manifest: Manifest) -> TokenBucket:
        bucket = self._buckets.get(manifest.id)
        if bucket is None:
            limit = manifest.source.rate_limit
            bucket = TokenBucket(
                limit.per_min,
                limit.burst,
                max_wait_s=self.config.rate_limit_max_wait_s,
            )
            self._buckets[manifest.id] = bucket
        return bucket

    # -- classification -------------------------------------------------

    def _classify(self, exc: ProviderError) -> str:
        status = exc.status
        if status in NO_FAILOVER_STATUS:
            return "terminal"
        if status in RETRYABLE_STATUS:
            return "retryable"
        if status in {401, 403}:
            return "auth"
        if status == 404:
            return "not_found"
        message = exc.message.lower()
        if "timeout" in message or "request failed" in message:
            return "retryable"
        return "terminal"

    def _should_retry(self, exc: ProviderError) -> bool:
        if exc.status == 429:
            return False
        return self._classify(exc) == "retryable"

    def _schema_broken(self, envelope: Envelope, cap: Capability) -> bool:
        if not envelope.records or not cap.fields:
            return False
        projected = set(cap.fields)
        if projected and set(envelope.missing) >= projected:
            return True
        return all(not record.data for record in envelope.records)

    def _reason(self, attempts: list[Attempt]) -> str:
        if not attempts:
            return "no_candidates"
        outcomes = {attempt.outcome for attempt in attempts}
        if "terminal" in outcomes:
            return "parameter_error"
        if "unmapped_query" in outcomes and len(outcomes) == 1:
            return "unmapped_query"
        if "missing_credential" in outcomes and len(outcomes) == 1:
            return "missing_credential"
        if "rate_limited" in outcomes and len(outcomes) == 1:
            return "rate_limited"
        if "auth" in outcomes and len(outcomes) == 1:
            return "auth_failed"
        return "all_sources_failed"
