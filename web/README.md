# AutoCodabench Web Interface: Local Development and Hugging Face Spaces Deployment

This document describes the Chainlit-based chat interface that exposes the
two-phase AutoCodabench workflow (Plan, then Competition Creation), and the
procedure for deploying it as a Hugging Face Space. It is intended for
maintainers; end-user documentation is provided in
[`../docs/INSTRUCTION_FOR_USER.md`](../docs/INSTRUCTION_FOR_USER.md), Section 6.

```
┌────────────────────────────────────────────────────────────────────┐
│ Browser  ─►  Chainlit app  ─►  ClaudeSDKClient ─► api.anthropic.com │
│                          │                                          │
│                          ├─► spawns: autocodabench MCP server       │
│                          │           alex-mcp MCP server            │
│                          │                                          │
│                          ├─► writes: .autocodabench/runs/web_…/     │
│                          │           transcript.md / events.jsonl   │
│                          │           specs/implementation_plan.md   │
│                          │           bundles/<slug>/<slug>.zip      │
│                          │                                          │
│                          └─► serves: /public/sessions/<sid>/...     │
│                                      (manifest.json + plan/         │
│                                       transcript/cost HTML +        │
│                                       bundle.zip + workspace.zip)   │
│                                                                     │
│ Browser  ─►  POST /ac/upload-codabench (workspace publish form)     │
│              direct HTTP — no LLM involved                          │
└────────────────────────────────────────────────────────────────────┘
```

---

## 0. Prerequisites

- An **Anthropic API key**, which is distinct from a Claude Max or Claude Pro
  subscription (see https://console.anthropic.com, under API Keys). A balance
  of approximately $20 is sufficient to cover trial usage. The web interface
  is restricted to API-key authentication: a hosted multi-user surface must
  not route requests through an individual subscription, as required by the
  Anthropic terms of service (see `docs/INSTRUCTION_FOR_USER.md`, Section 2).
  For local single-user smoke tests, an individual subscription login is also
  acceptable.
- A **Hugging Face account** (free tier is sufficient).
- A Python environment, version 3.10 or later, with the package and web
  dependencies installed. From the repository root:
  ```bash
  pip install -e .
  pip install "git+https://github.com/drAbreu/alex-mcp.git@v4.8.2"
  pip install -r web/requirements.txt
  ```

---

## 1. Running locally (smoke test)

Create a `.env` file at the **repository root**; Chainlit loads it
automatically:

```bash
ANTHROPIC_API_KEY=sk-ant-…
SHARED_PASSWORD=…                  # any 16-character random string
CHAINLIT_AUTH_SECRET=…             # run `chainlit create-secret` once
OPENALEX_MAILTO=you@example.com
AUTOCODABENCH_DEFAULT_MODEL=claude-sonnet-4-6
MAX_USD_PER_SESSION=5.0

# Optional — fallback for CLI uploads via autocodabench_upload_bundle.
# The web UI's workspace form takes credentials from the user directly
# and does NOT need these to be set.
CODABENCH_USERNAME=ihsanchalearn
CODABENCH_PASSWORD=…
```

Launch from inside `web/`, so that Chainlit finds `chainlit.md` and
`.chainlit/config.toml`; the repository-root `.env` is still loaded
automatically:

```bash
cd web
chainlit run app.py --host 127.0.0.1 --port 8500 -h
```

Open http://localhost:8500 and sign in with any username together with the
`SHARED_PASSWORD`.

### Smoke-test checklist

1. Confirm that the greeting appears with the session ID, the model name, and
   `budget $5.00`.
2. Confirm that the phase pill bar at the top shows the Plan pill (item 1,
   active, rendered white) and the Competition Creation pill (item 2, grayed
   out and marked locked).
3. Send the message "design a competition on detecting AI-generated text".
   Claude opens a run directory (visible under
   `.autocodabench/runs/web_*_<sid>/`) and begins asking one to two scoping
   questions.
4. After the agent saves `specs/implementation_plan.md`, confirm that the
   workspace panel on the right shows the rendered plan and that the Phase 2
   pill turns blue with an advance arrow.
5. Click the advance arrow and confirm the transition. A fresh agent starts
   Phase 2 and writes the bundle (tool chips such as `init_bundle` and
   `write_competition_yaml` appear). When the phase completes, the workspace
   footer shows the competition bundle (`.zip`) as a working download link
   alongside `workspace.zip`.
6. Confirm that the per-turn footer shows
   `turn ≈ $X · session $Y / $5.00 · ctx Z% (N tok)`.
7. Click the locked Plan pill and confirm the transition. The bundle is
   deleted and the Phase 1 chat resumes for revisions.
8. Optionally, expand the **Publish to Codabench** form in the workspace
   footer, enter a username and password, and click Upload. The status shows
   "uploading...", after which the competition URL appears inline (30 to 90
   seconds).

Press Ctrl-C to stop the local server.

---

## 2. Deploying to Hugging Face Spaces

### 2.1 Create a private Space

1. Log in to https://huggingface.co.
2. Click **+ New → Space**.
3. Configure the settings as follows:
   - **Owner**: your user or organization.
   - **Space name**: for example, `autocodabench-alpha`.
   - **License**: `mit`.
   - **SDK**: **Docker** (Chainlit has no first-class template).
   - **Hardware**: `CPU basic — 2 vCPU · 16 GB RAM · free` is sufficient.
   - **Visibility**: **Private**.
4. Click **Create Space**.

### 2.2 Add repository secrets

Navigate to Settings → Variables and secrets → **New secret**. Use the
Secrets table, not the Variables table, because Variables are public:

| Secret name | Required | Value |
|-------------|----------|-------|
| `ANTHROPIC_API_KEY` | yes | `sk-ant-…` from console.anthropic.com |
| `SHARED_PASSWORD` | yes | a 16-character random string; gates the UI |
| `CHAINLIT_AUTH_SECRET` | yes | output of `chainlit create-secret` |
| `OPENALEX_MAILTO` | yes | any working email address |
| `HF_TOKEN` | optional | a token with `write` scope, used for the per-session HF Dataset upload (`autocodabench-runs`). If omitted, uploads silently become no-ops. |
| `CODABENCH_USERNAME` | optional | fallback for the CLI MCP upload tool. The web UI publish form takes credentials from the user directly. |
| `CODABENCH_PASSWORD` | optional | as above; fallback only |

Optional Variables (visible in the Space settings, not secrets):

| Variable | Default |
|----------|---------|
| `AUTOCODABENCH_DEFAULT_MODEL` | `claude-sonnet-4-6` |
| `MAX_USD_PER_SESSION` | `5.0` |

### 2.3 Push the code

The `Dockerfile` at the repository root is the source of truth for the build.
There are two push options.

**Option A — git push (recommended)**, so that future updates require only a
single `git push`:

```bash
# from repo root, on the try-web-ui branch
git remote add hf https://huggingface.co/spaces/<your-user>/autocodabench-alpha
git push hf try-web-ui:main
```

You will be prompted for an HF write token; create one at
https://huggingface.co/settings/tokens with the `write` scope.

**Option B — web upload.** Drag and drop the repository into the Space's
"Files" tab.

### 2.4 Monitor the build

The "Logs" tab streams the build output. The first build takes approximately
five minutes (pip install); subsequent builds take approximately thirty
seconds when the cache is hit. When the log reports
`Your app is available at <URL>`, open the URL and sign in.

### 2.5 Invite collaborators

Navigate to Settings → **Members → Add member** and enter the collaborators'
HF usernames; the Read role is sufficient. Collaborators open the URL and
sign in with the `SHARED_PASSWORD`.

---

## 3. Operational notes

### Phase model

Each web session has its own:
- **Run directory** at `.autocodabench/runs/web_<user>_<runtime>_<sid>/`
  (anchored at the repository root via `AUTOCODABENCH_HOME`; the operator may
  override this).
- **MCP subprocess** with the `AUTOCODABENCH_RUN_DIR` environment variable
  set to that run directory. Bundles are written to `<run>/bundles/<slug>/`.
- **Public session directory** at `web/public/sessions/<sid>/` containing
  `plan.html`, `transcript.html`, `cost.html`, `bundle.zip`,
  `workspace.zip`, and `manifest.json` together with `phase_state.json`,
  which are polled by chat.js.

On every phase transition (between Plan and Competition Creation), the SDK
client is disconnected and a fresh client is spawned with the new phase's
system prompt and tool allowlist; the chat history is dropped entirely.

### Cold start

Hugging Face Spaces puts a free-tier Space to sleep after approximately 48
hours without traffic. The first request wakes the container, which takes
approximately 30 seconds.

### Data locations

- **Inside the container**: `.autocodabench/runs/web_*` contains chat
  transcripts, the plan, the bundle, the cost log, and MCP tool snapshots.
- **HF Dataset upload**: if `HF_TOKEN` is set, the run directory is uploaded
  (subject to a text-only allowlist) to a private dataset
  (`autocodabench-runs` by default; override with
  `AUTOCODABENCH_RUNS_REPO`). This upload is the only durable record, because
  the container filesystem is ephemeral.

### Cost monitoring

- **Anthropic**: https://console.anthropic.com/usage updates every few
  minutes.
- **HF Spaces** (free tier): nothing requires monitoring.
- **Codabench**: each successful upload via the form creates a competition
  under whichever username the user entered. Track these at
  https://www.codabench.org/profiles/me/ once signed in.

### Decommissioning the alpha

1. In the Anthropic console, navigate to API keys and revoke the key (or
   unset the corresponding HF secret).
2. In the HF Space, navigate to Settings and select Delete this Space.
3. Locally, run `git branch -D try-web-ui` after merging any work you intend
   to keep.

---

## 4. Troubleshooting

### Sign-in loop locally
`SHARED_PASSWORD` is not set, or it does not match the value being entered.
The username field is informational only.

### Phase pill remains disabled after the agent reports the plan as saved
The phase bar polls `web/public/sessions/<sid>/phase_state.json` every two
seconds. If it is not updating, inspect the browser console for fetch
errors. Also confirm that `specs/implementation_plan.md` actually exists in
the run directory.

### Bundle download remains grayed out after Phase 2 finishes
Inspect the Space logs for warnings from `_find_bundle_zip`; it prefers
`<run>/bundles/` and falls back to the global `.autocodabench/bundles/` if
the environment propagation failed.

### Publish form reports "unknown error" or another unclear failure
The `/ac/upload-codabench` route returns a non-empty `error` string for
every failure path. If "unknown error" appears in the UI, expand the
`<details>` block below the error message; it includes the full server
response (HTTP status and body). Cross-reference this against the Space
logs (search for `upload-codabench` lines).

### MCP server does not boot
Inspect `web/.files/mcp_stderr_*` or the Space's main logs. The most common
cause is a `fastmcp` version mismatch (the Dockerfile pins
`fastmcp==2.14.7`).

### HF Space build fails
Read the build log. Common causes include:
- A missing required repository secret (see Section 2.2).
- A Python version mismatch; the Dockerfile uses `python:3.11-slim`.
- A `pip install` step failing on a transient network error; re-trigger the
  build (Settings → Factory rebuild).
