# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**autocodabench**: a pip-installable library for agentic authoring + pre-launch validation of Codabench competition bundles, built on the Claude Agent SDK. Target venue: JMLR MLOSS (see `docs/` and the design discussion on branch `jmlr-oss-direction`). The package lives in `src/autocodabench/`; a Chainlit web UI (`web/`, deployed as an HF Space via `Dockerfile`) consumes the library; `benchmark/` holds the pure-SDK end-to-end benchmarks (create-bench and validate-bench, both live under `benchmark/`).

The root `README.md` doubles as the HF Spaces metadata file — its YAML header configures the Space; don't remove it.

## Commands

```bash
pip install -e .                          # editable install (also: pip install -e '.[dev]')
python -m pytest tests/                   # unit suite — fast, fully keyless, must stay that way

# Keyless CLI paths (work with no Claude auth at all):
autocodabench demo --out /tmp/demo        # rebuild+validate the demo bundle from a recorded run
autocodabench validate <bundle-dir-or-zip> [--facts facts.yaml]
autocodabench checks list                 # registered checks by tier, with citations

# Auth-requiring paths (subscription login preferred; ANTHROPIC_API_KEY second):
autocodabench auth status [--no-probe]    # active path + masked creds; verifies the SDK can sign in (live turn) unless --no-probe
autocodabench auth use <auto|subscription|api_key>   # choose; subscription hides any key from the SDK
autocodabench validate <bundle> --judged      # adds LLM-judged advisory checks
autocodabench plan-build-validate "<idea>" [--data D] [--pdf P]   # agentic plan→build→validate pipeline (alias: create; idea and/or a PDF proposal)
autocodabench plan "<idea>" [--data D]   # Phase 1 only → specs/implementation_plan.md
autocodabench build <plan.md | --run-dir D>  # Phase 2 only → build a bundle from a plan

# Any agentic command above accepts --backend: claude[:model] (default), ollama:<m>, openai:<m>, or URL#<m>.

python -m autocodabench.core.bundle_io    # core smoke test (demo bundle in a tempdir)
python -m autocodabench.mcp.server        # MCP stdio server (hangs on stdin — correct)
python scripts/make_demo_fixture.py       # regenerate the shipped replay fixture

cd web && chainlit run app.py --host 127.0.0.1 --port 8500 -h   # web UI (needs .env)

# Benchmarks (pure-SDK; any backbone via --backend; needs Docker + a populated instrument):
python benchmark/autocodabench_create_bench/run.py --competition style-trans-fair --backend claude
```

## Architecture

### Package layers (`src/autocodabench/`)

- `core/` — pure file I/O: bundle authoring (`bundle_io.py`), schema lint (`validate_bundle`), zip. No LLM, no network, no MCP. Unit-tested conventionally.
- `runner/` — runtime counterpart: stages the Codabench sandbox and executes scoring/ingestion/notebook **exclusively through Docker** (the conda engine has been removed). Programs run inside the bundle's declared `docker_image` exactly as the Codabench worker does — sandbox at `/app`, workdir `/app/program`, **no** requirements installation (the platform installs nothing); the starting-kit notebook runs the same way (bundle mounted at `/app`, executed with the image's pinned `jupyter`/`nbconvert` toolchain). A missing Docker daemon is a hard error (`resolve_execution_engine`). Default image is the autocodabench base image (`{AUTOCODABENCH_DOCKER_NAMESPACE}/autocodabench-base-cpu:latest`; GPU variant `_DEFAULT_DOCKER_IMAGE_GPU`), overridable via `AUTOCODABENCH_DOCKER_IMAGE[_GPU]`; build from `docker/`. `prepare_run_env` only ensures the image is present locally (pull if needed); `install_env_extras` returns a "change docker_image instead" error; `remove_run_env` is a no-op (containers run `--rm`). A `docker_preflight` (image arch fit native-vs-QEMU + daemon status) is surfaced by `plan-build-validate`/`validate`. One-shot functions; iteration is the agent's job.
- `checks/` — the validation framework. `Check` components registered in three tiers with different epistemic standing: **deterministic** (code computes PASS/FAIL — the only tier that gates), **judged** (LLM grades a rubric → advisory FINDINGs, never gates), **attestation** (human-only criteria, surfaced as unchecked boxes). Checks that need undeclarable context consume `competition_facts.yaml` (declare-then-verify); missing facts → SKIPPED with instructions, never a silent pass. Every check carries a citation (Pavão et al. chapter or Codabench schema).
- `backends/` — the AgentBackend seam. `claude.py` (live, Claude Agent SDK; lazy import), `openai_compat.py` (a stdlib tool-calling loop over `/chat/completions` for any OpenAI-compatible endpoint — Ollama/OpenAI/vLLM — given the same 20-tool surface + `tool_calls/` audit trail via `local_tools.py`), and `replay.py` (re-executes a recorded run's `tool_calls/` against the real core — keyless, deterministic; powers `demo` and CI). `resolve_backend(spec, model)` parses `claude[:model]` / `ollama:<m>` / `openai:<m>` / `URL#<m>`. Everything above the seam talks only to `backends.base.AgentTask/AgentRunResult`.
- `agent/` — the plan→build pipeline: two isolated SDK sessions joined only by the locked `specs/implementation_plan.md`. Prompts come from the packaged skills (`skills/*/SKILL.md`, frontmatter stripped + per-surface runtime footer). Per-phase tool allowlists are the capability contract. `create_async` runs the full plan→build→validate pipeline (one session, per-phase subdirs); `plan_async`/`bundle_async` (CLI `plan`/`build`) run either phase standalone; `reformat.py` is the reformat-and-run phase used by the benchmark. `research.py` is the Phase-1 external-knowledge capability — OpenAlex (external `openalex-research-mcp` via `npx`) + Kaggle (first-party tools in `mcp/tools/research.py` wrapping the Kaggle SDK; `kaggle` ships in the base install, imported lazily) + web search (last resort), on by default, resolved per backbone (Claude-only: it's the only backend that hosts external MCP/web tools), CLI-toggleable via `--no-research`/`--no-openalex`/`--no-kaggle`/`--no-web-search`, and recorded in benchmark results for fairness. `resolve()` returns external servers + extra tools + env (e.g. the Kaggle token, injected into the autocodabench MCP server). The network tools clear the FS sandbox only for a research-granted plan phase (`AgentTask.allow_web_tools`).
- `mcp/` — FastMCP stdio server exposing 20 tools over core+runner (`instance.py` holds the shared FastMCP object; `tools/` are thin logged wrappers). It is *one interface*, spawned as a subprocess by both the agent pipeline and the web UI.
- `auth.py` — subscription-vs-API-key status, plus a persisted **auth preference** (`auto|subscription|api_key`, in `~/.config/autocodabench/auth.json`, env override `AUTOCODABENCH_AUTH`). The SDK gives `ANTHROPIC_API_KEY` precedence over a subscription login; rather than make users unset the key, `apply_auth_preference()` hides it from the process when the preference is `subscription`. Every live command prints an `INFO:` auth banner. Invariant: multi-user deployments MUST use an API key (Anthropic ToS); local dev should prefer subscription.
- `run_log.py` — every MCP tool call is snapshotted to `<run>/tool_calls/NNNN_*.json` + `events.jsonl` via the `logged_tool` decorator. This audit trail is also the replay-fixture format — don't break that duality.

### Path resolution

Nothing may assume a repo checkout. Artifact roots resolve: explicit arg → env var (`AUTOCODABENCH_HOME` / `AUTOCODABENCH_BUNDLES_ROOT` / `AUTOCODABENCH_RUNS_ROOT`) → `<cwd>/.autocodabench/`. The *active session* is `AUTOCODABENCH_RUN_DIR`, set by `open_run()` and inherited by child processes so fresh MCP subprocesses adopt their parent's run (`current_run()` adoption). `resolve_bundle_dir()` scopes bundles into the active run dir, which is what isolates concurrent web sessions.

### Benchmarks (`benchmark/`)

Pure-SDK orchestrators — no `claude -p` shell-outs, no ambient `.mcp.json`/`.claude` dependency — so the backbone is a measured variable and runs are reproducible on any machine (incl. offline GPU via `ollama:`/`openai:`/`URL#model`). `autocodabench_create_bench/run.py` drives plan→build→self-validate via `create_async(pdf=...)`, then runs the reformat-and-run phase per ground-truth submission and audits the produced score against `expected_result.json` deterministically (`bench/audit.py`). Reusable logic lives in the package (`autocodabench.bench`, `autocodabench.agent.reformat`); the runnable harness + instruments + contributed `results/` live under `benchmark/`. **Data-leakage isolation is a code invariant**, not prompt discipline: the build session only ever receives `input/**`; reformat-and-run only receives the bundle + a submission dir (never `expected_result.json`); the auditor reads the expected score but is plain Python. `benchmark/autocodabench_validate_bench/` is the validate-bench: it seeds known authoring defects into a clean bundle and measures the validator's catch rate per tier.

## Conventions

- `fastmcp` is pinned to exactly `2.14.7` — looser constraints break on HF Spaces (see pyproject comment and the Dockerfile install-order comment). Don't relax it.
- The unit suite must stay keyless and fast: live-SDK behavior is verified manually (`autocodabench validate --judged`, `autocodabench auth status --probe`), never in `tests/`.
- Judged checks emit FINDINGs, never PASS/FAIL gates — "valid" is defined by executable checks only. Preserve the three-status report semantics (FAIL gates; FINDING advises; ATTESTATION_REQUIRED surfaces).
- Do not add a Claude co-author trailer to commits or PRs.
