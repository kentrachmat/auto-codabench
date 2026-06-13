"""The plan→build pipeline: two isolated agent sessions joined by one file.

Phase isolation is load-bearing, not an implementation detail. The build
session starts with zero conversation history; the locked
``implementation_plan.md`` is the entire interface between deliberation
and execution. We discard the planner's context deliberately: it reduces
token cost, gives the builder a focused prompt instead of a long mixed
history, leaves a human-reviewable (and editable) decision record between
the phases, and makes "was the plan wrong, or the implementation?"
answerable from artifacts alone. See ``docs/design-rationale.md``,
Section 10, for the full argument and its costs.

Each phase is one ``AgentBackend.run()``; the agent acts only through the
autocodabench MCP server (spawned as a stdio subprocess scoped to the run
directory), so every authoring action lands in the run's ``tool_calls/``
audit trail — which is also what makes finished runs replayable.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..backends.base import AgentBackend, AgentRunResult, AgentTask
from ..run_log import open_run
from . import prompts

# Tool allowlists per phase — the narrow capability surface is the contract.
PLAN_TOOLS = [
    "mcp__autocodabench__autocodabench_open_run",
    "mcp__autocodabench__autocodabench_current_run",
    "mcp__autocodabench__autocodabench_log_event",
    "mcp__autocodabench__autocodabench_snapshot_spec",
    "Read", "Grep", "Glob",
]
BUILD_TOOLS = [
    "mcp__autocodabench__*",
    "Read", "Grep", "Glob",
]


@dataclass
class CreateResult:
    ok: bool
    run_dir: Path
    plan_path: Path | None = None
    bundle_dir: Path | None = None
    zip_path: Path | None = None
    validation: Any = None              # checks.ValidationReport | None
    plan: AgentRunResult | None = None
    build: AgentRunResult | None = None
    error: str | None = None

    @property
    def total_cost_usd(self) -> float:
        return sum(
            r.total_cost_usd or 0.0
            for r in (self.plan, self.build)
            if r is not None
        )


def _mcp_servers(run_dir: Path) -> dict[str, Any]:
    """The autocodabench MCP server as a stdio subprocess scoped to this run."""
    return {
        "autocodabench": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "autocodabench.mcp.server"],
            "env": {**os.environ, "AUTOCODABENCH_RUN_DIR": str(run_dir)},
        },
    }


def _find_bundle(run_dir: Path) -> tuple[Path | None, Path | None]:
    bundles = run_dir / "bundles"
    if not bundles.is_dir():
        return None, None
    for d in sorted(bundles.iterdir()):
        if d.is_dir() and (d / "competition.yaml").is_file():
            zip_path = bundles / f"{d.name}.zip"
            return d, zip_path if zip_path.is_file() else None
    return None, None


async def create_async(
    idea: str,
    *,
    data: str | None = None,
    backend: AgentBackend | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
    on_text: Callable[[str], None] | None = None,
    validate: bool = True,
) -> CreateResult:
    """Idea (or proposal text) → validated Codabench bundle, in two phases."""
    if backend is None:
        from ..backends import get_claude_backend
        backend = get_claude_backend(model=model) if model else get_claude_backend()

    info = open_run(slug="create")
    run_dir = info.path
    mcp_servers = _mcp_servers(run_dir)
    env = {**os.environ, "AUTOCODABENCH_RUN_DIR": str(run_dir)}

    # ---- Phase 1: plan -----------------------------------------------------
    plan_prompt = (
        "Open the run with autocodabench_open_run, then produce the "
        "implementation plan for this competition idea:\n\n"
        f"{idea}\n"
    )
    if data:
        plan_prompt += (
            f"\nSample data for the competition is available at: {data}\n"
            "Inspect it with the Read/Glob tools before fixing the data design."
        )
    plan_result = await backend.run(AgentTask(
        prompt=plan_prompt,
        system_prompt=prompts.plan_system_prompt(),
        allowed_tools=PLAN_TOOLS,
        mcp_servers=mcp_servers,
        env=env,
        model=model,
        max_budget_usd=max_budget_usd,
        trace_path=run_dir / "agent_trace" / "plan.jsonl",
        on_text=on_text,
    ))
    plan_path = run_dir / "specs" / "implementation_plan.md"
    if not plan_result.ok or not plan_path.is_file():
        return CreateResult(
            ok=False, run_dir=run_dir,
            plan=plan_result,
            error=plan_result.error or (
                "plan phase finished without saving specs/implementation_plan.md"),
        )

    # ---- Phase 2: build (fresh session; only the plan carries over) --------
    build_result = await backend.run(AgentTask(
        prompt=("The locked implementation plan is at "
                f"{plan_path}. Read it and build the bundle now."),
        system_prompt=prompts.build_system_prompt(),
        allowed_tools=BUILD_TOOLS,
        mcp_servers=mcp_servers,
        env=env,
        model=model,
        max_budget_usd=max_budget_usd,
        trace_path=run_dir / "agent_trace" / "build.jsonl",
        on_text=on_text,
    ))
    bundle_dir, zip_path = _find_bundle(run_dir)
    if not build_result.ok or bundle_dir is None:
        return CreateResult(
            ok=False, run_dir=run_dir, plan_path=plan_path,
            plan=plan_result, build=build_result,
            error=build_result.error or "build phase produced no bundle",
        )

    # ---- Validate through the check framework ------------------------------
    report = None
    if validate:
        from ..checks import validate_bundle_path_async
        report = await validate_bundle_path_async(bundle_dir)

    return CreateResult(
        ok=report.ok if report is not None else True,
        run_dir=run_dir,
        plan_path=plan_path,
        bundle_dir=bundle_dir,
        zip_path=zip_path,
        validation=report,
        plan=plan_result,
        build=build_result,
    )


def create(idea: str, **kwargs: Any) -> CreateResult:
    """Sync wrapper around :func:`create_async`."""
    return asyncio.run(create_async(idea, **kwargs))
