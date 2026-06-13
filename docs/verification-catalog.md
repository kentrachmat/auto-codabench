# Verification catalog: every check and test, and what each one establishes

This document is a complete inventory of the verification performed by and on autocodabench. It serves two readers. A **competition organizer** about to upload a bundle to Codabench will find here, in one place, everything that will be examined about that bundle — statically, dynamically, and by human attestation. A **reviewer** assessing the software will find the full catalog of tests that verify the tool itself, together with the system-level evidence that the two bodies of verification compose.

The catalog is organized into four layers, distinguished by *what is under test*:

| Layer | Object under test | Mechanism | Section |
|---|---|---|---|
| 1 | The organizer's bundle (static) | 17 registered checks (`autocodabench validate-bundle`) | §2 |
| 2 | The organizer's bundle (dynamic) | Actual execution of the bundle's programs | §3 |
| 3 | autocodabench itself | 65-test keyless unit suite (`tests/`) | §4 |
| 4 | The system end to end | CI matrix, seeded-defect instrument, blinded experiment harness | §5 |

Layers 1–2 answer the organizer's question, "what will be tested about my competition?" Layers 3–4 answer the reviewer's question, "why should the answers from layers 1–2 be trusted?"

---

## 1. The verdict vocabulary

Every result in layer 1 carries one of four statuses, and the distinctions are enforced by the framework rather than left to convention (see [`design-rationale.md`](./design-rationale.md), Section 5):

- **PASS / FAIL** — a code-computed verdict. FAIL is the only status that gates: `ValidationReport.ok` is defined as the absence of FAILs.
- **FINDING** — an advisory deficiency. The bundle remains uploadable; the report explains what would be improved and cites why.
- **SKIPPED** — the check could not run, and says so explicitly, with instructions (typically: a required fact is undeclared). A skipped check is never counted as a pass.
- **ATTESTATION_REQUIRED** — a criterion only a human can certify, surfaced as an unchecked box.

Every registered check carries a citation, either to the Codabench bundle schema or to Pavão et al. (2024), *AI Competitions and Benchmarks: The Science Behind the Contests* — the competition-design reference whose checklist the registry operationalizes. The live registry, with citations, is printed by `autocodabench checks list`.

---

## 2. Layer 1 — static checks on the bundle (`autocodabench validate-bundle`)

### 2.1 The structural gate: `bundle-schema`

One check gates: `bundle-schema` (severity BLOCKER, cited to the Codabench schema). It wraps the schema lint in `core/bundle_io.py::validate_bundle` and converts each error-severity lint issue into a FAIL. The lint examines six condition families:

| # | Condition | Failure it prevents on the platform |
|---|---|---|
| 1 | `competition.yaml` parses and carries the required top-level keys | Upload rejected or competition page blank |
| 2 | Every file referenced from `competition.yaml` exists in the bundle | Broken pages, missing programs, dead data references after unpacking |
| 3 | Every leaderboard `column.key` appears among the keys the scoring program writes (static scan of the scorer for `json.dump(...)` / `scores.json` keys; an inconclusive scan warns rather than gates) | A leaderboard column that never populates because the scorer emits differently named scores |
| 4 | Each scoring/ingestion program carries a `metadata.yaml` with a `command:` (the legacy extensionless `metadata` filename, which production Codabench accepts, is also accepted — a regression-tested fix found by validating a production bundle) | The platform has no entry point to execute the program |
| 5 | Phases are sorted, do not overlap, and reference declared tasks | Submissions routed to the wrong phase, or no open phase at launch |
| 6 | Tasks referenced from phases and solutions exist by index | Dangling task references that fail at evaluation time |

### 2.2 Deterministic design checks (advisory)

The remaining ten deterministic checks examine competition *design quality* rather than structural validity. They deliberately emit FINDINGs, not FAILs: a bundle violating them is still accepted and executed by Codabench, so gating on them would overstate the validator's authority. Each finding cites the chapter of Pavão et al. (2024) that motivates it.

| Check id | What is examined | Cited rationale |
|---|---|---|
| `two-phase-structure` | A development phase and a final phase both exist | Single-phase contests conflate model development with final evaluation (Ch. 5, 11) |
| `dev-phase-duration` | The development phase runs at least 40 days | Shorter windows disadvantage participants without ready pipelines (Ch. 13) |
| `daily-submission-cap` | Development phases declare a daily submission cap | Uncapped phases invite leaderboard probing of the test set (Ch. 5) |
| `final-phase-submission-limit` | The final phase allows at most 3 total submissions | Unlimited final submissions reintroduce test-set overfitting at the stage meant to prevent it (Ch. 5) |
| `leaderboard-sorting` | Every leaderboard column declares its sorting direction | An undeclared direction can silently rank ascending when the metric means descending (Ch. 4; Codabench schema) |
| `starting-kit` | A runnable starting kit ships with the bundle | Entry friction measurably reduces participation (Ch. 5, 13) |
| `baseline-solutions` | Baseline solutions ship *and* are declared in `competition.yaml` (shipped-but-undeclared is flagged: Codabench will not run them) | Without baselines the bundle cannot be smoke-tested and participants lack a reference score (Ch. 5) |
| `docker-image-pinned` | The worker Docker image is pinned | An unpinned image makes scores irreproducible across the competition's lifetime (Ch. 11) |
| `test-set-size` | Test-set size satisfies the 100/E rule for the anticipated error rate | An undersized test set cannot statistically separate the leaders (Ch. 4) — *facts-gated, see §2.3* |
| `external-data-rule` | The external-data rule is declared and documented in the pages | An unstated rule is unenforceable and contested after the fact (Ch. 5) — *facts-gated, see §2.3* |

### 2.3 Facts-gated checks: declare-then-verify

Some checks require context the bundle cannot carry (the anticipated error rate of a top system; whether external data is permitted). These checks declare `requires_facts`, consume a `competition_facts.yaml` supplied with `--facts`, and report **SKIPPED with instructions for declaring the missing fact** whenever it is absent. Unknown fact keys are rejected rather than ignored, so a typo cannot silently disable a check. The recognized facts are `anticipated_error_rate`, `test_set_size`, `unit_of_generalization`, `external_data_allowed`, `prizes`, and `task_type`.

### 2.4 The judged tier (advisory by construction)

One check is LLM-judged: `judged-docs-config-consistency` (Ch. 11, 13), which asks whether the human-readable pages contradict the machine-readable configuration — submission limits stated in prose versus declared caps, metric direction, phase dates. Judged checks run only with `--judged` and an authenticated backend; their verdicts are FINDINGs, never gates, and an unparseable model response degrades to SKIPPED rather than to any verdict. What the judge contributes is coverage of semantic properties code cannot see; what it never contributes is authority over `ok`.

### 2.5 The attestation tier (human-only criteria)

Five launch criteria can be certified only by a person. The validator surfaces them in every report as explicit unchecked items, so they can be tracked without ever being assumed:

| Attestation id | What a human must certify |
|---|---|
| `attest-external-review` | The competition proposal received external review (Ch. 2) |
| `attest-datasheet` | A datasheet / data nutrition label exists for the dataset (Ch. 3) |
| `attest-leakage-probe` | A per-feature leakage probe was run on the data (Ch. 3) |
| `attest-data-persistence` | The dataset has a license and a post-competition home (Ch. 3, 13) |
| `attest-game-of-skill` | Prizes are legal as a game of skill in the relevant jurisdictions (Ch. 13) |

---

## 3. Layer 2 — dynamic verification: the bundle is executed

Static checks cannot establish that a scoring program *runs*. The runner layer (`runner/execution.py`, exposed through the MCP tools and used by the `create` pipeline's self-validation) stages the Codabench worker's sandbox layout and executes the bundle's programs. When a Docker daemon is available, execution is platform-faithful: programs run inside the bundle's declared `docker_image` (Codabench's default, `codalab/codalab-legacy:py37`, when none is declared), with the active program directory mounted at `/app/program` and the data and output trees at `/app/input` and `/app/output` — the worker's layout — and with **no dependency installation**, since the platform's worker never installs `requirements.txt`. A clean run under this engine is therefore evidence the bundle will execute on Codabench; a subsequent platform failure points at the server, not the bundle. Without Docker, a per-run conda environment with the bundle's `requirements.txt` installed serves as the fallback; it verifies the programs but is more permissive than the platform, and every result records which engine ran, with an explicit fidelity note on the fallback. Three execution stages exist:

1. **Baseline execution** — the bundle's shipped baseline solution is run through the full ingestion-then-scoring pipeline; the run must complete and produce scores. This catches missing dependencies, API breaks, and scorer crashes that no static scan can see.
2. **Starting-kit execution** — the starting-kit notebook is executed end to end, cell by cell, verifying that the artifact participants will run first actually runs.
3. **External-submission scoring** — an arbitrary submission directory can be run through the same pipeline (`run_user_submission`), which is how the experiment harness scores real ground-truth submissions against a generated bundle (§5.3).

In the agentic `create` pipeline these steps are mandatory self-validation: a bundle whose baseline or starting kit fails after the attempt budget is not zipped. For an imported bundle, the same functions are available through the library and the MCP tools.

---

## 4. Layer 3 — the unit suite: verifying the verifier

The 65 tests in `tests/` verify autocodabench itself. The suite is **keyless, network-free, and sub-second** as a hard rule: every agentic code path is exercised through the recorded-replay backend rather than a live model, which is what allows continuous integration and reviewers to run all of it with no credentials. The tests are listed in full because a substantial fraction of them verify the *epistemic contract* of §1 — that gates gate, findings advise, and skips never pass — rather than ordinary plumbing.

### 4.1 The check framework checked (`test_checks.py`, 13 tests)

| Test | What it establishes |
|---|---|
| `test_demo_bundle_passes_gates` | The shipped demo bundle passes every deterministic gate (the suite's positive control) |
| `test_zip_validation_equivalent` | Validating a zip and validating the unpacked directory yield the same report |
| `test_schema_failure_gates` | A structural defect produces FAIL and flips `ok` to false |
| `test_missing_starting_kit_is_finding_not_gate` | A design deficiency produces a FINDING and leaves `ok` true — the advisory tier cannot gate |
| `test_single_phase_finding` | A missing final phase is detected and reported as advisory |
| `test_uncapped_dev_phase_finding` | An uncapped development phase is detected and reported as advisory |
| `test_facts_gate_skips_without_facts` | A facts-gated check reports SKIPPED, not PASS, when the fact is undeclared |
| `test_test_set_size_uses_declared_facts` | The 100/E sizing check consumes declared facts and passes a correctly sized set |
| `test_test_set_size_flags_undersized_set` | The same check flags an undersized test set |
| `test_attestations_always_surface` | Every attestation item appears in every report — human criteria cannot be forgotten |
| `test_unknown_fact_key_rejected` | A misspelled fact key raises an error instead of silently disabling a check |
| `test_checklist_coverage_lists_all_tiers` | The registry contains all three tiers (guards against an import regression emptying a tier) |
| `test_report_markdown_renders` | The Markdown rendering of a report is well formed |

### 4.2 The file layer (`test_core_bundle_io.py`, 12 tests)

| Test | What it establishes |
|---|---|
| `test_init_bundle_creates_layout` | Bundle skeletons are created with the expected structure |
| `test_init_bundle_refuses_overwrite_by_default` | An existing bundle is never silently clobbered |
| `test_competition_yaml_rejects_unknown_keys` | Unknown configuration keys are rejected, not passed through to the platform |
| `test_competition_yaml_requires_keys` | Required keys are enforced at write time |
| `test_page_path_traversal_rejected` | A page path cannot escape the bundle root (path-traversal defense) |
| `test_validate_clean_bundle` | The lint passes a well-formed bundle (positive control) |
| `test_validate_catches_missing_page` | A dangling page reference is caught (§2.1, condition 2) |
| `test_validate_warns_on_unwritten_leaderboard_key` | A leaderboard key the scorer never writes is flagged (§2.1, condition 3) |
| `test_zip_puts_yaml_at_root` | The zip places `competition.yaml` at the archive root, as Codabench requires |
| `test_resolve_bundle_dir_rejects_bad_slugs` | Malformed slugs cannot resolve to arbitrary paths |
| `test_resolve_bundle_dir_prefers_run_dir` | Bundles scope into the active run directory, which is what isolates concurrent sessions |
| `test_validate_accepts_legacy_metadata_filename` | Regression test for the false gate found on a production bundle (§2.1, condition 4) |

### 4.3 The keyless agent path (`test_replay_backend.py`, 5 tests)

| Test | What it establishes |
|---|---|
| `test_replay_rebuilds_validates_and_zips` | A recorded run replays through the real core into a real, valid, zipped bundle |
| `test_replay_is_deterministic` | Two replays of the same fixture produce identical results |
| `test_unknown_and_session_tools_skipped` | Unknown or session-scoped recorded calls are skipped safely, not executed blindly |
| `test_load_fixture_from_run_dir` | A raw run directory is loadable as a fixture — the audit-format/fixture-format duality holds |
| `test_on_text_callback_fires` | Streaming progress callbacks fire during replay (the UI contract) |

### 4.4 The generic model backend (`test_openai_compat.py`, 6 tests)

| Test | What it establishes |
|---|---|
| `test_plain_chat_no_tools` | A toolless exchange completes against a scripted transport (no network in tests) |
| `test_tool_call_round_trip` | A model tool call is executed and its result returned to the model |
| `test_unknown_tool_returns_error_not_crash` | A hallucinated tool name yields a structured error, not a crash |
| `test_max_turns_guard` | The loop terminates at the turn budget (runaway-agent guard) |
| `test_select_tools_mapping` | Tool-name selection maps onto the registered local tool surface |
| `test_resolve_backend_specs` | Backend specifications (`claude`, `ollama:<model>`, `openai:<model>`, URL forms) parse correctly, with `--model` taking precedence |

### 4.5 Authentication (`test_auth.py`, 13 tests)

| Test | What it establishes |
|---|---|
| `test_none_detected` | A machine with no credentials reports `none`, with no spurious warnings |
| `test_api_key_wins` | An exported key is detected as the effective path |
| `test_subscription_via_credentials_file` / `test_subscription_via_oauth_account` | Both on-disk subscription artifacts are recognized |
| `test_api_key_shadows_subscription_with_warning` | The key-shadows-subscription hazard produces an explicit warning |
| `test_empty_api_key_warns` | An empty exported key is flagged rather than treated as valid |
| `test_describe_renders` | The status report renders |
| `test_load_dotenv_parses_and_never_overrides` | `.env` parsing handles `export`/quotes/comments and never overrides real environment variables |
| `test_load_dotenv_missing_file_is_noop` | A missing `.env` is a no-op, not an error |
| `test_ensure_live_auth_noninteractive_raises_with_guidance` | Headless contexts get a refusal with guidance instead of an opaque mid-run failure |
| `test_ensure_live_auth_passes_through_existing_key` | An authenticated environment passes the preflight untouched |
| `test_ensure_live_auth_interactive_api_key_entry` | The interactive flow accepts a pasted key and persists it to `./.env` with mode 600 |
| `test_ensure_live_auth_interactive_quit_raises` | Declining the interactive flow aborts cleanly |

### 4.6 The Docker execution engine (`test_docker_engine.py`, 10 tests)

| Test | What it establishes |
|---|---|
| `test_auto_prefers_docker_when_available` | `engine="auto"` selects Docker whenever a daemon is reachable |
| `test_auto_falls_back_to_conda_with_note` | Without Docker, the fallback is taken and announced, never silent |
| `test_explicit_docker_errors_without_daemon` | `engine="docker"` on a Docker-less host fails with a clear error |
| `test_explicit_conda_carries_fidelity_note` | An explicitly requested conda run carries the fidelity caveat |
| `test_unknown_engine_rejected` | A misspelled engine name is an error, not a silent default |
| `test_docker_run_mirrors_worker_contract` | The constructed `docker run` matches the worker: program dir mounted at `/app/program`, `/app/input` and `/app/output` mounts, `/app/program` working directory, `$program`/`$input`/`$output` resolution, and **no** `pip install` |
| `test_conda_translate_maps_worker_paths_to_host` | The conda fallback rewrites both `$variable` and `/app/...` spellings to real host paths, longest-token-first (so `/app/input` and `/app/input_data` do not collide) |
| `test_bundle_docker_image_reads_declared_image` | The engine uses the image `competition.yaml` declares |
| `test_bundle_docker_image_defaults_to_platform_default` | An undeclared image resolves to Codabench's default (`codalab/codalab-legacy:py37`) |
| `test_run_user_submission_requires_daemon_for_explicit_docker` | The engine error propagates through the public scoring entry point |

### 4.7 The CLI contract (`test_cli.py`, 6 tests)

| Test | What it establishes |
|---|---|
| `test_checks_list` | The registry listing renders with all tiers |
| `test_demo_then_validate` | The full keyless path — replay a recorded run, then validate the result — works end to end |
| `test_validate_json_output` | `validate-bundle --json` emits a machine-readable report |
| `test_validate_legacy_alias_still_works` | `validate` remains a back-compatible alias for `validate-bundle` |
| `test_validate_exit_code_on_gate_failure` | A gated failure exits non-zero (the contract scripts and CI depend on) |
| `test_version` | `--version` reports the package version |

---

## 5. Layer 4 — system-level evidence

### 5.1 Continuous integration

CI runs the entire layer-3 suite across a Python 3.10–3.13 × Linux/macOS matrix, executes the offline demo (a real recorded run replayed into a real validated bundle), and checks the built wheel's contents. Because the suite is keyless, CI exercises the genuine code paths rather than mocks of them.

### 5.2 The seeded-defect instrument

`experiments/backbone_bench/run_judge_bench.py` seeds known authoring defects into otherwise clean bundles (rebuilt deterministically from the replay fixture) and measures whether the validator catches them, together with the false-positive rate on clean copies. The defect library comprises 9 deterministic-tier targets and 3 judged-tier targets (pages↔config contradictions in submission caps, metric direction, and phase dates). The deterministic tier is backbone-independent and must score 9/9 with zero false positives — it is the instrument's sanity baseline; the judged tier is the backbone-sensitive measurement. This is the direct answer to "does the validator actually detect the defects it claims to detect?" — measured, not asserted.

### 5.3 The blinded end-to-end harness

`experiments/bundle_creation_test/` runs the full authoring pipeline against real competitions with held-out ground truth, under blinding rules (the implementer never sees the ground truth or the proposal PDF; the submission adapter never sees expected scores) and a no-retry rule at the orchestrator level. Real ground-truth submissions are scored through the generated bundle and audited against expected results within declared tolerances. The protocol and results are documented in [`scientific-validation.md`](./scientific-validation.md) and the harness [README](../experiments/bundle_creation_test/README.md).

### 5.4 What is deliberately *not* unit-tested

Live-SDK behavior (a real Claude session, a real judged check) is verified manually before releases (`autocodabench validate-bundle --judged`, `autocodabench auth status --probe`) and measured by the experiments, never placed in `tests/`. Live model calls are slow, billed, and non-deterministic; admitting them into the unit suite would trade a fast, trustworthy oracle for a flaky one. The same boundary applies to actual container execution: the docker engine's selection logic and command construction are unit-tested (§4.6), while pulling and running a real image is verified manually and by experiment runs on Docker-equipped hosts. The boundary is stated as an invariant in [`architecture.md`](./architecture.md).

---

## 6. Summary

| Layer | Count |
|---|---|
| Registered checks on the bundle | 17 (11 deterministic, of which 1 gates; 1 judged; 5 attestation) |
| Structural lint condition families inside the gate | 6 |
| Dynamic execution stages | 3 (baseline, starting kit, external submission) |
| Unit tests on the tool | 65 across 7 modules, keyless and sub-second |
| Execution engines | 2 (docker — platform-faithful; conda — fallback with fidelity note) |
| Seeded defect classes in the validator instrument | 12 (9 deterministic-tier, 3 judged-tier) |
| CI matrix | Python 3.10–3.13 × Linux/macOS, plus offline demo and wheel checks |

Adding to the catalog is intentionally cheap: a new bundle check is one registered `Check` subclass with a citation (it then appears in `autocodabench checks list`, every report, and the seeded-defect instrument's reach), and a new unit test is one keyless function in `tests/`. The procedure is given in [`architecture.md`](./architecture.md), Section 7.
