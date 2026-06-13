"""MCP tools for opening / logging into a run directory.

A *run* is the working folder for one planning or execution session. The
plan skill calls `autocodabench_open_run` as its first action and then
records progress through `autocodabench_log_event` /
`autocodabench_snapshot_spec`, so the run directory accumulates a
contemporaneous account of the session rather than a reconstruction
after the fact.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..instance import mcp
from ...run_log import (
    current_run,
    log_event,
    logged_tool,
    open_run,
    snapshot_spec,
)

log = logging.getLogger("autocodabench.runs")


@mcp.tool()
@logged_tool("autocodabench_open_run")
async def autocodabench_open_run(
    slug: str | None = None,
    branch_id: str | None = None,
    runtime_id: str | None = None,
) -> dict[str, Any]:
    """Open (or adopt) the run directory for this session.

    Call this **as the first MCP call** of any orchestration session. It
    creates `<runs_root>/<branch_id>_<runtime_id>/` and routes all
    subsequent tool snapshots, events, and spec writes into it.

    If `AUTOCODABENCH_RUN_DIR` is already set to a valid run directory
    (e.g. a parent process passed it down), this call adopts that run
    rather than creating a new one.

    Args:
        slug:        short label for this session (becomes part of meta.json).
        branch_id:   override the auto-detected git branch (kebab-case).
        runtime_id:  override the auto-generated timestamp.

    Returns:
        Dict with `path`, `branch_id`, `runtime_id`, `git_sha`, and `slug`.
    """
    try:
        info = open_run(slug=slug, branch_id=branch_id, runtime_id=runtime_id)
        return info.to_dict()
    except Exception as e:
        return {"error": f"open_run failed: {e}"}


@mcp.tool()
@logged_tool("autocodabench_current_run")
async def autocodabench_current_run() -> dict[str, Any]:
    """Return the path of the active run, or {opened: False} if none.

    Useful for the skill to check before doing work — a missing run is a
    sign that `autocodabench_open_run` was forgotten.
    """
    p = current_run()
    if p is None:
        return {"opened": False}
    return {"opened": True, "path": str(p)}


@mcp.tool()
@logged_tool("autocodabench_log_event")
async def autocodabench_log_event(
    kind: str,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a structured event to the active run's events.jsonl.

    Recommended `kind` values for the plan skill:

      - `iter1_started`           — first call after the skill activates
      - `question_asked`          — payload={"question": "..."}
      - `user_answer_recorded`    — payload={"summary": "..."}
      - `ss_searched`             — payload={"query": "...", "n_results": int}
      - `proposal_made`           — payload={"dim": "metric|data|...", "value": "...", "citations": ["<paperId>"]}
      - `spec_written`            — auto-emitted by autocodabench_snapshot_spec
      - `iter1_done`              — payload={"specs": [...], "plan_path": "..."}

    Args:
        kind:    short lowercase snake_case label.
        message: optional human-readable description.
        payload: optional dict carried in the event verbatim.

    Returns:
        Dict echoing what was logged.
    """
    fields: dict[str, Any] = {}
    if message is not None:
        fields["message"] = message
    if payload is not None:
        fields["payload"] = payload
    log_event(kind, **fields)
    return {"logged": True, "kind": kind, **fields}


@mcp.tool()
@logged_tool("autocodabench_snapshot_spec")
async def autocodabench_snapshot_spec(filename: str, body: str) -> dict[str, Any]:
    """Write a spec into `<run>/specs/<filename>` AND a versioned copy in `specs_history/`.

    Use this for any spec file (`01-task-framing.md`, etc.) or the
    `implementation_plan.md`. Versioned history is created automatically so
    successive rewrites are preserved and diffable.

    Args:
        filename: relative path inside the run's specs/ directory.
                  e.g. `01-task-framing.md` or `implementation_plan.md`.
        body:     full file content.

    Returns:
        Dict with `path` (active copy) and `history` (versioned copy).
    """
    try:
        return snapshot_spec(filename, body)
    except Exception as e:
        return {"error": f"snapshot_spec failed: {e}"}
