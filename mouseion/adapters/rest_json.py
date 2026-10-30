"""Generic JSON REST adapter: covers the majority of upstreams, GET or POST."""

from __future__ import annotations

from typing import Any

from ..envelope import ProviderError, dig
from ..manifest import Capability, Manifest
from .base import Adapter, AdapterResult, PreparedRequest, extract_records


class RestJsonAdapter(Adapter):
    name = "rest_json"

    def prepare(
        self,
        manifest: Manifest,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> PreparedRequest:
        merged = self.resolve_params(manifest, capability, params)
        merged = self.resolve_dataset(capability, merged)
        # Path params are popped by substitution, so snapshot them first: URL
        # templates still need them to build a citation link.
        template_params = dict(merged)
        path = self.substitute_path(capability.path, merged)

        # Pagination writes limit/offset/page into `merged`; whether those become
        # query params or body fields depends on the capability.
        state = self.apply_pagination(capability, merged, None if capability.local_window else cursor)
        if capability.local_window and capability.pagination:
            state["window_offset"] = _as_int(cursor, default=0)

        headers = self.headers(manifest)
        query: dict[str, Any] = {}
        self.apply_auth(manifest, query, headers)

        url = (capability.base or manifest.base).rstrip("/") + "/" + path.lstrip("/")

        if capability.body_template is not None:
            headers["Content-Type"] = "application/json"
            return PreparedRequest(
                method="POST",
                url=url,
                params=query,
                headers=headers,
                pagination_state=state,
                json_body=self.render_body(capability.body_template, merged),
                template_params=template_params,
                shared_cache_key=capability.local_window,
            )

        if capability.query_template is not None:
            rendered = self.render_body(capability.query_template, merged)
            query.update(_flatten(rendered))
        else:
            query.update({key: value for key, value in merged.items() if value is not None})
        return PreparedRequest(
            method=capability.method,
            url=url,
            params=query,
            headers=headers,
            pagination_state=state,
            template_params=template_params,
            shared_cache_key=capability.local_window,
        )

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        raw = extract_records(
            body, capability.record_path, capability.map_exclude, capability.transpose
        )
        if capability.local_filter is not None:
            value = request.template_params.get(capability.local_filter.param)
            if value in (None, ""):
                value = request.params.get(capability.local_filter.param)
            if value not in (None, ""):
                wanted = str(value).lower()
                raw = [
                    item
                    for item in raw
                    if str(dig(item, capability.local_filter.field, "")).lower() == wanted
                ]
        result = self.build_records(capability, raw, request)
        self.paginate_local(capability, result, request, body)

        if capability.terms_field:
            result.terms = dig(body, capability.terms_field)
        if capability.version_field:
            version = dig(body, capability.version_field)
            result.upstream_version = str(version) if version is not None else None
        else:
            total = dig(body, "meta.results.total")
            if total is None:
                total = dig(body, "meta.total")
            if total is None:
                total = dig(body, "meta.total-count")
            result.upstream_version = f"total={total}" if total is not None else None
        return result


def _as_int(value: str | None, *, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ProviderError(f"cursor must be an integer offset, got {value!r}") from None


def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    """{'a': {'b': 1}} -> {'a[b]': 1}; list items use indices, as DKAN and Socrata expect."""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}[{key}]" if prefix else str(key)))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            out.update(_flatten(value, f"{prefix}[{index}]"))
    elif prefix:
        out[prefix] = node
    return out
