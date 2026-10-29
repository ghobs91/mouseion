"""End-to-end loop for llama.cpp: question in, cited records out.

    python examples/llamacpp_agent.py --question "Were there Class I drug recalls recently?"
    python examples/llamacpp_agent.py --question "..." --mode tools   # needs llama-server --jinja

Default mode is grammar: llama-server constrains output to a valid tool call, so a
small model cannot emit malformed JSON or invent a provider name.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

# The example runs from a checkout without installation, so make the package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mouseion.bootstrap import build
from mouseion.llamacpp import grammar, openai_tools, system_prompt

MAX_TOOL_CHARS = 6_000
MAX_ROUNDS = 6


async def chat(
    http: httpx.AsyncClient,
    base_url: str,
    messages: list[dict[str, Any]],
    mode: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 900,
        "stream": False,
    }
    if mode == "tools":
        payload["tools"] = openai_tools()
        payload["tool_choice"] = "auto"
    else:
        payload["grammar"] = grammar()

    response = await http.post(f"{base_url}/v1/chat/completions", json=payload)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]


def parse_call(message: dict[str, Any], mode: str) -> tuple[str, dict[str, Any]] | None:
    if mode == "tools":
        calls = message.get("tool_calls") or []
        if not calls:
            return None
        call = calls[0]["function"]
        return call["name"], json.loads(call.get("arguments") or "{}")

    content = (message.get("content") or "").strip()
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "tool" not in parsed:
        return None
    return parsed["tool"], parsed.get("arguments") or {}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--mode", choices=["grammar", "tools"], default="grammar")
    args = parser.parse_args()

    app = build()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(app.registry.summary())},
        {"role": "user", "content": args.question},
    ]

    async with httpx.AsyncClient(timeout=120) as http:
        for round_no in range(1, MAX_ROUNDS + 1):
            message = await chat(http, args.base_url, messages, args.mode)
            call = parse_call(message, args.mode)

            if call is None:
                print(f"\n--- answer (round {round_no}) ---\n{message.get('content')}")
                break

            name, arguments = call
            if name == "final":
                print(f"\n--- answer (round {round_no}) ---\n{arguments.get('answer')}")
                break

            print(f"[{round_no}] {name}({json.dumps(arguments, ensure_ascii=False)[:160]})")
            result = await dispatch(app, name, arguments)
            blob = json.dumps(result, ensure_ascii=False, default=str)[:MAX_TOOL_CHARS]

            if args.mode == "tools":
                messages.append(message)
                messages.append(
                    {"role": "tool", "tool_call_id": "call_0", "content": blob}
                )
            else:
                messages.append({"role": "assistant", "content": message.get("content")})
                messages.append(
                    {"role": "user", "content": f"TOOL RESULT ({name}):\n{blob}\n\nContinue."}
                )
        else:
            print("\n--- out of rounds; stopping ---")

    await app.aclose()


async def dispatch(app: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    toolkit = app.toolkit
    if name == "providers_search":
        return await toolkit.providers_search(
            arguments.get("query", ""), arguments.get("category"), int(arguments.get("limit", 8))
        )
    if name == "provider_describe":
        return await toolkit.provider_describe(arguments.get("provider", ""))
    if name == "provider_call":
        return await toolkit.provider_call(
            arguments.get("provider", ""),
            arguments.get("capability", ""),
            arguments.get("params") or {},
            arguments.get("cursor"),
            bool(arguments.get("refresh", False)),
        )
    if name == "fetch_url":
        return await toolkit.fetch_url(
            arguments.get("url", ""), int(arguments.get("max_chars", 20_000))
        )
    return {"error": f"unknown tool '{name}'"}


if __name__ == "__main__":
    asyncio.run(main())
