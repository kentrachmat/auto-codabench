"""In-process tool surface for generic (non-Claude) backends.

The Claude backend reaches the authoring/runner layers through the MCP
stdio server. Generic chat-completions backends get the *same tool
surface* here, executed in-process — same tool names, same underlying
functions, and crucially the **same audit trail**: every execution is
recorded through :mod:`autocodabench.run_log` (``tool_calls/NNNN_*.json``
+ ``events.jsonl``), so runs are replayable and comparable across
backbones. That parity is what makes cross-model benchmarking
(``experiments/backbone_bench``) commensurable by construction: every
backbone acts through the identical tool surface and leaves identical
evidence.

Tool schemas are hand-written (OpenAI function-calling format) and kept
deliberately small.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import run_log
from ..core import bundle_io
from ..runner import execution as runner

# Tool results returned to the model are capped so a verbose stderr tail
# cannot flood the context window. Full streams are on disk regardless.
_RESULT_CHARS_CAP = 20_000


def _now() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Tool implementations that aren't simple re-exports
# ---------------------------------------------------------------------------

def _open_run(slug: str | None = None) -> dict[str, Any]:
    return run_log.open_run(slug=slug).to_dict()


def _current_run() -> dict[str, Any]:
    p = run_log.current_run()
    return {"opened": p is not None, "path": str(p) if p else None}


def _log_event(kind: str, message: str | None = None, payload: dict | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if message is not None:
        fields["message"] = message
    if payload is not None:
        fields["payload"] = payload
    run_log.log_event(kind, **fields)
    return {"logged": True, "kind": kind}


def _snapshot_spec(filename: str, body: str) -> dict[str, Any]:
    return run_log.snapshot_spec(filename, body)


def _read_file(path: str, max_chars: int = 40_000) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_file():
        return {"error": f"not a file: {p}"}
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"path": str(p), "truncated": len(text) > max_chars, "content": text[:max_chars]}


def _list_dir(path: str, max_entries: int = 200) -> dict[str, Any]:
    p = Path(path).expanduser()
    if not p.is_dir():
        return {"error": f"not a directory: {p}"}
    entries = []
    for child in sorted(p.rglob("*")):
        kind = "dir" if child.is_dir() else "file"
        entries.append(f"{kind}: {child.relative_to(p)}")
        if len(entries) >= max_entries:
            entries.append("… (truncated)")
            break
    return {"path": str(p), "entries": entries}


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}

_S = {"type": "string"}
_B = {"type": "boolean"}
_FILES = {"type": "object", "additionalProperties": {"type": "string"},
          "description": "mapping of relative file path -> full text content"}


@dataclass(frozen=True)
class LocalTool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]

    def spec(self) -> dict[str, Any]:
        """OpenAI function-calling tool spec."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.parameters}}


_TOOLS: list[LocalTool] = [
    LocalTool("autocodabench_open_run",
              "Open (or adopt) the run directory for this session. Call FIRST.",
              _obj({"slug": _S}, []), _open_run),
    LocalTool("autocodabench_current_run",
              "Return the active run directory path, if any.",
              _obj({}, []), _current_run),
    LocalTool("autocodabench_log_event",
              "Append a structured event to the run's events.jsonl.",
              _obj({"kind": _S, "message": _S,
                    "payload": {"type": "object"}}, ["kind"]), _log_event),
    LocalTool("autocodabench_snapshot_spec",
              "Save a spec/plan markdown file into the run's specs/ dir "
              "(e.g. filename='implementation_plan.md').",
              _obj({"filename": _S, "body": _S}, ["filename", "body"]), _snapshot_spec),
    LocalTool("autocodabench_init_bundle",
              "Create the empty Codabench bundle skeleton. Call before any other write.",
              _obj({"slug": _S, "overwrite": _B}, ["slug"]), bundle_io.init_bundle),
    LocalTool("autocodabench_write_competition_yaml",
              "Write competition.yaml from a full payload dict (Codabench v2 schema; "
              "required keys: version,title,image,terms,pages,phases,tasks,leaderboards).",
              _obj({"slug": _S, "payload": {"type": "object"}}, ["slug", "payload"]),
              bundle_io.write_competition_yaml),
    LocalTool("autocodabench_write_page",
              "Write one markdown page under the bundle's pages/ dir.",
              _obj({"slug": _S, "filename": _S, "body": _S}, ["slug", "filename", "body"]),
              bundle_io.write_page),
    LocalTool("autocodabench_write_scoring_program",
              "Write scoring_program/score.py (+ metadata.yaml). The script must write "
              "scores.json whose keys match the leaderboard column keys.",
              _obj({"slug": _S, "script": _S, "script_filename": _S, "command": _S},
                   ["slug", "script"]), bundle_io.write_scoring_program),
    LocalTool("autocodabench_write_ingestion_program",
              "Write ingestion_program/ingestion.py (+ metadata.yaml) — only for "
              "code-submission competitions.",
              _obj({"slug": _S, "script": _S, "script_filename": _S, "command": _S},
                   ["slug", "script"]), bundle_io.write_ingestion_program),
    LocalTool("autocodabench_write_solution",
              "Write a baseline solution folder under solutions/<subdir>/.",
              _obj({"slug": _S, "files": _FILES, "subdir": _S}, ["slug", "files"]),
              bundle_io.write_solution),
    LocalTool("autocodabench_attach_data",
              "Place text data files into reference_data | input_data | starting_kit | public_data.",
              _obj({"slug": _S, "target": _S, "files": _FILES, "from_path": _S},
                   ["slug", "target"]), bundle_io.attach_data),
    LocalTool("autocodabench_validate_bundle",
              "Lint the bundle (schema, file references, leaderboard keys). Run before zipping.",
              _obj({"slug": _S}, ["slug"]), bundle_io.validate_bundle),
    LocalTool("autocodabench_zip_bundle",
              "Produce <slug>.zip with competition.yaml at the zip root.",
              _obj({"slug": _S}, ["slug"]), bundle_io.zip_bundle),
    LocalTool("autocodabench_prepare_run_env",
              "Clone the base conda env and install the bundle's requirements.txt files.",
              _obj({"slug": _S, "force_recreate": _B}, ["slug"]), runner.prepare_run_env),
    LocalTool("autocodabench_install_env_extras",
              "Install extra pip packages into the per-run env (after a ModuleNotFoundError).",
              _obj({"env_name": _S, "packages": {"type": "array", "items": _S}},
                   ["env_name", "packages"]), runner.install_env_extras),
    LocalTool("autocodabench_run_baseline_submission",
              "Run the bundle's own baseline through its scoring pipeline in a sandbox "
              "(engine: auto|docker|conda — docker runs inside the bundle's declared "
              "docker_image, as Codabench does).",
              _obj({"slug": _S, "env_name": _S, "subdir": _S, "engine": _S},
                   ["slug", "env_name"]),
              runner.run_baseline_submission),
    LocalTool("autocodabench_run_user_submission",
              "Run an arbitrary submission directory through the bundle's scoring pipeline "
              "(engine: auto|docker|conda — docker runs inside the bundle's declared "
              "docker_image, as Codabench does).",
              _obj({"slug": _S, "env_name": _S, "submission_dir": _S, "label": _S,
                    "engine": _S},
                   ["slug", "env_name", "submission_dir", "label"]),
              runner.run_user_submission),
    LocalTool("autocodabench_run_starting_kit",
              "Execute the bundle's starting-kit notebook end-to-end in the per-run env.",
              _obj({"slug": _S, "env_name": _S, "notebook_path": _S}, ["slug", "env_name"]),
              runner.run_starting_kit),
    LocalTool("autocodabench_remove_run_env",
              "Remove a per-run conda env (cleanup).",
              _obj({"env_name": _S}, ["env_name"]), runner.remove_run_env),
    LocalTool("read_file",
              "Read a text file (UTF-8, content truncated past 40k chars).",
              _obj({"path": _S}, ["path"]), _read_file),
    LocalTool("list_dir",
              "Recursively list a directory (up to 200 entries).",
              _obj({"path": _S}, ["path"]), _list_dir),
]

REGISTRY: dict[str, LocalTool] = {t.name: t for t in _TOOLS}

# Claude-style allowlist names -> local tool names.
_ALIAS = {"Read": "read_file", "Glob": "list_dir", "Grep": "list_dir", "LS": "list_dir"}


def select_tools(allowed: list[str] | None) -> list[LocalTool]:
    """Map an AgentTask.allowed_tools list onto the local registry.

    Accepts MCP-style names (``mcp__autocodabench__autocodabench_init_bundle``),
    wildcards (``mcp__autocodabench__*``), bare local names, and the Claude
    built-in aliases (Read/Glob/Grep). ``None`` → the full registry;
    ``[]`` → no tools (pure chat, e.g. judged checks).
    """
    if allowed is None:
        return list(_TOOLS)
    out: dict[str, LocalTool] = {}
    for name in allowed:
        if name.startswith("mcp__autocodabench__"):
            suffix = name.removeprefix("mcp__autocodabench__")
            if suffix == "*":
                out.update({t.name: t for t in _TOOLS if t.name.startswith("autocodabench_")})
            elif suffix in REGISTRY:
                out[suffix] = REGISTRY[suffix]
        elif name in _ALIAS:
            out[_ALIAS[name]] = REGISTRY[_ALIAS[name]]
        elif name in REGISTRY:
            out[name] = REGISTRY[name]
        # unknown names (e.g. other MCP servers) are silently unavailable
    return list(out.values())


def execute_tool(name: str, arguments: dict[str, Any]) -> str:
    """Execute one tool call; return a JSON string for the model.

    Mirrors the MCP layer's behavior: exceptions become ``{"error": ...}``
    results rather than crashes, and every call is snapshotted through
    run_log so the audit trail is backend-independent.
    """
    tool = REGISTRY.get(name)
    started = _now()
    t0 = time.perf_counter()
    run_log.log_event("tool_call_started", tool=name, args=arguments)
    if tool is None:
        result: Any = {"error": f"unknown tool: {name}"}
    else:
        try:
            result = tool.func(**arguments)
        except Exception as e:
            result = {"error": f"{name} failed: {type(e).__name__}: {e}"}
    duration_ms = int((time.perf_counter() - t0) * 1000)
    err = result.get("error") if isinstance(result, dict) else None
    run_log.snapshot_tool_call(
        tool=name, args=arguments, result=result, error=err,
        started_at=started, finished_at=_now(), duration_ms=duration_ms)
    run_log.log_event("tool_call_error" if err else "tool_call_finished",
                      tool=name, duration_ms=duration_ms, error=err)
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > _RESULT_CHARS_CAP:
        text = text[:_RESULT_CHARS_CAP] + '… (truncated; full record in tool_calls/)"}'
    return text
