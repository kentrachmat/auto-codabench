# Changelog

All notable changes to autocodabench. Format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[SemVer](https://semver.org/).

## [Unreleased]

### Changed
- **`auth status` and `auth use` now verify by default.** Both realize the
  resolved auth preference and authenticate the agent SDK with it — one
  minimal live turn — rather than reporting only on-disk credential
  detection, which cannot prove a login is accepted. The verification is on
  by default (it was previously opt-in via `--probe`); pass `--no-probe` for
  static detection only (offline / CI). `--probe` is still accepted as a
  no-op. The Codabench section is relabeled to make clear those credentials
  are the codabench.org account login used only for publishing — never Claude
  or agent-SDK auth (Claude auth has no username/password concept).
- The standalone `codabench-validate` console script is removed; bundle
  validation is now the `autocodabench validate-bundle` subcommand (with
  `validate` kept as a back-compatible alias). One console script,
  `autocodabench`, with subcommands; the validator still accepts any
  bundle directory or zip, hand-written or generated.

### Fixed
- Runner misclassified a λ-style (prediction-file) bundle as γ-style when
  `ingestion_program/` existed but was empty — `init_bundle` creates that
  skeleton directory for every bundle, so the runner then tried to execute
  a nonexistent `ingestion.py`. Now requires the directory to hold
  runnable content. Surfaced by the first real docker-engine run of the
  demo bundle.
- `validate_bundle` falsely gated bundles using the legacy extensionless
  `metadata` program filename, which production Codabench accepts —
  found by validating the STYLE-TRANS-FAIR production reference bundle;
  regression-tested.

### Added
- **In-place Claude sign-in.** When the subscription path is chosen but no
  login is found (the `auth status` picker, `auth use subscription`, or the
  `create` / `--judged` preflight), autocodabench now asks for consent and,
  on agreement, launches Claude Code's own sign-in (`claude auth login
  --claudeai`) as a child process — no second terminal, no manual `/login`.
  Consent is always requested first ("We did not find your Claude
  credentials… sign in now?"); declining is a first-class outcome and the
  user can quit and run `claude auth login` themselves. Public helper
  `launch_claude_login()` in `autocodabench.auth`; autocodabench delegates the
  OAuth flow to the official CLI and never handles subscription tokens itself.
- **Masked credential inspection** in `autocodabench auth status`: the
  report now shows a non-recoverable preview of each configured secret
  rather than a bare boolean — `ANTHROPIC_API_KEY` as its scheme prefix
  plus last four characters and length, and a second block for the
  Codabench publishing credentials (`CODABENCH_USERNAME` in full,
  `CODABENCH_PASSWORD` and `CODABENCH_TOKEN` masked). Absent, set-but-empty,
  and present values are distinguishable. Public helpers `mask_secret`,
  `codabench_credentials_status`, and `describe_codabench_credentials` in
  `autocodabench.auth`.
- **Auth preference (choose without unsetting)**: a persisted
  `auto|subscription|api_key` preference (`~/.config/autocodabench/auth.json`,
  env override `AUTOCODABENCH_AUTH`). The Claude SDK prefers
  `ANTHROPIC_API_KEY` over a subscription login; instead of requiring users
  to delete the key, `auth use subscription` (or the picker in `auth status`)
  hides the key from the SDK for the run so the subscription is used.
  `autocodabench auth use <mode>` sets it non-interactively and can paste a
  key (hidden input, optional save to `./.env`) without editing files. Every
  command that starts a live model session prints an `INFO:` banner naming
  the auth in use (API key / subscription / none).
- **Docker execution engine** (platform-faithful runs):
  `run_baseline_submission` / `run_user_submission` (library + MCP tools)
  accept `engine: auto|docker|conda`. The docker engine — selected by
  default whenever a daemon is reachable — executes programs inside the
  bundle's declared `docker_image` exactly as the Codabench worker does
  (sandbox mounted at `/app`, working dir `/app/program`, legacy
  `$input`/`$output`/`$program` substitution, **no** requirements
  installation, platform default `codalab/codalab-legacy:py37`), so a
  clean local run is evidence the bundle will execute on Codabench. The
  conda engine remains the fallback (and the starting-kit notebook host),
  and honors the same worker path tokens by rewriting `$program`/`$input`/
  `$output` (and the `/app/...` spellings) to host sandbox paths; every
  result records `engine` / `docker_image` / `engine_note`. Verified
  end-to-end: the demo bundle's baseline scores identically (accuracy
  0.825) under both engines, the docker run executing inside the real
  `codalab/codalab-legacy:py39` image.
- **Interactive auth preflight**: `create` and `validate-bundle --judged` now
  check for a usable Claude auth path *before* starting a live session. On an
  interactive terminal with no auth, the CLI walks you through it —
  subscription login re-check, or paste an API key (input hidden, optional
  save to `./.env` with mode 600). Non-interactive contexts get a clear
  refusal (exit 2) with guidance instead of an opaque SDK failure mid-run.
- The CLI loads `<cwd>/.env` at startup (stdlib parser; never overrides
  real environment variables) — same convention as the web UI.
- `docs/validate-bundle-walkthrough.md`: a line-by-line execution trace
  of `autocodabench validate-bundle` for newcomers, with debugger
  breakpoints per stage.
- `docs/verification-catalog.md`: a complete inventory of all verification,
  in four layers — the 17 registered bundle checks (with the six lint
  condition families inside the structural gate), the dynamic execution
  stages, all 54 unit tests with what each establishes, and the
  system-level evidence (CI matrix, 12-defect seeded instrument, blinded
  harness).
- `docs/design-rationale.md`: derives the architecture from first principles —
  starting from a single-file if/else validator and introducing each layer
  (`core/`, `runner/`, `checks/`, `mcp/`, `backends/`, `agent/`) as the
  resolution of a concrete failure, with the contested decisions (plan/build
  split, keyless test split, validating imported bundles) argued explicitly.
- **Multi-backbone support**: `OpenAICompatBackend` — a stdlib
  tool-calling loop over any OpenAI-compatible chat-completions
  endpoint (Ollama local models, OpenAI, vLLM, LiteLLM proxies), with
  an in-process tool registry exposing the same `autocodabench_*`
  tool surface and writing the same `tool_calls/` audit trail as the
  MCP layer. `--backend claude[:model] | ollama:<model> |
  openai:<model> | <url>#<model>` on `create` and `validate-bundle --judged`;
  `resolve_backend()` in the library.
- **Backbone benchmark** (`experiments/backbone_bench/`): axis A
  (validator/judge quality — the E3 seeded-defect instrument,
  12 defect types, per-backbone catch rate + clean-bundle
  false-positive rate; deterministic baseline 9/9) and axis B
  (bundle-creation quality over the ground-truth competitions —
  protocol fixed, runs per backbone).
- `docs/scientific-validation.md` §6: explicit review-gauntlet mapping
  (F4 wrapper objection, F5 solver-grading, F6 overselling,
  F7 key/service walls + non-determinism).

## [0.2.0.dev0] — 2026-06-12

The repository restructured from an MCP-server-in-a-repo
(`auto_codabench/`) into the pip-installable **autocodabench** library
(`src/autocodabench/`).

### Added
- **Check framework** (`autocodabench.checks`): registered validation
  checks in three tiers — deterministic (gates), LLM-judged (advisory
  findings, never gates), attestation (human-certified) — each citing
  Pavão et al. (2024) or the Codabench schema. Declared
  `competition_facts.yaml` enables context-dependent checks
  (100/E test-set sizing, external-data rule, prize legality).
- **`codabench-validate` CLI** — validate any bundle directory or zip,
  hand-written or generated; `--judged` adds LLM-graded checks;
  `--json` for machine-readable reports.
- **Agent backends** (`autocodabench.backends`): the `AgentBackend`
  seam with two implementations — `ClaudeAgentBackend` (live, Claude
  Agent SDK; subscription login or `ANTHROPIC_API_KEY`) and
  `ReplayBackend` (keyless, deterministic re-execution of a recorded
  run's tool calls).
- **`autocodabench demo`** — offline end-to-end demo: replays a shipped
  recorded run into a real bundle, validates, zips. No keys, no network.
- **`autocodabench create`** — the plan→build pipeline as a CLI/library
  call: two isolated agent sessions joined by a locked
  `implementation_plan.md`, full `tool_calls/` audit trail per run.
- **`autocodabench auth status [--probe]`** — reports the active Claude
  auth path; warns when an exported `ANTHROPIC_API_KEY` shadows a
  subscription login.
- Unit test suite (keyless, sub-second) + GitHub Actions CI
  (3.10–3.13, Linux + macOS, including the offline demo and a
  wheel-content check).

### Changed
- Import path: `auto_codabench.mcp_server.*` → `autocodabench.{core,runner,mcp}.*`;
  MCP server entry point is `python -m autocodabench.mcp.server`.
- Artifact roots no longer live inside the package tree: runs and
  bundles default to `<cwd>/.autocodabench/` (override with
  `AUTOCODABENCH_HOME` / `AUTOCODABENCH_BUNDLES_ROOT` /
  `AUTOCODABENCH_RUNS_ROOT`).
- The Codabench upload helper moved into the package
  (`python -m autocodabench.upload.codabench_api`); the web UI and MCP
  tool share one `upload_zip()` implementation.
- The web UI consumes the installed package (skills, config, upload)
  instead of repo-relative paths.

### Removed
- `auto_codabench/` (superseded by `src/autocodabench/`),
  `test_pdf_folder/`, vendored predecessor-project tutorials under
  `documentation/`, and accumulated run artifacts (old web sessions,
  experiment runs).
