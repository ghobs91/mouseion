from __future__ import annotations

import asyncio

import httpx

from mouseion.bootstrap import build
from mouseion.tools import html_to_text


def run(coro):
    return asyncio.run(coro)


def make_app(config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body><h1>Title</h1><p>Body text</p></body></html>")

    return build(config, transport=httpx.MockTransport(handler))


def test_html_to_text_drops_scripts_and_keeps_structure():
    html = """
    <html><head><style>.x{}</style><script>var a=1;</script></head>
    <body><h1>Recall notice</h1><p>Firm recalled   batches.</p><ul><li>one</li><li>two</li></ul></body></html>
    """
    text = html_to_text(html)
    assert "var a=1" not in text
    assert ".x{}" not in text
    assert "Recall notice" in text
    assert "Firm recalled batches." in text
    assert "one" in text and "two" in text


def test_fetch_url_refuses_loopback(config):
    app = make_app(config)
    out = run(app.toolkit.fetch_url("http://127.0.0.1:8080/admin"))
    run(app.aclose())
    assert "refusing" in out["error"]


def test_fetch_url_rejects_non_http_scheme(config):
    app = make_app(config)
    out = run(app.toolkit.fetch_url("file:///etc/passwd"))
    run(app.aclose())
    assert "scheme" in out["error"]


def test_fetch_url_returns_text(config):
    app = make_app(config)
    out = run(app.toolkit.fetch_url("https://example.com/notice"))
    run(app.aclose())
    assert out["http"] == 200
    assert "Title" in out["text"]
    assert "Body text" in out["text"]
    assert out["truncated"] is False


def test_providers_search_ranks_openfda_for_recalls(config):
    app = make_app(config)
    out = run(app.toolkit.providers_search("drug recall"))
    run(app.aclose())
    assert out["providers"][0]["id"] == "openfda"


def test_providers_search_retries_without_a_wrong_category(config):
    app = make_app(config)
    out = run(
        app.toolkit.providers_search(
            "Class I drug recalls recent FDA", category="government"
        )
    )
    run(app.aclose())
    assert out["providers"][0]["id"] == "openfda"
    assert out["category_fallback"] is True
    assert "provider_describe" in out["hint"]


def test_provider_describe_exposes_params(config):
    app = make_app(config)
    out = run(app.toolkit.provider_describe("federal_register"))
    run(app.aclose())
    terms = out["capabilities"]["search"]["params"]
    assert "term" in terms
    assert terms["limit"]["type"] == "integer"
    assert out["attribution"]
