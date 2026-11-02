"""Coinbase exchange-rates adapter.

``/v2/exchange-rates`` returns one object whose ``data.rates`` maps an asset code
to a decimal rate. That map is not a record list, so this adapter turns each
entry into a row and applies the caller's local ``q`` filter.
"""

from __future__ import annotations

from typing import Any

from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter


class CoinbaseRatesAdapter(RestJsonAdapter):
    name = "coinbase_rates"

    def prepare(
        self,
        manifest: Manifest,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> PreparedRequest:
        prepared = super().prepare(manifest, capability, params, cursor)
        # `q` filters the rate table locally, not upstream.
        prepared.params.pop("q", None)
        return prepared

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        data = body.get("data") if isinstance(body, dict) else None
        data = data or {}
        base = data.get("currency")
        rates = data.get("rates") or {}

        rows = [
            {"asset": asset, "rate": rate, "currency": base}
            for asset, rate in rates.items()
        ]

        query = str(request.template_params.get("q") or "").strip().upper()
        if query:
            rows = [row for row in rows if query in str(row["asset"]).upper()]

        result = self.build_records(capability, rows, request)
        self.paginate_local(capability, result, request, body)
        return result
