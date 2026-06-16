# autocodabench — User Guide

This guide describes how to install, authenticate, and use autocodabench
through each of its four surfaces: the command-line interface (CLI), the
Python library, an MCP host (Claude Code or Claude Desktop), and the web
user interface. Maintainer documentation is provided in
[`architecture.md`](./architecture.md).

---

## 1. Installation

```bash
pip install -e .        # from a checkout (PyPI release pending)
```

Python 3.10 or later is required. The agentic features depend on the
Claude Agent SDK, which is installed automatically. The validator and the
demo command do not use the SDK and therefore operate in any environment,
without a Node runtime, credentials, or network access.

To verify the installation:

1. Run `autocodabench --version`.
2. Run `autocodabench demo --out /tmp/acb-demo` to execute a fully offline
   end-to-end test.

### Docker (required to *run* bundles)

autocodabench executes every bundle program — scoring, ingestion, and the
starting-kit notebook — inside the competition's Docker image, exactly as the
Codabench compute worker does. **Docker must be installed and running** for the
run phases: `plan-build-validate`'s build self-validation, and any direct call to the
runner. Static `validate` and `demo` do not need it.

Both `plan-build-validate` and `validate` open with a **Docker preflight banner** that
reports the image that will run, its CPU architecture versus your host (native
vs. slow QEMU emulation), and whether the daemon is up — so the runtime is never
a surprise. On Apple silicon, prefer the multi-arch `codalab/codalab-legacy:py312`
(Docker resolves it to arm64) for fast local testing:

```bash
export AUTOCODABENCH_DOCKER_IMAGE=codalab/codalab-legacy:py312
```

See `docs/post-create-pipeline.md` for exactly what runs after `create` and how
to test each step, and `docker/README.md` for the autocodabench base images.

---

## 2. Authentication (agentic features only)

Two authentication paths are supported, listed in recommended order.

| Path | Intended use | Setup |
|---|---|---|
| **Claude subscription** (Pro or Max) | Local use; the recommended path for individual users | Install Claude Code; then run `autocodabench auth use subscription` — if no login is found it asks for consent and opens Claude Code's sign-in for you (equivalent to `claude auth login`). Subsequent autocodabench agent sessions draw from the plan's monthly Agent SDK credit. |
| **`ANTHROPIC_API_KEY`** | Automation in CI-adjacent environments, users without a subscription, and any hosted multi-user deployment, for which it is required | `export ANTHROPIC_API_KEY=sk-ant-…`, or place the key in a `.env` file (see below). |

To inspect or choose the active path:

```bash
autocodabench auth status            # report, pick, and verify (one live turn)
autocodabench auth status --no-probe # report only; no live turn (offline / CI)
autocodabench auth use subscription  # prefer the subscription, even if a key is set
autocodabench auth use api_key       # prefer the key (and paste one if none is set)
autocodabench auth use auto          # default: the key if present, else the subscription
```

Both `auth status` and `auth use <mode>` do more than report: by default they
**realize the preference and authenticate the agent SDK with it** — one
minimal live turn — and report whether the sign-in actually succeeded. Static
detection only confirms that a credential file or variable is present on disk;
the probe confirms the credential is accepted. Pass `--no-probe` to skip the
live turn (for offline or CI use).

### Choosing between a key and a subscription

The Claude Agent SDK prefers an exported `ANTHROPIC_API_KEY` over a stored
subscription login. Rather than require you to delete a key to fall back to
your plan, autocodabench stores an explicit **preference** and realizes it
for you: choosing `subscription` hides any `ANTHROPIC_API_KEY` from the SDK
for the run, so the subscription login is the one used — nothing to unset.
The preference persists at `~/.config/autocodabench/auth.json` (override
per-invocation with `AUTOCODABENCH_AUTH=auto|subscription|api_key`), and is
set with `autocodabench auth use <mode>` or the picker that `auth status`
shows on a terminal. The default, `auto`, uses a key when one is present
and the subscription otherwise.

Every command that starts a live model session prints a one-line `INFO:`
banner naming the auth in use (API key, subscription, or none) before any
tokens are spent.

`autocodabench auth status` also reports a masked preview of each configured
credential, so you can confirm *which* value is set without revealing it. The
`ANTHROPIC_API_KEY` shows its scheme prefix and last four characters; the
Codabench publishing credentials (section 7) are listed in a second block,
with the password and token masked and the username shown in full. A
credential that is absent reads `(not set)`, distinct from `(set but empty)`.

One rule still binds:

1. **Hosted multi-user deployments must use an API key.** Under
   Anthropic's terms of service, requests from other users may not be
   routed through one person's Free, Pro, or Max credentials. Running the
   web UI locally for personal use on a personal subscription is
   permitted; deploying it for other users (for example, as a Hugging Face
   Space) requires an API key.

### `.env` loading and the authentication preflight

The CLI loads `<cwd>/.env` at startup using a minimal parser that never
overrides variables already present in the real environment, so
`ANTHROPIC_API_KEY` may live in a `.env` file instead of being exported.
You do not have to edit that file by hand: `autocodabench auth use api_key`
(and the `auth status` picker) prompt for a key with hidden input and offer
to save it to `./.env` (file mode 600) for you.

The commands `autocodabench plan-build-validate` and `autocodabench validate --judged`
perform an authentication preflight before starting a session. When no
credentials are found and the command is running on an interactive
terminal, the preflight offers the same two options — signing in to the
subscription (after asking for consent, it opens Claude Code's sign-in in
place; you may decline and run `claude auth login` yourself), or entering an
API key. In non-interactive contexts, these commands exit with status 2 and
print guidance instead.

Keyless commands (`validate`, `demo`, `checks list`) do not consult
authentication state at all.

---

## 3. Validating a bundle (keyless)

The validator accepts any Codabench bundle, whether generated by
autocodabench or written by hand, supplied as a directory or a zip
archive:

```bash
autocodabench validate path/to/bundle/          # or bundle.zip
autocodabench validate bundle.zip --json        # machine-readable report
autocodabench checks list                   # every check, by tier, with citations
```

The report contains four sections with distinct meanings:

- **Gate failures** — deterministic checks that block upload, such as
  missing referenced files, unparseable YAML, or leaderboard keys that the
  scoring program never writes. These produce exit code 1.
- **Findings (advisory)** — design risks with citations into Pavão et al.
  (2024), *AI Competitions and Benchmarks*; examples include the absence
  of a daily submission cap, a development phase shorter than 40 days, or
  a test set too small to satisfy the 100/E rule.
- **Attestations required** — launch criteria that only a human can
  certify, such as completion of external review, publication of a
  datasheet, or prize legality. The validator surfaces these criteria; it
  does not claim to have verified them.
- **Skipped** — checks that require a declared fact that has not been
  provided.

### Declared facts (`competition_facts.yaml`)

Some checks require context that the bundle cannot carry. Declare this
context in a file placed next to `competition.yaml`, or supply it with
`--facts path.yaml`:

```yaml
anticipated_error_rate: 0.05      # enables the 100/E test-set sizing check
test_set_size: 2400               # overrides counting reference_data rows
unit_of_generalization: patient   # what a data split must not straddle
external_data_allowed: false
prizes: false                     # resolves the game-of-skill attestation
task_type: binary_classification_imbalanced
```

### LLM-judged checks (authentication required)

```bash
autocodabench validate path/to/bundle --judged
```

This option adds advisory checks graded by an LLM — for example, whether
the participant-facing pages contradict the machine-readable
configuration (the pages state five submissions per day while the YAML
enforces ten). Judged results are findings, never gates: an LLM's
assessment never blocks an upload, and an unparseable judge reply degrades
to a skipped result, never to a silent pass.

---

## 4. Creating a competition agentically (authentication required)

```bash
autocodabench plan-build-validate "AI-generated-text detection, balanced accuracy, \
    two phases, result submission" \
    --data ./sample_data/      # `create` remains as a shorter alias
```

Before any tokens are spent, `plan-build-validate` prints its full effective configuration
— backend and auth path, model, the exact output directory, sample data, cost
cap, output mode, and the three pipeline stages — then (on a terminal) asks
where the output should go and confirms before starting.

**Phase-1 research.** The banner also shows which external knowledge sources
the planner may consult so the design is grounded in what already exists rather
than the model's training data alone:
- **OpenAlex** — recent related competition / benchmark papers (topic search,
  related works, top-AI-conference venue preset), via the external
  `openalex-research-mcp` server launched with `npx` (install
  [Node/npx](https://nodejs.org/); overridable with
  `AUTOCODABENCH_OPENALEX_MCP_CMD`). Keyless; OpenAlex appreciates a courtesy
  email (`OPENALEX_EMAIL`).
- **Kaggle** — how similar competitions are hosted (metric, submission caps,
  team-size limits, deadlines, full rules pages), via first-party tools that
  wrap the Kaggle SDK (the `kaggle` package ships in the base install). Reads
  **public** competitions only and needs no key from you — a shared throw-away
  token is used unless you set `KAGGLE_API_TOKEN` (or have `~/.kaggle/`), with
  your own token from <https://www.kaggle.com/settings/api>.
- **Web search** — a last resort (single-source, easily biased); the planner is
  instructed to prefer OpenAlex and Kaggle for related-work discovery.

All on by default; turn them off with `--no-research` (all) or `--no-openalex` /
`--no-kaggle` / `--no-web-search` (individually). Research is a **Claude-only**
capability — OpenAI-compatible / Ollama backbones cannot host the external MCP
server or web tools, and the banner says so. A missing launcher/package marks
that source unavailable and the plan proceeds without it. Phase 1 ends with a
**provenance table** (✓ specified by your input · ⚠ partially · ✗ inferred by the
planner) so you can see at a glance which decisions warrant your review.

The run reports progress at one of three levels of detail:

- **Default** — a concise, user-oriented narrative: a header per phase and the
  plain-language milestone messages the agent emits, including any *deviation*
  from the plan stated in non-technical terms (for example, that the plan named
  a scikit-learn argument removed in a recent release, that it was corrected,
  and the resulting metric). Raw tool calls, raw tool output, and the agent's
  internal reasoning are not shown.
- **`--debug`** — the full developer trace: every tool call with its arguments,
  tool errors, and the agent's reasoning. A notice before the run explains that
  this mode is intended for diagnosing the pipeline rather than routine use.
- **`--quiet`** — only the final summary.

If the build phase departs from the locked plan in any way, it writes
`specs/updated_implementation_plan.md`, which opens with a *Changes from the
original plan* section enumerating each change (original specification → what
changed → why); the original `implementation_plan.md` is preserved unchanged as
the provenance record. Absence of the updated file means the bundle was built
exactly as planned.

Other useful flags: `--out DIR` (set the output location non-interactively),
`--yes` (skip the confirmation), `--model` (override the model), and
`--max-budget-usd` (a cost cap per phase).

The pipeline proceeds as follows:

1. **Plan session.** An agent drafts the full competition design — task
   framing, data, metric, baselines, phases, and rules — and saves a
   locked `implementation_plan.md`. Headless runs make conservative
   assumptions and state each one explicitly in the plan.
2. Optionally, stop at this point, edit the plan by hand, and re-run the
   build.
3. **Build session.** A fresh agent reads only the plan and writes the
   bundle through the MCP tool surface: `competition.yaml`, pages, the
   scoring program, a baseline solution, and data; it then validates and
   zips the result.
4. The check framework described in section 3 runs over the resulting
   bundle.

All artifacts are written to a per-run directory under
`./.autocodabench/runs/`: the plan, the bundle and its zip archive, a
complete `tool_calls/` audit trail of every authoring action, and message
traces for both sessions. Relevant flags include `--model` and
`--max-budget-usd` (a cost cap per phase).

The same functionality is available from Python:

```python
import autocodabench

result = autocodabench.create("plankton image classification, two phases")
print(result.bundle_dir, result.zip_path, result.total_cost_usd)
print(result.validation.to_markdown())

report = autocodabench.validate("path/to/any_bundle.zip")
print(report.ok, report.counts)
```

---

## 5. Choosing an LLM backend

`autocodabench plan-build-validate` (and the benchmarks under `benchmark/`) drive the model
**hermetically through the backend seam** (`autocodabench.backends`): the
autocodabench MCP tool surface is registered *programmatically* for each agent
session, so there is **no `claude mcp add` and no `.mcp.json` to maintain** —
install the package and it works, from any directory.

Select a backend with `--backend` (and optionally `--model`):

| spec | backbone | credentials |
|------|----------|-------------|
| `claude[:model]` (default) | Claude Agent SDK | subscription login or `ANTHROPIC_API_KEY` |
| `ollama:<model>` | local Ollama (offline) | none |
| `openai:<model>` | OpenAI or a proxy (`OPENAI_BASE_URL`) | `OPENAI_API_KEY` |
| `<http(s)://host/v1>#<model>` | any OpenAI-compatible endpoint (vLLM, LiteLLM, …) | `AUTOCODABENCH_LLM_API_KEY` / `OPENAI_API_KEY` |

The generic (OpenAI-compatible) backends require native tool calling and get
the **same 20-tool surface and the same `tool_calls/` audit trail** as the SDK
path (`autocodabench.backends.local_tools`) — that parity is what makes
cross-backbone benchmarking commensurable.

The same tool surface is still available as a standalone MCP stdio server
(`python -m autocodabench.mcp.server`) for embedding in a custom MCP host, but
it is **not** required for `plan-build-validate`, `validate`, or the benchmarks.

---

## 6. Web user interface

The web UI is a Chainlit chat surface over the same plan-then-build flow,
with a phase bar, cost tracking, and a Publish form.

```bash
pip install -r web/requirements.txt
cd web && chainlit run app.py --host 127.0.0.1 --port 8500 -h
```

A `.env` file at the repository root is required (see `.env.example`)
with the following variables: `ANTHROPIC_API_KEY` (the web UI is
API-key-only; see section 2), `SHARED_PASSWORD`, `CHAINLIT_AUTH_SECRET`,
and `OPENALEX_MAILTO`. Operating and deploying the Space is documented in
[`../web/README.md`](../web/README.md).

---

## 7. Publishing to Codabench

Three equivalent routes are available; all are optional and all require
an explicit action:

- **Web UI Publish form.** Enter a Codabench username and password into
  the workspace panel; the upload is sent directly to codabench.org and
  never passes through the LLM.
- **MCP tool** `autocodabench_upload_bundle`. This tool reads
  `CODABENCH_USERNAME` and `CODABENCH_PASSWORD` (or `CODABENCH_TOKEN`)
  from the environment.
- **Script.** Run `python -m autocodabench.upload.codabench_api bundle.zip`.

The four-step REST flow (token, dataset placeholder, signed PUT, unpack
poll) is documented in
[`codabench-upload-api.md`](./codabench-upload-api.md).

---

## 8. Troubleshooting

| Symptom | Likely cause and resolution |
|---|---|
| `plan-build-validate` or `--judged` fails to start a session | Run `autocodabench auth status` — it verifies the agent SDK can actually sign in and reports the failure if it cannot. If no authentication is configured, log in through Claude Code or export `ANTHROPIC_API_KEY` (or place it in `./.env`); on an interactive terminal, the preflight described in section 2 offers these options directly. |
| Usage is billed to the API instead of the subscription plan | A stale `ANTHROPIC_API_KEY` is exported; `auth status` warns about precisely this condition. Unset the variable. |
| Bundle validates locally but is rejected by Codabench | Confirm that the uploaded archive is the zip produced by `zip_bundle` or by the pipeline — `competition.yaml` must reside at the zip root, not inside a subdirectory. |
| Checks report `skipped … requires facts` | Add the named keys to `competition_facts.yaml` (section 3). |
| Artifacts appear in an unexpected location | Roots default to `<cwd>/.autocodabench/`; override with `AUTOCODABENCH_HOME` (or `AUTOCODABENCH_BUNDLES_ROOT` / `AUTOCODABENCH_RUNS_ROOT`). |
