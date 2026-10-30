from __future__ import annotations

import asyncio

from mouseion.bootstrap import build
from mouseion.doctor import doctor


def run(coro):
    return asyncio.run(coro)


def test_doctor_reports_a_ready_registry(config):
    app = build(config)
    try:
        report = run(doctor(app))
    finally:
        run(app.aclose())

    checks = {check["check"]: check for check in report["checks"]}
    assert report["ok"] is True
    assert checks["registry"]["ok"] is True
    assert checks["audit"]["ok"] is True
    assert checks["cache"]["ok"] is True
    assert checks["embeddings"]["ok"] is True
    assert checks["oauth"]["ok"] is True
    assert "model_server" not in checks
