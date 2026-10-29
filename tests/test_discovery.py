from __future__ import annotations

from mouseion.discovery import (
    Candidate,
    ProbeResult,
    _is_api_like,
    classify,
    find_record_path,
    manifest_from,
)


def test_classifies_known_shapes():
    assert classify("https://x.gov/api/3/action/package_search") == "ckan"
    assert classify("https://x.gov/arcgis/rest/services/y/FeatureServer/0") == "arcgis"
    assert classify("https://x.gov/resource/abcd-1234.json") == "socrata"
    assert classify("https://x.gov/odata/Table?$filter=a eq b") == "odata"
    assert classify("https://x.gov/data/live/browsers.json") == "rest_json"


def test_finds_the_largest_record_list():
    body = {"meta": {"count": 2}, "results": [{"a": 1}, {"a": 2}], "other": [{"b": 1}]}
    assert find_record_path(body) == ("results", 2)

    nested = {"data": {"items": [{"x": 1}]}}
    assert find_record_path(nested) == ("data.items", 1)


def test_root_list_body_has_no_path():
    assert find_record_path([{"a": 1}, {"a": 2}, {"a": 3}]) == (None, 3)


def test_body_without_records_is_rejected():
    assert find_record_path({"status": "ok", "count": 3}) == (None, 0)


def test_api_like_filter():
    assert _is_api_like({"format": "API", "accessURL": "https://x.gov/v1"})
    assert _is_api_like({"format": "CSV", "accessURL": "https://x.gov/rows.json"})
    assert not _is_api_like({"format": "PDF", "accessURL": "https://x.gov/report.pdf"})


def test_generated_manifest_is_loadable_shaped():
    result = ProbeResult(
        candidate=Candidate("https://x.gov/data/live/browsers.json", "Browsers", "GSA"),
        status=200,
        record_path="results",
        records=4,
        verdict="ok",
    )
    manifest = manifest_from(result)
    assert manifest["id"].startswith("auto_gsa_")
    assert manifest["base"] == "https://x.gov"
    capability = manifest["capabilities"]["list"]
    assert capability["record_path"] == "results"
    assert capability["local_window"] is True, "auto-imports must cap record counts"
    assert capability["example"] == {}
