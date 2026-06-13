"""Agent backends — the seam between autocodabench and the model runtime.

Shipped backends:

- ``claude`` — the Claude Agent SDK (subscription login or
  ``ANTHROPIC_API_KEY``). Richest runtime: subagents, MCP, file tools.
- ``ollama:<model>`` / ``openai:<model>`` / any OpenAI-compatible
  endpoint — :class:`~autocodabench.backends.openai_compat.OpenAICompatBackend`,
  a plain tool-calling loop over chat-completions with the same tool
  surface and the same per-run audit trail. Ollama runs local models
  with no API key at all.
- ``replay`` — deterministic, keyless re-execution of a recorded run's
  tool calls (:class:`~autocodabench.backends.replay.ReplayBackend`).
  Powers ``autocodabench demo`` and CI.

Everything above this seam (phases, judged checks, CLI, web UI) talks
only to :class:`~autocodabench.backends.base.AgentBackend`, which is what
makes cross-backbone benchmarking possible
(see ``experiments/backbone_bench/``).
"""
from __future__ import annotations

import os

from .base import AgentBackend, AgentRunResult, AgentTask
from .replay import ReplayBackend

__all__ = [
    "AgentBackend",
    "AgentRunResult",
    "AgentTask",
    "ReplayBackend",
    "get_claude_backend",
    "resolve_backend",
]


def get_claude_backend(**kwargs):
    """Late import so keyless environments never load the Agent SDK."""
    from .claude import ClaudeAgentBackend

    return ClaudeAgentBackend(**kwargs)


def resolve_backend(spec: str | None = None, *, model: str | None = None) -> AgentBackend:
    """Turn a CLI/user backend spec into a backend instance.

    Specs (model after the first ``:``; ``--model`` overrides):

    - ``None`` / ``"claude"`` / ``"claude:<model>"`` — Claude Agent SDK.
    - ``"ollama:<model>"`` — local Ollama (``OLLAMA_HOST`` env overrides
      the default ``http://localhost:11434``). No API key.
    - ``"openai:<model>"`` — OpenAI or any proxy via ``OPENAI_BASE_URL``;
      key from ``OPENAI_API_KEY``.
    - ``"<http(s)://...>#<model>"`` — any OpenAI-compatible endpoint;
      key from ``AUTOCODABENCH_LLM_API_KEY`` (or ``OPENAI_API_KEY``).
    """
    if spec is None or spec == "claude":
        return get_claude_backend(model=model) if model else get_claude_backend()

    if spec.startswith(("http://", "https://")):
        base_url, sep, url_model = spec.partition("#")
        chosen = model or (url_model if sep else "")
        if not chosen:
            raise ValueError(
                f"backend spec {spec!r} needs a model: append '#<model>' or pass --model")
        from .openai_compat import OpenAICompatBackend
        key = os.environ.get("AUTOCODABENCH_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        return OpenAICompatBackend(model=chosen, base_url=base_url, api_key=key)

    kind, _, spec_model = spec.partition(":")
    chosen = model or spec_model

    if kind == "claude":
        return get_claude_backend(model=chosen) if chosen else get_claude_backend()

    if kind == "ollama":
        if not chosen:
            raise ValueError("usage: --backend ollama:<model>  (e.g. ollama:llama3.1)")
        from .openai_compat import OpenAICompatBackend
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        return OpenAICompatBackend(model=chosen, base_url=f"{host}/v1", api_key="ollama")

    if kind == "openai":
        if not chosen:
            raise ValueError("usage: --backend openai:<model>  (e.g. openai:gpt-4o)")
        from .openai_compat import OpenAICompatBackend
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return OpenAICompatBackend(model=chosen, base_url=base,
                                   api_key=os.environ.get("OPENAI_API_KEY"))

    raise ValueError(
        f"unknown backend spec {spec!r} — expected claude[:model], ollama:<model>, "
        "openai:<model>, or an http(s) endpoint URL with '#<model>'")
