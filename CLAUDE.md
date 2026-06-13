# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

**autocodabench**: a pip-installable library for agentic authoring + pre-launch validation of Codabench competition bundles, built on the Claude Agent SDK. Target venue: JMLR MLOSS (see `docs/` and the design discussion on branch `jmlr-oss-direction`). The package lives in `src/autocodabench/`; a Chainlit web UI (`web/`, deployed as an HF Space via `Dockerfile`) consumes the library; `experiments/bundle_creation_test/` is the end-to-end test harness.

The root `README.md` doubles as the HF Spaces metadata file — its YAML header configures the Space; don't remove it.

## Commands

```bash
pip install -e .                          # editable install (also: pip install -e '.[dev]')
python -m pytest tests/                   # unit suite — fast, fully keyless, must stay that way

# Keyless CLI paths (work with no Claude auth at all):
autocodabench demo --out /tmp/demo        # rebuild+validate the demo bundle from a recorded run
autocodabench validate-bundle <bundle-dir-or-zip> [--facts facts.yaml]
autocodabench checks list                 # registered checks by tier, with citations

# Auth-requiring paths (subscription login preferred; ANTHROPIC_API_KEY second):
autocodabench auth status [--probe]       # which auth path is active + foot-gun warnings
autocodabench validate-bundle <bundle> --judged      # adds LLM-judged advisory checks
autocodabench create "<idea>" [--data D]  # agentic plan→build pipeline

python -m autocodabench.core.bundle_io    # core smoke test (demo bundle in a tempdir)
python -m autocodabench.mcp.server        # MCP stdio server (hangs on stdin — correct)
python scripts/make_demo_fixture.py       # regenerate the shipped replay fixture

cd web && chainlit run app.py --host 127.0.0.1 --port 8500 -h   # web UI (needs .env)
./experiments/bundle_creation_test/setup.sh                      # symlink skills into .claude/
```

## Architecture

### Package layers (`src/autocodabench/`)

- `core/` — pure file I/O: bundle authoring (`bundle_io.py`), schema lint (`validate_bundle`), zip. No LLM, no network, no MCP. Unit-tested conventionally.
- `runner/` — runtime counterpart: stages the Codabench sandbox and executes scoring/ingestion through two engines. **docker** (preferred when a daemon is reachable): runs programs inside the bundle's declared `docker_image` exactly as the Codabench worker does — sandbox at `/app`, workdir `/app/program`, **no** requirements installation (the platform installs nothing). **conda** (fallback + notebook host): clones `acb-run-<short>` (deterministic from the run-dir name), installs per-program `requirements.txt` via uv/pip — more permissive than the platform, so results carry a fidelity note. One-shot functions; iteration is the agent's job. BLAS/OMP thread vars must be set in the subprocess env *before* Python starts (conda engine).
- `checks/` — the validation framework. `Check` components registered in three tiers with different epistemic standing: **deterministic** (code computes PASS/FAIL — the only tier that gates), **judged** (LLM grades a rubric → advisory FINDINGs, never gates), **attestation** (human-only criteria, surfaced as unchecked boxes). Checks that need undeclarable context consume `competition_facts.yaml` (declare-then-verify); missing facts → SKIPPED with instructions, never a silent pass. Every check carries a citation (Pavão et al. chapter or Codabench schema).
- `backends/` — the AgentBackend seam. `claude.py` (live, Claude Agent SDK; lazy import) and `replay.py` (re-executes a recorded run's `tool_calls/` against the real core — keyless, deterministic; powers `demo` and CI). Everything above the seam talks only to `backends.base.AgentTask/AgentRunResult`.
- `agent/` — the plan→build pipeline: two isolated SDK sessions joined only by the locked `specs/implementation_plan.md`. Prompts come from the packaged skills (`skills/*/SKILL.md`, frontmatter stripped + per-surface runtime footer). Per-phase tool allowlists are the capability contract.
- `mcp/` — FastMCP stdio server exposing 20 tools over core+runner (`instance.py` holds the shared FastMCP object; `tools/` are thin logged wrappers). It is *one interface*, spawned as a subprocess by both the agent pipeline and the web UI.
- `auth.py` — subscription-vs-API-key status. Key invariant to preserve in docs and code: the SDK gives `ANTHROPIC_API_KEY` precedence over a subscription login; multi-user deployments MUST use an API key (Anthropic ToS), local dev should prefer subscription.
- `run_log.py` — every MCP tool call is snapshotted to `<run>/tool_calls/NNNN_*.json` + `events.jsonl` via the `logged_tool` decorator. This audit trail is also the replay-fixture format — don't break that duality.

### Path resolution

Nothing may assume a repo checkout. Artifact roots resolve: explicit arg → env var (`AUTOCODABENCH_HOME` / `AUTOCODABENCH_BUNDLES_ROOT` / `AUTOCODABENCH_RUNS_ROOT`) → `<cwd>/.autocodabench/`. The *active session* is `AUTOCODABENCH_RUN_DIR`, set by `open_run()` and inherited by child processes so fresh MCP subprocesses adopt their parent's run (`current_run()` adoption). `resolve_bundle_dir()` scopes bundles into the active run dir, which is what isolates concurrent web sessions.

### Experiment harness (`experiments/bundle_creation_test/`)

Seven-phase pipeline (plan → implement+self-validate → per-submission reformat+run → log-audit → aggregate → finalize → report); phases 2/3/4a are `claude -p` shell-outs against the packaged skills. **Data-leakage rules are load-bearing** (table in its README): the implementer never sees `ground_truth/**` or the proposal PDF; reformat-and-run never sees `expected_result.json`; `ground_truth/bundle/` is human-only. The harness predates the `src/` re-layout — `setup.sh` paths are updated, but expect references to old runs' layouts inside recorded artifacts.

## Conventions

- `fastmcp` is pinned to exactly `2.14.7` — looser constraints break on HF Spaces (see pyproject comment and the Dockerfile install-order comment). Don't relax it.
- The unit suite must stay keyless and fast: live-SDK behavior is verified manually (`autocodabench validate-bundle --judged`, `autocodabench auth status --probe`), never in `tests/`.
- Judged checks emit FINDINGs, never PASS/FAIL gates — "valid" is defined by executable checks only. Preserve the three-status report semantics (FAIL gates; FINDING advises; ATTESTATION_REQUIRED surfaces).
- Do not add a Claude co-author trailer to commits or PRs.
