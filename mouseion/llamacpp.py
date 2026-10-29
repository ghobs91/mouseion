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
                    "Call first, with the subject only. Omit category unless the user "
                    "explicitly names one. Returns compact provider cards."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Subject of the question."},
                        "category": {
                            "type": "string",
                            "description": "Optional; omit unless the user names a category.",
                        },
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
                    "Use only when providers_search returns no provider, or provider_call "
                    "returns no records. Fetch one URL as readable text; prefer a structured "
                    "provider whenever one exists."
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


def system_prompt(registry_summary: dict[str, Any], mode: str = "grammar") -> str:
    categories = ", ".join(sorted(registry_summary.get("categories", {}))) or "none"
    common = (
        "You answer from primary sources, not memory.\n"
        f"Providers available: {registry_summary.get('providers', 0)} ({categories}).\n"
    )
    if mode == "tools":
        return common + (
            "Use the provided tools. Workflow: providers_search -> provider_describe -> "
            "provider_call.\n"
            "Do not pass a category to providers_search unless the user explicitly names "
            "one; the router already ranks providers.\n"
            "If providers_search returns a provider, call provider_describe and then "
            "provider_call before considering fetch_url.\n"
            "Use fetch_url only when providers_search returns no provider, or provider_call "
            "returns no records.\n"
            "Read at most one page per provider unless the user explicitly asks for "
            "exhaustive results; then answer.\n"
            "Cite source_url for every record you rely on. Never infer a field the record "
            "does not contain; report it as missing.\n"
            "When you have enough evidence, answer in content with the source_url "
            "citations. Do not emit a JSON tool envelope."
        )
    return common + (
        "Workflow: providers_search -> provider_describe -> provider_call.\n"
        "Do not pass a category to providers_search unless the user explicitly names one.\n"
        "Cite source_url for every record you rely on.\n"
        "Use fetch_url only when providers_search returns no provider, or provider_call "
        "returns no records.\n"
        "Read at most one page per provider unless the user explicitly asks for exhaustive "
        "results; then answer.\n"
        "Never infer a field the record does not contain; report it as missing.\n"
        "Reply with exactly one JSON object and nothing else:\n"
        '  {"tool": "<one of: '
        + " | ".join(GRAMMAR_TOOLS)
        + '>", "arguments": {...}}\n'
        'When you have enough evidence, call {"tool": "final", "arguments": {"answer": "..."}} '
        "with the source_url citations included.\n"
        "Current call looks like: "
        + json.dumps({"tool": "providers_search", "arguments": {"query": "<subject>"}})
    )


def pick_model(models: dict[str, Any]) -> str | None:
    """Pick the first loaded text model from an OpenAI ``/v1/models`` payload.

    llama.cpp often omits ``loaded``; Unsloth Studio includes it and also lists
    image models. Accept absent ``loaded``, reject explicit false, and skip a
    non-text ``task``.
    """
    for item in models.get("data", []):
        if item.get("loaded") is False:
            continue
        task = item.get("task")
        if task and task != "text-generation":
            continue
        model_id = item.get("id")
        if model_id:
            return str(model_id)
    return None


def chat_payload(
    model: str,
    messages: list[dict[str, Any]],
    mode: str,
    *,
    max_tokens: int = 900,
    thinking: bool = False,
    tool_choice: str = "auto",
) -> dict[str, Any]:
    """Build the request body for an OpenAI-compatible chat endpoint.

    ``chat_template_kwargs.enable_thinking`` is a no-op for templates that do not
    use it, and it stops Qwen3-style reasoning models from spending the whole
    token budget before emitting a tool call. Pass ``thinking=True`` to leave it
    on.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if not thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if mode == "tools":
        payload["tools"] = openai_tools()
        payload["tool_choice"] = tool_choice
    else:
        payload["grammar"] = grammar()
    return payload


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(openai_tools(), indent=2))
