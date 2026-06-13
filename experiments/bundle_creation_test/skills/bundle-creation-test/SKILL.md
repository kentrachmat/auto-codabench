---
name: bundle-creation-test
description: End-to-end bundle-creation test experiment runner. Load this skill when the user says something like "run the bundle-creation experiment on <competition_sample_name>" or "test the autocodabench bundle pipeline against <comp>". You orchestrate a SHELL-OUT pipeline (plan → implement+self-validate → per-sub reformat+run → log-audit → finalize) by invoking `claude -p` for each phase against the packaged autocodabench skills, with a single log-audit subagent per ground-truth submission via the Task tool. MUST run at top-level (Task tool required for log auditors).
---

You are running the bundle-creation-test experiment harness defined in
[`experiments/bundle_creation_test/README.md`](../../README.md). Read
that file first if you have not — it has the full layout and rationale
for the shell-out-driven architecture.

## What this skill does

You — the top-level Claude Code session — are the orchestrator. You
receive a `competition_sample_name` (e.g. `style-trans-fair`), compute
a `run_id`, create the run dir, and drive five phases:

1. **Preconditions** — verify the competition dir is well-formed; record expected scores.
2. **Plan** (shell-out) — `claude -p "/autocodabench-plan ..."` produces `specs/implementation_plan.md`.
3. **Implement + self-validate** (shell-out) — `claude -p "/autocodabench-implement ..."` writes the bundle, lints it, runs its OWN baseline + starting-kit notebook in a per-run conda env, iterates on runtime errors until both pass. STRICT: if the implementer can't get its own bundle to run, the experiment fails here.
4. **Score each ground-truth submission** (per sub_N): shell-out `claude -p "/autocodabench-reformat-and-run ..."` to adapt the GT code to the bundle's interface and run it through scoring; then spawn a `submission-log-auditor` SUBAGENT to compare the produced score to the GT's `expected_result.json` (within tolerance).
5. **Finalize** — aggregate missing-info, write `manifest.json` + `run_report.md`, clean up the per-run conda env.

The shell-out architecture exists because subagents cannot spawn
further subagents (Task is depth-limited at 1). `claude -p` invocations
are independent root-level Claude sessions — each one has fresh
subagent depth and can drive its own inner iteration loops (the
`autocodabench-implement` skill's runtime-validation loop, the
`autocodabench-reformat-and-run` skill's adapter loop). The
orchestrator only spawns ONE kind of in-process subagent: the
log-auditor (lightweight, no recursion needed).

## Hard rules — data leakage prevention

These exist because the whole experiment is only valid if the
isolation chain holds.

- **You MUST NOT read `<comp>/input/**`** — that's planner-only.
- **You MUST NOT read `<comp>/ground_truth/bundle/**`** — the golden
  reference, reserved for human comparison.
- **You MUST NOT read `<comp>/ground_truth/sample_submissions/*/submission/**`**
  — that's reformat-and-run-only.
- **You MAY read `<comp>/ground_truth/sample_submissions/*/expected_result.json`**
  — for recording in the manifest AND for handing to the
  log-auditor. Don't paste its score into any `claude -p` prompt.
- **You MUST NOT pass content from one shell-out into another**
  except via on-disk paths. The point of the shell-out architecture
  is each phase reads files; you don't paraphrase them.
- **Write only inside `runs/<comp>/<run_id>/`.** Source materials under
  `competitions/<comp>/` are immutable from your perspective.
- **Single-invocation rule**: one skill activation = one experiment run.
- **No retries inside a phase.** Phases 1/2/3 each get one shot. If
  they fail, record `fail_at_<phase>` and stop. (The shell-outs
  themselves run their own internal iteration loops — but the
  orchestrator does NOT re-spawn them.)
- **Synthetic data is forbidden.** If the implementer or
  reformat-and-run reports it generated stand-in data because the
  real source was inaccessible, fail the run with
  `fail_at_synthetic_data_detected`. Do NOT mask this — the whole
  experiment is invalid if the bundle silently runs on toy data
  when the proposal specified a real dataset.

## What you receive

A `competition_sample_name` (the subdir under
`experiments/bundle_creation_test/competitions/`). Example:
`style-trans-fair`.

---

## Pipeline

### 0. Compute `run_id` and create the run dir

```bash
SHORT_SHA=$(git rev-parse --short=8 HEAD)
UTC_TS=$(date -u +%Y%m%d_%H%M%S)
RUN_ID="${SHORT_SHA}_${UTC_TS}"
BRANCH=$(git branch --show-current)
COMP=<competition_sample_name>
REPO_ROOT="$(pwd)"
COMP_DIR="${REPO_ROOT}/experiments/bundle_creation_test/competitions/${COMP}"
RUN_ROOT="${REPO_ROOT}/experiments/bundle_creation_test/runs/${COMP}/${RUN_ID}"

mkdir -p "${RUN_ROOT}"/{specs,bundles,run_logs,reformat_run,log_audit}
```

Resolve `${COMP_DIR}` and `${RUN_ROOT}` to absolute paths once and reuse
them — never use relative `../../../` walks in the `claude -p` prompts.
Shell-outs run with their own cwd; relative paths quietly point at the
wrong place.

Write `<RUN_ROOT>/meta.json` (the MCP server's `open_run` will
*adopt* this dir when shell-outs set `AUTOCODABENCH_RUN_DIR` to it):

```json
{
  "branch_id": "<BRANCH>",
  "runtime_id": "<RUN_ID>",
  "started_at": "<iso-utc>",
  "slug": null,
  "experiment": "bundle_creation_test",
  "competition": "<COMP>"
}
```

Write `<RUN_ROOT>/manifest.json`:

```json
{
  "competition_sample_name": "<COMP>",
  "run_id": "<RUN_ID>",
  "branch": "<BRANCH>",
  "started_at": "<iso-utc>",
  "finished_at": null,
  "expected_results": {},
  "phases": [],
  "overall_status": "in_progress"
}
```

### 1. Preconditions

- `<comp>/input/` exists and is non-empty (you can `ls` it but **NOT
  read** its files).
- `<comp>/ground_truth/sample_submissions/` has at least one `sub_N/`.
- For each `sub_N`, read `sub_N/expected_result.json` and validate it
  has `metric` / `score` / `tolerance`. Populate
  `manifest.expected_results[sub_N]`.
- On any precondition failure: `overall_status = "fail_at_preconditions"`,
  add a phase entry, exit.

### 2. Plan (shell-out)

```bash
PLAN_PROMPT='Run AutoCodabench Phase 1 in NON-INTERACTIVE mode.

input_dir:   '"${COMP_DIR}/input/"'
output:      save the plan via autocodabench_snapshot_spec(name="implementation_plan.md", ...).

The proposal paper / design doc is under input_dir. Read it directly,
infer the 7 design sections of the Codabench competition (task, data,
metric, baseline, rules, ethics, schedule), and write the full plan
without asking any questions. Make sensible decisions for ambiguous
fields and document each in an "Assumptions" section at the end of
the plan.

When done, emit a single JSON object as your last message with shape:
  { "status": "pass" | "fail",
    "plan_path": "specs/implementation_plan.md",
    "sections_covered": ["1.task", "2.data", "3.metric", "4.baseline", "5.rules", "6.ethics", "7.schedule"],
    "info_gaps_count": <int>,
    "error": null | "..." }
'

AUTOCODABENCH_RUN_DIR="${RUN_ROOT}" \
claude --print --dangerously-skip-permissions \
       --output-format stream-json --verbose --input-format text \
       "${PLAN_PROMPT}" \
       < /dev/null \
       > "${RUN_ROOT}/plan_session.jsonl" 2> "${RUN_ROOT}/plan_session.stderr"
```

**How to invoke this shell-out** (read carefully — getting this
wrong is the most common harness friction):

- **Always launch with `run_in_background: true`.** Plan shell-outs
  take 5–30 minutes; the foreground Bash tool's 2-minute default
  timeout will kill them.
- **`< /dev/null` is required.** `claude --print` waits 3 seconds
  for stdin even when a prompt arg is given — redirect to skip the
  wait.
- **`--output-format stream-json --verbose`** emits one JSON line
  per session event (`type: system|user|assistant|result`). The
  text-mode default buffers everything until exit, which masks
  progress and leaves you blind to mid-run failures.
- **After launching, do NOT poll.** No `sleep`, no `tail`, no
  `BashOutput` peeks. The harness will notify you when the
  background process exits. Sleeping a few hundred ms and then
  checking the log is wasted budget and the harness will block it
  anyway.
- **When you receive the completion notification**, parse the result
  line: `tail -n 50 ${RUN_ROOT}/plan_session.jsonl | grep '"type":"result"' | tail -n 1`
  gives you a single JSON object with `subtype`, `is_error`,
  `result` (the final assistant text), `num_turns`, `total_cost_usd`.
  The inner JSON payload your prompt requested is inside `.result`
  (the assistant message body, last paragraph of which is the
  JSON object you asked for).

Parse the JSON object from `.result`. Append a phase entry:
`{name: "plan", status, started_at, finished_at, num_turns, total_cost_usd, agent_summary, artifacts}`.
If `status == "fail"`, `is_error == true`, or the file
`specs/implementation_plan.md` doesn't exist after the shell-out:
`overall_status = "fail_at_plan"`, stop.

**No retries.** A failed plan is a defect to investigate, not to
paper over.

### 3. Implement + self-validate (shell-out)

```bash
IMPL_PROMPT='Run AutoCodabench Phase 2 in NON-INTERACTIVE mode.

plan_path:    '"specs/implementation_plan.md"'
sample_data:  '"${COMP_DIR}/input/sample_data/"' (read-only reference for dataset shape; do not copy data outside this path into the bundle)

Per your skill body:
  1. Read the plan (autocodabench_current_run → Read specs/implementation_plan.md).
  2. Write the full bundle (init + scoring + ingestion if γ-style + solution + pages + data + competition.yaml).
  3. Lint via autocodabench_validate_bundle. Fix any issues.
  4. Prepare the per-run conda env via autocodabench_prepare_run_env.
  5. Run the bundle baseline via autocodabench_run_baseline_submission. Iterate up to 5 attempts on runtime errors (install extras for ModuleNotFoundError; edit bundle code for API breaks).
  6. Run the starting-kit notebook via autocodabench_run_starting_kit. Iterate up to 4 attempts.
  7. STRICT: both 5 and 6 must finish ok before you zip. If either fails after the attempt cap, do not zip, emit the failure-shape JSON.

When done, emit a single JSON object as your last message with shape:
  { "status": "pass" | "fail",
    "slug": "<bundle slug>",
    "bundle_dir": "<absolute path>",
    "zip_path": "<absolute path | null>",
    "validate_bundle": <bool>,
    "validate_runtime": <bool>,
    "env_name": "<...>",
    "baseline_status": "pass" | "fail",
    "baseline_scores": { ... } | null,
    "baseline_attempts_used": <int>,
    "notebook_status": "pass" | "fail",
    "notebook_cells_executed": <int> | null,
    "notebook_attempts_used": <int>,
    "info_gaps_count": <int>,
    "error": null | "..." }
'

AUTOCODABENCH_RUN_DIR="${RUN_ROOT}" \
claude --print --dangerously-skip-permissions \
       --output-format stream-json --verbose --input-format text \
       "${IMPL_PROMPT}" \
       < /dev/null \
       > "${RUN_ROOT}/implement_session.jsonl" 2> "${RUN_ROOT}/implement_session.stderr"
```

Same invocation discipline as phase 2: `run_in_background: true`,
`< /dev/null`, `stream-json --verbose`, no polling. Wait for harness
notification, then grep the result line. Implement shell-outs are
the longest in the pipeline (bundle write + lint + env clone +
baseline run + notebook run, often 15–45 minutes) — budget for it
and don't be tempted to peek.

Parse the final JSON. Append a phase entry. If `status == "fail"` or
`validate_runtime == false`: `overall_status = "fail_at_implement"`,
record the failure, skip phase 4. (We still proceed to phases 5/6 so
the missing-info report and run_report.md land — those are the
forensic artifacts.)

If success, record:
- `slug` → `manifest.bundle_slug`
- `env_name` → `manifest.env_name` (for cleanup at finalize)
- `bundle_dir` → `manifest.bundle_dir`

### 4. Score each ground-truth submission (per sub_N)

Only runs if phase 3 succeeded. For each `sub_N` under
`<comp>/ground_truth/sample_submissions/`:

#### 4a. Reformat + run (shell-out)

```bash
SUB="sub_N"
SUB_DIR="${COMP_DIR}/ground_truth/sample_submissions/${SUB}/submission/"
OUT_DIR="${RUN_ROOT}/reformat_run/${SUB}/"
mkdir -p "${OUT_DIR}"

RFR_PROMPT='Run AutoCodabench Reformat-and-Run NON-INTERACTIVE.

bundle_dir:     '"${BUNDLE_DIR}"'
submission_dir: '"${SUB_DIR}"'
env_name:       '"${ENV_NAME}"'
out_dir:        '"${OUT_DIR}"'

Per your skill body: probe the env, learn the bundle interface, adapt
the submission to the bundle, run via autocodabench_run_user_submission,
iterate up to 4 attempts. You have no access to expected_result.json
or any other ground-truth metadata.

Emit a single JSON object as your last message with the shape your
skill body specifies (status, attempts_used, final_attempt_dir,
logs_dir, scores, stage_failed, error, extras_installed,
adapter_notes).
'

AUTOCODABENCH_RUN_DIR="${RUN_ROOT}" \
claude --print --dangerously-skip-permissions \
       --output-format stream-json --verbose --input-format text \
       "${RFR_PROMPT}" \
       < /dev/null \
       > "${RUN_ROOT}/reformat_run/${SUB}/session.jsonl" \
       2> "${RUN_ROOT}/reformat_run/${SUB}/session.stderr"
```

Same invocation discipline (`run_in_background: true`, `< /dev/null`,
`stream-json --verbose`, no polling).

The shell-out writes `${OUT_DIR}/final.json` and `${OUT_DIR}/attempt_<K>/`.

Parse the JSON. **Do not skip on failure** — even a failed
reformat-and-run is a valid input to the log auditor (it'll report
"no score produced"). Move directly to 4b.

#### 4b. Audit (Task → `submission-log-auditor`)

Spawn the auditor:

> Audit one submission's reformat-and-run output against its expected
> result.
>
> sub_label:             `${SUB}`
> reformat_run_dir:      `${OUT_DIR}` (contains `final.json`, `attempt_<K>/`, `session.jsonl`)
> bundle_run_logs_dir:   `<RUN_ROOT>/run_logs/<slug>/<sub_label>.attempt_<K>/` (the sandbox + stdout/stderr for the final attempt)
> expected_result_path:  `<comp>/ground_truth/sample_submissions/<SUB>/expected_result.json`
> out_path:              `<RUN_ROOT>/log_audit/<SUB>/verdict.json`
>
> Your final message: the JSON object specified in your skill body.

When the auditor returns, parse its JSON. Aggregate per-sub into a
phase entry:

```json
{
  "name": "score_submissions",
  "status": "pass" | "fail",
  "submissions": [
    {
      "sub": "sub_1",
      "reformat_run_status": "pass" | "fail",
      "reformat_attempts_used": <int>,
      "audit_status": "pass" | "fail",
      "scores": <dict | null>,
      "expected_score": <float>,
      "actual_score": <float | null>,
      "tolerance": <float>,
      "delta": <float | null>,
      "within_tolerance": <bool | null>,
      "error": null | "..."
    }
  ]
}
```

`status = "pass"` iff every sub's `audit_status == "pass"` AND
`within_tolerance == true`. On fail: `overall_status =
"fail_at_score_submissions/sub_N"` (record the first failing sub).

### 5. Aggregate the missing-info inventories

Per [`MISSING_INFO.md`](../../MISSING_INFO.md). The plan-side
inventory lives at `<RUN_ROOT>/specs/missing_info_inventory.json` (if
the planner emitted it); the implement-side at
`<RUN_ROOT>/bundles/<slug>/missing_info_inventory.json` (ditto). If
neither exists (planner hard-failed pre-emit), write a stub
`missing_info_report.json` with empty `items: []`.

Concatenate, re-tag with `stage`, re-ID, compute totals,
narrative_summary. Write
`<RUN_ROOT>/missing_info_report.json` per the aggregated schema.

Add a `missing_info_summary` block to `manifest.json` with the
`totals` from the report.

### 6. Finalize

- **Clean up the per-run conda env**: invoke
  `autocodabench_remove_run_env(env_name)` if `manifest.env_name` is
  populated. Best-effort — failure logs to manifest but doesn't
  change `overall_status`.
- Set `finished_at` = ISO-UTC now.
- Set `overall_status = "pass"` iff every phase passed AND every
  sub_N's `within_tolerance == true`. Otherwise
  `fail_at_<first_failed_phase>`.
- Write final `manifest.json`.
- Write `run_report.md` per the template below.

### 7. Write `run_report.md` (human-readable run summary)

This is the primary deliverable for a human reviewer. Write it every
run, especially on failure. Path:
`<RUN_ROOT>/run_report.md`.

Template (fill in everything in braces; keep section headings
verbatim for grep-across-runs):

```markdown
# Run report — bundle-creation-test

**Competition:** {comp}
**Run ID:** {run_id}
**Branch:** {branch}
**Started:** {started_at}
**Finished:** {finished_at} ({duration_human})
**Overall status:** {overall_status}

---

## Summary table

| phase                 | status | notes |
|-----------------------|--------|-------|
| preconditions         | {pass|fail|—} | {one-line: K subs discovered} |
| plan                  | {pass|fail|—} | {sections covered, citations, info-gap count, X critical} |
| implement+selfvalidate| {pass|fail|—} | {slug, validate_bundle=Y/N, validate_runtime=Y/N, baseline_attempts=K/5, notebook_attempts=K/4} |
| score_submissions     | {pass|fail|—} | {K/N subs within tolerance} |
| sub_<N>               | {pass|fail|—} | {reformat_attempts=K/4, actual vs expected, Δ, within_tolerance} |
| ...                   | ...    | ... |

(Rows for phases that never ran show status `—` and a "not reached" note.)

---

## What happened

{1–3 paragraphs of analysis. Per failure, explicitly state what the
defect was and why no recovery was attempted. Concrete: "the
implementer's baseline run failed at attempt 5/5 with abseil ABI
deadlock between TF 2.21 and pyarrow's libarrow.2400.dylib".}

---

## Environment notes

{Anything the shell-outs surfaced in `notes` / `error` that's worth
flagging. If none: "No environment anomalies reported."}

---

## Missing-info summary

**Total:** {N} items ({P} from planner, {I} from implementer)

- by impact_area: bundle_functionality={A}, deployment_polish={B}, participant_experience={C}
- by severity: critical={X}, important={Y}, nice_to_have={Z}, best_practice={W}
- by resolution.action: inferred={inf}, default_applied={da}, deferred={def}, omitted={om}
- would_block_correct_scoring: {K} — high-stakes inferences worth a human pass
- would_have_asked_user_if_interactive: {Q}

### Highest-stakes items

(Top 10 from missing_info_report.json by would_block_correct_scoring,
or "None" if zero.)

1. **{section}.{field}** ({severity}, confidence={conf})
   - Missing: {what_was_missing}
   - Filled: {resolution.choice}
   - Rationale: {resolution.rationale}

---

## Artifacts

- Plan: `specs/implementation_plan.md`
- Plan session log: `plan_session.jsonl`
- Bundle: `bundles/{slug}/` ({size_kb} KB)
- Bundle zip: `bundles/{slug}.zip` ({"produced" | "not produced — runtime validation failed"})
- Implement session log: `implement_session.jsonl`
- Per-sub reformat runs: `reformat_run/sub_<N>/` (session.jsonl + attempt_<K>/ + final.json)
- Per-sub audits: `log_audit/sub_<N>/verdict.json`
- Bundle run_logs: `run_logs/{slug}/` (env, baseline, starting_kit, sub_<N>.attempt_<K>/)
- Missing-info report: `missing_info_report.json` ({N} items)
- Manifest: `manifest.json`

---

## Run dir

`./experiments/bundle_creation_test/runs/{comp}/{run_id}/`
```

Then print the same summary table to the chat:

```
Experiment: <comp> · run_id: <run_id> · status: pass | fail_at_<phase>

| phase                  | status | notes                                                              |
|------------------------|--------|--------------------------------------------------------------------|
| preconditions          | pass   | <N> subs discovered                                                |
| plan                   | pass   | 7 sections, <K> citations, <M> info gaps (<X> critical)            |
| implement+selfvalidate | pass   | slug:<slug>, baseline 1/5, notebook 1/4, env:<env>                 |
| score_submissions      | pass   | <K>/<N> subs within tolerance                                      |
| sub_1                  | pass   | reformat 1/4; actual 0.000, expected 0.000, Δ 0.000 (within 0.001) |
| ...                    | ...    |                                                                    |

Missing-info report: <M+K> items total
  ...
Full report: runs/<comp>/<run_id>/missing_info_report.json

run dir: ./experiments/bundle_creation_test/runs/<comp>/<run_id>/
```
