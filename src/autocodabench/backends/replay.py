"""Deterministic, keyless replay of a recorded agentic run.

A replay fixture is the sequence of bundle-authoring tool calls an agent
made, replayed against the *real* core layer — so the bundle is genuinely
rebuilt, validated, and zipped on the user's machine, with zero LLM access.
This is what makes ``autocodabench demo --replay`` and CI work without keys.

Fixture sources:

- a ``.jsonl`` file — one ``{"tool": ..., "args": {...}}`` record per line
  (shipped fixtures live in ``autocodabench/backends/fixtures/``);
- a run directory — its ``tool_calls/NNNN_*.json`` snapshots (written by
  ``run_log.logged_tool`` on every live run) are read in order, so any real
  run is replayable as-is.

Path-bearing recorded args (``root_dir``, ``output``) are stripped and
re-targeted at the replay output directory; ``attach_data(from_path=...)``
records are skipped (they referenced files on the recording machine).
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Callable

from ..core import bundle_io
from .base import AgentRunResult, AgentTask

log = logging.getLogger("autocodabench.backends.replay")

# Tool name -> core function. Args are passed through minus the path args.
_TOOL_FUNCS: dict[str, Callable[..., Any]] = {
    "autocodabench_init_bundle": bundle_io.init_bundle,
    "autocodabench_write_competition_yaml": bundle_io.write_competition_yaml,
    "autocodabench_write_page": bundle_io.write_page,
    "autocodabench_write_scoring_program": bundle_io.write_scoring_program,
    "autocodabench_write_ingestion_program": bundle_io.write_ingestion_program,
    "autocodabench_write_solution": bundle_io.write_solution,
    "autocodabench_attach_data": bundle_io.attach_data,
    "autocodabench_validate_bundle": bundle_io.validate_bundle,
    "autocodabench_zip_bundle": bundle_io.zip_bundle,
}

# Run/log/runner tools are session bookkeeping or host-specific — not replayed.
_SKIP_TOOLS = {
    "autocodabench_open_run",
    "autocodabench_current_run",
    "autocodabench_log_event",
    "autocodabench_snapshot_spec",
    "autocodabench_prepare_run_env",
    "autocodabench_install_env_extras",
    "autocodabench_run_baseline_submission",
    "autocodabench_run_user_submission",
    "autocodabench_run_starting_kit",
    "autocodabench_remove_run_env",
    "autocodabench_upload_bundle",
}


def load_fixture(source: str | Path) -> tuple[list[dict[str, Any]], str | None]:
    """Load fixture records + optional final text from a .jsonl file or run dir."""
    src = Path(source)
    records: list[dict[str, Any]] = []
    final_text: str | None = None

    if src.is_dir():
        calls_dir = src / "tool_calls" if (src / "tool_calls").is_dir() else src
        for p in sorted(calls_dir.glob("*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            if "tool" in data:
                records.append({"tool": data["tool"], "args": data.get("args") or {}})
        ft = src / "final_text.md"
        if ft.is_file():
            final_text = ft.read_text(encoding="utf-8")
    elif src.is_file():
        for line in src.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if "final_text" in data:
                final_text = data["final_text"]
            elif "tool" in data:
                records.append(data)
    else:
        raise FileNotFoundError(f"replay fixture not found: {src}")

    return records, final_text


class ReplayBackend:
    """Replay a recorded run's authoring tool calls against the core layer."""

    name = "replay"

    def __init__(self, fixture: str | Path, *, out_dir: str | Path) -> None:
        self.fixture = Path(fixture)
        self.out_dir = Path(out_dir)

    async def run(self, task: AgentTask) -> AgentRunResult:
        records, final_text = load_fixture(self.fixture)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        replayed = 0
        skipped: list[str] = []
        last_results: dict[str, Any] = {}
        for rec in records:
            tool = rec["tool"]
            args = dict(rec.get("args") or {})

            if tool == "_write_file":
                # Fixture-local primitive: the live agent writes a few files
                # (e.g. the logo) with its own file tools rather than MCP;
                # fixtures express those as raw writes inside the bundle dir.
                rel = args["path"]
                slug = args["slug"]
                target = bundle_io.resolve_bundle_dir(slug, str(self.out_dir)) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if "base64" in args:
                    target.write_bytes(base64.b64decode(args["base64"]))
                else:
                    target.write_text(args.get("text", ""), encoding="utf-8")
                replayed += 1
                self._emit(task, f"write {rel}")
                continue

            if tool in _SKIP_TOOLS:
                skipped.append(tool)
                continue
            func = _TOOL_FUNCS.get(tool)
            if func is None:
                skipped.append(tool)
                log.warning("replay: unknown tool %r skipped", tool)
                continue
            if tool == "autocodabench_attach_data" and args.get("from_path"):
                skipped.append(f"{tool}(from_path)")
                log.warning("replay: attach_data(from_path=...) skipped — "
                            "recorded path is machine-specific")
                continue

            # Re-target every path-bearing arg at the replay output dir.
            args.pop("root_dir", None)
            args.pop("output", None)
            result = func(**args, root_dir=str(self.out_dir))
            last_results[tool] = result
            replayed += 1
            self._emit(task, f"{tool.removeprefix('autocodabench_')} → ok")

        validation = last_results.get("autocodabench_validate_bundle")
        zipped = last_results.get("autocodabench_zip_bundle")
        summary = final_text or "Replay complete."
        details = []
        if validation is not None:
            details.append(f"validate: ok={validation.get('ok')} "
                           f"issues={len(validation.get('issues') or [])}")
        if zipped is not None:
            details.append(f"zip: {zipped.get('zip_path')}")
        if details:
            summary = summary.rstrip() + "\n\n" + "\n".join(details)

        ok = validation is None or bool(validation.get("ok"))
        return AgentRunResult(
            status="success" if ok else "error",
            final_text=summary,
            num_turns=replayed,
            total_cost_usd=0.0,
            usage={"replayed_calls": replayed, "skipped_calls": skipped},
            error=None if ok else "replayed bundle failed validation",
        )

    @staticmethod
    def _emit(task: AgentTask, text: str) -> None:
        if task.on_text is not None:
            task.on_text(text)
