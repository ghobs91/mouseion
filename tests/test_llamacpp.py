from __future__ import annotations

import json

from mouseion.llamacpp import GRAMMAR_TOOLS, grammar, openai_tools, system_prompt


def test_tool_definitions_are_well_formed():
    tools = openai_tools()
    assert [t["function"]["name"] for t in tools] == [
        "providers_search",
        "provider_describe",
        "provider_call",
        "fetch_url",
    ]
    for tool in tools:
        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        assert params["properties"]
        assert params["required"]
    json.dumps(tools)  # must be serializable as-is


def test_grammar_closes_the_tool_set():
    text = grammar()
    for name in GRAMMAR_TOOLS:
        assert f'\\"{name}\\"' in text
    assert "object" in text and "string" in text
    # Every rule referenced must be defined.
    rules = {line.split("::=")[0].strip() for line in text.splitlines() if "::=" in line}
    assert rules == {"root", "object", "pair", "array", "value", "string", "number", "ws"}


def test_system_prompt_names_the_stop_token():
    prompt = system_prompt({"providers": 4, "categories": {"public-records": 4}})
    assert '"final"' in prompt
    assert "providers_search" in prompt
    assert "source_url" in prompt
