from __future__ import annotations

import pytest


def test_summary_counts(registry):
    summary = registry.summary()
    assert summary["providers"] == 36
    assert summary["capabilities"] == 44
    assert summary["categories"] == {
        "government": 8,
        "places": 7,
        "finance": 6,
        "research": 5,
        "biomedical": 3,
        "health": 2,
        "company": 1,
        "legal": 1,
        "life-sciences": 1,
        "reference": 1,
        "security": 1,
    }


def test_search_ranks_by_subject(registry):
    assert registry.search("clinical trial melanoma")[0]["id"] == "clinicaltrials_gov"
    assert registry.search("federal register rule")[0]["id"] == "federal_register"
    assert registry.search("futures positioning")[0]["id"] == "cftc_cot"


def test_search_is_empty_for_unrelated_query(registry):
    assert registry.search("sourdough starter hydration") == []


def test_category_filter(registry):
    hits = registry.search("drug", category="retail")
    assert hits == []


def test_get_normalizes_ids(registry):
    assert registry.get("Federal-Register").id == "federal_register"


def test_get_unknown_suggests_alternatives(registry):
    with pytest.raises(KeyError) as exc:
        registry.get("clinicaltrials")
    assert "clinicaltrials_gov" in str(exc.value)


def test_describe_includes_auth_and_limits(registry):
    described = registry.describe("openfda")
    assert described["auth"] == "none"
    assert described["terms"].startswith("Do not rely")
    assert described["capabilities"]["drug_recalls"]["pagination"] == "offset"


def test_compact_cards_are_small(registry):
    card = registry.search("clinicaltrials")[0]
    assert set(card) == {"id", "title", "category", "auth", "capabilities"}
    assert all(len(cap["description"]) <= 110 for cap in card["capabilities"])
