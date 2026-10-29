"""Tests for the source-policy audit.

The real registry must be clean. The remaining cases construct a valid manifest and
break one rule at a time, so a regression names the rule that stopped being enforced.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from mouseion.audit import audit_manifest, audit_registry, summarize
from mouseion.manifest import DOMAINS, Manifest
from mouseion.registry import Registry

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 29)


def base_manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "id": "demo",
            "title": "Demo",
            "domain": "developer_infrastructure",
            "sub_category": "demo",
            "base": "https://api.example.gov",
            "docs_url": "https://example.gov/docs",
            "attribution": "Example Agency",
            "cache_ttl": 3600,
            "source": {
                "is_primary": True,
                "originator": "Example Agency",
                "originator_domain": "example.gov",
                "provenance": "official",
                "tier": 1,
                "auth_policy": {
                    "free": True,
                    "credit_card_required": False,
                    "trial": False,
                    "evidence_url": "https://example.gov/docs",
                    "verified_at": "2026-10-29",
                },
                "rate_limit": {"per_min": 60, "burst": 10, "concurrency": 2},
            },
            "capabilities": {
                "search": {
                    "description": "Search the demo endpoint.",
                    "path": "/search",
                    "params": {
                        "query": {"type": "string", "required": True},
                        "limit": {"type": "integer"},
                    },
                    "cluster": "demo_search",
                    "query_map": {"text": "query", "limit": "limit"},
                }
            },
        }
    )


def codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def test_repository_manifests_have_no_errors():
    registry = Registry.load(ROOT / "providers")
    findings = audit_registry(registry, today=TODAY)
    errors = [finding for finding in findings if finding.severity == "error"]
    assert errors == [], "\n".join(f"{e.provider}: {e.code}: {e.message}" for e in errors)


def test_every_manifest_declares_a_domain_and_source():
    registry = Registry.load(ROOT / "providers")
    for manifest in registry.manifests.values():
        assert manifest.domain in DOMAINS
        assert manifest.sub_category
        assert manifest.source.originator
        assert manifest.source.originator_domain
        assert manifest.source.tier in {1, 2}


def test_aggregator_exceptions_are_explicit():
    registry = Registry.load(ROOT / "providers")
    for provider in ("courtlistener", "openalex", "gbif", "openlibrary"):
        manifest = registry.get(provider)
        assert manifest.source.is_primary is False
        assert manifest.source.provenance == "aggregator_exception"
        assert manifest.source.exception_reason


def test_tier_two_sources_are_free_and_cardless():
    registry = Registry.load(ROOT / "providers")
    for manifest in registry.manifests.values():
        if manifest.source.tier != 2:
            continue
        assert manifest.auth.type == "api_key"
        assert manifest.auth.env
        assert manifest.source.auth_policy.free
        assert not manifest.source.auth_policy.credit_card_required
        assert not manifest.source.auth_policy.trial


def test_tier_one_cannot_require_a_key():
    manifest = base_manifest()
    manifest.auth.type = "api_key"
    manifest.auth.env = "DEMO_KEY"
    assert "tier1_requires_key" in codes(audit_manifest(manifest, today=TODAY))


def test_tier_two_must_use_api_key_auth():
    manifest = base_manifest()
    manifest.source.tier = 2
    found = codes(audit_manifest(manifest, today=TODAY))
    assert "tier2_not_api_key" in found
    assert "tier2_missing_env" in found


def test_tier_two_must_not_require_a_card_or_trial():
    manifest = base_manifest()
    manifest.source.tier = 2
    manifest.auth.type = "api_key"
    manifest.auth.env = "DEMO_KEY"
    manifest.source.auth_policy.credit_card_required = True
    manifest.source.auth_policy.trial = True
    found = codes(audit_manifest(manifest, today=TODAY))
    assert "tier2_requires_card" in found
    assert "tier2_trial" in found


def test_aggregator_cannot_be_primary():
    manifest = base_manifest()
    manifest.source.provenance = "aggregator_exception"
    manifest.source.exception_reason = "secondary index"
    assert "aggregator_marked_primary" in codes(audit_manifest(manifest, today=TODAY))


def test_aggregator_requires_a_reason():
    manifest = base_manifest()
    manifest.source.is_primary = False
    manifest.source.provenance = "aggregator_exception"
    assert "aggregator_missing_reason" in codes(audit_manifest(manifest, today=TODAY))


def test_non_primary_requires_an_exception():
    manifest = base_manifest()
    manifest.source.is_primary = False
    assert "non_primary_without_exception" in codes(audit_manifest(manifest, today=TODAY))


def test_stale_evidence_fails():
    manifest = base_manifest()
    manifest.source.auth_policy.verified_at = date(2025, 1, 1)
    assert "evidence_stale" in codes(audit_manifest(manifest, today=TODAY))


def test_evidence_must_belong_to_the_originator():
    manifest = base_manifest()
    manifest.source.auth_policy.evidence_url = "https://other.gov/docs"
    assert "evidence_domain_mismatch" in codes(audit_manifest(manifest, today=TODAY))


def test_base_must_belong_to_the_originator():
    manifest = base_manifest()
    manifest.base = "https://api.other.gov"
    assert "base_domain_mismatch" in codes(audit_manifest(manifest, today=TODAY))


def test_blocked_secondary_hosts_fail():
    manifest = base_manifest()
    manifest.base = "https://www.reuters.com"
    manifest.docs_url = "https://www.reuters.com/docs"
    manifest.source.originator_domain = "reuters.com"
    manifest.source.auth_policy.evidence_url = "https://www.reuters.com/docs"
    assert "blocked_secondary_host" in codes(audit_manifest(manifest, today=TODAY))


def test_cluster_requires_a_query_map():
    manifest = base_manifest()
    manifest.capabilities["search"].query_map = {}
    assert "cluster_without_query_map" in codes(audit_manifest(manifest, today=TODAY))


def test_query_map_must_name_a_real_param():
    manifest = base_manifest()
    manifest.capabilities["search"].query_map = {"text": "not_a_param"}
    assert "query_map_unknown_param" in codes(audit_manifest(manifest, today=TODAY))


def test_unknown_canonical_field_fails():
    manifest = base_manifest()
    manifest.capabilities["search"].query_map = {"nonsense": "query"}
    assert "unknown_canonical_field" in codes(audit_manifest(manifest, today=TODAY))


def test_cache_ttl_must_fit_the_domain():
    manifest = base_manifest()
    manifest.cache_ttl = 1
    assert "cache_ttl_out_of_range" in codes(audit_manifest(manifest, today=TODAY))


def test_summarize_separates_errors_and_warnings():
    manifest = base_manifest()
    manifest.cache_ttl = 1
    manifest.docs_url = None
    report = summarize(audit_manifest(manifest, today=TODAY))
    assert report["ok"] is False
    assert report["errors"] >= 1
    assert report["warnings"] >= 1
