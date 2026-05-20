# AutoCodabench

An MCP-server-driven workflow for turning a vague competition idea into a
ready-to-upload Codabench bundle — without ever touching the Codabench server
during design / authoring.

```
your idea (one sentence)
        │
        ▼
┌────────────────────────────────────────────────────────────┐
│ Session 1 — Planning (this skill loop)                    │
│                                                            │
│  Claude (host) ──► semantic-scholar MCP   (cite papers)    │
│         │      ──► competition-design SKILL (book wisdom)  │
│         │      ──► YOU         (adaptive Q&A)               │
│         ▼                                                  │
│   specs/*.md + implementation_plan.md                       │
│   (NO bundle files yet — review and push back)              │
└────────────────────────────────────────────────────────────┘
        │
        ▼  (you review, say "go" in a fresh session)
┌────────────────────────────────────────────────────────────┐
│ Session 2 — Execution                                     │
│                                                            │
│   /agents subagents ──► autocodabench MCP                   │
│   (data-curator, scoring-author, baseline-author,           │
│    pages-author, bundle-assembler, validator, packager,     │
│    meta-reviewer)                                           │
│         ▼                                                  │
│   auto_codabench/bundles/<slug>/                            │
│   auto_codabench/bundles/<slug>.zip   ← upload to Codabench │
│   logs/<branchid>_<runtime_id>/                            │
└────────────────────────────────────────────────────────────┘
```

The autocodabench MCP server **never talks to Codabench**. It only writes
files to your local disk and lints them. Uploading is a separate, future
concern (see `scripts/upload_bundle.py` in the parent repo's git history
for the API contract).

---

## Components

| Path | What it is |
|------|------------|
| `mcp_server/` | FastMCP 2.x server. Tools: `autocodabench_init_bundle`, `autocodabench_write_competition_yaml`, `autocodabench_write_page`, `autocodabench_write_scoring_program`, `autocodabench_write_ingestion_program`, `autocodabench_write_solution`, `autocodabench_attach_data`, `autocodabench_validate_bundle`, `autocodabench_zip_bundle`. |
| `mcp_server/bundle_io.py` | Pure data layer — runnable standalone (`python -m auto_codabench.mcp_server.bundle_io` runs the self-test that creates, validates, and zips a tiny demo bundle). |
| `skills/competition-design/SKILL.md` | Best practices distilled from the Pavao et al. AI-competitions book — task framing, metrics, splits, leaderboards, anti-cheating. |
| `skills/codabench-bundle/SKILL.md` | Technical schema reference for `competition.yaml`, pages, phases, scoring/ingestion programs, leaderboard ↔ `scores.json` mapping, zip layout. |
| `skills/orchestrator/SKILL.md` | The iterative-loop skill: drives Q&A + Semantic Scholar searches → `specs/*.md` + `implementation_plan.md`. **Iteration 1 must NOT touch bundle files.** |
| `bundles/<slug>/` | Generated bundle directories live here (gitignored). |
| `specs/` | Per-module specs produced during iteration 1. |

---

## Install

You will share the conda env with the semantic-scholar-fastmcp-mcp-server,
per the project decision. First time:

```bash
# from repo root
conda create -n semantic-scholar --clone base -y   # or: conda create -n semantic-scholar python=3.11 -y
conda activate semantic-scholar

# install both packages into the same env
pip install -e ./semantic-scholar-fastmcp-mcp-server
pip install -e .                                   # installs autocodabench from repo root pyproject.toml
```

Sanity-check the data layer (creates a tiny demo bundle in a tempdir):

```bash
python -m auto_codabench.mcp_server.bundle_io
# expect: { "ok": true, "issues": [] ... } then a zip_path line
```

Sanity-check the MCP server boots (it will hang waiting for stdin — that's
correct; press Ctrl-C):

```bash
python -m auto_codabench.mcp_server.server
```

---

## Wire it into Claude Desktop / Claude Code

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) and add **both** servers:

```json
{
  "mcpServers": {
    "semantic-scholar": {
      "command": "/Users/ktgiahieu/miniconda3/envs/semantic-scholar/bin/python",
      "args": ["-m", "semantic_scholar.server"],
      "env": {
        "SEMANTIC_SCHOLAR_API_KEY": "${SEMANTIC_SCHOLAR_API_KEY}"
      }
    },
    "autocodabench": {
      "command": "/Users/ktgiahieu/miniconda3/envs/semantic-scholar/bin/python",
      "args": ["-m", "auto_codabench.mcp_server.server"],
      "env": {
        "AUTOCODABENCH_BUNDLES_ROOT": "/Users/ktgiahieu/Documents/auto-codabench/auto_codabench/bundles"
      }
    }
  }
}
```

Restart Claude Desktop. You should see two MCP-tool indicators with
`paper_relevance_search` / `autocodabench_init_bundle` listed.

For Claude Code: the same JSON shape works in your project's
`.claude/settings.json` under `"mcpServers"`.

---

## Skills

The three skill files in `skills/*/SKILL.md` are designed to be loaded by
Claude as user-invocable skills. To install them globally:

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/auto_codabench/skills/competition-design"   ~/.claude/skills/competition-design
ln -s "$(pwd)/auto_codabench/skills/codabench-bundle"     ~/.claude/skills/codabench-bundle
ln -s "$(pwd)/auto_codabench/skills/autocodabench-orchestrator"   ~/.claude/skills/autocodabench-orchestrator
```

(For Claude Code project-scoped skills, drop them under `.claude/skills/`.)

---

## .env

`.env` lives at the repo root (already gitignored). Minimum keys:

```
SEMANTIC_SCHOLAR_API_KEY=...
```

Add whatever your competition needs (e.g. dataset-download tokens, an
OpenAI key for an evaluation model). Spec 06 (`specs/06-run-logging-and-env.md`)
must enumerate every key the implementation phase reads.

---

## The two-session workflow

**Session 1 — planning (this is what the orchestrator skill drives):**

You say: *"I want a competition on detecting AI-generated text."*

Claude (with the orchestrator skill active):
1. Restates the task in one paragraph.
2. Searches Semantic Scholar for recent baselines (RAID, M4, …).
3. Asks one decisive question.
4. Cycles Q&A + searches until every dimension in §3 of the orchestrator skill
   has a confirmed answer or a citation-backed proposal.
5. Writes `specs/01-*.md` … `specs/06-*.md` + `implementation_plan.md`.
6. **Stops.** No bundle files exist on disk yet.

You read the specs, push back, iterate. When you say "go":

**Session 2 — execution (fresh session, run `/agents` to spawn subagents):**

The `implementation_plan.md` defines subagents — each with narrow
permissions and a focused task. They each call the appropriate
`autocodabench_*` tools, log to `logs/<branchid>_<runtime_id>/`, and the
final `meta-reviewer` subagent audits everything and writes a report.

The final artifact is `auto_codabench/bundles/<slug>.zip`. Upload that to
Codabench through the UI (or — future work — a small `upload_bundle` MCP
tool).

---

## Explicitly out of scope (v1)

- No Codabench API client. No auth. No upload tool.
- No compute-worker setup, no Docker image building, no queue config.
- No verification that the bundle actually runs on Codabench infrastructure.
- No live smoke-test (running the scoring program against the baseline
  solution as a subprocess) — `validate_bundle` is the strongest local
  guarantee. A `smoke_test_bundle` tool may be added later.
