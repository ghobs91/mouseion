"""The four tools the model sees. This is the entire public surface.

Three of them are the routing loop (search → describe → call); the fourth is the
escape hatch that keeps a missing adapter from becoming a dead end.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from .cache import Cache, cache_key
from .client import ProviderClient
from .config import Settings
from .config import settings as default_settings
from .envelope import ProviderError, utcnow
from .registry import Registry
from .router import Router

MAX_FETCH_CHARS = 20_000

log = logging.getLogger(__name__)


class _TextExtractor(HTMLParser):
    """HTML → readable text. Not a scraper; just enough to feed a model."""

    _SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "template", "svg"}
    _BREAK: ClassVar[set[str]] = {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._depth += 1
        elif tag in self._BREAK and not self._depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts).splitlines()
        lines = [" ".join(line.split()) for line in raw]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _assert_public_target(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ProviderError(f"unsupported scheme '{parsed.scheme}'", hint="use http or https")
    if not parsed.hostname:
        raise ProviderError("URL has no host")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        raise ProviderError(f"cannot resolve host '{parsed.hostname}'") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ProviderError(
                f"refusing to fetch private address {ip}",
                hint="set MOUSEION_ALLOW_PRIVATE_URLS=1 only if you trust the model's input",
            )


class Toolkit:
    def __init__(
        self,
        registry: Registry,
        client: ProviderClient,
        cache: Cache,
        *,
        router: Router | None = None,
        config: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.registry = registry
        self.client = client
        self.cache = cache
        self.router = router or Router(registry, cache)
        self.config = config or default_settings
        self._http = httpx.AsyncClient(
            timeout=self.config.timeout_s,
            follow_redirects=True,
            transport=transport,
            headers={"User-Agent": self.config.user_agent},
        )

    async def aclose(self) -> None:
        await self._http.aclose()
        await self.client.aclose()
        await self.router.aclose()

    # -- tool 1 ---------------------------------------------------------

    async def providers_search(
        self, query: str, category: str | None = None, limit: int = 8
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 25))
        try:
            results = await self.router.search(query, category=category, limit=limit)
        except Exception as exc:  # noqa: BLE001 - a tool must never raise into the transport
            log.warning("router failed, falling back to lexical: %s", exc)
            results = self.registry.search(query, category=category, limit=limit)

        category_fallback = False
        if not results and category:
            # A model that guesses a category should not lose the whole search.
            try:
                results = await self.router.search(query, limit=limit)
            except Exception as exc:  # noqa: BLE001
                log.warning("unfiltered router fallback failed: %s", exc)
                results = self.registry.search(query, limit=limit)
            category_fallback = bool(results)

        out: dict[str, Any] = {"query": query, "count": len(results), "providers": results}
        if category_fallback:
            out["category_fallback"] = True
            out["hint"] = (
                f"no provider matched category '{category}'; showing unfiltered results. "
                f"next: provider_describe('{results[0]['id']}')"
            )
        elif results:
            out["hint"] = (
                f"next: provider_describe('{results[0]['id']}') to see its exact parameters"
            )
        else:
            out["web_search_required"] = True
            out["search_reason"] = "no_provider_match"
            out["hint"] = (
                "no provider matches this query. Call fetch_url on a likely page, "
                "or answer without a structured source."
            )
        return out

    # -- tool 2 ---------------------------------------------------------

    async def provider_describe(self, provider: str) -> dict[str, Any]:
        try:
            return self.registry.describe(provider)
        except KeyError as exc:
            return {"error": str(exc).strip("'\"")}

    # -- tool 3 ---------------------------------------------------------

    async def provider_call(
        self,
        provider: str,
        capability: str,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any]:
        try:
            envelope = await self.client.call(
                provider, capability, params or {}, cursor, refresh=refresh
            )
        except ProviderError as exc:
            return exc.to_dict()
        except Exception as exc:  # noqa: BLE001 - a tool must never raise into the transport
            return {"error": f"{type(exc).__name__}: {exc}"}

        out = envelope.to_dict()
        if not envelope.records:
            out["hint"] = (
                "0 records. Broaden the filter, or check the param names with provider_describe."
            )
        elif envelope.truncated:
            out["hint"] = (
                "more records available: pass cursor.next to continue, or answer with "
                "these records if the user did not ask for exhaustive results"
            )
        return out

    # -- tool 4 ---------------------------------------------------------

    async def fetch_url(self, url: str, max_chars: int = MAX_FETCH_CHARS) -> dict[str, Any]:
        if not self.config.allow_private_urls:
            try:
                await asyncio.to_thread(_assert_public_target, url)
            except ProviderError as exc:
                return exc.to_dict()
            except Exception as exc:  # noqa: BLE001 - a tool must never raise into the transport
                return {"error": f"address check failed: {exc}"}

        max_chars = max(1_000, min(max_chars, 60_000))
        key = cache_key("__fetch__", "url", {"url": url}, None)
        cached = self.cache.get(key)
        if cached is not None:
            body, age = cached.body, cached.age_s
        else:
            try:
                response = await self._http.get(url)
            except httpx.HTTPError as exc:
                return {"error": f"fetch failed: {exc}"}
            body = {
                "http": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "text": response.text,
            }
            age = 0.0
            if response.status_code < 400:
                self.cache.put(
                    key,
                    provider="__fetch__",
                    capability="url",
                    params={"url": url},
                    status=response.status_code,
                    body=body,
                    ttl=3600,
                )

        content_type = body.get("content_type", "")
        raw = body.get("text", "")
        looks_html = "html" in content_type or raw.lstrip()[:1] == "<"
        text = html_to_text(raw) if looks_html else raw
        clipped = len(text) > max_chars

        return {
            "url": url,
            "http": body.get("http"),
            "content_type": content_type,
            "fetched_at": utcnow().isoformat(timespec="seconds"),
            "cache_age_s": round(age, 1),
            "text": text[:max_chars],
            "truncated": clipped,
        }
