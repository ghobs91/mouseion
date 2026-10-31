"""Remote MCP providers with OAuth 2.1.

This is not REST auth. A remote MCP server speaks the Model Context Protocol over
Streamable HTTP and advertises its own OAuth metadata. The MCP SDK already
implements discovery, dynamic client registration, PKCE, token refresh, and the
authorization-code flow; this module supplies storage, a localhost callback
server, and the envelope conversion mouseion needs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
import traceback
import webbrowser
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp import types
from mcp.client.auth import OAuthClientProvider
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

from .cache import Cache
from .credentials import resolve
from .config import Settings
from .config import settings as default_settings
from .envelope import Envelope, ProviderError, Record, SourceStatus, utcnow
from .manifest import Manifest


class _OAuthNoiseFilter(logging.Filter):
    """Drop the SDK's traceback for the expected 'run auth login' path."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage() != "OAuth flow error" or not record.exc_info:
            return True
        text = "".join(traceback.format_exception(*record.exc_info))
        return "OAuth login required" not in text


logging.getLogger("mcp.client.auth.oauth2").addFilter(_OAuthNoiseFilter())


class OAuthStore:
    """SQLite-backed TokenStorage for one remote provider."""

    def __init__(self, cache: Cache, provider: str):
        self.cache = cache
        self.provider = provider

    async def get_tokens(self) -> OAuthToken | None:
        data = self.cache.get_oauth(self.provider)
        if not data or not data.get("tokens"):
            return None
        token = OAuthToken.model_validate(data["tokens"])
        expires_at = data.get("expires_at")
        if expires_at is not None and token.expires_in is not None:
            token.expires_in = max(0, int(expires_at - time.time()))
        return token

    async def set_tokens(self, tokens: OAuthToken) -> None:
        expires_at = time.time() + tokens.expires_in if tokens.expires_in else None
        self.cache.put_oauth(
            self.provider,
            tokens=tokens.model_dump(mode="json"),
            expires_at=expires_at,
        )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self.cache.get_oauth(self.provider)
        if not data or not data.get("client_info"):
            return None
        return OAuthClientInformationFull.model_validate(data["client_info"])

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.cache.put_oauth(
            self.provider,
            client_info=client_info.model_dump(mode="json"),
        )


class _CallbackServer:
    """One-shot localhost listener for the OAuth authorization code."""

    def __init__(self, redirect_uri: str):
        parsed = urlparse(redirect_uri)
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 8765
        self.path = parsed.path or "/callback"
        self._queue: queue.Queue[AuthorizationCodeResult | Exception] = queue.Queue()
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server API
                parsed = urlparse(self.path)
                if parsed.path != outer.path:
                    self.send_error(404)
                    return
                params = parse_qs(parsed.query)
                if "error" in params:
                    outer._queue.put(
                        ProviderError(f"OAuth error: {params['error'][0]}")
                    )
                else:
                    code = params.get("code", [None])[0]
                    state = params.get("state", [None])[0]
                    iss = params.get("iss", [None])[0]
                    if not code:
                        outer._queue.put(ProviderError("OAuth callback had no code"))
                    else:
                        outer._queue.put(
                            AuthorizationCodeResult(code=code, state=state, iss=iss)
                        )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h1>mouseion authorized</h1>"
                    b"<p>You can close this window and return to the terminal.</p>"
                )

            def log_message(self, *args: Any) -> None:
                return

        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            raise ProviderError(
                f"cannot start OAuth callback server on {self.host}:{self.port}: {exc}",
                hint="free the port or change auth.redirect_uri in the manifest",
            ) from None
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def wait(self, timeout: float = 300.0) -> AuthorizationCodeResult:
        try:
            result = self._queue.get(timeout=timeout)
        except queue.Empty:
            raise ProviderError("OAuth login timed out") from None
        if isinstance(result, Exception):
            raise result
        return result

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


class ClientCredentialsAuth(httpx2.Auth):
    """Machine-to-machine bearer auth for UCP/Shopify catalog servers."""

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 30.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ):
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self.transport = transport
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _get_token(self) -> str:
        async with self._lock:
            if self._token and time.time() < self._expires_at:
                return self._token
            try:
                async with httpx2.AsyncClient(
                    timeout=self.timeout, transport=self.transport
                ) as client:
                    response = await client.post(
                        self.token_url,
                        json={
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "grant_type": "client_credentials",
                        },
                    )
            except httpx2.HTTPError as exc:
                raise ProviderError(
                    f"client-credentials token request failed: {exc}"
                ) from None
            if response.status_code >= 400:
                raise ProviderError(
                    f"client-credentials token endpoint returned {response.status_code}: "
                    f"{response.text[:200]}"
                )
            try:
                token = response.json()["access_token"]
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(
                    f"unexpected client-credentials response: {exc}"
                ) from None
            self._token = str(token)
            # Shopify tokens are valid for 60 minutes; refresh early.
            self._expires_at = time.time() + 3_000
            return self._token

    async def async_auth_flow(self, request: httpx2.Request):
        token = await self._get_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            self._token = None
            token = await self._get_token()
            request.headers["Authorization"] = f"Bearer {token}"
            yield request


class RemoteMCPClient:
    """Connect to a remote MCP server, with OAuth when the manifest asks for it."""

    def __init__(self, cache: Cache, config: Settings | None = None):
        self.cache = cache
        self.config = config or default_settings

    def _metadata(self, manifest: Manifest) -> OAuthClientMetadata:
        auth = manifest.auth
        if not auth.redirect_uri:
            raise ProviderError(
                f"provider '{manifest.id}' has no OAuth redirect_uri",
                hint="set auth.redirect_uri in the manifest",
            )
        return OAuthClientMetadata(
            client_name=auth.client_name or "mouseion",
            redirect_uris=[auth.redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope=" ".join(auth.scopes) or None,
        )

    def _provider(
        self,
        manifest: Manifest,
        *,
        interactive: bool,
        callback: _CallbackServer | None,
    ) -> httpx2.Auth | None:
        auth = manifest.auth
        if auth.type == "client_credentials":
            if not auth.token_url or not auth.client_id_env or not auth.client_secret_env:
                raise ProviderError(
                    f"provider '{manifest.id}' has incomplete client credentials",
                    hint="set auth.token_url, auth.client_id_env, auth.client_secret_env",
                )
            client_id = resolve(auth.client_id_env)
            client_secret = resolve(auth.client_secret_env)
            if not client_id or not client_secret:
                raise ProviderError(
                    f"missing credentials for '{manifest.id}'",
                    hint=(
                        f"set {auth.client_id_env} and {auth.client_secret_env}, "
                        "or save them in the dashboard"
                    ),
                )
            return ClientCredentialsAuth(
                auth.token_url,
                client_id,
                client_secret,
            )
        if auth.type != "oauth":
            return None
        store = OAuthStore(self.cache, manifest.id)

        if interactive:
            assert callback is not None

            async def redirect(url: str) -> None:
                print(
                    f"\nOpen this URL to authorize {manifest.id}:\n{url}\n",
                    flush=True,
                )
                webbrowser.open(url)

            async def callback_handler() -> AuthorizationCodeResult:
                return await asyncio.to_thread(callback.wait, 300.0)

        else:

            async def redirect(url: str) -> None:
                raise ProviderError(
                    f"OAuth login required for '{manifest.id}'",
                    hint=f"run: mouseion auth login {manifest.id}",
                )

            async def callback_handler() -> AuthorizationCodeResult:
                raise ProviderError(
                    f"OAuth login required for '{manifest.id}'",
                    hint=f"run: mouseion auth login {manifest.id}",
                )

        return OAuthClientProvider(
            manifest.base,
            client_metadata=self._metadata(manifest),
            storage=store,
            redirect_handler=redirect,
            callback_handler=callback_handler,
        )

    @asynccontextmanager
    async def _session(
        self,
        manifest: Manifest,
        *,
        interactive: bool = False,
        callback: _CallbackServer | None = None,
    ):
        auth = self._provider(manifest, interactive=interactive, callback=callback)
        http = create_mcp_http_client(
            headers={"User-Agent": self.config.user_agent},
            auth=auth,
        )
        try:
            async with streamable_http_client(manifest.base, http_client=http) as (
                read,
                write,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except BaseExceptionGroup as exc:
            for inner in exc.exceptions:
                if isinstance(inner, ProviderError):
                    raise inner
            raise ProviderError(f"remote MCP error: {exc}") from None
        finally:
            await http.aclose()

    async def list_tools(
        self,
        manifest: Manifest,
        *,
        interactive: bool = False,
        callback: _CallbackServer | None = None,
    ) -> list[types.Tool]:
        async with self._session(
            manifest, interactive=interactive, callback=callback
        ) as session:
            result = await session.list_tools()
            return self._visible(manifest, result.tools)

    def _visible(self, manifest: Manifest, tools: list[types.Tool]) -> list[types.Tool]:
        if manifest.allow_tools:
            return [tool for tool in tools if tool.name in manifest.allow_tools]
        if manifest.read_only_only:
            return [
                tool
                for tool in tools
                if getattr(tool.annotations, "read_only_hint", False) is True
            ]
        return tools

    async def call(
        self, manifest: Manifest, tool: str, arguments: dict[str, Any]
    ) -> Envelope:
        if manifest.ucp_profile:
            arguments = _with_ucp_profile(arguments, manifest.ucp_profile)
        async with self._session(manifest) as session:
            result = await session.call_tool(tool, arguments)
        return envelope_from_result(manifest, tool, result)

    async def login(self, manifest: Manifest) -> list[types.Tool]:
        if manifest.auth.type != "oauth":
            raise ProviderError(
                f"provider '{manifest.id}' does not use OAuth",
            )
        if not manifest.auth.redirect_uri:
            raise ProviderError(
                f"provider '{manifest.id}' has no OAuth redirect_uri",
            )
        callback = _CallbackServer(manifest.auth.redirect_uri)
        callback.start()
        try:
            return await self.list_tools(
                manifest, interactive=True, callback=callback
            )
        finally:
            callback.stop()


def describe_remote(manifest: Manifest, tools: list[types.Tool]) -> dict[str, Any]:
    return {
        "id": manifest.id,
        "title": manifest.title,
        "transport": "mcp_remote",
        "server": manifest.base,
        "auth": manifest.auth.type,
        "ucp_profile": manifest.ucp_profile,
        "attribution": manifest.attribution,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "read_only": bool(
                    getattr(tool.annotations, "read_only_hint", False)
                ),
            }
            for tool in tools
        ],
        "hint": (
            "call provider_call with capability=<tool name> and params=<arguments>"
            if tools
            else "no visible tools; check read_only_only or allow_tools in the manifest"
        ),
    }


def envelope_from_result(
    manifest: Manifest, tool: str, result: types.CallToolResult
) -> Envelope:
    if result.is_error:
        text = " ".join(
            content.text
            for content in result.content
            if isinstance(content, types.TextContent)
        ).strip()
        raise ProviderError(text or f"remote tool '{tool}' failed")

    data: Any = result.structured_content
    if data is None:
        text = "\n".join(
            content.text
            for content in result.content
            if isinstance(content, types.TextContent)
        ).strip()
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"text": text}
        else:
            data = {}

    now = utcnow()
    records = [
        Record(
            source_record_id=_record_id(record),
            source_url=_record_url(record, manifest.base),
            retrieved_at=now,
            data=record,
        )
        for record in _records(data)
    ]
    return Envelope(
        provider=manifest.id,
        capability=tool,
        source_status=SourceStatus(url=manifest.base, http=200, fetched_at=now),
        attribution=manifest.attribution,
        terms=manifest.terms,
        records=records,
        missing=[],
        truncated=False,
    )


def _with_ucp_profile(arguments: dict[str, Any], profile: str) -> dict[str, Any]:
    """Inject the UCP agent profile into tool arguments without overwriting it."""
    meta = dict(arguments.get("meta") or {})
    agent = dict(meta.get("ucp-agent") or {})
    agent.setdefault("profile", profile)
    meta["ucp-agent"] = agent
    return {**arguments, "meta": meta}


def _records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = next(
            (
                data[key]
                for key in ("results", "items", "records", "data")
                if isinstance(data.get(key), list)
            ),
            [data],
        )
    else:
        items = [{"value": data}]
    return [item if isinstance(item, dict) else {"value": item} for item in items]


def _record_id(record: dict[str, Any]) -> str | None:
    for key in ("id", "uuid", "pk", "cve_id", "nct_id", "bill_id"):
        value = record.get(key)
        if value is not None:
            return str(value)
    return None


def _record_url(record: dict[str, Any], fallback: str) -> str:
    for key in ("source_url", "url", "html_url", "link", "legislation_url"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback
