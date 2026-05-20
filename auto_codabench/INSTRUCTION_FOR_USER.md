# AutoCodabench — User Guide

You have one sentence about a competition idea ("a contest on detecting
AI-generated text"). You want to walk away with a `.zip` you can upload to
Codabench. This guide takes you from a fresh laptop to that `.zip`, with
Claude doing all the work via two MCP servers and three skills.

---

## 1. What this actually is, in 30 seconds

Two **MCP servers** plug into Claude:

- **semantic-scholar** — lets Claude search papers, fetch citations, get
  recommendations. Used during planning to ground every metric / dataset /
  baseline suggestion in real published work.
- **autocodabench** — lets Claude write Codabench bundle files
  (`competition.yaml`, scoring program, pages, etc.), lint them, and zip
  them. Purely local — it never touches the Codabench server.

Three **skills** in `auto_codabench/skills/` tell Claude *how* to use those
servers:

- `competition-design` — distilled rules of thumb from a 315-page book on
  designing competitions.
- `codabench-bundle` — the exact schema Codabench expects.
- `autocodabench-orchestrator` — the iterative loop you'll run in Session 1.

You have **two conversations** with Claude:

| Session | What happens | What gets written |
|---------|--------------|--------------------|
| **1 — Planning** | Claude asks you questions, searches papers, makes proposals. You push back, iterate. *No bundle files are created.* | `specs/*.md` (one per module) + `implementation_plan.md` |
| **2 — Execution** | A fresh chat. Spawn subagents from the plan. They write data, scoring program, pages, assemble the bundle, validate, zip. | `auto_codabench/bundles/<slug>.zip` + run logs |

That separation is the most important rule in this whole repo. If Claude
ever starts writing bundle files during Session 1, stop it and remind it of
the iteration-1 rule.

---

## 2. What you need on your machine

- **macOS, Linux, or WSL.** Windows native should work but isn't tested.
- **Miniconda or Anaconda.** If you don't have it:
  https://docs.conda.io/projects/miniconda/en/latest/
- **Claude Desktop OR Claude Code.** Either works.
- **A free Semantic Scholar API key** (optional but recommended; without
  it you'll get hard rate limits): https://www.semanticscholar.org/product/api
- **git** to clone this repo.

---

## 3. One-time install (5–10 minutes)

```bash
# Clone the repo (skip if you already have it)
git clone <this-repo-url> auto-codabench
cd auto-codabench

# Create a dedicated conda env. The fastest way is to clone base.
conda create -n semantic-scholar --clone base -y
conda activate semantic-scholar

# Install BOTH MCP servers into the same env
pip install -e ./semantic-scholar-fastmcp-mcp-server
pip install -e .

# Create your .env (gitignored)
cp auto_codabench/.env.example .env
# Then open .env in any editor and paste your Semantic Scholar key:
#   SEMANTIC_SCHOLAR_API_KEY=...
```

Verify both packages installed by running the **in-process smoke test**:

```bash
python - <<'PY'
import asyncio
from fastmcp import Client
from auto_codabench.mcp_server.mcp import mcp
from auto_codabench.mcp_server import tools  # noqa

async def main():
    async with Client(mcp) as c:
        ts = await c.list_tools()
        print(f"OK: {len(ts)} autocodabench tools available")

asyncio.run(main())
PY
```

You should see `OK: 9 autocodabench tools available` (a one-line
`AuthlibDeprecationWarning` from fastmcp's optional auth module may
appear first — harmless, ignore it).

Also confirm the python path you'll need for Claude's config:

```bash
which python
# expected:  /Users/<you>/miniconda3/envs/semantic-scholar/bin/python
```

Copy that path — you need it in the next step.

---

## 4. Wire the MCP servers into Claude

Pick **A** or **B** based on which Claude you use.

### A. Claude Desktop

Open the config file (create it if absent):

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add or merge:

```json
{
  "mcpServers": {
    "semantic-scholar": {
      "command": "/Users/<you>/miniconda3/envs/semantic-scholar/bin/python",
      "args": ["-m", "semantic_scholar.server"],
      "env": {
        "SEMANTIC_SCHOLAR_API_KEY": "paste-your-key-here-or-leave-blank"
      }
    },
    "autocodabench": {
      "command": "/Users/<you>/miniconda3/envs/semantic-scholar/bin/python",
      "args": ["-m", "auto_codabench.mcp_server.server"],
      "env": {
        "AUTOCODABENCH_BUNDLES_ROOT": "/absolute/path/to/auto-codabench/auto_codabench/bundles"
      }
    }
  }
}
```

Two things people get wrong:

1. **Absolute paths only.** Claude Desktop does not expand `~` and does not
   read your shell's PATH.
2. **The Python from the conda env**, not system Python. That's why the
   `command` ends in `…/envs/semantic-scholar/bin/python`.

**Restart Claude Desktop.** In the bottom-right of the chat window there's
a small icon (hammer / plug) showing connected MCP servers. Click it — you
should see `semantic-scholar` and `autocodabench` both listed, with their
tools enumerated.

### B. Claude Code (in this repo)

From the repo root:

```bash
mkdir -p .claude
```

Create `.claude/settings.json`:

```json
{
  "mcpServers": {
    "semantic-scholar": {
      "command": "/Users/<you>/miniconda3/envs/semantic-scholar/bin/python",
      "args": ["-m", "semantic_scholar.server"],
      "env": {
        "SEMANTIC_SCHOLAR_API_KEY": "paste-your-key-here-or-leave-blank"
      }
    },
    "autocodabench": {
      "command": "/Users/<you>/miniconda3/envs/semantic-scholar/bin/python",
      "args": ["-m", "auto_codabench.mcp_server.server"]
    }
  }
}
```

Then run `claude` in the repo and confirm with `/mcp` that both servers are connected.

---

## 5. Install the three skills

Skills tell Claude *how* to use the MCP servers. There are three; install
all of them.

### A. Globally (recommended — works in any project)

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/auto_codabench/skills/competition-design"          ~/.claude/skills/competition-design
ln -s "$(pwd)/auto_codabench/skills/codabench-bundle"            ~/.claude/skills/codabench-bundle
ln -s "$(pwd)/auto_codabench/skills/autocodabench-orchestrator"  ~/.claude/skills/autocodabench-orchestrator
```

### B. Project-scoped (only inside this repo)

```bash
mkdir -p .claude/skills
ln -s "$(pwd)/auto_codabench/skills/competition-design"          .claude/skills/competition-design
ln -s "$(pwd)/auto_codabench/skills/codabench-bundle"            .claude/skills/codabench-bundle
ln -s "$(pwd)/auto_codabench/skills/autocodabench-orchestrator"  .claude/skills/autocodabench-orchestrator
```

Restart Claude (Desktop) or relaunch `claude` (Code). Type `/skills` —
you should see all three listed.

---

## 6. Session 1 — your first conversation (planning only)

Start a **fresh** Claude conversation. The orchestrator skill is keyed on
phrases like "design", "plan", or "build a competition", so it should
auto-activate. To be safe, type `/autocodabench-orchestrator` to invoke
it explicitly the first time.

Then write your idea in one sentence. **Just one.** Don't pre-fill the
form — let Claude pull details out of you.

### Example opening prompt

```
/autocodabench-orchestrator

I want to design a Codabench competition on detecting AI-generated text.
```

### What Claude will do

1. Restate your idea in one paragraph so you can catch misunderstandings
   immediately.
2. Search Semantic Scholar for recent baselines (RAID, M4, DAIGT, etc.)
   and tell you what it found, with paper IDs.
3. Ask you **one** decisive question. Examples it might pick:
   - "Will submissions be prediction files or runnable code?"
   - "Do you already have a dataset, or will we use a public one?"
   - "Is this single-language English-only, or multilingual?"
4. Cycle Q&A + paper searches until every dimension is locked down or
   has a citation-backed proposal you've confirmed.
5. Write seven files:
   - `auto_codabench/specs/01-task-framing.md`
   - `auto_codabench/specs/02-data.md`
   - `auto_codabench/specs/03-metrics-and-leaderboard.md`
   - `auto_codabench/specs/04-baseline-and-starting-kit.md`
   - `auto_codabench/specs/05-bundle-and-pages.md`
   - `auto_codabench/specs/06-run-logging-and-env.md`
   - `auto_codabench/implementation_plan.md`
6. **Stop.** No bundle file under `bundles/<slug>/` exists yet.

### Your job in Session 1

- Answer questions briefly and honestly. "I don't know — what do you
  recommend?" is a valid answer. Claude has the `competition-design`
  skill to fall back on.
- **Push back** when something feels wrong. "The book says X but my
  audience is undergrads — adjust." Claude will revise.
- When all seven files exist, **read the specs** in your editor (they
  live in `auto_codabench/specs/`). Anything ambiguous? Tell Claude in
  the same chat. Iterate until happy.

### Red flag

If Claude starts calling `autocodabench_write_competition_yaml` or any
`autocodabench_write_*` tool during Session 1, **stop it**:

> Stop. We're in iteration 1 — no bundle files. Plan only.

This is the orchestrator skill's hard rule. Claude may forget it under
context pressure; the reminder will reset it.

---

## 7. Session 2 — execution (a fresh conversation)

When you're happy with the specs and `implementation_plan.md`, **start
a brand-new chat**. (This is deliberate: a fresh context window keeps
the execution subagents focused.)

### Opening prompt

```
Execute auto_codabench/implementation_plan.md.

Use /agents to spawn the subagents it defines. Each subagent should
work in parallel where the plan permits, and log to
logs/<branchid>_<runtime_id>/ as specified in spec 06.

When done, write a final report from the meta-reviewer subagent
summarising what was produced, what the validate_bundle output was,
and where the final .zip lives.
```

### What happens

The plan defines roughly these subagents (the exact list is whatever
Session 1 wrote — read your plan):

| Subagent | Tools it can use | What it produces |
|----------|------------------|------------------|
| `data-curator` | filesystem + `autocodabench_attach_data` | populates `reference_data/`, `input_data/` |
| `scoring-author` | `autocodabench_write_scoring_program` | `scoring_program/score.py` + `metadata.yaml` |
| `baseline-author` | `autocodabench_write_solution` | a "barely-passes" reference solution |
| `pages-author` | `autocodabench_write_page` | overview / evaluation / terms / data pages |
| `bundle-assembler` | `autocodabench_write_competition_yaml` | the master `competition.yaml` |
| `bundle-validator` | `autocodabench_validate_bundle` | runs the linter, fixes issues, retries |
| `packager` | `autocodabench_zip_bundle` | the final `.zip` at the bundle root |
| `meta-reviewer` | read-only on logs/ + bundles/ | the final report (markdown + viz) |

Each subagent has narrow permissions — the `pages-author` cannot
overwrite the scoring program, etc.

### What you end up with

```
auto_codabench/bundles/<slug>/         ← the unpacked bundle (browse it)
auto_codabench/bundles/<slug>.zip      ← upload THIS to Codabench
logs/<branchid>_<runtime_id>/          ← stdout, stderr, structured events
auto_codabench/specs/<slug>-report.md  ← meta-reviewer's audit
```

Upload `<slug>.zip` to https://www.codabench.org → Benchmark →
"Submit a new benchmark". Codabench unpacks it server-side.

---

## 8. Troubleshooting

### "I don't see the MCP servers in Claude"

- Check the Python path in your JSON config is **absolute** and **points
  to the conda env's** `python`, not system Python.
- Check the JSON is valid (a missing comma will silently disable the
  whole `mcpServers` block).
- Open Claude Desktop's "MCP" / server-logs panel. The server's stderr
  goes there. Look for ImportError, file-not-found, port-in-use.

### "Claude says the tool failed"

Tool errors come back as `{"error": "..."}`. Common causes:

- **`init_bundle failed: bundle not initialised`** — you called a write
  tool before `init_bundle`. Tell Claude to init first.
- **`validate_bundle: missing required key 'leaderboards'`** — Claude
  wrote a partial `competition.yaml`. Have it fill the missing keys.
- **`zip_bundle: competition.yaml missing`** — same.

### "The orchestrator skill never activates"

Type `/autocodabench-orchestrator` explicitly at the start of the
conversation. Skills auto-activate on description matches but the
match isn't always confident.

### "Semantic Scholar searches return empty"

- Without an API key you're rate-limited to ~100 requests / 5 min and
  some queries time out. Set `SEMANTIC_SCHOLAR_API_KEY` in your config
  and restart Claude.
- The API occasionally has cold starts. If a search returns nothing,
  ask Claude to retry once.

### "Claude is making things up"

The orchestrator skill requires every metric / dataset / baseline
suggestion to be backed by an `<!-- ss:<paperId> -->` comment. If you
see a claim without one, push back:

> Where did you get that? Cite an S2 paperId or remove the claim.

### "My env doesn't have `fastmcp`"

You probably installed into the wrong conda env. Verify:

```bash
conda activate semantic-scholar
python -c "import fastmcp; print(fastmcp.__file__)"
```

If that fails, repeat step 3.

---

## 9. Quick reference

### Paths you'll touch

| Path | What |
|------|------|
| `~/.claude/skills/` (or `.claude/skills/`) | Skill symlinks |
| `~/Library/Application Support/Claude/claude_desktop_config.json` | Desktop MCP config |
| `.claude/settings.json` (in repo) | Claude Code MCP config |
| `.env` (repo root) | API keys, gitignored |
| `auto_codabench/specs/` | Session-1 output |
| `auto_codabench/implementation_plan.md` | Session-2 input |
| `auto_codabench/bundles/<slug>/` | Generated bundle (gitignored) |
| `auto_codabench/bundles/<slug>.zip` | What you upload |
| `logs/<branchid>_<runtime_id>/` | Run logs (gitignored) |

### The 9 autocodabench tools (so you can read Claude's tool calls)

| Tool | When it runs |
|------|--------------|
| `autocodabench_init_bundle` | First, creates the empty skeleton |
| `autocodabench_write_competition_yaml` | After all other files exist, ties them together |
| `autocodabench_write_page` | Overview / evaluation / terms / data tabs |
| `autocodabench_write_scoring_program` | `score.py` + `metadata.yaml` |
| `autocodabench_write_ingestion_program` | (Only for code-submission competitions) |
| `autocodabench_write_solution` | Baseline / starting kit |
| `autocodabench_attach_data` | Reference data, input data |
| `autocodabench_validate_bundle` | Lint pass — always run before zipping |
| `autocodabench_zip_bundle` | Produces the final upload .zip |

### Commands cheatsheet

```bash
# Activate the env
conda activate semantic-scholar

# Run the data-layer self-test (no MCP, no Claude — just verifies file I/O)
python -m auto_codabench.mcp_server.bundle_io

# Manually boot the MCP server (it will hang on stdin — Ctrl-C to exit)
python -m auto_codabench.mcp_server.server

# Manually list tools through a real MCP client
python - <<'PY'
import asyncio
from fastmcp import Client
from auto_codabench.mcp_server.mcp import mcp
from auto_codabench.mcp_server import tools  # noqa

async def main():
    async with Client(mcp) as c:
        for t in await c.list_tools():
            print(t.name)

asyncio.run(main())
PY
```

---

## 10. The shortest possible recipe

1. `conda create -n semantic-scholar --clone base -y && conda activate semantic-scholar`
2. `pip install -e ./semantic-scholar-fastmcp-mcp-server && pip install -e .`
3. Edit `claude_desktop_config.json` (paths in §4) and restart Claude.
4. Symlink the three skills into `~/.claude/skills/` (§5).
5. New Claude chat: `/autocodabench-orchestrator` + your one-sentence idea.
6. Iterate until `specs/` + `implementation_plan.md` look right.
7. **New** Claude chat: "Execute `auto_codabench/implementation_plan.md`."
8. Upload `auto_codabench/bundles/<slug>.zip` to https://www.codabench.org.

That's it. The hard part is being patient in Session 1.
