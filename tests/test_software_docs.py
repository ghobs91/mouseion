from __future__ import annotations

import json
from pathlib import Path

import pytest

from mouseion.adapters import get_adapter
from mouseion.adapters.software_docs import (
    SoftwareDocsAdapter,
    blocks_to_markdown,
    html_to_markdown,
)
from mouseion.envelope import ProviderError
from mouseion.manifest import Capability

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _prepare(manifest, capability, params=None, cursor=None):
    return get_adapter(manifest.adapter).prepare(
        manifest, capability, params or {}, cursor
    )


# -- search -------------------------------------------------------------


def test_mdn_search_asks_for_a_page_of_results(registry):
    manifest = registry.get("mdn")
    cap = manifest.capabilities["search"]

    request = _prepare(manifest, cap, {"query": "fetch response json", "limit": 3})

    assert request.url == "https://developer.mozilla.org/api/v1/search"
    assert request.params["q"] == "fetch response json"
    assert request.params["locale"] == "en-US"
    # Page mode starts at 1, not 0.
    assert request.params["page"] == 1

    second = _prepare(
        manifest, cap, {"query": "fetch response json", "limit": 3}, cursor="2"
    )
    assert second.params["page"] == 2


def test_mdn_search_trims_to_the_callers_limit(registry):
    manifest = registry.get("mdn")
    cap = manifest.capabilities["search"]
    body = _fixture("mdn__search.json")["body"]

    request = _prepare(manifest, cap, {"query": "fetch response json", "limit": 2})
    result = get_adapter(manifest.adapter).parse(manifest, cap, body, request)

    assert len(result.records) == 2
    first = result.records[0]
    assert first.source_record_id == "/en-US/docs/Web/API/Response/json"
    assert first.source_url == "https://developer.mozilla.org/en-US/docs/Web/API/Response/json"
    assert first.data["slug"] == "web/api/response/json"
    assert first.data["title"]
    # The upstream summary runs over several lines; a record field should not.
    assert "\n" not in first.data["summary"]
    assert result.upstream_version


# -- page ---------------------------------------------------------------


def test_mdn_page_accepts_a_slug_or_the_page_url(registry):
    manifest = registry.get("mdn")
    cap = manifest.capabilities["page"]

    from_slug = _prepare(manifest, cap, {"path": "Web/API/Response/json"})
    from_url = _prepare(
        manifest,
        cap,
        {"path": "https://developer.mozilla.org/en-US/docs/Web/API/Response/json"},
    )
    from_index = _prepare(manifest, cap, {"path": "/en-US/docs/Web/API/Response/json/index.json"})

    assert from_slug.url == "https://developer.mozilla.org/en-US/docs/Web/API/Response/json/index.json"
    assert from_url.url == from_slug.url
    assert from_index.url == from_slug.url


def test_mdn_page_rejects_a_traversing_path(registry):
    manifest = registry.get("mdn")
    cap = manifest.capabilities["page"]

    with pytest.raises(ProviderError):
        _prepare(manifest, cap, {"path": "Web/../../etc/passwd"})


def test_mdn_page_renders_the_prose_blocks(registry):
    manifest = registry.get("mdn")
    cap = manifest.capabilities["page"]
    body = _fixture("mdn__page.json")["body"]

    request = _prepare(manifest, cap, {"path": "Web/API/Response/json"})
    result = get_adapter(manifest.adapter).parse(manifest, cap, body, request)

    assert len(result.records) == 1
    record = result.records[0]
    assert record.source_record_id == "/en-US/docs/Web/API/Response/json"
    assert record.source_url == "https://developer.mozilla.org/en-US/docs/Web/API/Response/json"
    assert record.data["title"] == "Response: json() method"
    assert "Syntax" in record.data["headings"]
    assert record.data["truncated"] is False

    text = record.data["text"]
    assert text.startswith("Note:")
    assert "## Syntax" in text
    assert "```js" in text
    # Compatibility and specification tables are data, not prose: they stay out.
    assert "Chrome" not in text
    assert "ECMAScript" not in text


def test_mdn_page_respects_the_character_budget(registry):
    manifest = registry.get("mdn")
    cap = manifest.capabilities["page"].model_copy(update={"docs_max_chars": 200})
    body = _fixture("mdn__page.json")["body"]

    request = _prepare(manifest, cap, {"path": "Web/API/Response/json"})
    result = get_adapter(manifest.adapter).parse(manifest, cap, body, request)

    data = result.records[0].data
    assert data["truncated"] is True
    assert data["text"].endswith("[truncated]")
    assert data["chars"] <= 220


# -- the HTML shape other docs sites publish ----------------------------

HTML = """
<html><head><title>Promise.all() - JavaScript | Docs</title></head><body>
<nav><a href="/">Home</a><a href="/javascript">JavaScript</a></nav>
<main>
  <h1>Promise.all()</h1>
  <p>The <code>Promise.all()</code> method returns a single promise.</p>
  <h2>Syntax</h2>
  <pre class="language-js"><code>Promise.all(iterable)
  .then((values) =&gt; values)
</code></pre>
  <nav class="toc"><a href="#syntax">Syntax</a></nav>
  <script>window.track = 1;</script>
</main>
<footer>Copyright</footer>
</body></html>
"""


def _page_capability(**overrides) -> Capability:
    spec = {
        "description": "Read one page.",
        "path": "/docs/{path}",
        "params": {"path": {"type": "string", "required": True}},
        "fields": {
            "title": "title",
            "text": "text",
            "truncated": "truncated",
            "chars": "chars",
        },
        "source_url_template": "https://docs.example/docs/{slug}",
    }
    spec.update(overrides)
    return Capability.model_validate(spec)


def _parse_html(manifests, capability, markup: str, params: dict):
    adapter = SoftwareDocsAdapter()
    request = adapter.prepare(manifests, capability, params, None)
    return adapter.parse(manifests, capability, {"_raw_text": markup}, request)


def test_html_page_is_turned_into_markdown(registry):
    manifest = registry.get("mdn")
    cap = _page_capability()

    result = _parse_html(manifest, cap, HTML, {"path": "javascript/promise_all"})

    data = result.records[0].data
    assert data["title"] == "Promise.all()"
    assert "# Promise.all()" in data["text"]
    assert "## Syntax" in data["text"]
    # Chrome is dropped: site navigation, the in-page toc, the footer, scripts.
    assert "Home" not in data["text"]
    assert "Syntax</a>" not in data["text"]
    assert "Copyright" not in data["text"]
    assert "window.track" not in data["text"]
    # The fence keeps its language and the code keeps its indentation.
    assert "```js" in data["text"]
    assert "  .then((values) => values)" in data["text"]


def test_html_selector_narrows_the_page(registry):
    manifest = registry.get("mdn")
    cap = _page_capability(docs_selector="main")

    result = _parse_html(manifest, cap, HTML, {"path": "javascript/promise_all"})

    assert "Promise.all" in result.records[0].data["text"]


def test_html_selector_that_misses_falls_back_to_the_document(registry):
    manifest = registry.get("mdn")
    cap = _page_capability(docs_selector="article.does-not-exist")

    result = _parse_html(manifest, cap, HTML, {"path": "javascript/promise_all"})

    # Better the whole page than an empty answer.
    assert "Promise.all" in result.records[0].data["text"]


def test_blocks_render_titles_only_when_present():
    blocks = [
        {"type": "prose", "value": {"title": None, "content": "<p>Lead.</p>"}},
        {"type": "prose", "value": {"title": "Usage", "content": "<p>More.</p>"}},
        {"type": "prose", "value": {"title": "Nested", "isH3": True, "content": "<p>Deep.</p>"}},
        {"type": "browser_compatibility", "value": {"query": "api"}},
        {"type": "prose", "value": {"content": 42}},
    ]

    text = blocks_to_markdown(blocks)

    assert text.startswith("Lead.")
    assert "## Usage" in text
    assert "### Nested" in text
    assert "api" not in text


def test_html_to_markdown_keeps_list_markers_and_inline_code():
    markup = "<ul><li>First <code>a()</code>.</li><li>Second</li></ul>"

    text = html_to_markdown(markup)

    assert text == "- First `a()`.\n\n- Second"
