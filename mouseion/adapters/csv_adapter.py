"""CSV adapter for bulk exports.

State campaign-finance filings and candidate lists are usually a download, not
an API: one CSV behind a link, refreshed on a schedule. The adapter reuses the
REST request preparation and projection, and only changes how the body becomes
raw records. `local_window` pages the whole file locally.

Bulk files are often ZIP archives, and a year of Colorado contributions is 68MB
of CSV. Both are handled without loading every row: the archive is read for the
one member we need, and parsing stops at the last row the page requires.
"""

from __future__ import annotations

import base64
import csv
import io
import itertools
import zipfile
from typing import Any

from ..config import Settings
from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter

#: Refuse an archive member beyond this; a decompression bomb is not a dataset.
MAX_UNCOMPRESSED = 300_000_000


class CsvAdapter(RestJsonAdapter):
    name = "csv"

    def headers(
        self, manifest: Manifest, config: Settings | None = None
    ) -> dict[str, str]:
        headers = super().headers(manifest, config)
        headers["Accept"] = "text/csv, application/zip;q=0.9, */*;q=0.5"
        return headers

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        text = _text(body, capability.zip_member)
        pagination = capability.pagination

        if capability.local_window and pagination:
            # Read only the rows this page needs, plus one to know whether the
            # file ends here. A full year of filings is hundreds of thousands of
            # rows; building them all would defeat the point of paging.
            offset = request.pagination_state.get("window_offset", 0)
            limit = request.pagination_state.get("limit", pagination.default_limit)
            rows = _rows(text, max_rows=offset + limit + 1)
            result = self.build_records(capability, rows, request)
            more = len(rows) > offset + limit
            result.records = result.records[offset : offset + limit]
            request.pagination_state["count"] = len(result.records)
            result.next_cursor = str(offset + limit) if more else None
            return result

        result = self.build_records(capability, _rows(text), request)
        self.paginate_local(capability, result, request, body)
        return result


def _text(body: Any, member: str | None) -> str:
    if not isinstance(body, dict):
        raise ProviderError("expected a CSV body")
    if "_raw_text" in body:
        return body["_raw_text"]
    encoded = body.get("_raw_b64")
    if not encoded:
        raise ProviderError("expected a CSV body")
    return _unzip(base64.b64decode(encoded), member)


def _unzip(data: bytes, member: str | None) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ProviderError("expected a ZIP archive") from None

    names = archive.namelist()
    name = member or next(
        (candidate for candidate in names if candidate.lower().endswith(".csv")), None
    )
    if name is None or name not in names:
        raise ProviderError(
            f"archive has no member '{name}'",
            hint=f"members: {names[:10]}",
        )
    info = archive.getinfo(name)
    if info.file_size > MAX_UNCOMPRESSED:
        raise ProviderError(
            f"archive member is {info.file_size} bytes, over the {MAX_UNCOMPRESSED} limit"
        )
    return archive.read(name).decode("utf-8-sig", errors="replace")


def _rows(text: str, max_rows: int | None = None) -> list[dict[str, Any]]:
    """Parse a CSV/TSV body into row dicts, tolerating a BOM and stray columns."""
    text = text.removeprefix("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows: list[dict[str, Any]] = []
    for row in itertools.islice(reader, max_rows):
        row.pop(None, None)
        rows.append({(key or "").strip(): value for key, value in row.items()})
    return rows
