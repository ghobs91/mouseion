"""FAA NAS airport-status adapter.

The FAA publishes airport closures, arrival/departure delays, ground delays and
ground stops as one XML document. The shape is a delay type wrapping a list of
airports, so a generic JSON projection cannot reach the rows: this adapter
flattens type x airport into one record each.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..config import Settings
from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter


class FaaNasAdapter(RestJsonAdapter):
    name = "faa_nas"

    def headers(
        self, manifest: Manifest, config: Settings | None = None
    ) -> dict[str, str]:
        headers = super().headers(manifest, config)
        headers["Accept"] = "application/xml, text/xml;q=0.9, */*;q=0.5"
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
            raise ProviderError("expected the FAA XML status document")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ProviderError(f"invalid FAA XML: {exc}") from None

        rows: list[dict[str, Any]] = []
        for delay_type in root.findall("Delay_type"):
            name = (delay_type.findtext("Name") or "").strip()
            for group in delay_type:
                if group.tag == "Name":
                    continue
                for item in group:
                    row: dict[str, Any] = {"type": name}
                    for field in item:
                        row[field.tag.lower()] = (field.text or "").strip()
                    rows.append(row)

        result = self.build_records(capability, rows, request)
        result.next_cursor = None
        return result
