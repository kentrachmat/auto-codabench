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

Public API
----------
plan_async(idea, ...)          → PlanResult      # Phase 1 only
bundle_async(run_dir|plan_path, ...) → BundleResult  # Phase 2 only
create_async(idea, ...)        → CreateResult    # Phase 1 + 2 + validate

Sync wrappers: plan_competition(), create_bundle(), create().
"""
from __future__ import annotations

import asyncio
import os
import shutil
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


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PlanResult:
    """Result of a standalone plan phase (Phase 1)."""
    ok: bool
    run_dir: Path
    plan_path: Path | None = None
    plan: AgentRunResult | None = None
    error: str | None = None

    @property
    def total_cost_usd(self) -> float:
        return (self.plan.total_cost_usd or 0.0) if self.plan else 0.0


@dataclass
class BundleResult:
    """Result of a standalone bundle phase (Phase 2)."""
    ok: bool
    run_dir: Path
    bundle_dir: Path | None = None
    zip_path: Path | None = None
    validation: Any = None              # checks.ValidationReport | None
    build: AgentRunResult | None = None
    error: str | None = None

    @property
    def total_cost_usd(self) -> float:
        return (self.build.total_cost_usd or 0.0) if self.build else 0.0


@dataclass
class CreateResult:
    """Result of the full plan→build pipeline (create command)."""
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _update_run_slug(run_dir: Path, slug: str) -> None:
    """Overwrite the slug field in an existing run's meta.json.

    When bundle_async adopts a plan-phase run dir, the stored slug is "plan".
    The agent reads meta.json via autocodabench_open_run and would otherwise
    use "plan" as the bundle subdirectory name. Updating it to "bundle" here
    ensures the agent sees the correct context before it calls init_bundle.
    """
    meta_path = run_dir / "meta.json"
    if not meta_path.is_file():
        return
    try:
        import json as _json
        meta = _json.loads(meta_path.read_text(encoding="utf-8"))
        meta["slug"] = slug
        meta_path.write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    except Exception:
        pass  # non-fatal — worst case the agent picks its own slug


def _find_bundle(run_dir: Path) -> tuple[Path | None, Path | None]:
    bundles = run_dir / "bundles"
    if not bundles.is_dir():
        return None, None
    for d in sorted(bundles.iterdir()):
        if d.is_dir() and (d / "competition.yaml").is_file():
            zip_path = bundles / f"{d.name}.zip"
            return d, zip_path if zip_path.is_file() else None
    return None, None


def _resolve_backend(backend: AgentBackend | None, model: str | None) -> AgentBackend:
    if backend is not None:
        return backend
    from ..backends import get_claude_backend
    return get_claude_backend(model=model) if model else get_claude_backend()


# ---------------------------------------------------------------------------
# Phase 1 — plan
# ---------------------------------------------------------------------------

async def plan_async(
    idea: str,
    *,
    data: str | None = None,
    backend: AgentBackend | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
    on_text: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> PlanResult:
    """Run Phase 1 only: produce specs/implementation_plan.md from an idea.

    Creates (or adopts via AUTOCODABENCH_RUN_DIR) a run directory and runs
    the planning agent in it. Returns a PlanResult whose ``run_dir`` can be
    passed directly to ``bundle_async`` to continue in the same run.
    """
    backend = _resolve_backend(backend, model)

    info = open_run(slug="plan")
    run_dir = info.path
    mcp_servers = _mcp_servers(run_dir)
    env = {**os.environ, "AUTOCODABENCH_RUN_DIR": str(run_dir)}

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
        on_event=on_event,
    ))

    plan_path = run_dir / "specs" / "implementation_plan.md"
    if not plan_result.ok or not plan_path.is_file():
        return PlanResult(
            ok=False, run_dir=run_dir,
            plan=plan_result,
            error=plan_result.error or (
                "plan phase finished without saving specs/implementation_plan.md"),
        )

    return PlanResult(ok=True, run_dir=run_dir, plan_path=plan_path, plan=plan_result)


# ---------------------------------------------------------------------------
# Phase 2 — bundle
# ---------------------------------------------------------------------------

async def bundle_async(
    *,
    run_dir: Path | None = None,
    plan_path: Path | None = None,
    backend: AgentBackend | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
    validate: bool = True,
    on_text: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
) -> BundleResult:
    """Run Phase 2 only: build a Codabench bundle from a plan.

    Exactly one of ``run_dir`` or ``plan_path`` must be supplied:

    ``run_dir``
        Path to an existing run directory that already contains
        ``specs/implementation_plan.md`` (e.g. produced by ``plan_async``).
        The bundle is written into ``<run_dir>/bundles/``.

    ``plan_path``
        Path to a standalone ``implementation_plan.md`` file. A fresh run
        directory is created under the configured runs root, the file is
        copied into it, and the bundle is written into the new run's
        ``bundles/`` subdirectory.
    """
    if (run_dir is None) == (plan_path is None):
        raise ValueError("pass exactly one of run_dir or plan_path")

    backend = _resolve_backend(backend, model)

    if run_dir is not None:
        # Adopt the existing run dir (set env var so open_run picks it up).
        run_dir = Path(run_dir).resolve()
        os.environ["AUTOCODABENCH_RUN_DIR"] = str(run_dir)
        open_run(slug="bundle")
        # Update slug in meta.json from "plan" → "bundle" so the agent doesn't
        # inherit the plan-phase slug and use it as the bundle directory name.
        _update_run_slug(run_dir, "bundle")
        effective_plan = run_dir / "specs" / "implementation_plan.md"
        if not effective_plan.is_file():
            return BundleResult(
                ok=False, run_dir=run_dir,
                error=f"no implementation_plan.md found in {run_dir / 'specs'}",
            )
    else:
        # Fresh run dir — create it and copy the supplied plan in.
        info = open_run(slug="bundle")
        run_dir = info.path
        effective_plan = run_dir / "specs" / "implementation_plan.md"
        shutil.copy2(plan_path, effective_plan)

    mcp_servers = _mcp_servers(run_dir)
    env = {**os.environ, "AUTOCODABENCH_RUN_DIR": str(run_dir)}

    build_result = await backend.run(AgentTask(
        prompt=(
            f"The locked implementation plan is at {effective_plan}. "
            "Read it and build the bundle now."
        ),
        system_prompt=prompts.build_system_prompt(),
        allowed_tools=BUILD_TOOLS,
        mcp_servers=mcp_servers,
        env=env,
        model=model,
        max_budget_usd=max_budget_usd,
        trace_path=run_dir / "agent_trace" / "build.jsonl",
        on_text=on_text,
        on_event=on_event,
    ))

    bundle_dir, zip_path = _find_bundle(run_dir)
    if not build_result.ok or bundle_dir is None:
        return BundleResult(
            ok=False, run_dir=run_dir,
            build=build_result,
            error=build_result.error or "build phase produced no bundle",
        )

    report = None
    if validate:
        from ..checks import validate_bundle_path_async
        report = await validate_bundle_path_async(bundle_dir)

    return BundleResult(
        ok=report.ok if report is not None else True,
        run_dir=run_dir,
        bundle_dir=bundle_dir,
        zip_path=zip_path,
        validation=report,
        build=build_result,
    )


# ---------------------------------------------------------------------------
# Full pipeline — plan + bundle + validate
# ---------------------------------------------------------------------------

async def create_async(
    idea: str,
    *,
    data: str | None = None,
    backend: AgentBackend | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
    on_text: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
    validate: bool = True,
) -> CreateResult:
    """Idea (or proposal text) → validated Codabench bundle, in two phases.

    ``on_event`` (optional) receives structured progress dicts so a caller can
    show step-by-step activity. The pipeline emits phase lifecycle events
    (``{"kind": "phase", ...}`` / ``{"kind": "phase_done", ...}``) and threads
    the same callback into each backend task, which adds tool-call and result
    events. Without it the run is silent, as before.
    """
    backend = _resolve_backend(backend, model)

    def emit(event: dict) -> None:
        if on_event is not None:
            on_event(event)

    # ---- Phase 1: plan -----------------------------------------------------
    emit({"kind": "phase", "phase": "plan", "index": 1, "total": 3,
          "title": "Planning the competition design",
          "detail": "drafting specs/implementation_plan.md (task, data, metric, "
                    "baseline, rules, ethics, schedule)"})
    plan_r = await plan_async(
        idea,
        data=data,
        backend=backend,
        model=model,
        max_budget_usd=max_budget_usd,
        on_text=on_text,
        on_event=on_event,
    )
    emit({"kind": "phase_done", "phase": "plan", "ok": plan_r.ok,
          "num_turns": plan_r.plan.num_turns if plan_r.plan else 0,
          "cost_usd": plan_r.total_cost_usd})

    if not plan_r.ok:
        return CreateResult(
            ok=False, run_dir=plan_r.run_dir,
            plan=plan_r.plan,
            error=plan_r.error,
        )

    # ---- Phase 2: build (fresh session; only the plan carries over) --------
    emit({"kind": "phase", "phase": "build", "index": 2, "total": 3,
          "title": "Building the bundle",
          "detail": "writing competition.yaml, pages, scoring program, baseline "
                    "solution, and data; then linting and zipping"})
    bundle_r = await bundle_async(
        run_dir=plan_r.run_dir,
        backend=backend,
        model=model,
        max_budget_usd=max_budget_usd,
        validate=False,  # validation handled below so we can emit its phase event
        on_text=on_text,
        on_event=on_event,
    )
    emit({"kind": "phase_done", "phase": "build", "ok": bundle_r.ok,
          "num_turns": bundle_r.build.num_turns if bundle_r.build else 0,
          "cost_usd": bundle_r.total_cost_usd})

    if not bundle_r.ok:
        return CreateResult(
            ok=False, run_dir=plan_r.run_dir, plan_path=plan_r.plan_path,
            plan=plan_r.plan, build=bundle_r.build,
            error=bundle_r.error,
        )

    # ---- Phase 3: validate -------------------------------------------------
    report = None
    if validate:
        emit({"kind": "phase", "phase": "validate", "index": 3, "total": 3,
              "title": "Validating the bundle",
              "detail": "running the registered pre-launch checks"})
        from ..checks import validate_bundle_path_async
        report = await validate_bundle_path_async(bundle_r.bundle_dir)
        emit({"kind": "phase_done", "phase": "validate",
              "ok": report.ok if report is not None else True})

    return CreateResult(
        ok=report.ok if report is not None else True,
        run_dir=plan_r.run_dir,
        plan_path=plan_r.plan_path,
        bundle_dir=bundle_r.bundle_dir,
        zip_path=bundle_r.zip_path,
        validation=report,
        plan=plan_r.plan,
        build=bundle_r.build,
    )


# ---------------------------------------------------------------------------
# Sync wrappers
# ---------------------------------------------------------------------------

def plan_competition(idea: str, **kwargs: Any) -> PlanResult:
    """Sync wrapper around :func:`plan_async`."""
    return asyncio.run(plan_async(idea, **kwargs))


def create_bundle(**kwargs: Any) -> BundleResult:
    """Sync wrapper around :func:`bundle_async`."""
    return asyncio.run(bundle_async(**kwargs))


def create(idea: str, **kwargs: Any) -> CreateResult:
    """Sync wrapper around :func:`create_async`."""
    return asyncio.run(create_async(idea, **kwargs))
