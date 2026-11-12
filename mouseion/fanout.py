"""Fan-out over named primary providers.

A fanout provider is a view, not a source: every record still comes from one of
the member catalogues, and every record names the member it came from. The merge
is intentionally conservative:

* records stay in member order unless the caller asks for a price sort;
* a failed member does not fail the call, but it is listed in ``missing`` and in
  ``source_status.upstream_version``;
* pagination is a JSON map of member cursors, so each member advances exactly as
  far as it was read. No page is skipped and no record is silently dropped.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from .envelope import Cursor, Envelope, ProviderError, Record, SourceStatus, utcnow
from .manifest import Capability, Manifest

if TYPE_CHECKING:  # pragma: no cover - import cycle only for type checking
    from .client import ProviderClient

_NUMERIC_FIELDS = ("price", "list_price", "rating", "reviews", "shipping_cost")


async def call_fanout(
    client: ProviderClient,
    manifest: Manifest,
    capability: str,
    params: dict[str, Any],
    cursor: str | None,
    *,
    refresh: bool = False,
) -> Envelope:
    name = capability.strip().lower().replace("-", "_")
    cap = manifest.capabilities.get(name)
    if cap is None:
        raise ProviderError(
            f"provider '{manifest.id}' has no capability '{capability}'",
            hint=f"available: {sorted(manifest.capabilities)}",
        )

    query, limit, members, order = _resolve_inputs(manifest, cap, params)
    cursor_map = _parse_cursor(cursor)

    async def fetch(member: str) -> tuple[str, Envelope | None, Exception | None]:
        try:
            member_manifest = client.registry.get(member)
            if member_manifest.transport == "fanout":
                raise ProviderError(
                    f"fanout member '{member}' is itself a fanout provider",
                    hint="point fanout members at providers with a real transport",
                )
            envelope = await client.call(
                member,
                manifest.member_capability,
                {"query": query, "limit": limit},
                cursor_map.get(member),
                refresh=refresh,
            )
            return member, envelope, None
        except Exception as exc:  # noqa: BLE001 - partial failure is a result
            return member, None, exc

    results = await asyncio.gather(*(fetch(member) for member in members))

    per_member: dict[str, list[Record]] = {}
    summary: list[str] = []
    failures: list[str] = []
    missing: list[str] = []
    next_cursors: dict[str, str] = {}
    succeeded = 0

    for member, envelope, error in results:
        if error is not None or envelope is None:
            message = getattr(error, "message", None) or f"{type(error).__name__}: {error}"
            failures.append(f"{member}: {message}")
            missing.append(f"{member}: {message}")
            summary.append(f"{member}=error")
            continue

        succeeded += 1
        summary.append(f"{member}={len(envelope.records)}")
        if envelope.cursor and envelope.cursor.next:
            next_cursors[member] = envelope.cursor.next
        missing.extend(f"{member}.{field}" for field in envelope.missing)

        member_manifest = client.registry.get(member)
        retailer = member_manifest.attribution or member_manifest.title
        rows: list[Record] = []
        for record in envelope.records:
            data = dict(record.data)
            data["retailer"] = retailer
            data["retailer_provider"] = member
            for field in _NUMERIC_FIELDS:
                if field not in data:
                    continue
                value = _number(data[field])
                if value is None:
                    data.pop(field)
                else:
                    data[field] = value
            data.setdefault("currency", "USD")
            rows.append(
                Record(
                    source_record_id=(
                        f"{member}:{record.source_record_id}"
                        if record.source_record_id
                        else None
                    ),
                    source_url=record.source_url,
                    retrieved_at=record.retrieved_at,
                    data=data,
                )
            )
        per_member[member] = rows

    if not succeeded:
        raise ProviderError(
            "all retailers failed",
            hint="; ".join(failures)[:500],
        )

    if order == "price":
        records = sorted(
            (record for rows in per_member.values() for record in rows),
            key=_price_key,
        )
    else:
        records = _interleave(members, per_member)

    now = utcnow()
    next_cursor = (
        json.dumps(next_cursors, sort_keys=True) if next_cursors else None
    )
    return Envelope(
        provider=manifest.id,
        capability=name,
        source_status=SourceStatus(
            url=f"fanout://{manifest.id}",
            http=200,
            fetched_at=now,
            upstream_version=", ".join(summary)[:300],
        ),
        attribution=manifest.attribution,
        cursor=(
            Cursor(snapshot=now.isoformat(timespec="seconds"), next=next_cursor)
            if next_cursor
            else None
        ),
        records=records,
        missing=missing,
        truncated=next_cursor is not None,
    )


def _resolve_inputs(
    manifest: Manifest, cap: Capability, params: dict[str, Any]
) -> tuple[str, int, list[str], str]:
    try:
        resolved = cap.coerce(params)
    except ValueError as exc:
        raise ProviderError(str(exc), hint=f"valid params: {sorted(cap.params)}") from None

    missing = [
        name
        for name in cap.required_params()
        if resolved.get(name) in (None, "", [])
    ]
    if missing:
        raise ProviderError(
            f"missing required param(s): {', '.join(missing)}",
            hint=f"required: {cap.required_params()}; see provider_describe",
        )

    query = str(resolved["query"]).strip()
    if not query:
        raise ProviderError("'query' must not be empty")

    raw_limit = resolved.get("limit")
    limit = 5 if raw_limit is None else int(raw_limit)
    limit = max(1, min(limit, 25))

    members = _select_members(manifest, resolved.get("retailers"))

    sort_spec = cap.params.get("sort")
    allowed = list(sort_spec.enum) if sort_spec and sort_spec.enum else ["relevance", "price"]
    order = str(resolved.get("sort") or "relevance")
    if order not in allowed:
        raise ProviderError(
            f"unknown sort '{order}'",
            hint=f"available: {allowed}",
        )
    return query, limit, members, order


def _parse_cursor(cursor: str | None) -> dict[str, str]:
    if not cursor:
        return {}
    try:
        value = json.loads(cursor)
    except ValueError:
        raise ProviderError(
            "cursor must be the JSON object returned in cursor.next"
        ) from None
    if not isinstance(value, dict):
        raise ProviderError("cursor must be a JSON object of member cursors")
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _select_members(manifest: Manifest, requested: Any) -> list[str]:
    if not requested:
        return list(manifest.members)
    values = requested if isinstance(requested, list) else [requested]

    aliases: dict[str, str] = {}
    for member in manifest.members:
        aliases[member.lower()] = member
        aliases[member.split("_")[0].lower()] = member
        aliases[member.replace("_", "").lower()] = member

    selected: list[str] = []
    for value in values:
        key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        member = aliases.get(key) or aliases.get(key.replace("_", ""))
        if member is None:
            raise ProviderError(
                f"unknown retailer '{value}'",
                hint=f"available: {sorted(set(aliases.values()))}",
            )
        if member not in selected:
            selected.append(member)
    return selected or list(manifest.members)


def _interleave(order: list[str], per_member: dict[str, list[Record]]) -> list[Record]:
    out: list[Record] = []
    index = 0
    while True:
        added = False
        for member in order:
            rows = per_member.get(member, [])
            if index < len(rows):
                out.append(rows[index])
                added = True
        if not added:
            return out
        index += 1


def _price_key(record: Record) -> tuple[bool, float, str, str]:
    price = record.data.get("price")
    return (
        price is None,
        float(price) if isinstance(price, (int, float)) else 0.0,
        str(record.data.get("retailer") or ""),
        str(record.data.get("title") or "").lower(),
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None
