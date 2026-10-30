"""Executes a provider call: prepare → cache → HTTP → normalize → envelope."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx

from .adapters import get_adapter
from .cache import Cache, cache_key
from .config import Settings
from .config import settings as default_settings
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
        timeout = manifest.timeout_s or self.config.timeout_s
        try:
            response = await self._http.request(
                prepared.method,
                prepared.url,
                params=prepared.params,
                headers=prepared.headers,
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

        raw = response.content
        if len(raw) > self.config.max_body_bytes:
            raise ProviderError(
                f"upstream response is {len(raw)} bytes, over the {self.config.max_body_bytes} limit",
                hint="narrow the filter or reduce 'limit'",
                status=response.status_code,
            )

        try:
            body = response.json()
        except ValueError:
            # Keep the full text: the Atom adapter needs every <entry>, and the
            # body-size limit above already bounds this.
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
