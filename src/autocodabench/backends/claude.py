"""Live backend on the Claude Agent SDK.

Credential resolution is deliberately *not* re-implemented here: the SDK's
bundled Claude Code runtime owns it, with its own precedence (an exported
``ANTHROPIC_API_KEY`` takes priority over a stored subscription login).
:mod:`autocodabench.auth` provides the user-facing status report and the
pre-session preflight that surface that shadowing hazard before any
tokens are spent.

The backend records every SDK message to a JSONL trace when
``task.trace_path`` is set — those traces, together with the MCP layer's
``tool_calls/`` snapshots, are the raw material for replay fixtures.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

from .base import AgentRunResult, AgentTask

log = logging.getLogger("autocodabench.backends.claude")

DEFAULT_MODEL = "claude-sonnet-4-6"


def _to_jsonable(obj: Any) -> Any:
    """Best-effort conversion of SDK message objects to plain JSON."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


class ClaudeAgentBackend:
    """Execute a phase as one Claude Agent SDK session."""

    name = "claude"

    def __init__(
        self,
        *,
        model: str | None = None,
        permission_mode: str = "bypassPermissions",
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.permission_mode = permission_mode

    async def run(self, task: AgentTask) -> AgentRunResult:
        # Lazy import: keyless environments (validator-only, CI replay)
        # never touch the SDK or its bundled CLI runtime.
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            model=task.model or self.model,
            system_prompt=task.system_prompt,
            mcp_servers=task.mcp_servers or {},
            allowed_tools=task.allowed_tools or [],
            permission_mode=self.permission_mode,
            cwd=task.cwd,
            env=task.env or {},
            max_budget_usd=task.max_budget_usd,
        )

        trace_file = None
        if task.trace_path is not None:
            Path(task.trace_path).parent.mkdir(parents=True, exist_ok=True)
            trace_file = Path(task.trace_path).open("w", encoding="utf-8")

        texts: list[str] = []
        result_msg: Any = None
        try:
            async for message in query(prompt=task.prompt, options=options):
                if trace_file is not None:
                    record = {"type": type(message).__name__, "data": _to_jsonable(message)}
                    trace_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    trace_file.flush()
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            texts.append(block.text)
                            if task.on_text is not None:
                                task.on_text(block.text)
                elif isinstance(message, ResultMessage):
                    result_msg = message
        except Exception as e:
            log.exception("Claude backend run failed")
            return AgentRunResult(
                status="error",
                final_text="\n\n".join(texts),
                error=f"{type(e).__name__}: {e}",
                trace_path=str(task.trace_path) if task.trace_path else None,
            )
        finally:
            if trace_file is not None:
                trace_file.close()

        if result_msg is None:
            return AgentRunResult(
                status="error",
                final_text="\n\n".join(texts),
                error="session ended without a ResultMessage",
                trace_path=str(task.trace_path) if task.trace_path else None,
            )

        status = result_msg.subtype or ("error" if result_msg.is_error else "success")
        return AgentRunResult(
            status=status,
            # `result` carries the final text on success; fall back to the
            # accumulated assistant text for error subtypes.
            final_text=result_msg.result or "\n\n".join(texts),
            session_id=result_msg.session_id,
            num_turns=result_msg.num_turns,
            total_cost_usd=result_msg.total_cost_usd,
            usage=_to_jsonable(result_msg.usage) if result_msg.usage else None,
            trace_path=str(task.trace_path) if task.trace_path else None,
            error="; ".join(result_msg.errors) if getattr(result_msg, "errors", None) else None,
        )
