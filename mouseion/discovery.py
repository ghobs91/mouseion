"""Discovery funnel: catalog → candidates → probes → manifests.

"Every US government API" is not a list; it is a pipeline. The unit that scales is
the shape, not the provider, so discovery only has to answer two questions per
candidate: does it answer with JSON, and where is the record list?

Sources are agency ``data.json`` files published under Project Open Data. data.gov's
CKAN search API is gone as of 2026, so the harvest walks the agencies directly and
skips whoever 404s.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

#: Agency catalogs. Missing ones are skipped, not fatal: /data.json is voluntary.
SEED_CATALOGS: dict[str, str] = {
    "GSA": "https://www.gsa.gov/data.json",
    "NASA": "https://data.nasa.gov/data.json",
    "USDA": "https://www.usda.gov/data.json",
    "DOL": "https://www.dol.gov/data.json",
    "HHS": "https://www.hhs.gov/data.json",
    "DOE": "https://www.energy.gov/data.json",
    "DOT": "https://www.transportation.gov/data.json",
    "ED": "https://www.ed.gov/data.json",
    "VA": "https://www.va.gov/data.json",
    "HUD": "https://www.hud.gov/data.json",
    "DOJ": "https://www.justice.gov/data.json",
    "DHS": "https://www.dhs.gov/data.json",
    "STATE": "https://www.state.gov/data.json",
    "COMMERCE": "https://www.commerce.gov/data.json",
    "NARA": "https://www.archives.gov/data.json",
    "SSA": "https://www.ssa.gov/data.json",
    "SBA": "https://www.sba.gov/data.json",
    "TREASURY": "https://home.treasury.gov/data.json",
    "NSF": "https://www.nsf.gov/data.json",
    "SEC": "https://www.sec.gov/data.json",
    "FCC": "https://www.fcc.gov/data.json",
    "FTC": "https://www.ftc.gov/data.json",
    "USGS": "https://www.usgs.gov/data.json",
    "NIST": "https://www.nist.gov/data.json",
    "EPA": "https://www.epa.gov/data.json",
}

#: Above this, stop: a 96MB catalog costs more memory than it returns.
MAX_CATALOG_BYTES = 30_000_000

API_FORMATS = {"api", "json", "jsonp", "geojson", "json-ld"}
API_URL_HINTS = (".json", "/api/", "/rest/", "/v1/", "/v2/", "/v3/")


@dataclass(slots=True)
class Candidate:
    url: str
    title: str
    agency: str
    shape: str = "rest_json"

    @property
    def key(self) -> str:
        parsed = urlparse(self.url)
        return f"{parsed.netloc}{parsed.path}".rstrip("/").lower()


@dataclass(slots=True)
class ProbeResult:
    candidate: Candidate
    status: int | None = None
    content_type: str = ""
    bytes: int = 0
    record_path: str | None = None
    records: int = 0
    verdict: str = "unknown"
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "url": self.candidate.url,
            "agency": self.candidate.agency,
            "shape": self.candidate.shape,
            "status": self.status,
            "records": self.records,
            "record_path": self.record_path,
            "verdict": self.verdict,
            "detail": self.detail,
        }


def classify(url: str) -> str:
    lowered = url.lower()
    if "/api/3/action/" in lowered:
        return "ckan"
    if "featureserver" in lowered or "mapserver" in lowered or "/arcgis/rest" in lowered:
        return "arcgis"
    if "/resource/" in lowered and lowered.endswith(".json"):
        return "socrata"
    if "$filter" in lowered or "/odata" in lowered:
        return "odata"
    return "rest_json"


def _is_api_like(distribution: dict[str, Any]) -> bool:
    fmt = str(distribution.get("format") or distribution.get("mediaType") or "").lower()
    if fmt in API_FORMATS:
        return True
    url = str(distribution.get("accessURL") or distribution.get("downloadURL") or "")
    return any(hint in url.lower() for hint in API_URL_HINTS)


async def harvest(
    client: httpx.AsyncClient, agency: str, url: str
) -> tuple[list[Candidate], str | None]:
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        return [], f"{agency}: {type(exc).__name__}"
    if response.status_code >= 400:
        return [], f"{agency}: HTTP {response.status_code}"
    if len(response.content) > MAX_CATALOG_BYTES:
        return [], f"{agency}: catalog too large ({len(response.content) // 1_000_000}MB)"

    try:
        payload = response.json()
    except ValueError:
        return [], f"{agency}: not JSON"

    out: list[Candidate] = []
    for dataset in payload.get("dataset") or []:
        title = str(dataset.get("title") or "")[:120]
        for distribution in dataset.get("distribution") or []:
            link = distribution.get("accessURL") or distribution.get("downloadURL")
            if not link or not _is_api_like(distribution):
                continue
            out.append(
                Candidate(url=link, title=title, agency=agency, shape=classify(link))
            )
    return out, None


def find_record_path(body: Any, max_depth: int = 3) -> tuple[str | None, int]:
    """Largest list-of-objects in the response, with its dotted path."""
    best: tuple[str | None, int] = (None, 0)
    if isinstance(body, list):
        rows = [row for row in body if isinstance(row, dict)]
        return None, len(rows)

    def walk(node: Any, prefix: str, depth: int) -> None:
        nonlocal best
        if depth > max_depth or not isinstance(node, dict):
            return
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, list) and value and isinstance(value[0], dict):
                if len(value) > best[1]:
                    best = (path, len(value))
            elif isinstance(value, dict):
                walk(value, path, depth + 1)

    walk(body, "", 0)
    return best


async def probe(
    client: httpx.AsyncClient, candidate: Candidate, timeout: float = 15.0
) -> ProbeResult:
    result = ProbeResult(candidate=candidate)
    try:
        response = await client.get(candidate.url, timeout=timeout)
    except httpx.HTTPError as exc:
        result.verdict = "unreachable"
        result.detail = type(exc).__name__
        return result

    result.status = response.status_code
    result.bytes = len(response.content)
    result.content_type = response.headers.get("content-type", "")

    if response.status_code in {401, 403}:
        result.verdict = "auth_required"
        return result
    if response.status_code >= 400:
        result.verdict = f"http_{response.status_code}"
        return result
    if result.bytes > 5_000_000:
        result.verdict = "too_large"
        return result
    try:
        body = response.json()
    except ValueError:
        result.verdict = "not_json"
        return result

    path, count = find_record_path(body)
    result.record_path = path
    result.records = count
    if count == 0:
        result.verdict = "no_records"
        return result
    result.verdict = "ok"
    return result


async def discover(
    catalog_limit: int | None = None, probe_limit: int | None = None
) -> dict[str, Any]:
    headers = {"User-Agent": "mouseion/0.1 (discovery; contact set in MOUSEION_UA)"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        catalogs = list(SEED_CATALOGS.items())[: catalog_limit or None]
        harvested = await asyncio.gather(
            *(harvest(client, agency, url) for agency, url in catalogs)
        )

    candidates: list[Candidate] = []
    seen: set[str] = set()
    failures: list[str] = []
    for found, error in harvested:
        if error:
            failures.append(error)
        for candidate in found:
            if candidate.key in seen:
                continue
            seen.add(candidate.key)
            candidates.append(candidate)

    before = len(candidates)
    if probe_limit:
        candidates = candidates[:probe_limit]

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        semaphore = asyncio.Semaphore(8)

        async def one(candidate: Candidate) -> ProbeResult:
            async with semaphore:
                return await probe(client, candidate)

        results = await asyncio.gather(*(one(c) for c in candidates))

    by_verdict: dict[str, int] = {}
    for result in results:
        by_verdict[result.verdict] = by_verdict.get(result.verdict, 0) + 1

    return {
        "catalogs_ok": len(catalogs) - len(failures),
        "catalog_failures": failures,
        "api_resources": before,
        "probed": len(results),
        "by_verdict": dict(sorted(by_verdict.items(), key=lambda kv: -kv[1])),
        "results": [result.as_dict() for result in results],
    }


def manifest_from(result: ProbeResult) -> dict[str, Any]:
    """A skeleton a human still has to name, categorise and give example params."""
    parsed = urlparse(result.candidate.url)
    slug = re.sub(r"[^a-z0-9]+", "_", f"{result.candidate.agency}_{parsed.path}".lower()).strip("_")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return {
        "id": f"auto_{slug[:60]}",
        "title": result.candidate.title or parsed.path,
        "category": "government",
        "description": (
            f"{result.candidate.title} — {result.candidate.agency} JSON endpoint, "
            "auto-discovered from the agency data.json catalogue. Unreviewed: the "
            "record projection is raw and the parameters are unknown."
        ),
        "base": f"{parsed.scheme}://{parsed.netloc}",
        "adapter": "rest_json",
        "auth": {"type": "none"},
        "capabilities": {
            "list": {
                "description": (
                    f"Auto-imported listing endpoint ({result.records} records in the "
                    "probe response)."
                ),
                "path": path,
                "record_path": result.record_path,
                "local_window": True,
                "pagination": {"mode": "offset", "default_limit": 10, "max_limit": 50},
                "example": {},
            }
        },
    }
