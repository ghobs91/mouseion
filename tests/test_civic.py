from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mouseion.civic import (
    Authority,
    Jurisdiction,
    discover,
    load_jurisdictions,
    probe_authority,
    summarize,
)

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    import asyncio

    return asyncio.run(coro)


def test_registry_covers_states_territories_and_cities():
    jurisdictions = load_jurisdictions(ROOT / "jurisdictions.yaml")
    levels = [j.level for j in jurisdictions]

    assert levels.count("state") == 51  # 50 states + DC
    assert levels.count("territory") == 5
    assert levels.count("city") >= 40
    assert len({j.id for j in jurisdictions}) == len(jurisdictions)


def test_every_jurisdiction_has_an_official_election_authority():
    for jurisdiction in load_jurisdictions(ROOT / "jurisdictions.yaml"):
        assert jurisdiction.election is not None, jurisdiction.id
        assert jurisdiction.election.url.startswith("https://"), jurisdiction.id


def test_city_entries_name_their_state():
    for jurisdiction in load_jurisdictions(ROOT / "jurisdictions.yaml"):
        if jurisdiction.level == "city":
            assert jurisdiction.state, jurisdiction.id


def test_registry_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "j.yaml"
    path.write_text(
        "jurisdictions:\n"
        "  - id: US-XX\n"
        "    name: Example\n"
        "    level: state\n"
        "    state: XX\n"
        "  - id: US-XX\n"
        "    name: Example again\n"
        "    level: state\n"
        "    state: XX\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate jurisdiction"):
        load_jurisdictions(path)


def _jurisdiction(url: str) -> Jurisdiction:
    return Jurisdiction(
        id="US-XX",
        name="Example",
        level="state",
        state="XX",
        election=Authority(name="Example Elections", url=url),
    )


def _probe(handler):
    transport = httpx.MockTransport(handler)

    async def scenario():
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            return await probe_authority(
                client,
                _jurisdiction("https://example.test/elections"),
                "election",
                Authority(name="Example Elections", url="https://example.test/elections"),
            )

    return run(scenario())


def test_probe_detects_json():
    surface = _probe(
        lambda request: httpx.Response(
            200,
            json=[{"candidate": "A"}, {"candidate": "B"}],
            headers={"content-type": "application/json"},
        )
    )
    assert surface.format == "json"
    assert surface.records == 2
    assert surface.machine_readable


def test_probe_detects_csv():
    surface = _probe(
        lambda request: httpx.Response(
            200,
            text="name,office\nA,Mayor\nB,Council\n",
            headers={"content-type": "text/csv"},
        )
    )
    assert surface.format == "csv"
    assert surface.records == 2


def test_probe_detects_pdf():
    surface = _probe(
        lambda request: httpx.Response(
            200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
        )
    )
    assert surface.format == "pdf"


def test_probe_detects_html_tables():
    page = "<html><body><table><tr><td>a</td></tr></table></body></html>"
    surface = _probe(
        lambda request: httpx.Response(
            200, text=page, headers={"content-type": "text/html"}
        )
    )
    assert surface.format == "html_tables"
    assert surface.tables == 1


def test_probe_follows_a_machine_readable_link():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data.json":
            return httpx.Response(
                200, json=[{"a": 1}], headers={"content-type": "application/json"}
            )
        return httpx.Response(
            200,
            text='<html><a href="/data.json">Download</a></html>',
            headers={"content-type": "text/html"},
        )

    surface = _probe(handler)
    assert surface.format == "json"
    assert "https://example.test/data.json" in surface.links


def test_probe_reports_edge_blocking_as_blocked():
    surface = _probe(lambda request: httpx.Response(403, text="denied"))
    assert surface.format == "blocked"
    assert surface.http == 403


def test_probe_reports_401_as_auth_required():
    surface = _probe(lambda request: httpx.Response(401, text="login"))
    assert surface.format == "auth_required"


def test_probe_reports_404_as_error():
    surface = _probe(lambda request: httpx.Response(404, text="missing"))
    assert surface.format == "error"
    assert surface.http == 404


def test_probe_reports_incomplete_tls_chain():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] incomplete chain")

    surface = _probe(handler)
    assert surface.format == "tls_error"
    assert "certificate chain" in surface.detail


def test_probe_marks_an_empty_body_as_a_js_shell():
    surface = _probe(lambda request: httpx.Response(200, content=b""))
    assert surface.format == "html"
    assert "JS-rendered" in surface.detail


def test_summarize_counts_formats_and_gaps():
    jurisdictions = [
        _jurisdiction("https://example.test/elections"),
        Jurisdiction(id="US-YY", name="Other", level="state", state="YY"),
    ]
    surfaces = [
        _probe(lambda request: httpx.Response(200, json=[{"a": 1}])),
    ]
    report = summarize(jurisdictions, surfaces)

    assert report["jurisdictions"] == 2
    assert report["authorities"] == 1
    assert report["missing_authorities"] == {"election": 1, "campaign_finance": 2}
    assert report["machine_readable"] == 1
    assert report["machine_readable_pct"] == 100.0
    assert report["by_format"] == {"json": 1}


def test_discover_runs_every_authority_through_the_transport():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[{"a": 1}])

    jurisdictions = [
        _jurisdiction("https://example.test/elections"),
        Jurisdiction(
            id="US-YY",
            name="Other",
            level="state",
            state="YY",
            election=Authority(name="Other Elections", url="https://other.test/"),
        ),
    ]

    report = run(discover(jurisdictions, transport=httpx.MockTransport(handler)))

    assert report["authorities"] == 2
    assert len(seen) == 2
    assert all(result["format"] == "json" for result in report["results"])
