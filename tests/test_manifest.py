from __future__ import annotations

from mouseion.adapters import get_adapter
from mouseion.manifest import Manifest


def test_all_manifests_load(registry):
    assert set(registry.manifests) == {
        "bls",
        "census_geocoder",
        "cftc_cot",
        "cisa_kev",
        "clinicaltrials_gov",
        "cms_provider",
        "congress_gov",
        "courtlistener",
        "courtlistener_mcp",
        "colorado_campaign_finance",
        "crates_io",
        "crossref",
        "data_gov_au",
        "datacite",
        "ecb",
        "federal_register",
        "fred",
        "gbif",
        "gleif",
        "go_proxy",
        "gov_uk",
        "govinfo",
        "grants_gov",
        "github",
        "internet_archive",
        "library_of_congress",
        "maven_central",
        "nasa_eonet",
        "nhc",
        "nhtsa",
        "nih_reporter",
        "nominatim",
        "noaa_tides",
        "npm",
        "nvd",
        "nws",
        "nyc_open_data",
        "openalex",
        "openfda",
        "openfec",
        "openfema",
        "openlibrary",
        "overpass",
        "arxiv",
        "pmc",
        "pubchem",
        "pubmed",
        "pypi",
        "ror",
        "sec_edgar",
        "sf_open_data",
        "hawaii_campaign_spending",
        "king_county_election_results",
        "la_campaign_finance",
        "ny_campaign_finance",
        "nyc_campaign_finance",
        "austin_campaign_finance",
        "dallas_campaign_finance",
        "el_paso_elected_officials",
        "fulton_county_election_results",
        "maryland_lobbyist_contributions",
        "ny_campaign_filers",
        "oakland_campaign_finance",
        "ramsey_county_election_results",
        "rhode_island_recent_filings",
        "shopify_catalog",
        "wa_campaign_expenditures",
        "wikipedia",
        "wa_campaign_finance",
        "treasury_fiscal",
        "uk_national_archives",
        "uk_parliament",
        "uniprot",
        "usaspending",
        "usgs_earthquake",
        "usgs_water",
        "wayback",
        "wikidata",
        "world_bank",
        "zenodo",
    }


def test_manifest_fields_are_consistent(registry):
    for manifest in registry.manifests.values():
        assert manifest.base.startswith("https://")
        for name, cap in manifest.capabilities.items():
            assert cap.description, f"{manifest.id}/{name} needs a description"
            if cap.id_field and cap.fields:
                assert cap.id_field in cap.fields, f"{manifest.id}/{name} id_field not projected"
            if cap.id_template:
                assert cap.fields, f"{manifest.id}/{name} id_template needs a projection"
            if cap.pagination:
                assert cap.pagination.mode in {"offset", "page", "token"}


def test_param_shorthand_and_aliasing():
    manifest = Manifest.model_validate(
        {
            "id": "demo",
            "title": "Demo",
            "domain": "developer_infrastructure",
            "sub_category": "demo",
            "base": "https://example.test",
            "source": {
                "originator": "Example",
                "originator_domain": "example.test",
                "tier": 1,
                "auth_policy": {
                    "evidence_url": "https://example.test/docs",
                    "verified_at": "2026-10-29",
                },
            },
            "capabilities": {
                "search": {
                    "description": "d",
                    "path": "/x",
                    "params": {"term": "string", "limit": {"type": "int", "default": 5}},
                    "alias": {"term": "q"},
                }
            },
        }
    )
    cap = manifest.capabilities["search"]
    assert cap.params["term"].type == "string"
    assert cap.params["limit"].type == "integer"
    merged = get_adapter("rest_json").resolve_params(manifest, cap, {"term": "abc"})
    assert merged == {"q": "abc", "limit": 5}


def test_unknown_params_are_dropped(registry):
    manifest = registry.get("clinicaltrials_gov")
    cap = manifest.capabilities["search"]
    merged = get_adapter(manifest.adapter).resolve_params(
        manifest, cap, {"condition": "melanoma", "bogus": "nope"}
    )
    assert "bogus" not in merged
    assert merged["query.cond"] == "melanoma"
