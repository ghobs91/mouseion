"""llama.cpp integration: tool definitions plus a grammar that makes bad calls impossible.

Two ways to constrain a local model, depending on how much of the template you trust:

1. Native tool calling — llama-server with `--jinja` and a model whose chat template has
   tool tokens. Feed it `openai_tools()`.
2. Grammar — no template required, works with any model. Feed llama-server
   `"grammar": grammar()` so the output must be `{"tool": ..., "arguments": {...}}`.

Grammar mode adds a fifth pseudo-tool, `final`, whose `arguments.answer` holds the reply.
"""

from __future__ import annotations

import json
from typing import Any

TOOLS = ["providers_search", "provider_describe", "provider_call", "fetch_url"]
GRAMMAR_TOOLS = [*TOOLS, "final"]


def openai_tools() -> list[dict[str, Any]]:
    """The four tools in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": "providers_search",
                "description": (
                    "Find providers whose upstream API can answer this question. "
                    "Call first, with the subject only. Returns compact provider cards."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Subject of the question."},
                        "category": {"type": "string", "description": "Optional category filter."},
                        "limit": {"type": "integer", "default": 8},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "provider_describe",
                "description": (
                    "Show one provider's capabilities, exact parameter names and licence. "
                    "Call before provider_call."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"provider": {"type": "string"}},
                    "required": ["provider"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "provider_call",
                "description": (
                    "Call one provider capability and get normalized records with source_url. "
                    "Pass cursor.next as cursor when truncated=true."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string"},
                        "capability": {"type": "string"},
                        "params": {"type": "object", "additionalProperties": True},
                        "cursor": {"type": "string"},
                        "refresh": {"type": "boolean", "default": False},
                    },
                    "required": ["provider", "capability"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_url",
                "description": (
                    "Fetch one URL as readable text. Escape hatch when no provider covers the "
                    "question; prefer a provider when one exists."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "max_chars": {"type": "integer", "default": 20000},
                    },
                    "required": ["url"],
                },
            },
        },
    ]


def grammar() -> str:
    """GBNF for a single tool call with a closed tool-name set."""
    names = " | ".join(f'"\\"{name}\\""' for name in GRAMMAR_TOOLS)
    return f"""root        ::= "{{" ws "\\"tool\\"" ws ":" ws ( {names} ) ws "," ws "\\"arguments\\"" ws ":" ws object ws "}}"
object      ::= "{{" ws ( pair ( ws "," ws pair )* )? ws "}}"
pair        ::= string ws ":" ws value
array       ::= "[" ws ( value ( ws "," ws value )* )? ws "]"
value       ::= object | array | string | number | "true" | "false" | "null"
string      ::= "\\"" ( [^"\\\\] | "\\\\" ( ["\\\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] ) )* "\\""
number      ::= "-"? ( "0" | [1-9] [0-9]* ) ( "." [0-9]+ )? ( [eE] [+-]? [0-9]+ )?
ws          ::= [ \\t\\n]*
"""


def system_prompt(registry_summary: dict[str, Any]) -> str:
    categories = ", ".join(sorted(registry_summary.get("categories", {}))) or "none"
    return (
        "You answer from primary sources, not memory.\n"
        f"Providers available: {registry_summary.get('providers', 0)} ({categories}).\n"
        "Workflow: providers_search -> provider_describe -> provider_call.\n"
        "Cite source_url for every record you rely on.\n"
        "If no provider fits, call fetch_url on the authoritative page.\n"
        "Reply with exactly one JSON object and nothing else:\n"
        '  {"tool": "<one of: '
        + " | ".join(GRAMMAR_TOOLS)
        + '>", "arguments": {...}}\n'
        'When you have enough evidence, call {"tool": "final", "arguments": {"answer": "..."}} '
        "with the source_url citations included.\n"
        "Current call looks like: " + json.dumps({"tool": "providers_search", "arguments": {"query": "<subject>"}})
    )


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(openai_tools(), indent=2))
