#!/usr/bin/env python3
"""Claude Code hook — mirror the live session transcript into the active run.

Invoked by `.claude/settings.json` hooks on UserPromptSubmit + Stop. Reads the
hook payload from stdin, locates `<repo>/auto_codabench/runs/LATEST/`, copies
the session's JSONL transcript into it, and regenerates a human-readable
`transcript.md`.

Failure is silent: if no active run exists, if the transcript_path is missing,
if JSON parsing fails, etc., we exit 0 without touching anything. We do NOT
want hooks to ever break Claude Code itself.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Locate the active run
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = REPO_ROOT / "auto_codabench" / "runs"
LATEST = RUNS_ROOT / "LATEST"


def _active_run() -> Path | None:
    if not LATEST.is_symlink() and not LATEST.exists():
        return None
    try:
        target = LATEST.resolve()
    except OSError:
        return None
    if not target.is_dir():
        return None
    if not (target / "meta.json").exists():
        return None
    return target


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Transcript rendering
# ---------------------------------------------------------------------------

def _friendly_tool_name(raw: str) -> str:
    """`mcp__alex-mcp__search_works` → `alex-mcp · search_works`."""
    if isinstance(raw, str) and raw.startswith("mcp__"):
        parts = raw.split("__", 2)
        if len(parts) >= 3:
            return f"{parts[1]} · {parts[2]}"
    return str(raw)


def _classify_turn(content) -> dict:
    """Walk one message's content list and bucket the blocks.

    Returns:
      {"text": <combined prose>,
       "tool_calls": [{"name", "input", "id"}],
       "tool_results": [{"id", "text", "is_error"}]}

    This is used to render a turn as ONE prose block followed by ONE
    collapsed <details> with all tools — rather than one details per
    block, which was the source of the clutter.
    """
    bucket = {"text": "", "tool_calls": [], "tool_results": []}
    if isinstance(content, str):
        bucket["text"] = content
        return bucket
    if not isinstance(content, list):
        return bucket

    text_parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            bucket["tool_calls"].append({
                "name":  block.get("name", "?"),
                "input": block.get("input", {}),
                "id":    block.get("id", ""),
            })
        elif btype == "tool_result":
            rc = block.get("content", "")
            if isinstance(rc, list):
                rtext = "\n".join(
                    b.get("text", "") for b in rc if isinstance(b, dict)
                )
            else:
                rtext = str(rc)
            bucket["tool_results"].append({
                "id":       block.get("tool_use_id", ""),
                "text":     rtext,
                "is_error": block.get("is_error", False),
            })
        # `thinking` blocks intentionally dropped from the share view.
    bucket["text"] = "\n\n".join(p for p in text_parts if p).strip()
    return bucket


def _render_tools_block(tool_calls: list[dict], results_by_id: dict) -> str:
    """One `<details>` per turn collecting all tool input/output pairs.

    Nests cleanly under the assistant's prose; collapsed by default in
    every markdown renderer we care about (GitHub, VS Code, Obsidian).
    """
    if not tool_calls:
        return ""
    inner: list[str] = []
    for tc in tool_calls:
        name = _friendly_tool_name(tc["name"])
        try:
            input_str = json.dumps(tc["input"], indent=2, ensure_ascii=False, default=str)
        except Exception:
            input_str = str(tc["input"])
        result = results_by_id.get(tc["id"])
        if result:
            icon = "❌" if result["is_error"] else "🔧"
            out_text = (result["text"] or "").strip()
            if len(out_text) > 2000:
                out_text = out_text[:2000] + "\n…[truncated; full output in tool_calls/]"
            tool_md = (
                f"**{icon} {name}**\n\n"
                f"_input_\n```json\n{input_str}\n```\n\n"
                f"_output_\n```\n{out_text}\n```\n"
            )
        else:
            # The result usually arrives in the NEXT user message; if it
            # didn't, render the input alone.
            tool_md = (
                f"**🔧 {name}**\n\n"
                f"_input_\n```json\n{input_str}\n```\n_(output not in transcript)_\n"
            )
        inner.append(tool_md)
    n = len(tool_calls)
    summary = f"🔧 {n} tool call{'s' if n != 1 else ''}"
    return (
        f"\n<details><summary>{summary}</summary>\n\n"
        + "\n---\n\n".join(inner)
        + "\n\n</details>\n"
    )


_ROLE_HEADER = {
    "user": "## 👤 user — ",
    "assistant": "## 🤖 autocodabench — ",
    "system": "## ⚙️ system — ",
}


def _run_window(run_dir: Path) -> tuple[str | None, str | None]:
    """Return (start_ts, end_ts) bounding the run's "active" period.

    - start_ts comes from meta.json::started_at.
    - end_ts is the latest `autocodabench_*` tool call timestamp in
      events.jsonl, plus a 90-second buffer for the agent's final prose.
      If no autocodabench tool ever fired we leave end_ts = None
      (unbounded) so the user's first turn is at least visible.

    Why this works: the orchestrator skill calls
    `autocodabench_log_event` and friends on every substantive turn. The
    moment the user steers off-topic ("how do I fix X", "show me the
    config file"), those calls stop firing. So the last autocodabench
    tool event is a reliable "end-of-run" marker without needing the
    user to type a stop command.
    """
    try:
        started_at = json.loads((run_dir / "meta.json").read_text()).get("started_at")
    except Exception:
        started_at = None

    end_ts: str | None = None
    evpath = run_dir / "events.jsonl"
    if evpath.is_file():
        try:
            for ln in evpath.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(ln)
                except Exception:
                    continue
                tool = ev.get("tool", "")
                if not isinstance(tool, str) or not tool.startswith("autocodabench_"):
                    continue
                if ev.get("kind") not in ("tool_call_started", "tool_call_finished"):
                    continue
                ts = ev.get("ts")
                if ts and (end_ts is None or ts > end_ts):
                    end_ts = ts
        except Exception:
            pass

    # +90s buffer so the agent's prose after the last tool call gets in.
    if end_ts:
        try:
            t = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
            from datetime import timedelta
            end_ts = (t + timedelta(seconds=90)).isoformat().replace("+00:00", "Z")
        except Exception:
            pass
    return started_at, end_ts


def _ts_for_turn(turn: dict) -> str | None:
    """Best-effort timestamp string for a session-jsonl row."""
    return (
        turn.get("timestamp")
        or turn.get("ts")
        or (turn.get("message") or {}).get("timestamp")
    )


def _render_markdown(jsonl_path: Path, run_dir: Path) -> None:
    """Read session JSONL and write transcript.md / transcript.jsonl.

    The full session JSONL is mirrored verbatim as `transcript.jsonl` for
    auditing. `transcript.md` is filtered AND reformatted for sharing:
      - bounded by [started_at, last_autocodabench_tool_ts + 90s];
      - per-turn aggregation (one assistant turn = one prose block + one
        collapsed details block listing every tool call from that turn);
      - tool-only turns are absorbed into the next prose-bearing turn so
        the conversation reads top-to-bottom without empty cards.
    """
    raw_text = jsonl_path.read_text(encoding="utf-8", errors="replace")
    lines = raw_text.splitlines()
    turns = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            turns.append(json.loads(ln))
        except json.JSONDecodeError:
            continue

    # Always mirror the raw JSONL — ground truth, full session.
    (run_dir / "transcript.jsonl").write_text(raw_text, encoding="utf-8")

    started_at, end_ts = _run_window(run_dir)
    total_count = sum(1 for t in turns if t.get("type") in ("user", "assistant"))

    md: list[str] = [
        f"# Transcript — {run_dir.name}",
        "",
        f"_Last refreshed: {_utc_now()}_",
        "",
        f"_Run window: {started_at or '?'} → {end_ts or 'ongoing'}_  "
        f"_·  Source: `{jsonl_path.name}` (filtered to this run)_",
        "",
        "Each user/assistant turn is one block. Tool calls used during a "
        "turn are aggregated into a single collapsed `<details>` at the "
        "bottom of that turn. `transcript.jsonl` is the *full*, unfiltered "
        "session log — use it for programmatic analysis.",
        "",
        "---",
        "",
    ]

    # First pass: collect per-turn classified content + a flat results
    # index (tool_use_id → result) so we can pair them across turns.
    classified: list[dict] = []  # [{role, ts, text, tool_calls}]
    pending_tool_results: dict[str, dict] = {}

    for turn in turns:
        ttype = turn.get("type")
        if ttype not in ("user", "assistant"):
            continue
        ts = _ts_for_turn(turn) or ""
        if started_at and ts and ts < started_at:
            continue
        if end_ts and ts and ts > end_ts:
            continue
        msg = turn.get("message") or {}
        role = msg.get("role") or ttype
        bucket = _classify_turn(msg.get("content") or turn.get("content") or "")
        # Stash any tool results we found inside this turn for later pairing.
        for r in bucket["tool_results"]:
            pending_tool_results[r["id"]] = r
        # Only keep user/assistant turns that have prose; tool-only assistant
        # turns get merged forward by attaching their tool_calls to the next
        # prose-bearing assistant turn.
        classified.append({
            "role":       role,
            "ts":         ts,
            "text":       bucket["text"],
            "tool_calls": bucket["tool_calls"],
        })

    # Second pass: emit prose turns + merge tool calls forward into the
    # next prose-bearing assistant turn. Two kinds of "noise" to drop:
    #   - user turns that are pure tool_result containers (Claude Code
    #     wraps tool_results in user-role messages with no text);
    #   - assistant turns that are pure tool_use with no text — these
    #     get collapsed into the *following* assistant turn's tools.
    pending_tools: list[dict] = []
    kept = 0
    for turn in classified:
        role = turn["role"]
        if role == "user":
            # Drop tool_result-only "user" messages; their tools are
            # already paired into the calling assistant turn's <details>.
            if not turn["text"]:
                continue
            md.append(_ROLE_HEADER["user"] + turn["ts"])
            md.append("")
            md.append(turn["text"])
            md.append("")
            kept += 1
            continue
        # assistant
        merged_tools = pending_tools + turn["tool_calls"]
        if not turn["text"] and merged_tools:
            # Tool-only turn — defer to the next prose-bearing turn.
            pending_tools = merged_tools
            continue
        if not turn["text"] and not merged_tools:
            continue  # genuinely empty
        md.append(_ROLE_HEADER["assistant"] + turn["ts"])
        md.append("")
        if turn["text"]:
            md.append(turn["text"])
            md.append("")
        if merged_tools:
            md.append(_render_tools_block(merged_tools, pending_tool_results))
        kept += 1
        pending_tools = []

    # Flush any orphan tool-only assistant run at the very end of the
    # window (e.g. agent fires a final log_event then yields control).
    if pending_tools:
        md.append(_ROLE_HEADER["assistant"] + "(closing tool calls)")
        md.append("")
        md.append(_render_tools_block(pending_tools, pending_tool_results))

    md.append("")
    md.append("---")
    md.append("")
    md.append(
        f"_Rendered {kept} turn(s) for this run; full session JSONL has "
        f"{total_count} user/assistant turn(s) total._"
    )

    (run_dir / "transcript.md").write_text("\n".join(md), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        payload = json.loads(raw)
    except Exception:
        return 0  # bad input — ignore

    run = _active_run()
    if run is None:
        return 0  # no active run — skip silently

    event = payload.get("hook_event_name") or "unknown"

    # Always append a small bookkeeping line to events.jsonl so we know hooks
    # actually fired (helps debug "I expected a transcript and got nothing").
    try:
        with (run / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": _utc_now(),
                "kind": "hook_fired",
                "hook": event,
                "session_id": payload.get("session_id"),
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass

    # Copy the session transcript and regenerate transcript.md
    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath")
    if transcript_path:
        try:
            p = Path(transcript_path).expanduser()
            if p.is_file():
                _render_markdown(p, run)
        except Exception:
            try:
                (run / "mcp_stderr" / "hook_errors.log").parent.mkdir(parents=True, exist_ok=True)
                with (run / "mcp_stderr" / "hook_errors.log").open("a", encoding="utf-8") as f:
                    f.write(f"{_utc_now()} {event} render failed:\n")
                    traceback.print_exc(file=f)
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
