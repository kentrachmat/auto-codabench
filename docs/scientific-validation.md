# Scientific validation of autocodabench

This document specifies what autocodabench claims, how each claim is
tested, and how every test can be reproduced. It is written for a
scientific reviewer: each test type states its procedure, its oracle
(the mechanism that decides pass or fail), its scope, and its known
limitations. All commands shown are runnable from a fresh checkout.

Status legend: **[implemented]** runs today (command given);
**[piloted]** the instrument exists and has at least one recorded run;
**[designed]** the protocol is specified but the campaign has not run.

---

## 1. Claims under test

The software makes four falsifiable claims, and every element of this
document exists to test one of them. Table 1 maps each claim to the
sections that test it.

| # | Claim | Tested by |
|---|-------|-----------|
| C1 | The authoring layer produces **structurally valid** Codabench bundles (schema-correct, internally consistent, uploadable). | §3.1 unit tests, §3.2 replay E2E, §3.4 check framework |
| C2 | Generated competitions are **functional**: their own baseline runs through their own scoring pipeline and produces scores; the starting kit executes. | §3.3 execution oracles |
| C3 | The validator **catches real authoring defects** before launch, in both generated and hand-written bundles. | §3.4 check framework, §4.3 seeded-defect study (E3) |
| C4 | Given a competition proposal with known ground truth, the end-to-end pipeline produces a competition that **scores known submissions within tolerance of their expected scores**. | §3.5 ground-truth harness, §4.1 success-rate campaign (E1) |

We deliberately do not claim or measure how well third-party models
solve the generated competitions. A statement of the form "model X
scores Y% on our benchmark" evaluates the solver, not the software, and
conflating the two is a known methodological error in tooling papers.
No experiment in this document reports solver accuracy as evidence of
tool quality.

---

## 2. Why an agentic tool requires a different test discipline

Two properties of LLM-driven generation shape the methodology that
follows.

1. **Non-determinism.** The same prompt can yield different bundles.
   Correctness is therefore never defined by the generation path; it is
   defined by executable post-hoc oracles applied to the artifact: does
   the bundle validate, does its baseline run, does it reproduce
   expected scores. Success rates are reported over repeated runs, not
   single anecdotes.
2. **Self-assessment is inadmissible.** An agent reporting that "the
   bundle is correct" is not evidence. Every verdict in this codebase
   is produced by code that is independent of the generating agent: the
   schema linter, the sandbox runner's exit codes and parsed
   `scores.json`, and the check registry. LLM judgment is used in
   exactly one place (§3.4.2) and is advisory by construction; it can
   never change a pass/fail verdict.

Every agentic run also produces a complete audit trail
(`tool_calls/NNNN_*.json` and `events.jsonl` per run): the inputs and
outputs of every authoring action, with timestamps and durations. All
quantitative statements about a run are recomputable from this record,
and any recorded run can be re-executed deterministically (§3.2).

---

## 3. The test pyramid (implemented)

### 3.1 Unit tests: the deterministic core **[implemented]**

```bash
pip install -e '.[dev]'
python -m pytest tests/        # 41 tests, < 1 s, no network, no API keys
```

**Scope.** The LLM-free layers: bundle file I/O (round-trip authoring,
path-traversal rejection, schema-key allow-listing), the schema linter
(detection of missing referenced files and unwritten leaderboard keys),
zip layout (`competition.yaml` at the zip root, the most common upload
rejection), the check framework (every status transition: PASS, FAIL,
FINDING, SKIPPED, ATTESTATION_REQUIRED), facts gating, the replay
backend (including determinism: two replays produce byte-identical
`competition.yaml`), auth resolution (all credential combinations via a
faked home directory), and the CLI surface (exit codes included).

**Oracle.** Conventional assertions. The suite is keyless by policy:
no test may require Claude authentication or network access. This is
what makes the suite a hard CI gate rather than a flaky one.

**CI.** `.github/workflows/ci.yml` runs the suite on Python 3.10–3.13
(Linux) and 3.12 (macOS) on every push and pull request, together with
the §3.2 replay test and a wheel-content check (the packaged skills and
fixtures must ship; otherwise the install is broken in a way unit tests
cannot detect).

### 3.2 Deterministic end-to-end testing: record and replay **[implemented]**

```bash
autocodabench demo --out /tmp/demo
autocodabench validate-bundle /tmp/demo/demo-ai-text-detection.zip
```

**Procedure.** Every live run records each MCP tool call (name and full
arguments). The replay backend re-executes a recorded sequence against
the real authoring layer: the bundle is genuinely rebuilt on the local
machine, then schema-validated and zipped. The shipped fixture
(14 authoring calls) is regenerated by `scripts/make_demo_fixture.py`.

**What it establishes.** (a) The full authoring path — init, pages,
scoring program, data, solution, `competition.yaml`, validation, zip —
functions end-to-end with no model access; (b) determinism: the
pipeline below the model seam has no hidden nondeterminism (asserted
byte-for-byte in `tests/test_replay_backend.py`); (c) reviewability:
anyone can exercise the system without credentials.

**Limitation.** Replay validates the machinery, not the model's design
choices; the latter are the subject of §3.3–§3.5.

### 3.3 Execution oracles: the bundle must run itself **[implemented]**

These oracles are used by `autocodabench create` (and by the experiment
harness) as the runtime definition of a working bundle. Structural
validity (§3.1) is necessary but not sufficient: a bundle can be
schema-perfect and still fail on its first real submission.

**Procedure.** After the build agent writes a bundle:

1. An execution environment is prepared. On Docker-equipped hosts the
   programs run inside the bundle's declared `docker_image`, exactly as
   Codabench's worker runs them (no dependency installation — the
   platform installs nothing), which makes the oracle platform-faithful.
   Otherwise a fresh conda environment is cloned and the bundle's own
   per-program `requirements.txt` files are installed
   (`prepare_run_env`), so that the bundle's declared dependencies,
   rather than the developer's environment, are what is tested; this
   fallback is more permissive than the platform, and each recorded
   result names the engine that produced it.
2. **Baseline oracle** (`run_baseline_submission`): the bundle's own
   shipped baseline solution is staged into a sandbox laid out exactly
   as Codabench's worker lays it out (`program/`, `input/res`,
   `input/ref`, `output/`); its ingestion program (if any) and scoring
   program are invoked via the commands in their `metadata.yaml`; and
   the run passes if and only if the exit code is 0, no timeout
   occurs, and a parseable `scores.json` is produced whose keys cover
   the leaderboard columns.
3. **Starting-kit oracle** (`run_starting_kit`): the participant-facing
   notebook is executed top-to-bottom (`jupyter execute`,
   `--allow-errors=false`); the run passes if and only if the exit code
   is 0 and all code cells executed.
4. The generating agent receives the stderr and may retry, bounded at
   5 baseline attempts and 4 notebook attempts. Exhausting the budget
   is recorded as failure, and the agent is forbidden from weakening
   the task to make the test pass (the no-silent-downgrade rule).

All subprocess stdout and stderr is streamed to disk as it is produced
(`run_logs/<slug>/...`), with timeouts enforced by process-group
termination, so that failures are diagnosable from artifacts alone.

**Evidence from the recorded pilot** (run directory retained;
2026-06-12, `claude-sonnet-4-6`): starting from the one-line idea "Iris
species classification…", the build agent encountered a genuine
sandbox/ingestion mismatch, diagnosed it from stderr, repaired the
bundle, and converged in 4 baseline attempts and 5 notebook attempts;
the final baseline scored `balanced_accuracy = 0.941` with bootstrap
confidence intervals, the notebook executed 6/6 cells, the total cost
was $2.69, and the run comprised 61 fully audited tool calls.

### 3.4 The check framework: competition design as an executable checklist **[implemented]**

```bash
autocodabench validate-bundle <bundle-dir-or-zip> [--facts facts.yaml] [--judged]
autocodabench checks list        # the live inventory, by tier, with citations
```

The checklist is derived from Pavão et al. (2024), *AI Competitions and
Benchmarks: The Science Behind the Contests*; each check cites the
chapter it operationalizes. The framework's central methodological
commitment is that checks are typed by how their verdict is produced,
and the three types are never conflated in reports. Table 2 summarizes
the three tiers and their epistemic standing.

| Tier | Verdict produced by | Can it gate? | Failure mode handled |
|------|--------------------:|--------------|----------------------|
| deterministic | code | **yes** (FAIL blocks) | — |
| LLM-judged | a model grading a rubric | **no** (FINDING only) | unparseable/failed judge ⇒ SKIPPED, never silent pass |
| attestation | a human | no (surfaced as unchecked box) | cannot be faked by the tool |

#### 3.4.1 Deterministic checks (the gates and cited findings)

Each check states its decision rule exactly. BLOCKER failures gate
(exit 1); WARNING and INFO severities emit findings. Table 3 lists the
deterministic checks, their decision rules, and their sources.

| Check id | Decision rule | Severity | Source |
|----------|---------------|----------|--------|
| `bundle-schema` | `competition.yaml` parses; required keys present; every referenced file (image, terms, pages, task data, programs, solutions) exists; programs carry a runnable `metadata.yaml command`; every non-computation leaderboard `column.key` appears as a literal in the scoring program; phases reference declared tasks; `start ≤ end` | BLOCKER | Codabench schema docs |
| `two-phase-structure` | ≥ 2 phases declared (development + final) | WARNING | Pavão Ch. 5, 11 |
| `dev-phase-duration` | first phase ≥ 40 days | WARNING | Pavão Ch. 13 |
| `daily-submission-cap` | every development phase declares `max_submissions_per_day`; flag > 10/day | WARNING | Pavão Ch. 5 |
| `final-phase-submission-limit` | last phase declares `max_submissions ≤ 3` | WARNING | Pavão Ch. 5 |
| `leaderboard-sorting` | every ranked column declares `sorting ∈ {asc, desc}` | WARNING | Pavão Ch. 4 |
| `starting-kit` | `starting_kit/` ships ≥ 1 file | WARNING | Pavão Ch. 5, 13 |
| `baseline-solutions` | ≥ 1 solution shipped *and* declared; recommend ≥ 2 (trivial + competent) | WARNING/INFO | Pavão Ch. 5 |
| `docker-image-pinned` | `docker_image` declared | WARNING | Pavão Ch. 11 |
| `test-set-size` | N ≥ 100/E where E = declared `anticipated_error_rate`; N from declared `test_set_size`, else counted from a single reference CSV | WARNING | Pavão Ch. 4 |
| `external-data-rule` | given declared `external_data_allowed`, the pages must mention the external-data/pre-training policy | WARNING | Pavão Ch. 5 |

**Declared facts.** The `test-set-size` and `external-data-rule` checks
and the prize-legality attestation consume a `competition_facts.yaml`
that the organizer declares (anticipated error rate, unit of
generalization, external-data policy, prizes). This is a deliberate
declare-then-verify design: rather than guessing unverifiable context,
a check missing its fact reports `SKIPPED — requires facts: …`, which
is auditable, instead of a silent pass, which is not.

#### 3.4.2 LLM-judged checks (advisory by construction)

The judged tier currently contains one check,
`judged-docs-config-consistency`. **Procedure:** the raw
`competition.yaml` (≤ 8k characters) and all pages (≤ 16k characters)
are embedded in a fixed rubric prompt that asks only for contradictions
between what the pages promise participants and what the configuration
enforces (phase dates, submission limits, metric names and ranking
direction, submission format, prizes), with the instruction that every
finding must quote both sides. The judge replies in strict JSON
(`{"findings": [{where, message}]}`); replies that do not parse degrade
to SKIPPED. Each finding is rendered with its locator, marked advisory,
and never gates.

**Calibration evidence (manual, recorded 2026-06-12):** on the clean
demo bundle the judge returned zero findings; after a single
contradiction was planted (a page stating "max 20 submissions/day"
while the configuration enforces 5), the judge flagged exactly that
contradiction with the correct locator and both quotes. Systematic
sensitivity/specificity measurement is the E3 protocol (§4.3), now
implemented as a runnable instrument
(`experiments/backbone_bench/run_judge_bench.py`).

Judged checks run through the same backend seam as everything else, so
the judging backbone is a measured variable: the same rubric, the same
parse-or-skip policy, and the same defect oracles apply whether the
judge is Claude, a local Ollama model, or any OpenAI-compatible
endpoint. Per-backbone quality of the judged tier is axis A of the
backbone benchmark (§4.5).

#### 3.4.3 Attestations

The attestation tier comprises five launch criteria that no code or
model can verify: external reviewers attempted the task (Pavão Ch. 2),
a per-feature leakage probe was run (Ch. 3), a datasheet was published
(Ch. 3), a data license and persistent home were decided (Ch. 3, 13),
and prize legality was confirmed (Ch. 13). The report surfaces these as
unchecked boxes. Scientifically, this is a refusal to overclaim: the
tool distinguishes "verified," "advised," and "asserted by a human" in
its output format itself.

### 3.5 Ground-truth harness: score fidelity on real competitions **[piloted]**

`experiments/bundle_creation_test/` is the instrument behind claim C4
and the E1 campaign.

**Design.** For a competition with known ground truth (a proposal
document, sample data, and K reference submissions each with an
`expected_result.json`):

1. **Plan** from the proposal; **build and self-validate** from the
   plan (the §3.3 oracles apply).
2. **Adapt** each reference submission to the generated bundle's
   interface (an agent may rewrite glue code but never the method), and
   **score** it through the generated bundle's own pipeline.
3. **Oracle:** the produced score must match the expected score within
   the per-submission tolerance recorded in `expected_result.json`. An
   independent auditor agent renders a verdict on each submission from
   the logs and writes `verdict.json`.

**Blinding and leakage protocol** (enforced via per-phase file-access
rules; the full table appears in the harness README): the builder never
sees the ground-truth bundle, the reference submissions, or the
proposal PDF (the plan only); the adapter never sees expected scores;
expected scores exist only in the orchestrator/auditor pair; the
human-made reference bundle is off-limits to all agents. This prevents
the generator from shaping the competition to the answers, which is the
experiment's central validity threat.

**Attribution.** Phases receive no cross-phase retries; a run that
fails records where it failed (`fail_at_plan` / `fail_at_implement` /
per-submission), so that failure classes are separable in analysis.
Every run's `manifest.json` records the model and version, per-phase
cost, validation outcomes, scores, and deltas.

**Status.** The instrument is complete, with one full pilot competition
(`style-trans-fair`); the multi-competition campaign is E1 below.

---

## 4. Designed experiments (the evaluation campaign)

These experiments are specified now so that the instruments above were
built to collect them. The reporting standards for all of them are: the
model and version are pinned and recorded per run; at least 3 runs per
condition are reported, with success rates and dispersion, never single
anecdotes; cost per run is reported; and all raw logs (`tool_calls/`,
`events.jsonl`, sandbox stdout/stderr) are retained and linkable.

### 4.1 E1 — Bundle-generation success rate and score fidelity **[designed; N=1 piloted]**

Over N ≈ 10–15 named, diverse real competitions, E1 measures the
fraction of runs reaching (a) a structurally valid bundle, (b) a
running baseline (the §3.3 oracle), and (c) a ground-truth score match
within tolerance (the §3.5 oracle), reported per phase, with cost. This
is the headline table; the harness already emits every column into
`manifest.json`, so the campaign requires execution and tabulation, not
new instrumentation.

### 4.2 E2 — Capability matrix **[designed]**

E2 compares autocodabench against manual authoring, raw Codabench, and
EvalAI across user-meaningful capabilities (bundle-from-natural-language,
pre-launch ground-truth validation, erroneous-submission testing,
auditable authoring log, platform generality, cost), with honest
negative cells, including our own (Codabench-only scope; LLM cost).

### 4.3 E3 — Validation effectiveness (seeded defects) **[implemented; campaign pending]**

E3 is the validator's sensitivity/specificity study, implemented as
`experiments/backbone_bench/run_judge_bench.py`. Protocol: 12 defect
types drawn from real authoring failures are programmatically seeded
into otherwise-clean bundles (the replay fixture provides the
deterministic clean base) — 9 targeting the deterministic tier (missing
referenced file, unwritten leaderboard key, missing daily cap, 10-day
dev phase, missing sorting direction, unlimited final submissions,
missing starting kit, single phase, unpinned docker image) and 3
targeting the judged tier (pages-versus-config contradictions in
submission caps, metric direction, and phase dates). Catch or miss is
recorded per defect per run, together with the judged tier's
false-positive rate on clean copies (the judged tier is stochastic, so
at least 3 runs per condition are required).

**First result (2026-06-12):** the deterministic tier catches **9/9**
seeded defects, as required. Because this tier is code, anything below
9/9 constitutes a bug, and the study doubles as a regression test for
the check registry. Per-backbone results for the judged tier accumulate
under `experiments/backbone_bench/results/`.

### 4.4 E4 — Authoring-effort study **[designed, optional]**

E4 is a small within-subjects user study (N ≈ 10–20) measuring
time-to-first-valid-bundle and defect count, manual versus assisted,
with a pre-registered protocol; results are reported descriptively
given the N.

### 4.5 E5 — Backbone benchmark **[axis A implemented; axis B protocol fixed]**

Because every backbone runs behind the same seam — an identical tool
surface, an identical audit-trail format, and identical oracles — the
LLM itself becomes a measured variable rather than a confound. The
benchmark has two axes (full protocol:
`experiments/backbone_bench/README.md`):

- **Axis A — validation/judging quality:** E3's seeded-defect catch
  rate and clean-bundle false-positive rate, per backbone (Claude,
  local models via Ollama, OpenAI-compatible endpoints). Runnable now.
- **Axis B — bundle-creation quality:** the ground-truth harness
  (§3.5) run per backbone per competition, measuring plan completeness,
  structural validity, runtime validity with attempts-to-converge,
  ground-truth score fidelity, and cost. The blinding protocol is
  unchanged; backbone-specific conditions (no PDF reading in the
  generic backend; native tool-calling required) are recorded as run
  conditions, never silently worked around.

This benchmark also serves as the seed of a public benchmark of agentic
benchmark-authoring capability, with `style-trans-fair` as the first of
the ground-truth competitions.

---

## 5. Threats to validity and limitations

- **Backbone coverage is uneven.** Two live backend families ship:
  the Claude Agent SDK (the richest runtime: subagents, MCP, PDF
  reading) and a generic OpenAI-compatible loop (covering Ollama-served
  local models and API endpoints; it requires native tool-calling and
  cannot read PDF proposals). Results are always conditioned on the
  recorded backbone and model, and the backbone benchmark (§4.5)
  measures rather than assumes the differences. The deterministic tier
  and replay remain model-free.
- **Stochasticity of the judged tier.** This is treated by design
  (advisory-only, parse-or-skip) and by measurement (E3 repeats judged
  runs).
- **The checklist is not complete.** `autocodabench checks list` is the
  live inventory of what is and is not covered; attestations make the
  uncovered-by-construction portion explicit. The validator targets
  common authoring errors, not adversarial organizers.
- **Local oracles approximate the production worker.** The sandbox
  mirrors Codabench's layout and command contract but is not the hosted
  queue; `docker_image` pinning is checked, but container parity is not
  executed locally.
- **Toy-scale pilots.** The recorded pilots use small datasets by
  design (minutes of runtime, dollars of cost); E1's named real
  competitions are where claims at scale will rest.
- **Cost and time variance.** Agentic runs vary in the number of turns
  and in cost; E1 reports distributions, and per-phase budget caps
  bound the tail.

---

## 6. Where the contribution lives, and the review-gauntlet mapping

A fair objection to any LLM-driven tool is that the model may be doing
all of the work. The falsifiable answer is to enumerate what functions
with the model removed or swapped, and what the scaffolding measurably
adds:

1. **Components that run with zero LLM:** the entire deterministic tier
   of the validator (9/9 on the E3 seeded defects, by code rather than
   by model), the bundle authoring core, the execution sandbox, the
   replay demo, the full unit suite, and CI. A reviewer can exercise
   all of these without credentials.
2. **Components that are model-independent by construction:** the typed
   tool surface, the per-run audit trail (identical format across
   backbones), the blinding protocol of the ground-truth harness, the
   plan-lock phase boundary, and the three-tier verdict epistemics.
   None of these are prompts; they are inspectable code with tests.
3. **The model is a measured variable, not the contribution:** the
   backbone benchmark (§4.5) runs different LLMs — including local
   Ollama models — through the same scaffolding and reports how outcome
   quality varies. A wrapper cannot run this experiment; a framework is
   what makes it well-posed.

Table 4 maps the standard failure modes of LLM-tool submissions to this
project's standing answers.

| Mode | Risk | Standing answer |
|------|------|-----------------|
| F4 "wrapper" | the LLM does all the work | the three points above; design section of `architecture.md` |
| F5 solver-grading confusion | reporting model accuracy as tool quality | policy in §1: no experiment reports solver performance; oracles measure bundle validity, runtime, score fidelity, defect catch rate |
| F6 overselling | claims beyond evidence | status tags (implemented/piloted/designed) throughout; threats-to-validity §5; attestation tier = the tool refusing to overclaim *in its own output format* |
| F7a key wall | reviewer cannot run it without paying | keyless tiers (validator, replay demo, tests, CI) + **Ollama local models** for the LLM tiers + Claude subscription path |
| F7b live-service wall | needs codabench.org | everything ends at the validated zip; upload is an explicitly optional final step |
| F7c non-determinism | "ran it twice, got different bundles" | artifact-level oracles (§2), deterministic sub-model layer proven by replay, ≥3-runs-with-dispersion reporting standard |

## 7. Reproducibility quick reference

Table 5 lists each reproducibility entry point, its command, and its
authentication requirement.

| What | Command | Needs auth? |
|------|---------|-------------|
| Unit suite | `python -m pytest tests/` | no |
| Core smoke | `python -m autocodabench.core.bundle_io` | no |
| Offline E2E + validation | `autocodabench demo --out /tmp/d && autocodabench validate-bundle /tmp/d/demo-ai-text-detection.zip` | no |
| Check inventory | `autocodabench checks list` | no |
| E3 deterministic baseline | `python experiments/backbone_bench/run_judge_bench.py` | no |
| Judged tier | `autocodabench validate-bundle <bundle> --judged [--backend ollama:<model>]` | Claude auth, or none with a local Ollama model |
| Judge bench per backbone | `python experiments/backbone_bench/run_judge_bench.py --backend <spec> --runs 3` | per backbone |
| Full live pipeline | `autocodabench create "<idea>" [--backend <spec>] --verbose` | per backbone |
| Ground-truth harness | see `experiments/bundle_creation_test/README.md` | yes |

Authentication means a Claude subscription login or an
`ANTHROPIC_API_KEY` (`autocodabench auth status` reports which is
active).

---

## 8. Sources

- Pavão et al. (2024), *AI Competitions and Benchmarks: The Science
  Behind the Contests* — the source of the design rules the checks
  operationalize (cited per check, at chapter level, in code and
  reports).
- Codabench documentation (bundle schema and worker contract) —
  vendored under `documentation/codabench_getting_started/` for
  provenance; the schema checks cite it.
- Gebru et al., *Datasheets for Datasets* — the basis of the datasheet
  attestation.
