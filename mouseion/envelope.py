"""The response envelope every provider adapter must produce.

The envelope is the whole trick: 128 heterogeneous upstreams become
interchangeable to the model because they all return this same shape.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dig(obj: Any, path: str | None, default: Any = None) -> Any:
    """Resolve a dotted path against nested dicts and lists.

    ``dig({"a": {"b": [{"c": 1}]}}, "a.b.0.c") -> 1``

    ``$`` addresses the root object, for upstreams whose body is already the
    keyed record map (Firebase Realtime Database, the DFL match feed).
    """
    if not path or path == "$":
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return cur


class SourceStatus(BaseModel):
    """Where the bytes came from, and how stale they are."""

    url: str
    http: int
    fetched_at: datetime
    cache_age_s: float = 0.0
    upstream_version: str | None = None


class Cursor(BaseModel):
    """Pagination token, pinned to the snapshot the first page was served from."""

    snapshot: str
    next: str | None = None


class Record(BaseModel):
    """One normalized row. ``source_url`` is what makes the answer citable."""

    source_record_id: str | None = None
    source_url: str | None = None
    retrieved_at: datetime
    data: dict[str, Any]


class Envelope(BaseModel):
    provider: str
    capability: str
    source_status: SourceStatus
    attribution: str | None = None
    terms: str | None = None
    cursor: Cursor | None = None
    records: list[Record] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ProviderError(Exception):
    """Upstream or manifest failure, carrying enough context for the model to self-correct."""

    def __init__(self, message: str, *, hint: str | None = None, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.status:
            out["http"] = self.status
        return out


def hint_for_status(status: int) -> str | None:
    return {
        401: "Upstream rejected the credentials. Check the API key env var named in the manifest.",
        403: "Missing scope, blocked user agent, or licence-gated content.",
        404: "Endpoint or record id not found. Re-check the id format in provider_describe.",
        429: "Upstream rate limit hit. Retry after a pause, or reduce 'limit'.",
        500: "Upstream server error. This is not a parameter problem; retry later.",
    }.get(status)
