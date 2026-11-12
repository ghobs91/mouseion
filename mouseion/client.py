"""Executes a provider call: prepare → cache → HTTP → normalize → envelope."""

from __future__ import annotations

import base64
import json
import time
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import httpx

from .adapters import get_adapter
from .browser import BrowserClient
from .cache import Cache, cache_key
from .config import Settings
from .config import settings as default_settings
from .credentials import resolve
from .envelope import (
    Cursor,
    Envelope,
    ProviderError,
    SourceStatus,
    hint_for_status,
    utcnow,
)
from .manifest import Capability, Manifest
from .registry import Registry
from .remote_mcp import RemoteMCPClient


class ProviderClient:
    def __init__(
        self,
        registry: Registry,
        cache: Cache,
        *,
        config: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.registry = registry
        self.cache = cache
        self.config = config or default_settings
        self.remote = RemoteMCPClient(cache, self.config)
        self.browser = BrowserClient(self.config)
        #: Bearer tokens, keyed by provider. Tokens are process-local and never
        #: enter the response cache.
        self._tokens: dict[str, tuple[str, float]] = {}
        self._http = httpx.AsyncClient(
            timeout=self.config.timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def raw(
        self,
        provider: str,
        capability: str,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
        *,
        refresh: bool = True,
    ) -> Any:
        """Return the upstream body, unparsed. Used by the fixture recorder."""
        manifest = self._resolve(provider)
        if manifest.transport in {"mcp_remote", "fanout"}:
            raise ProviderError(
                f"provider '{manifest.id}' is a {manifest.transport} provider",
                hint="remote MCP and fanout providers cannot be recorded as fixtures",
            )
        cap = self._resolve_capability(manifest, capability)
        adapter = get_adapter(cap.adapter or manifest.adapter)
        prepared = adapter.prepare(manifest, cap, dict(params or {}), cursor)

        cached = None if refresh else self.cache.get(
            cache_key(manifest.id, capability, {"query": prepared.params, "body": prepared.json_body}, cursor)
        )
        if cached is not None:
            return cached.body
        body, _ = await self._fetch(manifest, prepared, cap)
        return body

    async def call(
        self,
        provider: str,
        capability: str,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
        *,
        refresh: bool = False,
    ) -> Envelope:
        manifest = self._resolve(provider)
        if manifest.transport == "mcp_remote":
            return await self.remote.call(manifest, capability, dict(params or {}))
        if manifest.transport == "fanout":
            from .fanout import call_fanout

            return await call_fanout(
                self, manifest, capability, dict(params or {}), cursor, refresh=refresh
            )
        envelope, _ = await self.call_with_body(
            provider, capability, params, cursor, refresh=refresh
        )
        return envelope

    async def call_with_body(
        self,
        provider: str,
        capability: str,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
        *,
        refresh: bool = False,
    ) -> tuple[Envelope, Any]:
        """Same as call(), but also returns the raw upstream body (fixture recording)."""
        manifest = self._resolve(provider)
        if manifest.transport in {"mcp_remote", "fanout"}:
            raise ProviderError(
                f"provider '{manifest.id}' is a {manifest.transport} provider",
                hint="remote MCP and fanout providers cannot be recorded as fixtures",
            )
        cap = self._resolve_capability(manifest, capability)
        adapter = get_adapter(cap.adapter or manifest.adapter)

        prepared = adapter.prepare(manifest, cap, dict(params or {}), cursor)
        cache_params = {"query": prepared.params, "body": prepared.json_body}
        key = cache_key(
            manifest.id,
            capability,
            cache_params,
            None if prepared.shared_cache_key else cursor,
        )
        ttl = manifest.ttl(self.config.default_ttl_s)

        cached = None if refresh else self.cache.get(key)
        if cached is not None:
            body, status, http_age = cached.body, cached.status, cached.age_s
        else:
            body, status = await self._fetch(manifest, prepared, cap)
            http_age = 0.0
            if ttl > 0 and status < 400:
                self.cache.put(
                    key,
                    provider=manifest.id,
                    capability=capability,
                    params=cache_params,
                    status=status,
                    body=body,
                    ttl=ttl,
                )

        result = adapter.parse(manifest, cap, body, prepared)

        # Provenance is the point of the envelope: report when the bytes were fetched,
        # not when we happened to read them out of the cache.
        fetched_at = utcnow() - timedelta(seconds=http_age)

        return Envelope(
            provider=manifest.id,
            capability=capability,
            source_status=SourceStatus(
                url=prepared.url,
                http=status,
                fetched_at=fetched_at,
                cache_age_s=round(http_age, 1),
                upstream_version=result.upstream_version,
            ),
            attribution=manifest.attribution,
            terms=result.terms or manifest.terms,
            cursor=None
            if result.next_cursor is None
            else Cursor(
                snapshot=fetched_at.isoformat(timespec="seconds"),
                next=result.next_cursor,
            ),
            records=result.records,
            missing=result.missing,
            truncated=result.next_cursor is not None,
        ), body

    # -- internals ------------------------------------------------------

    def _resolve(self, provider: str) -> Manifest:
        try:
            return self.registry.get(provider)
        except KeyError as exc:
            raise ProviderError(str(exc).strip("'\""), hint="use providers_search first") from None

    def _resolve_capability(self, manifest: Manifest, capability: str) -> Capability:
        name = capability.strip().lower().replace("-", "_")
        if name in manifest.capabilities:
            return manifest.capabilities[name]
        raise ProviderError(
            f"provider '{manifest.id}' has no capability '{capability}'",
            hint=f"available: {sorted(manifest.capabilities)}",
        )

    async def _fetch(
        self, manifest: Manifest, prepared: Any, cap: Capability
    ) -> tuple[Any, int]:
        if manifest.transport == "browser":
            return await self.browser.fetch(manifest, prepared, cap), 200

        headers = dict(prepared.headers)
        if manifest.auth.type == "client_credentials":
            headers["Authorization"] = (
                f"Bearer {await self._client_credentials_token(manifest)}"
            )

        retried_token = False
        while True:
            response = await self._send(manifest, prepared, headers)
            if (
                response.status_code == 401
                and manifest.auth.type == "client_credentials"
                and not retried_token
            ):
                self._tokens.pop(manifest.id, None)
                headers["Authorization"] = (
                    f"Bearer {await self._client_credentials_token(manifest)}"
                )
                retried_token = True
                continue
            break

        raw = response.content
        limit = manifest.max_body_bytes or self.config.max_body_bytes
        if len(raw) > limit:
            raise ProviderError(
                f"upstream response is {len(raw)} bytes, over the {limit} limit",
                hint="narrow the filter or reduce 'limit'",
                status=response.status_code,
            )

        try:
            body = response.json()
        except ValueError:
            # Keep the full text: the Atom adapter needs every <entry>, and the
            # body-size limit above already bounds this. Binary bulk archives are
            # base64 so the cached body stays JSON-serializable.
            if raw[:4] == b"PK\x03\x04" or "zip" in response.headers.get("content-type", ""):
                body = {"_raw_b64": base64.b64encode(raw).decode("ascii")}
            else:
                body = {"_raw_text": response.text}

        if response.status_code >= 400:
            if response.status_code == 404 and cap.empty_on_404:
                return ([] if cap.record_path is None else {}), 404
            raise ProviderError(
                _upstream_message(body, response.status_code),
                hint=hint_for_status(response.status_code),
                status=response.status_code,
            )
        return body, response.status_code

    async def _send(
        self, manifest: Manifest, prepared: Any, headers: dict[str, str]
    ) -> httpx.Response:
        timeout = manifest.timeout_s or self.config.timeout_s
        try:
            return await self._http.request(
                prepared.method,
                prepared.url,
                params=prepared.params,
                headers=headers,
                json=prepared.json_body,
                timeout=timeout,
            )
        except httpx.TimeoutException:
            raise ProviderError(
                f"upstream timeout after {timeout}s",
                hint="narrow the query or retry",
            ) from None
        except httpx.HTTPError as exc:
            raise ProviderError(f"upstream request failed: {exc}") from None

    async def _client_credentials_token(self, manifest: Manifest) -> str:
        """Fetch and cache an OAuth client-credentials bearer token."""
        auth = manifest.auth
        cached = self._tokens.get(manifest.id)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        if not auth.token_url or not auth.client_id_env or not auth.client_secret_env:
            raise ProviderError(
                f"provider '{manifest.id}' declares incomplete client_credentials auth",
                hint="token_url, client_id_env and client_secret_env are required",
            )

        client_id = resolve(auth.client_id_env)
        client_secret = resolve(auth.client_secret_env)
        if not client_id or not client_secret:
            missing = [
                name
                for name, value in (
                    (auth.client_id_env, client_id),
                    (auth.client_secret_env, client_secret),
                )
                if not value
            ]
            raise ProviderError(
                f"missing credential for provider '{manifest.id}'",
                hint=f"set {', '.join(missing)} in the environment or save it in the dashboard",
            )

        # RFC 6749 §2.3.1: encode the client id/secret before base64-encoding the
        # pair, or special characters corrupt the Basic value (eBay is strict).
        basic = base64.b64encode(
            f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}".encode()
        ).decode()
        form = {"grant_type": "client_credentials"}
        if auth.scopes:
            form["scope"] = " ".join(auth.scopes)

        try:
            response = await self._http.post(
                auth.token_url,
                data=form,
                headers={"Authorization": f"Basic {basic}", "Accept": "application/json"},
            )
        except httpx.TimeoutException:
            raise ProviderError(
                f"token endpoint timed out for provider '{manifest.id}'",
                hint="retry, or check the token_url in the manifest",
            ) from None
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"token request failed for provider '{manifest.id}': {exc}"
            ) from None

        try:
            payload = response.json()
        except ValueError:
            payload = None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code >= 400 or not token:
            message = (
                _upstream_message(payload, response.status_code)
                if payload is not None
                else f"token endpoint returned HTTP {response.status_code}"
            )
            raise ProviderError(
                message,
                hint=f"check the client credentials for '{manifest.id}'",
                status=response.status_code,
            )

        try:
            expires = float(payload.get("expires_in") or 300)
        except (TypeError, ValueError):
            expires = 300.0
        self._tokens[manifest.id] = (str(token), time.monotonic() + max(1.0, expires - 60))
        return str(token)


def _upstream_message(body: Any, status: int) -> str:
    if isinstance(body, dict):
        for key in ("error", "message", "detail", "errors"):
            value = body.get(key)
            if not value:
                continue
            if isinstance(value, dict):
                value = value.get("message") or value.get("code") or value
            if not isinstance(value, str):
                value = json.dumps(value, default=str)
            return f"upstream {status}: {' '.join(value.split())[:300]}"
        text = body.get("_raw_text")
        if text:
            return f"upstream {status}: {' '.join(text.split())[:300]}"
    return f"upstream returned HTTP {status}"
