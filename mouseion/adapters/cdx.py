"""Wayback CDX adapter.

The CDX API returns a JSON array of arrays: the first row is the field header and
the rest are values. This adapter zips them into records and lets the normal
projection/URL/ID path do the rest.
"""

from __future__ import annotations

from typing import Any

from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter


class CdxAdapter(RestJsonAdapter):
    name = "cdx"

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        if not isinstance(body, list) or not body:
            return self.build_records(capability, [], request)
        header = body[0]
        if not isinstance(header, list):
            return self.build_records(capability, [], request)
        raw = [
            dict(zip(header, row))
            for row in body[1:]
            if isinstance(row, list) and len(row) == len(header)
        ]
        return self.build_records(capability, raw, request)
