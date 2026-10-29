"""SDMX-JSON adapter.

ECB's Data Portal returns SDMX-JSON: a `structure` block describing dimensions,
and a `dataSets[0].series` map keyed by dimension-index tuples. This adapter
flattens each observation into one row with dimension ids as columns.

The shape is shared by other SDMX 2.1 JSON endpoints, so the adapter is generic.
"""

from __future__ import annotations

from typing import Any

from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter


class SdmxJsonAdapter(RestJsonAdapter):
    name = "sdmx_json"

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        if not isinstance(body, dict):
            raise ValueError("expected an SDMX-JSON object")

        data_sets = body.get("dataSets") or []
        structure = body.get("structure") or {}
        dimensions = structure.get("dimensions") or {}
        series_dims = dimensions.get("series") or []
        obs_dims = dimensions.get("observation") or []

        raw: list[dict[str, Any]] = []
        for series_key, series in ((data_sets[0].get("series") or {}) if data_sets else {}).items():
            parts = str(series_key).split(":")
            row_base: dict[str, Any] = {}
            for index, dimension in enumerate(series_dims):
                values = dimension.get("values") or []
                position = int(parts[index]) if index < len(parts) else 0
                if 0 <= position < len(values):
                    row_base[dimension.get("id", f"dim{index}")] = values[position].get("id")

            for obs_key, observation in (series.get("observations") or {}).items():
                row = dict(row_base)
                position = int(obs_key)
                for index, dimension in enumerate(obs_dims):
                    values = dimension.get("values") or []
                    if 0 <= position < len(values):
                        row[dimension.get("id", f"obs{index}")] = values[position].get("id")
                row["value"] = observation[0] if observation else None
                row["series_key"] = str(series_key)
                raw.append(row)

        pagination = capability.pagination
        if pagination is not None:
            offset = int(request.pagination_state.get("offset", 0))
            limit = int(request.pagination_state.get("limit", pagination.default_limit))
            total = len(raw)
            raw = raw[offset : offset + limit]
        else:
            offset, limit, total = 0, len(raw), len(raw)

        result = self.build_records(capability, raw, request)
        if pagination is not None:
            following = offset + limit
            result.next_cursor = str(following) if following < total else None
        prepared = (body.get("header") or {}).get("prepared")
        if prepared:
            result.upstream_version = str(prepared)
        return result
