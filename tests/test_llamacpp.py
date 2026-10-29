from __future__ import annotations

import json

from mouseion.llamacpp import (
    GRAMMAR_TOOLS,
    chat_payload,
    grammar,
    openai_tools,
    pick_model,
    system_prompt,
)


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


def test_tools_system_prompt_does_not_ask_for_a_json_envelope():
    prompt = system_prompt(
        {"providers": 43, "categories": {"developer": 7}}, mode="tools"
    )
    assert "Do not emit a JSON tool envelope" in prompt
    assert "Do not pass a category" in prompt
    assert "provider_call before considering fetch_url" in prompt
    assert '"final"' not in prompt


def test_pick_model_skips_unloaded_and_image_models():
    payload = {
        "data": [
            {"id": "image", "object": "model", "task": "text-to-image", "loaded": True},
            {"id": "unloaded", "object": "model", "loaded": False},
            {"id": "text", "object": "model", "loaded": True},
        ]
    }
    assert pick_model(payload) == "text"


def test_pick_model_accepts_llamacpp_shape_without_loaded():
    payload = {"data": [{"id": "local-model", "object": "model"}]}
    assert pick_model(payload) == "local-model"


def test_chat_payload_disables_thinking_by_default():
    payload = chat_payload("m", [{"role": "user", "content": "hi"}], "tools")
    assert payload["model"] == "m"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["tools"]
    assert payload["tool_choice"] == "auto"


def test_chat_payload_can_leave_thinking_on():
    payload = chat_payload("m", [], "grammar", thinking=True)
    assert "chat_template_kwargs" not in payload
    assert "grammar" in payload


def test_chat_payload_can_force_a_final_answer():
    payload = chat_payload("m", [], "tools", tool_choice="none")
    assert payload["tool_choice"] == "none"
