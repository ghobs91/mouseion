from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from mcp import types
from mcp.shared.auth import OAuthToken

import httpx2

from mouseion.audit import audit_manifest
from mouseion.bootstrap import build
from mouseion.cache import Cache
from mouseion.manifest import Manifest
from mouseion.registry import Registry
from mouseion.remote_mcp import (
    ClientCredentialsAuth,
    OAuthStore,
    RemoteMCPClient,
    _with_ucp_profile,
    describe_remote,
    envelope_from_result,
)

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def test_oauth_store_round_trip(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    store = OAuthStore(cache, "demo")
    token = OAuthToken(
        access_token="access",
        token_type="Bearer",
        expires_in=3600,
        refresh_token="refresh",
    )
    run(store.set_tokens(token))

    loaded = run(store.get_tokens())
    assert loaded is not None
    assert loaded.access_token == "access"
    assert loaded.refresh_token == "refresh"
    assert loaded.expires_in is not None and loaded.expires_in > 0
    assert cache.oauth_status()["demo"] == {"client": False, "tokens": True}
    cache.close()


def test_envelope_from_text_result():
    manifest = Registry.load(ROOT / "providers").get("courtlistener_mcp")
    result = types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text='{"results": [{"id": "1", "title": "x", "url": "https://example.com/1"}]}',
            )
        ]
    )

    envelope = envelope_from_result(manifest, "search", result)

    assert len(envelope.records) == 1
    assert envelope.records[0].source_record_id == "1"
    assert envelope.records[0].source_url == "https://example.com/1"
    assert envelope.records[0].data["title"] == "x"


def test_envelope_from_structured_content():
    manifest = Registry.load(ROOT / "providers").get("courtlistener_mcp")
    result = types.CallToolResult(
        content=[],
        structuredContent={"items": [{"id": 2, "title": "y"}]},
    )

    envelope = envelope_from_result(manifest, "search", result)

    assert envelope.records[0].source_record_id == "2"
    assert envelope.records[0].source_url == manifest.base


def test_visible_tools_filters_read_only(tmp_path):
    manifest = Registry.load(ROOT / "providers").get("courtlistener_mcp")
    manifest = manifest.model_copy(deep=True)
    client = RemoteMCPClient(Cache(tmp_path / "cache.db"))
    tools = [
        types.Tool(
            name="read",
            description="read",
            inputSchema={},
            annotations=types.ToolAnnotations(readOnlyHint=True),
        ),
        types.Tool(
            name="write",
            description="write",
            inputSchema={},
            annotations=types.ToolAnnotations(readOnlyHint=False),
        ),
    ]

    assert [tool.name for tool in client._visible(manifest, tools)] == ["read"]

    manifest.allow_tools = ["write"]
    assert [tool.name for tool in client._visible(manifest, tools)] == ["write"]


def test_describe_remote_lists_schemas():
    manifest = Registry.load(ROOT / "providers").get("courtlistener_mcp")
    tools = [
        types.Tool(
            name="search",
            description="Search opinions",
            inputSchema={"type": "object", "properties": {"q": {"type": "string"}}},
            annotations=types.ToolAnnotations(readOnlyHint=True),
        )
    ]

    described = describe_remote(manifest, tools)

    assert described["transport"] == "mcp_remote"
    assert described["tools"][0]["name"] == "search"
    assert described["tools"][0]["read_only"] is True


def test_remote_manifest_passes_audit():
    manifest = Registry.load(ROOT / "providers").get("courtlistener_mcp")
    findings = audit_manifest(manifest, today=date(2026, 10, 30))
    errors = [finding for finding in findings if finding.severity == "error"]
    assert errors == []


def test_shopify_manifest_passes_audit():
    manifest = Registry.load(ROOT / "providers").get("shopify_catalog")
    findings = audit_manifest(manifest, today=date(2026, 10, 30))
    errors = [finding for finding in findings if finding.severity == "error"]
    assert errors == []


def test_client_credentials_auth_fetches_bearer_token():
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://example.test/token"
        return httpx2.Response(200, json={"access_token": "test-token"})

    auth = ClientCredentialsAuth(
        "https://example.test/token",
        "client-id",
        "client-secret",
        transport=httpx2.MockTransport(handler),
    )

    async def scenario():
        request = httpx2.Request("GET", "https://example.test/mcp")
        flow = auth.async_auth_flow(request)
        outgoing = await flow.__anext__()
        assert outgoing.headers["Authorization"] == "Bearer test-token"
        await flow.aclose()

    run(scenario())


def test_ucp_profile_is_injected_without_clobbering_arguments():
    arguments = _with_ucp_profile(
        {"catalog": {"query": "running shoes"}},
        "https://example.test/.well-known/ucp",
    )
    assert arguments["catalog"]["query"] == "running shoes"
    assert (
        arguments["meta"]["ucp-agent"]["profile"]
        == "https://example.test/.well-known/ucp"
    )


def test_provider_describe_and_call_use_the_remote_client(config):
    app = build(config)
    manifest = app.registry.get("courtlistener_mcp")
    tool = types.Tool(
        name="search",
        description="Search opinions",
        inputSchema={"type": "object"},
        annotations=types.ToolAnnotations(readOnlyHint=True),
    )

    async def fake_list_tools(manifest_arg: Manifest):
        assert manifest_arg.id == "courtlistener_mcp"
        return [tool]

    async def fake_call(manifest_arg: Manifest, name: str, arguments: dict):
        assert name == "search"
        return envelope_from_result(
            manifest_arg,
            name,
            types.CallToolResult(
                content=[types.TextContent(type="text", text='{"id": "1"}')]
            ),
        )

    app.client.remote.list_tools = fake_list_tools  # type: ignore[method-assign]
    app.client.remote.call = fake_call  # type: ignore[method-assign]
    try:
        described = run(app.toolkit.provider_describe("courtlistener_mcp"))
        called = run(
            app.toolkit.provider_call(
                "courtlistener_mcp", "search", {"q": "qualified immunity"}
            )
        )
    finally:
        run(app.aclose())

    assert described["transport"] == "mcp_remote"
    assert described["tools"][0]["name"] == "search"
    assert called["provider"] == "courtlistener_mcp"
    assert called["records"][0]["source_record_id"] == "1"
