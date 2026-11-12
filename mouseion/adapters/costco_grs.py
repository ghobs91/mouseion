"""Costco GRS search adapter for the browser transport.

The browser intercepts Costco's own ``/catalog/search/api/v1/search`` response.
The GRS payload nests each product, its variants, and a flat ``variantRollupValues``
map whose price keys are warehouse-specific (``inventory(226-wh, price)``). This
adapter projects that shape onto the same offer fields the other retailers use.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..envelope import ProviderError, dig
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter


class CostcoGrsAdapter(RestJsonAdapter):
    name = "costco_grs"

    def prepare(
        self,
        manifest: Manifest,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> PreparedRequest:
        prepared = super().prepare(manifest, capability, params, cursor)
        query = prepared.template_params.get("query")
        if capability.browser_url and query not in (None, ""):
            prepared.browser_url = capability.browser_url.format(
                query=quote(str(query), safe="")
            )
        return prepared

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        if not isinstance(body, dict) or "searchResult" not in body:
            raise ProviderError(
                "Costco did not return a search result set",
                hint=(
                    "the browser may be showing a challenge; retry with "
                    "MOUSEION_BROWSER_OFFSCREEN=0 and complete it manually"
                ),
            )
        results = dig(body, capability.record_path or "searchResult.results") or []
        warehouse = str(request.browser_meta.get("warehouseId") or "")
        rows = [
            self._flatten(item, warehouse)
            for item in results
            if isinstance(item, dict)
        ]

        limit = request.template_params.get("limit")
        if limit is not None:
            try:
                rows = rows[: max(1, int(limit))]
            except (TypeError, ValueError):
                pass

        return self.build_records(capability, rows, request)

    def _flatten(self, item: dict[str, Any], warehouse: str) -> dict[str, Any]:
        product = item.get("product") or {}
        variants = product.get("variants") or []
        variant = variants[0] if variants else {}
        rollup = item.get("variantRollupValues") or {}
        attributes = product.get("attributes") or {}
        rating = product.get("rating") or {}
        categories = product.get("categories") or []
        brands = product.get("brands") or []

        price = _warehouse_price(rollup, warehouse)
        list_price = _first(rollup.get("originalPrice"))
        if list_price is None:
            listed = _first(rollup.get("price"))
            if listed is not None and price is not None and listed > price:
                list_price = listed

        return {
            "item_id": product.get("id") or item.get("id"),
            "variant_id": variant.get("id"),
            "title": product.get("title"),
            "price": price,
            "list_price": list_price,
            "currency": "USD",
            "condition": "new",
            "availability": _availability(rollup, attributes),
            "rating": rating.get("averageRating"),
            "reviews": rating.get("ratingCount"),
            "brand": brands[0] if brands else None,
            "category": categories[-1] if categories else None,
            "image": _attribute_text(attributes, "primary_image"),
            "url": product.get("uri"),
            "description": product.get("description") or None,
        }


def _warehouse_price(rollup: dict[str, Any], warehouse: str) -> Any:
    if warehouse:
        value = _first(rollup.get(f"inventory({warehouse}, price)"))
        if value is not None:
            return value
    for key in sorted(rollup):
        if key.endswith(", price)") and "original" not in key.lower():
            value = _first(rollup[key])
            if value is not None:
                return value
    return _first(rollup.get("price"))


def _availability(rollup: dict[str, Any], attributes: dict[str, Any]) -> str | None:
    for key, value in rollup.items():
        if "availability" not in key:
            continue
        values = value if isinstance(value, list) else [value]
        if any(str(entry).upper() == "IN_STOCK" for entry in values):
            return "in_stock"
    buyable = (attributes.get("buyable") or {}).get("numbers") or []
    if buyable and buyable[0] == 1.0:
        return "in_stock"
    return None


def _attribute_text(attributes: dict[str, Any], name: str) -> Any:
    values = (attributes.get(name) or {}).get("text") or []
    return values[0] if values else None


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value
