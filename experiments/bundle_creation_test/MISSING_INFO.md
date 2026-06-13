# Missing-information inventory — structure + conventions

A competition proposal almost never spells out every detail needed to
build a working bundle. The plan-phase shell-out (running the
`autocodabench-plan` skill) has to fill gaps —
sometimes by inferring from context, sometimes by applying sensible
defaults, sometimes by punting. Each gap-filling decision is a piece of
**reproducible forensic data** that is more valuable than the
implementation_plan.md itself for two reasons:

1. **Per-run debug**: when the bundle-scoring step in the experiment
   pipeline produces a delta from the expected result, the first
   question is "where did the agent diverge from the original
   proposal?" — the inventory makes the answer mechanical instead of
   reading thousands of plan tokens.
2. **Meta-analysis across runs**: aggregating inventories across many
   competition samples surfaces patterns. "Proposals miss output_format
   60% of the time." "anti_cheating is missing in 90% of academic
   proposals but specified in 70% of industry ones." These trends drive
   targeted improvements to the autocodabench-plan skill itself.

This file is the **source of truth** for what gets logged, in what
shape, and how it's aggregated. Both the plan-phase and
implement-phase shell-outs (the `autocodabench-plan` and
`autocodabench-implement` skills) reference it. The orchestrator
skill aggregates per these conventions. Any tool reading these JSONs
(meta-analysis, dashboards, regression detection) treats this schema
as stable contract — version it in the file when changing.

---

## Schema version

```
1
```

Bump it when adding/removing/renaming fields. Old inventories should
still be parseable by tools that handle `schema_version` lookups.

---

## Where each file lives

```
runs/<comp>/<run_id>/
├── specs/
│   ├── implementation_plan.md
│   └── missing_info_inventory.json      ← written by the plan-phase shell-out (autocodabench-plan)
├── bundles/<slug>/
│   └── missing_info_inventory.json      ← written by the implement-phase shell-out (autocodabench-implement; smaller — gaps in the PLAN, not the proposal)
└── missing_info_report.json             ← written by the orchestrator skill, aggregates the two above + adds run-level totals
```

The orchestrator skill is the only writer of `missing_info_report.json`
and the only reader that needs to combine the two inventories. Tools
doing meta-analysis read **only** the aggregated `missing_info_report.json`
files — one per run.

---

## Per-item shape

Every gap the planner (or implementer) had to deal with becomes one
JSON object in the `items` array of its inventory:

```jsonc
{
  "id": "miss_001",                      // unique within this inventory (zero-padded counter)
  "stage": "planner",                    // "planner" | "implementer"
  "section": "task_definition",          // one of the controlled-vocabulary sections (below)
  "field": "output_format",              // free-form short label for the specific gap
  "what_was_missing": "Proposal says 'predict the class' but doesn't specify whether output is class indices, one-hot vectors, or probability distributions.",
  "severity": "critical",                // critical | important | nice_to_have | best_practice
  "impact_area": "bundle_functionality", // bundle_functionality | deployment_polish | participant_experience
  "resolution": {
    "action": "inferred",                // inferred | default_applied | deferred | omitted
    "choice": "Probability distribution over 9 classes (numpy ndarray of shape (n_samples, 9), each row summing to 1.0).",
    "rationale": "Standard for multi-task classification scored by geometric_mean_accuracy. The provided sample_data has 9 class subdirs under tasks/, so 9 outputs matches.",
    "alternatives_considered": ["class indices (ndarray of shape (n_samples,) ints)", "one-hot (ndarray of shape (n_samples, 9) {0,1})"],
    "confidence": "high",                // high | medium | low
    "would_block_correct_scoring": false // true iff getting this wrong would directly invalidate the scoring program
  },
  "would_have_asked_user_if_interactive": true,
  "trace": {                             // optional — concrete proposal reference for the gap
    "section_in_proposal": "report.pdf §2.3, paragraph 2",
    "verbatim_quote": "We ask participants to predict the class for each task."
  }
}
```

### Field definitions

**`section`** — controlled vocabulary. Pick the single best match.

| Value | Covers |
|---|---|
| `task_definition` | input shape, output shape, label types, problem class (classification / regression / etc.) |
| `data` | data sources, splits, train/val/test sizes, file format, preprocessing |
| `metric` | which metric, primary vs. secondary, tiebreaker, score range expectations |
| `baseline` | baseline model, expected baseline score, "what does random get" |
| `submission_format` | code-submission vs. result-submission, file format, code-execution timeout |
| `leaderboard` | columns, primary sort key, secondary sort key, tie-breaking |
| `phases` | dev/final phase boundaries, dates, max submissions, auto-migration |
| `anti_cheating` | per-day rate limits, duplicate-submission detection, IP/account banning, hashed-prediction tricks |
| `ethics` | data licensing, participant data handling, dual-use, conflict-of-interest disclosure |
| `documentation` | overview page, terms, FAQ, contact, citation guidance |
| `infrastructure` | docker_image choice, compute limits, GPU/CPU, memory cap, timeout |
| `other` | anything that doesn't fit — but try to fit. Add a new section to this doc + bump schema_version if `other` accumulates a pattern. |

**`field`** — free-form, but be consistent across runs (lowercase
snake_case). Examples: `output_format`, `train_test_split_ratio`,
`tiebreaker`, `daily_submission_limit`, `docker_image`,
`terms_jurisdiction`. The more agents converge on the same `field`
names for the same gap, the more powerful meta-analysis becomes.

**`severity`** — how much does this gap matter?

| Value | Meaning |
|---|---|
| `critical` | Bundle won't function (will fail validation or scoring) without this info. |
| `important` | Bundle works but produces clearly suboptimal participant experience (e.g. unclear instructions, missing baseline). |
| `nice_to_have` | Polish item; reasonable defaults exist and won't surprise participants. |
| `best_practice` | Industry standard or recommended by competition-design skill, but absent in most academic proposals. |

**`impact_area`** — what does this gap affect?

| Value | Meaning |
|---|---|
| `bundle_functionality` | Touches the scoring program, ingestion program, or data schemas. A wrong choice here can invalidate the bundle. |
| `deployment_polish` | Touches pages, phases, anti-cheating, terms — the "running a competition" surface. Wrong choices reduce quality but don't break scoring. |
| `participant_experience` | Touches documentation, examples, error messages, starting_kit clarity. |

**`resolution.action`** — what did the planner do about it?

| Value | Meaning |
|---|---|
| `inferred` | Filled in by reading context clues elsewhere in the proposal (e.g., sample_data shape) or by domain knowledge. |
| `default_applied` | Used a sensible default from the autocodabench skills' guidance (or Codabench defaults). |
| `deferred` | Left as a TODO in the plan; the implementer will need to make a choice or fail at it. |
| `omitted` | Decided not to include this aspect at all (e.g., decided no leaderboard tiebreaker is fine for v1). |

**`resolution.would_block_correct_scoring`** — be honest. `true` means
"if I got this wrong, the comparison-vs-expected at step 5 of the
experiment would fail or be invalid". This is the single most valuable
field for meta-analysis: it tells you which inferences carry real
stakes.

**`would_have_asked_user_if_interactive`** — if this experiment were
the production web app instead of unattended batch, would the planner
have surfaced this as a clarifying question? `true` indicates a gap
where automation took on risk the production UX wouldn't have.

---

## Per-stage inventory shape (`<plan|bundle>/missing_info_inventory.json`)

```jsonc
{
  "schema_version": 1,
  "stage": "planner",                    // "planner" | "implementer"
  "competition_sample_name": "style-trans-fair",
  "run_id": "abc12345_20260530_104500",
  "captured_at": "2026-05-30T10:45:00Z",
  "input_summary": {
    "files_read": ["report.pdf"],         // planner: paper files; implementer: ["plan/implementation_plan.md"]
    "total_chars": 45000,
    "overall_completeness": "medium",     // planner's gestalt: high | medium | low | unusable
    "completeness_notes": "Strong on data and metric; weak on submission format, ethics, and anti-cheating."
  },
  "items": [
    { /* per-item shape above */ },
    { /* ... */ }
  ]
}
```

The implementer's inventory typically has FEWER items (the plan is
much more constrained than a free-form proposal). Its items typically
fall under `infrastructure`, `submission_format`, or `bundle assembly
details`. A common implementer item: "plan says 'use 1×1 placeholder
image' but image format isn't specified — defaulted to PNG."

---

## Aggregated report shape (`<run>/missing_info_report.json`)

The orchestrator skill writes this after both stages return:

```jsonc
{
  "schema_version": 1,
  "competition_sample_name": "style-trans-fair",
  "run_id": "abc12345_20260530_104500",
  "branch": "experiment_test-bundle-creation",
  "generated_at": "2026-05-30T10:48:30Z",
  "stages_aggregated": ["planner", "implementer"],
  "items": [
    // The union of items from both stages' inventories, with stage tagged.
  ],
  "totals": {
    "all_items": 8,
    "by_stage": { "planner": 6, "implementer": 2 },
    "by_section": {
      "task_definition": 2, "data": 0, "metric": 1, "baseline": 0,
      "submission_format": 1, "leaderboard": 1, "phases": 0,
      "anti_cheating": 2, "ethics": 1, "documentation": 0,
      "infrastructure": 0, "other": 0
    },
    "by_severity": {
      "critical": 2, "important": 1, "nice_to_have": 3, "best_practice": 2
    },
    "by_impact_area": {
      "bundle_functionality": 3, "deployment_polish": 4, "participant_experience": 1
    },
    "by_resolution_action": {
      "inferred": 4, "default_applied": 3, "deferred": 1, "omitted": 0
    },
    "would_block_correct_scoring_count": 1,
    "would_have_asked_user_if_interactive_count": 4
  },
  "narrative_summary": "8 gaps surfaced (6 in the proposal, 2 in the plan). 2 critical: output format inferred from sample_data shape (high confidence); train/test split inferred from filenames (medium confidence). 1 gap is flagged as 'would_block_correct_scoring' — the output normalization convention. 4 of 8 gaps would have triggered a clarifying question in the production web UX. No gaps were left unresolved."
}
```

---

## Meta-analysis pattern

After many runs across many competition samples, aggregate with a
helper at [`scripts/aggregate_missing_info.py`](scripts/aggregate_missing_info.py)
or roll your own with jq:

```bash
# Top-10 fields that are most commonly missing across all runs:
find experiments/bundle_creation_test/runs -name "missing_info_report.json" \
  | xargs cat | jq -s '
    map(.items[]) | group_by(.field) | map({field: .[0].field, count: length})
    | sort_by(-.count) | .[:10]'

# Fraction of runs where a critical gap was inferred with low confidence
# (signals a planner-prompt improvement opportunity):
find experiments/bundle_creation_test/runs -name "missing_info_report.json" \
  | xargs cat | jq -s '
    {total_runs: length,
     low_conf_critical_runs: ([.[] | select(any(.items[]; .severity == "critical" and .resolution.confidence == "low"))] | length)}'

# Pattern view: which sections are most often missing per competition sample
find experiments/bundle_creation_test/runs -name "missing_info_report.json" \
  | xargs cat | jq -s '
    group_by(.competition_sample_name)
    | map({comp: .[0].competition_sample_name, runs: length,
           common_missing_sections: ([.[] | .totals.by_section | to_entries[] | select(.value > 0) | .key] | group_by(.) | map({section: .[0], hits: length}) | sort_by(-.hits)[:5])})'
```

---

## Edge cases the planner should handle

- **The proposal IS complete on a section** → don't invent gaps. The
  `items` array can have zero entries for sections that were fully
  specified.
- **A gap was filled by an explicit author choice** (e.g., the
  proposal says "we leave the docker image up to the bundle builder")
  → log it with `resolution.action = "default_applied"` and the
  `trace.verbatim_quote` showing the author's deferral.
- **Multiple gaps in one section** → emit one item per gap, with the
  same `section` value. Don't merge them — meta-analysis needs the
  granular `field`s.
- **A gap that affects multiple sections** → pick the primary one for
  `section`, mention the other(s) in `what_was_missing`.

The planner should treat the inventory as a **first-class
deliverable**, not an afterthought. A run with a clean plan but a
sloppy inventory is worse than a run with a slightly-fuzzy plan and a
thorough inventory — the meta-analysis is what makes this harness
worth running.
