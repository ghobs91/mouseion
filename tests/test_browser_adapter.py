from __future__ import annotations

import json
from pathlib import Path

from mouseion.adapters import get_adapter

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _body() -> dict:
    return json.loads(
        (FIXTURES / "costco_products__search.json").read_text(encoding="utf-8")
    )["body"]


def test_costco_prepare_builds_the_browser_url(registry):
    manifest = registry.get("costco_products")
    cap = manifest.capabilities["search"]
    prepared = get_adapter(manifest.adapter).prepare(
        manifest, cap, {"query": "electric snowblower", "limit": 2}, None
    )

    assert prepared.browser_url == (
        "https://www.costco.com/s?keyword=electric%20snowblower"
    )
    assert cap.browser_response_match == "/catalog/search/api/v1/search"


def test_costco_parse_selects_the_session_warehouse_price(registry):
    manifest = registry.get("costco_products")
    cap = manifest.capabilities["search"]
    adapter = get_adapter(manifest.adapter)
    prepared = adapter.prepare(
        manifest, cap, {"query": "electric snowblower", "limit": 2}, None
    )
    prepared.browser_meta["warehouseId"] = "226-wh"

    result = adapter.parse(manifest, cap, _body(), prepared)

    assert len(result.records) == 2
    first = result.records[0]
    assert first.source_record_id == "4000358874"
    assert first.source_url.startswith("https://www.costco.com/p/-/")
    assert first.data["price"] == 699.99
    assert first.data["list_price"] == 899.99
    assert first.data["availability"] == "in_stock"
    assert first.data["brand"] == "Greenworks"
    assert first.data["currency"] == "USD"
    assert first.data["condition"] == "new"
    assert first.data["variant_id"] == "1936691"
