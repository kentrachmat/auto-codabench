# bundle_creation_test

This directory contains the end-to-end evaluation harness for AutoCodabench. Given a paper or proposal as input, the harness exercises the full pipeline — plan, build, self-validate, and score known submissions — and then verifies whether the resulting scores match pre-recorded ground truth within a stated tolerance.

The harness is a wrapper around the `autocodabench` package: the experiment side orchestrates, while the substantive work (authoring the bundle, running its baseline, executing the starting-kit notebook, reformatting external submissions to the bundle's interface, and scoring them) is performed by the packaged skills and MCP tools. The two sides share a single on-disk run directory through the `AUTOCODABENCH_RUN_DIR` environment variable.

Future experiments (for example latency, cost, or robustness studies) reside in sibling `experiments/<other_name>/` directories.

---

## Architecture overview

The orchestrator skill (this directory's `SKILL.md`) is loaded into the top-level Claude session and drives the phases described below. Phases 2, 3, and 4a are shell-outs via `claude --print` to fresh Claude sessions running the packaged skills (`autocodabench-plan`, `autocodabench-implement`, `autocodabench-reformat-and-run`). Each shell-out constitutes its own root-level session, so it can spawn subagents and run its own internal iteration loops without encountering Claude Code's depth-1 subagent limit. Phase 4b, executed once per ground-truth submission, spawns a single in-process subagent (`submission-log-auditor`) that renders a verdict on the produced score against `expected_result.json`. Phases 5, 6, and 7 remain inside the orchestrator (missing-information aggregation, finalization, and the generation of `run_report.md`).

---

## Directory layout

The harness and its per-competition fixtures are organized as follows:

```
experiments/bundle_creation_test/
├── README.md                            # this file
├── MISSING_INFO.md                      # schema for the missing-info inventory
├── setup.sh                             # one-time: symlinks skills+agents into .claude/
├── scripts/
│   └── aggregate_missing_info.py        # cross-run meta-analysis over missing_info_report.json files
├── skills/
│   └── bundle-creation-test/
│       └── SKILL.md                     # ORCHESTRATOR — loaded into top-level conversation
├── agents/
│   └── submission-log-auditor.md        # spawned via Task per ground-truth sub_N (phase 4b)
└── competitions/
    └── <competition_sample_name>/       # e.g. style-trans-fair/
        ├── input/                       # planner-only — orchestrator can ls but not read
        │   ├── report.pdf
        │   └── sample_data/             # planner / implementer / reformat-and-run can read
        ├── ground_truth/
        │   ├── bundle/                  # GOLDEN reference bundle — OFF-LIMITS to ALL agents
        │   └── sample_submissions/
        │       └── sub_<N>/
        │           ├── submission/      # reformat-and-run reads; orchestrator/auditor do NOT
        │           └── expected_result.json   # orchestrator reads, hands to auditor
        └── <branch_sha>_<utc_ts>/       # one folder per experiment RUN
            ├── manifest.json            # orchestrator's structured log
            ├── meta.json                # makes the dir AUTOCODABENCH_RUN_DIR-adoptable
            ├── events.jsonl             # MCP tool-call timeline (written by the MCP server)
            ├── tool_calls/              # full MCP request/response snapshots
            ├── specs/                   # plan-phase output
            │   └── implementation_plan.md
            ├── plan_session.jsonl         # claude --print stdout for phase 2
            ├── bundles/<slug>/          # implementer's bundle (validates + zips here)
            │   ├── competition.yaml
            │   ├── scoring_program/    (with requirements.txt)
            │   ├── ingestion_program/  (if γ-style; with requirements.txt)
            │   ├── solutions/solution_baseline/   # the implementer's own baseline
            │   ├── pages/
            │   ├── reference_data/  input_data/  public_data/
            │   ├── README.ipynb         # the starting-kit notebook
            │   └── <slug>.zip           # produced only if validate_runtime=true
            ├── implement_session.jsonl    # claude --print stdout for phase 3
            ├── run_logs/<slug>/         # runner_io output
            │   ├── env/                 # conda clone + install logs
            │   │   ├── clone.stdout/stderr
            │   │   ├── requirements.txt
            │   │   └── install.stdout/stderr
            │   ├── baseline/            # bundle's OWN baseline run (implement-phase 5a)
            │   │   ├── sandbox/
            │   │   ├── stdout.txt / stderr.txt
            │   │   ├── ingestion_stdout.txt / ingestion_stderr.txt
            │   │   ├── scoring_stdout.txt / scoring_stderr.txt
            │   │   └── output/scores.json
            │   ├── starting_kit/        # README.ipynb execution (implement-phase 5b)
            │   │   ├── executed.ipynb
            │   │   └── stdout.txt / stderr.txt
            │   └── sub_<N>.attempt_<K>/ # reformat-and-run-driven user-submission scoring
            │       └── (same shape as baseline/)
            ├── reformat_run/<sub_N>/    # phase 4a per-sub shell-out
            │   ├── session.jsonl
            │   ├── env_probe.txt
            │   ├── attempt_<K>/         # per-attempt adapted code
            │   │   ├── model.py (or whatever the bundle interface expects)
            │   │   └── adapter_notes.md
            │   └── final.json           # status + scores + extras_installed + adapter_notes
            ├── log_audit/<sub_N>/       # phase 4b in-process subagent verdict
            │   └── verdict.json
            ├── missing_info_report.json # phase 5 aggregated inventory
            └── run_report.md            # phase 7 human-readable summary
```

Each experiment run is identified by a run directory named `<branch_sha>_<utc_ts>`.

---

## Experimental pipeline (per experiment run)

The following table summarizes each phase of the pipeline, its execution mechanism, and its read and write sets:

| # | Phase | Mechanism | Reads | Writes |
|---|-------|-----------|-------|--------|
| 1 | Preconditions | _(orchestrator)_ | `<comp>/input/` exists; per-sub `expected_result.json` parses | `manifest.expected_results` |
| 2 | Plan | shell-out `claude -p "/autocodabench-plan ..."` | `<comp>/input/**` (the proposal + sample_data) | `<run>/specs/implementation_plan.md` |
| 3 | Implement + self-validate | shell-out `claude -p "/autocodabench-implement ..."` | `<run>/specs/implementation_plan.md` + `<comp>/input/sample_data/` only — BLIND to ground_truth | `<run>/bundles/<slug>/**` + per-program `requirements.txt` + `run_logs/<slug>/{env,baseline,starting_kit}/**` + zip (only if `validate_runtime=true`) |
| 4a | Reformat + run (per `sub_N`) | shell-out `claude -p "/autocodabench-reformat-and-run ..."` | `<run>/bundles/<slug>/**` + `<comp>/ground_truth/sample_submissions/<N>/submission/**` — BLIND to `expected_result.json` | `<run>/reformat_run/<N>/attempt_<K>/**` + `final.json` + `run_logs/<slug>/<N>.attempt_<K>/**` |
| 4b | Log audit (per `sub_N`) | Task → `submission-log-auditor` | `<run>/reformat_run/<N>/final.json` + `<run>/run_logs/<slug>/<N>.attempt_<K>/**` + `expected_result.json` | `<run>/log_audit/<N>/verdict.json` |
| 5 | Aggregate missing-info | _(orchestrator)_ | per-stage `missing_info_inventory.json` files | `<run>/missing_info_report.json` |
| 6 | Finalize | _(orchestrator)_ | manifest + audits | `manifest.json` final, `conda env remove` |
| 7 | run_report.md | _(orchestrator)_ | everything above | `<run>/run_report.md` |

Phases 3 and 4a run their own internal iteration loops: the implementer retries the baseline and notebook runs up to 5 and 4 times respectively, and reformat-and-run retries up to 4 times. The orchestrator does not retry phases — each shell-out receives exactly one attempt. This design keeps failure attribution clean: an implementer that exhausts its inner attempts represents a different class of failure than an orchestrator that reached an unrecoverable state, and the manifest distinguishes the two.

---

## Rationale for shell-outs (rather than Task subagents) in phases 2, 3, and 4a

Claude Code's `Task` tool can spawn a subagent at depth 1, but that subagent cannot itself spawn another subagent; depth 2 is blocked.

The implementer's inner loop must call MCP tools (to write the bundle), then `prepare_run_env`, then `run_baseline_submission`, then potentially `install_env_extras`, then re-run the baseline, then `run_starting_kit`, and so on. These are all MCP calls within a single session and are permitted at any depth.

The orchestrator, however, must delegate to the implementer as its own conversational unit, with fresh context and a focused prompt. If `Task` were used for that delegation, the depth budget would be consumed and the phase 4b log auditor could not spawn. By shelling out the phases that require their own runtime loop, we keep `Task` available for the inexpensive single-shot auditor.

The shell-outs share state with the parent orchestrator only through two channels:

- the on-disk run directory (the `AUTOCODABENCH_RUN_DIR` environment variable), and
- the JSON object that the shell-out emits as its final assistant message (captured in the corresponding `*_session.jsonl` and parsed by the orchestrator).

This constitutes the entire inter-phase contract. Each phase is reproducible in isolation by re-running `claude --print` with the same prompt.

---

## Blinding protocol (data-leakage rules, preserved from the prior architecture)

The following table specifies, for each agent or phase, which paths it may and may not read; these access rules form the blinding protocol of the experiment and must be enforced exactly as stated:

| Agent / phase | May read | May NOT read |
|---|---|---|
| Orchestrator (top-level) | `<comp>/ground_truth/sample_submissions/*/expected_result.json` | `<comp>/input/**`, `<comp>/ground_truth/bundle/**`, `<comp>/ground_truth/sample_submissions/*/submission/**` |
| Plan shell-out | `<comp>/input/**` (incl. report.pdf + sample_data) | ground_truth/** |
| Implement shell-out | `<run>/specs/implementation_plan.md` + `<comp>/input/sample_data/**` | report.pdf, ground_truth/** |
| Reformat-and-run shell-out (per sub) | `<run>/bundles/<slug>/**` + `<comp>/ground_truth/sample_submissions/<N>/submission/**` | `expected_result.json`, `ground_truth/bundle/**`, report.pdf, plan |
| Log auditor (subagent, per sub) | `<run>/reformat_run/<N>/**` + `<run>/run_logs/<slug>/<N>.*` + `expected_result.json` | the submission's original code, ground_truth/bundle/, report.pdf, plan |

Three invariants follow from this protocol. The expected score never leaves the orchestrator–auditor pair. The submission's code never reaches the implementer, so the implementer cannot shape the bundle interface to match ground-truth code it has just read. The golden reference bundle is human-only and is off limits to all agents.

---

## One-time setup

Run the setup script once per checkout.

```bash
./experiments/bundle_creation_test/setup.sh
```

The script symlinks the orchestrator skill and the packaged skills (`autocodabench-plan`, `autocodabench-implement`, `autocodabench-reformat-and-run`, `codabench-bundle`, `competition-design`) into `.claude/skills/`, and the `submission-log-auditor` agent definition into `.claude/agents/`.

The `.claude/` directory is gitignored; the source of truth resides in this directory and in `src/autocodabench/skills/`.

---

## Running an experiment

In a top-level Claude Code session, issue the following request:

> Run the bundle-creation experiment on `<competition_sample_name>`

Claude loads `bundle-creation-test` and executes the seven phases. Total wall-clock time depends on the complexity of the proposal but is typically dominated by the conda environment clone (approximately 30 seconds), the bundle's baseline training (variable), and each reformat-and-run attempt (variable).

To inspect a finished run, proceed as follows.

1. Begin with `run_report.md`, the one-screen human-readable summary.
2. For machine analysis, consult `manifest.json` (structured) and `missing_info_report.json`.
3. For deeper investigation of a phase failure, consult the corresponding `*_session.jsonl` (one JSON event per line, with `type` values system, user, assistant, tool_use, tool_result, and result) together with the `run_logs/` subdirectory for the artifact that failed. The command `jq -c 'select(.type=="result")' session.jsonl | tail -1` yields the final result blob.

---

## Cross-run analysis

The aggregation script performs a meta-analysis across runs.

```bash
python experiments/bundle_creation_test/scripts/aggregate_missing_info.py
```

The script walks every `runs/<comp>/<run_id>/missing_info_report.json`, reports counts by section, severity, and impact, and surfaces the fields most frequently missing across runs. The script docstring documents the available filter flags.

---

## Host runtime expectations

The implementer's `prepare_run_env` clones the active conda environment (named `base` by default in `autocodabench/runner/execution.py`) into a per-run scratch environment. The clone inherits whatever is installed in the base environment; per-program `requirements.txt` files are then installed on top via `uv pip install` (or `pip` if `uv` is not on PATH).

For competitions that require a GPU (for example TensorFlow or PyTorch CNN baselines), the base environment must have the GPU stack pre-installed; `uv pip install` cannot materialize CUDA libraries that are not already present. When testing on CPU hardware against a proposal that calls for a GPU, the implementer's baseline run should be expected to be slow but functional, or to fail at the environment layer (for example, TensorFlow built without CUDA support).

The implementer does not downgrade or substitute when the host cannot service the plan's compute requirements; instead, it reports `validate_runtime: false` and allows the orchestrator to log the failure. This behavior is intentional: shrinking the baseline to fit the host would invalidate the experiment by silently making the bundle solve a different problem than the proposal specified.

The same principle applies to data. Synthetic stand-in data is prohibited: if the implementer or the reformat-and-run session reports that it generated substitute data because the real source was inaccessible, the orchestrator records `fail_at_synthetic_data_detected` and the run fails. A bundle that silently executes on fabricated data while the proposal specifies a real dataset would render the entire experiment invalid.

---

## Known limitations

- **No retries between phases.** If the implementer's inner loop exhausts its baseline attempts, the orchestrator records `fail_at_implement` and stops phases 4 and 4a, but still writes `missing_info_report.json` and `run_report.md`. Reviewing the implementer's stderr is the responsibility of the human experimenter, not the orchestrator.
- **Single competition per invocation.** To test against N competitions, invoke the skill N times.
- **`claude --print` cost.** Each shell-out is a full fresh Claude session. A run with K ground-truth submissions makes 2 + K shell-outs; budget accordingly.
- **No native-library deadlock recovery.** The implementer's retry table can patch Python-level errors (for example, `ModuleNotFoundError` or Keras API breaks). It cannot patch native-side issues such as the abseil ABI deadlock between TensorFlow and pyarrow; these manifest as `SIGTERM` with no traceback and lie upstream of any code edit the implementer can make. When the implementer observes that failure shape, it falls through to `validate_runtime: false` and the experiment fails honestly.

---

## Migration note (2026-06-12, branch `jmlr-oss-direction`)

The `auto_codabench/` package was restructured into the pip-installable `src/autocodabench/` library (core / runner / checks / backends / agent / mcp / cli). The implications for this harness are as follows:

- `setup.sh` symlink sources now point at `src/autocodabench/skills/`; re-run the script once per checkout.
- The MCP server module is `python -m autocodabench.mcp.server` (the old `auto_codabench.mcp_server.server` path no longer exists).
- Default artifact roots moved from `auto_codabench/{runs,bundles}/` to `./.autocodabench/{runs,bundles}/` (override with `AUTOCODABENCH_HOME`).
- The per-bundle deterministic checks that previously lived only in `validate_bundle` are now the `autocodabench validate-bundle` CLI and check registry; future harness phases should call that interface instead of re-implementing the lint.

Recorded artifacts inside old runs still reference the old layout; this is expected and harmless.
