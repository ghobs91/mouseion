"""CSV adapter for bulk exports.

State campaign-finance filings and candidate lists are usually a download, not
an API: one CSV behind a link, refreshed on a schedule. The adapter reuses the
REST request preparation and projection, and only changes how the body becomes
raw records. `local_window` pages the whole file locally.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from ..config import Settings
from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter


class CsvAdapter(RestJsonAdapter):
    name = "csv"

    def headers(
        self, manifest: Manifest, config: Settings | None = None
    ) -> dict[str, str]:
        headers = super().headers(manifest, config)
        headers["Accept"] = "text/csv, text/plain;q=0.9, */*;q=0.5"
        return headers

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        text = body.get("_raw_text") if isinstance(body, dict) else None
        if not text:
            raise ProviderError("expected a CSV body")

        result = self.build_records(capability, _rows(text), request)
        self.paginate_local(capability, result, request, body)
        return result


def _rows(text: str) -> list[dict[str, Any]]:
    """Parse a CSV/TSV body into row dicts, tolerating a BOM and stray columns."""
    text = text.removeprefix("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    rows: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(text), dialect=dialect):
        row.pop(None, None)
        rows.append({(key or "").strip(): value for key, value in row.items()})
    return rows
