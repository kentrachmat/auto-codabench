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

The commands `autocodabench create` and `autocodabench validate-bundle --judged`
perform an authentication preflight before starting a session. When no
credentials are found and the command is running on an interactive
terminal, the preflight offers the same two options — signing in to the
subscription (after asking for consent, it opens Claude Code's sign-in in
place; you may decline and run `claude auth login` yourself), or entering an
API key. In non-interactive contexts, these commands exit with status 2 and
print guidance instead.

Keyless commands (`validate-bundle`, `demo`, `checks list`) do not consult
authentication state at all.

---

## 3. Validating a bundle (keyless)

The validator accepts any Codabench bundle, whether generated by
autocodabench or written by hand, supplied as a directory or a zip
archive:

```bash
autocodabench validate-bundle path/to/bundle/          # or bundle.zip
autocodabench validate-bundle bundle.zip --json        # machine-readable report
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
autocodabench validate-bundle path/to/bundle --judged
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
autocodabench create "AI-generated-text detection, balanced accuracy, \
    two phases, result submission" \
    --data ./sample_data/ \
    --verbose
```

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

## 5. Using autocodabench from an MCP host (Claude Code or Claude Desktop)

The tool surface used by the pipeline is also available as a standalone
MCP stdio server.

For Claude Code:

```bash
claude mcp add autocodabench -- python -m autocodabench.mcp.server
```

For Claude Desktop:

```json
// claude_desktop_config.json
{
  "mcpServers": {
    "autocodabench": {
      "command": "python",
      "args": ["-m", "autocodabench.mcp.server"]
    }
  }
}
```

The server exposes 20 tools covering run management and logging, bundle
authoring, validation and zipping, execution (scoring runs execute inside
the bundle's declared Docker image when Docker is available — the same way
Codabench's worker runs them — with a per-run conda environment as the
fallback), and upload. In Claude Code, the packaged skills may
additionally be symlinked into `.claude/skills/`, so that
`/autocodabench-plan` and `/autocodabench-implement` drive the same
two-phase flow conversationally; the experiment harness's `setup.sh`
shows the exact links.

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
| `create` or `--judged` fails to start a session | Run `autocodabench auth status` — it verifies the agent SDK can actually sign in and reports the failure if it cannot. If no authentication is configured, log in through Claude Code or export `ANTHROPIC_API_KEY` (or place it in `./.env`); on an interactive terminal, the preflight described in section 2 offers these options directly. |
| Usage is billed to the API instead of the subscription plan | A stale `ANTHROPIC_API_KEY` is exported; `auth status` warns about precisely this condition. Unset the variable. |
| Bundle validates locally but is rejected by Codabench | Confirm that the uploaded archive is the zip produced by `zip_bundle` or by the pipeline — `competition.yaml` must reside at the zip root, not inside a subdirectory. |
| Checks report `skipped … requires facts` | Add the named keys to `competition_facts.yaml` (section 3). |
| Artifacts appear in an unexpected location | Roots default to `<cwd>/.autocodabench/`; override with `AUTOCODABENCH_HOME` (or `AUTOCODABENCH_BUNDLES_ROOT` / `AUTOCODABENCH_RUNS_ROOT`). |
