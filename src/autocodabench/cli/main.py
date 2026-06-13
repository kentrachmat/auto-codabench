"""autocodabench CLI.

Entry points are tiered by their authentication demands, keyless first:

  autocodabench validate-bundle BUNDLE [--facts F] [--judged]  # keyless (unless --judged)
  autocodabench demo [--out DIR]                               # keyless replay demo
  autocodabench create "IDEA" [--data PATH]                    # agentic; subscription or API key
  autocodabench auth status [--probe]
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

def _cmd_auth(args: argparse.Namespace) -> int:
    from ..auth import probe, resolve_auth

    status = resolve_auth()
    print(status.describe())
    if args.probe:
        print("\nProbing with a one-turn session…")
        outcome = asyncio.run(probe(model=args.model))
        if outcome["ok"]:
            cost = outcome.get("cost_usd")
            print(f"probe OK (cost: ${cost:.4f})" if cost is not None else "probe OK")
        else:
            print(f"probe FAILED: {outcome.get('error') or outcome.get('status')}",
                  file=sys.stderr)
            return 1
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

    p = sub.add_parser("auth", help="report which Claude auth path is active")
    p.add_argument("action", choices=["status"], nargs="?", default="status")
    p.add_argument("--probe", action="store_true",
                   help="spend one tiny turn to confirm auth end to end")
    p.add_argument("--model", help="model for the probe turn")
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
