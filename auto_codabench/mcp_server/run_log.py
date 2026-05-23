"""Run-directory bookkeeping for AutoCodabench sessions.

A "run" is the working directory for one planning or execution session,
under `auto_codabench/runs/<branch_id>_<runtime_id>/`. Everything Claude
writes during that session (specs, implementation_plan.md, tool-call
snapshots, structured events, MCP stderr) lives there so that
postmortems and reproduction are local-grep-friendly.

The module is intentionally small and *server-process-scoped*:
    - The active run is held in a single module-level variable.
    - `open_run()` creates the directory and points the module variable
      at it, also writes the environment variable AUTOCODABENCH_RUN_DIR
      so any child process inherits the same run.
    - `log_event()` appends a single JSON line to events.jsonl.
    - `snapshot_tool_call()` writes the full request/response of one
      MCP tool call to its own file under tool_calls/ with a zero-padded
      counter for sortability.

No external dependencies beyond the stdlib + the repo layout.
"""
from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import REPO_ROOT

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

RUNS_ROOT = (REPO_ROOT / "auto_codabench" / "runs").resolve()
_state_lock = threading.Lock()
_current_run: Path | None = None
_call_counter = 0
_stderr_file_handler: logging.Handler | None = None


def _utc_now_iso() -> str:
    """ISO8601 UTC timestamp, second precision, no microseconds."""
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# Static per-run README template, written into every run directory on open.
def _run_readme(meta: dict) -> str:
    return f"""\
# Run: `{meta.get('branch_id','?')}_{meta.get('runtime_id','?')}`

| field | value |
|-------|-------|
| started_at | `{meta.get('started_at','')}` |
| branch_id | `{meta.get('branch_id','')}` |
| runtime_id | `{meta.get('runtime_id','')}` |
| git_sha | `{meta.get('git_sha','')}` |
| slug | `{meta.get('slug','') or '(none)'}` |
| conda_env | `{meta.get('conda_env','')}` |
| python | `{meta.get('python','')}` |
| cwd | `{meta.get('cwd','')}` |
| pid | `{meta.get('pid','')}` |

A *run* is the working folder for one Claude session — either iteration 1
(planning) or iteration 2 (execution). Every artifact Claude produced
during this session lives here.

## How to read this folder

### Conversation (what was actually said)

| File | Read it when… |
|------|---------------|
| **`transcript.md`** | You want the human-readable back-and-forth. Each turn is rendered with role headers (👤 user / 🤖 claude). Tool calls and tool results are folded into `<details>` blocks so the prose stays readable. **Start here.** |
| **`transcript.jsonl`** | You want the raw ground-truth Claude Code session log — one JSON object per line. Use this for programmatic analysis (e.g. `jq`). It's a copy of Claude Code's internal session JSONL, refreshed by the `Stop` hook after every assistant turn. |

> The transcript files are produced by the Claude Code hook in
> `auto_codabench/runs/log_hook.py`, registered in
> `.claude/settings.json`. If they are missing, the hook either didn't
> fire (no Claude Code session running here) or this is a session
> launched via Claude Desktop (which does not invoke the hook).

### Structured timeline

| File | Read it when… |
|------|---------------|
| `meta.json` | One-shot snapshot of who/what/when the run started — branch, sha, started_at, conda env, pid, slug. |
| `events.jsonl` | Structured one-event-per-line timeline. Includes `run_opened`, `tool_call_started`/`tool_call_finished`/`tool_call_error` (auto-emitted by every autocodabench MCP tool), `hook_fired` (auto-emitted by the Claude Code hook), and any skill-level events like `question_asked`, `ss_searched`, `proposal_made`, `spec_written`, `iter1_done`. Greppable with `jq`. |
| `tool_calls/NNNN_<tool>.json` | Full request + response of every MCP tool call, in order. Each file has `args`, `result`, `error`, `duration_ms`, `started_at`, `finished_at`. The leading `NNNN` is a 4-digit counter so `ls` sorts chronologically. |

### Specs (the deliverables of iteration 1)

| Dir | Read it when… |
|------|---------------|
| `specs/` | The current set of planning specs (`01-task-framing.md`, …, `06-run-logging-and-env.md`) plus `implementation_plan.md`. This is what iteration 2 reads as input. |
| `specs_history/` | Versioned snapshots of each spec — every time Claude rewrote one, a timestamped copy was saved here. `diff` adjacent ones to see what changed. |

### Execution artifacts (iteration 2 only)

| Dir | Read it when… |
|------|---------------|
| `artifacts/<subagent>/` | Per-subagent outputs of the execution session — model checkpoints, plots, the meta-reviewer's final report. Empty during iteration 1. |
| `mcp_stderr/autocodabench.log` | Stderr from the autocodabench MCP server tee'd in. Useful when a tool errored silently. |
| `mcp_stderr/hook_errors.log` | (Only if non-empty) Errors from the transcript-mirroring hook. |

## Cheatsheet

```bash
# Read the conversation
less {meta.get('branch_id','RUN')}_{meta.get('runtime_id','')}/transcript.md

# Greppable timeline
jq -c '{{ts, kind, tool, msg: .message}}' < events.jsonl

# Which tool calls failed?
jq -c 'select(.error != null)' < events.jsonl

# All Claude messages in one stream
jq -r 'select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text' \\
   < transcript.jsonl

# Latest version of a spec
ls -t specs_history/ | head
```
"""


def _safe_slug(text: str) -> str:
    out = []
    for ch in text:
        out.append(ch if (ch.isalnum() or ch in "-_") else "-")
    s = "".join(out).strip("-")
    return s or "unknown"


def _branch_id() -> str:
    try:
        b = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        b = "detached"
    return _safe_slug(b)


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _runtime_id() -> str:
    # Sortable; collision-resistant enough for a single user.
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

@dataclass
class RunInfo:
    path: Path
    branch_id: str
    runtime_id: str
    git_sha: str | None
    slug: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "branch_id": self.branch_id,
            "runtime_id": self.runtime_id,
            "git_sha": self.git_sha,
            "slug": self.slug,
        }


def open_run(slug: str | None = None, *, branch_id: str | None = None, runtime_id: str | None = None) -> RunInfo:
    """Create a new run directory and make it the active run.

    If AUTOCODABENCH_RUN_DIR already points at an existing directory,
    we adopt it instead of creating a new one (so child processes
    inherit the parent's run).
    """
    global _current_run, _call_counter

    inherited = os.environ.get("AUTOCODABENCH_RUN_DIR")
    if inherited and Path(inherited).is_dir() and (Path(inherited) / "meta.json").exists():
        with _state_lock:
            _current_run = Path(inherited).resolve()
        meta = json.loads((_current_run / "meta.json").read_text(encoding="utf-8"))
        return RunInfo(
            path=_current_run,
            branch_id=meta.get("branch_id", ""),
            runtime_id=meta.get("runtime_id", ""),
            git_sha=meta.get("git_sha"),
            slug=meta.get("slug"),
        )

    bid = _safe_slug(branch_id) if branch_id else _branch_id()
    rid = _safe_slug(runtime_id) if runtime_id else _runtime_id()
    name = f"{bid}_{rid}"
    run_path = (RUNS_ROOT / name).resolve()

    # If the same name collides (extremely unlikely with second precision),
    # append a numeric suffix.
    if run_path.exists():
        i = 1
        while (RUNS_ROOT / f"{name}-{i}").exists():
            i += 1
        run_path = (RUNS_ROOT / f"{name}-{i}").resolve()

    (run_path / "tool_calls").mkdir(parents=True, exist_ok=True)
    (run_path / "specs").mkdir(parents=True, exist_ok=True)
    (run_path / "specs_history").mkdir(parents=True, exist_ok=True)
    (run_path / "mcp_stderr").mkdir(parents=True, exist_ok=True)

    meta = {
        "started_at": _utc_now_iso(),
        "branch_id": bid,
        "runtime_id": rid,
        "git_sha": _git_sha(),
        "slug": slug,
        "python": os.environ.get("CONDA_PREFIX", ""),
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV", ""),
        "cwd": str(Path.cwd()),
        "pid": os.getpid(),
    }
    (run_path / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (run_path / "events.jsonl").touch()
    (run_path / "README.md").write_text(_run_readme(meta), encoding="utf-8")

    # Maintain a LATEST symlink for easy postmortem access.
    latest = RUNS_ROOT / "LATEST"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        os.symlink(run_path.name, latest, target_is_directory=True)
    except OSError as e:
        # Filesystems that don't support symlinks just lose this nicety.
        logging.getLogger("autocodabench.runlog").warning("LATEST symlink not created: %s", e)

    with _state_lock:
        _current_run = run_path
        _call_counter = 0

    os.environ["AUTOCODABENCH_RUN_DIR"] = str(run_path)

    _attach_stderr_handler(run_path / "mcp_stderr" / "autocodabench.log")

    log_event("run_opened", **meta)

    return RunInfo(path=run_path, branch_id=bid, runtime_id=rid, git_sha=meta["git_sha"], slug=slug)


def current_run() -> Path | None:
    """Return the active run dir.

    Adopts `AUTOCODABENCH_RUN_DIR` if it's set, exists, and contains a
    meta.json — this matters for fresh MCP subprocesses spawned by the
    web layer on phase transitions: each new agent inherits the env
    var from the parent but starts with `_current_run = None`. Without
    this adoption, the first `current_run()` call would return None and
    the agent might fall through to globbing / the LATEST symlink, which
    under concurrent web sessions points at whichever session was opened
    most recently — not necessarily this agent's session.
    """
    global _current_run
    if _current_run is not None:
        return _current_run
    inherited = os.environ.get("AUTOCODABENCH_RUN_DIR")
    if (inherited
        and Path(inherited).is_dir()
        and (Path(inherited) / "meta.json").exists()):
        with _state_lock:
            _current_run = Path(inherited).resolve()
        return _current_run
    return None


def require_run() -> Path | None:
    """Return the active run dir if one is open, else None.

    All logging primitives silently no-op when no run is open. The
    `autocodabench_current_run` MCP tool exists for skills to verify that a
    run is open BEFORE doing real work — calling other tools without first
    opening a run is allowed (and the call still succeeds) but no event
    will be captured.
    """
    return _current_run


# ---------------------------------------------------------------------------
# Logging primitives
# ---------------------------------------------------------------------------

def _atomic_counter() -> int:
    global _call_counter
    with _state_lock:
        _call_counter += 1
        return _call_counter


def log_event(kind: str, **fields: Any) -> None:
    """Append one JSON line to events.jsonl in the active run.

    `kind` is a short, lowercase, snake_case label (e.g. tool_call_started,
    spec_written, ss_searched, iter1_done). All other fields are stored
    verbatim, so the caller fully controls the shape.

    Silently no-ops when no run is open — call `open_run` first.
    """
    run = require_run()
    if run is None:
        return
    event = {"ts": _utc_now_iso(), "kind": kind, **fields}
    line = json.dumps(event, ensure_ascii=False, default=str)
    with (run / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def snapshot_tool_call(
    *,
    tool: str,
    args: dict[str, Any],
    result: Any,
    error: str | None,
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> Path:
    """Write a full record of one MCP tool call to its own JSON file.

    Silently no-ops when no run is open.
    """
    run = require_run()
    if run is None:
        return Path("/dev/null")
    n = _atomic_counter()
    fname = f"{n:04d}_{_safe_slug(tool)}.json"
    out = run / "tool_calls" / fname
    record = {
        "n": n,
        "tool": tool,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "args": args,
        "result": result,
        "error": error,
    }
    out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return out


def snapshot_spec(filename: str, body: str) -> dict[str, str]:
    """Write a spec into <run>/specs/<filename> AND a versioned copy under specs_history/.

    Raises RuntimeError if no run is open — specs must always be attributed.
    """
    run = require_run()
    if run is None:
        raise RuntimeError("no active run — call autocodabench_open_run first")
    rel = Path(filename)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"spec filename must be relative inside the run: {filename!r}")

    target = run / "specs" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    # versioned snapshot
    stamp = _utc_now_iso().replace(":", "-")
    hist_name = f"{rel.stem}.{stamp}{rel.suffix}"
    hist = run / "specs_history" / hist_name
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(body, encoding="utf-8")

    log_event("spec_written", filename=str(rel), bytes=len(body), history=hist_name)
    return {"path": str(target), "history": str(hist)}


# ---------------------------------------------------------------------------
# Logging-to-stderr-file plumbing
# ---------------------------------------------------------------------------

def _attach_stderr_handler(target: Path) -> None:
    """Tee the autocodabench logger's output into <run>/mcp_stderr/autocodabench.log."""
    global _stderr_file_handler

    target.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("autocodabench")
    if _stderr_file_handler is not None:
        try:
            root_logger.removeHandler(_stderr_file_handler)
        except Exception:
            pass
        try:
            _stderr_file_handler.close()
        except Exception:
            pass

    fh = logging.FileHandler(target, mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
    root_logger.addHandler(fh)
    _stderr_file_handler = fh


# ---------------------------------------------------------------------------
# Decorator for MCP tools
# ---------------------------------------------------------------------------

def logged_tool(name: str | None = None):
    """Decorator: wrap an async MCP tool to auto-log every call.

    Logs are:
      1. an `events.jsonl` entry (`tool_call_started` / `tool_call_finished`
         / `tool_call_error`)
      2. a full snapshot file under `tool_calls/`

    `name` overrides the auto-detected tool name (defaults to the wrapped
    function's __name__).
    """
    def deco(func):
        tool_name = name or func.__name__

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            started = _utc_now_iso()
            t0 = time.perf_counter()
            log_event("tool_call_started", tool=tool_name, args=kwargs)
            try:
                result = await func(*args, **kwargs)
                err = None
            except Exception as e:  # never reachable for our tools (they catch internally) but kept for safety
                err = f"{type(e).__name__}: {e}"
                result = {"error": err}
            finished = _utc_now_iso()
            duration_ms = int((time.perf_counter() - t0) * 1000)
            # An "error" key in a successful return also counts as an error event
            inline_err = None
            if isinstance(result, dict) and "error" in result:
                inline_err = result["error"]
            snapshot_tool_call(
                tool=tool_name, args=kwargs, result=result, error=err or inline_err,
                started_at=started, finished_at=finished, duration_ms=duration_ms,
            )
            log_event(
                "tool_call_error" if (err or inline_err) else "tool_call_finished",
                tool=tool_name, duration_ms=duration_ms, error=err or inline_err,
            )
            return result

        # CRITICAL: preserve the original signature so fastmcp/pydantic can
        # introspect parameter names and types for the JSON schema. Without
        # this, the wrapper's bare (*args, **kwargs) signature would replace
        # the real one and tool registration fails with KeyError on the args.
        wrapper.__signature__ = inspect.signature(func)
        return wrapper

    return deco
