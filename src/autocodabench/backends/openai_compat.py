"""Generic backend for any OpenAI-compatible chat-completions endpoint.

One backend covers many backbones: OpenAI's API, **Ollama** (its
``/v1`` endpoint serves local models keylessly), vLLM, LiteLLM proxies,
Together, etc. The model must support native tool calling (e.g.
``llama3.1+``, ``qwen2.5+``, ``gpt-4o`` family) — autocodabench does not
attempt prompt-side tool emulation, because silent emulation failures
would contaminate cross-backbone comparisons.

The agentic loop is deliberately plain: post messages + tool specs,
execute returned tool calls through :mod:`.local_tools` (same functions,
same audit trail as the MCP layer), append results, repeat until the
model stops calling tools or ``max_turns`` is hit. No SDK dependency —
stdlib HTTP with an injectable transport so tests run keyless.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import local_tools
from .base import AgentRunResult, AgentTask

log = logging.getLogger("autocodabench.backends.openai_compat")

Transport = Callable[[str, dict[str, Any], dict[str, str], int], dict[str, Any]]

# Env vars that scope tool execution to the caller's session. Generic
# backends execute tools in-process, so these must be visible to the
# config/run_log layers for the duration of a run.
_SCOPED_ENV_KEYS = ("AUTOCODABENCH_RUN_DIR", "AUTOCODABENCH_HOME",
                    "AUTOCODABENCH_BUNDLES_ROOT", "AUTOCODABENCH_RUNS_ROOT")


def _urllib_transport(url: str, body: dict[str, Any], headers: dict[str, str],
                      timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"HTTP {e.code} from {url}: {detail}") from e


class OpenAICompatBackend:
    """Tool-calling agent loop over a chat-completions endpoint."""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        max_turns: int = 60,
        request_timeout_s: int = 900,
        transport: Transport | None = None,
    ) -> None:
        if not model:
            raise ValueError("OpenAICompatBackend requires an explicit model name")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_turns = max_turns
        self.request_timeout_s = request_timeout_s
        self.transport = transport or _urllib_transport

    # -- helpers -------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _chat(self, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        return self.transport(f"{self.base_url}/chat/completions", body,
                              self._headers(), self.request_timeout_s)

    @staticmethod
    def _accumulate_usage(total: dict[str, int], usage: dict | None) -> None:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if usage and isinstance(usage.get(k), int):
                total[k] = total.get(k, 0) + usage[k]

    # -- the loop ------------------------------------------------------------

    async def run(self, task: AgentTask) -> AgentRunResult:
        tools = local_tools.select_tools(task.allowed_tools)
        tool_specs = [t.spec() for t in tools]

        messages: list[dict[str, Any]] = []
        if task.system_prompt:
            messages.append({"role": "system", "content": task.system_prompt})
        messages.append({"role": "user", "content": task.prompt})

        trace_file = None
        if task.trace_path is not None:
            Path(task.trace_path).parent.mkdir(parents=True, exist_ok=True)
            trace_file = Path(task.trace_path).open("w", encoding="utf-8")

        # Scope tool execution to the task's session env, then restore.
        saved_env = {k: os.environ.get(k) for k in _SCOPED_ENV_KEYS}
        for k in _SCOPED_ENV_KEYS:
            if task.env.get(k):
                os.environ[k] = task.env[k]

        usage_total: dict[str, int] = {}
        final_text = ""
        status = "error_max_turns"
        error: str | None = None
        turns = 0
        try:
            for turns in range(1, self.max_turns + 1):
                try:
                    resp = await asyncio.to_thread(self._chat, messages, tool_specs)
                except Exception as e:
                    status, error = "error", f"{type(e).__name__}: {e}"
                    break
                if trace_file is not None:
                    trace_file.write(json.dumps({"turn": turns, "response": resp},
                                                ensure_ascii=False, default=str) + "\n")
                    trace_file.flush()

                choices = resp.get("choices") or []
                if not choices:
                    status, error = "error", f"no choices in response: {str(resp)[:300]}"
                    break
                msg = choices[0].get("message") or {}
                self._accumulate_usage(usage_total, resp.get("usage"))

                if msg.get("content") and task.on_text is not None:
                    task.on_text(str(msg["content"]))

                tool_calls = msg.get("tool_calls") or []
                # Echo the assistant message back verbatim (required so the
                # endpoint can match tool results to calls).
                messages.append({k: v for k, v in msg.items() if v is not None})

                if not tool_calls:
                    final_text = str(msg.get("content") or "")
                    status = "success"
                    break

                for call in tool_calls:
                    fn = (call.get("function") or {})
                    name = fn.get("name", "")
                    try:
                        arguments = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError as e:
                        result_text = json.dumps(
                            {"error": f"unparseable tool arguments: {e}"})
                    else:
                        result_text = await asyncio.to_thread(
                            local_tools.execute_tool, name, arguments)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": result_text,
                    })
            else:  # pragma: no cover — loop exhausted via range
                pass
            if status == "error_max_turns":
                error = error or f"model did not finish within max_turns={self.max_turns}"
        finally:
            if trace_file is not None:
                trace_file.close()
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        return AgentRunResult(
            status=status,
            final_text=final_text,
            num_turns=turns,
            total_cost_usd=None,   # endpoint pricing unknown; tokens reported instead
            usage=usage_total or None,
            trace_path=str(task.trace_path) if task.trace_path else None,
            error=error,
        )
