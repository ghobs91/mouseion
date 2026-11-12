"""Contract tests.

Two layers:

* fixture replay — offline, runs on every commit, catches adapter/projection breaks.
* live — `pytest -m live`, hits every upstream with the manifest example. This is the
  nightly job that notices when a provider renames a field or drops a page token.

Neither asserts data values. Both assert the manifest still describes the response.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from mouseion.adapters.base import extract_records
from mouseion.bootstrap import build
from mouseion.browser import BrowserClient
from mouseion.client import ProviderClient
from mouseion.contract import ERROR, check_body, check_envelope
from mouseion.envelope import ProviderError
from mouseion.registry import Registry

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_IDS = sorted(path.stem for path in FIXTURES.glob("*.json")) or ["<none recorded>"]


def _capabilities() -> list[tuple[str, str]]:
    registry = Registry.load(ROOT / "providers")
    return [
        (manifest.id, name)
        for manifest in registry.manifests.values()
        if manifest.transport == "http"
        for name in manifest.capabilities
    ]


def _errors(violations):
    return [str(violation) for violation in violations if violation.severity == ERROR]


def _credential_envs(manifest, walmart_private_key: str) -> list[tuple[str, str]]:
    """Dummy env values so the offline replay can exercise the auth path."""
    auth = manifest.auth
    if auth.type in {"api_key", "basic", "user_agent"}:
        return [(auth.env, "fixture-token")] if auth.env else []
    if auth.type == "client_credentials":
        return [
            (env, "fixture-token")
            for env in (auth.client_id_env, auth.client_secret_env)
            if env
        ]
    if auth.type == "signed":
        return [
            (env, value)
            for env, value in (
                (auth.env, "fixture-consumer"),
                (auth.private_key_env, walmart_private_key),
                (auth.key_version_env, "1"),
            )
            if env
        ]
    return []


def _missing_credentials(manifest) -> list[str]:
    auth = manifest.auth
    names: list[str | None] = []
    if auth.type in {"api_key", "basic", "user_agent"}:
        names = [auth.env]
    elif auth.type == "client_credentials":
        names = [auth.client_id_env, auth.client_secret_env]
    elif auth.type == "signed":
        names = [auth.env, auth.private_key_env]
    return [name for name in names if name and not os.getenv(name)]


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_fixture_matches_contract(fixture_id, config, monkeypatch, walmart_private_key):
    monkeypatch.setenv("SEC_USER_AGENT", "mouseion-contract-test test@example.com")
    payload = json.loads((FIXTURES / f"{fixture_id}.json").read_text(encoding="utf-8"))
    provider, capability = payload["provider"], payload["capability"]

    # Replay tests parsing, not auth: give any credentialed manifest a dummy secret.
    # The transport is mocked, so it never leaves the process.
    manifest = Registry.load(ROOT / "providers").get(provider)
    for env, value in _credential_envs(manifest, walmart_private_key):
        monkeypatch.setenv(env, value)
    if manifest.auth.type == "client_credentials":

        async def _token(_self, _manifest):
            return "fixture-token"

        monkeypatch.setattr(ProviderClient, "_client_credentials_token", _token)
    if manifest.transport == "browser":

        async def _browser(_self, _manifest, _prepared, _capability):
            return payload["body"]

        monkeypatch.setattr(BrowserClient, "fetch", _browser)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload["body"])

    async def run() -> list:
        app = build(config, transport=httpx.MockTransport(handler))
        try:
            manifest = app.registry.get(provider)
            cap = manifest.capabilities[capability]
            envelope = await app.client.call(provider, capability, payload["params"])
            body = payload["body"]
            shape = check_body(
                cap,
                body,
                record_path_resolves=bool(
                    extract_records(body, cap.record_path, cap.map_exclude)
                ),
            )
            return shape + check_envelope(
                cap, envelope, expected_records=payload["expected_records"]
            )
        finally:
            await app.aclose()

    violations = asyncio.run(run())
    assert not _errors(violations), f"{fixture_id}: " + "; ".join(_errors(violations))


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_fixture_is_self_describing(fixture_id):
    payload = json.loads((FIXTURES / f"{fixture_id}.json").read_text(encoding="utf-8"))
    assert set(payload) == {
        "provider",
        "capability",
        "recorded_at",
        "params",
        "expected_records",
        "body",
    }
    assert payload["expected_records"] >= 0
    assert isinstance(payload["params"], dict)


@pytest.mark.live
@pytest.mark.parametrize(("provider", "capability"), _capabilities())
def test_live_contract(provider, capability, config):
    registry = Registry.load(ROOT / "providers")
    manifest = registry.get(provider)
    cap = manifest.capabilities[capability]

    if manifest.auth.type in {"api_key", "user_agent", "basic"}:
        env = manifest.auth.env or ""
        if not os.getenv(env):
            pytest.skip(f"needs {env}")
    missing = _missing_credentials(manifest)
    if missing:
        pytest.skip(f"needs {', '.join(missing)}")

    async def run() -> object:
        app = build(config)
        try:
            return await app.client.call(provider, capability, cap.example, refresh=True)
        finally:
            await app.aclose()

    try:
        envelope = asyncio.run(run())
    except ProviderError as exc:
        # A nightly job that fails on upstream throttling gets ignored, which defeats
        # the point. Shape breaks still fail; rate limits skip with a visible reason.
        if exc.status in {429, 503}:
            pytest.skip(f"upstream rate limited ({exc.status})")
        raise

    violations = check_envelope(cap, envelope)
    assert not _errors(violations), f"{provider}/{capability}: " + "; ".join(_errors(violations))
