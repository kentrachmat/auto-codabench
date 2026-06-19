"""Central configuration for the AutoCodabench web UI.

All phase definitions, tool allowlists, and runtime constants live here.
app.py and the phase/session modules import from this file — never the
other way around, so there are no circular imports.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Runtime paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PYTHON_BIN = os.environ.get("AUTOCODABENCH_PYTHON", sys.executable)

# ---------------------------------------------------------------------------
# Model + cost
# ---------------------------------------------------------------------------

DEFAULT_MODEL         = os.environ.get("AUTOCODABENCH_DEFAULT_MODEL", "claude-sonnet-4-6")
MAX_USD_PER_SESSION   = float(os.environ.get("MAX_USD_PER_SESSION", "5.0"))
CONTEXT_WINDOW_TOKENS = int(os.environ.get("AUTOCODABENCH_CONTEXT_WINDOW", "200000"))

# Models the user can pick from in the docked selector. The CLI exposes the
# full `--backend`/`--model` surface; the web UI offers a curated short list.
# For now Sonnet 4.6 only — add entries here (e.g. Opus, or OpenAI-compatible
# backbones already supported by the CLI's --backend) to grow the picker. The
# first entry is the fallback when DEFAULT_MODEL isn't in the list.
MODEL_CHOICES = [
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
]
MODEL_LABELS = {m["id"]: m["label"] for m in MODEL_CHOICES}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

SHARED_PASSWORD = os.environ.get("SHARED_PASSWORD", "")

# ---------------------------------------------------------------------------
# Hugging Face persistence
# ---------------------------------------------------------------------------

HF_RUNS_REPO = os.environ.get("AUTOCODABENCH_RUNS_REPO", "ktgiahieu/autocodabench-runs")
HF_TOKEN     = os.environ.get("HF_TOKEN", "")

# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------

PHASE_PLAN     = "plan"
PHASE_BUNDLE   = "bundle"
PHASE_VALIDATE = "validate"

PHASE_ORDER = [PHASE_PLAN, PHASE_BUNDLE, PHASE_VALIDATE]

PHASE_TITLE = {
    PHASE_PLAN:     "📝 Plan",
    PHASE_BUNDLE:   "📦 Competition Creation",
    PHASE_VALIDATE: "✅ Validation",
}

# The artifact each phase must produce before the user can advance forward.
PHASE_ARTIFACT = {
    PHASE_PLAN:     "specs/implementation_plan.md",
    PHASE_BUNDLE:   "bundle.zip",
    PHASE_VALIDATE: "validation_report.md",
}

# ---------------------------------------------------------------------------
# Per-phase tool allowlists
#
# Narrow allowlists reduce per-turn input tokens by keeping unused tool
# definitions out of the system prompt.
# ---------------------------------------------------------------------------

PLAN_TOOLS = [
    "mcp__autocodabench__autocodabench_open_run",
    "mcp__autocodabench__autocodabench_current_run",
    "mcp__autocodabench__autocodabench_log_event",
    "mcp__autocodabench__autocodabench_snapshot_spec",
    "mcp__alex-mcp__*",
    "Read", "Grep", "Glob",
]

BUNDLE_TOOLS = [
    "mcp__autocodabench__*",
    "mcp__alex-mcp__*",
    "Read", "Grep", "Glob",
]

VALIDATE_TOOLS = [
    "mcp__autocodabench__autocodabench_validate_bundle",
    "mcp__autocodabench__autocodabench_current_run",
    "Read", "Grep", "Glob",
]

TOOLS_BY_PHASE = {
    PHASE_PLAN:     PLAN_TOOLS,
    PHASE_BUNDLE:   BUNDLE_TOOLS,
    PHASE_VALIDATE: VALIDATE_TOOLS,
}

# ---------------------------------------------------------------------------
# Public artifact paths (served as static files by Chainlit)
# ---------------------------------------------------------------------------

PUBLIC_DIR      = Path(__file__).resolve().parent / "public"
PUBLIC_SESSIONS = PUBLIC_DIR / "sessions"
