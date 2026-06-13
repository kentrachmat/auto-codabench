"""autocodabench CLI.

Entry points are tiered by their authentication demands, keyless first:

  autocodabench validate-bundle BUNDLE [--facts F] [--judged]  # keyless (unless --judged)
  autocodabench demo [--out DIR]                               # keyless replay demo
  autocodabench create "IDEA" [--data PATH]                    # agentic; subscription or API key
  autocodabench auth status [--no-probe]   # report, pick, and verify via a live turn
  autocodabench checks list

``validate-bundle`` validates any bundle — including hand-written ones that
never touched an agent (``validate`` remains as a back-compatible alias).

The CLI is a thin argument-parsing layer over the library: it contributes
``.env`` loading and the live-auth preflight, and contains no validation
or authoring logic of its own, so everything reachable here is equally
reachable via ``import autocodabench``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
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

def _cmd_create(args: argparse.Namespace) -> int:
    from ..agent.pipeline import create

    if not _require_live_claude_auth(args.backend):
        return 2

    def on_text(text: str) -> None:
        print(text, flush=True)

    backend = None
    if args.backend:
        from ..backends import resolve_backend
        backend = resolve_backend(args.backend, model=args.model)
    result = create(
        args.idea,
        data=args.data,
        backend=backend,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        on_text=on_text if args.verbose else None,
    )
    print()
    print(f"run dir:   {result.run_dir}")
    print(f"plan:      {result.plan_path}")
    print(f"bundle:    {result.bundle_dir}")
    print(f"zip:       {result.zip_path}")
    print(f"cost:      ${result.total_cost_usd:.2f}")
    if result.validation is not None:
        print()
        print(result.validation.to_markdown())
    if not result.ok:
        print(f"\ncreate failed: {result.error}", file=sys.stderr)
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

    p = sub.add_parser("create", help="agentic plan→build pipeline (needs an LLM backend)")
    p.add_argument("idea", help="one-line competition idea or proposal text")
    p.add_argument("--data", help="path to sample data the planner may inspect")
    p.add_argument("--backend", default=None,
                   help="LLM backend: claude[:model] (default), ollama:<model> "
                        "(local, keyless), openai:<model>, or an OpenAI-compatible "
                        "URL with '#<model>'")
    p.add_argument("--model", help="model override for the agent sessions")
    p.add_argument("--max-budget-usd", type=float, default=None,
                   help="cumulative cost cap per phase")
    p.add_argument("--verbose", action="store_true", help="stream agent text")
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
