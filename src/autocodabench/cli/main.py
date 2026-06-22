"""autocodabench CLI.

Entry points are tiered by their authentication demands, keyless first:

  autocodabench plan "IDEA" [--data PATH]                      # Phase 1 — design → implementation_plan.md
  autocodabench build [plan.md | --run-dir DIR]                # Phase 2 — plan → bundle
  autocodabench validate BUNDLE [--facts F] [--judged]         # Phase 3 / standalone — keyless (unless --judged)
  autocodabench plan-build-validate "IDEA" [--pdf P] [--data PATH]   # all three phases end to end (alias: create)
  autocodabench demo [--out DIR]                               # keyless replay demo
  autocodabench auth status [--no-probe]   # report, pick, and verify via a live turn
  autocodabench checks list

``validate`` validates any bundle — including hand-written ones that never
touched an agent.

``plan`` and ``build`` run the two agentic phases independently. ``plan``
prints its run directory on completion so you can pass it to
``build --run-dir`` to reuse the same run.

The CLI is a thin argument-parsing layer over the library: it contributes
``.env`` loading and the live-auth preflight, and contains no validation
or authoring logic of its own, so everything reachable here is equally
reachable via ``import autocodabench``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

from . import style
from .. import __version__

# Where the human-readable prerequisites (Docker, Node/npx, git + install steps)
# live. Surfaced in Docker-preflight messages so a user without Docker is pointed
# straight at how to install it.
_PREREQS_URL = "https://github.com/ktgiahieu/auto-codabench#prerequisites"


def _require_live_claude_auth(backend_spec: str | None) -> bool:
    """Preflight before starting a live Claude session: if no auth path
    exists, walk the user through one (interactive) or print guidance and
    refuse (non-interactive) — instead of failing opaquely inside the SDK
    mid-run. Non-Claude backends (ollama:/openai:/URL) carry their own
    credentials and are skipped."""
    if backend_spec and backend_spec.split(":", 1)[0] != "claude":
        print(style.info(f"INFO: backend = {backend_spec} (non-Claude; uses its own "
              "credentials / runs locally)", stream=sys.stderr), file=sys.stderr)
        return True
    from ..auth import AuthRequiredError, ensure_live_auth
    try:
        ensure_live_auth()
    except AuthRequiredError as e:
        print(f"\n{e}", file=sys.stderr)
        return False
    except (KeyboardInterrupt, EOFError):
        print("\naborted", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def _add_validate_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("bundle", help="bundle directory or .zip")
    p.add_argument("--facts", help="path to competition_facts.yaml "
                                   "(default: <bundle>/competition_facts.yaml if present)")
    p.add_argument("--judged", action="store_true",
                   help="also run LLM-judged advisory checks (needs an LLM backend)")
    p.add_argument("--execute", "--run", action="store_true", dest="execute",
                   help="(default) run the bundle: execute its baseline through "
                        "scoring and its starting-kit notebook in Docker, reusing "
                        "any runs the build phase already did")
    p.add_argument("--no-execute", "--static", action="store_false", dest="execute",
                   help="schema/static checks only — do not run the bundle")
    p.set_defaults(execute=True)
    p.add_argument("--backend", default=None,
                   help="LLM backend for --judged: claude[:model] (default), "
                        "ollama:<model> (local, keyless), openai:<model>, or an "
                        "OpenAI-compatible URL with '#<model>'")
    p.add_argument("--model", default=None, help="model override for --backend")
    p.add_argument("--assessment", default=None,
                   help="path to a Phase-1 design_assessment.json (or a dir "
                        "containing one); adds the design scorecard to the "
                        "report. Auto-discovered from the bundle dir if omitted.")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="emit the machine-readable report instead of markdown")


def _add_research_args(p: argparse.ArgumentParser) -> None:
    """Phase-1 research toggles — external knowledge sources for the planner.

    On by default (the whole point is to beat the bare LLM); the user sees their
    status in the config banner and can turn the whole capability, or any single
    source, off before the run starts.
    """
    p.add_argument("--no-research", action="store_true",
                   help="disable ALL Phase-1 research sources (OpenAlex, Kaggle, "
                        "web search); plan from the model's own knowledge only")
    p.add_argument("--no-openalex", action="store_true",
                   help="disable the OpenAlex MCP (related competition/benchmark "
                        "papers)")
    p.add_argument("--no-kaggle", action="store_true",
                   help="disable the Kaggle MCP (how similar competitions are hosted)")
    p.add_argument("--no-web-search", action="store_true",
                   help="disable the planner's internet search (WebSearch/WebFetch)")


def _research_from_args(args: argparse.Namespace):
    """Build a ResearchConfig from the CLI flags (default: everything on)."""
    from ..agent.research import ResearchConfig
    if getattr(args, "no_research", False):
        return ResearchConfig.off()
    return ResearchConfig(
        enabled=True,
        openalex=not getattr(args, "no_openalex", False),
        kaggle=not getattr(args, "no_kaggle", False),
        web_search=not getattr(args, "no_web_search", False),
    )


def _print_research_status(config, backend) -> None:
    """Render the resolved research sources in the pre-run config banner."""
    from ..agent.research import resolve as _resolve, describe
    resolved = _resolve(config, backend=backend)
    print("  research:    Phase 1 may consult (toggle with --no-research / "
          "--no-openalex / --no-kaggle / --no-web-search):")
    for line in describe(resolved):
        print(f"               • {line}")
    if not resolved.backend_supported:
        print("               note: this backbone cannot use external MCP/web "
              "tools — research is Claude-only.")


def _maybe_prompt_emulation(preflight: dict, *, execute: bool) -> None:
    """When execution is requested but the image would run under QEMU emulation,
    ask the user whether to proceed (slow) or skip execution. Sets
    ``AUTOCODABENCH_ALLOW_EMULATION`` for this process if they proceed.

    Only prompts on an interactive terminal; non-interactive runs (CI, pipes)
    fall through and the execution checks skip themselves with guidance."""
    if not execute or preflight.get("runs_natively") is not False:
        return
    from ..runner import emulation_allowed
    if emulation_allowed():
        return  # already opted in via the env var
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return  # non-interactive: leave the self-skip + guidance in place
    try:
        ans = input(
            "Execution tests need this image, which would run under slow emulation "
            "(>20 min).\n"
            "Proceed with execution anyway? The other checks run either way. "
            "[y = proceed / N = skip]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans in ("y", "yes"):
        os.environ["AUTOCODABENCH_ALLOW_EMULATION"] = "1"
        print("→ proceeding with emulated execution (this can take >20 minutes)…\n")
    else:
        print("→ skipping execution tests; running the other checks.\n")


def _maybe_prompt_judged(args: argparse.Namespace) -> bool:
    """Whether to run the LLM-as-a-judge checks. ``--judged`` forces yes; a
    non-interactive run or ``--json`` stays off; otherwise ask the user, default
    yes (the LLM checks are the more thorough — but slower — review)."""
    if args.judged:
        return True
    if args.as_json or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        ans = input(
            "Also run LLM-as-a-judge checks for a more thorough review? "
            "(needs LLM backend — adds ~50s/check) "
            "[Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("", "y", "yes")


def _llm_done_line(title: str, results: list) -> str:
    """A one-line result summary for a finished LLM check (shown live)."""
    from ..checks import Status
    st = [r.status for r in results]
    if Status.FINDING in st:
        n = sum(1 for s in st if s == Status.FINDING)
        return f"⚠️  {title} — {n} finding(s)"
    if Status.ATTESTATION_REQUIRED in st:
        return f"📋 {title} — needs your review"
    if st and all(s == Status.SKIPPED for s in st):
        return f"•  {title} — skipped"
    if Status.PASS in st:
        return f"✅ {title} — no issues"
    return f"•  {title}"


def _make_llm_progress_cb(ui):
    """Drive the live spinner/log from validate's per-check progress events."""
    def cb(ev: dict) -> None:
        kind = ev.get("event")
        if kind == "phase":
            ui.line("")
            ui.line(f"  Running {ev.get('title')} (the LLM reads the bundle)…")
        elif kind == "start":
            ui.set_status(ev.get("title", "thinking"))
        elif kind == "done":
            ui.line("  " + _llm_done_line(ev.get("title", "check"),
                                          ev.get("results") or []))
    return cb


def _cmd_validate(args: argparse.Namespace) -> int:
    from ..checks import validate_bundle_path

    # Docker runtime preflight. When executing (the default) Docker is a real
    # prerequisite for the run checks; with --no-execute it is informational
    # (static validation does not itself run Docker). Suppressed for --json.
    if not args.as_json:
        from ..runner import docker_image_overridden
        override = docker_image_overridden()
        declared = _bundle_declared_image(args.bundle)
        if override:
            image = override
            src = ("AUTOCODABENCH_DOCKER_IMAGE_OVERRIDE — a local substitute, "
                   "not the bundle's declared image")
        else:
            image = declared or _default_docker_image()
            src = ("declared in competition.yaml" if declared
                   else "bundle declares none — Codabench/autocodabench default")
        if args.execute:
            note = (f"Codabench will run this bundle inside the image above ({src}). "
                    "This validation will run the bundle's baseline and starting-kit "
                    "in it (reusing the build phase's runs when unchanged); pass "
                    "--no-execute for static checks only.")
        else:
            note = (f"Codabench will run this bundle inside the image above ({src}). "
                    "Static validation only (--no-execute): the bundle is not run.")
        # When executing, this surfaces a loud, link-bearing warning if Docker
        # isn't reachable — but does NOT block: static checks still run and the
        # runtime checks report the missing-Docker error, so the user is informed
        # and can choose to install Docker (see the link) and re-run, or proceed.
        p = _print_docker_preflight(image, required=args.execute, note=note)
        print()
        # If execution would run under slow QEMU emulation, ask rather than
        # silently skip — the user may still want the (slow) execution evidence,
        # and the other checks run either way.
        _maybe_prompt_emulation(p, execute=args.execute)

    # Offer the (slower, more thorough) LLM-as-a-judge checks — default yes when
    # interactive. They need a live Claude session; if auth isn't available we
    # continue with the deterministic + execution checks rather than abort.
    judged = _maybe_prompt_judged(args)
    if judged and not _require_live_claude_auth(args.backend):
        print("  → continuing without LLM checks (the other checks still run).\n")
        judged = False
    backend = None
    if judged and (args.backend or args.model):
        from ..backends import resolve_backend
        backend = resolve_backend(args.backend, model=args.model)

    # When executing, give this standalone run its own session dir
    # (phase3_validate) so re-run logs and the saved report land in one
    # organized place — a separate prefix from any `create` session, so the
    # two provenances never get confused.
    run_dir = None
    if args.execute:
        from ..run_log import open_run, open_session
        session = open_session(kind="validate")
        run_dir = open_run(slug="validate", phase="phase3_validate",
                           session_dir=session.path).path

    # When LLM checks run on a terminal, show the same live spinner + per-check
    # progress as the agent phases (so the CLI isn't idle for 30–60s). Otherwise
    # the plain synchronous path.
    if judged and not args.as_json and sys.stdout.isatty():
        from ..checks import validate_bundle_path_async
        from .progress import ProgressUI
        with ProgressUI(verb="Running LLM checks") as ui:
            ui.set_status("running checks")
            report = asyncio.run(validate_bundle_path_async(
                args.bundle, facts_path=args.facts, judged=True,
                execute=args.execute, backend=backend,
                on_check=_make_llm_progress_cb(ui)))
    else:
        report = validate_bundle_path(args.bundle, facts_path=args.facts,
                                      judged=judged, execute=args.execute,
                                      backend=backend)

    # Optional Phase-1 design scorecard: explicit --assessment, else look in
    # the bundle dir (and its specs/). Absent/malformed → omitted gracefully.
    from ..checks import load_design_assessment
    assessment = load_design_assessment(args.assessment or args.bundle)
    md = report.to_markdown(design_assessment=assessment)

    if run_dir is not None:
        try:
            (run_dir / "validation_report.md").write_text(md, encoding="utf-8")
            (run_dir / "validation_report.json").write_text(
                json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
        except OSError:
            pass

    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        # The terminal gets a wrapped, aligned plain-text rendering; the markdown
        # (`md`, saved above and rendered by the web UI) is unchanged.
        from ..checks import render_report_terminal
        print(render_report_terminal(report, design_assessment=assessment))
        if run_dir is not None:
            print(f"\nReport + run logs: {run_dir}")
    return 0 if report.ok else 1


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------

def _cmd_demo(args: argparse.Namespace) -> int:
    from importlib import resources

    from ..backends.base import AgentTask
    from ..backends.replay import ReplayBackend
    from ..checks import validate_bundle_path

    fixture = args.fixture or str(
        resources.files("autocodabench.backends") / "fixtures" / "demo_bundle.jsonl")
    out_dir = Path(args.out).resolve()
    print(f"Replaying recorded agent run → {out_dir}  (no LLM, no keys)\n")

    backend = ReplayBackend(fixture, out_dir=out_dir)
    result = asyncio.run(backend.run(
        AgentTask(prompt="demo", on_text=lambda t: print(f"  • {t}"))))
    print()
    print(result.final_text)
    if not result.ok:
        print(f"\nreplay failed: {result.error}", file=sys.stderr)
        return 1

    bundles = [p for p in out_dir.iterdir()
               if p.is_dir() and (p / "competition.yaml").is_file()]
    if bundles:
        print("\nRunning the validator over the rebuilt bundle:\n")
        report = validate_bundle_path(bundles[0])
        from ..checks import render_report_terminal
        print(render_report_terminal(report))
        return 0 if report.ok else 1
    return 0


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

# --- create: progress rendering -------------------------------------------

def _make_progress_renderer(*, debug: bool = False):
    """Return an `on_event` callback that renders pipeline progress.

    Thin shim over :class:`autocodabench.cli.progress.ProgressUI` — the live
    spinner animates only inside the ``with`` block (see
    :func:`_run_with_progress`); used bare, this is just the per-step renderer.
    """
    from .progress import ProgressUI
    return ProgressUI(debug=debug).on_event


def _run_with_progress(make_coro, *, debug: bool, quiet: bool):
    """Run an agent pipeline coroutine while showing live progress.

    ``make_coro(on_event)`` builds the awaitable given the progress callback (or
    ``None`` when quiet). The animated status line only moves on a TTY;
    redirected output falls back to plain, scrollable lines. The context manager
    restores the cursor even if the run raises or is interrupted.
    """
    from .progress import ProgressUI
    if quiet:
        print("\n(running quietly — omit --quiet to see step-by-step progress)\n")
        return asyncio.run(make_coro(None))
    with ProgressUI(debug=debug) as ui:
        return asyncio.run(make_coro(ui.on_event))


def _bundle_docker_image(bundle_dir) -> str | None:
    """Read the final `docker_image` a built bundle declares. After the build
    phase's self-validation loop this is the image the baseline actually
    passed under — exactly what Codabench will run — so it is worth surfacing."""
    if not bundle_dir:
        return None
    yaml_path = Path(bundle_dir) / "competition.yaml"
    if not yaml_path.is_file():
        return None
    try:
        import yaml
        comp = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        image = comp.get("docker_image")
        return image.strip() if isinstance(image, str) and image.strip() else None
    except Exception:
        return None


def _default_docker_image() -> str:
    """The CPU base image a bundle gets when it declares none — what `create`
    will start from and what Codabench falls back to."""
    from ..runner.execution import _DEFAULT_DOCKER_IMAGE
    return _DEFAULT_DOCKER_IMAGE


def _bundle_declared_image(bundle_path) -> str | None:
    """The `docker_image` a bundle declares — works for a directory or a .zip."""
    p = Path(bundle_path)
    text = None
    if p.is_dir():
        yp = p / "competition.yaml"
        if yp.is_file():
            text = yp.read_text(encoding="utf-8", errors="replace")
    elif p.is_file() and p.suffix == ".zip":
        import zipfile
        try:
            with zipfile.ZipFile(p) as z:
                for name in z.namelist():
                    if name.endswith("competition.yaml") and name.count("/") <= 1:
                        text = z.read(name).decode("utf-8", "replace")
                        break
        except (zipfile.BadZipFile, OSError):
            return None
    if not text:
        return None
    try:
        import yaml
        comp = yaml.safe_load(text) or {}
        img = comp.get("docker_image")
        return img.strip() if isinstance(img, str) and img.strip() else None
    except Exception:
        return None


def _print_docker_preflight(image, *, required: bool, note: str | None = None) -> dict:
    """Report the Docker runtime up front: which image will run, its CPU
    architecture versus the host (native vs slow QEMU emulation), and whether
    Docker is installed and running.

    `required=True` (the run phases — `plan-build-validate`) makes a missing daemon a loud
    prerequisite warning; `required=False` (static `validate`) keeps it
    informational. Returns the underlying preflight dict.
    """
    from ..runner import docker_preflight

    p = docker_preflight(image)
    d = p["docker"]
    host, host_os = p["host_arch"], p["host_os"]
    warn = []  # lines to also echo to stderr when this is a hard prerequisite

    print("Docker runtime")
    if not d["cli_installed"]:
        print("  status:    ✗ Docker is not installed")
        warn.append("Docker is not installed. autocodabench executes every bundle "
                    "inside Docker — install Docker Desktop "
                    "(https://docs.docker.com/get-docker/) and start it before running.")
    elif not d["daemon_running"]:
        print("  status:    ✗ Docker is installed but its daemon is not reachable")
        warn.append("The Docker daemon is not running. Start Docker Desktop "
                    "(or `colima start`), then retry.")
    else:
        ver = d["server_version"] or "?"
        print(f"  status:    ✓ Docker {ver} · daemon running ({d['os']}/{d['arch']})")

    print(f"  image:     {image}")
    if note:
        for line in textwrap.wrap(note, width=66):
            print(f"             {line}")

    if p["runs_natively"] is True:
        avail = "/".join(p["image_available_arches"])
        tag = f"multi-arch ({avail})" if p["image_multi_arch"] else avail
        src = "" if p["image_present_locally"] else " — Docker will pull it on first run"
        print(f"  image fit: {tag} includes {host} → runs natively{src}")
    elif p["runs_natively"] is False:
        from ..runner import emulation_allowed
        only = "/".join(p["image_available_arches"])
        forced = emulation_allowed()
        print(f"  image fit: ⚠ {only}-only — host is {host}; would run under QEMU emulation (slow)")
        if required and not forced:
            # Execution would run under emulation (>20 min). The caller (validate)
            # asks the user whether to proceed or skip; here we just lay out the
            # cost and the faster alternative.
            print(f"             ⚠ execution would run under slow emulation — an "
                  f"emulated baseline +")
            print(f"             starting-kit run takes >20 min. For a fast run, use a "
                  f"native image:")
            print(f"               export AUTOCODABENCH_DOCKER_IMAGE_OVERRIDE=codalab/codalab-legacy:py312")
            print(f"             then re-run; or pass --no-execute for static checks only.")
        else:
            print(f"             For a native {host} image, substitute one (wins over the")
            print(f"             bundle's declared image):")
            print(f"               export AUTOCODABENCH_DOCKER_IMAGE_OVERRIDE=codalab/codalab-legacy:py312")
            if forced:
                print(f"             AUTOCODABENCH_ALLOW_EMULATION is set — the slow emulated "
                      f"run will proceed.")
    else:  # architecture undetermined (not pulled, registry unreachable/private)
        err = (p["image_error"] or "").lower()
        if "denied" in err or "unauthorized" in err or "not found" in err:
            reason = "not in the local image store and not published to a registry"
        else:
            reason = p["image_error"] or "architecture undetermined"
        print(f"  image fit: ? undetermined — {reason}")
        if d["daemon_running"]:
            print(f"             Not in the local store and no registry manifest. Build the")
            print(f"             autocodabench base image locally (docker/build_and_push.sh,")
            print(f"             no --push), or set AUTOCODABENCH_DOCKER_IMAGE to a pulled image")
            print(f"             such as codalab/codalab-legacy:py312 (native {host}).")

    print(f"  host:      {host} ({host_os})")

    if warn and required:
        print()
        for w in warn:
            print(f"WARNING: {w}", file=sys.stderr)
        print(f"WARNING: prerequisites & install steps → {_PREREQS_URL}", file=sys.stderr)
    return p


def _print_create_config(*, idea, pdf, backend_name, auth_label, model, run_dir,
                         data, max_budget_usd, validate, verbosity) -> None:
    """Show the full effective configuration before spending anything, so the
    run is never an opaque idle."""
    budget = f"${max_budget_usd:.2f} per phase" if max_budget_usd else "no cap"
    print(style.heading("plan-build-validate — configuration"))
    print(style.field("idea", textwrap.shorten(idea, width=72) if idea else "(none)"))
    print(style.field("proposal", pdf or "(none)"))
    print(style.field("backend", f"{backend_name}  ({auth_label})"))
    print(style.field("model", model))
    print(style.field("output dir", run_dir))
    print(style.field("sample data", data or "(none)"))
    print(style.field("cost cap", budget))
    print(style.field("output mode", verbosity))
    print(style.field("pipeline", "1) plan → specs/implementation_plan.md"))
    print(style.cont("2) build → bundle + zip (via the MCP tools)"))
    print(style.cont(f"3) validate → {'registered checks' if validate else 'skipped'}"))
    print(style.field("artifacts", "plan, bundle, zip, and a full tool-call audit trail"))
    print(style.cont("land under the output dir above."))


def _cmd_create(args: argparse.Namespace) -> int:
    from ..agent.pipeline import create_async
    from ..run_log import open_session

    if not args.idea and not args.pdf:
        print("plan-build-validate needs a competition source: pass an idea "
              "argument or --pdf <proposal.pdf> (or both).", file=sys.stderr)
        return 2
    if args.pdf and not Path(args.pdf).expanduser().is_file():
        print(f"--pdf: not a file: {args.pdf}", file=sys.stderr)
        return 2

    # The pipeline's build phase self-validates inside Docker — fail fast on a
    # missing daemon now, before the plan phase spends any model budget.
    if not _require_docker():
        return 2

    if not _require_live_claude_auth(args.backend):
        return 2

    # ---- Resolve where output should go (ask if not provided) --------------
    from ..core.config import runs_root
    out = args.out
    if out is None and sys.stdin.isatty():
        default_root = runs_root()
        try:
            ans = input(
                f"\nWhere should this run's output go?\n"
                f"  [Enter] = default ({default_root})\n"
                f"  or type a directory: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naborted", file=sys.stderr)
            return 130
        out = ans or None
    if out:
        os.environ["AUTOCODABENCH_RUNS_ROOT"] = str(Path(out).expanduser().resolve())

    # ---- Construct the backend up front so we can show the real model ------
    if args.backend:
        from ..backends import resolve_backend
        backend = resolve_backend(args.backend, model=args.model)
    else:
        from ..backends import get_claude_backend
        backend = (get_claude_backend(model=args.model) if args.model
                   else get_claude_backend())
    model_shown = getattr(backend, "model", args.model or "(backend default)")
    if backend.name == "claude":
        from ..auth import resolve_auth
        auth_label = {
            "subscription": "subscription login",
            "api_key": "ANTHROPIC_API_KEY",
            "none": "no auth configured",
        }.get(resolve_auth().effective, "unknown")
    else:
        auth_label = "own credentials"

    # ---- Resolve the verbosity tier (quiet | default | debug) --------------
    debug = bool(args.debug or args.verbose)
    if args.quiet:
        verbosity = "quiet (final summary only)"
    elif debug:
        verbosity = "debug (full developer trace)"
    else:
        verbosity = "summary (user-oriented progress; pass --debug for the full trace)"

    # ---- Create the session dir now so the config banner can name it -------
    # (per-phase subdirs phase1_plan / phase2_build / phase3_validate land
    # under this shared prefix.)
    session = open_session()
    run_dir = session.path

    research = _research_from_args(args)

    print()
    _print_create_config(
        idea=args.idea, pdf=args.pdf, backend_name=backend.name,
        auth_label=auth_label, model=model_shown, run_dir=run_dir, data=args.data,
        max_budget_usd=args.max_budget_usd, validate=not args.no_validate,
        verbosity=verbosity)
    _print_research_status(research, backend)

    # Docker runtime preflight — a hard prerequisite for `plan-build-validate`: the build
    # phase self-validates the bundle by running its baseline and starting-kit
    # notebook inside Docker. Show the starting image (the build may change it;
    # the final image is reported at the end), its arch fit, and daemon status.
    print()
    _print_docker_preflight(
        _default_docker_image(), required=True,
        note="Starting image. The build phase may change docker_image to make "
             "the bundle pass; the final image is reported when the run finishes.")

    if debug:
        print("\n  Note: --debug prints the full developer trace — every tool "
              "call, raw\n        tool output, and the agent's internal "
              "reasoning. It is intended for\n        diagnosing the pipeline, "
              "not routine use. Omit --debug for a concise,\n        "
              "user-oriented summary.", file=sys.stderr)

    # ---- Confirm before spending (tty only; --yes skips) -------------------
    if sys.stdin.isatty() and not args.yes:
        def _abort() -> int:
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)  # leave nothing behind
            os.environ.pop("AUTOCODABENCH_RUN_DIR", None)
            print("aborted (no run created)", file=sys.stderr)
            return 130
        try:
            if input("\n" + style.confirm("Start the run?")).strip().lower() not in ("", "y", "yes"):
                return _abort()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return _abort()

    result = _run_with_progress(
        lambda on_event: create_async(
            args.idea,
            data=args.data,
            pdf=args.pdf,
            backend=backend,
            model=args.model,
            max_budget_usd=args.max_budget_usd,
            on_event=on_event,
            validate=not args.no_validate,
            session=session,
            research=research,
        ),
        debug=debug, quiet=args.quiet)

    print("\n" + "═" * 60)
    print("Done.")
    print(f"  session:   {result.run_dir}  (phase1_plan / phase2_build / phase3_validate)")
    print(f"  plan:      {result.plan_path}")
    print(f"  bundle:    {result.bundle_dir}")
    print(f"  zip:       {result.zip_path}")
    if result.validate_dir:
        print(f"  report:    {Path(result.validate_dir) / 'validation_report.md'}")
    image = _bundle_docker_image(result.bundle_dir)
    if image:
        print(f"  docker:    {image}  (declared in competition.yaml; what "
              "Codabench will run)")
    updated_plan = (Path(result.build_dir) / "specs" / "updated_implementation_plan.md"
                    if result.build_dir else None)
    if updated_plan and updated_plan.is_file():
        print(f"  changes:   {updated_plan}  (deviations from the original plan)")
    print(f"  cost:      ${result.total_cost_usd:.2f}")
    if result.validation is not None:
        print()
        from ..checks import render_report_terminal
        print(render_report_terminal(result.validation))
    if not result.ok:
        print(f"\ncreate failed: {result.error}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# plan  (Phase 1 standalone)
# ---------------------------------------------------------------------------

def _print_plan_config(*, idea, pdf, backend_name, auth_label, model, run_dir,
                        data, max_budget_usd, verbosity) -> None:
    budget = f"${max_budget_usd:.2f}" if max_budget_usd else "no cap"
    print(style.heading("plan — configuration"))
    print(style.field("idea", textwrap.shorten(idea, width=72) if idea else "(none)"))
    print(style.field("proposal", pdf or "(none)"))
    print(style.field("backend", f"{backend_name}  ({auth_label})"))
    print(style.field("model", model))
    print(style.field("output/run dir", run_dir))
    print(style.field("sample data", data or "(none)"))
    print(style.field("cost cap", budget))
    print(style.field("output mode", verbosity))
    print(style.field("output", "specs/implementation_plan.md inside the run dir above"))


def _cmd_plan(args: argparse.Namespace) -> int:
    from ..agent.pipeline import plan_async
    from ..run_log import open_run

    # Cheap source validation before the live-auth probe (keyless-testable).
    if not args.idea and not args.pdf:
        print("plan needs a competition source: pass an idea argument or "
              "--pdf <proposal.pdf> (or both).", file=sys.stderr)
        return 2
    if args.pdf and not Path(args.pdf).expanduser().is_file():
        print(f"--pdf: not a file: {args.pdf}", file=sys.stderr)
        return 2

    if not _require_live_claude_auth(args.backend):
        return 2

    if args.backend:
        from ..backends import resolve_backend
        backend = resolve_backend(args.backend, model=args.model)
    else:
        from ..backends import get_claude_backend
        backend = (get_claude_backend(model=args.model) if args.model
                   else get_claude_backend())
    model_shown = getattr(backend, "model", args.model or "(backend default)")
    if backend.name == "claude":
        from ..auth import resolve_auth
        auth_label = {
            "subscription": "subscription login",
            "api_key": "ANTHROPIC_API_KEY",
            "none": "no auth configured",
        }.get(resolve_auth().effective, "unknown")
    else:
        auth_label = "own credentials"

    debug = bool(args.debug)
    verbosity = ("quiet (final summary only)" if args.quiet
                 else "debug (full developer trace)" if debug
                 else "summary (user-oriented progress)")

    # Pre-create the run dir so the config banner can show it.
    run_dir = open_run(slug="plan").path

    research = _research_from_args(args)

    print()
    _print_plan_config(
        idea=args.idea, pdf=args.pdf, backend_name=backend.name, auth_label=auth_label,
        model=model_shown, run_dir=run_dir, data=args.data,
        max_budget_usd=args.max_budget_usd, verbosity=verbosity)
    _print_research_status(research, backend)

    if sys.stdin.isatty() and not args.yes:
        def _abort() -> int:
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)
            os.environ.pop("AUTOCODABENCH_RUN_DIR", None)
            print("aborted (no run created)", file=sys.stderr)
            return 130
        try:
            if input("\n" + style.confirm("Start planning?")).strip().lower() not in ("", "y", "yes"):
                return _abort()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return _abort()

    result = _run_with_progress(
        lambda on_event: plan_async(
            args.idea,
            data=args.data,
            pdf=args.pdf,
            backend=backend,
            model=args.model,
            max_budget_usd=args.max_budget_usd,
            on_event=on_event,
            research=research,
        ),
        debug=debug, quiet=args.quiet)

    print("\n" + "═" * 60)
    print("Done." if result.ok else "Planning failed.")
    print(f"  run dir:  {result.run_dir}")
    print(f"  plan:     {result.plan_path}")
    print(f"  cost:     ${result.total_cost_usd:.2f}")
    if result.ok:
        print(
            f"\n  → To build the bundle from this plan:\n"
            f"    autocodabench build --run-dir {result.run_dir}"
        )
    if not result.ok:
        print(f"\nplan failed: {result.error}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# build  (Phase 2 standalone)
# ---------------------------------------------------------------------------

def _print_bundle_config(*, plan_source, backend_name, auth_label, model,
                          run_dir, max_budget_usd, validate, verbosity) -> None:
    budget = f"${max_budget_usd:.2f}" if max_budget_usd else "no cap"
    print(style.heading("build — configuration"))
    print(style.field("plan", plan_source))
    print(style.field("backend", f"{backend_name}  ({auth_label})"))
    print(style.field("model", model))
    print(style.field("output/run dir", run_dir))
    print(style.field("cost cap", budget))
    print(style.field("output mode", verbosity))
    print(style.field("pipeline", "1) build → bundle + zip (via the MCP tools)"))
    print(style.cont(f"2) validate → {'registered checks' if validate else 'skipped'}"))
    print(style.field("artifacts", "bundle, zip, and a full tool-call audit trail"))
    print(style.cont("land under the output/run dir above."))


def _cmd_build(args: argparse.Namespace) -> int:
    from ..agent.pipeline import bundle_async
    from ..run_log import open_run

    # Resolve the two mutually-exclusive input modes first — cheap argument
    # validation should not require a live-auth probe to reject malformed input.
    run_dir_arg = Path(args.run_dir).resolve() if args.run_dir else None
    plan_arg    = Path(args.plan).resolve()    if args.plan    else None

    if run_dir_arg is not None and plan_arg is not None:
        print("error: pass either a plan file or --run-dir, not both",
              file=sys.stderr)
        return 2
    if run_dir_arg is None and plan_arg is None:
        print("error: pass a plan file or --run-dir <path>",
              file=sys.stderr)
        return 2

    if run_dir_arg is not None:
        if not run_dir_arg.is_dir():
            print(f"error: run dir not found: {run_dir_arg}", file=sys.stderr)
            return 2
        spec = run_dir_arg / "specs" / "implementation_plan.md"
        if not spec.is_file():
            print(f"error: no specs/implementation_plan.md in {run_dir_arg}",
                  file=sys.stderr)
            return 2
        run_dir      = run_dir_arg
        plan_source  = str(spec)
        bundle_kwargs = {"run_dir": run_dir}
    else:
        if not plan_arg.is_file():
            print(f"error: plan file not found: {plan_arg}", file=sys.stderr)
            return 2

    # Phase 2 self-validates by running the bundle inside Docker — catch a
    # missing daemon now, before any model spend, rather than mid-build.
    if not _require_docker():
        return 2

    if not _require_live_claude_auth(args.backend):
        return 2

    if plan_arg is not None and run_dir_arg is None:
        # Pre-create a fresh run dir so the config banner can name it.
        run_dir      = open_run(slug="bundle").path
        plan_source  = str(plan_arg)
        bundle_kwargs = {"plan_path": plan_arg}

    if args.backend:
        from ..backends import resolve_backend
        backend = resolve_backend(args.backend, model=args.model)
    else:
        from ..backends import get_claude_backend
        backend = (get_claude_backend(model=args.model) if args.model
                   else get_claude_backend())
    model_shown = getattr(backend, "model", args.model or "(backend default)")
    if backend.name == "claude":
        from ..auth import resolve_auth
        auth_label = {
            "subscription": "subscription login",
            "api_key": "ANTHROPIC_API_KEY",
            "none": "no auth configured",
        }.get(resolve_auth().effective, "unknown")
    else:
        auth_label = "own credentials"

    debug = bool(args.debug)
    verbosity = ("quiet (final summary only)" if args.quiet
                 else "debug (full developer trace)" if debug
                 else "summary (user-oriented progress)")

    print()
    _print_bundle_config(
        plan_source=plan_source, backend_name=backend.name, auth_label=auth_label,
        model=model_shown, run_dir=run_dir, max_budget_usd=args.max_budget_usd,
        validate=not args.no_validate, verbosity=verbosity)

    if sys.stdin.isatty() and not args.yes:
        def _abort() -> int:
            if plan_arg is not None:
                import shutil
                shutil.rmtree(run_dir, ignore_errors=True)
                os.environ.pop("AUTOCODABENCH_RUN_DIR", None)
            print("aborted", file=sys.stderr)
            return 130
        try:
            if input("\n" + style.confirm("Start building?")).strip().lower() not in ("", "y", "yes"):
                return _abort()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return _abort()

    result = _run_with_progress(
        lambda on_event: bundle_async(
            **bundle_kwargs,
            backend=backend,
            model=args.model,
            max_budget_usd=args.max_budget_usd,
            validate=not args.no_validate,
            on_event=on_event,
        ),
        debug=debug, quiet=args.quiet)

    print("\n" + "═" * 60)
    print("Done." if result.ok else "Bundle creation failed.")
    print(f"  run dir:   {result.run_dir}")
    print(f"  bundle:    {result.bundle_dir}")
    print(f"  zip:       {result.zip_path}")
    image = _bundle_docker_image(result.bundle_dir)
    if image:
        print(f"  docker:    {image}  (declared in competition.yaml; what "
              "Codabench will run)")
    print(f"  cost:      ${result.total_cost_usd:.2f}")
    if result.validation is not None:
        print()
        from ..checks import render_report_terminal
        print(render_report_terminal(result.validation))
    if not result.ok:
        print(f"\nbuild failed: {result.error}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def _probe_active_auth(model: str | None) -> int:
    """Realize the current auth preference and actually authenticate the agent
    SDK with it — a one-turn live session — so the report reflects whether
    auth *works*, not merely whether a credential file exists on disk.
    Returns a process exit code (0 = authenticated, 1 = no auth / failed)."""
    from ..auth import apply_auth_preference, probe

    status = apply_auth_preference()  # honor the preference before probing
    if status.effective == "none":
        print("\nNo Claude auth is configured, so there is nothing to verify. "
              "Set one with `autocodabench auth use <subscription|api_key>`.",
              file=sys.stderr)
        return 1
    print("\n" + style.info(status.info_line()))
    print(f"Verifying the agent SDK can authenticate via {status.effective} "
          "(one minimal live turn)…")
    outcome = asyncio.run(probe(model=model))
    if outcome["ok"]:
        print(f"✓ authenticated — the agent SDK signed in via {status.effective} "
              "and replied. Setup works.")
        return 0
    print(f"✗ authentication FAILED via {status.effective}: "
          f"{outcome.get('error') or outcome.get('status')}", file=sys.stderr)
    print("  The credential was detected but the agent SDK could not "
          "authenticate with it. For a subscription, re-run `claude` then "
          "`/login`; for a key, confirm it is valid and unexpired.",
          file=sys.stderr)
    return 1


def _cmd_auth(args: argparse.Namespace) -> int:
    from ..auth import (
        AUTH_MODES,
        choose_auth_interactively,
        describe_codabench_credentials,
        resolve_auth,
        set_auth_preference,
    )

    # `autocodabench auth use <mode>` — set the preference, then verify it.
    if args.action == "use":
        mode = (args.mode or "").strip().lower()
        if mode not in AUTH_MODES:
            print(f"mode must be one of {', '.join(AUTH_MODES)}", file=sys.stderr)
            return 2
        path = set_auth_preference(mode)
        status = resolve_auth()
        # Fill the gap the chosen mode needs (tty only): paste a key, or launch
        # Claude Code's sign-in so the subscription is set up in place.
        if mode == "api_key" and not status.api_key_set and sys.stdin.isatty():
            from ..auth import _prompt_and_store_api_key
            _prompt_and_store_api_key()
            status = resolve_auth()
        elif (mode == "subscription" and not status.subscription_login_detected
              and sys.stdin.isatty()):
            from ..auth import launch_claude_login
            launch_claude_login()
            status = resolve_auth()
        print(f"auth preference set to '{mode}' ({path})")
        print(status.describe())
        # Always try to authenticate with the requested preference — the point
        # of choosing it is to confirm it actually works.
        if not args.no_probe:
            return _probe_active_auth(args.model)
        return 0

    # `autocodabench auth status`
    status = resolve_auth()
    print(status.describe())
    print()
    print(describe_codabench_credentials())

    # Interactive picker so the user can switch without editing files.
    if not args.no_pick and sys.stdin.isatty():
        try:
            changed = choose_auth_interactively(status)
        except (KeyboardInterrupt, EOFError):
            changed = None
            print(file=sys.stderr)
        if changed is not None:
            print()
            print(changed.describe())
            status = changed

    # By default, verify the resolved preference end to end rather than just
    # reporting on-disk detection. `--no-probe` keeps it static (offline/CI).
    if not args.no_probe:
        return _probe_active_auth(args.model)
    return 0


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------

def _cmd_checks(args: argparse.Namespace) -> int:
    from ..checks import checklist_coverage, render_checks_catalog_terminal

    if args.as_json:
        print(json.dumps(checklist_coverage(), indent=2))
        return 0
    # One box table per validation type; user-friendly (no internal ids), with
    # an LLM-as-a-judge column and a clickable Sources footer.
    print(render_checks_catalog_terminal())
    return 0


# ---------------------------------------------------------------------------
# doctor — system prerequisites (the bits pip cannot install)
# ---------------------------------------------------------------------------

def _cmd_doctor(args: argparse.Namespace) -> int:
    from ..preflight import render_report, system_report

    checks = system_report()
    if getattr(args, "as_json", False):
        print(json.dumps([c.as_dict() for c in checks], indent=2))
    else:
        print("autocodabench system check — prerequisites pip cannot install:\n")
        print(render_report(checks))
        print()

    failed_required = [c for c in checks if c.required and c.status == "fail"]
    optional_missing = [c for c in checks if not c.required and c.status != "ok"]
    if args.as_json:
        return 1 if failed_required else 0
    if failed_required:
        print(f"✗ {len(failed_required)} required prerequisite(s) missing — "
              "phases 2-3 will not run until fixed.")
        return 1
    if optional_missing:
        print(f"✓ required prerequisites OK; {len(optional_missing)} optional "
              "item(s) missing (see ⚠️ above).")
    else:
        print("✓ all prerequisites satisfied.")
    return 0


def _require_docker() -> bool:
    """Fail fast — before any model spend — when Docker isn't ready for phase 2/3.

    Used by `build` / `plan-build-validate`, where the build phase self-validates
    inside Docker and proceeding without it only wastes model budget. Returns
    True if the command may proceed. (`validate` deliberately does NOT call this —
    its static checks are useful without Docker; it only warns.)
    """
    from ..preflight import check_docker

    c = check_docker()
    if c.status == "fail":
        print(f"\n{c.glyph} Docker is required for this command but is unavailable:",
              file=sys.stderr)
        print(f"   {c.detail}", file=sys.stderr)
        print(f"   fix: {c.hint}", file=sys.stderr)
        print(f"   prerequisites & install steps → {_PREREQS_URL}", file=sys.stderr)
        print("   (run `autocodabench doctor` to check all prerequisites.)", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autocodabench",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Agentic authoring and pre-launch validation of Codabench "
            "competition bundles.\n\n"
            "Authoring runs in three phases — plan → build → validate — each "
            "runnable\non its own, or all at once with `plan-build-validate`."
        ),
        epilog=(
            "pipeline phases (run a phase on its own, or chain all three with `plan-build-validate`):\n"
            "  plan                 Phase 1 · idea/PDF → specs/implementation_plan.md   (needs an LLM backend)\n"
            "  build                Phase 2 · a plan → competition bundle + .zip        (needs an LLM backend)\n"
            "  validate             Phase 3 · run a bundle's pre-launch checks          (keyless; --judged adds an LLM)\n"
            "  plan-build-validate  all three phases end to end (alias: create)         (needs an LLM backend)\n"
            "\n"
            "utilities:\n"
            "  demo       rebuild + validate the shipped demo bundle, no keys  (keyless)\n"
            "  checks     list the registered checks by tier                   (keyless)\n"
            "  doctor     check system prerequisites (Docker, Node/npx, git)   (keyless)\n"
            "  auth       report / choose / verify the Claude auth path\n"
            "\n"
            "backends: every agentic command accepts --backend — claude[:model] (default),\n"
            "          ollama:<model>, openai:<model>, or an OpenAI-compatible URL with '#<model>'.\n"
            "\n"
            "getting started:\n"
            "  autocodabench plan-build-validate \"<idea>\" [--pdf proposal.pdf] [--data DIR]   # the whole pipeline\n"
            "  autocodabench plan \"<idea>\"  →  autocodabench build --run-dir <dir>  →  autocodabench validate <bundle>\n"
            "\n"
            "docs: docs/INSTRUCTION_FOR_USER.md   ·   per-phase walkthrough: docs/post-create-pipeline.md"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    # ---- Pipeline phases ---------------------------------------------------
    p = sub.add_parser("plan",
                       help="Phase 1: idea/PDF → implementation_plan.md (needs an LLM backend)")
    p.add_argument("idea", nargs="?", default=None,
                   help="one-line competition idea or proposal text "
                        "(optional if --pdf is given)")
    p.add_argument("--pdf", help="path to a PDF proposal; its text is extracted "
                                 "and handed to the planner (works on any backend)")
    p.add_argument("--data", help="path to sample data the planner may inspect")
    p.add_argument("--backend", default=None,
                   help="LLM backend: claude[:model] (default), ollama:<model>, "
                        "openai:<model>, or an OpenAI-compatible URL with '#<model>'")
    p.add_argument("--model", help="model override for the agent session")
    p.add_argument("--max-budget-usd", type=float, default=None,
                   help="cost cap for this phase")
    _add_research_args(p)
    p.add_argument("--yes", "-y", action="store_true",
                   help="do not prompt to confirm before starting")
    p.add_argument("--debug", action="store_true",
                   help="print the full developer trace")
    p.add_argument("--quiet", action="store_true",
                   help="suppress progress; print only the final summary")
    p.set_defaults(func=_cmd_plan)

    p = sub.add_parser("build",
                       help="Phase 2: build a bundle from a plan (needs an LLM backend)")
    p.add_argument("plan", nargs="?", default=None,
                   help="path to implementation_plan.md — a fresh run dir is "
                        "auto-created (mutually exclusive with --run-dir)")
    p.add_argument("--run-dir", default=None,
                   help="existing run dir produced by `plan`; the plan "
                        "is read from <run-dir>/specs/implementation_plan.md "
                        "(mutually exclusive with the positional plan argument)")
    p.add_argument("--no-validate", action="store_true",
                   help="skip the post-build validation pass")
    p.add_argument("--backend", default=None,
                   help="LLM backend: claude[:model] (default), ollama:<model>, "
                        "openai:<model>, or an OpenAI-compatible URL with '#<model>'")
    p.add_argument("--model", help="model override for the agent session")
    p.add_argument("--max-budget-usd", type=float, default=None,
                   help="cost cap for this phase")
    p.add_argument("--yes", "-y", action="store_true",
                   help="do not prompt to confirm before starting")
    p.add_argument("--debug", action="store_true",
                   help="print the full developer trace")
    p.add_argument("--quiet", action="store_true",
                   help="suppress progress; print only the final summary")
    p.set_defaults(func=_cmd_build)

    p = sub.add_parser("validate",
                       help="Phase 3 / standalone: run a bundle's checks, dir or zip (keyless)")
    _add_validate_args(p)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("plan-build-validate", aliases=["create"],
                       help="all 3 phases: plan → build → validate (needs an LLM backend)")
    p.add_argument("idea", nargs="?", default=None,
                   help="one-line competition idea or proposal text "
                        "(optional if --pdf is given)")
    p.add_argument("--pdf", help="path to a PDF proposal; its text is extracted "
                                 "and handed to the planner (works on any backend)")
    p.add_argument("--data", help="path to sample data the planner may inspect")
    p.add_argument("--backend", default=None,
                   help="LLM backend: claude[:model] (default), ollama:<model> "
                        "(local, keyless), openai:<model>, or an OpenAI-compatible "
                        "URL with '#<model>'")
    p.add_argument("--model", help="model override for the agent sessions")
    p.add_argument("--out", default=None,
                   help="directory for this run's output (the runs root); "
                        "if omitted you are prompted, defaulting to "
                        "<cwd>/.autocodabench/runs")
    p.add_argument("--max-budget-usd", type=float, default=None,
                   help="cumulative cost cap per phase")
    p.add_argument("--no-validate", action="store_true",
                   help="skip the post-build validation pass")
    _add_research_args(p)
    p.add_argument("--yes", "-y", action="store_true",
                   help="do not prompt to confirm before starting")
    p.add_argument("--debug", action="store_true",
                   help="print the full developer trace (every tool call, raw "
                        "output, agent reasoning) instead of the concise summary")
    p.add_argument("--quiet", action="store_true",
                   help="suppress progress; print only the final summary")
    p.add_argument("--verbose", action="store_true",
                   help=argparse.SUPPRESS)  # deprecated alias for --debug
    p.set_defaults(func=_cmd_create)

    # ---- Utilities ---------------------------------------------------------
    p = sub.add_parser("demo", help="rebuild + validate the demo bundle from a "
                                    "recorded run (keyless)")
    p.add_argument("--out", default="autocodabench-demo",
                   help="output directory (default ./autocodabench-demo)")
    p.add_argument("--fixture", help="replay a different fixture (.jsonl or run dir)")
    p.add_argument("--replay", action="store_true",
                   help="(default behavior; flag kept for clarity)")
    p.set_defaults(func=_cmd_demo)

    p = sub.add_parser("auth", help="report, choose, and verify which Claude auth path is used")
    p.add_argument("action", choices=["status", "use"], nargs="?", default="status",
                   help="'status' reports, lets you pick (on a terminal), and "
                        "verifies; 'use <mode>' sets the preference and verifies it")
    p.add_argument("mode", nargs="?", default=None,
                   help="for 'use': auto | subscription | api_key")
    p.add_argument("--no-probe", action="store_true",
                   help="skip the live verification turn; static detection only "
                        "(use offline or in CI)")
    p.add_argument("--probe", action="store_true",
                   help="(now the default) verify auth end to end with one live turn")
    p.add_argument("--no-pick", action="store_true",
                   help="status: just report, skip the interactive picker")
    p.add_argument("--model", help="model for the verification turn")
    p.set_defaults(func=_cmd_auth)

    p = sub.add_parser("checks", help="list the registered checks by tier")
    p.add_argument("action", choices=["list"], nargs="?", default="list")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.set_defaults(func=_cmd_checks)

    p = sub.add_parser("doctor", help="check system prerequisites pip cannot "
                                      "install (Docker, Node/npx, git)")
    p.add_argument("--json", action="store_true", dest="as_json")
    p.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    from ..auth import load_dotenv
    load_dotenv()  # <cwd>/.env, if present; never overrides real env vars
    # Brand banner to stderr (kept off stdout so JSON/piped output stays clean);
    # empty unless stderr is a TTY. Printed before parsing so it also tops
    # `--help` and usage errors.
    _banner = style.banner()
    if _banner:
        print(_banner, file=sys.stderr)
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
