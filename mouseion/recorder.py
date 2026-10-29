"""Record upstream fixtures so manifests can be tested without the network.

    mouseion record                 # every capability with an example
    mouseion record --provider nvd  # one provider

Fixtures are the raw upstream body plus the params that produced it, so the replay
test exercises the real adapter path rather than a hand-written sample.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .bootstrap import build

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


async def record(
    provider: str | None = None,
    capability: str | None = None,
    *,
    out_dir: Path | None = None,
) -> list[dict[str, Any]]:
    out_dir = out_dir or FIXTURES
    out_dir.mkdir(parents=True, exist_ok=True)
    app = build()
    written: list[dict[str, Any]] = []

    try:
        for manifest in app.registry.manifests.values():
            if provider and manifest.id != provider:
                continue
            for name, cap in manifest.capabilities.items():
                if capability and name != capability:
                    continue
                if cap.example is None:
                    print(f"skip {manifest.id}/{name}: no example params", file=sys.stderr)
                    continue
                try:
                    envelope, body = await app.client.call_with_body(
                        manifest.id, name, cap.example, refresh=True
                    )
                except Exception as exc:  # noqa: BLE001 - a recorder reports, never raises
                    print(f"fail {manifest.id}/{name}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    continue

                payload = {
                    "provider": manifest.id,
                    "capability": name,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "params": cap.example,
                    "expected_records": len(envelope.records),
                    "body": body,
                }
                path = out_dir / f"{manifest.id}__{name}.json"
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                written.append(
                    {
                        "fixture": path.name,
                        "records": len(envelope.records),
                        "bytes": path.stat().st_size,
                    }
                )
                # Be a good citizen: NCBI allows 3/s, NVD 5 per 30s.
                await asyncio.sleep(0.4)
    finally:
        await app.aclose()

    return written
