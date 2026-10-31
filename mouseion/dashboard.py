"""Local dashboard: one read-only page over the registry, civic matrix and audit.

The dashboard is deliberately dependency-free. It reads the same YAML and JSON
the CLI reads, builds a snapshot in memory, and serves one static page plus one
JSON endpoint. No build step, no npm, no second source of truth: if a manifest
changes, the next request reflects it.

    mouseion dashboard --open
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .audit import audit_registry, summarize
from .civic import Jurisdiction, load_jurisdictions
from .registry import Registry

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_COVERAGE = ROOT / "eval" / "civic-coverage.json"
DEFAULT_DATASETS = ROOT / "eval" / "civic-datasets.json"

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


def build_snapshot(
    registry: Registry,
    *,
    jurisdictions_path: Path | str | None = None,
    coverage_path: Path | str | None = None,
    datasets_path: Path | str | None = None,
) -> dict[str, Any]:
    """Everything the page needs, in one JSON document."""
    manifests = registry.manifests

    providers = [_provider(manifest) for manifest in manifests.values()]
    providers.sort(key=lambda item: (item["domain"], item["id"]))

    domains = _by_domain(providers)
    categories = _counts(provider["category"] for provider in providers)
    auth = _counts(provider["auth"] for provider in providers)

    jurisdictions = _jurisdictions(jurisdictions_path)
    coverage = _read_json(coverage_path or DEFAULT_COVERAGE)
    datasets = _read_json(datasets_path or DEFAULT_DATASETS)

    covered = _coverage_rows(jurisdictions, coverage, providers)
    audit = summarize(audit_registry(registry))

    formats = [
        authority["format"]
        for row in covered
        for authority in (row["election"], row["campaign_finance"])
        if authority and authority["format"] is not None
    ]
    machine_readable = sum(1 for fmt in formats if fmt in {"json", "csv"})
    authorities = len(formats)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "providers": len(providers),
            "capabilities": sum(len(provider["capabilities"]) for provider in providers),
            "domains": len(domains),
            "jurisdictions": len(jurisdictions),
            "civic_providers": sum(1 for provider in providers if provider["domain"] == "civic_local"),
            "tier1": sum(1 for provider in providers if provider["tier"] == 1),
            "keyless": sum(1 for provider in providers if provider["auth"] in {"none", "user_agent"}),
            "authorities": authorities,
            "machine_readable": machine_readable,
            "machine_readable_pct": round(100 * machine_readable / authorities, 1)
            if authorities
            else 0.0,
            "datasets": datasets.get("kept", 0) if datasets else 0,
            "audit_errors": audit["errors"],
            "audit_warnings": audit["warnings"],
        },
        "domains": domains,
        "categories": categories,
        "auth": auth,
        "providers": providers,
        "jurisdictions": covered,
        "coverage": {
            "by_format": (coverage or {}).get("by_format", {}),
            "by_level": (coverage or {}).get("by_level", {}),
            "generated_at": _file_mtime(coverage_path or DEFAULT_COVERAGE),
        },
        "catalog": {
            "kept": datasets.get("kept", 0) if datasets else 0,
            "by_domain": (datasets or {}).get("by_domain", {}),
        },
        "audit": audit,
    }


def _provider(manifest: Any) -> dict[str, Any]:
    capabilities = []
    for name, capability in manifest.capabilities.items():
        capabilities.append(
            {
                "name": name,
                "description": capability.description,
                "required": capability.required_params(),
                "params": len(capability.params),
                "exemplars": capability.exemplars[:3],
                "datasets": len(capability.datasets),
                "pagination": capability.pagination.mode if capability.pagination else None,
            }
        )
    return {
        "id": manifest.id,
        "title": manifest.title,
        "domain": manifest.domain,
        "sub_category": manifest.sub_category,
        "category": manifest.category,
        "tier": manifest.source.tier,
        "auth": manifest.auth.type,
        "transport": manifest.transport,
        "provenance": manifest.source.provenance,
        "is_primary": manifest.source.is_primary,
        "originator": manifest.source.originator,
        "attribution": manifest.attribution,
        "regions": manifest.regions,
        "jurisdictions": manifest.jurisdictions,
        "description": manifest.description,
        "docs_url": manifest.docs_url,
        "terms": manifest.terms,
        "ttl": manifest.cache_ttl,
        "capabilities": capabilities,
        "evidence": {
            "url": manifest.source.auth_policy.evidence_url,
            "verified_at": manifest.source.auth_policy.verified_at.isoformat(),
            "free": manifest.source.auth_policy.free,
            "card": manifest.source.auth_policy.credit_card_required,
            "trial": manifest.source.auth_policy.trial,
        },
        "rate_limit": manifest.source.rate_limit.model_dump(),
    }


def _by_domain(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for provider in providers:
        row = out.setdefault(
            provider["domain"],
            {
                "domain": provider["domain"],
                "providers": 0,
                "capabilities": 0,
                "tier1": 0,
                "tier2": 0,
                "keyless": 0,
            },
        )
        row["providers"] += 1
        row["capabilities"] += len(provider["capabilities"])
        row[f"tier{provider['tier']}"] += 1
        if provider["auth"] in {"none", "user_agent"}:
            row["keyless"] += 1
    return sorted(out.values(), key=lambda row: (-row["providers"], row["domain"]))


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _jurisdictions(path: Path | str | None) -> list[Jurisdiction]:
    candidates = [Path(path)] if path else [ROOT / "jurisdictions.yaml"]
    for candidate in candidates:
        if candidate.exists():
            try:
                return load_jurisdictions(candidate)
            except (OSError, ValueError):
                return []
    return []


def _read_json(path: Path | str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    file = Path(path)
    if not file.exists():
        return None
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _file_mtime(path: Path | str | None) -> str | None:
    if path is None:
        return None
    file = Path(path)
    if not file.exists():
        return None
    return datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat(
        timespec="seconds"
    )


def _coverage_rows(
    jurisdictions: list[Jurisdiction],
    coverage: dict[str, Any] | None,
    providers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    probes: dict[tuple[str, str], dict[str, Any]] = {}
    for result in (coverage or {}).get("results", []):
        key = (result.get("jurisdiction", ""), result.get("kind", ""))
        probes[key] = result

    by_jurisdiction: dict[str, list[str]] = {}
    for provider in providers:
        for jurisdiction_id in provider["jurisdictions"]:
            by_jurisdiction.setdefault(jurisdiction_id, []).append(provider["id"])

    rows: list[dict[str, Any]] = []
    for jurisdiction in jurisdictions:
        election = probes.get((jurisdiction.id, "election"), {})
        finance = probes.get((jurisdiction.id, "campaign_finance"), {})
        rows.append(
            {
                "id": jurisdiction.id,
                "name": jurisdiction.name,
                "level": jurisdiction.level,
                "state": jurisdiction.state,
                "notes": jurisdiction.notes,
                "election": _authority(jurisdiction.election, election),
                "campaign_finance": _authority(jurisdiction.campaign_finance, finance),
                "providers": sorted(by_jurisdiction.get(jurisdiction.id, [])),
            }
        )
    return rows


def _authority(authority: Any, probe: dict[str, Any]) -> dict[str, Any] | None:
    if authority is None and not probe:
        return None
    return {
        "name": authority.name if authority else None,
        "url": authority.url if authority else probe.get("url"),
        "note": authority.note if authority else "",
        "format": probe.get("format"),
        "http": probe.get("http"),
        "detail": probe.get("detail", ""),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    snapshot: Callable[[], dict[str, Any]]
    web_dir: Path = WEB_DIR

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = urlparse(self.path).path
        if path == "/api/snapshot":
            self._json(self.snapshot())
            return
        if path == "/healthz":
            self._json({"ok": True})
            return
        if path in {"/", "/index.html"}:
            self._file("index.html")
            return
        self._file(path.lstrip("/"))

    def _file(self, name: str) -> None:
        candidate = (self.web_dir / name).resolve()
        if not str(candidate).startswith(str(self.web_dir.resolve())) or not candidate.is_file():
            self.send_error(404)
            return
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", _CONTENT_TYPES.get(candidate.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # One line per request is noise for a local dashboard.
        return


def serve(
    registry: Registry,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    jurisdictions_path: Path | str | None = None,
    coverage_path: Path | str | None = None,
    datasets_path: Path | str | None = None,
) -> None:
    """Serve until interrupted. Rebuilds the snapshot on every request."""
    lock = threading.Lock()

    def snapshot() -> dict[str, Any]:
        with lock:
            return build_snapshot(
                registry,
                jurisdictions_path=jurisdictions_path,
                coverage_path=coverage_path,
                datasets_path=datasets_path,
            )

    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {"snapshot": staticmethod(snapshot)},
    )
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"mouseion dashboard: {url}", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
