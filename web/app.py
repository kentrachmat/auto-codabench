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

RUNS_ROOT = REPO_ROOT / "auto_codabench" / "runs"


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _system_prompt() -> str:
    """Load the orchestrator skill's body as the system prompt."""
    if not ORCHESTRATOR_SKILL.exists():
        return (
            "You are an orchestrator that helps researchers design Codabench "
            "competitions. (ORCHESTRATOR_SKILL not found at "
            f"{ORCHESTRATOR_SKILL}; falling back to minimal prompt.)"
        )
    body = ORCHESTRATOR_SKILL.read_text(encoding="utf-8")
    # Strip the YAML frontmatter (Anthropic does not need it in the prompt).
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:].lstrip()
    return body


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

    # 1. Per-session isolated run dir
    session_id = uuid.uuid4().hex[:12]
    user = cl.user_session.get("user")
    user_id = (user.identifier if user else "anon").replace("/", "_")
    runtime_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = RUNS_ROOT / f"web_{user_id}_{runtime_id}_{session_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    cl.user_session.set("run_dir", str(run_dir))
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("started_at", _utc_now())

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

    # 3. Configure the Claude Agent SDK client
    options = ClaudeAgentOptions(
        model=DEFAULT_MODEL,
        system_prompt=_system_prompt(),
        mcp_servers=_mcp_servers(run_dir),
        max_budget_usd=MAX_USD_PER_SESSION,
        permission_mode="bypassPermissions",  # tools run without per-call prompts
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "AUTOCODABENCH_RUN_DIR": str(run_dir),
        },
        # Allow Claude to invoke the autocodabench + alex-mcp tools and
        # standard file-reading tools, but lock out shell / network freely.
        allowed_tools=[
            "mcp__autocodabench__*",
            "mcp__alex-mcp__*",
            "Read",
            "Grep",
            "Glob",
        ],
    )

    client = ClaudeSDKClient(options=options)
    await client.connect()
    cl.user_session.set("client", client)

    # 4. Greeting — this contains READY_PHRASE ("Tell me a competition
    # idea") which is the signal chat.js watches for to drop the banner
    # and unlock the input. Keep that exact phrase in the first line.
    await cl.Message(
        content=(
            "**AutoCodabench — Phase 1A: proposal crystallization**\n\n"
            "Tell me a competition idea — a sentence is enough — and I'll "
            "explore the design space with you, citing the literature as we go.\n\n"
            f"_session `{session_id}` · model `{DEFAULT_MODEL}` · "
            f"budget ${MAX_USD_PER_SESSION:.2f}_"
        ),
        author="autocodabench",
    ).send()

    cl.user_session.set("ready", True)


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

    # Persist the entire run_dir to a private HF Dataset, async — so a
    # slow network request doesn't block the next user turn. The user
    # closing the tab mid-turn means we lose at most one turn's data.
    asyncio.create_task(_persist_to_hf(run_dir))


@cl.on_chat_end
async def on_chat_end():
    run_dir_str = cl.user_session.get("run_dir")
    if run_dir_str:
        # Final flush — wait for this one (chat is over, latency is fine).
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
    """
    role_header = {
        "user": "## 👤 user — ",
        "claude": "## 🤖 autocodabench — ",
    }.get(role, f"## {role} — ")
    line = f"\n---\n\n{role_header}{_utc_now()}\n\n{text}\n"
    (run_dir / "transcript.md").open("a", encoding="utf-8").write(line)


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
                                "*.py", "*.yaml", "*.yml", "*.log"],
            )

        # Run blocking HF I/O off the event loop.
        await asyncio.to_thread(_do_upload)
    except Exception as e:
        # Network blip, token rotated, repo deleted — log and move on.
        log.warning("HF persist for %s failed: %s", run_dir.name, e)
