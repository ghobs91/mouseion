"""Adapter contract.

An adapter turns (manifest, capability, params, cursor) into an HTTP request,
and turns the response body back into normalized records. Adding a new *shape*
of upstream means adding an adapter; adding a new *provider* of a known shape
means adding a YAML file.
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from ..config import Settings
from ..config import settings as default_settings
from ..envelope import ProviderError, Record, dig, utcnow
from ..manifest import Capability, Manifest

_PATH_PARAM = re.compile(r"{(\w+)(?::([^}]+))?}")


@dataclass(slots=True)
class PreparedRequest:
    method: str
    url: str
    params: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    pagination_state: dict[str, Any] = field(default_factory=dict)
    json_body: Any | None = None
    #: Resolved params before they were split into query/body. URL templates may
    #: reference these, so a body-only API can still build a citation link.
    template_params: dict[str, Any] = field(default_factory=dict)
    #: All pages of this capability share one upstream response, so the cache key
    #: must ignore the cursor.
    shared_cache_key: bool = False


@dataclass(slots=True)
class AdapterResult:
    records: list[Record]
    next_cursor: str | None = None
    upstream_version: str | None = None
    terms: str | None = None
    missing: list[str] = field(default_factory=list)


class Adapter(ABC):
    name: str = "base"

    @abstractmethod
    def prepare(
        self,
        manifest: Manifest,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> PreparedRequest: ...

    @abstractmethod
    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult: ...

    # -- shared helpers -------------------------------------------------

    def resolve_params(
        self, manifest: Manifest, capability: Capability, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate caller params, then overlay manifest defaults.

        Unknown keys are dropped, so a hallucinated parameter is a no-op
        instead of a 400 the model cannot interpret.
        """
        try:
            resolved = capability.coerce(params)
        except ValueError as exc:
            raise ProviderError(str(exc), hint=f"valid params: {sorted(capability.params)}") from None
        merged = dict(capability.defaults)
        merged.update({k: v for k, v in resolved.items() if v is not None})

        # Check required params before aliasing, so the error names the param the
        # caller was told about (ids), not the upstream spelling (id).
        missing = [name for name in capability.required_params() if merged.get(name) in (None, "", [])]
        if missing:
            raise ProviderError(
                f"missing required param(s): {', '.join(missing)}",
                hint=f"required: {capability.required_params()}; see provider_describe",
            )

        if capability.alias:
            merged = {capability.alias.get(k, k): v for k, v in merged.items()}
        return merged

    def headers(self, manifest: Manifest, config: Settings | None = None) -> dict[str, str]:
        cfg = config or default_settings
        headers = {"User-Agent": cfg.user_agent, "Accept": "application/json"}
        headers.update(manifest.headers)
        return headers

    def apply_auth(
        self, manifest: Manifest, params: dict[str, Any], headers: dict[str, str]
    ) -> None:
        auth = manifest.auth
        if auth.type == "user_agent":
            # Some upstreams (SEC) block unidentified clients. Fail with a hint
            # instead of forwarding a 403 HTML page to the model.
            value = os.getenv(auth.env) if auth.env else None
            if not value:
                raise ProviderError(
                    f"provider '{manifest.id}' requires a contact User-Agent",
                    hint=f"set {auth.env or 'MOUSEION_UA'} to 'Your Name you@example.com'",
                )
            headers["User-Agent"] = value
            return
        if auth.type != "api_key":
            return
        if not auth.env:
            raise ProviderError(f"provider '{manifest.id}' declares api_key auth without env")
        key = os.getenv(auth.env)
        if not key:
            raise ProviderError(
                f"missing credential for provider '{manifest.id}'",
                hint=f"set {auth.env} in the environment; provider_describe names the auth type",
            )
        if auth.location == "query":
            params[auth.param or "api_key"] = key
        else:
            headers[auth.param or "Authorization"] = f"{auth.prefix}{key}"

    def apply_pagination(
        self,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> dict[str, Any]:
        """Resolve limit + cursor into request params. Returns state for next_cursor()."""
        pg = capability.pagination
        if pg is None:
            return {}

        raw_limit = params.pop("limit", None)
        try:
            limit = int(raw_limit) if raw_limit is not None else pg.default_limit
        except (TypeError, ValueError):
            raise ProviderError(f"'limit' must be an integer, got {raw_limit!r}") from None
        limit = max(pg.min_limit, min(limit, pg.max_limit))

        state: dict[str, Any] = {"mode": pg.mode, "limit": limit}
        if pg.limit_param:
            params[pg.limit_param] = limit

        if pg.mode == "token":
            if cursor:
                params[pg.param or "page_token"] = cursor
        elif pg.mode == "offset":
            try:
                offset = int(cursor) if cursor not in (None, "") else pg.start
            except (TypeError, ValueError):
                raise ProviderError(f"cursor must be an integer offset, got {cursor!r}") from None
            state["offset"] = offset
            if pg.param:
                params[pg.param] = offset
        elif pg.mode == "page":
            try:
                page = int(cursor) if cursor not in (None, "") else pg.start
            except (TypeError, ValueError):
                raise ProviderError(f"cursor must be an integer page, got {cursor!r}") from None
            state["page"] = page
            if pg.param:
                params[pg.param] = page
        return state

    def next_cursor(self, capability: Capability, body: Any, state: dict[str, Any]) -> str | None:
        pg = capability.pagination
        if pg is None or not state:
            return None
        mode = state.get("mode")
        count = state.get("count", 0)
        limit = state.get("limit", pg.default_limit)
        has_next = bool(dig(body, pg.has_next_field)) if pg.has_next_field else None

        if mode == "token":
            token = dig(body, pg.next_field) if pg.next_field else None
            if token and pg.next_query_param:
                from urllib.parse import parse_qs, urlparse

                values = parse_qs(urlparse(str(token)).query).get(pg.next_query_param)
                token = values[0] if values else None
            return str(token) if token else None
        if mode == "offset":
            if has_next is False:
                return None
            if has_next is None and count < limit:
                return None
            return str(int(state.get("offset", 0)) + count)
        if mode == "page":
            page = int(state.get("page", 1))
            next_page = str(page + 1)
            if has_next is not None:
                return next_page if has_next else None
            total = dig(body, pg.total_pages_field) if pg.total_pages_field else None
            if total is not None:
                return next_page if page < int(total) else None
            return next_page if count == limit else None
        return None

    def normalize(
        self, capability: Capability, raw: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """Project a raw upstream object down to the fields the model needs."""
        missing: list[str] = []
        if capability.fields:
            data: dict[str, Any] = {}
            for target, path in capability.fields.items():
                paths = path if isinstance(path, list) else [path]
                value = None
                for candidate in paths:
                    value = dig(raw, candidate)
                    if value is not None:
                        break
                if value is None:
                    missing.append(target)
                else:
                    data[target] = value
        else:
            data = {k: v for k, v in raw.items() if k not in capability.drop}

        for field_name, sub_key in capability.pluck.items():
            value = data.get(field_name)
            if isinstance(value, list):
                data[field_name] = [
                    item.get(sub_key) if isinstance(item, dict) else item for item in value
                ]
            elif isinstance(value, dict):
                data[field_name] = value.get(sub_key)

        return data, missing

    def build_records(
        self,
        capability: Capability,
        raw_records: list[Any],
        request: PreparedRequest,
    ) -> AdapterResult:
        now = utcnow()
        records: list[Record] = []
        missing: set[str] = set()
        for item in raw_records:
            if not isinstance(item, dict):
                item = {"value": item}
            data, gaps = self.normalize(capability, item)
            missing.update(gaps)

            record_id = None
            if capability.id_template:
                try:
                    record_id = capability.id_template.format(**data)
                except (KeyError, IndexError, ValueError):
                    record_id = None
            elif capability.id_field:
                value = data.get(capability.id_field, dig(item, capability.id_field))
                record_id = str(value) if value is not None else None

            url = None
            if capability.source_url_template:
                # Templates may reference normalized fields or scalar request params
                # (FRED observations do not echo series_id back, for instance).
                namespace = {
                    key: value
                    for key, value in {**request.template_params, **request.params}.items()
                    if isinstance(value, (str, int, float))
                }
                namespace.update(data)
                try:
                    url = capability.source_url_template.format(**namespace)
                except (KeyError, IndexError):
                    url = None

            records.append(
                Record(
                    source_record_id=record_id,
                    source_url=url,
                    retrieved_at=now,
                    data=data,
                )
            )
        request.pagination_state["count"] = len(records)
        return AdapterResult(records=records, missing=sorted(missing))

    def substitute_path(self, path: str, params: dict[str, Any]) -> str:
        """Replace {name} and {name:format_spec} segments, popping them from the query.

        The format spec matters upstream: SEC wants a zero-padded CIK, so manifests
        write ``{cik:0>10}`` and the model can pass "320193".
        """
        out = path
        for match in _PATH_PARAM.finditer(path):
            raw, spec = match.group(1), match.group(2)
            value = params.pop(raw, None)
            if value is None:
                raise ProviderError(
                    f"missing required path parameter '{raw}'",
                    hint=f"this capability needs {sorted({m.group(1) for m in _PATH_PARAM.finditer(path)})}",
                )
            rendered = format(value, spec) if spec else str(value)
            out = out.replace(match.group(0), quote(rendered, safe=""))
        return out

    def render_body(self, template: Any, params: dict[str, Any]) -> Any:
        """Render a JSON body template.

        A string that is exactly ``"{name}"`` is replaced by the raw value, so
        integers stay integers. A dict key or list item that renders to a missing
        placeholder is dropped, which is how optional filters disappear instead
        of being sent as the literal text ``{start_date}``.
        """
        missing = object()

        def walk(node: Any, root: bool = False) -> Any:
            if isinstance(node, dict):
                out = {}
                for key, value in node.items():
                    rendered = walk(value)
                    if rendered is missing:
                        if not root:
                            # A nested object with an unresolved field depends on an
                            # optional param: drop the whole object, not just the key,
                            # so half-built filter clauses never reach the upstream.
                            return missing
                        continue
                    out[key] = rendered
                return out
            if isinstance(node, list):
                return [item for item in (walk(v) for v in node) if item is not missing]
            if isinstance(node, str):
                if (
                    node.startswith("{")
                    and node.endswith("}")
                    and node.count("{") == 1
                    and node.count("}") == 1
                ):
                    key = node[1:-1]
                    return params.get(key, missing)
                if "{" in node:
                    try:
                        return node.format(**params)
                    except (KeyError, IndexError):
                        return node
                return node
            return node

        return walk(template, root=True)


def extract_records(
    body: Any,
    record_path: str | None,
    map_exclude: list[str] | None = None,
    transpose: bool = False,
) -> list[Any]:
    if record_path:
        found = dig(body, record_path)
        if found is None:
            return []
        if isinstance(found, list):
            return found
        if isinstance(found, dict):
            if transpose:
                return _transpose(found)
            # E-utilities and friends key records by id and mix in metadata keys.
            excluded = set(map_exclude or ())
            return [value for key, value in found.items() if key not in excluded]
        return [found]
    if isinstance(body, list):
        return body
    return [body]


def _transpose(columns: dict[str, Any]) -> list[dict[str, Any]]:
    """{a: [1, 2], b: [3, 4]} -> [{a: 1, b: 3}, {a: 2, b: 4}] (SEC filings)."""
    keys = list(columns)
    series = [columns[key] for key in keys]
    if not series or not all(isinstance(values, list) for values in series):
        return [columns]
    length = min(len(values) for values in series)
    return [dict(zip(keys, (values[index] for values in series))) for index in range(length)]
