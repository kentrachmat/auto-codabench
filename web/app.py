"""AutoCodabench web UI — Chainlit + Claude Agent SDK.

A thin chat surface over the same orchestrator workflow Claude Code runs
locally. Designed for a small private-alpha deployment (3-5 collaborators,
1-2 weeks) on Hugging Face Spaces.

Architecture (per chat session):
  1. Visitor enters SHARED_PASSWORD (Chainlit password_auth_callback).
  2. on_chat_start:
      - Mint a session UUID; mkdir an isolated run dir under
        auto_codabench/runs/web_<uuid>/.
      - Spawn a ClaudeSDKClient with:
          * model from AUTOCODABENCH_DEFAULT_MODEL
          * system_prompt = the orchestrator SKILL.md (full text)
          * skills = the codabench-bundle + competition-design skill paths
          * mcp_servers = stdio configs for autocodabench + alex-mcp,
            with AUTOCODABENCH_RUN_DIR pointing at this session's run
          * max_budget_usd = MAX_USD_PER_SESSION
      - Stream a greeting to the user.
  3. on_message:
      - Pipe the user's text into client.query(...).
      - Iterate client.receive_response() and stream text chunks to UI.
      - Each tool call / tool result is logged into the per-session run
        dir by the MCP server's @logged_tool decorator (server-side
        snapshots already exist; no UI changes needed for that).
      - When Claude returns a tool_result containing `competition_url`,
        render it as a prominent clickable link.
      - At session end, dump transcript.md (since HF has no Claude Code
        hook to do this).
  4. on_chat_end: disconnect the client cleanly.

ENV expected at runtime:
  ANTHROPIC_API_KEY            (required) — pay-per-token
  SHARED_PASSWORD              (required) — gates the UI
  OPENALEX_MAILTO              (required) — alex-mcp polite-pool
  CODABENCH_USERNAME           (required for Session 2 uploads)
  CODABENCH_PASSWORD           (required for Session 2 uploads)
  AUTOCODABENCH_DEFAULT_MODEL  (default claude-sonnet-4-6)
  MAX_USD_PER_SESSION          (default 2.0)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("autocodabench.web")

import chainlit as cl
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
# McpStdioServerConfig is a TypedDict in .types; build via dict literal below.

# Tools we don't want to show as chips in the UI — they're agent
# machinery (Claude Code's skill loader, the deferred-tool searcher,
# the file-read primitives) and only add clutter for the end user.
_HIDDEN_TOOLS = {"Skill", "ToolSearch", "Read", "Grep", "Glob"}


# Human-readable verb phrase per MCP tool. Used in the running-step label
# ("Running OpenAlex search…"). When a tool isn't in this map, we fall
# back to humanising the tool's last segment.
_OP_LABELS: dict[str, str] = {
    "mcp__alex-mcp__search_works":              "OpenAlex search",
    "mcp__alex-mcp__search_authors":            "OpenAlex author search",
    "mcp__alex-mcp__autocomplete_authors":      "OpenAlex author autocomplete",
    "mcp__alex-mcp__retrieve_author_works":     "OpenAlex retrieve works",
    "mcp__alex-mcp__search_pubmed":             "PubMed search",
    "mcp__alex-mcp__pubmed_author_sample":      "PubMed author sample",
    "mcp__alex-mcp__search_orcid_authors":      "ORCID author search",
    "mcp__alex-mcp__get_orcid_publications":    "ORCID retrieve works",
    "mcp__autocodabench__autocodabench_open_run":              "opening session",
    "mcp__autocodabench__autocodabench_current_run":           "verifying session",
    "mcp__autocodabench__autocodabench_log_event":             "logging event",
    "mcp__autocodabench__autocodabench_snapshot_spec":         "saving spec",
    "mcp__autocodabench__autocodabench_init_bundle":           "creating bundle",
    "mcp__autocodabench__autocodabench_write_competition_yaml":"writing competition.yaml",
    "mcp__autocodabench__autocodabench_write_page":            "writing page",
    "mcp__autocodabench__autocodabench_write_scoring_program": "writing scoring program",
    "mcp__autocodabench__autocodabench_write_ingestion_program":"writing ingestion program",
    "mcp__autocodabench__autocodabench_write_solution":        "writing solution",
    "mcp__autocodabench__autocodabench_attach_data":           "attaching data",
    "mcp__autocodabench__autocodabench_validate_bundle":       "validating bundle",
    "mcp__autocodabench__autocodabench_zip_bundle":            "zipping bundle",
    "mcp__autocodabench__autocodabench_upload_bundle":         "uploading to Codabench",
}


def _operation_label(tool_name: str, tool_input: dict | None) -> str:
    """Friendly verb phrase for the step chip — derived from tool name + input.

    Used in the chip's name as "Running <label>" while the call is in
    flight, and as just "<label>" after the result arrives. For search
    tools we also append a truncated query string so the user can see
    what's being looked up at a glance.
    """
    base = _OP_LABELS.get(tool_name)
    if base is None:
        # Fallback: humanize the last segment (e.g. `foo_bar_baz` -> `foo bar baz`).
        last = tool_name.split("__")[-1]
        base = last.removeprefix("autocodabench_").replace("_", " ")
    if isinstance(tool_input, dict) and "search" in tool_name.lower():
        q = tool_input.get("query") or tool_input.get("q") or ""
        q = str(q).strip()
        if q:
            return f"{base}: ‘{q[:40]}’"
    return base
from dotenv import load_dotenv

# Make sure the autocodabench package is importable even when chainlit's
# CWD differs from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Load .env from repo root only when running locally. On HF Spaces the
# variables come from Repository Secrets and load_dotenv is a no-op.
load_dotenv(REPO_ROOT / ".env")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SHARED_PASSWORD = os.environ.get("SHARED_PASSWORD", "")
DEFAULT_MODEL = os.environ.get("AUTOCODABENCH_DEFAULT_MODEL", "claude-sonnet-4-6")
# Cost cap raised to $5 default while the 3-phase split is being tested —
# the per-phase context reset means a session now spans three smaller
# context windows instead of one large one, but iteration depth (refine
# multiple sections, revise plan, etc.) can still add up.
MAX_USD_PER_SESSION = float(os.environ.get("MAX_USD_PER_SESSION", "5.0"))
PYTHON_BIN = os.environ.get("AUTOCODABENCH_PYTHON", sys.executable)
# Sonnet's context-window cap (used as the denominator for the live
# "context %" chip in the phase bar — same metric Claude Code shows).
CONTEXT_WINDOW_TOKENS = int(os.environ.get(
    "AUTOCODABENCH_CONTEXT_WINDOW", "200000"))

# Three phases, each with its own agent. The whole point of the split
# is COST: each phase starts with no conversation history and only the
# previous phase's locked artifact in scope. We disconnect the SDK
# client at every boundary and build a fresh one with that phase's
# system prompt + tool allowlist.
#
#   Phase 1 — PLAN   : produce specs/implementation_plan.md.
#   Phase 2 — KIT    : read the plan, write starting_kit.ipynb.
#   Phase 3 — BUNDLE : read the notebook, package the Codabench bundle.
#
# The user moves between phases by clicking the phase bar at the top
# of the page. Forward advances are gated on the previous phase's
# artifact existing; back-navigation discards downstream artifacts so
# the next forward advance regenerates them from the (possibly edited)
# previous artifact.
PHASE_PLAN   = "plan"
PHASE_KIT    = "kit"
PHASE_BUNDLE = "bundle"
PHASE_ORDER  = [PHASE_PLAN, PHASE_KIT, PHASE_BUNDLE]
PHASE_TITLE  = {
    PHASE_PLAN:   "📝 Plan",
    PHASE_KIT:    "📓 Starting Kit",
    PHASE_BUNDLE: "📦 Bundle",
}
PHASE_ARTIFACT = {
    PHASE_PLAN:   "specs/implementation_plan.md",
    PHASE_KIT:    "starting_kit.ipynb",
    PHASE_BUNDLE: "bundle.zip",
}
# Legacy aliases for the old two-phase code. The PR removes most of the
# direct references, but a few call sites still pass these names; keep
# the constants pointing at the right new phase so the old code still
# routes correctly while we finish the migration.
PHASE_PLANNING       = PHASE_KIT
PHASE_IMPLEMENTATION = PHASE_BUNDLE

# 8-stage notebook flow (see notebook_kernel.STAGES). Each stage gets a
# row in the cl.TaskList that lives at the top of the chat. Stages
# 1-7 happen during planning (proposal-style work happens here as
# notebook cells); stage 8 packages the executed notebook into a
# Codabench bundle. Stage 0 is design-only (no cells, no kernel use).
STAGE_TITLES: list[tuple[str, str]] = [
    # The original 7-row competition-design checklist. Each row maps to
    # one section of the starting_kit.ipynb. Version 1 of the notebook
    # contains ALL of these (generated in one pass with demo code); the
    # user then iterates on any section.
    ("0.roadmap",      "📐 0. Roadmap"),
    ("1.task",         "🎯 1. Task formulation"),
    ("2.data",         "📊 2. Data & splits"),
    ("3.metric",       "📏 3. Metric"),
    ("4.baseline_kit", "🤖 4. Baseline & starting kit"),
    ("5.rules",        "📋 5. Rules"),
    ("6.ethics",       "⚖️ 6. Ethics & dual-use"),
    ("7.schedule",     "📅 7. Schedule & sustainability"),
    ("8.bundle",       "🛠 8. Bundle"),
]

# Per-phase tool allowlists. Each phase is given the minimum set it
# needs — narrower allowlists mean fewer tool definitions in the
# system prompt, which cuts per-turn input tokens.
_PLAN_TOOLS = [
    # Phase 1 — prose plan only. No notebook, no bundle.
    "mcp__autocodabench__autocodabench_open_run",
    "mcp__autocodabench__autocodabench_current_run",
    "mcp__autocodabench__autocodabench_log_event",
    "mcp__autocodabench__autocodabench_snapshot_spec",
    "mcp__alex-mcp__*",
    "Read", "Grep", "Glob",
]
_KIT_TOOLS = [
    # Phase 2 — notebook authoring. snapshot_spec deliberately omitted
    # so the agent can't accidentally clobber implementation_plan.md
    # (the Phase 1 artifact is "locked" at this phase boundary).
    "mcp__autocodabench__autocodabench_open_run",
    "mcp__autocodabench__autocodabench_current_run",
    "mcp__autocodabench__autocodabench_log_event",
    "mcp__autocodabench__autocodabench_nb_init",
    "mcp__autocodabench__autocodabench_nb_write_cell",
    "mcp__autocodabench__autocodabench_nb_run_stage",
    "mcp__autocodabench__autocodabench_nb_reset_to_stage",
    "mcp__autocodabench__autocodabench_nb_render_html",
    "mcp__autocodabench__autocodabench_nb_shutdown",
    "mcp__alex-mcp__*",
    "Read", "Grep", "Glob",
]
_BUNDLE_TOOLS = [
    # Phase 3 — full bundle-write + optional upload. The notebook
    # tools are still allowed in case stage 8 needs to re-render
    # the notebook from disk; snapshot_spec is allowed for writing
    # competition pages.
    "mcp__autocodabench__*",
    "mcp__alex-mcp__*",
    "Read", "Grep", "Glob",
]
_TOOLS_BY_PHASE = {
    PHASE_PLAN:   _PLAN_TOOLS,
    PHASE_KIT:    _KIT_TOOLS,
    PHASE_BUNDLE: _BUNDLE_TOOLS,
}
# Legacy aliases kept for any caller still using the old names.
_PLANNING_TOOLS       = _KIT_TOOLS
_IMPLEMENTATION_TOOLS = _BUNDLE_TOOLS

# Per-session run dirs are uploaded to this private HF Dataset repo
# (cost.jsonl, transcript.md, tool_calls/, specs/, events.jsonl, …).
# Set HF_TOKEN as a Repository Secret on the Space to enable uploads;
# when missing (local dev), uploads are silently skipped.
HF_RUNS_REPO = os.environ.get("AUTOCODABENCH_RUNS_REPO", "ktgiahieu/autocodabench-runs")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

SKILLS_ROOT = REPO_ROOT / "auto_codabench" / "skills"


def _resolve_skill(*candidates: str) -> Path:
    """Return the first existing SKILL.md among the candidate dir names."""
    for name in candidates:
        p = SKILLS_ROOT / name / "SKILL.md"
        if p.exists():
            return p
    # Even if none exists yet, return the first candidate so the caller
    # gets a reasonable path for log messages.
    return SKILLS_ROOT / candidates[0] / "SKILL.md"


PLAN_SKILL         = _resolve_skill("autocodabench-plan", "plan")
ORCHESTRATOR_SKILL = _resolve_skill("autocodabench-orchestrator", "orchestrator")
IMPLEMENT_SKILL    = _resolve_skill("autocodabench-implement")
_SKILL_BY_PHASE = {
    PHASE_PLAN:   PLAN_SKILL,
    PHASE_KIT:    ORCHESTRATOR_SKILL,
    PHASE_BUNDLE: IMPLEMENT_SKILL,
}

RUNS_ROOT = REPO_ROOT / "auto_codabench" / "runs"


def _read_skill(path: Path) -> str:
    """Return a skill's body without its YAML frontmatter."""
    if not path.exists():
        return ""
    body = path.read_text(encoding="utf-8")
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:].lstrip()
    return body


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _system_prompt(*, phase: str = PHASE_PLAN) -> str:
    """Return the per-phase system prompt.

    Each phase loads its own skill body — never both at once. A short
    web-UI footer is appended so the model knows phase transitions are
    button-driven (no "open a new chat" in either direction), and that
    the user — not the agent — owns the click.
    """
    skill_path = _SKILL_BY_PHASE.get(phase, PLAN_SKILL)
    base = _read_skill(skill_path) or (
        f"(skill body missing at {skill_path} — contact the operator.)"
    )

    if phase == PHASE_PLAN:
        footer = (
            "\n\n---\n\n"
            "## Web UI runtime note (Phase 1 — Plan)\n\n"
            "You are running in the AutoCodabench web UI, Phase 1. The user "
            "advances between phases by clicking pills in the **phase bar "
            "at the top of the page** — you cannot trigger the advance "
            "yourself.\n\n"
            "When `implementation_plan.md` is saved and you'd recommend "
            "moving on, say something like:\n\n"
            "> ✅ Plan saved. When you're ready, click **▶ Advance to "
            "> Phase 2 — Starting Kit** in the phase bar at the top.\n\n"
            "Phase 2 starts with NO memory of this conversation — only the "
            "plan file. If anything important from our chat is missing from "
            "the plan, tell the user so we can revise before advancing."
        )
    elif phase == PHASE_KIT:
        footer = (
            "\n\n---\n\n"
            "## Web UI runtime note (Phase 2 — Starting Kit)\n\n"
            "You are running in Phase 2. The plan lives at "
            "`<run>/specs/implementation_plan.md` — read it first. You did "
            "NOT participate in the Phase 1 conversation; the plan is your "
            "single source of truth for the design.\n\n"
            "Start now: `autocodabench_current_run` → "
            "`Read('<run>/specs/implementation_plan.md')` → "
            "`autocodabench_nb_init()` → write all 7 sections. Don't wait "
            "for additional instructions.\n\n"
            "When the notebook is good, suggest the user click "
            "**▶ Advance to Phase 3 — Bundle** in the phase bar at the top."
        )
    else:  # PHASE_BUNDLE
        footer = (
            "\n\n---\n\n"
            "## Web UI runtime note (Phase 3 — Bundle)\n\n"
            "You are running in Phase 3. The user reached this phase by "
            "clicking **▶ Advance to Phase 3** in the phase bar; the kit "
            "notebook at `<run>/starting_kit.ipynb` is locked. Execute "
            "the autocodabench-implement skill serially in this chat — "
            "`/agents` is not available here.\n\n"
            "Start now: call `autocodabench_current_run`, read the "
            "notebook + events.jsonl, then follow the autocodabench-implement "
            "skill end-to-end. Don't wait for additional instructions."
        )
    return base + footer


def _probe_mcp_imports() -> list[str]:
    """Run `python -c "import <module>"` for each MCP server we plan to spawn.

    Returns a list of human-readable error lines. Empty list = both servers
    are importable. We do this in a subprocess so an ImportError in one MCP
    module can't crash the web process itself, and so the probe environment
    matches what the SDK will actually exec.

    On any failure we also surface the fastmcp diagnostic line — almost
    every MCP import error so far has traced back to a wrong fastmcp
    version, so knowing what's actually installed is invaluable.
    """
    diag_snippet = (
        "import fastmcp, pathlib;"
        "p = pathlib.Path(fastmcp.__file__).parent;"
        "print('fastmcp', fastmcp.__version__);"
        "print('oauth_proxy as file:', (p / 'server/auth/oauth_proxy.py').is_file());"
        "print('oauth_proxy as pkg:', (p / 'server/auth/oauth_proxy/__init__.py').is_file())"
    )
    probes = {
        "autocodabench": "import auto_codabench.mcp_server.server",
        "alex-mcp": "import alex_mcp.server",
    }
    failures: list[str] = []
    for name, snippet in probes.items():
        try:
            result = subprocess.run(
                [PYTHON_BIN, "-c", snippet],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"`{name}`: import probe timed out after 15s")
            continue
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip().splitlines()
            tail = "\n".join(err[-6:]) if err else "(no stderr)"
            failures.append(f"`{name}` failed to import:\n```\n{tail}\n```")

    if failures:
        try:
            diag = subprocess.run(
                [PYTHON_BIN, "-c", diag_snippet],
                capture_output=True, text=True, timeout=10,
            )
            info = (diag.stdout or diag.stderr or "(no output)").strip()
            failures.append(f"**runtime diagnostic:**\n```\n{info}\n```")
        except Exception as e:
            failures.append(f"**runtime diagnostic failed:** {e}")
    return failures


def _mcp_servers(run_dir: Path) -> dict:
    """Configure both MCP servers as stdio subprocesses scoped to this session."""
    env_for_mcp = {
        # Inherit everything from the parent so credentials are available,
        # then overlay the per-session run dir so tool snapshots land here.
        **os.environ,
        "AUTOCODABENCH_RUN_DIR": str(run_dir),
    }
    return {
        "autocodabench": {
            "type": "stdio",
            "command": PYTHON_BIN,
            "args": ["-m", "auto_codabench.mcp_server.server"],
            "env": env_for_mcp,
        },
        "alex-mcp": {
            "type": "stdio",
            "command": PYTHON_BIN,
            "args": ["-m", "alex_mcp.server"],
            "env": env_for_mcp,
        },
    }


# ---------------------------------------------------------------------------
# Auth: a single shared password
# ---------------------------------------------------------------------------

@cl.password_auth_callback
def auth_callback(username: str, password: str):
    """Anyone who knows SHARED_PASSWORD gets in. The username is informational only."""
    if not SHARED_PASSWORD:
        return cl.User(identifier="anon", metadata={"warning": "SHARED_PASSWORD not set"})
    if password == SHARED_PASSWORD:
        return cl.User(identifier=username or "guest", metadata={})
    return None


# ---------------------------------------------------------------------------
# Per-session lifecycle
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_chat_start():
    # Guard against duplicate firings of on_chat_start for the SAME
    # underlying Chainlit session. This happens in two situations:
    #   1. The websocket reconnects (laptop sleep, network blip,
    #      browser putting the tab to sleep). Chainlit's default is to
    #      re-fire on_chat_start; we'd otherwise mint a NEW run dir +
    #      spawn a NEW SDK client + send a NEW greeting on top of the
    #      existing one — visible to the user as the greeting "appearing
    #      again" mid-conversation.
    #   2. The user has two tabs / windows open on the same authenticated
    #      session — depending on Chainlit + browser behaviour, opening
    #      the second tab can fire on_chat_start in the *first* tab as
    #      well.
    # `cl.user_session` is scoped per Chainlit session; if we've already
    # set session_id, we're a re-fire. Bail without sending the greeting.
    if cl.user_session.get("session_id"):
        log.info(
            "on_chat_start re-fired for existing session %s — skipping re-init",
            cl.user_session.get("session_id"),
        )
        # Make sure the input is unlocked even on a re-fire, in case the
        # client expects the READY_PHRASE to land again.
        cl.user_session.set("ready", True)
        return

    # Lock the chat input until init finishes. The visual is provided by
    # chat.js — it shows a top-of-page banner the moment the chat page
    # is rendered (before this handler has even fired) and only removes
    # the banner once the greeting (containing READY_PHRASE) lands.
    # `ready` here is a Python-side backstop in case the JS lock is
    # bypassed (older browser, ad-blocker, etc.).
    cl.user_session.set("ready", False)

    # 1. Per-session isolated run dir + sibling subdirs the MCP server
    # expects to exist (it creates them on its own when *it* opens a
    # run, but we're pre-creating so `autocodabench_open_run` adopts
    # this one instead of carving a parallel `detached_<ts>/` next to it).
    session_id = uuid.uuid4().hex[:12]
    user = cl.user_session.get("user")
    user_id = (user.identifier if user else "anon").replace("/", "_")
    runtime_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = RUNS_ROOT / f"web_{user_id}_{runtime_id}_{session_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("tool_calls", "specs", "specs_history", "mcp_stderr"):
        (run_dir / sub).mkdir(exist_ok=True)
    # Write meta.json so the autocodabench MCP server *adopts* this dir
    # when the agent calls `autocodabench_open_run` — the adoption check
    # in run_log.open_run gates on `<dir>/meta.json` existing. Without
    # this file the agent would carve a fresh `detached_<ts>/` dir and
    # write `implementation_plan.md` there, leaving the approval-gate
    # logic polling the wrong path and no gate ever firing.
    meta = {
        "started_at": _utc_now(),
        "branch_id":  f"web-{user_id}",
        "runtime_id": runtime_id,
        "slug":       f"web_{session_id}",
        "session_id": session_id,
        "user":       user_id,
        "git_sha":    None,
        "cwd":        str(REPO_ROOT),
        "pid":        os.getpid(),
        "created_by": "web/app.py:on_chat_start",
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    # Don't pre-touch events.jsonl here — leaving it absent means the
    # MCP server creates it on first event (when there's actually
    # activity). Touching it on every page load means every idle
    # tab-open uploads a 0-byte events.jsonl to the HF Dataset, which
    # is noise. The MCP server's open_run handles the touch when it
    # adopts this dir.
    cl.user_session.set("run_dir", str(run_dir))
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("started_at", _utc_now())
    cl.user_session.set("had_user_message", False)

    # 2. Self-test: confirm both MCP server modules import. If broken,
    # surface immediately so the agent doesn't run tool-less. (The
    # banner stays up until the greeting lands, so the user knows the
    # warning isn't the entire startup state — more is coming.)
    mcp_failures = _probe_mcp_imports()
    if mcp_failures:
        await cl.Message(
            content=(
                "**⚠️ MCP servers failed to start.** Tools will be unavailable "
                "in this session — Claude can still chat about design, but "
                "can't open a run dir, snapshot specs, or search OpenAlex.\n\n"
                + "\n\n".join(mcp_failures)
            ),
            author="autocodabench",
        ).send()

    # 3. Configure the Claude Agent SDK client for Phase 1 (Plan).
    cl.user_session.set("phase", PHASE_PLAN)
    cl.user_session.set("phase_history", [])           # phases that have an artifact
    cl.user_session.set("switch_offered", False)       # legacy single-button flag
    cl.user_session.set("last_input_tokens", 0)        # for context-% chip
    cl.user_session.set("last_output_tokens", 0)

    client = ClaudeSDKClient(options=_build_options(run_dir, PHASE_PLAN))
    await client.connect()
    cl.user_session.set("client", client)

    # 3b. Stage-progress is displayed as an in-chat message ("Starting
    # Kit development progress"), not the right-side cl.TaskList. The
    # right sidebar was confusing — users didn't know the rows meant
    # notebook sections. Progress lives in the conversation flow now.
    # Created lazily on the first build event (see _refresh_progress).
    cl.user_session.set("progress_msg", None)
    cl.user_session.set("section_status", {})       # stage_id -> "ready|running|done|failed"
    cl.user_session.set("events_cursor", 0)         # byte offset into events.jsonl
    cl.user_session.set("show_progress", False)

    # 4. Greeting — this contains READY_PHRASE ("Tell me a competition
    # idea") which is the signal chat.js watches for to drop the banner
    # and unlock the input. Keep that exact phrase in the first line.
    # We lay out the 3-phase contract up front so the user knows the
    # phase bar is the navigation surface.
    await cl.Message(
        content=(
            "# 🧠 AutoCodabench — design a Codabench competition\n\n"
            "Tell me a competition idea — a sentence is enough — and I'll "
            "explore the design space with you, citing the literature as "
            "we go. You can also drop a PDF / markdown design doc and I'll "
            "fill in only the gaps.\n\n"
            "### How this app works — 3 phases\n\n"
            "**1. 📝 Plan** *(you are here)* — short roadmap conversation; "
            "I save a one-page `implementation_plan.md`. Pure prose, no "
            "code.\n\n"
            "**2. 📓 Starting Kit** — a fresh agent reads the locked plan "
            "and builds the full `starting_kit.ipynb` (all 7 design "
            "sections + working demo code). Per-section refinement here.\n\n"
            "**3. 📦 Bundle** — another fresh agent packages the executed "
            "notebook into a Codabench `.zip`. Optional one-click upload.\n\n"
            "**Phase bar at the top** drives navigation. Each phase starts "
            "with a clean context (no memory of the previous chat — just "
            "the locked artifact). That's the cost-savings mechanism. "
            "Click 🔒 on a previous phase to revise it (discards everything "
            "downstream).\n\n"
            "The panel on the right holds the live notebook + transcript + "
            "cost + specs tabs.\n\n"
            f"_session `{session_id}` · model `{DEFAULT_MODEL}` · "
            f"budget ${MAX_USD_PER_SESSION:.2f}_"
        ),
        author="autocodabench",
    ).send()

    cl.user_session.set("ready", True)

    # Pre-write a placeholder notebook.html + manifest.json so the
    # right panel has something to load on first render — even before
    # the user's first message arrives. Phase state too, so the phase
    # bar in chat.js can paint immediately.
    _write_public_artifacts(run_dir, session_id)
    _write_phase_state(run_dir, session_id)
    # Stand up the hidden phase-controls message so chat.js has
    # buttons to simulate-click from pill / advance interactions.
    await _refresh_phase_controls()


# ---------------------------------------------------------------------------
# SDK options builder (per phase)
# ---------------------------------------------------------------------------

def _build_options(run_dir: Path, phase: str) -> ClaudeAgentOptions:
    """Build the ClaudeAgentOptions for the requested phase.

    Each phase gets its own skill body + its own tool allowlist — see
    the _TOOLS_BY_PHASE table and the per-phase footer in
    _system_prompt(). max_budget_usd is shared across the whole
    session (cumulative cost cap), not per-phase.
    """
    tools = _TOOLS_BY_PHASE.get(phase, _PLAN_TOOLS)
    return ClaudeAgentOptions(
        model=DEFAULT_MODEL,
        system_prompt=_system_prompt(phase=phase),
        mcp_servers=_mcp_servers(run_dir),
        max_budget_usd=MAX_USD_PER_SESSION,
        permission_mode="bypassPermissions",
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "AUTOCODABENCH_RUN_DIR": str(run_dir),
        },
        allowed_tools=tools,
    )


@cl.on_message
async def on_message(msg: cl.Message):
    # If init is still in flight, gently reject. chat.js should have the
    # input disabled in this state anyway — this is a backstop in case
    # the user got around the JS lock (e.g. older browser, ad-blocker).
    if not cl.user_session.get("ready"):
        await cl.Message(
            content="_Still initializing — give me a few more seconds._",
            author="autocodabench",
        ).send()
        return

    client: ClaudeSDKClient | None = cl.user_session.get("client")
    run_dir = Path(cl.user_session.get("run_dir"))

    if client is None:
        await cl.Message(content="(no active session; please refresh)").send()
        return

    # Mark this session as "has activity" so on_chat_end's HF upload
    # actually fires. Without this flag the on_chat_end path skips the
    # upload, which means idle page loads don't litter the HF Dataset
    # with empty `meta.json` + `events.jsonl` shells.
    cl.user_session.set("had_user_message", True)

    # Mix in attached file contents (Demo path B: PDF / md drop).
    augmented_text = _augment_user_message(run_dir, msg)

    # Persist the user's turn to a plain-text transcript right away so it's
    # visible in run_dir even if Claude is mid-think. Include attachment
    # text so the transcript is reproducible without the original files.
    _append_transcript(run_dir, role="user", text=augmented_text)

    # Send the user's message to Claude and stream the response.
    response_msg = cl.Message(content="", author="autocodabench")
    await response_msg.send()

    # Track open tool steps (and their op label) by tool_use_id so we can
    # attach results when they come back as ToolResultBlock in a UserMessage
    # on the next iter. The op label is remembered separately so we can
    # restore it as the final chip name once the result lands.
    open_steps: dict[str, tuple[cl.Step, str]] = {}

    # Collect text + tool-call markdown for this turn so we can write a
    # single human-readable assistant block to transcript.md at the end.
    # Each tool call is stored by tool_use_id so when its result arrives
    # later we can fill in the output before flushing.
    turn_parts: list[dict] = []  # [{"kind":"text","text":...} | {"kind":"tool","id":..., "md":..., "raw_name":..., "op":..., "input":..., "output":..., "is_error":...}]
    tool_idx_by_id: dict[str, int] = {}

    try:
        await client.query(augmented_text)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        await response_msg.stream_token(block.text)
                        turn_parts.append({"kind": "text", "text": block.text})
                    elif isinstance(block, ToolUseBlock):
                        if block.name in _HIDDEN_TOOLS:
                            continue  # don't pollute the UI with agent machinery
                        op = _operation_label(block.name, block.input)
                        step = cl.Step(
                            name=f"Running {op}",
                            type="tool",
                            show_input="json",
                            parent_id=response_msg.id,
                        )
                        step.input = block.input
                        await step.send()
                        open_steps[block.id] = (step, op)
                        # Reserve the slot in the transcript; result fills later.
                        turn_parts.append({
                            "kind":     "tool",
                            "id":       block.id,
                            "raw_name": block.name,
                            "op":       op,
                            "input":    block.input,
                            "output":   "",
                            "is_error": False,
                        })
                        tool_idx_by_id[block.id] = len(turn_parts) - 1
                    elif isinstance(block, ThinkingBlock):
                        pass
            elif isinstance(message, UserMessage):
                blocks = message.content if isinstance(message.content, list) else []
                for block in blocks:
                    if isinstance(block, ToolResultBlock):
                        record = open_steps.pop(block.tool_use_id, None)
                        if record is None:
                            continue
                        step, op = record
                        step.name = op
                        # Normalise the tool result to a string.
                        if isinstance(block.content, list):
                            parts = []
                            for c in block.content:
                                if hasattr(c, "text"):
                                    parts.append(c.text)
                                elif isinstance(c, dict) and "text" in c:
                                    parts.append(c["text"])
                                else:
                                    parts.append(str(c))
                            out_text = "\n".join(parts)
                        else:
                            out_text = str(block.content or "")
                        is_error = bool(getattr(block, "is_error", False))
                        if is_error:
                            step.is_error = True
                        step.output = out_text
                        await step.update()
                        # Fill the transcript slot reserved earlier.
                        idx = tool_idx_by_id.get(block.tool_use_id)
                        if idx is not None:
                            turn_parts[idx]["output"]   = out_text
                            turn_parts[idx]["is_error"] = is_error
            elif isinstance(message, ResultMessage):
                # Final usage/cost summary at the end of an assistant turn.
                cost = getattr(message, "total_cost_usd", None) or 0.0
                cum = cl.user_session.get("cum_cost_usd", 0.0) + cost
                cl.user_session.set("cum_cost_usd", cum)
                # Pull token usage for the live context-% chip in the
                # phase bar. Schema: ResultMessage.usage is a dict
                # (anthropic SDK shape) with input_tokens / output_tokens.
                usage = getattr(message, "usage", None) or {}
                if isinstance(usage, dict):
                    in_tok  = int(usage.get("input_tokens")  or 0)
                    out_tok = int(usage.get("output_tokens") or 0)
                else:
                    in_tok  = int(getattr(usage, "input_tokens",  0) or 0)
                    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
                if in_tok:
                    cl.user_session.set("last_input_tokens", in_tok)
                if out_tok:
                    cl.user_session.set("last_output_tokens", out_tok)
                # Per-turn footer: cost + cumulative + context %. The
                # header-row phase pills intentionally don't carry this
                # info — too noisy on every paint. The user sees a
                # one-line summary at the end of each assistant turn
                # instead, same place the Claude Code CLI puts it.
                if cost or in_tok:
                    ctx_pct = (100.0 * in_tok / CONTEXT_WINDOW_TOKENS
                               if in_tok else 0.0)
                    await response_msg.stream_token(
                        f"\n\n_turn ≈ ${cost:.3f} · session "
                        f"${cum:.2f} / ${MAX_USD_PER_SESSION:.2f} · "
                        f"ctx {ctx_pct:.1f}% ({in_tok:,} tok)_"
                    )
                # Persist a per-turn cost line so we can audit spend
                # offline (one JSON object per line, easy to aggregate).
                _log_cost(run_dir, turn_cost=cost, cumulative=cum)
            elif isinstance(message, SystemMessage):
                # Surface SDK system events (rate limit, budget hit) in chat.
                subtype = getattr(message, "subtype", "")
                if subtype in ("budget_exceeded", "rate_limit", "stop"):
                    await cl.Message(
                        content=f"_[system: {subtype}]_",
                        author="autocodabench",
                    ).send()
    except Exception as e:
        await cl.Message(
            content=f"**Error:** `{type(e).__name__}: {e}`",
            author="autocodabench",
        ).send()

    await response_msg.update()

    # Mirror the assistant's full response into the per-session transcript.
    # We splice the streamed text and the (now-completed) tool calls into
    # a single markdown block so the transcript reads top-to-bottom, with
    # each tool call appearing inline in `<details>` collapsibles between
    # the assistant's prose. This is the format colleagues will read
    # offline (per-session run_dir / GitHub view / HF Dataset).
    if turn_parts:
        body_chunks: list[str] = []
        for part in turn_parts:
            if part["kind"] == "text":
                body_chunks.append(part["text"])
            else:
                body_chunks.append(_format_tool_call_md(
                    op=part["op"],
                    raw_name=part["raw_name"],
                    input_json=part["input"],
                    output_text=part["output"],
                    is_error=part["is_error"],
                ))
        _append_transcript(run_dir, role="claude", text="".join(body_chunks))

    # Write the per-session public artifacts (notebook HTML +
    # transcript + cost + specs as HTML, plus a manifest.json). The
    # always-visible right panel injected by chat.js fetches these
    # from `/public/sessions/<sid>/...` — the workspace panel is the
    # ONLY file viewer.
    sid = cl.user_session.get("session_id") or ""
    _write_public_artifacts(run_dir, sid)
    # Phase state powers the top phase bar (current phase, lock
    # indicators, context %). Cheap; do it every turn.
    _write_phase_state(run_dir, sid)
    # Keep the hidden phase-controls message current so chat.js can
    # find the right buttons to simulate-click from pill clicks.
    await _refresh_phase_controls()
    # Update the in-chat "Starting Kit development progress" message.
    await _refresh_task_list(run_dir)
    # If stage 8 just produced a bundle, offer download + upload once.
    await _maybe_offer_bundle_actions()

    # Persist the entire run_dir to a private HF Dataset, async — so a
    # slow network request doesn't block the next user turn. The user
    # closing the tab mid-turn means we lose at most one turn's data.
    asyncio.create_task(_persist_to_hf(run_dir))

    # Per-stage approval gates fire from inside _refresh_task_list when
    # a stage transitions to DONE — see below.


# ---------------------------------------------------------------------------
# Per-stage approval gates (replaces the legacy single-button switch)
#
# After every stage transitions to DONE (via `stage_done` /
# `stage_approved` events), we show a small cl.AskActionMessage with
# three actions: Approve & advance, Revise this stage, Save & exit.
# Stage 7 (Diagnostics) is special — its Approve action does the
# planning→implementation phase switch (rebuild SDK client with bundle
# tools) and kicks off stage 8 (Bundle packaging).
#
# Stages 0 (Roadmap) and 8 (Bundle) don't fire gates: stage 0 is
# design-only with no executable cells; stage 8 is the terminal
# packaging step and presents a download link directly.
#
# Pre-PR4 backstop: if the agent (still on the prose-proposal flow)
# writes `implementation_plan.md`, we treat that as equivalent to
# stage 7 being done and offer the same gate path. Once PR4's skill
# rewrite ships, the agent emits real stage events and this fallback
# stops firing in practice.
# ---------------------------------------------------------------------------

# Index for STAGE_TITLES so payloads stay machine-readable.
_STAGE_BY_ID:   dict[str, int]  = {sid: i for i, (sid, _) in enumerate(STAGE_TITLES)}
_STAGE_BY_IDX:  dict[int, str]  = {i: sid for i, (sid, _) in enumerate(STAGE_TITLES)}
_STAGE_TITLE:   dict[str, str]  = {sid: title for sid, title in STAGE_TITLES}


def _stage_at(i: int) -> tuple[str, str] | None:
    if 0 <= i < len(STAGE_TITLES):
        return STAGE_TITLES[i]
    return None


async def _maybe_offer_stage_gate(run_dir: Path, stage: str) -> None:
    """Phase 2 only: open the section-picker after a writing pass.

    In the 3-phase model, the bundle decision is made at the phase bar
    (Advance to Phase 3), not at this gate. We keep the Refine /
    Save & exit affordances and *also* mention the Advance pill so the
    user knows where to go next.

    The new flow (per user direction 2026-05-23): the agent generates
    the FULL starting_kit.ipynb — all 7 design sections + demo code —
    in one pass during stage 1 (and progressively logs `stage_done`
    for each section as it goes). When the LAST design section
    (`7.schedule`) lands, we surface ONE gate with:

      - 7 [Refine <section>] buttons (one per design section)
      - 1 [✅ Build the Codabench bundle] button
      - 1 [🛑 Save & exit] button

    No per-section Approve/Revise treadmill — the user picks what to
    refine, or accepts the whole thing and builds the bundle.

    Sub-iterations re-open the same gate when the refined section's
    `stage_done` fires; the `gates_offered` set is keyed by a marker
    rather than per-stage so the gate can re-appear after each
    refinement.
    """
    if cl.user_session.get("phase") != PHASE_KIT:
        return

    # Don't gate on `0.roadmap` (design talk only) or `8.bundle`
    # (terminal packaging) — but ANY of 1-7 going DONE is a signal
    # that the notebook now has content worth offering choices over.
    si = _STAGE_BY_ID.get(stage, -1)
    if si < 1 or si > 7:
        return

    # The gate is offered once per "writing pass" — keyed by a
    # session-incremented marker rather than per-stage. Each pass
    # of agent-writes-things ends with the user choosing the next
    # step; refining a section starts a new pass.
    pass_id = cl.user_session.get("gate_pass_id", 0)
    gates: set = cl.user_session.get("gates_offered") or set()
    marker = f"pass:{pass_id}"
    if marker in gates:
        return
    gates = set(gates)
    gates.add(marker)
    cl.user_session.set("gates_offered", gates)

    actions: list[cl.Action] = []
    # One Refine button per design section that has any cells in the
    # notebook. We look at the notebook on disk so we don't list
    # buttons for sections the agent hasn't written yet.
    nb_path = run_dir / "starting_kit.ipynb"
    sections_present: set[str] = set()
    if nb_path.is_file():
        try:
            import nbformat
            nb = nbformat.read(nb_path, as_version=4)
            for c in nb.cells:
                s = (c.get("metadata") or {}).get("autocodabench_stage")
                if s:
                    sections_present.add(s)
        except Exception:
            pass
    for sid, title in STAGE_TITLES:
        si2 = _STAGE_BY_ID[sid]
        if si2 < 1 or si2 > 7:
            continue
        if sid not in sections_present:
            continue
        actions.append(cl.Action(
            name="ac_section_refine",
            payload={"section": sid},
            label=f"✏️ Refine {title}",
            tooltip=f"Restart the kernel, reset {title}'s cells, ask "
                    f"the agent what to refine.",
        ))

    actions.append(cl.Action(
        name="ac_build_bundle",
        payload={},
        label="✅ Approve all & advance to Phase 3 — Bundle",
        tooltip="Same as clicking ▶ Advance to Phase 3 in the phase bar. "
                "Rebuilds the agent with bundle-write tools and discards "
                "the kit-phase chat history.",
    ))
    actions.append(cl.Action(
        name="ac_stage_save_exit",
        payload={"stage": stage, "stage_index": si},
        label="🛑 Save & exit",
        tooltip="End the session, keep the run dir + HF Dataset upload.",
    ))

    if sections_present == {"1.task", "2.data", "3.metric", "4.baseline_kit",
                            "5.rules", "6.ethics", "7.schedule"}:
        headline = ("## ✅ Notebook v1 — all 7 sections drafted\n\n"
                    "Look at the panel on the right: the executed "
                    "`starting_kit.ipynb` has all 7 design sections "
                    "with demo code that ran end-to-end. From here:")
    else:
        nice = ", ".join(sorted(sections_present))
        headline = (f"## ⏵ Drafted sections: {nice}\n\n"
                    f"The agent paused after writing some sections. "
                    f"Pick what to do next:")

    await cl.Message(
        author="autocodabench",
        content=(
            f"{headline}\n\n"
            f"- **Refine a section** — restart its kernel state and "
            f"  rewrite cells; the rest of the notebook stays put.\n"
            f"- **Approve all & build the Codabench bundle** — package "
            f"  what's in the notebook now into a `.zip`. Stage 8 runs "
            f"  automatically and adds the bundle to the file panel.\n"
            f"- **Save & exit** — keep everything as-is, upload to the "
            f"  HF Dataset, end the session.\n"
        ),
        actions=actions,
    ).send()


async def _refresh_legacy_bundle_gate(run_dir: Path) -> None:
    """Pre-PR4 fallback: treat implementation_plan.md as section-7-done.

    Older sessions (built against the prose proposal/specs flow) write
    `implementation_plan.md` at the end. We map that onto the new
    section-picker gate so legacy sessions still expose the build-bundle
    affordance.
    """
    if cl.user_session.get("phase") != PHASE_KIT:
        return
    candidates = [
        run_dir / "specs" / "implementation_plan.md",
        run_dir / "implementation_plan.md",
    ]
    latest = RUNS_ROOT / "LATEST"
    if latest.exists():
        try:
            latest_resolved = latest.resolve()
        except OSError:
            latest_resolved = None
        if latest_resolved and latest_resolved != run_dir.resolve():
            candidates.extend([
                latest_resolved / "specs" / "implementation_plan.md",
                latest_resolved / "implementation_plan.md",
            ])
    plan = next((p for p in candidates if p.is_file()), None)
    if plan is None:
        return
    effective = plan.parent if plan.parent.name == "specs" else plan.parent
    if effective.resolve() != run_dir.resolve():
        log.warning("legacy bundle gate: plan in %s, pivoting from %s", effective, run_dir)
        cl.user_session.set("run_dir", str(effective))
        run_dir = effective
    # Use the most-meaningful design section as the trigger for the gate.
    await _maybe_offer_stage_gate(run_dir, "7.schedule")


# ---------------------------------------------------------------------------
# Stage-action callbacks
# ---------------------------------------------------------------------------

async def _maybe_offer_bundle_actions() -> None:
    """Surface a 'Download / Upload' message once stage 8's zip exists.

    Fires at most once per session — `bundle_actions_offered` flag.
    The bundle's location is `auto_codabench/bundles/<slug>/<slug>.zip`
    (the agent's zip_bundle tool puts it there); we mirror it to the
    per-session public dir from _write_public_artifacts. Download =
    direct link to that public file. Upload = cl.Action triggering
    `autocodabench_upload_bundle`.
    """
    if cl.user_session.get("bundle_actions_offered"):
        return
    run_dir = Path(cl.user_session.get("run_dir") or ".")
    session_id = cl.user_session.get("session_id") or ""
    if not session_id:
        return
    public_zip = _PUBLIC_SESSIONS / session_id / "bundle.zip"
    if not public_zip.is_file():
        return
    cl.user_session.set("bundle_actions_offered", True)

    size_mb = public_zip.stat().st_size / (1024 * 1024)
    download_url = f"/public/sessions/{session_id}/bundle.zip"
    actions = [
        cl.Action(name="ac_upload_codabench",
                  payload={"zip_path": str(public_zip)},
                  label="⬆️ Upload to Codabench",
                  tooltip="Publishes the bundle to Codabench using "
                          "CODABENCH_USERNAME / CODABENCH_PASSWORD "
                          "from the Space's Repository Secrets. "
                          "Returns the competition URL when finished."),
    ]
    await cl.Message(
        author="autocodabench",
        content=(
            f"## 📦 Bundle ready ({size_mb:.1f} MB)\n\n"
            f"**[📥 Download bundle.zip]({download_url})** — click to "
            f"save it locally; the same link is in the workspace "
            f"panel's `📦 bundle.zip` tab.\n\n"
            f"Or click below to publish it directly to Codabench. "
            f"You'll see the competition URL when the upload finishes."
        ),
        actions=actions,
    ).send()


@cl.action_callback("ac_upload_codabench")
async def _on_upload_codabench(action: cl.Action):
    """Run autocodabench_upload_bundle through the agent and show progress."""
    p = action.payload or {}
    run_dir = Path(cl.user_session.get("run_dir") or ".")
    # Find the slug — the bundle dir name under auto_codabench/bundles/.
    bundle_src = _find_bundle_zip(run_dir)
    slug = bundle_src.parent.name if bundle_src else None
    if not slug:
        await cl.Message(
            author="autocodabench",
            content="❌ Couldn't find the bundle slug. Re-run stage 8 first.",
        ).send()
        return
    # Surface a status step so the user can watch.
    step = cl.Step(name=f"Uploading {slug} to Codabench…", type="tool",
                   show_input="json")
    step.input = {"slug": slug}
    await step.send()
    _append_transcript(run_dir, role="user",
                       text=f"[ui] Upload bundle '{slug}' to Codabench.")
    # The agent (in PHASE_IMPLEMENTATION) has the upload tool;
    # synthesise a turn asking it to run upload_bundle and surface
    # the competition URL.
    await _stream_one_turn(
        run_dir,
        f"The user clicked **Upload to Codabench**. Call "
        f"`autocodabench_upload_bundle(slug='{slug}')` NOW. When it "
        f"returns, surface the `competition_url` as a clickable "
        f"markdown link, prominently. If the call fails (bad "
        f"credentials, network), report the error verbatim and "
        f"suggest the user verify CODABENCH_USERNAME / "
        f"CODABENCH_PASSWORD in the Space's Repository Secrets.",
    )
    try:
        step.output = "Upload kicked off — watch the tool chips above for the URL."
        await step.update()
    except Exception:
        pass


@cl.action_callback("ac_section_refine")
async def _on_section_refine(action: cl.Action):
    """User picked a section to refine — open a new writing pass."""
    p = action.payload or {}
    section = p.get("section", "")
    run_dir = Path(cl.user_session.get("run_dir"))
    si = _STAGE_BY_ID.get(section, -1)
    if si < 1 or si > 7:
        return

    # Open a new "pass" so the next stage_done fires a fresh gate.
    cl.user_session.set("gate_pass_id",
                        int(cl.user_session.get("gate_pass_id", 0)) + 1)

    # Bump this section back to "running" in the in-chat progress.
    section_status: dict[str, str] = dict(cl.user_session.get("section_status") or {})
    if section in section_status:
        section_status[section] = "running"
    cl.user_session.set("section_status", section_status)
    progress_msg = cl.user_session.get("progress_msg")
    if progress_msg is not None:
        progress_msg.content = _render_progress_markdown(section_status)
        try:
            await progress_msg.update()
        except Exception:
            pass

    title = _STAGE_TITLE.get(section, section)
    _append_transcript(run_dir, role="user",
                       text=f"[ui] Refine {title}.")
    await _stream_one_turn(
        run_dir,
        f"The user clicked **Refine {title}** (section `{section}`). "
        f"Do this NOW, in order: "
        f"(1) call `autocodabench_nb_reset_to_stage(stage='{section}')` "
        f"to restart the kernel and re-execute earlier sections, "
        f"(2) ask the user in ONE short paragraph what specifically "
        f"they want different about this section — don't rewrite "
        f"cells until they reply, "
        f"(3) when they answer, rewrite the cells for `{section}` "
        f"and `nb_run_stage('{section}')`, "
        f"(4) log `stage_done` with payload {{\"stage\":\"{section}\"}} "
        f"so the section-picker gate re-opens.",
    )


@cl.action_callback("ac_build_bundle")
async def _on_build_bundle(action: cl.Action):
    """Legacy: 'Approve all & build bundle' button — same effect as
    clicking Advance to Phase 3 in the phase bar."""
    await _advance_to_phase(PHASE_BUNDLE)


# ---------------------------------------------------------------------------
# Phase advance / revert — driven by the phase bar at the top of the page.
# Each transition disconnects the old SDK client and builds a fresh one with
# the new phase's system prompt + tool allowlist. The previous conversation
# is dropped entirely — only the artifact on disk carries forward. That is
# the entire cost-savings mechanism of the 3-phase split.
# ---------------------------------------------------------------------------

def _reset_kit_session_state() -> None:
    """Wipe per-phase ephemeral state (used on every phase transition)."""
    cl.user_session.set("last_input_tokens", 0)
    cl.user_session.set("last_output_tokens", 0)
    cl.user_session.set("section_status", {})
    cl.user_session.set("progress_msg", None)
    cl.user_session.set("events_cursor", 0)
    cl.user_session.set("show_progress", False)
    cl.user_session.set("gates_offered", set())
    cl.user_session.set("gate_pass_id", 0)
    cl.user_session.set("bundle_actions_offered", False)


def _delete_phase_artifact(run_dir: Path, phase: str) -> None:
    """Remove a phase's artifact + per-session public copies.

    Called on back-navigation: discard everything downstream so the next
    forward advance regenerates from the (possibly edited) earlier
    artifact. Plan is NEVER auto-deleted — back-nav to Plan means EDIT,
    not blank.
    """
    try:
        if phase == PHASE_KIT:
            nb = run_dir / "starting_kit.ipynb"
            if nb.is_file():
                nb.unlink()
        elif phase == PHASE_BUNDLE:
            # The agent's zip_bundle writes into auto_codabench/bundles/<slug>/.
            # We don't track the slug here — nuke any zips that exist so
            # the next bundle phase regenerates them.
            bundles_root = REPO_ROOT / "auto_codabench" / "bundles"
            if bundles_root.is_dir():
                for d in bundles_root.iterdir():
                    if d.is_dir():
                        shutil.rmtree(d, ignore_errors=True)
            # Also drop the per-session bundle.zip copy.
            sid = cl.user_session.get("session_id") or ""
            if sid:
                pub = _PUBLIC_SESSIONS / sid / "bundle.zip"
                if pub.is_file():
                    pub.unlink()
    except Exception as e:
        log.warning("delete artifact for %s failed: %s", phase, e)


async def _switch_sdk_client(run_dir: Path, target: str) -> None:
    """Disconnect the current SDK client and stand up a fresh one for
    `target`. Leaves session state mutation (phase, history) to caller."""
    old = cl.user_session.get("client")
    if old is not None:
        try:
            await old.disconnect()
        except Exception as e:
            log.warning("disconnect on phase switch failed: %s", e)
    new_client = ClaudeSDKClient(options=_build_options(run_dir, target))
    await new_client.connect()
    cl.user_session.set("client", new_client)


async def _advance_to_phase(target: str) -> None:
    """Move forward to `target` (must be exactly one step ahead, or
    delegate to revert if it's behind)."""
    if target not in PHASE_ORDER:
        return
    run_dir = Path(cl.user_session.get("run_dir"))
    current = cl.user_session.get("phase") or PHASE_PLAN
    if target == current:
        return
    tgt_idx = PHASE_ORDER.index(target)
    cur_idx = PHASE_ORDER.index(current)
    if tgt_idx < cur_idx:
        await _revert_to_phase(target)
        return

    # Sanity: don't advance unless the current phase has its artifact.
    if not _phase_artifact_exists(run_dir, current):
        await cl.Message(
            author="autocodabench",
            content=(
                f"⚠ Can't advance to {PHASE_TITLE[target]} — "
                f"{PHASE_TITLE[current]} hasn't produced "
                f"`{PHASE_ARTIFACT[current]}` yet."
            ),
        ).send()
        return

    # Record the current phase as completed.
    history = list(cl.user_session.get("phase_history") or [])
    if current not in history:
        history.append(current)
    cl.user_session.set("phase_history", history)

    _reset_kit_session_state()
    cl.user_session.set("phase", target)
    await _switch_sdk_client(run_dir, target)
    _append_transcript(run_dir, role="user",
                       text=f"[ui] Advance to {PHASE_TITLE[target]}.")
    await _send_phase_kickoff(run_dir, target)

    _write_public_artifacts(run_dir, cl.user_session.get("session_id") or "")
    _write_phase_state(run_dir, cl.user_session.get("session_id") or "")
    await _refresh_phase_controls()


async def _revert_to_phase(target: str) -> None:
    """Move BACK to `target` and discard all downstream artifacts."""
    if target not in PHASE_ORDER:
        return
    run_dir = Path(cl.user_session.get("run_dir"))
    current = cl.user_session.get("phase") or PHASE_PLAN
    if target == current:
        return
    tgt_idx = PHASE_ORDER.index(target)
    cur_idx = PHASE_ORDER.index(current)
    if tgt_idx >= cur_idx:
        # Caller asked for forward via the revert path; delegate.
        await _advance_to_phase(target)
        return

    # Discard artifacts for all phases > target (per user-confirmed design).
    for ph in PHASE_ORDER[tgt_idx + 1:]:
        _delete_phase_artifact(run_dir, ph)

    # Trim history.
    history = [p for p in (cl.user_session.get("phase_history") or [])
               if PHASE_ORDER.index(p) < tgt_idx]
    cl.user_session.set("phase_history", history)

    _reset_kit_session_state()
    cl.user_session.set("phase", target)
    await _switch_sdk_client(run_dir, target)
    _append_transcript(
        run_dir, role="user",
        text=f"[ui] Back to {PHASE_TITLE[target]} — discarded downstream artifacts.")
    await _send_phase_revisit(run_dir, target)

    _write_public_artifacts(run_dir, cl.user_session.get("session_id") or "")
    _write_phase_state(run_dir, cl.user_session.get("session_id") or "")
    await _refresh_phase_controls()


async def _send_phase_kickoff(run_dir: Path, target: str) -> None:
    """User-facing greeting + agent kickoff prompt for a forward advance."""
    if target == PHASE_KIT:
        await cl.Message(
            author="autocodabench",
            content=(
                "# 📓 Phase 2 — Starting Kit\n\n"
                "Fresh agent with no memory of Phase 1. It will read "
                "`specs/implementation_plan.md`, then write the full "
                "`starting_kit.ipynb` (all 7 sections + demo code) in one "
                "pass. Per-section refinement after that."
            ),
        ).send()
        await _stream_one_turn(
            run_dir,
            "Begin Phase 2. Read `specs/implementation_plan.md` first, then "
            "build the starting kit notebook end-to-end. Follow your skill "
            "exactly.",
        )
    elif target == PHASE_BUNDLE:
        await cl.Message(
            author="autocodabench",
            content=(
                "# 📦 Phase 3 — Bundle\n\n"
                "Fresh agent with bundle-write tools. It will read the "
                "notebook and `events.jsonl`, then produce "
                "`competition.yaml`, `scoring_program/`, `solution/`, "
                "`pages/`, and finally a `.zip` you can download or "
                "publish to Codabench."
            ),
        ).send()
        await _stream_one_turn(run_dir, "Begin Phase 3: bundle packaging.")


async def _send_phase_revisit(run_dir: Path, target: str) -> None:
    """User-facing message after a BACK navigation."""
    if target == PHASE_PLAN:
        await cl.Message(
            author="autocodabench",
            content=(
                "# 📝 Phase 1 — Plan *(re-opened)*\n\n"
                "Downstream artifacts discarded. The plan itself is "
                "preserved — tell me what to change and I'll re-snapshot "
                "it. When you're done, click **▶ Advance to Phase 2** to "
                "regenerate the starting kit from the updated plan."
            ),
        ).send()
        # Don't auto-prompt the agent here — wait for the user to say what
        # to change. (Saves a needless turn.)
    elif target == PHASE_KIT:
        await cl.Message(
            author="autocodabench",
            content=(
                "# 📓 Phase 2 — Starting Kit *(re-opened)*\n\n"
                "Bundle discarded. The notebook is preserved — refine "
                "sections, or click **▶ Advance to Phase 3** when you're "
                "ready to rebuild the bundle."
            ),
        ).send()


async def _refresh_phase_controls() -> None:
    """Maintain a single hidden cl.Message with phase-action buttons.

    chat.js finds these buttons by their stable label prefix
    (`AC_ADVANCE::<target>` / `AC_REVERT::<target>`), hides them
    visually, and simulates clicks when the user clicks a pill in
    the top phase bar.
    """
    run_dir = Path(cl.user_session.get("run_dir") or ".")
    current = cl.user_session.get("phase") or PHASE_PLAN
    cur_idx = PHASE_ORDER.index(current)

    actions: list[cl.Action] = []
    if cur_idx + 1 < len(PHASE_ORDER):
        nxt = PHASE_ORDER[cur_idx + 1]
        actions.append(cl.Action(
            name="ac_advance_phase",
            payload={"target": nxt},
            label=f"AC_ADVANCE::{nxt}",
            tooltip=f"Advance to {PHASE_TITLE[nxt]}",
        ))
    for prev in PHASE_ORDER[:cur_idx]:
        actions.append(cl.Action(
            name="ac_revert_phase",
            payload={"target": prev},
            label=f"AC_REVERT::{prev}",
            tooltip=f"Back to {PHASE_TITLE[prev]} (discards downstream)",
        ))

    msg: cl.Message | None = cl.user_session.get("phase_controls_msg")
    placeholder = "_phase controls (hidden — driven by the top phase bar)_"
    if msg is None:
        msg = cl.Message(content=placeholder,
                         author="ac-phase-controls",
                         actions=actions)
        await msg.send()
        cl.user_session.set("phase_controls_msg", msg)
    else:
        msg.actions = actions
        try:
            await msg.update()
        except Exception as e:
            log.warning("phase_controls update failed: %s — sending fresh msg", e)
            msg = cl.Message(content=placeholder,
                             author="ac-phase-controls",
                             actions=actions)
            await msg.send()
            cl.user_session.set("phase_controls_msg", msg)


@cl.action_callback("ac_advance_phase")
async def _on_advance_phase(action: cl.Action):
    target = (action.payload or {}).get("target")
    await _advance_to_phase(str(target))


@cl.action_callback("ac_revert_phase")
async def _on_revert_phase(action: cl.Action):
    target = (action.payload or {}).get("target")
    await _revert_to_phase(str(target))


@cl.action_callback("ac_stage_save_exit")
async def _on_stage_save_exit(action: cl.Action):
    p = action.payload or {}
    stage = p.get("stage", "")
    si    = int(p.get("stage_index", 0))
    run_dir = Path(cl.user_session.get("run_dir"))
    cl.user_session.set("ended", True)
    _append_transcript(run_dir, role="user",
                       text=f"[ui] Save & exit after {_STAGE_TITLE.get(stage, stage)} (stage {si}).")
    await cl.Message(
        author="autocodabench",
        content=(
            f"## 🛑 Session saved at {_STAGE_TITLE.get(stage, stage)}\n\n"
            f"All artifacts up to this point are in `{run_dir}/` and have "
            f"been (or are about to be) uploaded to the private HF "
            f"Dataset. Refresh the page to start a fresh session.\n\n"
            f"This session's run dir name: `{run_dir.name}` — keep it "
            f"around for the dataset path."
        ),
    ).send()
    # Final flush — synchronous so we don't lose the last upload to a
    # tab-close race.
    try:
        await _persist_to_hf(run_dir)
    except Exception as e:
        log.warning("save&exit HF persist failed: %s", e)


async def _switch_to_implementation_for_bundle(run_dir: Path) -> None:
    """Legacy shim — old call sites end up at the generic advance helper."""
    await _advance_to_phase(PHASE_BUNDLE)


async def _stream_one_turn(run_dir: Path, prompt_text: str) -> None:
    """Stream one assistant response to the UI for a *synthetic* user prompt.

    Used by the phase-switch kickoff: we inject a server-side prompt the
    user didn't type but still want to render and log normally.
    """
    client: ClaudeSDKClient | None = cl.user_session.get("client")
    if client is None:
        return
    response_msg = cl.Message(content="", author="autocodabench")
    await response_msg.send()
    turn_parts: list[dict] = []
    open_steps: dict[str, tuple[cl.Step, str]] = {}
    tool_idx_by_id: dict[str, int] = {}

    try:
        await client.query(prompt_text)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        await response_msg.stream_token(block.text)
                        turn_parts.append({"kind": "text", "text": block.text})
                    elif isinstance(block, ToolUseBlock):
                        if block.name in _HIDDEN_TOOLS:
                            continue
                        op = _operation_label(block.name, block.input)
                        step = cl.Step(
                            name=f"Running {op}",
                            type="tool",
                            show_input="json",
                            parent_id=response_msg.id,
                        )
                        step.input = block.input
                        await step.send()
                        open_steps[block.id] = (step, op)
                        turn_parts.append({
                            "kind": "tool", "id": block.id,
                            "raw_name": block.name, "op": op,
                            "input": block.input, "output": "", "is_error": False,
                        })
                        tool_idx_by_id[block.id] = len(turn_parts) - 1
            elif isinstance(message, UserMessage):
                blocks = message.content if isinstance(message.content, list) else []
                for block in blocks:
                    if isinstance(block, ToolResultBlock):
                        record = open_steps.pop(block.tool_use_id, None)
                        if record is None:
                            continue
                        step, op = record
                        step.name = op
                        if isinstance(block.content, list):
                            parts = []
                            for c in block.content:
                                if hasattr(c, "text"):
                                    parts.append(c.text)
                                elif isinstance(c, dict) and "text" in c:
                                    parts.append(c["text"])
                                else:
                                    parts.append(str(c))
                            out_text = "\n".join(parts)
                        else:
                            out_text = str(block.content or "")
                        is_error = bool(getattr(block, "is_error", False))
                        step.is_error = is_error
                        step.output = out_text
                        await step.update()
                        idx = tool_idx_by_id.get(block.tool_use_id)
                        if idx is not None:
                            turn_parts[idx]["output"]   = out_text
                            turn_parts[idx]["is_error"] = is_error
            elif isinstance(message, ResultMessage):
                cost = getattr(message, "total_cost_usd", None) or 0.0
                cum  = cl.user_session.get("cum_cost_usd", 0.0) + cost
                cl.user_session.set("cum_cost_usd", cum)
                usage = getattr(message, "usage", None) or {}
                if isinstance(usage, dict):
                    in_tok  = int(usage.get("input_tokens")  or 0)
                    out_tok = int(usage.get("output_tokens") or 0)
                else:
                    in_tok  = int(getattr(usage, "input_tokens",  0) or 0)
                    out_tok = int(getattr(usage, "output_tokens", 0) or 0)
                if in_tok:
                    cl.user_session.set("last_input_tokens", in_tok)
                if out_tok:
                    cl.user_session.set("last_output_tokens", out_tok)
                if cost or in_tok:
                    ctx_pct = (100.0 * in_tok / CONTEXT_WINDOW_TOKENS
                               if in_tok else 0.0)
                    await response_msg.stream_token(
                        f"\n\n_turn ≈ ${cost:.3f} · session "
                        f"${cum:.2f} / ${MAX_USD_PER_SESSION:.2f} · "
                        f"ctx {ctx_pct:.1f}% ({in_tok:,} tok)_"
                    )
                _log_cost(run_dir, turn_cost=cost, cumulative=cum)
    except Exception as e:
        await cl.Message(
            content=f"**Error during phase switch:** `{type(e).__name__}: {e}`",
            author="autocodabench",
        ).send()

    await response_msg.update()
    if turn_parts:
        body_chunks = []
        for part in turn_parts:
            if part["kind"] == "text":
                body_chunks.append(part["text"])
            else:
                body_chunks.append(_format_tool_call_md(
                    op=part["op"], raw_name=part["raw_name"],
                    input_json=part["input"], output_text=part["output"],
                    is_error=part["is_error"],
                ))
        _append_transcript(run_dir, role="claude", text="".join(body_chunks))
    # Same panel + progress refresh as the regular on_message path.
    sid = cl.user_session.get("session_id") or ""
    _write_public_artifacts(run_dir, sid)
    _write_phase_state(run_dir, sid)
    await _refresh_task_list(run_dir)
    await _maybe_offer_bundle_actions()
    asyncio.create_task(_persist_to_hf(run_dir))


@cl.on_chat_end
async def on_chat_end():
    run_dir_str = cl.user_session.get("run_dir")
    had_activity = cl.user_session.get("had_user_message", False)
    if run_dir_str and had_activity:
        # Final flush — wait for this one (chat is over, latency is fine).
        # Gated on `had_user_message` so an idle tab-open doesn't push a
        # near-empty dir (just meta.json) to the HF Dataset.
        try:
            await _persist_to_hf(Path(run_dir_str))
        except Exception as e:
            log.warning("final HF persist failed: %s", e)
    client: ClaudeSDKClient | None = cl.user_session.get("client")
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Transcript writer (HF-Spaces equivalent of the Claude Code Stop hook)
# ---------------------------------------------------------------------------

def _append_transcript(run_dir: Path, *, role: str, text: str) -> None:
    """Append a role-tagged turn to <run_dir>/transcript.md.

    Tool calls are NOT separate turns — they're embedded inside the
    assistant turn as `<details>` collapsibles (see on_message). This
    keeps the transcript human-readable as a single linear document
    that renders cleanly in any markdown viewer (GitHub, VS Code,
    Obsidian) — sharable to colleagues without rerunning the chat.

    First-write quirk: a literal `---` near the top of a markdown file
    is interpreted by most renderers (GitHub, HF Dataset viewer,
    Obsidian, Pandoc, …) as the opening of YAML frontmatter — the
    next `---` closes it, and *everything in between is hidden*.
    Earlier versions of this function led every entry with `\\n---\\n\\n`,
    which meant the first user prompt + its header were swallowed by
    the frontmatter detector. Now we emit a title on first write and
    only use `---` as a *between-entries* separator from the second
    entry onward.
    """
    role_header = {
        "user": "## 👤 user — ",
        "claude": "## 🤖 autocodabench — ",
    }.get(role, f"## {role} — ")
    path = run_dir / "transcript.md"
    is_first_write = (not path.exists()) or path.stat().st_size == 0
    if is_first_write:
        header = (
            f"# Transcript — {run_dir.name}\n\n"
            f"_Per-session conversation, written turn-by-turn. Tool calls "
            f"are embedded inside each assistant block as `<details>` "
            f"collapsibles. Cost, events, and raw tool snapshots are "
            f"in sibling files (`cost.jsonl`, `events.jsonl`, "
            f"`tool_calls/`)._\n\n"
        )
        line = f"{header}{role_header}{_utc_now()}\n\n{text}\n"
    else:
        line = f"\n---\n\n{role_header}{_utc_now()}\n\n{text}\n"
    path.open("a", encoding="utf-8").write(line)


def _format_tool_call_md(*, op: str, raw_name: str, input_json: dict,
                         output_text: str, is_error: bool = False) -> str:
    """Render one tool call as a collapsed <details> block for transcript.md.

    The summary line is the friendly op label; expanding reveals the
    raw MCP tool name, the input JSON, and a truncated output. Output
    is capped at 2000 chars in the transcript so a noisy search result
    doesn't dominate; the full output is still on disk under
    `tool_calls/` (written by the MCP server's @logged_tool decorator).
    """
    icon = "❌" if is_error else "🔧"
    output_text = (output_text or "").strip()
    if len(output_text) > 2000:
        output_text = output_text[:2000] + f"\n…[truncated; full output in tool_calls/]"
    try:
        input_str = json.dumps(input_json, indent=2, ensure_ascii=False)
    except Exception:
        input_str = str(input_json)
    return (
        f"\n<details><summary>{icon} {op}</summary>\n\n"
        f"`{raw_name}`\n\n"
        f"**Input:**\n```json\n{input_str}\n```\n\n"
        f"**Output:**\n```\n{output_text}\n```\n\n"
        f"</details>\n"
    )


# ---------------------------------------------------------------------------
# File attachments (Demo path B: user drops a competition design PDF)
# ---------------------------------------------------------------------------

_ATTACHMENT_MAX_CHARS = 60_000   # ~10 dense pages; trimmed past that
_PDF_MIME = "application/pdf"


def _extract_attachment_text(element) -> tuple[str, str] | None:
    """Return (label, body_text) for one cl.File / cl.Pdf / cl.Text element.

    Returns None when the element isn't textual or we can't extract.
    Supported:
      - PDF      → pypdf text extraction, capped at _ATTACHMENT_MAX_CHARS.
      - .md/.txt → raw read.
    Other binary types (images, zip, …) are skipped silently — the
    orchestrator skill assumes text-only inputs at this stage.
    """
    path = getattr(element, "path", None)
    name = getattr(element, "name", None) or (Path(path).name if path else "<unknown>")
    mime = (getattr(element, "mime", "") or "").lower()
    if not path or not Path(path).exists():
        return None
    try:
        # PDF
        if mime == _PDF_MIME or name.lower().endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(path)
            pages = []
            for i, page in enumerate(reader.pages):
                try:
                    pages.append(page.extract_text() or "")
                except Exception as e:
                    pages.append(f"[page {i + 1}: extraction failed: {e}]")
            body = "\n\n".join(pages).strip()
            n_pages = len(reader.pages)
            label = f"{name} (PDF, {n_pages} pages)"
        # Plain text / markdown
        elif mime in ("text/plain", "text/markdown") or name.lower().endswith((".md", ".txt")):
            body = Path(path).read_text(encoding="utf-8", errors="replace")
            label = f"{name} ({len(body):,} chars)"
        else:
            return None
    except Exception as e:
        log.warning("attachment extraction for %s failed: %s", name, e)
        return None

    if not body.strip():
        return (label, "[empty after text extraction]")
    if len(body) > _ATTACHMENT_MAX_CHARS:
        body = body[:_ATTACHMENT_MAX_CHARS] + (
            f"\n\n[…truncated at {_ATTACHMENT_MAX_CHARS:,} chars; "
            f"full file on disk under run_dir/uploads/]"
        )
    return (label, body)


# ---------------------------------------------------------------------------
# Right-side notebook panel
#
# The agent builds <run>/starting_kit.ipynb stage by stage via the
# autocodabench_nb_* MCP tools. After every assistant turn we render
# the on-disk notebook (with whatever outputs the kernel has produced
# so far) to HTML and put it in the side panel — the user sees the
# notebook materialise in real time. Files (transcript, cost,
# proposals later in PR4) also live in the same panel as siblings.
# ---------------------------------------------------------------------------

def _render_notebook_html(run_dir: Path) -> str | None:
    """Read <run>/starting_kit.ipynb and produce GitHub-style HTML.

    Uses nbconvert's `classic` template — full Jupyter-classic CSS:
    cell borders, In[]/Out[] prompts, Pygments syntax highlighting,
    monospace code, indented outputs. The `basic` template we used
    before was a structural skeleton only and looked like plain text.

    Returns None if the file doesn't exist yet (no cells written),
    so the caller can skip the sidebar update on early turns.
    """
    nb_path = run_dir / "starting_kit.ipynb"
    if not nb_path.is_file():
        return None
    try:
        import nbformat
        from nbconvert import HTMLExporter
        nb = nbformat.read(nb_path, as_version=4)
        if not nb.cells:
            return None
        # `classic` ships with stock nbconvert and embeds enough CSS
        # (cell wrappers, Pygments) for the output to look like a
        # rendered Jupyter notebook on GitHub. No external scripts
        # required, which keeps the sandboxed iframe happy.
        exporter = HTMLExporter(template_name="classic")
        body, _ = exporter.from_notebook_node(nb)
        return body
    except Exception as e:
        log.warning("notebook render failed: %s", e)
        return None


# Where chat.js fetches per-session files from. The iframe in the
# persistent right panel points at `<PUBLIC_SESSIONS>/<sid>/notebook.html`
# (Chainlit serves `web/public/` as static `/public/`).
_PUBLIC_DIR = Path(__file__).resolve().parent / "public"
_PUBLIC_SESSIONS = _PUBLIC_DIR / "sessions"


def _public_session_dir(session_id: str) -> Path:
    p = _PUBLIC_SESSIONS / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _find_bundle_zip(run_dir: Path) -> Path | None:
    """Locate the most-recent .zip the agent's stage-8 packaging wrote.

    Bundles live under `auto_codabench/bundles/<slug>/<slug>.zip` (the
    bundle-write tools default), not inside the run dir. We pick the
    most-recently-modified one — there's normally one slug per run.
    """
    bundles_root = REPO_ROOT / "auto_codabench" / "bundles"
    if not bundles_root.is_dir():
        return None
    candidates = list(bundles_root.glob("*/*.zip"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _write_public_artifacts(run_dir: Path, session_id: str) -> None:
    """Drop the notebook HTML + a small file-manifest into web/public/.

    The persistent right panel (injected by chat.js) reads these via
    plain HTTP GET against `/public/sessions/<sid>/...`, so it never
    relies on Chainlit's drawer/element machinery.

    Files written:
      - notebook.html       — full nbconvert HTML of starting_kit.ipynb,
                              or a small placeholder before any cells.
      - manifest.json       — list of additional files (transcript.md,
                              cost.jsonl, specs/*.md) with their relative
                              public URLs.
      - transcript.html     — sanitised render of transcript.md, opened
                              from the manifest's chip.
      - cost.html           — same, for cost.jsonl.
      - specs/<name>.html   — one per spec file.
    """
    try:
        out = _public_session_dir(session_id)

        # --- notebook ---
        nb_html = _render_notebook_html(run_dir)
        if nb_html is None:
            nb_html = (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<style>body{font:14px/1.5 -apple-system,sans-serif;"
                "padding:24px;color:#555}em{color:#888}</style>"
                "</head><body><h2>📓 starting_kit.ipynb</h2>"
                "<p><em>The notebook will appear here as the agent writes "
                "and executes cells. Right now it's empty.</em></p>"
                "</body></html>"
            )
        (out / "notebook.html").write_text(nb_html, encoding="utf-8")

        # --- transcript ---
        transcript = run_dir / "transcript.md"
        if transcript.is_file() and transcript.stat().st_size > 0:
            try:
                import markdown as _md_lib  # type: ignore
                rendered = _md_lib.markdown(
                    transcript.read_text(encoding="utf-8", errors="replace"),
                    extensions=["fenced_code", "tables"],
                )
            except Exception:
                rendered = (
                    "<pre style='white-space:pre-wrap'>"
                    + transcript.read_text(encoding="utf-8", errors="replace")
                    + "</pre>"
                )
            (out / "transcript.html").write_text(
                "<!doctype html><meta charset='utf-8'>"
                "<style>body{font:14px/1.5 -apple-system,sans-serif;"
                "padding:18px;color:#222;max-width:80ch;margin:0 auto}"
                "pre{background:#f6f8fa;padding:12px;border-radius:6px;"
                "overflow:auto}code{font-family:ui-monospace,Menlo,Consolas;"
                "font-size:12.5px}h1,h2,h3{margin-top:1.5em}</style>"
                "<title>transcript.md</title>" + rendered,
                encoding="utf-8",
            )

        # --- cost.jsonl ---
        cost = run_dir / "cost.jsonl"
        if cost.is_file() and cost.stat().st_size > 0:
            (out / "cost.html").write_text(
                "<!doctype html><meta charset='utf-8'>"
                "<style>body{font:13px/1.4 ui-monospace,Menlo;"
                "padding:18px;background:#0d1117;color:#c9d1d9}</style>"
                "<title>cost.jsonl</title><pre>"
                + cost.read_text(encoding="utf-8", errors="replace")
                + "</pre>",
                encoding="utf-8",
            )

        # --- specs/*.md ---
        specs_in = run_dir / "specs"
        specs_out = out / "specs"
        specs_out.mkdir(exist_ok=True)
        for spec_md in (specs_in.glob("*.md") if specs_in.is_dir() else []):
            try:
                import markdown as _md_lib  # type: ignore
                rendered = _md_lib.markdown(
                    spec_md.read_text(encoding="utf-8", errors="replace"),
                    extensions=["fenced_code", "tables"],
                )
            except Exception:
                rendered = (
                    "<pre style='white-space:pre-wrap'>"
                    + spec_md.read_text(encoding="utf-8", errors="replace")
                    + "</pre>"
                )
            (specs_out / (spec_md.stem + ".html")).write_text(
                "<!doctype html><meta charset='utf-8'>"
                "<style>body{font:14px/1.5 -apple-system,sans-serif;"
                "padding:18px;color:#222;max-width:80ch;margin:0 auto}"
                "pre{background:#f6f8fa;padding:12px;border-radius:6px;"
                "overflow:auto}code{font-family:ui-monospace,Menlo,Consolas;"
                "font-size:12.5px}h1,h2,h3{margin-top:1.5em}</style>"
                f"<title>{spec_md.name}</title>" + rendered,
                encoding="utf-8",
            )

        # --- bundle.zip: copy to public dir so it's downloadable ---
        bundle_src = _find_bundle_zip(run_dir)
        bundle_pub = out / "bundle.zip"
        if bundle_src is not None:
            try:
                shutil.copyfile(bundle_src, bundle_pub)
            except Exception as e:
                log.warning("bundle copy failed: %s", e)

        # --- manifest.json: drives the panel's tab strip ---
        # Each file gets a `tag` (size + mtime) so chat.js can detect
        # actual content changes and skip reloads otherwise — without
        # this, the iframe reloaded every 3.5 s and the user's
        # scroll position jumped back to the top while reading.
        def _tag(p: Path) -> str:
            try:
                st = p.stat()
                return f"{st.st_size}-{int(st.st_mtime)}"
            except Exception:
                return "0-0"

        manifest = {
            "session_id": session_id,
            "updated_at": _utc_now(),
            "files": [
                {
                    "name":  "📓 starting_kit.ipynb",
                    "url":   f"/public/sessions/{session_id}/notebook.html",
                    "kind":  "notebook",
                    "ready": nb_html is not None and "empty" not in nb_html[:200],
                    "tag":   _tag(out / "notebook.html"),
                },
            ],
        }
        if bundle_pub.is_file():
            manifest["files"].append({
                "name": "📦 bundle.zip (download)",
                "url":  f"/public/sessions/{session_id}/bundle.zip",
                "kind": "bundle",
                "ready": True,
                "tag":  _tag(bundle_pub),
            })
        if (out / "transcript.html").is_file():
            manifest["files"].append({
                "name": "📄 transcript.md",
                "url":  f"/public/sessions/{session_id}/transcript.html",
                "kind": "transcript",
                "ready": True,
                "tag":  _tag(out / "transcript.html"),
            })
        if (out / "cost.html").is_file():
            manifest["files"].append({
                "name": "💰 cost.jsonl",
                "url":  f"/public/sessions/{session_id}/cost.html",
                "kind": "cost",
                "ready": True,
                "tag":  _tag(out / "cost.html"),
            })
        for spec_html in sorted(specs_out.glob("*.html")):
            manifest["files"].append({
                "name": f"📄 specs/{spec_html.stem}.md",
                "url":  f"/public/sessions/{session_id}/specs/{spec_html.name}",
                "kind": "spec",
                "ready": True,
                "tag":  _tag(spec_html),
            })
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    except Exception as e:
        log.warning("public artifacts write failed: %s", e)


# ---------------------------------------------------------------------------
# Phase state (drives the top phase bar in chat.js)
# ---------------------------------------------------------------------------

def _phase_artifact_exists(run_dir: Path, phase: str) -> bool:
    """Has this phase produced its locked artifact yet?

    PLAN   → specs/implementation_plan.md (or implementation_plan.md as
             a legacy fallback).
    KIT    → starting_kit.ipynb with at least one cell.
    BUNDLE → any *.zip under auto_codabench/bundles/.
    """
    if phase == PHASE_PLAN:
        return ((run_dir / "specs" / "implementation_plan.md").is_file()
                or (run_dir / "implementation_plan.md").is_file())
    if phase == PHASE_KIT:
        nb = run_dir / "starting_kit.ipynb"
        if not nb.is_file():
            return False
        try:
            import nbformat
            return bool(nbformat.read(nb, as_version=4).cells)
        except Exception:
            return nb.stat().st_size > 100
    if phase == PHASE_BUNDLE:
        return _find_bundle_zip(run_dir) is not None
    return False


def _phase_status(phase: str, current: str, history: list[str],
                  artifact_exists: bool) -> str:
    """One of: 'active' (current phase), 'locked' (done, not current),
    'pending' (not yet reached / no artifact)."""
    if phase == current:
        return "active"
    # Index-based ordering so back-nav lands in a coherent state.
    pi = PHASE_ORDER.index(phase)
    ci = PHASE_ORDER.index(current)
    if pi < ci or artifact_exists or (phase in history):
        return "locked"
    return "pending"


def _write_phase_state(run_dir: Path, session_id: str) -> None:
    """Drop web/public/sessions/<sid>/phase_state.json for chat.js.

    Polled by the phase bar every ~3.5 s. Cheap to compute on every
    turn: 3 disk stats plus a small JSON write.
    """
    try:
        out = _public_session_dir(session_id)
        current  = cl.user_session.get("phase") or PHASE_PLAN
        history  = list(cl.user_session.get("phase_history") or [])
        in_tok   = int(cl.user_session.get("last_input_tokens") or 0)
        out_tok  = int(cl.user_session.get("last_output_tokens") or 0)
        cum_cost = float(cl.user_session.get("cum_cost_usd") or 0.0)

        phases_payload = []
        for ph in PHASE_ORDER:
            exists = _phase_artifact_exists(run_dir, ph)
            phases_payload.append({
                "id":       ph,
                "title":    PHASE_TITLE[ph],
                "artifact": PHASE_ARTIFACT[ph],
                "exists":   exists,
                "status":   _phase_status(ph, current, history, exists),
            })
        # Forward advance is only enabled when the CURRENT phase has an
        # artifact on disk (don't let the user advance to Phase 2 with
        # no plan written).
        cur_idx       = PHASE_ORDER.index(current)
        next_phase    = (PHASE_ORDER[cur_idx + 1]
                         if cur_idx + 1 < len(PHASE_ORDER) else None)
        can_advance   = (next_phase is not None
                         and _phase_artifact_exists(run_dir, current))

        payload = {
            "session_id":   session_id,
            "updated_at":   _utc_now(),
            "current":      current,
            "next":         next_phase,
            "can_advance":  can_advance,
            "phases":       phases_payload,
            "context": {
                "input_tokens":  in_tok,
                "output_tokens": out_tok,
                "max_tokens":    CONTEXT_WINDOW_TOKENS,
                "pct":           round(100.0 * in_tok / CONTEXT_WINDOW_TOKENS, 1),
            },
            "cost": {
                "cumulative_usd": round(cum_cost, 4),
                "budget_usd":     MAX_USD_PER_SESSION,
                "pct":            round(100.0 * cum_cost / MAX_USD_PER_SESSION, 1)
                                  if MAX_USD_PER_SESSION > 0 else 0.0,
            },
        }
        (out / "phase_state.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("phase_state write failed: %s", e)


_PROGRESS_ICONS = {
    "ready":   "⏵",
    "running": "🟡",
    "done":    "✅",
    "failed":  "❌",
}


def _render_progress_markdown(section_status: dict[str, str]) -> str:
    """Render the 'Starting Kit development progress' message body."""
    overall_done = all(s == "done" for s in section_status.values() if s)
    any_running  = any(s == "running" for s in section_status.values())
    any_failed   = any(s == "failed"  for s in section_status.values())
    if any_failed:
        headline = "**🛠 Starting Kit development progress — ⚠️ stage failed**"
    elif overall_done and section_status:
        headline = "**🛠 Starting Kit development progress — ✅ complete**"
    elif any_running:
        headline = "**🛠 Starting Kit development progress — in progress**"
    else:
        headline = "**🛠 Starting Kit development progress**"
    lines = [headline, ""]
    for stage, title in STAGE_TITLES:
        if stage == "0.roadmap":
            continue
        st = section_status.get(stage, "ready")
        icon = _PROGRESS_ICONS.get(st, "⏵")
        suffix = " — *running*" if st == "running" else ""
        lines.append(f"{icon} {title}{suffix}")
    return "\n".join(lines)


async def _refresh_task_list(run_dir: Path) -> None:
    """Drive the in-chat 'Starting Kit development progress' message.

    Previously this rendered a cl.TaskList in the right sidebar — that
    UI was confusing (rows looked like generic 'Tasks'). Progress is
    now a single, in-chat message that we keep updating as section
    statuses change. Lazy-created on the first build event for any of
    `1.task` ... `8.bundle`.
    """
    events_path = run_dir / "events.jsonl"
    if not events_path.is_file():
        return

    # --- Tail events.jsonl from last cursor ---
    cursor: int = int(cl.user_session.get("events_cursor") or 0)
    try:
        with events_path.open("r", encoding="utf-8") as f:
            f.seek(cursor)
            new_text = f.read()
            new_cursor = f.tell()
    except Exception as e:
        log.warning("events.jsonl read failed: %s", e)
        return
    cl.user_session.set("events_cursor", new_cursor)

    # Parse new events once into a list.
    parsed: list[dict] = []
    for line in new_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed.append(json.loads(line))
        except Exception:
            continue

    # --- Detect "should we show progress yet?" on first build event ---
    show_progress = bool(cl.user_session.get("show_progress") or False)
    if not show_progress:
        for ev in parsed:
            payload = ev.get("payload") or {}
            stage = payload.get("stage")
            if stage and stage in _STAGE_BY_ID:
                si = _STAGE_BY_ID[stage]
                if ev.get("kind") in ("stage_started", "stage_done",
                                      "stage_approved", "stage_failed") and 1 <= si <= 8:
                    show_progress = True
                    cl.user_session.set("show_progress", True)
                    break

    if not show_progress:
        # Pre-PR4 legacy fallback still applies for old sessions.
        await _refresh_legacy_bundle_gate(run_dir)
        return

    # --- Apply event status changes to the section_status dict ---
    section_status: dict[str, str] = dict(cl.user_session.get("section_status") or {})
    if not section_status:
        # Initialize all design sections as "ready" the first time we
        # decide to render progress.
        for stage, _ in STAGE_TITLES:
            if stage == "0.roadmap":
                continue
            section_status[stage] = "ready"

    changed = False
    for ev in parsed:
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        stage = payload.get("stage")
        if not stage or stage not in section_status:
            continue
        old = section_status[stage]
        if kind == "stage_started" and old != "done":
            section_status[stage] = "running"; changed = True
        elif kind in ("stage_done", "stage_approved"):
            section_status[stage] = "done"; changed = True
        elif kind == "stage_failed":
            section_status[stage] = "failed"; changed = True
    cl.user_session.set("section_status", section_status)

    # --- Send / update the progress message ---
    progress_msg = cl.user_session.get("progress_msg")
    body = _render_progress_markdown(section_status)
    if progress_msg is None:
        progress_msg = cl.Message(content=body, author="autocodabench")
        await progress_msg.send()
        cl.user_session.set("progress_msg", progress_msg)
    elif changed:
        progress_msg.content = body
        try:
            await progress_msg.update()
        except Exception as e:
            log.warning("progress msg update failed: %s", e)

    # Section-picker gate trigger on any DONE.
    for stage_id, st in section_status.items():
        if st == "done":
            await _maybe_offer_stage_gate(run_dir, stage_id)

    await _refresh_legacy_bundle_gate(run_dir)


def _augment_user_message(run_dir: Path, msg: "cl.Message") -> str:
    """Mix in extracted attachment text so Claude sees PDF / md content.

    Also copies each successfully-extracted upload into
    `<run_dir>/uploads/<name>` so the agent could later `Read` it
    directly (the Read tool is in `allowed_tools`).
    """
    elements = getattr(msg, "elements", None) or []
    if not elements:
        return msg.content or ""

    uploads_dir = run_dir / "uploads"
    uploads_dir.mkdir(exist_ok=True)
    extracted_blocks: list[str] = []

    for el in elements:
        result = _extract_attachment_text(el)
        if result is None:
            continue
        label, body = result
        # Mirror the file into run_dir/uploads/ so the agent can re-read
        # the original later via the Read tool if needed.
        src = getattr(el, "path", None)
        if src and Path(src).exists():
            try:
                shutil.copy2(src, uploads_dir / Path(src).name)
            except Exception as e:
                log.warning("failed to mirror %s: %s", src, e)
        extracted_blocks.append(
            f"<attached_document name=\"{label}\">\n{body}\n</attached_document>"
        )

    if not extracted_blocks:
        return msg.content or ""

    head = (
        f"_The user attached {len(extracted_blocks)} document(s). The full "
        f"extracted text is included below; treat this per orchestrator §1.6 "
        f"(PDF intake — map onto the §1.0 roadmap, ask only for missing rows)._"
    )
    return f"{msg.content or ''}\n\n{head}\n\n" + "\n\n".join(extracted_blocks)


def _log_cost(run_dir: Path, *, turn_cost: float, cumulative: float) -> None:
    """Append one JSON line to <run_dir>/cost.jsonl per assistant turn.

    Aggregated offline by joining all sessions' cost.jsonl files — gives
    a quick per-collaborator / per-session / per-model cost breakdown.
    """
    line = json.dumps({
        "at":         _utc_now(),
        "turn_cost":  round(turn_cost, 6),
        "cumulative": round(cumulative, 6),
        "model":      DEFAULT_MODEL,
        "session":    cl.user_session.get("session_id"),
        "user":       (cl.user_session.get("user").identifier
                       if cl.user_session.get("user") else "anon"),
    })
    (run_dir / "cost.jsonl").open("a", encoding="utf-8").write(line + "\n")


# ---------------------------------------------------------------------------
# Per-session persistence: upload run_dir to a private HF Dataset repo.
#
# Why HF Dataset (vs. push to a git branch): the Space already speaks HF
# natively and the upload API is just one HTTP call per file — no git
# config, no commits, no merge-conflict risk under concurrent sessions.
# Each session lives under its own folder so multiple collaborators can
# write at once without stepping on each other.
#
# Setup checklist (once, on the Space owner's account):
#   1. https://huggingface.co/new-dataset  -> name `autocodabench-runs`, set Private.
#   2. https://huggingface.co/settings/tokens -> new token with `write` scope.
#   3. On the Space: Settings -> Variables and secrets -> add Secret HF_TOKEN
#      with the token's value. (Optional Variable AUTOCODABENCH_RUNS_REPO to
#      point at a non-default repo id.)
# ---------------------------------------------------------------------------

async def _persist_to_hf(run_dir: Path) -> None:
    """Best-effort upload of run_dir to the private HF Dataset repo.

    No-ops cleanly if HF_TOKEN isn't set (local dev) or if the network is
    unreachable — we never want analytics shipping to break a live chat.
    """
    if not HF_TOKEN:
        return  # local dev or operator hasn't configured the secret
    if not run_dir.exists():
        return

    # One-time repair for transcripts written by the older format,
    # whose leading `---` made markdown renderers eat the first user
    # prompt as YAML frontmatter. We detect the broken shape and
    # prepend a title so the renderer stops treating it as frontmatter.
    try:
        tpath = run_dir / "transcript.md"
        if tpath.is_file():
            body = tpath.read_text(encoding="utf-8")
            if body.startswith("\n---\n") or body.startswith("---\n"):
                fix = (
                    f"# Transcript — {run_dir.name}\n\n"
                    f"_(repaired: leading `---` was being parsed as YAML "
                    f"frontmatter and hiding the first user prompt)_\n"
                )
                tpath.write_text(fix + body, encoding="utf-8")
    except Exception as e:
        log.warning("transcript repair for %s failed: %s", run_dir.name, e)
    try:
        # Late import so the module can be installed at build time without
        # the rest of the app caring whether it's actually used.
        from huggingface_hub import HfApi

        def _do_upload() -> None:
            api = HfApi(token=HF_TOKEN)
            # Create the repo lazily — idempotent thanks to exist_ok.
            api.create_repo(
                repo_id=HF_RUNS_REPO,
                repo_type="dataset",
                private=True,
                exist_ok=True,
            )
            api.upload_folder(
                folder_path=str(run_dir),
                repo_id=HF_RUNS_REPO,
                repo_type="dataset",
                path_in_repo=run_dir.name,
                commit_message=f"sync {run_dir.name}",
                # Avoid hammering HF with binary artifacts in case anything
                # ever ends up here that doesn't belong. We're shipping
                # text-only data (markdown, json, jsonl, py, yaml).
                allow_patterns=["*.md", "*.jsonl", "*.json", "*.txt",
                                "*.py", "*.yaml", "*.yml", "*.log",
                                # The per-session starting kit notebook —
                                # outputs are embedded, so the dataset
                                # contains the *executed* state.
                                "*.ipynb"],
            )

        # Run blocking HF I/O off the event loop.
        await asyncio.to_thread(_do_upload)
    except Exception as e:
        # Network blip, token rotated, repo deleted — log and move on.
        log.warning("HF persist for %s failed: %s", run_dir.name, e)
