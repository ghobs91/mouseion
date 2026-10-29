"""MCP server: four tools, stable forever.

Register this with any MCP client. For llama.cpp specifically, see
examples/llamacpp_agent.py, which drives the same Toolkit without MCP.

Requires mcp >= 2 (FastMCP was renamed MCPServer).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .bootstrap import build

app = build()
toolkit = app.toolkit

mcp = MCPServer(
    name="mouseion",
    version="0.1.0",
    instructions=(
        "You are answering questions from primary sources.\n"
        "Workflow: call providers_search with the topic, then provider_describe on the "
        "best provider to learn its exact parameters, then provider_call.\n"
        "Prefer a structured provider over guessing from memory. Cite the source_url of "
        "every record you use. If no provider fits, use fetch_url on the authoritative page."
    ),
)


@mcp.tool()
async def providers_search(query: str, category: str | None = None, limit: int = 8) -> dict:
    """Find providers whose upstream API can answer this question.

    Call this first, with the subject of the question (not a full sentence).
    Returns compact cards: id, title, category, auth type and capability names.
    Pass an id to provider_describe to see its parameters.
    """
    return await toolkit.providers_search(query, category=category, limit=limit)


@mcp.tool()
async def provider_describe(provider: str) -> dict:
    """Show one provider's capabilities, exact parameter names, pagination mode, auth and licence.

    Always call this before provider_call so param names and value formats are correct.
    """
    return await toolkit.provider_describe(provider)


@mcp.tool()
async def provider_call(
    provider: str,
    capability: str,
    params: dict | None = None,
    cursor: str | None = None,
    refresh: bool = False,
) -> dict:
    """Call one provider capability and get normalized records.

    Every record carries source_record_id, source_url and retrieved_at; cite source_url.
    If the response has truncated=true, pass cursor.next back as cursor to keep reading.
    Cached responses are served with source_status.cache_age_s > 0; set refresh=true to bypass.
    """
    return await toolkit.provider_call(provider, capability, params, cursor, refresh)


@mcp.tool()
async def fetch_url(url: str, max_chars: int = 20000) -> dict:
    """Fetch one URL and return readable text (HTML is stripped, JSON is passed through).

    The escape hatch when no provider covers the question. Prefer a provider whenever one
    exists: it returns structured, citable records instead of a page you must interpret.
    """
    return await toolkit.fetch_url(url, max_chars=max_chars)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
