"""Shared agent response streaming for the AutoCodabench web UI.

Both the regular on_message handler and the synthetic phase-kickoff prompt
use the same streaming loop. Each turn renders as an inline, CLI-style activity
log inside the bot's own response bubble (see :class:`TurnView`): tool actions,
milestones/deviations, narration, and a cost footer — plus a transcript write.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import chainlit as cl
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLIJSONDecodeError,
    ProcessError,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from artifacts import CostLog, Transcript
from config import CONTEXT_WINDOW_TOKENS, DEFAULT_MODEL, MAX_USD_PER_SESSION

# Reuse the CLI's renderer helpers verbatim so the web activity log reads exactly
# like the terminal the user liked. These are pure, side-effect-free, stdlib-only.
from autocodabench.cli.progress import (
    _friendly_action,
    _is_parallel_cancellation,
    _short_tool_name,
)

log = logging.getLogger("autocodabench.web.streaming")

# A turn dies most often on HF not from a bug but from a transient drop of the
# CLI's OUTBOUND HTTPS stream to the Anthropic API ("API Error: The socket
# connection was closed unexpectedly"). That failure is on the api.anthropic.com
# side — the local Python↔CLI stdio connection is still alive — so simply
# re-issuing the same query recovers the turn AND keeps the session's context.
_TURN_RETRY_ERRORS = (CLIConnectionError, CLIJSONDecodeError, ProcessError)
_TURN_MAX_ATTEMPTS = 3  # 1 initial + 2 retries

# Substrings that mark a transient/retryable API or network failure surfaced as a
# generic exception (the CLI relays the underlying fetch error text verbatim).
_TRANSIENT_MARKERS = (
    "socket connection was closed",
    "socket hang up",
    "connection closed",
    "connection reset",
    "econnreset",
    "epipe",
    "etimedout",
    "network error",
    "api error",
    "overloaded",
    "timeout",
    "timed out",
)


def _is_transient_turn_error(exc: BaseException) -> bool:
    """True if *exc* looks like a retryable transient API/network drop."""
    if isinstance(exc, _TURN_RETRY_ERRORS):
        return True
    s = str(exc).lower()
    return any(m in s for m in _TRANSIENT_MARKERS)

# Tools we don't surface as their own log line — pure agent machinery that
# would only add clutter (file reads, searches, tool loading, skills).
_HIDDEN_TOOLS = {"Skill", "ToolSearch", "Read", "Grep", "Glob"}

# The agent addresses the user directly via autocodabench_log_event. These are
# the kinds we surface as visible log lines — mirrors the CLI progress renderer
# (src/autocodabench/cli/progress.py:_USER_MESSAGE_KINDS). "deviation" reports a
# departure from the locked plan and is highlighted.
_USER_MESSAGE_KINDS = {"progress", "milestone", "status", "deviation"}
_LOG_EVENT_TOOL = "mcp__autocodabench__autocodabench_log_event"

# Knight-rider blob for the "working" tail: one lit circle bouncing across a
# dim track. Equal-width glyphs so it reads cleanly in the proportional chat font.
_BLOB_WIDTH = 9
_BLOB_LIT = "●"
_BLOB_DIM = "○"


def _blob(frame: int) -> str:
    """One lit circle bouncing left↔right across a dim track (CLI knight-rider)."""
    period = 2 * (_BLOB_WIDTH - 1)
    pos = frame % period
    if pos >= _BLOB_WIDTH:
        pos = period - pos
    return "".join(_BLOB_LIT if i == pos else _BLOB_DIM for i in range(_BLOB_WIDTH))


class RunningIndicator:
    """A standalone animated "<verb>… ●○○ · Ns" line in its own message.

    The web counterpart of the CLI's live status line, for NON-agent work that
    runs off the event loop (e.g. Phase 3 validation, which executes the check
    framework + Docker baseline in a worker thread). It animates a Chainlit
    message via a background ticker so the UI never looks frozen; stop() removes
    it. Same blob/elapsed vocabulary as the agent turns' "Composing…" tail, with
    a configurable verb ("Running checks", "Running LLM-judged checks", …).
    """

    def __init__(self, verb: str = "Working", *, status: str = "",
                 author: str = "autocodabench") -> None:
        self._verb = verb
        self._status = status
        self._author = author
        self._msg: cl.Message | None = None
        self._t0 = time.monotonic()
        self._task: asyncio.Task | None = None
        self._stopped = False

    def _frame_text(self, frame: int) -> str:
        elapsed = int(time.monotonic() - self._t0)
        extra = f" · {self._status}" if self._status else ""
        return f"_{self._verb}… {_blob(frame)} · {elapsed}s{extra}_"

    async def _run(self) -> None:
        frame = 0
        try:
            while not self._stopped:
                frame += 1
                if self._msg is not None:
                    self._msg.content = self._frame_text(frame)
                    await self._msg.update()
                await asyncio.sleep(0.45)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("[run] running indicator update failed", exc_info=True)

    def set_status(self, text: str | None) -> None:
        self._status = " ".join((text or "").split())[:70]

    async def start(self) -> None:
        self._msg = cl.Message(content=self._frame_text(0), author=self._author)
        await self._msg.send()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop animating and remove the indicator message. Idempotent."""
        if self._stopped:
            return
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None
        if self._msg is not None:
            try:
                await self._msg.remove()
            except Exception:
                pass
            self._msg = None


def _result_text(content) -> str:
    """Flatten a ToolResultBlock's content into plain text."""
    if isinstance(content, list):
        parts = []
        for c in content:
            if hasattr(c, "text"):
                parts.append(c.text)
            elif isinstance(c, dict) and "text" in c:
                parts.append(c["text"])
            else:
                parts.append(str(c))
        return "\n".join(parts)
    return str(content or "")


class TurnView:
    """Renders one agent turn as an inline, CLI-style activity log.

    This is the terminal renderer (src/autocodabench/cli/progress.py) ported to
    the browser, in the bot's OWN response bubble — no tool chips, no perpetual
    animations:

      - each tool call is a short, STATIC action line — ``⏺ Write scoring
        program · +84 lines`` — gaining a ``✓`` (or ``✗``) when it returns;
      - the agent's milestones/deviations are ``📍`` / ``⚠️`` notes;
      - its narration (markdown tables included) renders as prose;
      - a single trailing ``Composing… ●○○…`` line (blob + elapsed + current
        action) shows the turn is still working, and is dropped when it ends.

    All items render in arrival order into one message. Implementation is the
    official Chainlit message API only: an ordered item list is rewritten into
    the message content. A background ticker (~0.45 s) advances the blob and
    flushes streamed narration — bounding re-renders to a steady cadence rather
    than one per token — while tool/milestone events render immediately.
    """

    def __init__(self, msg: cl.Message) -> None:
        self._msg = msg
        self._items: list[dict] = []          # ordered {kind: "log"|"text", text, ...}
        self._tool_pos: dict[str, int] = {}   # tool_use_id -> index in _items
        self._status = ""
        self._t0 = time.monotonic()
        self._frame = 0
        self._working = True
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def reset(self) -> None:
        """Discard all rendered items so a retried turn starts from a clean slate.

        Keeps the same Chainlit message and running animator; only the content
        (tool lines, narration, status, elapsed clock) is cleared.
        """
        self._items = []
        self._tool_pos = {}
        self._status = ""
        self._t0 = time.monotonic()
        self._frame = 0

    # -- content assembly ---------------------------------------------------
    def _tail(self) -> str:
        if not self._working:
            return ""
        elapsed = int(time.monotonic() - self._t0)
        extra = f" · {self._status}" if self._status else ""
        return f"_Composing… {_blob(self._frame)} · {elapsed}s{extra}_"

    def _content(self) -> str:
        # Consecutive log lines pack into one tight block (markdown hard breaks);
        # narration/footer blocks stand alone so prose and tables render right.
        blocks: list[str] = []
        run: list[str] = []
        for it in self._items:
            if it["kind"] == "log":
                run.append(it["text"])
            else:
                if run:
                    blocks.append("  \n".join(run)); run = []
                blocks.append(it["text"])
        if run:
            blocks.append("  \n".join(run))
        tail = self._tail()
        if tail:
            blocks.append(tail)
        return "\n\n".join(b for b in blocks if b)

    async def _render(self) -> None:
        async with self._lock:
            self._msg.content = self._content()
            try:
                await self._msg.update()
            except Exception:
                log.debug("[turn] render failed", exc_info=True)

    async def _animate(self) -> None:
        try:
            while self._working:
                await asyncio.sleep(0.45)
                self._frame += 1
                await self._render()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.debug("[turn] animator failed", exc_info=True)

    # -- lifecycle / events -------------------------------------------------
    def start(self) -> None:
        # Inherits the Chainlit context (contextvars are copied into the task).
        self._task = asyncio.create_task(self._animate())

    def set_status(self, text: str | None) -> None:
        self._status = " ".join((text or "").split())[:70]

    async def add_tool(self, tool_id: str, action: str, detail: str | None) -> None:
        text = f"⏺ {action}" + (f"  ·  {detail}" if detail else "")
        self._items.append({"kind": "log", "text": text, "done": False})
        self._tool_pos[tool_id] = len(self._items) - 1
        self.set_status(action)
        await self._render()

    async def mark_tool(self, tool_id: str, ok: bool) -> None:
        pos = self._tool_pos.get(tool_id)
        if pos is None:
            return
        it = self._items[pos]
        if it.get("done"):
            return
        it["done"] = True
        it["text"] = f"{it['text']}  {'✓' if ok else '✗'}"
        await self._render()

    async def add_note(self, glyph: str, message: str) -> None:
        self._items.append({"kind": "log", "text": f"{glyph} _{message}_"})
        self.set_status(message)
        await self._render()

    def add_text(self, text: str) -> None:
        # Accumulate narration into one prose block; the ticker flushes it to the
        # screen (so a token-level stream doesn't trigger a render per token).
        if not text:
            return
        if self._items and self._items[-1]["kind"] == "text":
            self._items[-1]["text"] += text
        else:
            self._items.append({"kind": "text", "text": text})

    async def add_block(self, text: str) -> None:
        self._items.append({"kind": "text", "text": text})
        await self._render()

    async def finish(self) -> None:
        if not self._working and self._task is None:
            return
        self._working = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None
        await self._render()  # final paint with no "Composing…" tail


async def _surface_log_event(
    tool_input: dict | None,
    view: "TurnView",
    turn_parts: list[dict],
) -> None:
    """Render an autocodabench_log_event call as an inline note, like the CLI.

    progress/milestone/status → a "📍" note; deviation → a highlighted "⚠️"
    note, both inline in the activity log. The line is also recorded in the
    transcript so the on-disk record matches the screen. Machinery-only events
    (no message / non-user kind) are dropped — the tool_calls/ audit has them.
    """
    inp = tool_input if isinstance(tool_input, dict) else {}
    kind = str(inp.get("kind") or "").lower()
    message = str(inp.get("message") or "").strip()
    if not message or kind not in _USER_MESSAGE_KINDS:
        return
    glyph = "⚠️" if kind == "deviation" else "📍"
    await view.add_note(glyph, message)
    turn_parts.append({"kind": "text", "text": f"\n\n> {glyph} {message}\n"})


async def run_agent_turn(
    client,
    prompt: str,
    run_dir: Path,
    response_msg: cl.Message,
) -> None:
    """Stream one agent turn to the UI and append the result to the transcript.

    Renders the turn as an inline CLI-style activity log (see TurnView): tool
    actions, milestones/deviations, narration, and a cost footer, plus the
    transcript write. Used by both on_message (user-initiated turns) and the
    phase-kickoff prompts injected by the server.
    """
    turn_parts: list[dict] = []
    tool_idx_by_id: dict[str, int] = {}
    msg_count = 0
    view = TurnView(response_msg)

    log.info("[turn] START — prompt %.80r", prompt)

    async def _stream_once() -> None:
        """One attempt at the query + receive loop (retried on transient drops)."""
        nonlocal msg_count
        await client.query(prompt)
        log.info("[turn] client.query() returned — entering receive_response() loop")

        async for message in client.receive_response():
            msg_count += 1
            log.info("[turn] message #%d: %s", msg_count, type(message).__name__)

            if isinstance(message, AssistantMessage):
                block_types = [type(b).__name__ for b in message.content]
                log.info("[turn] AssistantMessage blocks: %s", block_types)
                for block in message.content:
                    if isinstance(block, TextBlock):
                        log.debug("[turn] TextBlock len=%d", len(block.text))
                        view.add_text(block.text)
                        turn_parts.append({"kind": "text", "text": block.text})

                    elif isinstance(block, ToolUseBlock):
                        log.info("[turn] ToolUseBlock name=%r id=%s", block.name, block.id)
                        # The agent's direct-to-user log lines: surface as inline
                        # notes (like the CLI), not as opaque tool chips.
                        if block.name == _LOG_EVENT_TOOL:
                            await _surface_log_event(block.input, view, turn_parts)
                            continue
                        action, detail = _friendly_action(
                            _short_tool_name(block.name), block.input or {})
                        # Housekeeping tools (open_run/current_run/TodoWrite) get
                        # no line of their own — _friendly_action returns None.
                        if action is None:
                            continue
                        view.set_status(action)
                        if block.name in _HIDDEN_TOOLS:
                            log.debug("[turn] hidden tool %r — no log line", block.name)
                            continue
                        await view.add_tool(block.id, action, detail)
                        turn_parts.append({
                            "kind":     "tool",
                            "id":       block.id,
                            "raw_name": block.name,
                            "op":       action,
                            "input":    block.input,
                            "output":   "",
                            "is_error": False,
                        })
                        tool_idx_by_id[block.id] = len(turn_parts) - 1

                    elif isinstance(block, ThinkingBlock):
                        log.debug("[turn] ThinkingBlock (not surfaced)")

            elif isinstance(message, UserMessage):
                blocks = message.content if isinstance(message.content, list) else []
                result_blocks = [b for b in blocks if isinstance(b, ToolResultBlock)]
                log.info("[turn] UserMessage with %d ToolResultBlock(s)", len(result_blocks))
                for block in blocks:
                    if not isinstance(block, ToolResultBlock):
                        continue
                    is_error = bool(getattr(block, "is_error", False))
                    out_text = _result_text(block.content)
                    log.info(
                        "[turn] ToolResultBlock tool_use_id=%s is_error=%s content_len=%d",
                        block.tool_use_id, is_error, len(out_text),
                    )
                    # A cancelled sibling of a parallel batch is benign, not a
                    # failure — mark it done (✓), same as the CLI.
                    benign = _is_parallel_cancellation(out_text)
                    await view.mark_tool(block.tool_use_id, ok=(not is_error) or benign)

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
                log.info("[turn] ResultMessage cost=$%.4f cum=$%.4f in_tok=%d out_tok=%d", cost, cum, in_tok, out_tok)
                if in_tok:
                    cl.user_session.set("last_input_tokens", in_tok)
                if out_tok:
                    cl.user_session.set("last_output_tokens", out_tok)

                if cost or in_tok:
                    ctx_pct = 100.0 * in_tok / CONTEXT_WINDOW_TOKENS if in_tok else 0.0
                    await view.add_block(
                        f"_turn ≈ ${cost:.3f} · session "
                        f"${cum:.2f} / ${MAX_USD_PER_SESSION:.2f} · "
                        f"ctx {ctx_pct:.1f}% ({in_tok:,} tok)_"
                    )

                user = cl.user_session.get("user")
                CostLog.append(
                    run_dir,
                    turn_cost=cost,
                    cumulative=cum,
                    model=DEFAULT_MODEL,
                    session_id=cl.user_session.get("session_id") or "",
                    user_id=user.identifier if user else "anon",
                )

            elif isinstance(message, SystemMessage):
                subtype = getattr(message, "subtype", "")
                log.info("[turn] SystemMessage subtype=%r", subtype)
                if subtype in ("budget_exceeded", "rate_limit", "stop"):
                    await cl.Message(
                        content=f"_[system: {subtype}]_",
                        author="autocodabench",
                    ).send()

            elif isinstance(message, RateLimitEvent):
                info = message.rate_limit_info
                status = getattr(info, "status", "unknown")
                utilization = getattr(info, "utilization", None)
                resets_at = getattr(info, "resets_at", None)
                log.info(
                    "[turn] RateLimitEvent status=%r utilization=%s resets_at=%s",
                    status, utilization, resets_at,
                )
                if status == "rejected":
                    import datetime
                    if resets_at:
                        reset_dt = datetime.datetime.fromtimestamp(resets_at, tz=datetime.timezone.utc)
                        wait_secs = max(0, int((reset_dt - datetime.datetime.now(tz=datetime.timezone.utc)).total_seconds()))
                        reset_str = f" Resets in ~{wait_secs}s (at {reset_dt.strftime('%H:%M:%S')} UTC)."
                    else:
                        reset_str = ""
                    log.warning("[turn] rate limit REJECTED — agent is paused.%s", reset_str)
                    await cl.Message(
                        content=f"_⏳ Rate limit reached — waiting for the window to reset.{reset_str}_",
                        author="autocodabench",
                    ).send()
                elif status == "allowed_warning":
                    pct = f"{utilization * 100:.0f}%" if utilization is not None else "high"
                    log.warning("[turn] rate limit warning — utilization=%s", pct)
                    await cl.Message(
                        content=f"_⚠️ Approaching rate limit ({pct} utilization) — may pause soon._",
                        author="autocodabench",
                    ).send()

        log.info("[turn] receive_response() loop exited normally after %d messages", msg_count)

    try:
        view.start()  # show the "Composing…" line immediately, before any wait
        for _attempt in range(1, _TURN_MAX_ATTEMPTS + 1):
            # A retry re-runs the whole turn, so wipe the per-attempt accumulators
            # and the partially-rendered view before re-issuing the query. The
            # CLI↔Python connection survives an outbound API drop, so re-querying
            # resumes with the session's context intact.
            turn_parts.clear()
            tool_idx_by_id.clear()
            msg_count = 0
            if _attempt > 1:
                view.reset()
            try:
                log.info("[turn] streaming attempt %d/%d", _attempt, _TURN_MAX_ATTEMPTS)
                await _stream_once()
                break  # turn completed — leave the retry loop
            except Exception as inner:
                if _is_transient_turn_error(inner) and _attempt < _TURN_MAX_ATTEMPTS:
                    log.warning(
                        "[turn] transient error on attempt %d/%d: %s: %s — retrying",
                        _attempt, _TURN_MAX_ATTEMPTS, type(inner).__name__, inner,
                    )
                    await view.add_note(
                        "⚠️",
                        f"connection dropped — retrying ({_attempt}/{_TURN_MAX_ATTEMPTS - 1})…",
                    )
                    await asyncio.sleep(1.5 * _attempt)
                    continue
                raise

    except Exception as e:
        log.exception("[turn] EXCEPTION in run_agent_turn: %s: %s", type(e).__name__, e)
        # If the client disconnected mid-turn (e.g. ClosedResourceError once the
        # browser tab closes), the error message itself can't be delivered —
        # don't let that second failure escape and take down the handler.
        try:
            await cl.Message(
                content=f"**Error:** `{type(e).__name__}: {e}`",
                author="autocodabench",
            ).send()
        except Exception:
            log.warning("[turn] could not deliver error message (connection closed?)")
    finally:
        # Stop the animation and paint the final log (no "Composing…" tail),
        # whether the turn succeeded or failed.
        await view.finish()

    log.info("[turn] END — %d messages processed, %d turn_parts", msg_count, len(turn_parts))

    # Write completed turn to transcript.
    if turn_parts:
        body_chunks: list[str] = []
        for part in turn_parts:
            if part["kind"] == "text":
                body_chunks.append(part["text"])
            else:
                body_chunks.append(Transcript.format_tool_call(
                    op=part["op"],
                    raw_name=part["raw_name"],
                    input_json=part["input"],
                    output_text=part["output"],
                    is_error=part["is_error"],
                ))
        Transcript.append(run_dir, role="claude", text="".join(body_chunks))
