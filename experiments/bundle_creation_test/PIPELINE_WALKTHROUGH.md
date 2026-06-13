# Bundle-creation-test — Pipeline walkthrough

A presentation-ready narrative of how the `bundle_creation_test`
experiment runs end-to-end against one competition sample. For the
technical reference (file layouts, schemas, edge cases), see
[`README.md`](./README.md); this document tells the **story** of one
successful run from `claude` prompt to final verdict.

---

## What the test answers

> Given only a research proposal (e.g. a PDF + a tiny sample dataset),
> can an LLM agent produce a Codabench competition bundle that scores
> a known reference submission to the same number a human author got
> on the real Codabench platform — within tolerance?

If yes, the bundle-creation pipeline is functional. If the score
diverges (or never lands), we know exactly which of the seven phases
broke and why.

This isn't testing whether the bundle "looks right." It's testing
whether the bundle, run end-to-end against an external submission,
produces the *correct number*.

---

## The experiment in three sentences

1. **An LLM agent reads a proposal** and produces a Codabench
   `implementation_plan.md` covering 7 design sections (task, data,
   metric, baseline, rules, ethics, schedule).
2. **A second LLM agent takes that plan**, writes a complete bundle
   (`competition.yaml`, scoring program, ingestion program, sample
   solution, starting-kit notebook, data dirs), then **runs it
   end-to-end** to prove its own baseline + notebook execute cleanly.
3. **A third LLM agent adapts a held-out reference submission** to
   the bundle's interface and runs it through ingestion+scoring; a
   small auditor agent compares the produced score to the score the
   real Codabench recorded for that same submission.

If all three agents complete and the auditor sees a score within
tolerance, the bundle is correct.

---

## Why three agents (and not one)

The pipeline runs each phase as a **fresh `claude --print` shell-out
session**, not as a Task subagent. That's because:

- **Subagent depth is capped at 1.** A Task-spawned subagent cannot
  itself spawn another subagent.
- **Each phase needs its own inner loop.** The implementer iterates
  on runtime errors (install missing packages, port broken APIs); the
  reformatter iterates on submission-interface mismatches. Each loop
  is a real conversation with real tools.
- **Shell-outs are root-level sessions.** They get a fresh subagent
  budget and a fresh context, and they share state with the
  orchestrator only through (a) the on-disk run directory and (b) a
  single JSON object emitted as their last assistant message.

The single in-process subagent the orchestrator does spawn is the
log auditor — a cheap, single-turn verdict agent that doesn't need
to spawn anything itself.

---

## A successful run, phase by phase

The walkthrough below uses real numbers from a `style-trans-fair`
run. Wall-clock totals add to ≈30–45 minutes; LLM cost ≈$10–15
depending on how many runtime fixes the implementer iterates on.

### Phase 0 — Setup (1 second)

The orchestrator computes a `run_id` from git SHA + timestamp and
creates the run directory:

```
experiments/bundle_creation_test/runs/style-trans-fair/<sha>_<utc>/
├── meta.json               # makes the dir AUTOCODABENCH_RUN_DIR-adoptable
├── manifest.json           # phase-by-phase structured log (orchestrator-owned)
├── specs/                  # plan-phase will write here
├── bundles/                # implement-phase will write here
├── run_logs/               # subprocess output (live-tee'd, tail -f works)
├── reformat_run/           # per-sub adapter + scoring output
└── log_audit/              # per-sub verdict
```

It also reads `ground_truth/sample_submissions/sub_N/expected_result.json`
for every reference submission and records the expected score +
tolerance in `manifest.expected_results`. **The orchestrator never
reads the submission's actual code** — that's reformat-and-run's job.

### Phase 1 — Preconditions (1 second)

A small `ls`-only check: `competitions/<comp>/input/` exists and is
non-empty, every `sub_N/expected_result.json` parses, the metric
name is well-formed.

If anything fails → `overall_status: fail_at_preconditions`, stop.
The experiment shouldn't proceed against a malformed competition
sample.

### Phase 2 — Plan (4 minutes, 20 LLM turns, ≈$1.04)

The orchestrator launches a **fresh root-level `claude --print`
session** with the `autocodabench-plan` skill loaded:

```bash
AUTOCODABENCH_RUN_DIR=<run> claude --print \
    --output-format stream-json --verbose \
    "Run AutoCodabench Phase 1 NON-INTERACTIVE.
     input_dir: <comp>/input/
     output: save the plan via autocodabench_snapshot_spec(...)
     ..." \
  < /dev/null \
  > plan_session.jsonl 2> plan_session.stderr
```

The fresh session has full Claude tools (Read, Write, Edit, Task,
WebFetch, MCP) and access to the proposal PDF + the sample dataset.
It produces `specs/implementation_plan.md` — a concrete spec naming
exactly which sklearn class to use as the baseline, which sklearn
metric function to compute, what input/output shapes are expected,
how data splits work, and where ambiguities were resolved by
assumption.

On exit the session emits one JSON object as its last assistant
message: `{status, plan_path, sections_covered, info_gaps_count}`.

The orchestrator parses the JSON, records the phase in
`manifest.json`, and proceeds.

### Phase 3 — Implement + self-validate (20 minutes, 79 LLM turns, ≈$7.30)

This is the longest and most consequential phase. A second
`claude --print` session loads `autocodabench-implement` and works
through eight sub-steps:

**3.1 Read the locked plan** — `specs/implementation_plan.md`.

**3.2 Write the bundle** — eight MCP file-writer calls produce:
```
bundles/style-trans-fair/
├── competition.yaml            (12 KB)
├── scoring_program/
│   ├── score.py                (the metric function from plan §3)
│   ├── metadata.yaml           (command: …)
│   └── requirements.txt
├── ingestion_program/          (γ-style only)
│   ├── ingestion.py
│   ├── metadata.yaml
│   └── requirements.txt
├── solutions/solution_baseline/
│   ├── model.py                (the sklearn class from plan §4)
│   └── README.md
├── pages/                      (4 markdown pages)
├── reference_data/             (held-out labels)
├── input_data/                 (participant-facing features)
├── public_data/                (downloadable starting data)
└── README.ipynb                (the starting-kit notebook)
```

**3.3 Lint the bundle** — `autocodabench_validate_bundle(slug)`
checks the schema: every file `competition.yaml` references exists,
every leaderboard column key appears in `scores.json`, every
`metadata.yaml` has a `command:`, etc. Errors are surfaced and the
implementer fixes them. Lint must be clean before proceeding.

**3.4 Prepare the per-run env** — `autocodabench_prepare_run_env(slug)`
clones the host's base conda env into a new env named
`acb-run-<short_run_id>` and installs the union of the bundle's
per-program `requirements.txt` files via `uv pip install`. Logs at
`run_logs/<slug>/env/`.

**3.5 Run the bundle's own baseline** —
`autocodabench_run_baseline_submission(slug, env_name)` stages a
sandbox containing:
```
sandbox/
├── program/scoring_program/       (copy of the bundle's)
├── program/ingestion_program/     (copy, if γ-style)
├── input/ref/                     (reference_data)
├── input/res/                     (predictions land here)
└── submission/                    (the bundle's own model.py)
```
and runs ingestion → scoring through the cloned env's python. Logs
stream live to disk so `tail -f run_logs/<slug>/baseline/ingestion_stdout.txt`
shows training progress in real time. On success, the parsed
`scores.json` is returned in the result dict.

If the baseline fails (missing package, broken API, path bug), the
implementer reads the stderr and tries one of:
- `install_env_extras(env_name, ["<pypi_name>"])` for a `ModuleNotFoundError`
- `Edit` the bundle file containing the broken API call
- `Edit` the script that has a path bug

Cap: 5 attempts. If it still fails, the implementer exits with
`validate_runtime: false` and the bundle is NOT zipped.

**3.6 Run the starting-kit notebook** —
`autocodabench_run_starting_kit(slug, env_name)` executes
`README.ipynb` end-to-end via `jupyter execute --inplace` in the
same env. Every code cell must run cleanly. Same retry loop, cap 4.

**3.7 Zip the bundle** — only reached if both 3.5 and 3.6 pass.
`autocodabench_zip_bundle(slug)` produces
`bundles/<slug>/<slug>.zip` with `competition.yaml` at the zip
root (the most common Codabench upload pitfall, eliminated).

**3.8 Emit final JSON** — `{status, slug, bundle_dir, zip_path,
validate_bundle, validate_runtime, env_name, baseline_status,
baseline_scores, baseline_attempts_used, notebook_status, ...}`.

The orchestrator parses, records, and proceeds only if
`validate_runtime: true`. A bundle whose own baseline can't run is a
broken bundle.

### Phase 4 — Per-sub reformat-and-run + audit (5–15 min per sub)

This phase loops over every `ground_truth/sample_submissions/sub_N/`.

#### 4a. Reformat + run (shell-out)

A third `claude --print` session loads `autocodabench-reformat-and-run`.
It receives four arguments: `bundle_dir`, `submission_dir`,
`env_name`, `out_dir`. It has access to the GT submission's **code**
but is strictly blind to `expected_result.json` — the experiment's
correctness depends on this isolation.

Its loop:

1. **Probe the env** — `conda run -n <env> pip list` to learn which
   libraries are installed at which versions. Saves `env_probe.txt`.
2. **Learn the bundle's interface** — read `competition.yaml`,
   `ingestion.py`, `solutions/solution_baseline/model.py`. Now it
   knows what shape the submission must conform to: file naming,
   class name, method signatures, input/output dtypes.
3. **Read the GT submission's code** — note: this is the original
   author's submission, written for whatever interface that author
   knew. It probably doesn't match the bundle's interface exactly.
4. **Write the adapted submission to `attempt_1/`** — same model
   architecture, same hyperparameters, same loss, same seed — only
   the **interface** is adapted to match the bundle. Adapter notes
   document every old→new substitution.
5. **Run it** — `autocodabench_run_user_submission(slug, env_name,
   submission_dir=attempt_1/, label="sub_N.attempt_1")` stages the
   same sandbox as the baseline did, ingestion → scoring, returns
   `scores`.
6. **If it failed** — diagnose stderr, fix in `attempt_2/`, run again.
   Cap: 4 attempts. Adapter discipline forbids changing what the
   model does — only how it interfaces with the bundle.

On success, `final.json` lands at `reformat_run/<sub_N>/final.json`:
```json
{
  "status": "pass",
  "attempts_used": 1,
  "final_attempt_dir": ".../attempt_1/",
  "scores": {"gm_accuracy": 0.247, "wga_accuracy": 0.05, ...},
  "extras_installed": ["tensorflow"],
  "adapter_notes": [...]
}
```

#### 4b. Log audit (in-process subagent)

The orchestrator now spawns its **one** Task subagent —
`submission-log-auditor`. The auditor receives:
- `final.json` (the reformatter's output)
- `run_logs/<slug>/sub_N.attempt_<K>/` (the sandbox + raw stdout/stderr)
- `expected_result.json` (the score real Codabench recorded)

The auditor reads, compares within tolerance, writes
`log_audit/sub_N/verdict.json`:
```json
{
  "sub": "sub_1",
  "verdict": "pass",
  "within_tolerance": true,
  "metric_key": "geometric_mean_accuracy_metric",
  "actual_score": 0.0,
  "expected_score": 0.0,
  "tolerance": 0.001,
  "delta": 0.0,
  "reformat_attempts_used": 1
}
```

`verdict: "pass"` requires `|actual − expected| ≤ tolerance`. Other
verdicts: `fail` (out of tolerance), `no_score_produced` (the
reformatter couldn't run the bundle to completion),
`metric_mismatch` (the bundle's scores.json key doesn't match the
expected metric).

### Phase 5 — Aggregate missing-info (5 seconds)

The plan and implement phases each may have logged a
`missing_info_inventory.json` documenting where they made
inference decisions (the plan was ambiguous about output shape, the
proposal didn't specify a seed, etc.). The orchestrator merges them
into a single `missing_info_report.json` with cross-stage totals.
This drives meta-analysis across runs ("which proposal section is
most frequently underspecified?").

### Phase 6 — Finalize (1 minute)

- `autocodabench_remove_run_env(env_name)` — drop the cloned conda
  env (best-effort; failure logged but not fatal).
- Set `manifest.overall_status = "pass"` iff every phase passed AND
  every `sub_N`'s `within_tolerance == true`.
- Write final `manifest.json`.

### Phase 7 — Human-readable run report (1 second)

Write `run_report.md` — the single-screen summary a reviewer opens
first. Includes the phase table, what happened narrative,
missing-info summary, top inference decisions, artifact pointers.

The same summary table is printed to the chat.

---

## What each phase checks

| phase | what it asserts | how it asserts |
|---|---|---|
| 0 | run dir clean and writable | `mkdir` succeeds |
| 1 | competition sample is well-formed | input/ exists; expected_result.json parses |
| 2 | plan covers all 7 sections | `sections_covered` array, info-gap count |
| 3 lint | bundle is schema-valid | `autocodabench_validate_bundle` returns ok |
| 3 baseline | bundle is runtime-functional with its OWN baseline | bundle's `solutions/solution_baseline/model.py` runs through `ingestion_program/ingestion.py` → `scoring_program/score.py`, exits 0, produces parseable `scores.json` |
| 3 notebook | the starting-kit story works | `README.ipynb` executes top-to-bottom under `jupyter execute --inplace`, every code cell exits 0 |
| 4a reformat | GT code can be adapted to bundle interface | adapter completes, ingestion+scoring exit 0, scores parseable |
| 4b audit | bundle's score matches reality within tolerance | `\|actual − expected\| ≤ tolerance` |
| 5 | missing-info data is captured for meta-analysis | per-stage inventories merged, totals match |
| 6 | env cleaned | `conda env remove` ran (best-effort) |
| 7 | human can review the run in <5 minutes | run_report.md written |

The runtime checks at 3-baseline, 3-notebook, and 4a are the
**load-bearing** ones. Lint can pass on a bundle that doesn't run.
A bundle that doesn't run is broken regardless of how clean the YAML
is.

---

## The four data-isolation walls

The experiment is only valid if no agent sees the answer it's supposed
to derive. Four walls preserve this:

```
┌──────────────────────────────────────────────────────────────┐
│ ORCHESTRATOR                                                 │
│   may read:    expected_result.json (for the auditor's hand) │
│   MUST NOT:    input/**, ground_truth/bundle/**,             │
│                ground_truth/sample_submissions/*/submission/** │
└──────────────────────────────────────────────────────────────┘
        ▲                              │
        │  JSON via stdout             │  Bash with AUTOCODABENCH_RUN_DIR
        │                              ▼
┌──────────────────────────────────────────────────────────────┐
│ PLAN SHELL-OUT  (autocodabench-plan)                         │
│   may read:    input/** (proposal + sample_data)             │
│   MUST NOT:    ground_truth/**                               │
└──────────────────────────────────────────────────────────────┘
        ▲                              │
        │  specs/implementation_plan.md│
        │                              ▼
┌──────────────────────────────────────────────────────────────┐
│ IMPLEMENT SHELL-OUT  (autocodabench-implement)               │
│   may read:    specs/implementation_plan.md +                │
│                input/sample_data/** only                     │
│   MUST NOT:    input/report.pdf, ground_truth/**             │
└──────────────────────────────────────────────────────────────┘
        ▲                              │
        │  bundle dir (read-only)      │
        │                              ▼
┌──────────────────────────────────────────────────────────────┐
│ REFORMAT-AND-RUN SHELL-OUT  (autocodabench-reformat-and-run) │
│   may read:    bundle/** +                                   │
│                ground_truth/sample_submissions/sub_N/        │
│                  submission/** (the code only)               │
│   MUST NOT:    expected_result.json, ground_truth/bundle/**, │
│                report.pdf, plan                              │
└──────────────────────────────────────────────────────────────┘
        ▲                              │
        │  final.json (scores only)    │
        │                              ▼
┌──────────────────────────────────────────────────────────────┐
│ LOG AUDITOR  (submission-log-auditor, in-process subagent)   │
│   may read:    final.json + expected_result.json +           │
│                run_logs/<slug>/sub_N.attempt_<K>/ (sandbox)  │
│   MUST NOT:    the submission's original code, plan, bundle, │
│                report.pdf                                    │
└──────────────────────────────────────────────────────────────┘
```

Why each wall matters:

- **Plan can't see ground truth** → the plan reflects what the
  proposal says, not what makes the reference submission score well.
- **Implement can't see ground truth** → the bundle's interface
  isn't shaped to fit code it just read. If the implementer had read
  the GT submission first, it could trivially "succeed" by writing a
  bundle whose interface matches that one specific submission.
- **Reformat can't see `expected_result.json`** → the adapter can't
  hard-code predictions to match the target. It has to produce
  whatever score the GT model actually produces against the bundle's
  data + scoring.
- **Auditor can't see the submission's code** → its verdict is
  purely on the produced score vs the expected score, without
  visibility into whether the reformatter "tried hard enough" or
  cheated. The auditor is a structural comparison only.

---

## What "pass" means at the end

```
overall_status = "pass"
  iff every phase finished cleanly AND
      every sub_N's auditor verdict is "pass" AND
      within_tolerance is true for every sub_N
```

A single phase failure, a single out-of-tolerance score, or a single
"no_score_produced" verdict downgrades the whole run to
`fail_at_<first_failed_phase>`. The pipeline writes `run_report.md`
even on failure — especially on failure — because that's when
diagnostic content matters most.

---

## What the operator sees

The chat output at the end of a successful run:

```
Experiment: style-trans-fair · run_id: 0ffa4d20_20260603_180000 · status: pass

| phase                  | status | notes                                                                  |
|------------------------|--------|------------------------------------------------------------------------|
| preconditions          | pass   | 1 sub discovered (sub_1); expected score 0.0, tolerance 0.001          |
| plan                   | pass   | 7 sections, 6 citations, 7 info gaps (1 critical)                      |
| implement+selfvalidate | pass   | slug:style-trans-fair, baseline 1/5, notebook 1/4, env:acb-run-0ffa4d2 |
| score_submissions      | pass   | 1/1 subs within tolerance                                              |
| sub_1                  | pass   | reformat 1/4; actual 0.000, expected 0.000, Δ 0.000 (within 0.001)     |

Missing-info report: 7 items total
  by impact:    bundle_functionality=4, deployment_polish=2, participant_experience=1
  by severity:  critical=1, important=3, nice_to_have=3, best_practice=0
  would_block_correct_scoring: 0
Full report: runs/style-trans-fair/0ffa4d20_20260603_180000/missing_info_report.json

run dir: ./experiments/bundle_creation_test/runs/style-trans-fair/0ffa4d20_20260603_180000/
```

On disk the run directory contains everything needed to reproduce or
debug: the full Claude transcripts for each shell-out
(`*_session.jsonl`), the live-tee'd subprocess output
(`run_logs/<slug>/**`), the per-attempt adapter code
(`reformat_run/<sub_N>/attempt_<K>/`), the final verdict
(`log_audit/<sub_N>/verdict.json`), and the human-readable summary
(`run_report.md`).

---

## What failure looks like

The pipeline is **fail-loud and fail-located**. Each failure mode
points at exactly which phase broke:

| `overall_status` | what to look at first |
|---|---|
| `fail_at_preconditions` | the comp sample dir is malformed; check `expected_result.json` shape |
| `fail_at_plan` | `plan_session.jsonl` — what did the planner say? was the proposal unreadable? |
| `fail_at_implement` | `implement_session.jsonl` + `run_logs/<slug>/baseline/` — the bundle's own baseline couldn't run after 5 attempts |
| `fail_at_score_submissions/sub_N` | `reformat_run/sub_N/session.jsonl` + `log_audit/sub_N/verdict.json` — either reformat hit its 4-attempt cap or the produced score is out of tolerance |

The shell-out architecture means each failure is captured as a
complete Claude transcript: you can read exactly what the failing
agent thought it was doing and where it got stuck. No state is
hidden in the orchestrator's context.

---

## What the harness handles automatically

A few host-side robustness measures that aren't visible in normal
operation but kick in when things go wrong:

- **Live stdout/stderr tee** — every subprocess writes its output to
  disk line-by-line as it runs. `tail -f run_logs/<slug>/sub_1.attempt_1/ingestion_stdout.txt`
  shows training progress in real time, so you can tell a slow run
  apart from a hung one without waiting for it to finish.
- **Process-group kill on timeout** — if a subprocess hangs past
  30 minutes, the harness kills the entire process group (conda run
  → bash → python), not just the direct child. No orphan processes.
- **Single-thread BLAS/OMP/TF defaults** — every subprocess inherits
  `OMP_NUM_THREADS=1` + sibling vars at .so-load time, preventing
  the macOS libomp deadlock that hangs multi-threaded TF training on
  some hosts. The defaults disappear when running on Codabench's
  Linux workers (which use the docker image's settings).
- **`bash -c`, not `bash -lc`** — the harness never invokes a login
  shell to wrap bundle commands. Login shells source the user's
  profile, which can run `conda init` and clobber `CONDA_PREFIX`,
  sending the bundle to the wrong python. `bash -c` preserves the
  env set by `conda run`.

---

## Why this design

The pipeline reflects three deliberate choices about how an
LLM-driven experimental harness should work:

**1. Each phase is a separate Claude session.**
Not a Task subagent (depth-limited), not a coroutine in the
orchestrator's context (context-window-limited). A fresh root
session has full subagent budget, fresh context, focused instructions
— exactly what each phase needs. They communicate only through
files and a single JSON object per phase. That makes each phase
independently re-runnable for debugging.

**2. The implementer runs what it writes.**
The previous design version had the implementer write the bundle
then hand it off to a separate "validator" subagent. That created a
gap: the implementer believed the bundle was done; the validator
discovered it didn't actually run. Now the implementer's success
criterion explicitly includes "and your own baseline runs cleanly
end-to-end" — the bundle either ships proven or doesn't ship.

**3. The auditor is mechanical.**
The auditor doesn't try to understand the submission, the proposal,
or the bundle. It compares two floats and writes a verdict. All the
hard work (does the bundle exist, does it run, does it produce a
score) happens upstream. The auditor only answers "is the score
right?"  That's the experiment's actual question.

---

## Summary in one sentence

The pipeline takes a research proposal as input, has three LLM
agents collaborate (with strict data isolation between them) to
produce a Codabench bundle and run a held-out reference submission
through it, and outputs a verdict on whether the bundle scored that
submission to the number it really got — within tolerance, in
≈30–45 minutes, for ≈$10–15 of LLM cost per competition sample.
