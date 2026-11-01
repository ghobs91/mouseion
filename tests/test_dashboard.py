from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from http.server import ThreadingHTTPServer

from mouseion.credentials import CredentialStore
from mouseion.dashboard import DashboardHandler, build_snapshot
from mouseion.favicons import FaviconStore

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def store(tmp_path):
    return CredentialStore(tmp_path / "credentials.json")


@pytest.fixture
def snapshot(registry, store):
    return build_snapshot(
        registry,
        jurisdictions_path=ROOT / "jurisdictions.yaml",
        coverage_path=ROOT / "eval" / "civic-coverage.json",
        datasets_path=ROOT / "eval" / "civic-datasets.json",
        store=store,
    )


def test_snapshot_counts_match_the_registry(snapshot, registry):
    totals = snapshot["totals"]
    assert totals["providers"] == len(registry.manifests)
    assert totals["capabilities"] == sum(
        len(manifest.capabilities) for manifest in registry.manifests.values()
    )
    assert totals["domains"] == len(snapshot["domains"])
    assert sum(domain["providers"] for domain in snapshot["domains"]) == totals["providers"]
    assert totals["tier1"] + sum(
        domain["tier2"] for domain in snapshot["domains"]
    ) == totals["providers"]


def test_every_provider_card_has_its_capabilities(snapshot, registry):
    for provider in snapshot["providers"]:
        assert provider["capabilities"], provider["id"]
        assert provider["id"] in registry.manifests


def test_jurisdictions_are_joined_with_the_coverage_probe(snapshot):
    assert len(snapshot["jurisdictions"]) == 101
    california = next(row for row in snapshot["jurisdictions"] if row["id"] == "US-CA")
    assert california["election"]["format"] == "html"
    assert california["campaign_finance"]["format"] == "html"
    # Coverage is exact: an LA-only provider does not claim the whole state.
    assert california["providers"] == []

    new_york = next(row for row in snapshot["jurisdictions"] if row["id"] == "US-NY")
    assert set(new_york["providers"]) == {"ny_campaign_finance", "ny_campaign_filers"}

    los_angeles = next(
        row for row in snapshot["jurisdictions"] if row["id"] == "US-CA-LOS-ANGELES"
    )
    assert los_angeles["providers"] == ["la_campaign_finance"]

    king = next(row for row in snapshot["jurisdictions"] if row["id"] == "US-WA-KING")
    assert "king_county_election_results" in king["providers"]


def test_snapshot_reports_the_audit(snapshot):
    assert snapshot["audit"]["ok"] is True
    assert snapshot["audit"]["errors"] == 0
    assert snapshot["audit"]["warnings"] >= 1


def test_snapshot_survives_missing_coverage_files(registry):
    snapshot = build_snapshot(
        registry,
        jurisdictions_path=ROOT / "jurisdictions.yaml",
        coverage_path=ROOT / "does-not-exist.json",
        datasets_path=ROOT / "does-not-exist.json",
    )
    assert snapshot["totals"]["authorities"] == 0
    assert snapshot["totals"]["datasets"] == 0
    assert snapshot["catalog"]["kept"] == 0


@pytest.fixture
def server(registry, store, tmp_path):
    def snapshot() -> dict:
        return build_snapshot(
            registry,
            jurisdictions_path=ROOT / "jurisdictions.yaml",
            coverage_path=ROOT / "eval" / "civic-coverage.json",
            store=store,
        )

    known = frozenset(
        name
        for manifest in registry.manifests.values()
        for name in (manifest.auth.env, manifest.auth.client_id_env, manifest.auth.client_secret_env)
        if name
    )
    handler = type(
        "TestDashboardHandler",
        (DashboardHandler,),
        {"snapshot": staticmethod(snapshot), "store": store,
         "favicons": FaviconStore({"data.ny.gov"}, tmp_path / "favicons",
                                  fetcher=lambda host: (b"\x89PNG\r\n\x1a\nicon", "image/png")),
         "known_envs": staticmethod(lambda: known)},
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def get(url: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read(), response.headers.get("content-type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("content-type", "")


def test_health_endpoint(server):
    status, body, content_type = get(server + "/healthz")
    assert status == 200
    assert json.loads(body) == {"ok": True}
    assert content_type.startswith("application/json")


def test_snapshot_endpoint(server):
    status, body, _ = get(server + "/api/snapshot")
    assert status == 200
    payload = json.loads(body)
    assert payload["totals"]["providers"] == 100
    assert payload["totals"]["jurisdictions"] == 101


def test_index_is_served(server):
    status, body, content_type = get(server + "/")
    assert status == 200
    assert content_type.startswith("text/html")
    assert b"mouseion" in body
    assert b"/api/snapshot" in body


def test_unknown_path_is_404(server):
    status, _, _ = get(server + "/nope")
    assert status == 404


def test_path_traversal_is_refused(server):
    status, _, _ = get(server + "/../providers/openfec.yaml")
    assert status == 404


# -- credential endpoints -------------------------------------------------


def call(url: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_snapshot_never_contains_a_stored_secret(registry, store):
    store.set("FRED_API_KEY", "very-secret-value")
    payload = build_snapshot(
        registry,
        jurisdictions_path=ROOT / "jurisdictions.yaml",
        coverage_path=ROOT / "eval" / "civic-coverage.json",
        store=store,
    )
    assert "very-secret-value" not in json.dumps(payload)
    fred = next(item for item in payload["providers"] if item["id"] == "fred")
    assert fred["credentials"][0]["configured"] is True
    assert fred["credentials"][0]["source"] == "store"


def test_credential_write_requires_the_custom_header(server):
    status, body = call(
        server + "/api/credentials",
        "POST",
        {"env": "FRED_API_KEY", "value": "secret"},
        {"Content-Type": "application/json"},
    )
    assert status == 403
    assert body["error"] == "forbidden"


def test_credential_write_and_delete(server):
    headers = {"Content-Type": "application/json", "X-Mouseion-Request": "1"}
    status, body = call(
        server + "/api/credentials",
        "POST",
        {"env": "FRED_API_KEY", "value": "secret"},
        headers,
    )
    assert status == 200
    assert body == {"env": "FRED_API_KEY", "configured": True, "source": "store"}

    _, snapshot = call(server + "/api/snapshot")
    fred = next(item for item in snapshot["providers"] if item["id"] == "fred")
    assert fred["credentials"][0]["source"] == "store"
    assert snapshot["totals"]["credentials_missing"] == 16

    status, body = call(
        server + "/api/credentials?env=FRED_API_KEY",
        "DELETE",
        headers={"X-Mouseion-Request": "1"},
    )
    assert status == 200
    assert body["removed"] is True
    assert body["configured"] is False


def test_credential_write_rejects_unknown_names(server):
    status, body = call(
        server + "/api/credentials",
        "POST",
        {"env": "NOT_A_PROVIDER_KEY", "value": "secret"},
        {"Content-Type": "application/json", "X-Mouseion-Request": "1"},
    )
    assert status == 400
    assert "unknown" in body["error"]


def test_credential_write_requires_json(server):
    status, _ = call(
        server + "/api/credentials",
        "POST",
        None,
        {"Content-Type": "text/plain", "X-Mouseion-Request": "1"},
    )
    assert status == 400


def test_favicon_endpoint_serves_an_allowed_host(server):
    status, body, content_type = get(server + "/api/favicon?host=data.ny.gov&label=NY")
    assert status == 200
    assert content_type == "image/png"
    assert body.startswith(b"\x89PNG")


def test_favicon_endpoint_refuses_an_unknown_host(server):
    status, _, _ = get(server + "/api/favicon?host=evil.test&label=Evil")
    assert status == 404
