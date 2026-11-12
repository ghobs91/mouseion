from __future__ import annotations

import asyncio

import httpx

from mouseion.bootstrap import build


def run(coro):
    return asyncio.run(coro)


def test_client_credentials_token_is_cached(config, monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "client-secret")
    calls = {"token": 0, "search": 0}
    auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            calls["token"] += 1
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 7200})
        calls["search"] += 1
        auth.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"itemSummaries": []})

    app = build(config, transport=httpx.MockTransport(handler))
    try:
        for _ in range(2):
            run(app.client.call("ebay_items", "search", {"query": "lens"}, refresh=True))
    finally:
        run(app.aclose())

    assert calls == {"token": 1, "search": 2}
    assert auth == ["Bearer token-1", "Bearer token-1"]


def test_client_credentials_refreshes_after_a_401(config, monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "client-id")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "client-secret")
    calls = {"token": 0, "search": 0}
    auth: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": f"token-{calls['token']}"})
        calls["search"] += 1
        auth.append(request.headers.get("authorization", ""))
        if calls["search"] == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, json={"itemSummaries": []})

    app = build(config, transport=httpx.MockTransport(handler))
    try:
        envelope = run(app.client.call("ebay_items", "search", {"query": "lens"}))
    finally:
        run(app.aclose())

    assert envelope.records == []
    assert calls == {"token": 2, "search": 2}
    assert auth == ["Bearer token-1", "Bearer token-2"]
