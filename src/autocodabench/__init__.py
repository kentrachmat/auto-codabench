"""autocodabench — agentic authoring + pre-launch validation of Codabench bundles.

Public surface:

- :func:`autocodabench.validate` / ``autocodabench validate-bundle`` CLI — deterministic,
  LLM-free bundle validation (plus optional LLM-judged advisory checks).
- :func:`autocodabench.create` / ``autocodabench create`` CLI — agentic
  plan→build pipeline on the Claude Agent SDK.
- ``python -m autocodabench.mcp.server`` — the MCP stdio server exposing the
  same authoring/runner tools to any MCP host (Claude Code, Claude Desktop).

The heavy imports (Claude Agent SDK, FastMCP) are deferred so that the
validator works in environments with neither installed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.2.0.dev0"

__all__ = ["__version__", "create", "validate"]


def validate(bundle: str, *, facts: str | None = None, judged: bool = False) -> "Any":
    """Validate a bundle directory or zip. See :mod:`autocodabench.checks`."""
    from .checks.api import validate_bundle_path

    return validate_bundle_path(bundle, facts_path=facts, judged=judged)


def create(idea: str, **kwargs: "Any") -> "Any":
    """Run the agentic plan→build pipeline. See :mod:`autocodabench.agent`."""
    from .agent.pipeline import create as _create

    return _create(idea, **kwargs)
