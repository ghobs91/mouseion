"""Yahoo Finance chart adapter.

The chart endpoint returns parallel arrays: a ``timestamp`` list and an
``indicators.quote[0]`` object whose ``open``/``high``/``low``/``close``/
``volume`` arrays share that index. This adapter zips them into one row per bar
and stamps each with the symbol from ``meta``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter

_COLUMNS = ("open", "high", "low", "close", "volume")


def _cell(series: dict[str, Any], key: str, index: int) -> Any:
    values = series.get(key) or []
    return values[index] if index < len(values) else None


class YahooChartAdapter(RestJsonAdapter):
    name = "yahoo_chart"

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        chart = body.get("chart") if isinstance(body, dict) else None
        results = (chart or {}).get("result") or []
        if not results:
            raise ProviderError("Yahoo returned no chart result for this symbol")

        result = results[0]
        meta = result.get("meta") or {}
        series = ((result.get("indicators") or {}).get("quote") or [{}])[0]

        raw: list[dict[str, Any]] = []
        for index, stamp in enumerate(result.get("timestamp") or []):
            row = {
                "symbol": meta.get("symbol"),
                "date": datetime.fromtimestamp(stamp, tz=UTC).isoformat(),
            }
            for column in _COLUMNS:
                row[column] = _cell(series, column, index)
            raw.append(row)

        result_page = self.build_records(capability, raw, request)
        self.paginate_local(capability, result_page, request, body)
        return result_page
