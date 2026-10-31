"""HTML table adapter.

Some election authorities publish a table and nothing else: a city council
roster, a recent-filings list, a candidate index. There is no CSV and no API, so
the only machine-readable surface is the markup itself.

Tables are selected by their header row, not by position. Portals put navigation
and calendar tables ahead of the data, and a positional index breaks the first
time the page is redesigned. `table_index` is the fallback when two tables share
a header.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from ..config import Settings
from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter


class _Tables(HTMLParser):
    """Collect every table as a list of rows, each a list of cell strings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in {"td", "th"} and self._stack and self._stack[-1]:
            self._cell = []
            self._stack[-1][-1].append(self._cell)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            self._cell = None
        elif tag == "tr":
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def _rows(table: list[list[list[str]]]) -> list[list[str]]:
    """Flatten cells to text and drop empty rows."""
    out: list[list[str]] = []
    for row in table:
        cells = [" ".join("".join(cell).split()) for cell in row]
        if any(cells):
            out.append(cells)
    return out


class HtmlTableAdapter(RestJsonAdapter):
    name = "html_table"

    def headers(
        self, manifest: Manifest, config: Settings | None = None
    ) -> dict[str, str]:
        headers = super().headers(manifest, config)
        headers["Accept"] = "text/html, application/xhtml+xml;q=0.9, */*;q=0.5"
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
            raise ProviderError("expected an HTML body")

        parser = _Tables()
        try:
            parser.feed(text)
        except Exception:  # legacy portals emit malformed markup
            pass

        tables = [rows for rows in (_rows(table) for table in parser.tables) if len(rows) >= 2]
        table = _select(capability, tables)
        result = self.build_records(capability, _records(table), request)
        self.paginate_local(capability, result, request, body)
        return result


def _select(
    capability: Capability, tables: list[list[list[str]]]
) -> list[list[str]]:
    if capability.table_headers:
        wanted = [header.lower() for header in capability.table_headers]
        matches = [
            table
            for table in tables
            if all(
                any(needle in cell for cell in [c.lower() for c in table[0]])
                for needle in wanted
            )
        ]
        if not matches:
            raise ProviderError(
                f"no table has headers {capability.table_headers}",
                hint=f"found: {[table[0] for table in tables][:5]}",
            )
        if capability.table_index >= len(matches):
            raise ProviderError(
                f"table index {capability.table_index} is out of range",
                hint=f"{len(matches)} table(s) match those headers",
            )
        return matches[capability.table_index]
    if capability.table_index >= len(tables):
        raise ProviderError(
            f"table index {capability.table_index} is out of range",
            hint=f"found {len(tables)} table(s)",
        )
    return tables[capability.table_index]


def _records(table: list[list[str]]) -> list[dict[str, Any]]:
    """Header row plus data rows, with duplicate headers disambiguated."""
    header: list[str] = []
    seen: dict[str, int] = {}
    for cell in table[0]:
        key = _normalise(cell) or "column"
        count = seen.get(key, 0)
        seen[key] = count + 1
        header.append(key if count == 0 else f"{key}_{count + 1}")

    out: list[dict[str, Any]] = []
    for row in table[1:]:
        out.append(
            {
                header[index]: row[index] if index < len(row) else ""
                for index in range(len(header))
            }
        )
    return out
