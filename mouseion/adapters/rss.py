"""RSS 2.0 adapter.

Agency newsrooms publish RSS 2.0, not Atom: ``<rss><channel><item>`` with a
``pubDate`` string and a ``guid``. The adapter reuses the REST adapter's request
preparation and record projection, and only changes how the body is turned into
raw records.

``query`` is a local keyword filter. A feed has no server-side search, so the
window is fetched once (``local_window``), matched against title and summary,
then paged locally.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from ..config import Settings
from ..envelope import ProviderError
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest
from .rest_json import RestJsonAdapter

DC = "{http://purl.org/dc/elements/1.1/}"


def _text(node: ET.Element, path: str, default: str = "") -> str:
    """All text inside ``path``, including descendants.

    ``findtext`` only returns the text before the first child element, which loses
    the title in feeds that wrap it in an anchor tag (Caltrain).
    """
    element = node.find(path)
    if element is None:
        return default
    return " ".join("".join(element.itertext()).split())


class RssAdapter(RestJsonAdapter):
    name = "rss"

    def headers(
        self, manifest: Manifest, config: Settings | None = None
    ) -> dict[str, str]:
        headers = super().headers(manifest, config)
        headers["Accept"] = "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
        return headers

    def prepare(
        self,
        manifest: Manifest,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> PreparedRequest:
        prepared = super().prepare(manifest, capability, params, cursor)
        # `query` filters the fetched window locally; it is not a feed parameter.
        prepared.params.pop("query", None)
        return prepared

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        text = body.get("_raw_text") if isinstance(body, dict) else None
        if not text:
            raise ProviderError("expected an RSS XML body")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise ProviderError(f"invalid RSS XML: {exc}") from None

        channel = root.find("channel")
        if channel is None:
            raise ProviderError("expected an RSS <channel>")
        if root.tag != "rss":
            raise ProviderError("expected an RSS 2.0 document, not Atom or RDF")

        raw: list[dict[str, Any]] = []
        for item in channel.findall("item"):
            link = _text(item, "link")
            raw.append(
                {
                    "id": _text(item, "guid") or link,
                    "title": _text(item, "title"),
                    "link": link,
                    "published": _text(item, "pubDate"),
                    "summary": _text(item, "description"),
                    "author": _text(item, "author") or _text(item, f"{DC}creator"),
                    "categories": [
                        category.text.strip()
                        for category in item.findall("category")
                        if (category.text or "").strip()
                    ],
                }
            )

        query = str(request.template_params.get("query") or "").strip().lower()
        if query:
            raw = [
                row
                for row in raw
                if query in row["title"].lower() or query in row["summary"].lower()
            ]

        result = self.build_records(capability, raw, request)
        self.paginate_local(capability, result, request, body)
        return result
