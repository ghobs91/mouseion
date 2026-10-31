"""Favicon resolver with a local cache and a monogram fallback.

The dashboard must not send the browser to a third-party icon service: that would
leak which sources you are looking at to Google or DuckDuckGo, and break when the
service is blocked. Instead the dashboard fetches the icon itself, only for hosts
that appear in the registry or the jurisdiction list, caches the bytes on disk,
and returns a generated monogram when there is no usable icon.

That makes the allowlist the security boundary: a request can only fetch a host
the registry already knows about, and every fetch is checked against private
addresses so a malicious icon link cannot turn the dashboard into an SSRF proxy.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import re
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".mouseion" / "favicons"
USER_AGENT = "mouseion/0.1 (favicon cache; contact set in MOUSEION_UA)"
MAX_BYTES = 256_000
MAX_PAGE_BYTES = 512_000
CACHE_TTL_S = 30 * 24 * 3600
NEGATIVE_TTL_S = 3600
ICON_RELS = {"icon", "shortcut icon", "apple-touch-icon", "apple-touch-icon-precomposed"}
_ICON_LINK = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r'([a-zA-Z:-]+)\s*=\s*"([^"]*)"|([a-zA-Z:-]+)\s*=\s*\'([^\']*)\'')
_LABEL = re.compile(r"[^A-Za-z0-9 ]+")


def normalize_host(value: str | None) -> str | None:
    """Accept a host or a URL and return a lowercase host without a port."""
    if not value:
        return None
    candidate = value.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    host = (urlparse(candidate).hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return None
    return host


def is_public_host(host: str) -> bool:
    """Reject loopback, private, link-local and otherwise reserved addresses."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] in {b"\x00\x00\x01\x00", b"\x00\x00\x02\x00"}:
        return "image/x-icon"
    if data.lstrip()[:5].lower() in {b"<svg ", b"<?xml"}:
        return "image/svg+xml"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def icon_href(page: str, base: str) -> str | None:
    """First usable <link rel="icon"> in a page, resolved against the base."""
    for tag in _ICON_LINK.findall(page):
        attrs: dict[str, str] = {}
        for match in _ATTR.finditer(tag):
            name = (match.group(1) or match.group(3) or "").lower()
            value = match.group(2) if match.group(1) else match.group(4)
            if name and value is not None:
                attrs[name] = value
        if attrs.get("rel", "").strip().lower() in ICON_RELS and attrs.get("href"):
            return urljoin(base, html.unescape(attrs["href"]))
    return None


def monogram_svg(label: str) -> bytes:
    """A deterministic tile for hosts with no usable icon."""
    clean = " ".join(_LABEL.sub(" ", label or "?").split()) or "?"
    initials = "".join(word[0] for word in clean.split()[:2]).upper() or clean[0].upper()
    hue = sum(ord(char) for char in (label or "?")) % 360
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="14" fill="hsl({hue} 42% 22%)"/>'
        f'<text x="32" y="42" font-family="system-ui,sans-serif" font-size="26" '
        f'font-weight="700" fill="hsl({hue} 80% 78%)" text-anchor="middle">{initials}</text>'
        f"</svg>"
    ).encode("utf-8")


class FaviconStore:
    def __init__(
        self,
        allowed: set[str],
        directory: Path | str = CACHE_DIR,
        *,
        fetcher=None,
        concurrency: int = 6,
    ):
        self.allowed = {host for host in allowed if host}
        self.directory = Path(directory)
        self._fetcher = fetcher or self._fetch
        self._semaphore = threading.Semaphore(concurrency)
        self._negative: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    # -- public ---------------------------------------------------------

    def get(self, host: str | None, label: str = "") -> tuple[bytes, str]:
        host = normalize_host(host)
        if host is None or host not in self.allowed:
            raise KeyError(host or "")

        cached = self._read_cache(host)
        if cached is not None:
            return cached

        with self._lock:
            negative = self._negative.get(host)
            if negative and negative[0] > time.time():
                return negative[1], "image/svg+xml"

        with self._semaphore:
            fetched = self._fetcher(host)

        if fetched is None:
            svg = monogram_svg(label or host)
            with self._lock:
                self._negative[host] = (time.time() + NEGATIVE_TTL_S, svg)
            return svg, "image/svg+xml"

        data, content_type = fetched
        self._write_cache(host, data)
        return data, content_type

    # -- cache ----------------------------------------------------------

    def _path(self, host: str) -> Path:
        digest = hashlib.sha256(host.encode("utf-8")).hexdigest()[:24]
        return self.directory / f"{digest}.bin"

    def _read_cache(self, host: str) -> tuple[bytes, str] | None:
        path = self._path(host)
        try:
            if time.time() - path.stat().st_mtime > CACHE_TTL_S:
                return None
            data = path.read_bytes()
        except OSError:
            return None
        content_type = _sniff(data)
        return (data, content_type) if content_type else None

    def _write_cache(self, host: str, data: bytes) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._path(host)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            pass

    # -- network --------------------------------------------------------

    def _fetch(self, host: str) -> tuple[bytes, str] | None:
        if not is_public_host(host):
            return None
        try:
            with httpx.Client(
                timeout=4.0,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            ) as client:
                for scheme in ("https", "http"):
                    found = self._try(client, f"{scheme}://{host}/favicon.ico")
                    if found:
                        return found
                    page = self._page(client, f"{scheme}://{host}/")
                    if page:
                        href = icon_href(page, f"{scheme}://{host}/")
                        if href:
                            found = self._try(client, href)
                            if found:
                                return found
        except httpx.HTTPError:
            return None
        return None

    def _try(self, client: httpx.Client, url: str) -> tuple[bytes, str] | None:
        if not is_public_host(urlparse(url).hostname or ""):
            return None
        try:
            response = client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400 or len(response.content) > MAX_BYTES:
            return None
        content_type = _sniff(response.content)
        return (response.content, content_type) if content_type else None

    def _page(self, client: httpx.Client, url: str) -> str | None:
        if not is_public_host(urlparse(url).hostname or ""):
            return None
        try:
            with client.stream("GET", url) as response:
                if response.status_code >= 400:
                    return None
                buffer = bytearray()
                for chunk in response.iter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) >= MAX_PAGE_BYTES:
                        break
        except httpx.HTTPError:
            return None
        return bytes(buffer).decode("utf-8", "replace")
