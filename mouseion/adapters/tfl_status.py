"""TfL line-status adapter.

The Unified API returns one object per line, with the useful status inside a
``lineStatuses`` list. That nested shape is awkward to filter or cite, so this
adapter flattens it: one row per line status, carrying the line, severity, the
published reason and the validity period.
"""

from __future__ import annotations

from typing import Any

from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter


class TflStatusAdapter(RestJsonAdapter):
    name = "tfl_status"

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        if not isinstance(body, list):
            raise ProviderError("expected a JSON list of line statuses")

        raw: list[dict[str, Any]] = []
        for line in body:
            statuses = line.get("lineStatuses") or [{}]
            for status in statuses:
                period = (status.get("validityPeriods") or [{}])[0]
                raw.append(
                    {
                        "line_id": line.get("id"),
                        "line": line.get("name"),
                        "mode": line.get("modeName"),
                        "severity": status.get("statusSeverity"),
                        "status": status.get("statusSeverityDescription"),
                        "reason": " ".join((status.get("reason") or "").split()),
                        "valid_from": period.get("fromDate"),
                        "valid_to": period.get("toDate"),
                        "is_now": period.get("isNow"),
                    }
                )

        result = self.build_records(capability, raw, request)
        self.paginate_local(capability, result, request, body)
        return result
