"""Local readiness checks.

`mouseion doctor` answers one question: can this checkout run? It checks the
registry, the source-policy audit, the cache, the embedding backend, and
optionally an OpenAI-compatible model server. It never calls an upstream data
provider, so it is safe to run before a demo.
"""

from __future__ import annotations

from typing import Any

import httpx

from .audit import audit_registry, summarize
from .bootstrap import App
from .llamacpp import pick_model


async def doctor(
    app: App,
    *,
    model_url: str | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    summary = app.registry.summary()
    checks.append(
        {
            "check": "registry",
            "ok": summary["providers"] > 0 and summary["capabilities"] > 0,
            "detail": (
                f"{summary['providers']} providers / "
                f"{summary['capabilities']} capabilities"
            ),
        }
    )

    audit = summarize(audit_registry(app.registry))
    checks.append(
        {
            "check": "audit",
            "ok": audit["ok"],
            "detail": f"{audit['errors']} errors, {audit['warnings']} warnings",
        }
    )

    try:
        app.cache.stats()
        checks.append(
            {
                "check": "cache",
                "ok": True,
                "detail": str(app.cache.path),
            }
        )
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        checks.append({"check": "cache", "ok": False, "detail": str(exc)})

    embedder = app.router.embedder
    checks.append(
        {
            "check": "embeddings",
            "ok": True,
            "detail": (
                embedder.identity
                if embedder is not None
                else "not configured; router stays lexical"
            ),
        }
    )

    oauth = app.cache.oauth_status()
    checks.append(
        {
            "check": "oauth",
            "ok": True,
            "detail": (
                f"{len(oauth)} provider(s) with stored OAuth state"
                if oauth
                else "none; run mouseion auth login <provider> for remote MCP"
            ),
        }
    )

    if model_url:
        base = model_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"{base}/v1/models")
                response.raise_for_status()
            model = pick_model(response.json())
            checks.append(
                {
                    "check": "model_server",
                    "ok": bool(model),
                    "detail": model or "no loaded text model",
                }
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {
                    "check": "model_server",
                    "ok": False,
                    "detail": f"{base}: {type(exc).__name__}: {exc}",
                }
            )

    return {
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
    }
