from __future__ import annotations

import asyncio

import httpx
import pytest

from mouseion.bootstrap import build
from mouseion.envelope import ProviderError
from tests.conftest import FDA_EMPTY_404, STUDIES_BODY


def run(coro):
    return asyncio.run(coro)


def make_app(config, handler):
    return build(config, transport=httpx.MockTransport(handler))


def test_call_normalizes_and_paginates(config):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=STUDIES_BODY)

    app = make_app(config, handler)
    out = run(app.toolkit.provider_call("clinicaltrials_gov", "search", {"condition": "melanoma"}))
    run(app.aclose())

    assert out["provider"] == "clinicaltrials_gov"
    assert out["attribution"].startswith("ClinicalTrials.gov")
    assert len(out["records"]) == 1
    record = out["records"][0]
    assert record["source_record_id"] == "NCT04631731"
    assert record["source_url"] == "https://clinicaltrials.gov/study/NCT04631731"
    assert out["truncated"] is True
    assert out["cursor"]["next"] == "TOKEN-2"
    assert "query.cond=melanoma" in seen[0]


def test_cache_hit_avoids_second_request(config):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=STUDIES_BODY)

    app = make_app(config, handler)
    run(app.toolkit.provider_call("clinicaltrials_gov", "search", {"condition": "melanoma"}))
    second = run(
        app.toolkit.provider_call("clinicaltrials_gov", "search", {"condition": "melanoma"})
    )
    run(app.aclose())

    assert calls["n"] == 1
    assert second["source_status"]["cache_age_s"] >= 0


def test_refresh_bypasses_cache(config):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=STUDIES_BODY)

    app = make_app(config, handler)
    run(app.toolkit.provider_call("clinicaltrials_gov", "search", {"condition": "melanoma"}))
    run(
        app.toolkit.provider_call(
            "clinicaltrials_gov", "search", {"condition": "melanoma"}, refresh=True
        )
    )
    run(app.aclose())
    assert calls["n"] == 2


def test_404_with_empty_on_404_is_an_empty_page(config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json=FDA_EMPTY_404)

    app = make_app(config, handler)
    out = run(app.toolkit.provider_call("openfda", "drug_recalls", {"search": 'x:"y"'}))
    run(app.aclose())

    assert out["records"] == []
    assert out["source_status"]["http"] == 404


def test_upstream_error_carries_a_hint(config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "Too many requests"}})

    app = make_app(config, handler)
    out = run(app.toolkit.provider_call("openfda", "drug_recalls", {"search": 'x:"y"'}))
    run(app.aclose())

    assert "429" in out["error"] or "Too many" in out["error"]
    assert "rate limit" in out["hint"].lower()


def test_unknown_provider_is_a_structured_error(config):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(500)

    app = make_app(config, handler)
    out = run(app.toolkit.provider_call("nope", "search", {}))
    run(app.aclose())
    assert "unknown provider" in out["error"]
    assert "hint" in out


def test_unknown_capability_lists_valid_names(config):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(500)

    app = make_app(config, handler)
    out = run(app.toolkit.provider_call("openfda", "search", {}))
    run(app.aclose())
    assert "drug_recalls" in out["hint"]


def test_provider_error_type_for_direct_client_use(config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    app = make_app(config, handler)
    with pytest.raises(ProviderError) as exc:
        run(app.client.call("openfda", "drug_recalls", {"search": "x:y"}))
    run(app.aclose())
    assert exc.value.status == 500


# -- unstable upstream windows -------------------------------------------


SEC_BODY = {
    "hits": {
        "hits": [
            {
                "_id": f"0000000000-26-0000{i:02d}:doc.htm",
                "_source": {
                    "display_names": [f"Company {i}"],
                    "ciks": [f"{i:010d}"],
                    "form": "10-K",
                    "file_date": "2026-01-01",
                    "adsh": f"0000000000-26-0000{i:02d}",
                },
            }
            for i in range(10)
        ],
        "total": {"value": 100},
    }
}


def test_local_window_pages_from_one_fetch(config, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "test test@example.com")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=SEC_BODY)

    app = make_app(config, handler)
    params = {"q": '"ai"', "forms": "10-K", "limit": 3}
    first = run(app.toolkit.provider_call("sec_edgar", "full_text_search", params))
    second = run(
        app.toolkit.provider_call(
            "sec_edgar", "full_text_search", params, cursor=first["cursor"]["next"]
        )
    )
    run(app.aclose())

    assert calls["n"] == 1, "second page must come from the cached window"
    ids_first = [record["source_record_id"] for record in first["records"]]
    ids_second = [record["source_record_id"] for record in second["records"]]
    assert len(ids_first) == 3
    assert not set(ids_first) & set(ids_second)
