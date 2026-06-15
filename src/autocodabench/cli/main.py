"""autocodabench CLI.

Entry points are tiered by their authentication demands, keyless first:

  autocodabench validate-bundle BUNDLE [--facts F] [--judged]  # keyless (unless --judged)
  autocodabench demo [--out DIR]                               # keyless replay demo
  autocodabench plan-competition "IDEA" [--data PATH]          # Phase 1 only
  autocodabench create-bundle [plan.md | --run-dir DIR]        # Phase 2 only
  autocodabench create "IDEA" [--data PATH]                    # Phase 1 + 2 + validate
  autocodabench auth status [--no-probe]   # report, pick, and verify via a live turn
  autocodabench checks list

``validate-bundle`` validates any bundle — including hand-written ones that
never touched an agent (``validate`` remains as a back-compatible alias).

``plan-competition`` and ``create-bundle`` let you run the two agentic phases
independently. ``plan-competition`` prints its run directory on completion so
you can pass it to ``create-bundle --run-dir`` to reuse the same run.

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

from .. import __version__


def _require_live_claude_auth(backend_spec: str | None) -> bool:
    """Preflight before starting a live Claude session: if no auth path
    exists, walk the user through one (interactive) or print guidance and
    refuse (non-interactive) — instead of failing opaquely inside the SDK
    mid-run. Non-Claude backends (ollama:/openai:/URL) carry their own
    credentials and are skipped."""
    if backend_spec and backend_spec.split(":", 1)[0] != "claude":
        print(f"INFO: backend = {backend_spec} (non-Claude; uses its own "
              "credentials / runs locally)", file=sys.stderr)
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
    p.add_argument("--backend", default=None,
                   help="LLM backend for --judged: claude[:model] (default), "
                        "ollama:<model> (local, keyless), openai:<model>, or an "
                        "OpenAI-compatible URL with '#<model>'")
    p.add_argument("--model", default=None, help="model override for --backend")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="emit the machine-readable report instead of markdown")


def _cmd_validate(args: argparse.Namespace) -> int:
    from ..checks import validate_bundle_path

    if args.judged and not _require_live_claude_auth(args.backend):
        return 2
    backend = None
    if args.judged and (args.backend or args.model):
        from ..backends import resolve_backend
        backend = resolve_backend(args.backend, model=args.model)
    report = validate_bundle_path(args.bundle, facts_path=args.facts,
                                  judged=args.judged, backend=backend)
    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.to_markdown())
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
        print(report.to_markdown())
        return 0 if report.ok else 1
    return 0


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

# --- create: progress rendering -------------------------------------------

def _short_tool_name(name: str) -> str:
    """`mcp__autocodabench__autocodabench_write_scoring_program` → `write_scoring_program`."""
    return name.split("__")[-1].replace("autocodabench_", "")


def _tool_arg_summary(inp: dict) -> str:
    """A short, identifying summary of a tool call's arguments for one line."""
    if not isinstance(inp, dict) or not inp:
        return ""
    for key in ("name", "slug", "page", "filename", "path", "file_path",
                "pattern", "spec_name", "kind"):
        val = inp.get(key)
        if isinstance(val, str) and val:
            return f"({val})"
    for val in inp.values():  # fall back to the first short scalar
        if isinstance(val, str) and val:
            return f"({val if len(val) <= 48 else val[:47] + '…'})"
    return ""


# Event kinds an agent uses to address the end user directly (via
# `autocodabench_log_event(kind=..., message=...)`). These are surfaced in the
# default, user-oriented view; "deviation" is highlighted as it reports a
# departure from the locked plan.
_USER_MESSAGE_KINDS = {"progress", "milestone", "status", "deviation"}


def _is_parallel_cancellation(text: str) -> bool:
    """A tool result that is a sibling-cancellation, not a genuine failure:
    when one call in a parallel batch errors, the runtime cancels the others."""
    low = (text or "").lower()
    return "cancelled" in low and "parallel tool call" in low


def _make_progress_renderer(*, debug: bool = False):
    """Return an `on_event` callback that renders pipeline progress.

    Two registers share one renderer:

    - **default** (``debug=False``) — a concise, user-oriented narrative:
      phase headers, the milestone messages the agent emits for the user
      (plain-language progress and deviation notices), and phase completion.
      Raw tool calls, raw tool output, and the agent's internal reasoning are
      omitted, as is the benign cancellation of parallel sibling calls.
    - **debug** (``debug=True``) — the full developer trace: every tool call
      with its arguments, tool errors (cancellations marked as benign
      retries), and the agent's narration. Intended for diagnosing the
      pipeline rather than routine use.
    """
    bar = "─" * 60

    def render(ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "phase":
            print(f"\n{bar}")
            print(f" Phase {ev.get('index')}/{ev.get('total')} · {ev.get('title')}")
            if debug and ev.get("detail"):
                print(f" {ev['detail']}")
            print(bar, flush=True)
        elif kind == "phase_done":
            mark = "✓" if ev.get("ok") else "✗"
            turns = ev.get("num_turns")
            tail = f" · {turns} turns" if (debug and turns) else ""
            print(f"  {mark} {ev.get('phase')} phase complete{tail}", flush=True)
        elif kind == "tool_use":
            name = _short_tool_name(ev.get("name", "?"))
            inp = ev.get("input") or {}
            if name == "log_event":
                message = inp.get("message")
                ekind = (inp.get("kind") or "").lower()
                if message and ekind in _USER_MESSAGE_KINDS:
                    bullet = "⚠" if ekind == "deviation" else "•"
                    print(f"  {bullet} {message}", flush=True)
                elif debug:
                    print(f"  ⏺ log_event({ekind})", flush=True)
            elif debug:
                print(f"  ⏺ {name}{_tool_arg_summary(inp)}", flush=True)
        elif kind == "tool_result" and ev.get("is_error"):
            preview = ev.get("preview") or "tool error"
            if _is_parallel_cancellation(preview):
                if debug:
                    print("      ↻ retried (a parallel sibling call was "
                          "cancelled — not a failure)", flush=True)
            elif debug:
                print(f"      ↳ ⚠ {preview}", flush=True)
        elif kind == "text" and debug:
            text = (ev.get("text") or "").strip()
            if text:
                for line in textwrap.wrap(text, width=84) or [""]:
                    print(f"  │ {line}", flush=True)

    return render


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


def _print_create_config(*, idea, backend_name, auth_label, model, run_dir,
                         data, max_budget_usd, validate, verbosity) -> None:
    """Show the full effective configuration before spending anything, so the
    run is never an opaque idle."""
    budget = f"${max_budget_usd:.2f} per phase" if max_budget_usd else "no cap"
    print("autocodabench create — configuration")
    print(f"  idea:        {textwrap.shorten(idea, width=72)}")
    print(f"  backend:     {backend_name}  ({auth_label})")
    print(f"  model:       {model}")
    print(f"  output dir:  {run_dir}")
    print(f"  sample data: {data or '(none)'}")
    print(f"  cost cap:    {budget}")
    print(f"  output mode: {verbosity}")
    print( "  pipeline:    1) plan → specs/implementation_plan.md")
    print( "               2) build → bundle + zip (via the MCP tools)")
    print(f"               3) validate → {'registered checks' if validate else 'skipped'}")
    print(f"  artifacts:   plan, bundle, zip, and a full tool-call audit trail")
    print(f"               land under the output dir above.")


def _cmd_create(args: argparse.Namespace) -> int:
    from ..agent.pipeline import create_async
    from ..run_log import open_run

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

    # ---- Create the run dir now so the config banner can name it -----------
    run_dir = open_run(slug="create").path

    print()
    _print_create_config(
        idea=args.idea, backend_name=backend.name, auth_label=auth_label,
        model=model_shown, run_dir=run_dir, data=args.data,
        max_budget_usd=args.max_budget_usd, validate=not args.no_validate,
        verbosity=verbosity)

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
            if input("\nStart the run? [Y/n]: ").strip().lower() not in ("", "y", "yes"):
                return _abort()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return _abort()

    renderer = None if args.quiet else _make_progress_renderer(debug=debug)
    if renderer is None:
        print("\n(running quietly — omit --quiet to see step-by-step progress)\n")

    result = asyncio.run(create_async(
        args.idea,
        data=args.data,
        backend=backend,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        on_event=renderer,
        validate=not args.no_validate,
    ))

    print("\n" + "═" * 60)
    print("Done.")
    print(f"  run dir:   {result.run_dir}")
    print(f"  plan:      {result.plan_path}")
    print(f"  bundle:    {result.bundle_dir}")
    print(f"  zip:       {result.zip_path}")
    image = _bundle_docker_image(result.bundle_dir)
    if image:
        print(f"  docker:    {image}  (declared in competition.yaml; what "
              "Codabench will run)")
    updated_plan = (Path(result.run_dir) / "specs" / "updated_implementation_plan.md"
                    if result.run_dir else None)
    if updated_plan and updated_plan.is_file():
        print(f"  changes:   {updated_plan}  (deviations from the original plan)")
    print(f"  cost:      ${result.total_cost_usd:.2f}")
    if result.validation is not None:
        print()
        print(result.validation.to_markdown())
    if not result.ok:
        print(f"\ncreate failed: {result.error}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# plan-competition  (Phase 1 standalone)
# ---------------------------------------------------------------------------

def _print_plan_config(*, idea, backend_name, auth_label, model, run_dir,
                        data, max_budget_usd, verbosity) -> None:
    budget = f"${max_budget_usd:.2f}" if max_budget_usd else "no cap"
    print("autocodabench plan-competition — configuration")
    print(f"  idea:        {textwrap.shorten(idea, width=72)}")
    print(f"  backend:     {backend_name}  ({auth_label})")
    print(f"  model:       {model}")
    print(f"  output/run dir:  {run_dir}")
    print(f"  sample data: {data or '(none)'}")
    print(f"  cost cap:    {budget}")
    print(f"  output mode: {verbosity}")
    print( "  output:      specs/implementation_plan.md inside the run dir above")


def _cmd_plan_competition(args: argparse.Namespace) -> int:
    from ..agent.pipeline import plan_async
    from ..run_log import open_run

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

    print()
    _print_plan_config(
        idea=args.idea, backend_name=backend.name, auth_label=auth_label,
        model=model_shown, run_dir=run_dir, data=args.data,
        max_budget_usd=args.max_budget_usd, verbosity=verbosity)

    if sys.stdin.isatty() and not args.yes:
        def _abort() -> int:
            import shutil
            shutil.rmtree(run_dir, ignore_errors=True)
            os.environ.pop("AUTOCODABENCH_RUN_DIR", None)
            print("aborted (no run created)", file=sys.stderr)
            return 130
        try:
            if input("\nStart planning? [Y/n]: ").strip().lower() not in ("", "y", "yes"):
                return _abort()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return _abort()

    renderer = None if args.quiet else _make_progress_renderer(debug=debug)

    result = asyncio.run(plan_async(
        args.idea,
        data=args.data,
        backend=backend,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        on_event=renderer,
    ))

    print("\n" + "═" * 60)
    print("Done." if result.ok else "Planning failed.")
    print(f"  run dir:  {result.run_dir}")
    print(f"  plan:     {result.plan_path}")
    print(f"  cost:     ${result.total_cost_usd:.2f}")
    if result.ok:
        print(
            f"\n  → To build the bundle from this plan:\n"
            f"    autocodabench create-bundle --run-dir {result.run_dir}"
        )
    if not result.ok:
        print(f"\nplan-competition failed: {result.error}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# create-bundle  (Phase 2 standalone)
# ---------------------------------------------------------------------------

def _print_bundle_config(*, plan_source, backend_name, auth_label, model,
                          run_dir, max_budget_usd, validate, verbosity) -> None:
    budget = f"${max_budget_usd:.2f}" if max_budget_usd else "no cap"
    print("autocodabench create-bundle — configuration")
    print(f"  plan:        {plan_source}")
    print(f"  backend:     {backend_name}  ({auth_label})")
    print(f"  model:       {model}")
    print(f"  output/run dir:  {run_dir}")
    print(f"  cost cap:    {budget}")
    print(f"  output mode: {verbosity}")
    print( "  pipeline:    1) build → bundle + zip (via the MCP tools)")
    print(f"               2) validate → {'registered checks' if validate else 'skipped'}")
    print( "  artifacts:   bundle, zip, and a full tool-call audit trail")
    print( "               land under the output/run dir above.")


def _cmd_create_bundle(args: argparse.Namespace) -> int:
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
        print("error: pass a plan file (plan-competition.md) or --run-dir <path>",
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
            if input("\nStart building? [Y/n]: ").strip().lower() not in ("", "y", "yes"):
                return _abort()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return _abort()

    renderer = None if args.quiet else _make_progress_renderer(debug=debug)

    result = asyncio.run(bundle_async(
        **bundle_kwargs,
        backend=backend,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        validate=not args.no_validate,
        on_event=renderer,
    ))

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
        print(result.validation.to_markdown())
    if not result.ok:
        print(f"\ncreate-bundle failed: {result.error}", file=sys.stderr)
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
    print(f"\n{status.info_line()}")
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
    from ..checks import checklist_coverage

    rows = checklist_coverage()
    if args.as_json:
        print(json.dumps(rows, indent=2))
        return 0
    width = max(len(r["id"]) for r in rows)
    tier = None
    for r in rows:
        if r["tier"] != tier:
            tier = r["tier"]
            print(f"\n[{tier}]")
        print(f"  {r['id']:<{width}}  {r['title']}  ({r['citation']})")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autocodabench",
        description="Agentic authoring and pre-launch validation of Codabench bundles.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate-bundle", aliases=["validate"],
                       help="validate a bundle directory or zip (keyless)")
    _add_validate_args(p)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("demo", help="rebuild + validate the demo bundle from a "
                                    "recorded run (keyless)")
    p.add_argument("--out", default="autocodabench-demo",
                   help="output directory (default ./autocodabench-demo)")
    p.add_argument("--fixture", help="replay a different fixture (.jsonl or run dir)")
    p.add_argument("--replay", action="store_true",
                   help="(default behavior; flag kept for clarity)")
    p.set_defaults(func=_cmd_demo)

    p = sub.add_parser("plan-competition",
                       help="Phase 1 only: plan a competition and save "
                            "implementation_plan.md (needs an LLM backend)")
    p.add_argument("idea", help="one-line competition idea or proposal text")
    p.add_argument("--data", help="path to sample data the planner may inspect")
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
    p.set_defaults(func=_cmd_plan_competition)

    p = sub.add_parser("create-bundle",
                       help="Phase 2 only: build a Codabench bundle from a plan "
                            "(needs an LLM backend)")
    p.add_argument("plan", nargs="?", default=None,
                   help="path to implementation_plan.md — a fresh run dir is "
                        "auto-created (mutually exclusive with --run-dir)")
    p.add_argument("--run-dir", default=None,
                   help="existing run dir produced by plan-competition; the plan "
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
    p.set_defaults(func=_cmd_create_bundle)

    p = sub.add_parser("create", help="agentic plan→build pipeline (needs an LLM backend)")
    p.add_argument("idea", help="one-line competition idea or proposal text")
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

    return parser


def main(argv: list[str] | None = None) -> int:
    from ..auth import load_dotenv
    load_dotenv()  # <cwd>/.env, if present; never overrides real env vars
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
