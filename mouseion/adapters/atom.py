"""Atom feed adapter.

arXiv is the motivating upstream: its API is Atom XML, not JSON. The adapter
reuses the REST adapter's request preparation and record projection, and only
changes how the body is turned into raw records.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..config import Settings
from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"


def _text(node: ET.Element, path: str, default: str = "") -> str:
    value = node.findtext(path, default=default) or ""
    return " ".join(value.split())


class AtomAdapter(RestJsonAdapter):
    name = "atom"

    def headers(
        self, manifest: Manifest, config: Settings | None = None
    ) -> dict[str, str]:
        headers = super().headers(manifest, config)
        headers["Accept"] = "application/atom+xml, application/xml;q=0.9, */*;q=0.8"
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
            raise ProviderError("expected an Atom XML body")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ProviderError(f"invalid Atom XML: {exc}") from None

        raw: list[dict[str, Any]] = []
        for entry in root.findall(f"{ATOM}entry"):
            links = entry.findall(f"{ATOM}link")
            alternate = next(
                (link.get("href") for link in links if link.get("rel") == "alternate"),
                None,
            )
            pdf = next(
                (link.get("href") for link in links if link.get("title") == "pdf"),
                None,
            )
            primary = entry.find(f"{ARXIV}primary_category")
            raw.append(
                {
                    "id": _text(entry, f"{ATOM}id"),
                    "title": _text(entry, f"{ATOM}title"),
                    "summary": _text(entry, f"{ATOM}summary"),
                    "published": _text(entry, f"{ATOM}published"),
                    "updated": _text(entry, f"{ATOM}updated"),
                    "authors": [
                        _text(author, f"{ATOM}name")
                        for author in entry.findall(f"{ATOM}author")
                    ],
                    "categories": [
                        category.get("term", "")
                        for category in entry.findall(f"{ATOM}category")
                    ],
                    "primary_category": primary.get("term", "") if primary is not None else "",
                    "comment": _text(entry, f"{ARXIV}comment"),
                    "journal_ref": _text(entry, f"{ARXIV}journal_ref"),
                    "doi": _text(entry, f"{ARXIV}doi"),
                    "url": alternate or "",
                    "pdf": pdf or "",
                }
            )

        result = self.build_records(capability, raw, request)
        total = root.findtext(f"{OPENSEARCH}totalResults")
        if total:
            result.upstream_version = f"total={total}"
        result.next_cursor = self.next_cursor(capability, {}, request.pagination_state)
        return result
