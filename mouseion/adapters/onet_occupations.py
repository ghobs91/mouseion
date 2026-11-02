"""O*NET occupation adapter.

The O*NET database is published as per-table CSV files. ``occupation_data.csv``
is the small one: about a thousand occupations with a SOC code, title and
description. It is a bulk file with no server-side search, so this adapter reads
it and filters locally on the caller's ``q``.
"""

from __future__ import annotations

from typing import Any

from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .csv_adapter import CsvAdapter, _rows, _text


class OnetOccupationsAdapter(CsvAdapter):
    name = "onet_occupations"

    def prepare(
        self,
        manifest: Manifest,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> PreparedRequest:
        prepared = super().prepare(manifest, capability, params, cursor)
        # `q` filters the downloaded rows locally; the CSV endpoint has no query.
        prepared.params.pop("q", None)
        return prepared

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        rows = _rows(_text(body, capability.zip_member))

        query = str(request.template_params.get("q") or "").strip().lower()
        if query:
            rows = [
                row
                for row in rows
                if any(query in str(value).lower() for value in row.values())
            ]

        result = self.build_records(capability, rows, request)
        self.paginate_local(capability, result, request, body)
        return result
