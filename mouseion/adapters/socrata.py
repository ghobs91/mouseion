"""Socrata SODA adapter (data.cms.gov, data.sf.gov, NYC Open Data, CFTC, ...).

Socrata differs from plain REST by a param-naming rule (`$where`, `$limit`, ...)
and a handful of metadata columns (`:id`, `:updated_at`).
"""

from __future__ import annotations

from typing import Any

from ..manifest import Capability, Manifest
from .base import Adapter, AdapterResult, PreparedRequest, extract_records

SOQL = {
    "where": "$where",
    "select": "$select",
    "order": "$order",
    "group": "$group",
    "having": "$having",
    "q": "$q",
    "limit": "$limit",
    "offset": "$offset",
}


class SocrataAdapter(Adapter):
    name = "socrata"

    def prepare(
        self,
        manifest: Manifest,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> PreparedRequest:
        merged = self.resolve_params(manifest, capability, params)
        merged = self.resolve_dataset(capability, merged)
        template_params = dict(merged)
        path = self.substitute_path(capability.path, merged)

        # Pagination runs before SOQL renaming so `limit`/`offset` are still plain keys.
        state = self.apply_pagination(capability, merged, cursor)

        query = {SOQL.get(k, k): v for k, v in merged.items() if v is not None}
        headers = self.headers(manifest)
        self.apply_auth(manifest, query, headers)
        return PreparedRequest(
            method="GET",
            url=(capability.base or manifest.base).rstrip("/") + "/" + path.lstrip("/"),
            params=query,
            headers=headers,
            pagination_state=state,
            template_params=template_params,
        )

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        raw = extract_records(body, capability.record_path, capability.map_exclude)
        result = self.build_records(capability, raw, request)
        result.next_cursor = self.next_cursor(capability, body, request.pagination_state)
        return result
