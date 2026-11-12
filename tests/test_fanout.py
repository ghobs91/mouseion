from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from mouseion.bootstrap import build
from mouseion.browser import BrowserClient
from mouseion.envelope import ProviderError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MEMBERS = ["bestbuy_products", "walmart_products", "ebay_items", "costco_products"]


def body(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}__search.json").read_text(encoding="utf-8"))["body"]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def retailer_env(monkeypatch, walmart_private_key):
    monkeypatch.setenv("BESTBUY_API_KEY", "fixture-key")
    monkeypatch.setenv("WALMART_CONSUMER_ID", "fixture-consumer")
    monkeypatch.setenv("WALMART_PRIVATE_KEY", walmart_private_key)
    monkeypatch.setenv("WALMART_KEY_VERSION", "1")
    monkeypatch.setenv("EBAY_CLIENT_ID", "fixture-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "fixture-secret")

    async def _browser(_self, _manifest, _prepared, _capability):
        return body("costco_products")

    monkeypatch.setattr(BrowserClient, "fetch", _browser)


def handler_for(overrides: dict[str, httpx.Response | None] | None = None):
    overrides = overrides or {}

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        for member, response in overrides.items():
            if member == "bestbuy_products" and host == "api.bestbuy.com":
                return response or httpx.Response(500, json={"error": "boom"})
            if member == "walmart_products" and host == "developer.api.walmart.com":
                return response or httpx.Response(500, json={"error": "boom"})
            if member == "ebay_items" and host == "api.ebay.com" and path != "/identity/v1/oauth2/token":
                return response or httpx.Response(500, json={"error": "boom"})

        if host == "api.bestbuy.com":
            return httpx.Response(200, json=body("bestbuy_products"))
        if host == "developer.api.walmart.com":
            return httpx.Response(200, json=body("walmart_products"))
        if host == "api.ebay.com":
            if path == "/identity/v1/oauth2/token":
                return httpx.Response(200, json={"access_token": "fixture", "expires_in": 7200})
            return httpx.Response(200, json=body("ebay_items"))
        return httpx.Response(404)

    return handler


def test_fanout_merges_every_retailer(config, retailer_env):
    app = build(config, transport=httpx.MockTransport(handler_for()))
    try:
        envelope = run(
            app.client.call("retail_search", "search", {"query": "headphones", "limit": 3})
        )
    finally:
        run(app.aclose())

    assert envelope.provider == "retail_search"
    assert len(envelope.records) == 11
    assert envelope.truncated is True

    seen = {record.data["retailer_provider"] for record in envelope.records}
    assert seen == set(MEMBERS)
    for record in envelope.records:
        assert record.source_record_id
        assert record.source_record_id.startswith(tuple(f"{m}:" for m in MEMBERS))
        assert record.source_url and record.source_url.startswith("https://")
        assert record.data["retailer"]
        assert record.data["currency"] == "USD"
        assert isinstance(record.data["price"], float)

    # Relevance keeps retailer order, so the first four offers are one per retailer.
    assert [r.data["retailer_provider"] for r in envelope.records[:4]] == MEMBERS


def test_fanout_retailer_filter(config, retailer_env):
    seen_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_hosts.append(request.url.host)
        return handler_for()(request)

    app = build(config, transport=httpx.MockTransport(handler))
    try:
        envelope = run(
            app.client.call(
                "retail_search",
                "search",
                {"query": "lens", "limit": 3, "retailers": ["ebay", "costco"]},
            )
        )
    finally:
        run(app.aclose())

    assert len(envelope.records) == 5
    assert {r.data["retailer_provider"] for r in envelope.records} == {
        "ebay_items",
        "costco_products",
    }
    assert "api.bestbuy.com" not in seen_hosts
    assert "developer.api.walmart.com" not in seen_hosts


def test_fanout_survives_one_failed_retailer(config, retailer_env):
    app = build(
        config,
        transport=httpx.MockTransport(
            handler_for({"walmart_products": httpx.Response(403, json={"error": "blocked"})})
        ),
    )
    try:
        envelope = run(
            app.client.call("retail_search", "search", {"query": "vacuum", "limit": 3})
        )
    finally:
        run(app.aclose())

    assert len(envelope.records) == 8
    assert any(item.startswith("walmart_products:") for item in envelope.missing)
    assert "walmart_products=error" in (envelope.source_status.upstream_version or "")


def test_fanout_all_failed_is_an_error(config, retailer_env, monkeypatch):
    overrides = {member: httpx.Response(500, json={"error": "boom"}) for member in MEMBERS}

    async def _broken_browser(_self, _manifest, _prepared, _capability):
        raise ProviderError("browser failed")

    monkeypatch.setattr(BrowserClient, "fetch", _broken_browser)
    app = build(config, transport=httpx.MockTransport(handler_for(overrides)))
    try:
        with pytest.raises(ProviderError) as exc:
            run(app.client.call("retail_search", "search", {"query": "tv", "limit": 2}))
    finally:
        run(app.aclose())
    assert "all retailers failed" in str(exc.value)


def test_fanout_price_sort_puts_cheapest_first(config, retailer_env):
    app = build(config, transport=httpx.MockTransport(handler_for()))
    try:
        envelope = run(
            app.client.call(
                "retail_search",
                "search",
                {"query": "headphones", "limit": 3, "sort": "price"},
            )
        )
    finally:
        run(app.aclose())

    prices = [record.data["price"] for record in envelope.records]
    assert prices == sorted(prices)


def test_fanout_cursor_advances_each_retailer(config, retailer_env):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler_for()(request)

    app = build(config, transport=httpx.MockTransport(handler))
    try:
        first = run(
            app.client.call("retail_search", "search", {"query": "headphones", "limit": 3})
        )
        assert first.cursor and first.cursor.next
        state = json.loads(first.cursor.next)
        assert state["walmart_products"] == "4"
        assert state["ebay_items"] == "3"

        run(
            app.client.call(
                "retail_search",
                "search",
                {"query": "headphones", "limit": 3},
                first.cursor.next,
            )
        )
    finally:
        run(app.aclose())

    walmart = [r for r in seen if r.url.host == "developer.api.walmart.com"][-1]
    ebay = [
        r
        for r in seen
        if r.url.host == "api.ebay.com" and r.url.path != "/identity/v1/oauth2/token"
    ][-1]
    assert walmart.url.params["start"] == "4"
    assert ebay.url.params["offset"] == "3"


def test_fanout_rejects_an_unknown_retailer(config, retailer_env):
    app = build(config, transport=httpx.MockTransport(handler_for()))
    try:
        with pytest.raises(ProviderError) as exc:
            run(
                app.client.call(
                    "retail_search", "search", {"query": "tv", "retailers": ["target"]}
                )
            )
    finally:
        run(app.aclose())
    assert "unknown retailer" in str(exc.value)


def test_fanout_is_reachable_through_the_tool_surface(config, retailer_env):
    app = build(config, transport=httpx.MockTransport(handler_for()))
    try:
        out = run(
            app.toolkit.provider_call(
                "retail_search", "search", {"query": "headphones", "limit": 1}
            )
        )
    finally:
        run(app.aclose())

    assert out["provider"] == "retail_search"
    assert len(out["records"]) == 10
    assert out["cursor"]["next"]
    assert "more records available" in out["hint"]
