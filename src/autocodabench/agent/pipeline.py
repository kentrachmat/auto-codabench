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
from typing import TYPE_CHECKING, Any, Callable

from ..backends.base import AgentBackend, AgentRunResult, AgentTask

if TYPE_CHECKING:
    from .research import ResearchConfig
from ..run_log import SessionInfo, open_run, open_session, record_session_phase
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


def _fs_roots(run_dir: Path | str, data: str | Path | None = None) -> list[str]:
    """The directories a phase is allowed to read from, enforced by code.

    Always the run/session workspace (its plan, bundle, and snapshots); plus any
    user-supplied ``--data`` directory. Everything else — the rest of the repo,
    a ground-truth bundle, the wider filesystem — is off-limits (see
    ``backends.sandbox``).
    """
    roots = [str(Path(run_dir).resolve())]
    if data:
        roots.append(str(Path(data).expanduser().resolve()))
    return roots


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
    ok: bool
    run_dir: Path                       # the session dir (shared phase prefix)
    plan_path: Path | None = None
    bundle_dir: Path | None = None
    zip_path: Path | None = None
    validation: Any = None              # checks.ValidationReport | None
    plan_dir: Path | None = None
    build_dir: Path | None = None
    validate_dir: Path | None = None
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


def _resolve_plan_file(run_path: Path) -> Path | None:
    """Return the saved implementation plan, or None if the agent never wrote it.

    Defense-in-depth for the class of bug behind #32: the plan agent is told to
    save `specs/implementation_plan.md`, but `snapshot_spec` writes the filename
    verbatim, so an agent that drops the extension lands at
    `specs/implementation_plan`. Phase 2 reads `implementation_plan.md`, so if we
    find only the extension-less file we promote it to the canonical name.
    """
    specs = run_path / "specs"
    canonical = specs / "implementation_plan.md"
    if canonical.is_file():
        return canonical
    extensionless = specs / "implementation_plan"
    if extensionless.is_file():
        extensionless.rename(canonical)
        return canonical
    return None


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


def _resolve_backend(backend: AgentBackend | None, model: str | None) -> AgentBackend:
    if backend is not None:
        return backend
    from ..backends import get_claude_backend
    return get_claude_backend(model=model) if model else get_claude_backend()


async def create_async(
    idea: str | None,
    *,
    data: str | None = None,
    pdf: str | Path | None = None,
    backend: AgentBackend | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
    on_text: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
    validate: bool = True,
    session: SessionInfo | None = None,
    research: "ResearchConfig | None" = None,
) -> CreateResult:
    """Idea (or proposal text/PDF) → validated Codabench bundle, in two phases.

    The competition source is either a one-line ``idea``, a ``pdf`` proposal
    (extracted to text here so the planner is backbone-agnostic), or both
    (the idea framing the PDF). At least one must be provided.

    ``on_event`` (optional) receives structured progress dicts so a caller can
    show step-by-step activity. The pipeline emits phase lifecycle events
    (``{"kind": "phase", ...}`` / ``{"kind": "phase_done", ...}``) and threads
    the same callback into each backend task, which adds tool-call and result
    events. Without it the run is silent, as before.
    """
    if idea is None and pdf is None:
        raise ValueError("create_async requires an idea or a pdf (or both)")
    if backend is None:
        from ..backends import get_claude_backend
        backend = get_claude_backend(model=model) if model else get_claude_backend()

    # Extract the PDF proposal to text up front so every backbone — including
    # the OpenAI-compatible one whose file tool is UTF-8-only — receives the
    # same proposal in the plan prompt, not a path only the SDK could read.
    proposal_text = None
    if pdf is not None:
        from ..core.proposal import pdf_to_text
        proposal_text = pdf_to_text(pdf)

    def emit(event: dict) -> None:
        if on_event is not None:
            on_event(event)

    session = session or open_session()
    run_dir = session.path                      # the shared-prefix session dir
    sid_kw = {"session_dir": run_dir, "branch_id": session.branch_id,
              "runtime_id": session.runtime_id}
    emit({"kind": "run_opened", "run_dir": str(run_dir)})

    def _phase_env(phase_dir: Path):
        return ({**os.environ, "AUTOCODABENCH_RUN_DIR": str(phase_dir)},
                _mcp_servers(phase_dir))

    # ---- Phase 1: plan -----------------------------------------------------
    from .research import ResearchConfig, resolve as _resolve_research
    rc = research if research is not None else ResearchConfig()
    research_resolved = _resolve_research(rc, backend=backend)
    p1 = open_run(slug="create", phase="phase1_plan", **sid_kw)
    env, mcp_servers = _phase_env(p1.path)
    # Inject research env (e.g. the Kaggle token) into BOTH the phase env and the
    # autocodabench MCP server subprocess (which serves the first-party Kaggle
    # tools), then add any external research servers (OpenAlex).
    if research_resolved.env:
        env = {**env, **research_resolved.env}
        mcp_servers["autocodabench"]["env"].update(research_resolved.env)
    mcp_servers = {**mcp_servers, **research_resolved.servers}
    emit({"kind": "phase", "phase": "plan", "index": 1, "total": 3,
          "title": "Planning the competition design",
          "detail": "drafting specs/implementation_plan.md (task, data, metric, "
                    "baseline, rules, ethics, schedule)",
          "research": research_resolved.sources})
    plan_prompt = (
        "Open the run with autocodabench_open_run, then produce the "
        "implementation plan for this competition.\n"
    )
    if idea:
        plan_prompt += f"\nCompetition idea / framing:\n\n{idea}\n"
    if proposal_text is not None:
        plan_prompt += (
            "\nThe full competition proposal (extracted from the provided PDF) "
            "follows between the markers. Treat it as the authoritative source; "
            "infer the design sections from it.\n"
            "\n===== BEGIN PROPOSAL =====\n"
            f"{proposal_text}\n"
            "===== END PROPOSAL =====\n"
        )
    if data:
        plan_prompt += (
            f"\nSample data for the competition is available at: {data}\n"
            "Inspect it with the Read/Glob tools before fixing the data design."
        )
    plan_result = await backend.run(AgentTask(
        prompt=plan_prompt,
        system_prompt=prompts.plan_system_prompt(),
        allowed_tools=PLAN_TOOLS + research_resolved.tools,
        mcp_servers=mcp_servers,
        env=env,
        model=model,
        max_budget_usd=max_budget_usd,
        trace_path=p1.path / "agent_trace" / "plan.jsonl",
        on_text=on_text,
        on_event=on_event,
        fs_roots=_fs_roots(run_dir, data),
        allow_web_tools=research_resolved.web_search,
    ))
    emit({"kind": "phase_done", "phase": "plan", "ok": plan_result.ok,
          "num_turns": plan_result.num_turns,
          "cost_usd": plan_result.total_cost_usd})
    plan_path = _resolve_plan_file(p1.path)
    record_session_phase(run_dir, "phase1_plan", {
        "dir": str(p1.path), "ok": bool(plan_result.ok and plan_path is not None),
        "cost_usd": plan_result.total_cost_usd, "num_turns": plan_result.num_turns})
    if not plan_result.ok or plan_path is None:
        return CreateResult(
            ok=False, run_dir=run_dir, plan_dir=p1.path,
            plan=plan_result,
            error=plan_result.error or (
                "plan phase finished without saving specs/implementation_plan.md"),
        )

    # ---- Phase 2: build (fresh session; only the plan carries over) --------
    p2 = open_run(slug="create", phase="phase2_build", **sid_kw)
    env, mcp_servers = _phase_env(p2.path)
    emit({"kind": "phase", "phase": "build", "index": 2, "total": 3,
          "title": "Building the bundle",
          "detail": "writing competition.yaml, pages, scoring program, baseline "
                    "solution, and data; then linting and zipping"})
    build_result = await backend.run(AgentTask(
        prompt=("The locked implementation plan is at "
                f"{plan_path}. Read it and build the bundle now."),
        system_prompt=prompts.build_system_prompt(),
        allowed_tools=BUILD_TOOLS,
        mcp_servers=mcp_servers,
        env=env,
        model=model,
        max_budget_usd=max_budget_usd,
        trace_path=p2.path / "agent_trace" / "build.jsonl",
        on_text=on_text,
        on_event=on_event,
        fs_roots=_fs_roots(run_dir, data),
    ))
    emit({"kind": "phase_done", "phase": "build", "ok": build_result.ok,
          "num_turns": build_result.num_turns,
          "cost_usd": build_result.total_cost_usd})
    bundle_dir, zip_path = _find_bundle(p2.path)
    record_session_phase(run_dir, "phase2_build", {
        "dir": str(p2.path), "ok": bool(build_result.ok and bundle_dir is not None),
        "bundle_dir": str(bundle_dir) if bundle_dir else None,
        "zip_path": str(zip_path) if zip_path else None,
        "cost_usd": build_result.total_cost_usd, "num_turns": build_result.num_turns})
    if not build_result.ok or bundle_dir is None:
        return CreateResult(
            ok=False, run_dir=run_dir, plan_path=plan_path,
            plan_dir=p1.path, build_dir=p2.path,
            plan=plan_result, build=build_result,
            error=build_result.error or "build phase produced no bundle",
        )

    # ---- Phase 3: validate through the check framework ---------------------
    report = None
    validate_dir = None
    if validate:
        p3 = open_run(slug="create", phase="phase3_validate", **sid_kw)
        validate_dir = p3.path
        emit({"kind": "phase", "phase": "validate", "index": 3, "total": 3,
              "title": "Validating the bundle",
              "detail": "running the registered pre-launch checks, including "
                        "executing the bundle (reusing the build phase's runs)"})
        from ..checks import validate_bundle_path_async, load_design_assessment
        report = await validate_bundle_path_async(bundle_dir, execute=True)
        assessment = load_design_assessment(p1.path)  # Phase-1 design scorecard
        try:
            (p3.path / "validation_report.md").write_text(
                report.to_markdown(design_assessment=assessment), encoding="utf-8")
            import json as _json
            (p3.path / "validation_report.json").write_text(
                _json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
        except OSError:
            pass
        record_session_phase(run_dir, "phase3_validate", {
            "dir": str(p3.path), "ok": report.ok,
            "counts": report.counts})
        emit({"kind": "phase_done", "phase": "validate",
              "ok": report.ok if report is not None else True})

    return CreateResult(
        ok=report.ok if report is not None else True,
        run_dir=run_dir,
        plan_path=plan_path,
        bundle_dir=bundle_dir,
        zip_path=zip_path,
        validation=report,
        plan_dir=p1.path,
        build_dir=p2.path,
        validate_dir=validate_dir,
        plan=plan_result,
        build=build_result,
    )


async def plan_async(
    idea: str | None,
    *,
    data: str | None = None,
    pdf: str | Path | None = None,
    backend: AgentBackend | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
    on_text: Callable[[str], None] | None = None,
    on_event: Callable[[dict], None] | None = None,
    research: "ResearchConfig | None" = None,
) -> PlanResult:
    """Run Phase 1 only: produce specs/implementation_plan.md.

    The competition source is a one-line ``idea``, a ``pdf`` proposal
    (extracted to text here so the planner is backbone-agnostic), or both. At
    least one must be given. Creates (or adopts via AUTOCODABENCH_RUN_DIR) a run
    directory and runs the planning agent in it. Returns a PlanResult whose
    ``run_dir`` can be passed directly to ``bundle_async`` to continue the run.
    """
    if idea is None and pdf is None:
        raise ValueError("plan_async requires an idea or a pdf (or both)")
    backend = _resolve_backend(backend, model)

    proposal_text = None
    if pdf is not None:
        from ..core.proposal import pdf_to_text
        proposal_text = pdf_to_text(pdf)

    from .research import ResearchConfig, resolve as _resolve_research
    rc = research if research is not None else ResearchConfig()
    research_resolved = _resolve_research(rc, backend=backend)

    info = open_run(slug="plan")
    run_dir = info.path
    mcp_servers = _mcp_servers(run_dir)
    env = {**os.environ, "AUTOCODABENCH_RUN_DIR": str(run_dir)}
    if research_resolved.env:
        env = {**env, **research_resolved.env}
        mcp_servers["autocodabench"]["env"].update(research_resolved.env)
    mcp_servers = {**mcp_servers, **research_resolved.servers}

    plan_prompt = (
        "Open the run with autocodabench_open_run, then produce the "
        "implementation plan for this competition.\n"
    )
    if idea:
        plan_prompt += f"\nCompetition idea / framing:\n\n{idea}\n"
    if proposal_text is not None:
        plan_prompt += (
            "\nThe full competition proposal (extracted from the provided PDF) "
            "follows between the markers. Treat it as the authoritative source; "
            "infer the design sections from it.\n"
            "\n===== BEGIN PROPOSAL =====\n"
            f"{proposal_text}\n"
            "===== END PROPOSAL =====\n"
        )
    if data:
        plan_prompt += (
            f"\nSample data for the competition is available at: {data}\n"
            "Inspect it with the Read/Glob tools before fixing the data design."
        )

    plan_result = await backend.run(AgentTask(
        prompt=plan_prompt,
        system_prompt=prompts.plan_system_prompt(),
        allowed_tools=PLAN_TOOLS + research_resolved.tools,
        mcp_servers=mcp_servers,
        env=env,
        model=model,
        max_budget_usd=max_budget_usd,
        trace_path=run_dir / "agent_trace" / "plan.jsonl",
        on_text=on_text,
        on_event=on_event,
        fs_roots=_fs_roots(run_dir, data),
        allow_web_tools=research_resolved.web_search,
    ))

    plan_path = _resolve_plan_file(run_dir)
    if not plan_result.ok or plan_path is None:
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
        fs_roots=_fs_roots(run_dir),
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


def plan_competition(idea: str, **kwargs: Any) -> PlanResult:
    """Sync wrapper around :func:`plan_async`."""
    return asyncio.run(plan_async(idea, **kwargs))


def create_bundle(**kwargs: Any) -> BundleResult:
    """Sync wrapper around :func:`bundle_async`."""
    return asyncio.run(bundle_async(**kwargs))


def create(idea: str, **kwargs: Any) -> CreateResult:
    """Sync wrapper around :func:`create_async`."""
    return asyncio.run(create_async(idea, **kwargs))
