# Design rationale: from a single script to the present architecture

This document explains *why* autocodabench is structured the way it is. It is written for a reader who has never built a system like this one and who may reasonably ask the question every reviewer should ask: **why could this not be one Python file that reads `competition.yaml` and runs a series of if/else checks?**

We answer that question constructively. Section 2 presents exactly that single-file design and takes it seriously. Each subsequent section identifies one concrete way the simple design fails, states the design decision that resolves the failure, names the package directory that embodies the decision, and — where the decision was genuinely contested during development — records the alternative we rejected and why. By the end, every directory under `src/autocodabench/` has been derived from a failure of a simpler design, rather than asserted.

A companion document, [`architecture.md`](./architecture.md), gives the resulting structure as a reference map; [`validate-bundle-walkthrough.md`](./validate-bundle-walkthrough.md) traces one command through the code line by line. This document sits before both: it explains why the map looks the way it does.

---

## 1. The problem being solved

A *Codabench competition bundle* is a directory (usually shipped as a zip file) that fully defines a machine-learning competition: a `competition.yaml` manifest describing phases, leaderboards, and file references; one or more *scoring programs* (Python code the platform runs to grade submissions); optionally an *ingestion program* (code that runs the participant's model); HTML/Markdown pages; datasets; and a reference solution. If any piece is wrong — a misspelled key, a scoring program that crashes, a leaderboard column that does not match what the scorer emits — the failure typically surfaces *after* launch, in front of participants.

autocodabench does two things:

1. **Validation.** Given a bundle, report what is wrong with it *before* upload.
2. **Authoring.** Given a competition idea (optionally with data), have an LLM agent write the entire bundle, then validate and execute it.

Both tasks sound simple. The architecture exists because neither is.

---

## 2. The strawman: one file, one YAML parse, if/else

The design every newcomer would write first:

```python
# validate.py — the whole tool?
import yaml, sys

comp = yaml.safe_load(open(sys.argv[1] + "/competition.yaml"))
errors = []
if "title" not in comp:
    errors.append("missing title")
if not comp.get("phases"):
    errors.append("no phases")
for phase in comp.get("phases", []):
    if "start" not in phase:
        errors.append("phase missing start date")
# ... fifty more if statements ...
print("\n".join(errors) or "OK")
```

This program is not worthless. A meaningful fraction of autocodabench's deterministic checks are, at their core, conditions of exactly this shape, and `core/bundle_io.py::validate_bundle` contains a lint pass that a determined reader could compress into something resembling the strawman.

The strawman fails under five distinct pressures. Each pressure produces one architectural decision, and the decisions compose into the present layout:

1. **Some properties of a bundle cannot be read from its files at all.** Whether the test set is large enough for the advertised number of evaluations, whether the prize is legal in the organizer's jurisdiction, whether external data is permitted — no amount of parsing reveals these (Section 5).
2. **Some properties can only be established by *running* the bundle.** A scoring program that imports a missing package, or deadlocks inside a BLAS library, passes every static check and fails on the platform (Section 4).
3. **Some properties are judgments, not computations.** "Is the task description clear enough for a newcomer?" has no if/else encoding. An LLM can grade it — but an LLM's grade has a different epistemic standing than a parser's verdict, and the architecture must not let the two be confused (Section 5).
4. **Authoring requires an agent, and an agent requires containment.** An LLM writing files freely on the user's machine is unauditable; an LLM holding a 200-page proposal in context while also writing code is expensive and unreliable (Sections 6–9).
5. **A tool whose interesting behavior requires a paid API key cannot be tested, reviewed, or demonstrated honestly.** Continuous integration, an MLOSS reviewer, and a student without a key must all be able to run the real code paths (Sections 8 and 10).

The remainder of the document takes these in dependency order, starting from the bottom of the stack.

---

## 3. Decision 1 — a pure file layer (`core/`)

**Naive design.** Mix file reading, validation logic, LLM calls, and subprocess execution in one module, since they are all "part of validating."

**Failure mode.** Every test of the parsing logic now needs an API key, or a network, or a conda installation. Worse, a bug in the YAML lint cannot be isolated from a bug in the agent, because the call graph runs through both.

**Decision.** `core/` contains only pure file operations: create a bundle skeleton, write a scoring program to disk, parse and lint `competition.yaml`, produce a zip. It imports no LLM SDK, opens no network connection, and spawns no subprocess. The consequence is that `core/` is unit-tested conventionally — `python -m pytest tests/` runs in under a second with no credentials — and every higher layer can trust it as a verified foundation.

This is the oldest idea in the document (layered architecture), and it was not contested. It is stated first because every later decision quietly depends on it: replay (Section 8) re-executes recorded calls *against the real core*, which is only meaningful because the core is deterministic.

`core/config.py` carries one further rule that looks bureaucratic but is load-bearing: **no path in the package may assume a repository checkout.** Artifact locations resolve explicit argument → environment variable (`AUTOCODABENCH_HOME` and friends) → `<cwd>/.autocodabench/`. Without this rule the package would work on the developer's laptop and fail when pip-installed — the single most common failure mode of research code.

---

## 4. Decision 2 — executing bundle code is a separate, riskier concern (`runner/`)

**Naive design.** When a check needs to run the scoring program, call `subprocess.run(["python", "scoring.py"])` from inside the validator.

**Failure mode.** Three distinct ones. First, the scoring program has its own dependencies (`requirements.txt`), which conflict with autocodabench's own; installing them into the host environment corrupts it. Second, two concurrent runs install into the same environment and race. Third — discovered empirically, not anticipated — numerical libraries misbehave unless thread-count variables (`OMP_NUM_THREADS` and related) are set in the subprocess environment *before* the Python interpreter starts; setting them afterwards is too late.

**Decision.** `runner/execution.py` stages the worker's sandbox layout and executes the bundle's programs through one of two engines. The **docker engine** — preferred whenever a daemon is reachable — runs each program inside the bundle's declared `docker_image` exactly as the Codabench worker does: the active program directory (scoring or ingestion, run as separate invocations) mounted at `/app/program` with the working directory set there, the data and output trees at `/app/input` and `/app/output`, the platform's default image (`codalab/codalab-legacy:py37`) when none is declared, and *no dependency installation*, because the worker never installs `requirements.txt`. A bundle's `/app/...` (or `$program`/`$input`/`$output`) metadata command therefore runs verbatim. The **conda engine** — the fallback for hosts without Docker, and the host for the starting-kit notebook, which runs on a participant's machine rather than on the worker — clones a dedicated environment per run, named deterministically (`acb-run-<prefix>`), installs each program's `requirements.txt`, and executes with the thread variables pre-set. Logs are teed live to `run_logs/` under both engines, and every result records which engine produced it.

The two engines make different promises, and the difference is the point. The conda engine answers "do the programs run?" — but it is strictly *more permissive* than the platform, precisely because it installs the bundle's requirements while the platform installs nothing: a scorer that depends on a package present in the cloned environment but absent from the declared `docker_image` passes the conda run and fails on Codabench. The docker engine answers the question the organizer actually has: "will this run on the platform?" A clean run under it is evidence of platform behavior — a subsequent failure on Codabench points at the server, not at the bundle — which is why it is the default whenever Docker is available, and why every conda-engine result carries an explicit fidelity note rather than passing silently for more than it proved.

Why is this a *separate directory* from `core/` rather than a submodule of it? Because the two have different trust and cost profiles, and code that depends only on `core/` should be able to say so. Parsing a YAML file is instantaneous and safe; cloning a conda environment takes minutes and executes third-party code. The import boundary makes the distinction checkable: `checks/deterministic.py` imports `core` and not `runner`, and a reader can verify from the imports alone that running `autocodabench validate-bundle` never executes bundle code.

One further choice deserves a sentence: the runner provides *one-shot* functions (`prepare_run_env`, `run_baseline_submission`, ...) and deliberately contains no retry loop. Iteration — "the run failed with `ModuleNotFoundError`, install the package, try again" — is judgment, and judgment belongs to the agent (or the human) driving the runner, where it is logged. A retry loop buried in library code hides failures; the same loop driven through logged tool calls is an audit trail.

---

## 5. Decision 3 — validation is a registry of small, cited checks in three tiers (`checks/`)

**Naive design.** One function, `validate(bundle) -> list[str]`, accumulating error strings — the strawman of Section 2, grown larger.

**Failure modes and the three sub-decisions that answer them.**

**(a) Provenance.** A monolithic validator asserts "this is wrong" with no authority behind it. When the validator and Codabench disagree — which happened: production Codabench accepts a legacy extensionless `metadata` program filename that an early version of our lint rejected — there is no way to adjudicate. The decision: every check is a small registered object carrying an `id`, a severity, and a **citation** to either the Codabench schema or Pavão et al. (2024), the competition-design reference the checks operationalize. `autocodabench checks list` prints the registry with citations. A check without a citation does not exist.

**(b) Epistemic tiers.** Once LLM-graded checks exist (clarity of task pages, internal consistency of rules), the temptation is to treat their output like any other check result. We refused, and this is among the most consequential decisions in the system. Checks register in one of three tiers:

| Tier | Verdict computed by | Effect on the result |
|---|---|---|
| Deterministic | Code | PASS or FAIL — **the only tier that can gate** |
| Judged | An LLM grading a rubric | FINDING — advisory, never gates |
| Attestation | A human, later | Surfaced as an explicit unchecked box |

`ValidationReport.ok` is defined as "no deterministic FAIL." An LLM judgment, however confident, can only annotate. The rationale is the test-oracle discipline familiar from software testing: a gate must be reproducible and contestable, and an LLM's grade is neither. Conversely, a human-only criterion ("the organizers have IRB approval") must never silently pass merely because no code could check it — the attestation tier exists so that such criteria appear in every report as visibly unresolved.

**(c) Declare-then-verify.** Some deterministic checks need context the bundle cannot carry — the intended number of evaluation runs, whether external data is permitted. The naive options are to skip such checks silently (they vanish) or to guess (they lie). The decision: the organizer declares these facts in a `competition_facts.yaml`, and checks that consume facts report **SKIPPED, with instructions for declaring the missing fact**, whenever it is absent. A skipped check is information; a silently passing check is a defect.

---

## 6. Decision 4 — the validator accepts *any* bundle, not only ones we generated

**Naive design** — and a genuinely debated one. The validator exists to check the agent's output, so wire it into the generation pipeline: `create` produces a bundle and validates it, end of story. This is simpler (one entry point, no zip handling, no tolerance for unfamiliar layouts) and was effectively the shape of an early version.

**Failure mode.** Three arguments eventually overturned it.

1. *The circularity argument.* If the only bundles the validator ever sees are bundles our own pipeline produced, then the validator and the generator co-evolve: the generator learns to produce whatever the validator accepts, and the validator is never confronted with the diversity of real bundles. Its verdicts become claims about our pipeline, not about Codabench.
2. *The standalone-value argument.* Most competition organizers already have a bundle — hand-written, inherited, exported from a previous year. A validator that can only check freshly generated bundles is useless to them; a validator that accepts any directory or zip is an independently publishable tool. This is why `validate-bundle` is a first-class subcommand taking a path, not a flag on `create`.
3. *The empirical argument*, which settled the debate: pointing the validator at a production reference bundle we did not generate (STYLE-TRANS-FAIR) immediately exposed a false gate — the legacy `metadata` filename of (a) above — that no generated bundle would ever have triggered. The fix and its regression test are in the changelog. Imported bundles are not just an audience; they are the validator's own test suite.

The same generality applies one step downstream: the upload utility (`upload/`, documented in [`codabench-upload-api.md`](./codabench-upload-api.md)) takes any bundle zip, not a handle to a pipeline run.

---

## 7. Decision 5 — authoring requires an agent, and the agent acts only through typed tools (`mcp/`)

**Naive design A: no agent.** Generate bundles from templates — a cookiecutter with holes for the title, metric, and dates.

**Failure mode.** The hard part of a bundle is not its skeleton; it is the scoring program that implements *this* competition's metric against *this* data format, the ingestion program matched to it, and the dozens of consistency conditions among them. Templates produce the easy 20%. The remaining 80% is open-ended programming, which is what LLM agents are for.

**Naive design B: an unconstrained agent.** Hand the LLM a shell and a file system and ask for a bundle.

**Failure mode.** Two. First, *auditability*: after a 40-minute session that touched 60 files, no human can reconstruct what the agent actually did, so no reviewer can trust the result. Second, *capability creep*: an agent that can write arbitrary files can also read arbitrary files, install packages globally, or — the failure that matters for the experiment harness — peek at ground-truth answers it was supposed to be blinded from.

**Decision.** The agent interacts with the system exclusively through twenty narrow, typed MCP tools (`autocodabench_init_bundle`, `autocodabench_write_scoring_program`, `autocodabench_run_baseline_submission`, ...), each a thin wrapper over `core/` and `runner/`. MCP (Model Context Protocol) is the standard interface through which an LLM session invokes external tools; `mcp/` hosts a stdio server exposing exactly these twenty. The tool surface is a *capability boundary*: what a phase is permitted to do is the list of tools it is given (Section 9 shows the planner's list excludes every write tool), and what a phase actually did is the log of tool calls it made (Section 8). Neither property is available to design B.

The directory split mirrors the reasoning: `mcp/tools/` contains no logic, only logging wrappers, precisely so that the boundary adds observability without forking behavior — the web UI, the CLI, and the agent all reach the same `core/` functions.

---

## 8. Decision 6 — every tool call is recorded, and the recording *is* the replay fixture (`run_log.py`, `backends/replay.py`)

**Naive design.** Log free-text progress messages, as most programs do.

**Decision.** Every MCP tool call is snapshotted — full request, full response, duration — to `<run>/tool_calls/NNNN_<tool>.json`, with an index line in `<run>/events.jsonl`. This began as an audit requirement (Section 7) and acquired a second life that now anchors the system's testability: a recorded run can be **re-executed**. `ReplayBackend` reads the recorded tool calls and replays them against the real `core/` — no LLM, no key, no network, deterministic. The shipped `autocodabench demo` command, and the continuous-integration job, are exactly this: a real recorded authoring run, replayed through the real file layer, producing a real bundle that the real checks then validate.

The design consequence is stated as an invariant in `architecture.md` and is worth restating here because it constrains future changes: **the audit format and the fixture format are the same format.** Any run directory doubles as a regression fixture; conversely, breaking the log schema breaks replay. We accept that coupling deliberately — it is what makes the claim "CI exercises the real code paths" true rather than aspirational.

---

## 9. Decision 7 — the model sits behind a seam (`backends/`)

**Naive design.** Import the Claude Agent SDK wherever a model is needed.

**Failure mode.** Three audiences are excluded at a stroke: continuous integration (no key), reviewers (no key, and a paid key is an unreasonable demand for reviewing open-source software), and any user whose model is not Claude. More subtly, the model itself becomes unmeasurable: if the SDK is hard-wired, "how much does bundle quality depend on the backbone model?" is not an askable question.

**Decision.** Everything above the seam talks to an abstract `AgentBackend` (`run(task) -> AgentRunResult`). Three implementations exist: `claude.py` (live, the Claude Agent SDK, imported lazily so the package functions without it), `openai_compat.py` (a self-contained tool-calling loop over any OpenAI-compatible endpoint — a local Ollama model, OpenAI, vLLM — exposing the same tool names and writing the same audit trail through an in-process registry, `local_tools.py`), and `replay.py` (Section 8).

The payoff is scientific rather than merely practical: because the backbone is a slot, it is an experimental variable, and `experiments/backbone_bench/` measures backbones against each other on a fixed protocol — including a deterministic no-LLM baseline that the judged tier must beat to justify its existence. A hard-wired SDK admits no such experiment.

Authentication lives beside the seam (`auth.py`) for one Claude-specific reason that costs users real money when misunderstood: the SDK gives an exported `ANTHROPIC_API_KEY` precedence over a stored subscription login, silently. Rather than make the user delete a key to use their plan, autocodabench stores an explicit preference (`auto`/`subscription`/`api_key`) and realizes it — choosing `subscription` hides the key from the SDK for the process, so no manual unsetting is required. `autocodabench auth status` reports and lets the user pick the path; every live command prints an `INFO:` banner naming the auth in use and runs an interactive preflight rather than failing opaquely mid-run.

---

## 10. Decision 8 — planning and building are separate sessions, joined by one locked file (`agent/`)

This was the most debated decision in the system, and the naive design is genuinely attractive.

**Naive design.** One agent session: read the proposal, think, then write the bundle, with the full deliberation available in context throughout. This is how a human works, it requires no inter-phase plumbing, and nothing is ever "lost between phases."

**Decision.** Two sessions with **zero shared conversation state**. The *plan* session reads the source material and may write exactly one artifact, `specs/implementation_plan.md`; its tool allowlist (`PLAN_TOOLS` in `agent/pipeline.py`) contains read tools and `snapshot_spec` and nothing else — it is physically incapable of writing bundle files. The *build* session starts fresh; its opening prompt contains only the plan's path ("The locked implementation plan is at .... Read it and build the bundle now."). The plan is "locked" procedurally rather than cryptographically: the planner's context is discarded, so the plan file is the *entire* channel between deliberation and execution.

Four arguments, in the order they accumulated:

1. **Context economics.** The planner ingests the bulkiest inputs in the pipeline — a proposal PDF, data documentation, exploratory reads — and that material is needed for *deciding*, not for *typing out scoring code*. Carrying it into a long implementation session means paying for those tokens on every subsequent model call, and long mixed contexts measurably degrade an LLM's instruction-following. Discarding the deliberation and re-reading a two-page distilled plan is cheaper *and* yields a more focused builder. This was the original motivation.
2. **The human review gate.** Because the plan is a Markdown file on disk and the builder reads it cold, a human can read — and edit — the plan *between* the phases. Every consequential decision (metric, phase structure, data split) passes through a human-reviewable bottleneck before any artifact is generated. In the single-session design there is nowhere to stand: the "plan" is distributed through a conversation already in progress.
3. **Auditability and blame.** When a generated bundle is wrong, the first diagnostic question is *was the plan wrong, or was the plan right and the implementation wrong?* With a locked plan the question is answerable by reading two artifacts. With one session it is unanswerable in principle, because deliberation and execution interleave.
4. **Reproducibility and rerun economics.** The build phase can be rerun from the plan alone — after a transient failure, with a different backbone, or after a human edits one section of the plan — without re-running (or re-paying for) planning. Phases that communicate only through artifacts can be re-executed independently; phases that share a context cannot.

**What the decision costs.** The builder occasionally lacks a nuance the planner knew but did not write down. We treat this as a feature with a sharp edge: an unwritten nuance is exactly the kind of implicit state the design is meant to flush out, and the experiment harness measures it directly — both phases emit a *missing-information inventory*, and the per-run report ranks the inferences that would have blocked correct scoring. The mitigation is to improve the plan skill's required sections, never to open a side channel between the sessions.

The same artifact-only principle, applied with adversarial rather than economic motivation, governs the experiment harness: in `experiments/bundle_creation_test/` the implementer is *blinded* from the ground truth and the proposal PDF, the submission-adapter is blinded from expected scores, and the orchestrator may not paraphrase one phase's content into another's prompt — each phase reads files. The harness README documents the full blinding table; the point here is that "sessions communicate only through on-disk artifacts" is one principle serving two purposes: efficiency inside `create`, and experimental validity inside the harness.

Behavioral contracts complete the picture: each phase's instructions are a versioned `SKILL.md` under `skills/` (frontmatter stripped, a per-surface runtime footer appended at load time). Prompts therefore diff like code across releases, rather than living as strings scattered through Python.

---

## 11. Decision 9 — testing splits into a keyless unit suite and a separate experiment harness

**Naive design.** One test suite covering everything, including live agent runs, gated by a key in CI secrets.

**Failure mode.** Live-LLM tests are slow (minutes), expensive (real tokens), and non-deterministic (the same prompt yields different tool sequences). Mixing them into the unit suite produces a suite nobody runs locally, flaky CI, and — the deeper problem — a category confusion: a live agent run is not a *test* with a binary oracle, it is an *experiment* with measured outcomes.

**Decision.** Three regimes, separated by directory and by epistemic role:

| Regime | Location | Properties | What it establishes |
|---|---|---|---|
| Unit suite | `tests/` | Keyless, deterministic, sub-second; replay covers the agent path | The deterministic layers are correct |
| Live smoke | manual (`autocodabench validate-bundle --judged`, `auth status --probe`) | Requires auth; run by a human before release | The live wiring works |
| Experiments | `experiments/` | Full pipeline, blinded phases, recorded artifacts, written reports | *How well* the system performs, quantitatively |

The rule "nothing in `tests/` may require a key or the network" is enforced socially and stated as an invariant, because it is the property the other two regimes lean on: when an experiment fails, the unit suite's keylessness is what licenses the inference that the failure is agentic, not infrastructural. The experiment harness, conversely, is held to standards the unit suite is not — blinding rules, a no-retry rule at the orchestrator level (a failed phase is a defect to record, not to paper over), and a prohibition on synthetic stand-in data — because its outputs are evidence in [`scientific-validation.md`](./scientific-validation.md), not green checkmarks.

---

## 12. The remaining modules, briefly

The directories not yet derived follow from the decisions above rather than adding new ones:

- **`cli/`** — argument parsing over the library. The single `autocodabench` console script and its subcommands (`validate-bundle`, `create`, …) call the same functions importable as `autocodabench.validate()` / `.create()`; the CLI adds `.env` loading and the auth preflight (Section 9) and contains no logic of its own.
- **`upload/`** — the four-step Codabench REST flow (token → placeholder dataset → upload → poll), shared verbatim by the CLI, the MCP tool, and the web UI so that there is exactly one implementation of "publish."
- **`run_log_hook.py`** — mirrors Claude Code session transcripts into the run directory, extending the Section-8 audit trail to the conversation layer.
- **`web/`** (outside the package) — a Chainlit chat front-end that consumes the installed library. Its existence is itself a small design assertion: if the web UI ever needed code the library does not export, the library's public surface would be wrong.

---

## 13. Summary

The table below restates the document in one pass: each directory, the simpler design it replaces, and the failure of that simpler design.

| Directory | Naive alternative | Why the alternative fails |
|---|---|---|
| `core/` | Mix I/O, LLM, and execution in one module | Nothing is testable without keys; bugs cannot be isolated |
| `runner/` | `subprocess.run` inside the validator | Dependency corruption, concurrent races, thread-env ordering; conflates safe parsing with code execution |
| (execution engine) | A host environment approximating the platform | More permissive than the worker, which installs nothing into the declared `docker_image`; a bundle can pass locally and fail on Codabench |
| `checks/` | One function returning error strings | No citations, so disputes with Codabench are unadjudicable; LLM grades masquerade as gates; unverifiable facts pass silently |
| (validator scope) | Validate only generated bundles | Circular co-evolution with the generator; no standalone value; the false `metadata` gate would never have been found |
| `mcp/` | Agent with unrestricted file access | Unauditable; uncontainable; blinding unenforceable |
| `run_log.py` + `backends/replay.py` | Free-text logging | No replay, so CI and reviewers cannot exercise real code paths keylessly |
| `backends/` | Hard-wired Claude SDK | Excludes keyless users and other models; backbone quality unmeasurable |
| `agent/` | One long plan-and-build session | Token cost and degraded focus; no human review gate; plan/implementation blame unanswerable; no independent rerun |
| `tests/` vs `experiments/` | One suite with live-LLM tests | Flaky, expensive, and a category error — experiments have measurements, tests have oracles |

Two threads run through every row. First, **epistemic honesty about who established what**: code gates, LLMs advise, humans attest, citations anchor, skipped checks announce themselves. Second, **artifacts over conversations**: every boundary in the system — between phases, between layers, between live and replay, between run and audit — is a file on disk that a human can read, diff, and replay. A reader who retains only those two principles can re-derive most of the table.

For the structure that results, see [`architecture.md`](./architecture.md); to watch one command traverse it, see [`validate-bundle-walkthrough.md`](./validate-bundle-walkthrough.md); for the complete inventory of checks and tests, see [`verification-catalog.md`](./verification-catalog.md); for the evidence that the result works, see [`scientific-validation.md`](./scientific-validation.md).
