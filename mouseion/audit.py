"""Source-policy audit.

Pydantic validates the *shape* of a manifest. This module validates the *claims*:
is the originator plausible, is the tier consistent with the auth mechanism, is the
free-access evidence current, is the host on the secondary-source blocklist, and does
a failover cluster have a query map that the engine can actually use.

The audit is deliberately separate from model validation. A manifest should load even
when its policy is wrong, so `mouseion audit` can explain what is wrong instead of
raising an opaque pydantic error at import time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Literal
from urllib.parse import urlparse

import httpx

from .config import Settings
from .config import settings as default_settings
from .manifest import DOMAINS, Manifest
from .registry import Registry

#: Canonical fields the failover engine can translate into source params.
CANONICAL_FIELDS = {"text", "identifier", "entity", "geo", "time_range", "limit"}

#: How long a human's "this is still free" note stays valid.
EVIDENCE_MAX_AGE = timedelta(days=180)

#: Hosts that are secondary by definition: news, social, review and aggregator sites.
#: Matching is on the registrable suffix, so `www.reuters.com` is caught by `reuters.com`.
#: Bare words such as "news" are deliberately not matched: a government newsroom
#: subdomain is still a primary source.
BLOCKED_HOSTS = {
    "medium.com",
    "substack.com",
    "reddit.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "linkedin.com",
    "yelp.com",
    "tripadvisor.com",
    "bloomberg.com",
    "reuters.com",
    "yahoo.com",
    "marketwatch.com",
    "seekingalpha.com",
    "coindesk.com",
    "cointelegraph.com",
}

#: Cache TTL bounds per domain. Broad on purpose: the point is to catch a 5-second
#: TTL on a statute or a 30-day TTL on a seismic feed, not to dictate exact values.
TTL_BOUNDS: dict[str, tuple[float, float]] = {
    "financial_sovereign": (60, 604_800),
    "academic_scientific": (60, 604_800),
    "developer_infrastructure": (60, 86_400),
    "geo_earth_observation": (30, 604_800),
    "archives_web_state": (3_600, 604_800),
    "transport_infrastructure": (60, 604_800),
    "governance_legislative": (60, 604_800),
    "civic_local": (60, 604_800),
    "commerce": (30, 86_400),
    "reference": (60, 604_800),
}

Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class Finding:
    provider: str
    code: str
    severity: Severity
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }


def _host(url: str | None) -> str:
    if not url:
        return ""
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _domain_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    domain = domain.lower().rstrip(".")
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


def _blocked_by(host: str) -> str | None:
    for blocked in BLOCKED_HOSTS:
        if host == blocked or host.endswith("." + blocked):
            return blocked
    return None


def audit_manifest(manifest: Manifest, *, today: date | None = None) -> list[Finding]:
    """Policy checks for one manifest. Never raises; returns findings."""
    today = today or date.today()
    out: list[Finding] = []

    def err(code: str, message: str) -> None:
        out.append(Finding(manifest.id, code, "error", message))

    def warn(code: str, message: str) -> None:
        out.append(Finding(manifest.id, code, "warning", message))

    # -- identity and provenance ---------------------------------------
    if not manifest.sub_category.strip():
        err("sub_category_empty", "sub_category must name the finer topic")
    if manifest.domain not in DOMAINS:  # defensive; pydantic already enforces it
        err("unknown_domain", f"domain '{manifest.domain}' is not one of {DOMAINS}")
    if not manifest.source.originator.strip():
        err("originator_empty", "source.originator must name the data originator")
    if not manifest.source.originator_domain.strip():
        err("originator_domain_empty", "source.originator_domain must name the originator")

    if manifest.source.provenance == "aggregator_exception":
        if manifest.source.is_primary:
            err(
                "aggregator_marked_primary",
                "provenance=aggregator_exception cannot also be is_primary=true",
            )
        if not manifest.source.exception_reason:
            err(
                "aggregator_missing_reason",
                "provenance=aggregator_exception requires exception_reason",
            )
        if manifest.source.tier == 1:
            warn(
                "aggregator_in_tier1",
                "aggregator exception is Tier 1; the engine must skip it during failover",
            )
    elif not manifest.source.is_primary:
        err(
            "non_primary_without_exception",
            "is_primary=false requires provenance=aggregator_exception and a reason",
        )
    elif manifest.source.provenance == "commercial_platform":
        warn(
            "commercial_platform",
            "commercial platform: primary for its own catalog, not a public-record originator",
        )

    # -- tier vs auth ---------------------------------------------------
    if manifest.source.tier == 1:
        if manifest.auth.type == "api_key":
            err("tier1_requires_key", "Tier 1 must be keyless; auth.type is api_key")
        if manifest.auth.type == "oauth":
            err("tier1_oauth", "Tier 1 must be keyless; auth.type is oauth")
        if manifest.auth.type == "client_credentials":
            err(
                "tier1_client_credentials",
                "Tier 1 must be keyless; auth.type is client_credentials",
            )
        if manifest.auth.type == "user_agent" and not manifest.auth.env:
            err(
                "user_agent_missing_env",
                "user_agent auth needs an env var holding the contact address",
            )
        if manifest.auth.type == "none" and manifest.auth.env:
            warn("tier1_unused_env", "auth.env is set but auth.type is none")
    else:
        if manifest.auth.type not in {"api_key", "oauth", "client_credentials"}:
            err(
                "tier2_not_api_key",
                f"Tier 2 must use api_key, oauth, or client_credentials; "
                f"found '{manifest.auth.type}'",
            )
        if manifest.auth.type == "api_key" and not manifest.auth.env:
            err("tier2_missing_env", "Tier 2 api_key auth must name auth.env")
        if manifest.auth.type == "oauth":
            if not manifest.auth.scopes:
                err("oauth_missing_scopes", "OAuth auth must declare scopes")
            if not manifest.auth.redirect_uri:
                err("oauth_missing_redirect", "OAuth auth must declare redirect_uri")
            if manifest.transport != "mcp_remote":
                err(
                    "oauth_requires_remote_mcp",
                    "OAuth auth is only supported for transport=mcp_remote",
                )
        if manifest.auth.type == "client_credentials":
            if not manifest.auth.token_url:
                err("client_credentials_missing_token_url", "client_credentials needs token_url")
            if not manifest.auth.client_id_env:
                err("client_credentials_missing_id", "client_credentials needs client_id_env")
            if not manifest.auth.client_secret_env:
                err(
                    "client_credentials_missing_secret",
                    "client_credentials needs client_secret_env",
                )
            if manifest.transport != "mcp_remote":
                err(
                    "client_credentials_requires_remote_mcp",
                    "client_credentials is only supported for transport=mcp_remote",
                )
        policy = manifest.source.auth_policy
        if not policy.free:
            err("tier2_not_free", "Tier 2 must be permanently free")
        if policy.credit_card_required:
            err("tier2_requires_card", "Tier 2 must not require a credit card")
        if policy.trial:
            err("tier2_trial", "Tier 2 must not be a trial")

    # -- free-access evidence ------------------------------------------
    evidence = manifest.source.auth_policy.evidence_url
    evidence_host = _host(evidence)
    if not evidence.startswith("https://"):
        err("evidence_not_https", f"evidence_url must be https: {evidence}")
    if not _domain_matches(evidence_host, manifest.source.originator_domain):
        err(
            "evidence_domain_mismatch",
            f"evidence host '{evidence_host}' does not belong to "
            f"'{manifest.source.originator_domain}'",
        )
    age = today - manifest.source.auth_policy.verified_at
    if age > EVIDENCE_MAX_AGE:
        err(
            "evidence_stale",
            f"auth policy evidence is {age.days} days old (limit {EVIDENCE_MAX_AGE.days})",
        )
    if manifest.source.auth_policy.verified_at > today:
        warn("evidence_future", "auth policy evidence is dated in the future")

    # -- base domain and secondary-source blocklist ---------------------
    base_host = _host(manifest.base)
    if not _domain_matches(base_host, manifest.source.originator_domain):
        err(
            "base_domain_mismatch",
            f"base host '{base_host}' does not belong to "
            f"'{manifest.source.originator_domain}'",
        )

    for label, url in (
        ("base", manifest.base),
        ("docs_url", manifest.docs_url),
        ("evidence_url", evidence),
    ):
        blocked = _blocked_by(_host(url))
        if blocked:
            err(
                "blocked_secondary_host",
                f"{label} host '{_host(url)}' matches blocked secondary source '{blocked}'",
            )

    if not manifest.docs_url:
        warn("docs_url_missing", "no docs_url; the model has no primary documentation link")
    if not manifest.attribution:
        warn("attribution_missing", "no attribution string for the upstream")

    # -- rate limits ----------------------------------------------------
    rl = manifest.source.rate_limit
    if rl.per_min <= 0:
        err("rate_limit_invalid", "rate_limit.per_min must be positive")
    if rl.burst <= 0:
        err("rate_limit_invalid", "rate_limit.burst must be positive")
    if rl.concurrency <= 0:
        err("rate_limit_invalid", "rate_limit.concurrency must be positive")
    if rl.burst > rl.per_min:
        warn("rate_limit_burst", "burst exceeds per_min; the bucket can never fill")

    # -- cache TTL ------------------------------------------------------
    if manifest.cache_ttl is None:
        warn("cache_ttl_missing", "no cache_ttl; the runtime default will be used")
    else:
        low, high = TTL_BOUNDS[manifest.domain]
        if not low <= manifest.cache_ttl <= high:
            err(
                "cache_ttl_out_of_range",
                f"cache_ttl {manifest.cache_ttl}s is outside {low}-{high}s for "
                f"{manifest.domain}",
            )

    if manifest.timeout_s is not None and manifest.timeout_s <= 0:
        err("timeout_invalid", "timeout_s must be positive")

    # -- failover clusters ---------------------------------------------
    for name, cap in manifest.capabilities.items():
        if cap.cluster:
            if not cap.query_map:
                err(
                    "cluster_without_query_map",
                    f"capability '{name}' declares cluster '{cap.cluster}' without query_map",
                )
            for canonical, param in cap.query_map.items():
                if canonical not in CANONICAL_FIELDS:
                    err(
                        "unknown_canonical_field",
                        f"capability '{name}' maps unknown canonical field '{canonical}'",
                    )
                if param not in cap.params and param not in {"limit", "page", "offset", "cursor"}:
                    err(
                        "query_map_unknown_param",
                        f"capability '{name}' maps '{canonical}' to unknown param '{param}'",
                    )
        if cap.local_filter is not None:
            if not cap.local_filter.field:
                err(
                    "local_filter_missing_field",
                    f"capability '{name}' has local_filter without a field",
                )
            if cap.local_filter.param not in cap.params:
                err(
                    "local_filter_unknown_param",
                    f"capability '{name}' filters on unknown param "
                    f"'{cap.local_filter.param}'",
                )

    return out


def audit_registry(
    registry: Registry, *, today: date | None = None
) -> list[Finding]:
    """Audit every manifest plus the cross-provider cluster invariants."""
    findings: list[Finding] = []
    for manifest in registry.manifests.values():
        findings.extend(audit_manifest(manifest, today=today))

    clusters: dict[str, list[tuple[str, str, dict[str, str], bool]]] = {}
    for manifest in registry.manifests.values():
        for name, cap in manifest.capabilities.items():
            if cap.cluster:
                clusters.setdefault(cap.cluster, []).append(
                    (manifest.id, name, cap.query_map, manifest.source.is_primary)
                )

    for cluster, members in sorted(clusters.items()):
        if len(members) < 2:
            findings.append(
                Finding(
                    members[0][0],
                    "cluster_singleton",
                    "warning",
                    f"cluster '{cluster}' has one member; failover cannot happen",
                )
            )
        key_sets = [set(query_map) for _, _, query_map, _ in members]
        if key_sets and not set.intersection(*key_sets):
            findings.append(
                Finding(
                    members[0][0],
                    "cluster_no_common_fields",
                    "warning",
                    f"cluster '{cluster}' members share no canonical field",
                )
            )
        if any(primary for *_, primary in members) and not all(
            primary for *_, primary in members
        ):
            findings.append(
                Finding(
                    members[0][0],
                    "cluster_has_aggregator",
                    "warning",
                    f"cluster '{cluster}' mixes primary and aggregator sources; "
                    "the engine must skip non-primary members during failover",
                )
            )

    return sorted(findings, key=lambda f: (f.provider, f.severity, f.code))


async def audit_live(
    registry: Registry,
    *,
    config: Settings | None = None,
    timeout: float = 10.0,
) -> list[Finding]:
    """Check that every evidence URL is still reachable.

    This does not prove the policy is still true; it proves the page a human cited
    still exists. Auth behaviour is covered by `pytest -m live`.
    """
    cfg = config or default_settings
    findings: list[Finding] = []
    semaphore = asyncio.Semaphore(8)

    async with httpx.AsyncClient(
        headers={"User-Agent": cfg.user_agent},
        follow_redirects=True,
        timeout=timeout,
    ) as client:

        async def check(manifest: Manifest) -> list[Finding]:
            url = manifest.source.auth_policy.evidence_url
            async with semaphore:
                try:
                    response = await client.get(url)
                except httpx.HTTPError as exc:
                    return [
                        Finding(
                            manifest.id,
                            "evidence_unreachable",
                            "error",
                            f"{url}: {type(exc).__name__}",
                        )
                    ]
            if response.status_code >= 500 or response.status_code == 404:
                return [
                    Finding(
                        manifest.id,
                        "evidence_http",
                        "error",
                        f"{url}: HTTP {response.status_code}",
                    )
                ]
            if response.status_code in {403, 429}:
                return [
                    Finding(
                        manifest.id,
                        "evidence_blocked",
                        "warning",
                        f"{url}: HTTP {response.status_code}; verify manually",
                    )
                ]
            return []

        results = await asyncio.gather(
            *(check(manifest) for manifest in registry.manifests.values())
        )

    for group in results:
        findings.extend(group)
    return sorted(findings, key=lambda f: (f.provider, f.severity, f.code))


def summarize(findings: Iterable[Finding]) -> dict[str, Any]:
    items = list(findings)
    errors = [item for item in items if item.severity == "error"]
    warnings = [item for item in items if item.severity == "warning"]
    return {
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(warnings),
        "findings": [item.as_dict() for item in items],
    }
