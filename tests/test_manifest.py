from __future__ import annotations

from mouseion.adapters import get_adapter
from mouseion.manifest import Manifest


def test_all_manifests_load(registry):
    assert set(registry.manifests) == {
        "bls",
        "census_geocoder",
        "cftc_cot",
        "clinicaltrials_gov",
        "cms_provider",
        "courtlistener",
        "crossref",
        "data_gov_au",
        "datacite",
        "federal_register",
        "fred",
        "gbif",
        "gleif",
        "gov_uk",
        "grants_gov",
        "nhc",
        "nhtsa",
        "nih_reporter",
        "nvd",
        "nws",
        "nyc_open_data",
        "openalex",
        "openfda",
        "openfec",
        "openfema",
        "openlibrary",
        "pubmed",
        "ror",
        "sec_edgar",
        "sf_open_data",
        "treasury_fiscal",
        "usaspending",
        "usgs_earthquake",
        "usgs_water",
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
            "base": "https://example.test",
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
