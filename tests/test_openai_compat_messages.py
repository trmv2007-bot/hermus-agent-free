"""What actually goes on the wire to an OpenAI-compatible provider.

A tool call travels through this repo as ``{"name", "arguments", "id"}``. Strict
providers validate the nested ``function`` object and answer a flat call with
HTTP 422, which killed the second round of every tool-using turn on the free
tier while Ollama, being lenient, never showed it.
"""

from __future__ import annotations

import json

from core.openai_compat import _normalize_messages


def test_a_flat_tool_call_is_sent_in_the_shape_a_provider_validates():
    out = _normalize_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "file_read", "arguments": {"path": "a.txt"}, "id": "c1"}],
            }
        ]
    )
    call = out[0]["tool_calls"][0]
    assert call["id"] == "c1"
    assert call["type"] == "function"
    assert call["function"]["name"] == "file_read"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.txt"}
    assert "name" not in call and "arguments" not in call


def test_an_already_nested_tool_call_keeps_its_arguments():
    out = _normalize_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c2", "type": "function", "function": {"name": "shell_execute", "arguments": '{"cmd": "dir"}'}}
                ],
            }
        ]
    )
    call = out[0]["tool_calls"][0]
    assert call["function"] == {"name": "shell_execute", "arguments": '{"cmd": "dir"}'}


def test_a_tool_result_keeps_the_call_it_answers():
    out = _normalize_messages([{"role": "tool", "tool_call_id": "c1", "content": "TOKEN-7731"}])
    assert out[0]["tool_call_id"] == "c1"
    assert out[0]["content"] == "TOKEN-7731"


def test_a_call_without_an_id_gets_one_so_the_result_can_be_matched():
    out = _normalize_messages([{"role": "assistant", "content": "", "tool_calls": [{"name": "file_read", "arguments": {}}]}])
    assert out[0]["tool_calls"][0]["id"].startswith("call_")
