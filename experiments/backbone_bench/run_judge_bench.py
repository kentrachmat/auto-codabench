#!/usr/bin/env python3
"""Backbone bench, axis A: validator/judge quality per LLM backbone (E3).

Seeds known authoring defects into otherwise-clean bundles, runs the
validator, and measures the catch rate per defect — separating the
deterministic tier (backbone-independent; sanity baseline) from the
LLM-judged tier (the backbone-sensitive measurement). Clean copies
measure the judged tier's false-positive rate.

Usage:
  python experiments/backbone_bench/run_judge_bench.py                      # deterministic only
  python experiments/backbone_bench/run_judge_bench.py --backend claude
  python experiments/backbone_bench/run_judge_bench.py --backend ollama:llama3.1 --runs 3
  python experiments/backbone_bench/run_judge_bench.py --backend openai:gpt-4o-mini

Outputs results.json + results.md under --out (default
experiments/backbone_bench/results/<backbone-tag>/).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from autocodabench.backends import ReplayBackend, resolve_backend
from autocodabench.backends.base import AgentTask
from autocodabench.checks import Status, validate_bundle_path

FIXTURE = (Path(__file__).resolve().parents[2]
           / "src" / "autocodabench" / "backends" / "fixtures" / "demo_bundle.jsonl")
SLUG = "demo-ai-text-detection"


# ---------------------------------------------------------------------------
# Defect library
# ---------------------------------------------------------------------------

def _edit_yaml(bundle: Path, mutate) -> None:
    p = bundle / "competition.yaml"
    comp = yaml.safe_load(p.read_text())
    mutate(comp)
    p.write_text(yaml.safe_dump(comp, sort_keys=False, allow_unicode=True))


def _edit_text(bundle: Path, rel: str, old: str, new: str) -> None:
    p = bundle / rel
    text = p.read_text()
    assert old in text, f"defect seed failed: {old!r} not in {rel}"
    p.write_text(text.replace(old, new))


@dataclass(frozen=True)
class Defect:
    id: str
    tier: str                 # "deterministic" | "judged"
    expect_check: str         # check id that should flag it
    apply: Callable[[Path], None]
    description: str


DEFECTS: list[Defect] = [
    # --- deterministic-tier targets (backbone-independent sanity baseline) ---
    Defect("missing-page", "deterministic", "bundle-schema",
           lambda b: (b / "pages" / "overview.md").unlink(),
           "a page referenced from competition.yaml is deleted"),
    Defect("unwritten-leaderboard-key", "deterministic", "bundle-schema",
           lambda b: _edit_text(b, "scoring_program/score.py",
                                '"balanced_accuracy"', '"bal_acc"'),
           "scoring program stops writing a leaderboard column key"),
    Defect("no-daily-cap", "deterministic", "daily-submission-cap",
           lambda b: _edit_yaml(b, lambda c: c["phases"][0].pop("max_submissions_per_day")),
           "development phase loses its per-day submission cap"),
    Defect("short-dev-phase", "deterministic", "dev-phase-duration",
           lambda b: _edit_yaml(b, lambda c: c["phases"][0].__setitem__(
               "end", "2026-07-11 00:00:00")),
           "development phase shrunk to 10 days"),
    Defect("no-sorting", "deterministic", "leaderboard-sorting",
           lambda b: _edit_yaml(b, lambda c: c["leaderboards"][0]["columns"][0].pop("sorting")),
           "primary leaderboard column loses its sorting direction"),
    Defect("final-unlimited", "deterministic", "final-phase-submission-limit",
           lambda b: _edit_yaml(b, lambda c: c["phases"][1].__setitem__("max_submissions", 50)),
           "final phase allows 50 total submissions"),
    Defect("kit-missing", "deterministic", "starting-kit",
           lambda b: shutil.rmtree(b / "starting_kit"),
           "starting kit removed"),
    Defect("single-phase", "deterministic", "two-phase-structure",
           lambda b: _edit_yaml(b, lambda c: c.__setitem__("phases", c["phases"][:1])),
           "final phase dropped (single-phase competition)"),
    Defect("docker-unpinned", "deterministic", "docker-image-pinned",
           lambda b: _edit_yaml(b, lambda c: c.pop("docker_image")),
           "docker image no longer pinned"),
    # --- judged-tier targets (the backbone-sensitive measurement) ---
    Defect("caps-contradiction", "judged", "judged-docs-config-consistency",
           lambda b: _edit_text(b, "pages/overview.md",
                                "max 5 submissions/day", "max 20 submissions/day"),
           "overview page promises 20 submissions/day; config enforces 5"),
    Defect("metric-direction-contradiction", "judged", "judged-docs-config-consistency",
           lambda b: _edit_text(
               b, "pages/evaluation.md",
               "The primary metric is **balanced accuracy**",
               "The primary metric is **balanced accuracy** — LOWER values rank "
               "higher on the leaderboard (ascending order),"),
           "evaluation page claims lower-is-better; leaderboard sorts descending"),
    Defect("phase-dates-contradiction", "judged", "judged-docs-config-consistency",
           lambda b: _edit_text(b, "pages/overview.md",
                                "## Phases",
                                "## Phases\n\nThe development phase runs from "
                                "2027-07-01 to 2027-08-15."),
           "overview page states 2027 phase dates; config says 2026"),
]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _build_clean(workdir: Path) -> Path:
    out = workdir / "clean"
    result = asyncio.run(ReplayBackend(FIXTURE, out_dir=out).run(AgentTask(prompt="seed")))
    assert result.ok, result.error
    return out / SLUG


def _flagged(report, check_id: str) -> bool:
    return any(r.check_id == check_id and r.status in (Status.FAIL, Status.FINDING)
               for r in report.results)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", default=None,
                    help="LLM backbone for the judged tier (claude[:model], "
                         "ollama:<model>, openai:<model>, URL#model). "
                         "Omit to run the deterministic tier only.")
    ap.add_argument("--model", default=None)
    ap.add_argument("--runs", type=int, default=1,
                    help="repetitions per judged condition (judged tier is "
                         "stochastic; >=3 recommended for reporting)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    backend = resolve_backend(args.backend, model=args.model) if args.backend else None
    tag = re.sub(r"[^A-Za-z0-9._-]+", "_", args.backend or "deterministic-only")
    out_dir = Path(args.out) if args.out else Path(__file__).parent / "results" / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="backbone-bench-") as tmp:
        workdir = Path(tmp)
        clean = _build_clean(workdir)

        for defect in DEFECTS:
            judged = defect.tier == "judged"
            if judged and backend is None:
                rows.append({"defect": defect.id, "tier": defect.tier,
                             "expect_check": defect.expect_check,
                             "runs": 0, "caught": None,
                             "note": "skipped — no --backend"})
                continue
            n_runs = args.runs if judged else 1   # deterministic tier is, well, deterministic
            caught = 0
            for i in range(n_runs):
                seeded = workdir / f"{defect.id}-{i}"
                shutil.copytree(clean, seeded)
                defect.apply(seeded)
                report = validate_bundle_path(seeded, judged=judged, backend=backend)
                if _flagged(report, defect.expect_check):
                    caught += 1
                shutil.rmtree(seeded)
            rows.append({"defect": defect.id, "tier": defect.tier,
                         "expect_check": defect.expect_check,
                         "runs": n_runs, "caught": caught,
                         "catch_rate": caught / n_runs,
                         "description": defect.description})
            print(f"  {defect.id:<32} {defect.tier:<13} {caught}/{n_runs}")

        # Judged false-positive rate on clean copies.
        fp = None
        if backend is not None:
            fp_hits = 0
            for i in range(args.runs):
                report = validate_bundle_path(clean, judged=True, backend=backend)
                if _flagged(report, "judged-docs-config-consistency"):
                    fp_hits += 1
            fp = {"runs": args.runs, "false_positives": fp_hits,
                  "fp_rate": fp_hits / args.runs}
            print(f"  clean-bundle judged FP: {fp_hits}/{args.runs}")

    results = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "backend": args.backend, "model_override": args.model,
        "runs_per_judged_condition": args.runs,
        "defects": rows, "judged_false_positive": fp,
    }
    (out_dir / "results.json").write_text(json.dumps(results, indent=2))

    md = [f"# Judge bench — backbone: `{args.backend or 'deterministic only'}`", "",
          "| defect | tier | expected check | caught |",
          "|--------|------|----------------|--------|"]
    for r in rows:
        caught = "skipped" if r["caught"] is None else f"{r['caught']}/{r['runs']}"
        md.append(f"| {r['defect']} | {r['tier']} | `{r['expect_check']}` | {caught} |")
    if fp:
        md += ["", f"Clean-bundle judged false positives: {fp['false_positives']}/{fp['runs']}"]
    (out_dir / "results.md").write_text("\n".join(md) + "\n")
    print(f"\nresults → {out_dir}/results.{{json,md}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
