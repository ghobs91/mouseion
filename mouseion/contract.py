"""Contract checks: does this manifest still describe the bytes the upstream returns?

Deliberately not value assertions. Upstream data changes every day; field renames,
shape changes and pagination breaks are what actually rot an index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .envelope import Envelope
from .manifest import Capability

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Violation:
    kind: str
    detail: str
    severity: str = ERROR

    def __str__(self) -> str:
        return f"[{self.severity}] {self.kind}: {self.detail}"


def check_envelope(
    capability: Capability,
    envelope: Envelope,
    *,
    expected_records: int | None = None,
) -> list[Violation]:
    out: list[Violation] = []

    if envelope.source_status.http >= 400:
        out.append(Violation("http", f"upstream answered {envelope.source_status.http}", ERROR))

    count = len(envelope.records)
    if expected_records is not None and count != expected_records:
        out.append(
            Violation(
                "record_count",
                f"expected {expected_records} records from this body, parsed {count}",
                ERROR,
            )
        )

    for record in envelope.records:
        if capability.id_field and not record.source_record_id:
            out.append(
                Violation(
                    "missing_id",
                    f"id_field {capability.id_field!r} did not resolve on a record",
                    ERROR,
                )
            )
            break
        if capability.source_url_template and not record.source_url:
            out.append(
                Violation(
                    "missing_url",
                    f"source_url_template {capability.source_url_template!r} did not resolve",
                    ERROR,
                )
            )
            break

    # A projected field that vanished upstream is a schema change, but plenty are
    # legitimately optional (DOIs, abstracts, close dates). Warn, do not fail.
    if capability.fields:
        present: set[str] = set()
        for record in envelope.records:
            present.update(record.data)
        for field in capability.fields:
            if field not in present:
                out.append(
                    Violation(
                        "missing_field",
                        f"projected field {field!r} absent from every record",
                        WARNING,
                    )
                )

    if envelope.truncated and (envelope.cursor is None or envelope.cursor.next is None):
        out.append(Violation("cursor", "truncated without a usable cursor.next", ERROR))

    return out


def check_body(capability: Capability, body: Any, record_path_resolves: bool) -> list[Violation]:
    """Shape-only checks against a raw body, before parsing."""
    out: list[Violation] = []
    if capability.record_path and not record_path_resolves:
        out.append(
            Violation(
                "record_path",
                f"record_path {capability.record_path!r} does not resolve in the response",
                ERROR,
            )
        )
    return out
