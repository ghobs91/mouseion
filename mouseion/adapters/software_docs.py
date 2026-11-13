"""Official software documentation as a source.

A docs site originates its own text, which makes it the right answer for the most
common question a coding agent asks: what does this API actually do. The shape is
different from every other provider here. The record is not a row, it is a page,
and the payload is HTML that has to become text a model can read without guessing
where the article ends and the navigation begins.

Two surfaces, one adapter:

* ``search`` returns rows like any other provider — title, summary, canonical path.
* a page read returns one record — title, URL, section headings and the article
  body as Markdown, because a model cites a heading, not a character offset.

A page arrives either as a structured block list (MDN publishes ``/index.json``
with ``doc.body``: prose blocks carrying HTML, plus compatibility tables that are
skipped) or as a plain HTML document. ``docs_blocks`` selects the first; anything
else is read as HTML, narrowed by ``docs_selector`` when a site's chrome survives
the skip list.
"""

from __future__ import annotations

import html as html_module
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urlparse

from ..envelope import ProviderError, dig
from ..manifest import Capability, Manifest
from .base import AdapterResult, PreparedRequest, extract_records
from .rest_json import RestJsonAdapter

_PLACEHOLDER = re.compile(r"{(\w+)(?::([^}]+))?}")
_TAGS = re.compile(r"<[^>]+>")
_LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")

#: Elements whose class marks them as chrome rather than prose. MDN puts the
#: language label of every code sample in a sibling ``.example-header``; the same
#: language is in the ``<pre class="brush: js">`` we already render as a fence.
_SKIP_CLASSES = frozenset({"example-header", "sidebar", "toc", "breadcrumbs"})

#: Default character budget for one page. Matches the fetch_url escape hatch: a
#: model that needs more can page the source or read a section by anchor.
DEFAULT_MAX_CHARS = 20_000


def _inline(text: str) -> str:
    """Strip tags and entities from a heading or title."""
    return " ".join(html_module.unescape(_TAGS.sub("", text)).split())


def _normalise_path(value: str) -> str:
    """Accept a URL, a site path or a bare slug; return the slug.

    Operators paste whatever the browser shows, so a caller should not have to
    know that this provider wants ``Web/API/Fetch_API`` and not the URL it came
    from. Case is preserved: MDN answers a lowercased path with a 301, so the
    canonical casing from ``search`` is the cheaper request.
    """
    raw = value.strip()
    if not raw:
        raise ProviderError("docs path is empty")
    if "://" in raw:
        raw = urlparse(raw).path
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    segments = [segment for segment in raw.split("/") if segment]
    if segments and _LOCALE.match(segments[0]):
        segments = segments[1:]
    if segments and segments[0].lower() == "docs":
        segments = segments[1:]
    if segments and segments[-1].lower() == "index.json":
        segments = segments[:-1]
    if not segments:
        raise ProviderError(f"'{value}' does not name a docs page")
    if any(segment in {".", ".."} for segment in segments):
        raise ProviderError("docs path may not contain '.' or '..' segments")
    return "/".join(segments)


def _quote_path(value: str) -> str:
    """Escape a path param per segment, so a slug keeps its slashes."""
    return "/".join(quote(segment, safe="") for segment in value.split("/") if segment)


def _language(attrs: list[tuple[str, str | None]]) -> str:
    """Read a code fence language from the ``pre`` class a docs site publishes."""
    for name, value in attrs:
        if name != "class" or not value:
            continue
        tokens = value.split()
        for index, token in enumerate(tokens):
            if token == "brush:" and index + 1 < len(tokens):
                return tokens[index + 1]
            if token.startswith("language-"):
                return token[len("language-"):]
            if token.startswith("highlight-source-"):
                return token[len("highlight-source-"):]
    return ""


#: Text nodes add a separator so adjacent inline elements do not run together;
#: that separator has to go back before punctuation it did not belong to.
_TIDY_BEFORE = re.compile(r" +([.,;:!?%)\]}])")
_TIDY_SPACE = re.compile(r"[ \t]{2,}")


class _Markdown(HTMLParser):
    """HTML → Markdown-ish text. Not a renderer; just enough to feed a model.

    Headings keep their level, list items keep their marker, and preformatted
    blocks keep their whitespace, because those are the parts a model copies.
    Links lose their href: a docs answer is read, and a page of relative URLs is
    noise next to the prose.
    """

    _SKIP = frozenset(
        {
            "script",
            "style",
            "noscript",
            "template",
            "svg",
            "nav",
            "aside",
            "footer",
            "form",
            "button",
            "iframe",
            "dialog",
        }
    )
    _BLOCK = frozenset(
        {
            "p",
            "div",
            "section",
            "article",
            "main",
            "header",
            "blockquote",
            "figure",
            "figcaption",
            "details",
            "summary",
            "dl",
            "dd",
            "dt",
        }
    )
    _HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self, selector: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.selector = selector
        #: Depth counter while hunting for the selected element, then while inside it.
        self._depth = 0
        self._selected = 0
        #: Stack of open skipped elements, so their text and children stay out.
        self._skipped: list[str] = []
        self._pre = 0
        self._parts: list[str] = []

    # -- selection ------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skipped or self._skip(tag, attrs):
            self._skipped.append(tag)
            return
        if self.selector:
            if self._selected:
                self._selected += 1
            elif self._matches(tag, attrs):
                self._selected = 1
            elif self._depth:
                self._depth += 1
            else:
                # A wrapper may hold the target further in, so keep descending.
                self._depth = 1
            if not self._selected:
                return
        self._open(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self._skipped:
            if not self._skipped or tag == self._skipped[-1]:
                self._skipped.pop()
            return
        if self.selector:
            if self._selected:
                self._selected -= 1
                if not self._selected:
                    return
            elif self._depth:
                self._depth -= 1
                return
            else:
                return
        self._close(tag)

    def _skip(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in self._SKIP:
            return True
        classes = set()
        for name, value in attrs:
            if name == "class" and value:
                classes.update(value.split())
        return bool(classes & _SKIP_CLASSES)

    def _matches(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        selector = self.selector or ""
        values = {name: (value or "") for name, value in attrs}
        if selector.startswith("#"):
            return values.get("id") == selector[1:]
        if selector.startswith("."):
            return selector[1:] in values.get("class", "").split()
        if "." in selector:
            wanted_tag, wanted_class = selector.split(".", 1)
            return tag == wanted_tag and wanted_class in values.get("class", "").split()
        if "#" in selector:
            wanted_tag, wanted_id = selector.split("#", 1)
            return tag == wanted_tag and values.get("id") == wanted_id
        return tag == selector

    # -- rendering ------------------------------------------------------

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "pre":
            self._pre += 1
            self._parts.append(f"\n\n```{_language(attrs)}\n")
        elif tag in self._HEADINGS:
            self._parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in self._BLOCK:
            self._parts.append("\n\n")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag == "br":
            self._parts.append("\n")
        elif tag == "code" and not self._pre:
            self._parts.append("`")
        elif tag in {"td", "th"}:
            self._parts.append(" | ")
        elif tag == "tr":
            self._parts.append("\n")

    def _close(self, tag: str) -> None:
        if tag == "pre":
            self._pre = max(0, self._pre - 1)
            # The source ends its code with a newline; a second one would open a
            # blank line inside the fence.
            while self._parts and not self._parts[-1].strip("\n"):
                if self._parts[-1].strip():
                    break
                self._parts.pop()
            if self._parts:
                self._parts[-1] = self._parts[-1].rstrip("\n")
            self._parts.append("\n```\n\n")
        elif tag in self._BLOCK or tag in self._HEADINGS or tag in {"li", "tr"}:
            self._parts.append("\n")
        elif tag == "code" and not self._pre:
            # Inline code: drop the separator space the text node introduced, so
            # `json()` does not render as `json() `.
            if self._parts:
                self._parts[-1] = self._parts[-1].rstrip()
            self._parts.append("` ")

    def handle_data(self, data: str) -> None:
        if self._skipped or (self.selector and not self._selected):
            return
        if self._pre:
            self._parts.append(data)
        else:
            self._parts.append(" ".join(data.split()) + " " if data.strip() else " ")

    def text(self) -> str:
        out: list[str] = []
        blanks = 0
        in_fence = False
        for line in "".join(self._parts).splitlines():
            if line.strip().startswith("```"):
                in_fence = not in_fence
                out.append(line.rstrip())
                blanks = 0
                continue
            if in_fence:
                # Code keeps its indentation; tidy rules apply to prose only.
                out.append(line)
                continue
            line = _TIDY_BEFORE.sub(r"\1", _TIDY_SPACE.sub(" ", line)).rstrip()
            if line.strip():
                blanks = 0
            else:
                blanks += 1
                if blanks > 1:
                    continue
            out.append(line)
        return "\n".join(out).strip()


def html_to_markdown(markup: str, selector: str | None = None) -> str:
    text = _render(markup, selector)
    if selector and not text:
        # The selector missed. Read the whole document rather than nothing.
        text = _render(markup, None)
    return text


def _render(markup: str, selector: str | None) -> str:
    parser = _Markdown(selector)
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - docs sites emit malformed markup
        pass
    return parser.text()


def blocks_to_markdown(blocks: Any) -> str:
    """Render a structured block list (MDN's ``doc.body``) as Markdown.

    Only blocks carrying an HTML ``content`` string are rendered. Compatibility
    tables and specification lists carry data instead of prose, and pasting their
    JSON into an answer would bury the text a model should quote.
    """
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for block in blocks:
        value = block.get("value") if isinstance(block, dict) else None
        if not isinstance(value, dict):
            continue
        content = value.get("content")
        if not isinstance(content, str):
            continue
        title = _inline(str(value.get("title") or ""))
        if title:
            parts.append(("### " if value.get("isH3") else "## ") + title)
        rendered = html_to_markdown(content)
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts).strip()


class SoftwareDocsAdapter(RestJsonAdapter):
    name = "software_docs"

    def prepare(
        self,
        manifest: Manifest,
        capability: Capability,
        params: dict[str, Any],
        cursor: str | None,
    ) -> PreparedRequest:
        cleaned = dict(params)
        for match in _PLACEHOLDER.finditer(capability.path):
            name = match.group(1)
            value = cleaned.get(name)
            if isinstance(value, str) and value.strip():
                cleaned[name] = _normalise_path(value)
        return super().prepare(manifest, capability, cleaned, cursor)

    def substitute_path(self, path: str, params: dict[str, Any]) -> str:
        """Like the base version, but a docs slug keeps its slashes."""
        out = path
        for match in _PLACEHOLDER.finditer(path):
            raw, spec = match.group(1), match.group(2)
            value = params.pop(raw, None)
            if value is None:
                raise ProviderError(
                    f"missing required path parameter '{raw}'",
                    hint="this capability needs the docs path, for example Web/API/Fetch_API",
                )
            rendered = format(value, spec) if spec else str(value)
            out = out.replace(match.group(0), _quote_path(rendered))
        return out

    def parse(
        self,
        manifest: Manifest,
        capability: Capability,
        body: Any,
        request: PreparedRequest,
    ) -> AdapterResult:
        if capability.docs_blocks:
            return self._page(capability, self._blocks_record(capability, body), request)

        markup = body.get("_raw_text") if isinstance(body, dict) else body
        if isinstance(markup, str) and "<" in markup:
            return self._page(
                capability, self._html_record(capability, markup, request), request
            )

        return self._rows(capability, body, request)

    # -- page reads -----------------------------------------------------

    def _blocks_record(
        self, capability: Capability, body: Any
    ) -> dict[str, Any]:
        blocks = dig(body, capability.docs_blocks or "")
        if not isinstance(blocks, list):
            raise ProviderError(
                "the docs payload has no structured body",
                hint=f"expected a block list at '{capability.docs_blocks}'",
            )
        doc = dig(body, "doc") or {}
        path = str(doc.get("mdn_url") or "")
        return {
            "title": _inline(str(doc.get("title") or "")),
            "summary": _inline(str(doc.get("summary") or "")),
            "slug": _normalise_path(path) if path else "",
            "path": path,
            "locale": doc.get("locale"),
            "updated": doc.get("modified"),
            "headings": [
                _inline(str(entry.get("text") or ""))
                for entry in (doc.get("toc") or [])
                if isinstance(entry, dict)
            ],
            "text": blocks_to_markdown(blocks),
        }

    def _html_record(
        self, capability: Capability, markup: str, request: PreparedRequest
    ) -> dict[str, Any]:
        title = ""
        heading = re.search(r"<h1[^>]*>(.*?)</h1>", markup, re.S | re.I)
        if heading and _inline(heading.group(1)):
            title = _inline(heading.group(1))
        else:
            match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.S | re.I)
            if match:
                title = _inline(match.group(1))
        path = next(
            (
                request.template_params.get(match.group(1))
                for match in _PLACEHOLDER.finditer(capability.path)
                if isinstance(request.template_params.get(match.group(1)), str)
            ),
            "",
        )
        return {
            "title": title,
            "summary": "",
            "slug": path,
            "locale": None,
            "updated": None,
            "headings": [],
            "text": html_to_markdown(markup, capability.docs_selector),
        }

    def _page(
        self, capability: Capability, record: dict[str, Any], request: PreparedRequest
    ) -> AdapterResult:
        budget = max(1, capability.docs_max_chars)
        text = str(record.get("text") or "")
        truncated = len(text) > budget
        if truncated:
            text = text[:budget].rstrip() + "\n\n[truncated]"
        record["text"] = text
        record["truncated"] = truncated
        record["chars"] = len(text)

        result = self.build_records(capability, [record], request)
        result.upstream_version = record.get("updated")
        return result

    # -- search rows ----------------------------------------------------

    def _rows(
        self, capability: Capability, body: Any, request: PreparedRequest
    ) -> AdapterResult:
        raw = extract_records(
            body, capability.record_path, capability.map_exclude, capability.transpose
        )
        limit = request.template_params.get("limit")
        if limit is not None:
            try:
                raw = raw[: max(1, int(limit))]
            except (TypeError, ValueError):
                pass
        result = self.build_records(capability, raw, request)
        self.paginate_local(capability, result, request, body)

        if capability.terms_field:
            result.terms = dig(body, capability.terms_field)
        if capability.version_field:
            version = dig(body, capability.version_field)
            result.upstream_version = str(version) if version is not None else None
        return result
