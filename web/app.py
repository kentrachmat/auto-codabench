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
MAX_USD_PER_SESSION = float(os.environ.get("MAX_USD_PER_SESSION", "2.0"))
PYTHON_BIN = os.environ.get("AUTOCODABENCH_PYTHON", sys.executable)

# Two phases shown in the UI. Planning is the orchestrator (proposal +
# implementation specs); Implementation is Phase C of the orchestrator
# (bundle writes + Codabench upload). On the CLI, Phase C runs in a
# *fresh* Claude session; on the web UI we don't have that option, so
# the user clicks a big confirm button and we recreate the SDK client
# in place — same run_dir, new tool allowlist, new system prompt.
PHASE_PLANNING       = "planning"
PHASE_IMPLEMENTATION = "implementation"

_PLANNING_TOOLS = [
    "mcp__autocodabench__autocodabench_open_run",
    "mcp__autocodabench__autocodabench_current_run",
    "mcp__autocodabench__autocodabench_log_event",
    "mcp__autocodabench__autocodabench_snapshot_spec",
    # Notebook authoring tools — used throughout stages 1-7. Stage 8
    # adds the bundle-write tools via _IMPLEMENTATION_TOOLS.
    "mcp__autocodabench__autocodabench_nb_init",
    "mcp__autocodabench__autocodabench_nb_write_cell",
    "mcp__autocodabench__autocodabench_nb_run_stage",
    "mcp__autocodabench__autocodabench_nb_reset_to_stage",
    "mcp__autocodabench__autocodabench_nb_render_html",
    "mcp__autocodabench__autocodabench_nb_shutdown",
    "mcp__alex-mcp__*",
    "Read", "Grep", "Glob",
]
_IMPLEMENTATION_TOOLS = [
    # Bundle-write side of the autocodabench MCP — only unlocked once
    # the user explicitly steps into the implementation phase.
    "mcp__autocodabench__*",
    "mcp__alex-mcp__*",
    "Read", "Grep", "Glob",
]

# Per-session run dirs are uploaded to this private HF Dataset repo
# (cost.jsonl, transcript.md, tool_calls/, specs/, events.jsonl, …).
# Set HF_TOKEN as a Repository Secret on the Space to enable uploads;
# when missing (local dev), uploads are silently skipped.
HF_RUNS_REPO = os.environ.get("AUTOCODABENCH_RUNS_REPO", "ktgiahieu/autocodabench-runs")
HF_TOKEN = os.environ.get("HF_TOKEN", "")

SKILLS_ROOT = REPO_ROOT / "auto_codabench" / "skills"
ORCHESTRATOR_SKILL = SKILLS_ROOT / "autocodabench-orchestrator" / "SKILL.md"
# Fallback if symlinks not yet installed: read directly from source.
if not ORCHESTRATOR_SKILL.exists():
    ORCHESTRATOR_SKILL = SKILLS_ROOT / "orchestrator" / "SKILL.md"
IMPLEMENT_SKILL = SKILLS_ROOT / "autocodabench-implement" / "SKILL.md"

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


def _system_prompt(*, phase: str = PHASE_PLANNING) -> str:
    """Return the per-phase system prompt.

    Each phase loads its OWN skill body — we don't try to teach the agent
    both at once. The orchestrator skill is for planning (proposal +
    specs); the autocodabench-implement skill is for execution.

    A short web-UI footer is appended so the model knows the phase
    switch is button-driven (no "open a new chat" in either direction).
    """
    if phase == PHASE_PLANNING:
        base = _read_skill(ORCHESTRATOR_SKILL) or (
            "You are an orchestrator that helps researchers design Codabench "
            "competitions. (ORCHESTRATOR_SKILL missing.)"
        )
        footer = (
            "\n\n---\n\n"
            "## Web UI runtime note\n\n"
            "You are running inside the AutoCodabench web UI. The user cannot "
            "open a fresh chat to enter Phase C — instead, the UI shows a big "
            "**START IMPLEMENTATION** button as soon as `implementation_plan.md` "
            "is written. When you finish Phase B, do NOT tell the user to "
            "'open a new chat' — instead say:\n\n"
            "> ✅ Implementation plan written. A big **START IMPLEMENTATION** "
            "> button has appeared below this message — click it to begin "
            "> Phase C in this same session. Switching is irreversible in "
            "> the current session.\n\n"
            "Stay in PLANNING mode until that button is clicked."
        )
    else:  # PHASE_IMPLEMENTATION
        base = _read_skill(IMPLEMENT_SKILL) or (
            "You are executing Phase C of an AutoCodabench run. "
            "(autocodabench-implement skill missing — please contact the operator.)"
        )
        footer = (
            "\n\n---\n\n"
            "## Web UI runtime note (Phase C)\n\n"
            "You are running inside the AutoCodabench web UI in Phase C. The "
            "user reached this mode by clicking **START IMPLEMENTATION**, "
            "which has already been confirmed. `/agents` is NOT available "
            "here — execute the plan serially in this chat. The user will "
            "see your tool chips (`Running …`) for each step.\n\n"
            "Start now: call `autocodabench_current_run` to find the run "
            "dir, then follow §1–§6 of the autocodabench-implement skill. "
            "Don't wait for additional instructions — the user expects you "
            "to begin immediately."
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
    # write `implementation_plan.md` there, leaving _maybe_offer_phase_switch
    # polling the wrong path and the START IMPLEMENTATION button never
    # appearing.
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

    # 3. Configure the Claude Agent SDK client for the PLANNING phase.
    cl.user_session.set("phase", PHASE_PLANNING)
    cl.user_session.set("switch_offered", False)  # so we don't spam the button

    client = ClaudeSDKClient(options=_build_options(run_dir, PHASE_PLANNING))
    await client.connect()
    cl.user_session.set("client", client)

    # 4. Greeting — this contains READY_PHRASE ("Tell me a competition
    # idea") which is the signal chat.js watches for to drop the banner
    # and unlock the input. Keep that exact phrase in the first line.
    # We also lay out the two-phase contract up front so the user knows
    # the irreversible step is coming.
    await cl.Message(
        content=(
            "# 🧠 PLANNING phase — proposal crystallization\n\n"
            "Tell me a competition idea — a sentence is enough — and I'll "
            "explore the design space with you, citing the literature as "
            "we go. You can also drop a PDF / markdown design doc and I'll "
            "fill in only the gaps.\n\n"
            "### How this app works\n\n"
            "Two phases, one session:\n\n"
            "1. **🧠 PLANNING** *(you are here)* — we draft "
            "   `project_proposal.md` and six implementation specs. "
            "   No bundle files are written yet.\n"
            "2. **🛠 IMPLEMENTATION** — once `implementation_plan.md` "
            "   exists, a big **START IMPLEMENTATION** button will "
            "   appear below this chat. Clicking it switches the agent "
            "   into Phase C: writing the Codabench bundle, validating, "
            "   zipping, and (optionally) uploading.\n\n"
            "> **⚠️ Switching is IRREVERSIBLE in this session.** The agent "
            "> rebuilds with bundle-write tools and a Phase-C system "
            "> prompt; you cannot return to planning. If you want a "
            "> different design after that, refresh the page to start a "
            "> brand-new session.\n\n"
            f"_session `{session_id}` · model `{DEFAULT_MODEL}` · "
            f"budget ${MAX_USD_PER_SESSION:.2f}_"
        ),
        author="autocodabench",
    ).send()

    cl.user_session.set("ready", True)


# ---------------------------------------------------------------------------
# SDK options builder (per phase)
# ---------------------------------------------------------------------------

def _build_options(run_dir: Path, phase: str) -> ClaudeAgentOptions:
    """Build the ClaudeAgentOptions for the requested phase.

    Planning uses a tight allowlist (no bundle writes). Implementation
    unlocks the full autocodabench MCP namespace including bundle write
    + Codabench upload tools.
    """
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
        allowed_tools=(_PLANNING_TOOLS if phase == PHASE_PLANNING
                       else _IMPLEMENTATION_TOOLS),
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
                if cost:
                    await response_msg.stream_token(
                        f"\n\n_turn cost ≈ ${cost:.3f}; session total ≈ ${cum:.2f} / "
                        f"${MAX_USD_PER_SESSION:.2f}_"
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

    # Refresh the side panel: notebook HTML + any spec/log files.
    # We pass response_msg so the file chips render *inline under the
    # response*; clicking a chip slides the right drawer open with
    # the file content. Without this, users would have to discover
    # the sidebar via Chainlit's chrome — which is non-obvious.
    await _refresh_side_panel(run_dir, attach_to=response_msg)

    # Persist the entire run_dir to a private HF Dataset, async — so a
    # slow network request doesn't block the next user turn. The user
    # closing the tab mid-turn means we lose at most one turn's data.
    asyncio.create_task(_persist_to_hf(run_dir))

    # If we're still in PLANNING and the orchestrator just produced
    # `implementation_plan.md`, surface the big phase-switch button.
    # `switch_offered` is sticky — we don't pester the user after they
    # see it once; if they want it back they can re-ask the agent.
    await _maybe_offer_phase_switch(run_dir)


# ---------------------------------------------------------------------------
# Phase-switch UI (planning -> implementation)
#
# The orchestrator writes `<run>/implementation_plan.md` at the end of
# Phase B. We poll for that file at the end of each turn and, the first
# time we see it while still in planning mode, send a message carrying
# a big bold cl.Action. Clicking it shows a *second* confirm action;
# only the second click actually does the irreversible switch (rebuild
# the SDK client with Phase-C options + kickoff prompt).
# ---------------------------------------------------------------------------

async def _maybe_offer_phase_switch(run_dir: Path) -> None:
    if cl.user_session.get("phase") != PHASE_PLANNING:
        return
    if cl.user_session.get("switch_offered"):
        return
    # Check the web's run_dir first (the expected path). Then fall back
    # to runs/LATEST in case the agent somehow opened a sibling dir
    # despite meta.json adoption — the MCP server always updates the
    # LATEST symlink to point at whichever run is current. If we adopt
    # *that* run dir going forward, the action button payload + later
    # transcript writes also land in the right place.
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
    plan = next((p for p in candidates if p.exists()), None)
    if plan is None:
        return
    # If we hit it via LATEST (i.e. the agent worked in a different dir
    # despite our adoption hint), pivot the session's run_dir to wherever
    # the plan actually lives — every downstream artifact (transcript,
    # cost, HF upload) should follow the work, not the empty dir.
    effective_run_dir = plan.parent if plan.parent.name == "specs" else plan.parent
    if effective_run_dir.resolve() != run_dir.resolve():
        log.warning("phase-switch: plan found in %s, pivoting from %s",
                    effective_run_dir, run_dir)
        cl.user_session.set("run_dir", str(effective_run_dir))
        run_dir = effective_run_dir
    cl.user_session.set("switch_offered", True)
    actions = [
        cl.Action(
            name="ac_switch_to_impl",
            payload={"run_dir": str(run_dir)},
            label="🛠 START IMPLEMENTATION (irreversible)",
            tooltip="Rebuilds the agent with bundle-write tools enabled. "
                    "You cannot return to planning after this in the "
                    "current session.",
        ),
    ]
    await cl.Message(
        author="autocodabench",
        content=(
            "## ✅ Implementation plan is ready\n\n"
            "`implementation_plan.md` has been written. From here, two "
            "options:\n\n"
            "- **Stay in planning** to refine the proposal or specs. Just "
            "  keep chatting.\n"
            "- **Switch to implementation** with the big button below. "
            "  Once you click and confirm, the agent rebuilds with "
            "  bundle-write tools and a Phase-C system prompt. **You "
            "  cannot come back to planning** in this session.\n"
        ),
        actions=actions,
    ).send()


@cl.action_callback("ac_switch_to_impl")
async def _on_switch_to_impl(action: cl.Action):
    """First click: show the confirmation prompt with two big actions."""
    confirm_actions = [
        cl.Action(
            name="ac_confirm_switch",
            payload=action.payload or {},
            label="✅ YES — switch to IMPLEMENTATION",
            tooltip="Proceed. This is irreversible in this session.",
        ),
        cl.Action(
            name="ac_cancel_switch",
            payload={},
            label="❌ Cancel — keep planning",
        ),
    ]
    await cl.Message(
        author="autocodabench",
        content=(
            "## ⚠️ Confirm: this is irreversible in this session\n\n"
            "Clicking **YES** will:\n\n"
            "1. Disconnect the current planning client.\n"
            "2. Recreate the agent with bundle-write tools (`init_bundle`, "
            "   `write_competition_yaml`, `write_scoring_program`, "
            "   `attach_data`, `zip_bundle`, `upload_bundle`, …) and a "
            "   Phase-C system prompt.\n"
            "3. Kick the agent off on `implementation_plan.md`.\n\n"
            "You will **NOT** be able to return to planning in this "
            "session. To do another design, refresh the page for a fresh "
            "session."
        ),
        actions=confirm_actions,
    ).send()


@cl.action_callback("ac_cancel_switch")
async def _on_cancel_switch(action: cl.Action):
    cl.user_session.set("switch_offered", False)  # re-offer if asked
    await cl.Message(
        author="autocodabench",
        content="Cancelled. Still in **PLANNING**.",
    ).send()


@cl.action_callback("ac_confirm_switch")
async def _on_confirm_switch(action: cl.Action):
    """Irreversibly switch to Phase C: rebuild the client, kick off."""
    run_dir_str = (action.payload or {}).get("run_dir") or cl.user_session.get("run_dir")
    if not run_dir_str:
        await cl.Message(
            author="autocodabench",
            content="❌ Couldn't find the run dir to switch on. Refresh and try again.",
        ).send()
        return
    run_dir = Path(run_dir_str)

    # 1. Tear down the planning client.
    old_client: ClaudeSDKClient | None = cl.user_session.get("client")
    if old_client is not None:
        try:
            await old_client.disconnect()
        except Exception as e:
            log.warning("disconnect during phase switch failed: %s", e)

    # 2. Build a fresh Phase-C client. Same run_dir on disk, new tools.
    cl.user_session.set("phase", PHASE_IMPLEMENTATION)
    new_client = ClaudeSDKClient(options=_build_options(run_dir, PHASE_IMPLEMENTATION))
    await new_client.connect()
    cl.user_session.set("client", new_client)

    # 3. Tell the user, render a clear phase-change marker, then send
    #    the kickoff prompt to the agent. The agent's first action will
    #    be to read project_proposal.md + specs and start executing the
    #    plan.
    await cl.Message(
        author="autocodabench",
        content=(
            "# 🛠 IMPLEMENTATION phase\n\n"
            "Switched. The agent has bundle-write tools available and a "
            "Phase-C system prompt. It will now read the proposal + "
            "specs + plan and start writing the Codabench bundle. "
            "Watch the tool chips for `init_bundle`, "
            "`write_competition_yaml`, `write_scoring_program`, etc."
        ),
    ).send()

    _append_transcript(run_dir, role="user",
                       text="[ui] User clicked START IMPLEMENTATION — switching to Phase C.")

    # The system prompt is already the autocodabench-implement skill,
    # which tells the agent exactly what to do. A one-word kickoff is
    # all we need — the skill handles the rest.
    await _stream_one_turn(run_dir, "Begin.")


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
                if cost:
                    await response_msg.stream_token(
                        f"\n\n_turn cost ≈ ${cost:.3f}; session total ≈ "
                        f"${cum:.2f} / ${MAX_USD_PER_SESSION:.2f}_"
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
    # Same side-panel refresh as the regular on_message path so the
    # post-kickoff phase-C response also surfaces clickable chips.
    await _refresh_side_panel(run_dir, attach_to=response_msg)
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
    """Read <run>/starting_kit.ipynb and produce sanitised HTML.

    Returns None if the file doesn't exist yet (no cells written), so
    the caller can skip the sidebar update on early turns.
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
        exporter = HTMLExporter(template_name="basic")
        body, _ = exporter.from_notebook_node(nb)
        return body
    except Exception as e:
        log.warning("notebook render failed: %s", e)
        return None


def _collect_side_files(run_dir: Path) -> list["cl.Text"]:
    """Build the per-turn list of files for cl.ElementSidebar.

    Surfaces (in this order, when present): the executed notebook,
    transcript.md, cost.jsonl, every spec under specs/. Each is a
    cl.Text whose `display='side'` makes it open the right drawer
    when its chip is clicked from chat.
    """
    elements: list[cl.Text] = []
    nb_html = _render_notebook_html(run_dir)
    if nb_html is not None:
        elements.append(cl.Text(
            name="📓 starting_kit.ipynb",
            content=nb_html,
            display="side",
            language="html",
        ))
    for name in ("transcript.md", "cost.jsonl"):
        p = run_dir / name
        if p.is_file() and p.stat().st_size > 0:
            try:
                elements.append(cl.Text(
                    name=f"📄 {name}",
                    content=p.read_text(encoding="utf-8", errors="replace"),
                    display="side",
                    language="markdown" if name.endswith(".md") else "json",
                ))
            except Exception as e:
                log.warning("read %s for sidebar failed: %s", p, e)
    specs_dir = run_dir / "specs"
    if specs_dir.is_dir():
        for spec in sorted(specs_dir.glob("*.md")):
            try:
                elements.append(cl.Text(
                    name=f"📄 specs/{spec.name}",
                    content=spec.read_text(encoding="utf-8", errors="replace"),
                    display="side",
                    language="markdown",
                ))
            except Exception as e:
                log.warning("read %s for sidebar failed: %s", spec, e)
    return elements


async def _refresh_side_panel(run_dir: Path,
                              attach_to: "cl.Message | None" = None) -> None:
    """Push the current file set into the UI.

    Two-channel rendering so users always see the files:

      1. **Inline chips on `attach_to`** (a recently-sent assistant
         message). With Chainlit's `display="side"`, each `cl.Text`
         renders as a clickable chip in that message — click it,
         right-side drawer opens with the rendered file. This is the
         visible signal that artifacts are accumulating.

      2. **cl.ElementSidebar.set_elements** as a backup global view —
         in Chainlit 2.11 the sidebar has a chrome-level toggle, but
         it isn't always obvious; the inline chips are the primary
         affordance.

    Idempotent — safe to call after every turn.
    """
    try:
        elements = _collect_side_files(run_dir)
        if not elements:
            return
        # Channel 1 — attach to a freshly-sent assistant message so the
        # chips appear *under* the response. Chainlit only renders
        # element chips when the host message is updated after the
        # elements are attached.
        if attach_to is not None:
            try:
                attach_to.elements = elements
                await attach_to.update()
            except Exception as e:
                log.warning("attach elements to message failed: %s", e)
        # Channel 2 — populate the persistent ElementSidebar.
        try:
            await cl.ElementSidebar.set_title("📁 Session files")
            await cl.ElementSidebar.set_elements(elements)
        except Exception as e:
            log.warning("ElementSidebar set failed: %s", e)
    except Exception as e:
        log.warning("side panel refresh failed: %s", e)


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
