from __future__ import annotations

import pytest

from mouseion.adapters import get_adapter


def prepare(manifest, capability, params=None, cursor=None):
    return get_adapter(manifest.adapter).prepare(manifest, capability, params or {}, cursor)


def test_clinicaltrials_token_pagination(registry):
    manifest = registry.get("clinicaltrials_gov")
    cap = manifest.capabilities["search"]
    req = prepare(manifest, cap, {"condition": "melanoma"})

    assert req.url == "https://clinicaltrials.gov/api/v2/studies"
    assert req.params["query.cond"] == "melanoma"
    assert req.params["pageSize"] == 20
    assert "pageToken" not in req.params

    second = prepare(manifest, cap, {"condition": "melanoma"}, cursor="TOKEN-2")
    assert second.params["pageToken"] == "TOKEN-2"


def test_clinicaltrials_parses_projection_and_cursor(registry):
    from tests.conftest import STUDIES_BODY

    manifest = registry.get("clinicaltrials_gov")
    cap = manifest.capabilities["search"]
    adapter = get_adapter(manifest.adapter)
    req = prepare(manifest, cap, {"condition": "melanoma"})
    result = adapter.parse(manifest, cap, STUDIES_BODY, req)

    assert len(result.records) == 1
    record = result.records[0]
    assert record.source_record_id == "NCT04631731"
    assert record.source_url == "https://clinicaltrials.gov/study/NCT04631731"
    assert record.data["phases"] == ["PHASE1", "PHASE2"]
    assert record.data["enrollment"] == 200
    assert "collaborators" in result.missing
    assert result.next_cursor == "TOKEN-2"


def test_local_filter_narrows_a_catalog(registry):
    manifest = registry.get("cisa_kev")
    cap = manifest.capabilities["catalog"]
    adapter = get_adapter(manifest.adapter)
    req = prepare(manifest, cap, {"cve_id": "CVE-2024-0001", "limit": 10})
    body = {
        "catalogVersion": "2026.10.28",
        "vulnerabilities": [
            {"cveID": "CVE-2024-0001", "vendorProject": "A", "product": "P"},
            {"cveID": "CVE-2024-0002", "vendorProject": "B", "product": "Q"},
        ],
    }

    result = adapter.parse(manifest, cap, body, req)

    assert len(result.records) == 1
    assert result.records[0].data["cve_id"] == "CVE-2024-0001"
    assert result.upstream_version == "2026.10.28"


def test_projection_field_fallbacks(registry):
    manifest = registry.get("wikidata")
    cap = manifest.capabilities["entity"]
    adapter = get_adapter(manifest.adapter)
    req = prepare(manifest, cap, {"ids": "Q42"})
    body = {
        "entities": {
            "Q42": {
                "id": "Q42",
                "labels": {"mul": {"value": "Douglas Adams"}},
                "descriptions": {},
                "aliases": {"mul": [{"value": "DNA"}]},
            }
        }
    }

    result = adapter.parse(manifest, cap, body, req)

    assert result.records[0].data["label"] == "Douglas Adams"
    assert result.records[0].data["aliases"] == ["DNA"]


def test_sdmx_json_flattens_series(registry):
    manifest = registry.get("ecb")
    cap = manifest.capabilities["yield_curve"]
    adapter = get_adapter(manifest.adapter)
    req = prepare(manifest, cap, {"maturity": "10Y", "limit": 10})
    body = {
        "header": {"prepared": "2026-10-29T00:00:00Z"},
        "dataSets": [
            {
                "series": {
                    "0:0:0": {
                        "observations": {"0": [2.1], "1": [2.2]},
                    }
                }
            }
        ],
        "structure": {
            "dimensions": {
                "series": [
                    {"id": "FREQ", "values": [{"id": "B"}]},
                    {"id": "REF_AREA", "values": [{"id": "U2"}]},
                    {"id": "DATA_TYPE_FM", "values": [{"id": "SR_10Y"}]},
                ],
                "observation": [
                    {
                        "id": "TIME_PERIOD",
                        "values": [{"id": "2024-01-02"}, {"id": "2024-01-03"}],
                    }
                ],
            }
        },
    }

    result = adapter.parse(manifest, cap, body, req)

    assert len(result.records) == 2
    assert result.records[0].data["time_period"] == "2024-01-02"
    assert result.records[0].data["maturity"] == "SR_10Y"
    assert result.upstream_version == "2026-10-29T00:00:00Z"


def test_cdx_adapter_maps_header_rows(registry):
    manifest = registry.get("wayback")
    cap = manifest.capabilities["snapshots"]
    adapter = get_adapter(manifest.adapter)
    req = prepare(manifest, cap, {"url": "example.com", "limit": 2})
    body = [
        ["timestamp", "original", "statuscode", "mimetype", "digest", "length"],
        ["20200101000000", "http://example.com/", "200", "text/html", "abc", "10"],
    ]

    result = adapter.parse(manifest, cap, body, req)

    assert len(result.records) == 1
    assert result.records[0].data["timestamp"] == "20200101000000"
    assert result.records[0].source_record_id == "20200101000000:http://example.com/"


def test_path_substitution_quotes(registry):
    manifest = registry.get("clinicaltrials_gov")
    cap = manifest.capabilities["get"]
    req = prepare(manifest, cap, {"nct_id": "NCT04631731"})
    assert req.url.endswith("/studies/NCT04631731")


def test_federal_register_aliases_and_page(registry):
    manifest = registry.get("federal_register")
    cap = manifest.capabilities["search"]
    req = prepare(
        manifest,
        cap,
        {"term": "artificial intelligence", "agency": ["gsa"], "limit": 7},
    )
    assert req.params["conditions[term]"] == "artificial intelligence"
    assert req.params["conditions[agencies][]"] == ["gsa"]
    assert req.params["per_page"] == 7
    assert req.params["page"] == 1

    page_two = prepare(manifest, cap, {"term": "x"}, cursor="2")
    assert page_two.params["page"] == 2


def test_federal_register_next_cursor_uses_total_pages(registry):
    from tests.conftest import FEDREG_BODY

    manifest = registry.get("federal_register")
    cap = manifest.capabilities["search"]
    adapter = get_adapter(manifest.adapter)
    req = prepare(manifest, cap, {"term": "ai", "limit": 1})
    result = adapter.parse(manifest, cap, FEDREG_BODY, req)
    assert result.records[0].source_url == "https://www.federalregister.gov/d/2026-12345"
    assert result.next_cursor == "2"


def test_socrata_soql_and_pagination(registry):
    manifest = registry.get("cftc_cot")
    cap = manifest.capabilities["legacy_futures_only"]
    req = prepare(manifest, cap, {"where": "market like '%CRUDE%'", "limit": 3})
    assert "$limit" in req.params
    assert "$offset" in req.params
    assert "$select" in req.params
    assert req.params["$limit"] == 3


def test_limit_is_clamped_to_max(registry):
    manifest = registry.get("openfda")
    cap = manifest.capabilities["drug_recalls"]
    req = prepare(manifest, cap, {"search": 'x:"y"', "limit": 99_999})
    assert req.params["limit"] == 100


def test_limit_is_clamped_to_upstream_minimum(registry):
    """Federal Register silently ignores per_page=1 and returns its 20-row default."""
    manifest = registry.get("federal_register")
    cap = manifest.capabilities["search"]
    req = prepare(manifest, cap, {"term": "ai", "limit": 1})
    assert req.params["per_page"] == 2


def test_missing_path_param_raises(registry):
    from mouseion.envelope import ProviderError

    manifest = registry.get("clinicaltrials_gov")
    cap = manifest.capabilities["get"]
    with pytest.raises(ProviderError) as exc:
        prepare(manifest, cap, {})
    assert "nct_id" in str(exc.value)


# -- POST body APIs ------------------------------------------------------


def test_body_template_renders_types_and_drops_optional_fields(registry):
    manifest = registry.get("usaspending")
    cap = manifest.capabilities["spending_by_award"]
    req = prepare(manifest, cap, {"keywords": "cloud,computing", "limit": 2})

    assert req.method == "POST"
    assert req.json_body["filters"]["keywords"] == ["cloud", "computing"]
    assert req.json_body["limit"] == 2
    assert isinstance(req.json_body["limit"], int), "must stay an int, not a string"
    assert req.json_body["page"] == 1
    assert req.params == {}, "body params must not leak into the query string"


def test_render_body_drops_unresolved_placeholders(registry):
    manifest = registry.get("usaspending")
    adapter = get_adapter(manifest.adapter)
    rendered = adapter.render_body(
        {"keep": "{x}", "drop": "{missing}", "nested": {"also": "{missing}"}, "list": ["{x}", "{missing}"]},
        {"x": 7},
    )
    assert rendered == {"keep": 7, "list": [7]}


def test_nih_reporter_posts_criteria(registry):
    manifest = registry.get("nih_reporter")
    cap = manifest.capabilities["search_projects"]
    req = prepare(manifest, cap, {"query": "melanoma"})
    assert req.json_body["criteria"]["advanced_text_search"]["search_text"] == "melanoma"
    assert req.json_body["limit"] == 3


def test_map_exclude_turns_keyed_maps_into_records():
    from mouseion.adapters.base import extract_records

    body = {"result": {"uids": ["1", "2"], "1": {"uid": "1"}, "2": {"uid": "2"}}}
    records = extract_records(body, "result", ["uids"])
    assert [record["uid"] for record in records] == ["1", "2"]


# -- unstable upstream windows -------------------------------------------


def test_local_window_slices_without_overlap(registry, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
    manifest = registry.get("sec_edgar")
    cap = manifest.capabilities["full_text_search"]
    adapter = get_adapter(manifest.adapter)
    body = {
        "hits": {
            "hits": [
                {"_id": f"acc-{i}:doc.htm", "_source": {"display_names": [f"C{i}"], "ciks": [str(i)], "form": "10-K", "adsh": f"acc-{i}"}}
                for i in range(12)
            ],
            "total": {"value": 100},
        }
    }

    def page(cursor):
        request = prepare(manifest, cap, {"q": "ai", "limit": 3}, cursor)
        result = adapter.parse(manifest, cap, body, request)
        return [record.data["document_id"] for record in result.records], result.next_cursor

    first, next_one = page(None)
    second, next_two = page(next_one)
    assert first == ["acc-0:doc.htm", "acc-1:doc.htm", "acc-2:doc.htm"]
    assert second == ["acc-3:doc.htm", "acc-4:doc.htm", "acc-5:doc.htm"]
    assert not set(first) & set(second)
    assert next_two == "6"


# -- user-agent auth -----------------------------------------------------


def test_user_agent_auth_requires_env(registry, monkeypatch):
    from mouseion.envelope import ProviderError

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    manifest = registry.get("sec_edgar")
    cap = manifest.capabilities["full_text_search"]
    with pytest.raises(ProviderError) as exc:
        prepare(manifest, cap, {"q": "ai"})
    assert "SEC_USER_AGENT" in (exc.value.hint or "")


# -- required params, query templates, body param citations ---------------


def test_missing_required_param_names_the_friendly_name(registry):
    from mouseion.envelope import ProviderError

    manifest = registry.get("pubmed")
    cap = manifest.capabilities["summaries"]
    with pytest.raises(ProviderError) as exc:
        prepare(manifest, cap, {})
    # 'ids' is what the caller was told to pass, even though it maps to 'id' upstream.
    assert "ids" in str(exc.value)


def test_query_template_flattens_and_drops_optional_groups(registry):
    manifest = registry.get("cms_provider")
    cap = manifest.capabilities["hospitals"]

    with_state = prepare(manifest, cap, {"state": "CA", "limit": 5})
    assert with_state.params["conditions[0][property]"] == "state"
    assert with_state.params["conditions[0][value]"] == "CA"
    assert with_state.params["conditions[0][operator]"] == "="
    assert with_state.params["limit"] == 5

    without_state = prepare(manifest, cap, {"limit": 5})
    assert not any(key.startswith("conditions") for key in without_state.params), (
        "an unanswered optional filter must not half-build a condition clause"
    )


def test_body_only_api_still_builds_a_citation_url(registry):
    manifest = registry.get("bls")
    cap = manifest.capabilities["series_observations"]
    adapter = get_adapter(manifest.adapter)
    request = prepare(manifest, cap, {"series_id": "LNS14000000", "limit": 2})
    body = {"Results": {"series": [{"data": [{"year": "2026", "period": "M09", "value": "4.2"}]}]}}
    result = adapter.parse(manifest, cap, body, request)
    assert result.records[0].source_url == "https://data.bls.gov/timeseries/LNS14000000"


# -- CSV bulk exports -----------------------------------------------------


def _csv_manifest():
    from mouseion.manifest import Manifest

    return Manifest.model_validate(
        {
            "id": "example_csv",
            "title": "Example Campaign Finance",
            "domain": "civic_local",
            "sub_category": "campaign_finance",
            "source": {
                "originator": "Example State",
                "originator_domain": "example.gov",
                "auth_policy": {
                    "evidence_url": "https://example.gov",
                    "verified_at": "2026-10-30",
                },
            },
            "base": "https://example.gov",
            "adapter": "csv",
            "capabilities": {
                "filings": {
                    "description": "Candidate filings",
                    "path": "/filings.csv",
                    "params": {"limit": {"type": "integer", "default": 2}},
                    "fields": {"name": "name", "office": "office"},
                    "pagination": {"mode": "offset", "default_limit": 2},
                    "local_window": True,
                }
            },
        }
    )


def test_csv_adapter_projects_and_pages_locally():
    manifest = _csv_manifest()
    cap = manifest.capabilities["filings"]
    adapter = get_adapter(manifest.adapter)
    body = {"_raw_text": "name,office\nA,Mayor\nB,Council\nC,Clerk\n"}

    first = adapter.parse(manifest, cap, body, prepare(manifest, cap, {"limit": 2}))
    assert [record.data["name"] for record in first.records] == ["A", "B"]
    assert first.next_cursor == "2"

    second = adapter.parse(
        manifest, cap, body, prepare(manifest, cap, {"limit": 2}, cursor="2")
    )
    assert [record.data["name"] for record in second.records] == ["C"]
    assert second.next_cursor is None


def test_csv_adapter_handles_bom_and_semicolons():
    manifest = _csv_manifest()
    cap = manifest.capabilities["filings"]
    adapter = get_adapter(manifest.adapter)
    body = {"_raw_text": "\ufeffname;office\nA;Mayor\n"}

    result = adapter.parse(manifest, cap, body, prepare(manifest, cap, {}))
    assert result.records[0].data == {"name": "A", "office": "Mayor"}


def test_csv_adapter_rejects_a_non_csv_body():
    from mouseion.envelope import ProviderError

    manifest = _csv_manifest()
    cap = manifest.capabilities["filings"]
    adapter = get_adapter(manifest.adapter)
    with pytest.raises(ProviderError):
        adapter.parse(manifest, cap, {"unexpected": True}, prepare(manifest, cap, {}))


# -- named per-election datasets ------------------------------------------


def _dataset_manifest():
    from mouseion.manifest import Manifest

    return Manifest.model_validate(
        {
            "id": "example_elections",
            "title": "Example Election Results",
            "domain": "civic_local",
            "sub_category": "election_results",
            "source": {
                "originator": "Example County",
                "originator_domain": "example.gov",
                "auth_policy": {
                    "evidence_url": "https://example.gov",
                    "verified_at": "2026-10-30",
                },
            },
            "base": "https://example.gov",
            "adapter": "socrata",
            "capabilities": {
                "results": {
                    "description": "One election by precinct",
                    "path": "/resource/{dataset}.json",
                    "dataset_param": "election",
                    "datasets": {"2024_general": "abcd-1234", "2022_general": "efgh-5678"},
                    "params": {
                        "election": {"type": "string", "required": True},
                        "limit": {"type": "integer", "default": 25},
                    },
                    "pagination": {"mode": "offset", "param": "offset", "limit_param": "limit"},
                }
            },
        }
    )


def test_named_dataset_is_resolved_and_not_forwarded():
    manifest = _dataset_manifest()
    cap = manifest.capabilities["results"]
    request = prepare(manifest, cap, {"election": "2024_general", "limit": 5})

    assert request.url == "https://example.gov/resource/abcd-1234.json"
    assert "election" not in request.params
    assert request.params["$limit"] == 5
    assert request.template_params["dataset"] == "abcd-1234"


def test_unknown_dataset_names_the_available_keys():
    from mouseion.envelope import ProviderError

    manifest = _dataset_manifest()
    cap = manifest.capabilities["results"]
    with pytest.raises(ProviderError) as exc:
        prepare(manifest, cap, {"election": "1999_general"})
    assert "2024_general" in exc.value.to_dict()["hint"]


def test_dataset_param_must_be_declared():
    from mouseion.manifest import Manifest

    with pytest.raises(Exception, match="dataset_param"):
        Manifest.model_validate(
            {
                "id": "bad",
                "title": "Bad",
                "domain": "civic_local",
                "sub_category": "election_results",
                "source": {
                    "originator": "X",
                    "originator_domain": "x.gov",
                    "auth_policy": {"evidence_url": "https://x.gov", "verified_at": "2026-10-30"},
                },
                "base": "https://x.gov",
                "capabilities": {
                    "results": {
                        "description": "x",
                        "path": "/resource/{dataset}.json",
                        "datasets": {"a": "b"},
                        "params": {},
                    }
                },
            }
        )


# -- ZIP bulk archives ----------------------------------------------------


def _zip_body(members: dict[str, str]) -> dict[str, str]:
    import base64
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return {"_raw_b64": base64.b64encode(buffer.getvalue()).decode("ascii")}


def test_csv_adapter_reads_a_zip_member():
    manifest = _csv_manifest()
    cap = manifest.capabilities["filings"]
    adapter = get_adapter(manifest.adapter)
    body = _zip_body({"filings.csv": "name,office\nA,Mayor\nB,Council\n"})

    result = adapter.parse(manifest, cap, body, prepare(manifest, cap, {"limit": 5}))
    assert [record.data["name"] for record in result.records] == ["A", "B"]


def test_csv_adapter_selects_a_named_zip_member():
    manifest = _csv_manifest()
    cap = manifest.capabilities["filings"]
    cap.zip_member = "wanted.csv"
    adapter = get_adapter(manifest.adapter)
    body = _zip_body(
        {
            "other.csv": "name,office\nX,Other\n",
            "wanted.csv": "name,office\nA,Mayor\n",
        }
    )

    result = adapter.parse(manifest, cap, body, prepare(manifest, cap, {"limit": 5}))
    assert [record.data["name"] for record in result.records] == ["A"]


def test_csv_adapter_reports_a_missing_zip_member():
    from mouseion.envelope import ProviderError

    manifest = _csv_manifest()
    cap = manifest.capabilities["filings"]
    cap.zip_member = "absent.csv"
    adapter = get_adapter(manifest.adapter)
    body = _zip_body({"filings.csv": "name,office\nA,Mayor\n"})

    with pytest.raises(ProviderError) as exc:
        adapter.parse(manifest, cap, body, prepare(manifest, cap, {}))
    assert "absent.csv" in str(exc.value)


def test_csv_adapter_pages_a_bulk_file_without_reading_it_all():
    manifest = _csv_manifest()
    cap = manifest.capabilities["filings"]
    adapter = get_adapter(manifest.adapter)
    rows = "name,office\n" + "".join(f"P{i},Office\n" for i in range(100))
    body = {"_raw_text": rows}

    first = adapter.parse(manifest, cap, body, prepare(manifest, cap, {"limit": 5}))
    assert len(first.records) == 5
    assert first.next_cursor == "5"

    last = adapter.parse(
        manifest, cap, body, prepare(manifest, cap, {"limit": 5}, cursor="95")
    )
    assert [record.data["name"] for record in last.records] == [
        "P95",
        "P96",
        "P97",
        "P98",
        "P99",
    ]
    assert last.next_cursor is None

    past_end = adapter.parse(
        manifest, cap, body, prepare(manifest, cap, {"limit": 5}, cursor="100")
    )
    assert past_end.records == []
    assert past_end.next_cursor is None


# -- HTML tables ----------------------------------------------------------


def _table_manifest(headers=("NAME", "OFFICE"), index=0):
    from mouseion.manifest import Manifest

    return Manifest.model_validate(
        {
            "id": "example_table",
            "title": "Example Roster",
            "domain": "civic_local",
            "sub_category": "elected_officials",
            "source": {
                "originator": "Example City",
                "originator_domain": "example.gov",
                "auth_policy": {
                    "evidence_url": "https://example.gov",
                    "verified_at": "2026-10-30",
                },
            },
            "base": "https://example.gov",
            "adapter": "html_table",
            "capabilities": {
                "roster": {
                    "description": "Current officials",
                    "path": "/officials",
                    "table_headers": list(headers),
                    "table_index": index,
                    "fields": {"name": "name", "office": "office"},
                }
            },
        }
    )


def _html(body: str) -> dict[str, str]:
    return {"_raw_text": body}


def test_html_table_skips_navigation_tables_by_header():
    manifest = _table_manifest()
    cap = manifest.capabilities["roster"]
    adapter = get_adapter(manifest.adapter)
    page = """
    <html><body>
      <table><tr><th>Menu</th><th>Link</th></tr><tr><td>Home</td><td>/</td></tr></table>
      <table><tr><th>NAME</th><th>OFFICE</th></tr>
             <tr><td>Ada</td><td>Mayor</td></tr></table>
    </body></html>
    """

    result = adapter.parse(manifest, cap, _html(page), prepare(manifest, cap, {}))
    assert result.records[0].data == {"name": "Ada", "office": "Mayor"}


def test_html_table_index_selects_among_matching_tables():
    manifest = _table_manifest(index=1)
    cap = manifest.capabilities["roster"]
    adapter = get_adapter(manifest.adapter)
    page = """
    <table><tr><th>NAME</th><th>OFFICE</th></tr><tr><td>Ada</td><td>Mayor</td></tr></table>
    <table><tr><th>NAME</th><th>OFFICE</th></tr><tr><td>Grace</td><td>Judge</td></tr></table>
    """

    result = adapter.parse(manifest, cap, _html(page), prepare(manifest, cap, {}))
    assert result.records[0].data["name"] == "Grace"


def test_html_table_reports_missing_headers():
    from mouseion.envelope import ProviderError

    manifest = _table_manifest(headers=("CANDIDATE", "PARTY"))
    cap = manifest.capabilities["roster"]
    adapter = get_adapter(manifest.adapter)
    page = "<table><tr><th>NAME</th><th>OFFICE</th></tr><tr><td>A</td><td>B</td></tr></table>"

    with pytest.raises(ProviderError) as exc:
        adapter.parse(manifest, cap, _html(page), prepare(manifest, cap, {}))
    assert "CANDIDATE" in str(exc.value)


def test_html_table_disambiguates_duplicate_headers_and_short_rows():
    from mouseion.manifest import Manifest

    manifest = Manifest.model_validate(
        {
            "id": "dup",
            "title": "Dup",
            "domain": "civic_local",
            "sub_category": "elected_officials",
            "source": {
                "originator": "X",
                "originator_domain": "x.gov",
                "auth_policy": {"evidence_url": "https://x.gov", "verified_at": "2026-10-30"},
            },
            "base": "https://x.gov",
            "adapter": "html_table",
            "capabilities": {
                "roster": {
                    "description": "x",
                    "path": "/x",
                    "table_index": 0,
                    "fields": {"a": "name", "b": "name_2", "c": "office"},
                }
            },
        }
    )
    cap = manifest.capabilities["roster"]
    adapter = get_adapter(manifest.adapter)
    page = "<table><tr><th>Name</th><th>Name</th><th>Office</th></tr><tr><td>Ada</td><td>A</td></tr></table>"

    result = adapter.parse(manifest, cap, _html(page), prepare(manifest, cap, {}))
    assert result.records[0].data == {"a": "Ada", "b": "A", "c": ""}


def test_strip_html_cleans_a_search_snippet():
    from mouseion.manifest import Manifest

    manifest = Manifest.model_validate(
        {
            "id": "example_html_snippet",
            "title": "Example",
            "domain": "reference",
            "sub_category": "encyclopedia",
            "source": {
                "originator": "Example",
                "originator_domain": "example.org",
                "auth_policy": {"evidence_url": "https://example.org", "verified_at": "2026-10-31"},
            },
            "base": "https://example.org",
            "adapter": "rest_json",
            "capabilities": {
                "search": {
                    "description": "x",
                    "path": "/search",
                    "record_path": "results",
                    "fields": {"snippet": "snippet"},
                    "strip_html": ["snippet"],
                }
            },
        }
    )
    cap = manifest.capabilities["search"]
    adapter = get_adapter(manifest.adapter)
    body = {"results": [{"snippet": 'Augusta <span class="searchmatch">Ada</span> &amp; King'}]}

    result = adapter.parse(manifest, cap, body, prepare(manifest, cap, {}))
    assert result.records[0].data["snippet"] == "Augusta Ada & King"
