from __future__ import annotations

import asyncio

import pytest

from mouseion.cache import Cache
from mouseion.regions import detect_regions, region_conflict
from mouseion.registry import Registry
from mouseion.router import Router


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def registry() -> Registry:
    from pathlib import Path

    return Registry.load(Path(__file__).resolve().parent.parent / "providers")


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "regions.db")


def test_detects_country_and_city_names():
    assert detect_regions("weather in Paris tomorrow") == {"FR"}
    assert detect_regions("hospital ratings in Germany") == {"DE"}
    assert detect_regions("earthquakes in Japan") == {"JP"}
    assert detect_regions("UK rules on data protection") == {"GB"}
    assert detect_regions("Australian open datasets") == {"AU"}


def test_detects_multiword_places():
    assert detect_regions("policy in the united kingdom") == {"GB"}
    assert detect_regions("unemployment in south africa") == {"ZA"}


def test_ignores_pronoun_us():
    """'tell us about X' must not look like a US constraint."""
    assert detect_regions("tell us about the weather") == set()


def test_no_place_means_no_constraint():
    assert detect_regions("drug recalls for blood pressure medication") == set()


def test_ambiguous_names_map_to_every_reading():
    assert detect_regions("georgia") == {"US", "GE"}
    assert detect_regions("paris, texas") == {"FR", "US"}


def test_conflict_requires_disjoint_regions():
    assert region_conflict(["US"], {"FR"})
    assert not region_conflict(["US"], {"US", "FR"}), "partial overlap must survive"
    assert not region_conflict([], {"FR"}), "undeclared regions are global"
    assert not region_conflict(["US"], set()), "no detected region, no filter"
    assert not region_conflict(["GLOBAL"], {"FR"})


def test_router_drops_a_provably_impossible_provider(registry, cache):
    """CMS covers US hospitals only; a German hospital query cannot be served."""
    assert run(Router(registry, cache).route("hospital star ratings in Germany")) == []


def test_router_keeps_global_providers_for_any_region(registry, cache):
    hits = run(Router(registry, cache).route("earthquakes in Japan this month"))
    assert hits and hits[0].provider == "usgs_earthquake"


def test_router_keeps_us_provider_when_query_names_both_regions(registry, cache):
    hits = run(Router(registry, cache).route("US hospitals operating in France"))
    assert any(candidate.provider == "cms_provider" for candidate in hits)


def test_describe_reports_coverage(registry):
    assert registry.describe("cms_provider")["regions"] == ["US"]
    assert registry.describe("world_bank")["regions"] == "global"
