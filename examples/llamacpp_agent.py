"""End-to-end loop for an OpenAI-compatible local model server.

Works with llama.cpp, Unsloth Studio, vLLM, LM Studio and similar.

    python examples/llamacpp_agent.py --question "Were there Class I drug recalls?"
    python examples/llamacpp_agent.py --question "..." \
        --base-url http://127.0.0.1:8889 --mode tools

Default mode is grammar: the server constrains output to a valid tool call, so a
small model cannot emit malformed JSON or invent a provider name. Unsloth Studio
is better served by `--mode tools` on port 8889; the model is auto-detected from
/v1/models, and reasoning models default to thinking off so the tool call is not
starved of tokens. Pass `--thinking` to leave reasoning on.
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
from mouseion.llamacpp import chat_payload, pick_model, system_prompt

MAX_TOOL_CHARS = 6_000
MAX_ROUNDS = 6


async def chat(
    http: httpx.AsyncClient,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    mode: str,
    *,
    max_tokens: int,
    thinking: bool,
    tool_choice: str = "auto",
) -> dict[str, Any]:
    payload = chat_payload(
        model,
        messages,
        mode,
        max_tokens=max_tokens,
        thinking=thinking,
        tool_choice=tool_choice,
    )
    response = await http.post(f"{base_url}/v1/chat/completions", json=payload)
    if response.status_code >= 400:
        raise SystemExit(
            f"model server returned HTTP {response.status_code}: {response.text[:500]}"
        )
    return response.json()["choices"][0]["message"]


async def resolve_model(http: httpx.AsyncClient, base_url: str, model: str | None) -> str:
    if model:
        return model
    try:
        response = await http.get(f"{base_url}/v1/models")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SystemExit(f"could not list models at {base_url}/v1/models: {exc}") from None
    chosen = pick_model(response.json())
    if not chosen:
        raise SystemExit("no loaded text model found; pass --model explicitly")
    print(f"using model: {chosen}")
    return chosen


def parse_call(
    message: dict[str, Any], mode: str
) -> tuple[str, dict[str, Any], str | None] | None:
    if mode == "tools":
        calls = message.get("tool_calls") or []
        if not calls:
            return None
        call = calls[0]
        raw = call["function"].get("arguments") or {}
        arguments = raw if isinstance(raw, dict) else json.loads(raw)
        return call["function"]["name"], arguments, call.get("id")

    content = (message.get("content") or "").strip()
    if not content:
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "tool" not in parsed:
        return None
    return parsed["tool"], parsed.get("arguments") or {}, None


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--model", default=None, help="model id; auto-detected if omitted")
    parser.add_argument("--mode", choices=["grammar", "tools"], default="grammar")
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="stop after N pages per capability; 0 = unlimited",
    )
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="leave reasoning on for Qwen3-style models",
    )
    args = parser.parse_args()

    app = build()
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": system_prompt(app.registry.summary(), mode=args.mode),
        },
        {"role": "user", "content": args.question},
    ]
    pages: dict[str, int] = {}
    force_answer = False

    async with httpx.AsyncClient(timeout=240) as http:
        model = await resolve_model(http, args.base_url, args.model)
        for round_no in range(1, MAX_ROUNDS + 1):
            message = await chat(
                http,
                args.base_url,
                model,
                messages,
                args.mode,
                max_tokens=args.max_tokens,
                thinking=args.thinking,
                tool_choice="none" if force_answer else "auto",
            )
            call = parse_call(message, args.mode)

            if call is None:
                print(f"\n--- answer (round {round_no}) ---\n{message.get('content')}")
                break

            name, arguments, call_id = call
            if name == "final":
                print(f"\n--- answer (round {round_no}) ---\n{arguments.get('answer')}")
                break

            print(f"[{round_no}] {name}({json.dumps(arguments, ensure_ascii=False)[:160]})")
            page_limit_hit = False
            if name == "provider_call" and args.max_pages:
                key = f"{arguments.get('provider')}/{arguments.get('capability')}"
                if pages.get(key, 0) >= args.max_pages:
                    page_limit_hit = True
                    result = {
                        "error": "page limit reached for this run",
                        "hint": "answer with the records already returned",
                        "truncated": False,
                    }
                else:
                    result = await dispatch(app, name, arguments)
                    pages[key] = pages.get(key, 0) + 1
                    if result.get("truncated") and pages[key] >= args.max_pages:
                        page_limit_hit = True
                        result = dict(result)
                        result["truncated"] = False
                        result.pop("cursor", None)
                        result["hint"] = "page limit reached; answer with these records"
            else:
                result = await dispatch(app, name, arguments)

            blob = json.dumps(result, ensure_ascii=False, default=str)[:MAX_TOOL_CHARS]

            if args.mode == "tools":
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": message.get("tool_calls") or [],
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id or "call_0",
                        "content": blob,
                    }
                )
            else:
                messages.append({"role": "assistant", "content": message.get("content")})
                messages.append(
                    {
                        "role": "user",
                        "content": f"TOOL RESULT ({name}):\n{blob}\n\nContinue.",
                    }
                )

            if page_limit_hit:
                force_answer = True
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Page limit reached. Do not call another tool. Answer the "
                            "user's question now with the records already returned, "
                            "citing source_url."
                        ),
                    }
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
