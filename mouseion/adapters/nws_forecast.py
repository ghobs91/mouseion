"""NWS point-forecast adapter.

``forecast.weather.gov`` publishes the seven-day forecast as two columnar blocks
for the same periods: ``data`` holds temperature, conditions, precipitation
probability and the narrative, while ``time`` holds the period label, valid time
and high/low marker. Neither block is a record list on its own, so this adapter
zips them by index and projects one record per period.
"""

from __future__ import annotations

from typing import Any

from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter

_COLUMNS = (
    ("period", "time", "startPeriodName"),
    ("start", "time", "startValidTime"),
    ("temp_label", "time", "tempLabel"),
    ("temperature", "data", "temperature"),
    ("precipitation_probability", "data", "pop"),
    ("weather", "data", "weather"),
    ("detail", "data", "text"),
    ("icon", "data", "iconLink"),
    ("hazard", "data", "hazard"),
    ("hazard_url", "data", "hazardUrl"),
)


def _cell(block: dict[str, Any], key: str, index: int) -> Any:
    values = block.get(key) or []
    return values[index] if index < len(values) else None


class NwsForecastAdapter(RestJsonAdapter):
    name = "nws_forecast"

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        if not isinstance(body, dict):
            raise ProviderError("expected a JSON forecast object")

        data = body.get("data") or {}
        time = body.get("time") or {}
        location = body.get("location") or {}
        count = max(len(data.get("temperature") or []), len(time.get("startPeriodName") or []))
        area = location.get("areaDescription")

        raw: list[dict[str, Any]] = []
        for index in range(count):
            row = {
                name: _cell(body.get(block) or {}, key, index)
                for name, block, key in _COLUMNS
            }
            row["area"] = area
            raw.append(row)

        result = self.build_records(capability, raw, request)
        self.paginate_local(capability, result, request, body)
        return result
