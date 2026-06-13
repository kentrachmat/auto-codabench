"""Keyless tests for the generic OpenAI-compatible backend.

A stubbed transport plays the endpoint; tool execution is real (the
in-process registry hits the actual core layer), so these tests cover
the full loop: tool spec exposure → tool_call dispatch → result echo →
final answer.
"""
import asyncio
import json

import pytest

from autocodabench.backends import resolve_backend
from autocodabench.backends.base import AgentTask
from autocodabench.backends.local_tools import REGISTRY, select_tools
from autocodabench.backends.openai_compat import OpenAICompatBackend


def _msg(content=None, tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def _scripted_transport(responses, seen):
    def transport(url, body, headers, timeout):
        seen.append(body)
        return responses.pop(0)
    return transport


def test_plain_chat_no_tools():
    seen = []
    backend = OpenAICompatBackend(
        model="m", base_url="http://x/v1",
        transport=_scripted_transport([_msg(content="OK")], seen))
    result = asyncio.run(backend.run(AgentTask(prompt="hi", allowed_tools=[])))
    assert result.ok and result.final_text == "OK"
    assert result.usage["total_tokens"] == 15
    assert "tools" not in seen[0]  # allowed_tools=[] → pure chat


def test_tool_call_round_trip(tmp_path):
    call = {"id": "c1", "type": "function", "function": {
        "name": "autocodabench_init_bundle",
        "arguments": json.dumps({"slug": "t", "root_dir": str(tmp_path)})}}
    seen = []
    backend = OpenAICompatBackend(
        model="m", base_url="http://x/v1",
        transport=_scripted_transport([_msg(tool_calls=[call]), _msg(content="done")], seen))
    result = asyncio.run(backend.run(
        AgentTask(prompt="build", allowed_tools=["mcp__autocodabench__*"])))
    assert result.ok and result.num_turns == 2
    assert (tmp_path / "t" / "pages").is_dir()          # the tool really ran
    tool_msg = next(m for m in seen[1]["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"])["created"] is True


def test_unknown_tool_returns_error_not_crash():
    call = {"id": "c1", "type": "function",
            "function": {"name": "rm_rf_everything", "arguments": "{}"}}
    seen = []
    backend = OpenAICompatBackend(
        model="m", base_url="http://x/v1",
        transport=_scripted_transport([_msg(tool_calls=[call]), _msg(content="ok")], seen))
    result = asyncio.run(backend.run(AgentTask(prompt="x")))
    assert result.ok
    tool_msg = next(m for m in seen[1]["messages"] if m["role"] == "tool")
    assert "unknown tool" in tool_msg["content"]


def test_max_turns_guard():
    call = {"id": "c", "type": "function",
            "function": {"name": "autocodabench_current_run", "arguments": "{}"}}
    responses = [_msg(tool_calls=[call]) for _ in range(5)]
    backend = OpenAICompatBackend(model="m", base_url="http://x/v1", max_turns=3,
                                  transport=_scripted_transport(responses, []))
    result = asyncio.run(backend.run(AgentTask(prompt="loop forever")))
    assert result.status == "error_max_turns" and not result.ok


def test_select_tools_mapping():
    all_acb = select_tools(["mcp__autocodabench__*"])
    assert all(t.name.startswith("autocodabench_") for t in all_acb)
    assert len(all_acb) >= 15

    subset = select_tools(["mcp__autocodabench__autocodabench_init_bundle", "Read", "Glob"])
    assert {t.name for t in subset} == {"autocodabench_init_bundle", "read_file", "list_dir"}

    assert select_tools([]) == []
    assert {t.name for t in select_tools(None)} == set(REGISTRY)


def test_resolve_backend_specs(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    b = resolve_backend("ollama:llama3.1")
    assert b.base_url.endswith("/v1") and b.api_key == "ollama" and b.model == "llama3.1"

    b = resolve_backend("openai:gpt-4o")
    assert "openai.com" in b.base_url and b.api_key == "sk-test"

    b = resolve_backend("http://gpu-box:8000/v1#qwen2.5", model=None)
    assert b.model == "qwen2.5" and b.base_url == "http://gpu-box:8000/v1"

    b = resolve_backend("ollama:llama3.1", model="mistral")  # --model wins
    assert b.model == "mistral"

    with pytest.raises(ValueError, match="ollama:<model>"):
        resolve_backend("ollama")
    with pytest.raises(ValueError, match="unknown backend"):
        resolve_backend("bedrock:foo")
