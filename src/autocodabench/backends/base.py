"""The AgentBackend contract.

A backend executes one *phase* of agentic work (plan, build, judge) given a
prompt and a capability surface (allowed tools + MCP servers), and returns a
structured result. The contract is deliberately narrow: callers never touch
SDK types, so a recorded-replay backend and the live Claude backend are
interchangeable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class AgentRunResult:
    """Outcome of one backend run."""

    status: str                      # "success" | "error" | "error_max_turns" | ...
    final_text: str = ""             # the agent's last text output
    session_id: str | None = None
    num_turns: int | None = None
    total_cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    trace_path: str | None = None    # JSONL trace of every message, if recorded
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "final_text": self.final_text,
            "session_id": self.session_id,
            "num_turns": self.num_turns,
            "total_cost_usd": self.total_cost_usd,
            "usage": self.usage,
            "trace_path": self.trace_path,
            "error": self.error,
        }


@dataclass
class AgentTask:
    """Everything a backend needs to execute one phase."""

    prompt: str
    system_prompt: str | None = None
    allowed_tools: list[str] | None = None
    mcp_servers: dict[str, Any] | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    max_budget_usd: float | None = None
    # Where to record the message trace (JSONL). None disables recording.
    trace_path: Path | None = None
    # Optional live text callback (CLI progress, web streaming).
    on_text: Callable[[str], None] | None = None


@runtime_checkable
class AgentBackend(Protocol):
    """Anything with an async ``run(task) -> AgentRunResult`` is a backend."""

    name: str

    async def run(self, task: AgentTask) -> AgentRunResult:  # pragma: no cover
        ...
