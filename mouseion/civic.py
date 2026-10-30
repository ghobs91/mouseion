"""Jurisdiction registry and coverage discovery for local elections.

The unit here is the jurisdiction, not the provider. Every state, DC and major
city gets an entry naming its election authority and, where one exists, its
campaign-finance authority. The discovery pass probes those official pages in
parallel and records what each one actually serves: JSON, CSV, an HTML table, a
PDF, or nothing machine-readable.

The point is to stop guessing. A jurisdiction becomes a provider only after its
surface passes this probe; until then it is a registry row with a status, and an
unregistered or non-machine-readable jurisdiction returns `web_search_required`
instead of an invented record.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from pydantic import BaseModel, ConfigDict

Level = Literal["state", "territory", "city"]
Kind = Literal["election", "campaign_finance"]

MACHINE_READABLE = {"json", "csv"}

#: The registry lives beside `providers/`, not inside the package: operators edit
#: it the same way they edit manifests.
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "jurisdictions.yaml"
USER_AGENT = "mouseion/0.1 (jurisdiction discovery; contact set in MOUSEION_UA)"
#: Several .gov WAFs reject any non-browser client. Retry a blocked page once as
#: a browser and record that the retry was needed, so the matrix stays honest.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ACCEPT = "application/json, text/csv;q=0.9, text/html;q=0.8, */*;q=0.5"

_MACHINE_LINK = re.compile(
    r"\.(json|geojson|csv|tsv)(\?|#|$)|/resource/|/api/|/download|csv", re.IGNORECASE
)
_PDF_LINK = re.compile(r"\.pdf(\?|#|$)", re.IGNORECASE)


class Authority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    #: Free-form caveat shown in the matrix, e.g. "county administers city races".
    note: str = ""


class Jurisdiction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    level: Level
    #: Two-letter state code. Required for cities, equals the id suffix for states.
    state: str | None = None
    election: Authority | None = None
    campaign_finance: Authority | None = None
    notes: str = ""

    def authorities(self) -> list[tuple[Kind, Authority]]:
        out: list[tuple[Kind, Authority]] = []
        if self.election is not None:
            out.append(("election", self.election))
        if self.campaign_finance is not None:
            out.append(("campaign_finance", self.campaign_finance))
        return out


@dataclass(slots=True)
class Surface:
    """What one authority URL actually serves."""

    jurisdiction: str
    name: str
    level: str
    kind: str
    authority: str
    url: str
    final_url: str | None = None
    http: int | None = None
    format: str = "error"
    records: int = 0
    tables: int = 0
    links: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def machine_readable(self) -> bool:
        return self.format in MACHINE_READABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "jurisdiction": self.jurisdiction,
            "name": self.name,
            "level": self.level,
            "kind": self.kind,
            "authority": self.authority,
            "url": self.url,
            "final_url": self.final_url,
            "http": self.http,
            "format": self.format,
            "records": self.records,
            "tables": self.tables,
            "links": self.links,
            "detail": self.detail,
        }


def load_jurisdictions(path: Path | str = DEFAULT_PATH) -> list[Jurisdiction]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("jurisdictions")
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list under 'jurisdictions'")

    out: list[Jurisdiction] = []
    seen: set[str] = set()
    for item in raw:
        jurisdiction = Jurisdiction.model_validate(item)
        if jurisdiction.id in seen:
            raise ValueError(f"{path}: duplicate jurisdiction id '{jurisdiction.id}'")
        seen.add(jurisdiction.id)
        out.append(jurisdiction)
    return out


# -- probing ------------------------------------------------------------


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.tables = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.tables += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def _sniff_format(content_type: str, body: bytes) -> str:
    lowered = content_type.lower()
    if "json" in lowered:
        return "json"
    if "csv" in lowered:
        return "csv"
    if "pdf" in lowered:
        return "pdf"
    if "html" in lowered or "xml" in lowered:
        return "html"
    head = body[:200].lstrip()
    if head[:1] in {b"{", b"["}:
        return "json"
    if b"<html" in head.lower() or b"<!doctype" in head.lower():
        return "html"
    if head and b"," in head.split(b"\n", 1)[0]:
        return "csv"
    return "unknown"


def _count_records(fmt: str, body: bytes) -> int:
    if fmt == "json":
        try:
            payload = json.loads(body)
        except ValueError:
            return 0
        return len(payload) if isinstance(payload, list) else 1
    if fmt == "csv":
        return max(0, body.count(b"\n") - 1)
    return 0


def _machine_links(html: str, base: str) -> tuple[list[str], int]:
    parser = _Links()
    try:
        parser.feed(html)
    except Exception:  # malformed markup is common on legacy portals
        pass
    links: list[str] = []
    seen: set[str] = set()
    for href in parser.hrefs:
        if not _MACHINE_LINK.search(href) or _PDF_LINK.search(href):
            continue
        absolute = urljoin(base, href)
        if not absolute.startswith(("http://", "https://")) or absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links, parser.tables


async def _probe_link(client: httpx.AsyncClient, url: str, timeout: float) -> str | None:
    response, _ = await _get(client, url, timeout)
    if response is None or response.status_code >= 400:
        return None
    fmt = _sniff_format(response.headers.get("content-type", ""), response.content)
    return fmt if fmt in MACHINE_READABLE else None


async def _get(
    client: httpx.AsyncClient, url: str, timeout: float
) -> tuple[httpx.Response | None, str]:
    """GET a page, retrying once as a browser when a WAF blocks the honest UA."""
    try:
        response = await client.get(url, timeout=timeout, headers={"Accept": ACCEPT})
    except httpx.TimeoutException:
        return None, f"timeout after {timeout}s"
    except httpx.ConnectError as exc:
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return None, "tls_verify_failed: incomplete certificate chain"
        return None, f"connect_error: {str(exc)[:120]}"
    except httpx.HTTPError as exc:
        return None, type(exc).__name__
    if response.status_code not in {401, 403}:
        return response, ""
    try:
        retry = await client.get(
            url,
            timeout=timeout,
            headers={"Accept": ACCEPT, "User-Agent": BROWSER_UA},
        )
    except httpx.HTTPError:
        return response, f"HTTP {response.status_code} (browser-UA retry failed)"
    if retry.status_code < 400:
        return retry, f"HTTP {response.status_code} for honest UA; browser UA accepted"
    return response, f"HTTP {response.status_code} after browser-UA retry"


async def probe_authority(
    client: httpx.AsyncClient,
    jurisdiction: Jurisdiction,
    kind: Kind,
    authority: Authority,
    *,
    timeout: float = 20.0,
    links_to_probe: int = 2,
) -> Surface:
    surface = Surface(
        jurisdiction=jurisdiction.id,
        name=jurisdiction.name,
        level=jurisdiction.level,
        kind=kind,
        authority=authority.name,
        url=authority.url,
    )
    response, blocked = await _get(client, authority.url, timeout)
    if response is None:
        surface.format = (
            "tls_error" if blocked.startswith("tls_verify_failed") else "unreachable"
        )
        surface.detail = blocked
        return surface

    surface.http = response.status_code
    surface.final_url = str(response.url)
    if response.status_code == 401:
        surface.format = "auth_required"
        surface.detail = blocked or "HTTP 401"
        return surface
    if response.status_code == 403:
        # Edge bot protection, not a login: httpx cannot pass a TLS-fingerprint
        # check, so this needs a browser-capable probe or a documented API.
        surface.format = "blocked"
        surface.detail = blocked or "HTTP 403"
        return surface
    if response.status_code >= 400:
        surface.format = "error"
        surface.detail = f"HTTP {response.status_code}"
        return surface

    body = response.content
    content_type = response.headers.get("content-type", "")
    if not body.strip():
        surface.format = "html"
        surface.detail = "empty body; likely a JS-rendered shell"
        return surface
    fmt = _sniff_format(content_type, body)


    if fmt == "pdf":
        surface.format = "pdf"
        surface.detail = "PDF only; no machine-readable table"
        return surface

    if fmt in MACHINE_READABLE:
        surface.format = fmt
        surface.records = _count_records(fmt, body)
        surface.detail = f"{content_type.split(';')[0] or fmt}, {surface.records} records"
        return surface

    if fmt != "html":
        surface.format = "error"
        surface.detail = f"unexpected content-type '{content_type or 'none'}'"
        return surface

    text = response.text
    candidates, tables = _machine_links(text, surface.final_url)
    surface.tables = tables

    confirmed: list[str] = []
    for candidate in candidates[: max(0, links_to_probe)]:
        found = await _probe_link(client, candidate, timeout)
        if found:
            confirmed.append(f"{found}:{candidate}")
    surface.links = candidates[:8]

    if confirmed:
        surface.format = confirmed[0].split(":", 1)[0]
        surface.detail = f"link {confirmed[0].split(':', 1)[1]}"
        return surface

    surface.format = "html_tables" if tables else "html"
    surface.detail = (
        f"{tables} table(s) on page, no JSON/CSV link"
        if tables
        else "no table, no JSON/CSV link"
    )
    return surface


async def discover(
    jurisdictions: list[Jurisdiction],
    *,
    concurrency: int = 12,
    timeout: float = 20.0,
    links_to_probe: int = 2,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Probe every authority in the registry, in parallel, and summarise it."""
    targets = [
        (jurisdiction, kind, authority)
        for jurisdiction in jurisdictions
        for kind, authority in jurisdiction.authorities()
    ]

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        transport=transport,
    ) as client:
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def one(
            jurisdiction: Jurisdiction, kind: Kind, authority: Authority
        ) -> Surface:
            async with semaphore:
                return await probe_authority(
                    client,
                    jurisdiction,
                    kind,
                    authority,
                    timeout=timeout,
                    links_to_probe=links_to_probe,
                )

        surfaces = await asyncio.gather(*(one(*target) for target in targets))

    return summarize(jurisdictions, list(surfaces))


def summarize(jurisdictions: list[Jurisdiction], surfaces: list[Surface]) -> dict[str, Any]:
    by_format: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    by_level: dict[str, dict[str, int]] = {}
    for surface in surfaces:
        by_format[surface.format] = by_format.get(surface.format, 0) + 1
        kind = by_kind.setdefault(surface.kind, {})
        kind[surface.format] = kind.get(surface.format, 0) + 1
        level = by_level.setdefault(surface.level, {})
        level[surface.format] = level.get(surface.format, 0) + 1

    expected = {
        "election": sum(1 for j in jurisdictions if j.election is not None),
        "campaign_finance": sum(1 for j in jurisdictions if j.campaign_finance is not None),
    }
    missing = {
        "election": sum(1 for j in jurisdictions if j.election is None),
        "campaign_finance": sum(1 for j in jurisdictions if j.campaign_finance is None),
    }
    machine = sum(1 for surface in surfaces if surface.machine_readable)
    return {
        "jurisdictions": len(jurisdictions),
        "authorities": len(surfaces),
        "missing_authorities": missing,
        "machine_readable": machine,
        "machine_readable_pct": round(100 * machine / len(surfaces), 1) if surfaces else 0.0,
        "by_format": dict(sorted(by_format.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_kind": by_kind,
        "by_level": by_level,
        "expected": expected,
        "results": [surface.as_dict() for surface in surfaces],
    }


# -- machine-readable dataset catalog -----------------------------------

#: Socrata's national discovery index. One query reaches every Socrata-hosted
#: government portal, which is what makes wave 1 bulk work instead of a crawl.
SOCRATA_CATALOG = "https://api.us.socrata.com/api/catalog/v1"
OFFICIAL_HOST_HINTS = (".gov", "opendata.com", "datahub.", "open-data.")
DEFAULT_QUERIES = ("campaign finance", "campaign contributions", "election results")


@dataclass(slots=True)
class Dataset:
    domain: str
    id: str
    name: str
    query: str
    updated: str | None = None
    rows: int = -1

    @property
    def endpoint(self) -> str:
        return f"https://{self.domain}/resource/{self.id}.json"

    @property
    def page(self) -> str:
        return f"https://{self.domain}/d/{self.id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "id": self.id,
            "name": self.name,
            "query": self.query,
            "updated": self.updated,
            "rows": self.rows,
            "endpoint": self.endpoint,
            "page": self.page,
        }


def _official(domain: str) -> bool:
    return any(hint in domain.lower() for hint in OFFICIAL_HOST_HINTS)


async def datasets(
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    *,
    min_rows: int = 100,
    per_query: int = 60,
    concurrency: int = 8,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Find machine-readable civic datasets, ranked by size.

    The catalog tells us which portals publish structured campaign-finance and
    election data. Row counts then separate a real table from a stub, so a
    promotion starts from a measured candidate rather than a landing-page guess.
    """
    found: dict[tuple[str, str], Dataset] = {}
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=30, transport=transport
    ) as client:
        for query in queries:
            try:
                response = await client.get(
                    SOCRATA_CATALOG,
                    params={"q": query, "only": "dataset", "limit": per_query},
                )
            except httpx.HTTPError as exc:
                found.setdefault(
                    ("", f"error:{query}"), Dataset("", "", f"{type(exc).__name__}", query)
                )
                continue
            if response.status_code >= 400:
                found.setdefault(
                    ("", f"error:{query}"),
                    Dataset("", "", f"HTTP {response.status_code}", query),
                )
                continue
            for result in response.json().get("results", []):
                resource = result.get("resource") or {}
                domain = (result.get("metadata") or {}).get("domain", "")
                if not domain or not _official(domain):
                    continue
                key = (domain, resource.get("id", ""))
                if key in found:
                    continue
                found[key] = Dataset(
                    domain=domain,
                    id=resource.get("id", ""),
                    name=resource.get("name", ""),
                    query=query,
                    updated=resource.get("updatedAt"),
                )

        candidates = [item for key, item in found.items() if key[0]]

        async def count(item: Dataset) -> Dataset:
            try:
                response = await client.get(
                    item.endpoint, params={"$select": "count(*)"}
                )
                if response.status_code == 200 and response.json():
                    item.rows = int(list(response.json()[0].values())[0])
            except (httpx.HTTPError, ValueError, IndexError, TypeError):
                item.rows = -1
            return item

        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def bounded(item: Dataset) -> Dataset:
            async with semaphore:
                return await count(item)

        counted = await asyncio.gather(*(bounded(item) for item in candidates))

    kept = sorted(
        (item for item in counted if item.rows >= min_rows), key=lambda item: -item.rows
    )
    by_domain: dict[str, int] = {}
    for item in kept:
        by_domain[item.domain] = by_domain.get(item.domain, 0) + 1
    return {
        "queries": list(queries),
        "candidates": len(candidates),
        "kept": len(kept),
        "min_rows": min_rows,
        "by_domain": dict(sorted(by_domain.items(), key=lambda kv: (-kv[1], kv[0]))),
        "results": [item.as_dict() for item in kept],
    }
