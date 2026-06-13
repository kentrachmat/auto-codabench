#!/usr/bin/env python3
"""Aggregate missing_info_report.json across all runs for meta-analysis.

Walks every `experiments/bundle_creation_test/runs/<comp>/<run_id>/missing_info_report.json`
file, computes cross-run statistics, and prints them to stdout.

Usage (from repo root):

  python experiments/bundle_creation_test/scripts/aggregate_missing_info.py
  python experiments/bundle_creation_test/scripts/aggregate_missing_info.py --comp style-trans-fair
  python experiments/bundle_creation_test/scripts/aggregate_missing_info.py --json    # machine-readable
  python experiments/bundle_creation_test/scripts/aggregate_missing_info.py --top-fields 20  # show most-missed fields

Outputs four views by default:
  1. Run count summary (per competition_sample, with / without inventory)
  2. Most-missed sections + fields (across all runs)
  3. Severity / impact breakdown (totals + per-run averages)
  4. High-stakes inferences (would_block_correct_scoring items)

The script reads ONLY missing_info_report.json files. The per-stage
inventories under plan/ and bundle/ are not consumed here — the
orchestrator already merged them into the report. See MISSING_INFO.md
for the schema this depends on.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

RUNS_ROOT = Path("experiments/bundle_creation_test/runs")
REPORT_GLOB = "*/*/missing_info_report.json"


def discover_reports(comp_filter: str | None) -> list[Path]:
    paths = sorted(RUNS_ROOT.glob(REPORT_GLOB))
    if comp_filter:
        paths = [p for p in paths if p.parts[-3] == comp_filter]
    return paths


def load_report(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARN: could not parse {path}: {e}", file=sys.stderr)
        return None


def aggregate(reports: list[dict]) -> dict:
    """Return an aggregation suitable for both human and JSON output."""
    by_comp_runs: dict[str, int] = defaultdict(int)
    by_comp_with_items: dict[str, int] = defaultdict(int)
    all_items: list[dict] = []
    section_counter: Counter[str] = Counter()
    field_counter: Counter[tuple[str, str]] = Counter()  # (section, field)
    severity_counter: Counter[str] = Counter()
    impact_counter: Counter[str] = Counter()
    action_counter: Counter[str] = Counter()
    confidence_counter: Counter[str] = Counter()
    high_stakes: list[dict] = []  # would_block_correct_scoring == true

    for r in reports:
        comp = r.get("competition_sample_name", "<unknown>")
        by_comp_runs[comp] += 1
        items = r.get("items", []) or []
        if items:
            by_comp_with_items[comp] += 1
        for it in items:
            all_items.append(it)
            section = it.get("section", "<unknown>")
            field = it.get("field", "<unknown>")
            severity = it.get("severity", "<unknown>")
            impact = it.get("impact_area", "<unknown>")
            resolution = it.get("resolution") or {}
            action = resolution.get("action", "<unknown>")
            confidence = resolution.get("confidence", "<unknown>")

            section_counter[section] += 1
            field_counter[(section, field)] += 1
            severity_counter[severity] += 1
            impact_counter[impact] += 1
            action_counter[action] += 1
            confidence_counter[confidence] += 1

            if resolution.get("would_block_correct_scoring"):
                high_stakes.append({
                    "competition_sample_name": comp,
                    "run_id": r.get("run_id"),
                    "section": section,
                    "field": field,
                    "what_was_missing": it.get("what_was_missing", "")[:200],
                    "resolution_choice": resolution.get("choice", "")[:200],
                    "confidence": confidence,
                })

    return {
        "total_runs": len(reports),
        "total_items": len(all_items),
        "items_per_run_avg": round(len(all_items) / len(reports), 2) if reports else 0,
        "by_competition_sample": {
            comp: {
                "runs": by_comp_runs[comp],
                "runs_with_items": by_comp_with_items[comp],
            } for comp in sorted(by_comp_runs)
        },
        "by_section": dict(section_counter.most_common()),
        "by_severity": dict(severity_counter.most_common()),
        "by_impact_area": dict(impact_counter.most_common()),
        "by_resolution_action": dict(action_counter.most_common()),
        "by_confidence": dict(confidence_counter.most_common()),
        "top_fields": [
            {"section": section, "field": field, "count": n}
            for (section, field), n in field_counter.most_common()
        ],
        "high_stakes_inferences": high_stakes,
    }


def render_human(agg: dict, top_fields_n: int) -> str:
    L = []
    L.append(f"=== runs scanned: {agg['total_runs']} ===")
    L.append(f"total missing-info items: {agg['total_items']}  ({agg['items_per_run_avg']} per run avg)")
    L.append("")
    L.append("--- runs per competition_sample ---")
    for comp, c in agg["by_competition_sample"].items():
        L.append(f"  {comp:30}  {c['runs']:3} runs  ({c['runs_with_items']} with non-empty inventory)")
    L.append("")
    L.append("--- by section ---")
    for k, v in agg["by_section"].items():
        L.append(f"  {k:24}  {v}")
    L.append("")
    L.append("--- by severity ---")
    for k, v in agg["by_severity"].items():
        L.append(f"  {k:16}  {v}")
    L.append("")
    L.append("--- by impact_area ---")
    for k, v in agg["by_impact_area"].items():
        L.append(f"  {k:28}  {v}")
    L.append("")
    L.append("--- by resolution.action ---")
    for k, v in agg["by_resolution_action"].items():
        L.append(f"  {k:18}  {v}")
    L.append("")
    L.append(f"--- top {top_fields_n} most-missed fields ---")
    for entry in agg["top_fields"][:top_fields_n]:
        L.append(f"  {entry['count']:3}  {entry['section']}.{entry['field']}")
    L.append("")
    hs = agg["high_stakes_inferences"]
    L.append(f"--- HIGH-STAKES inferences (would_block_correct_scoring): {len(hs)} ---")
    for h in hs[:20]:
        L.append(f"  [{h['confidence']:6}]  {h['competition_sample_name']}/{h['run_id']}:")
        L.append(f"           {h['section']}.{h['field']}")
        L.append(f"           missing:  {h['what_was_missing']}")
        L.append(f"           filled:   {h['resolution_choice']}")
    if len(hs) > 20:
        L.append(f"  ... and {len(hs) - 20} more (use --json for the full list)")
    return "\n".join(L)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--comp", help="restrict to one competition_sample_name")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of human-readable table")
    p.add_argument("--top-fields", type=int, default=15, help="how many of the most-missed fields to show")
    args = p.parse_args()

    paths = discover_reports(args.comp)
    if not paths:
        print("no missing_info_report.json files found under", RUNS_ROOT, file=sys.stderr)
        return 1
    reports = [r for p_ in paths if (r := load_report(p_)) is not None]
    if not reports:
        print("found reports but none parsed", file=sys.stderr)
        return 1

    agg = aggregate(reports)
    if args.json:
        json.dump(agg, sys.stdout, indent=2)
        print()
    else:
        print(render_human(agg, args.top_fields))
    return 0


if __name__ == "__main__":
    sys.exit(main())
