"""AutoCodabench web UI — entry point.

This file is what Chainlit scans at startup. It registers auth, session
lifecycle hooks, and action callbacks — all actual logic lives in the
dedicated modules:

  config.py          — constants, phase definitions, tool allowlists
  session_manager.py — on_chat_start / on_message / on_chat_end
  phase_manager.py   — advance / revert / phase bar controls
  phases/plan.py     — Phase 1 system prompt + revisit message
  phases/bundle.py   — Phase 2 system prompt + kickoff
  phases/validate.py — Phase 3 system prompt + kickoff (placeholder)
  streaming.py       — shared agent response streaming loop
  artifacts.py       — transcript, cost log, public HTML + manifest
  upload_route.py    — POST /ac/upload-codabench FastAPI route
  hf_persist.py      — HF Dataset persistence
  skills.py          — SKILL.md loader
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: add repo root to sys.path and load .env before any package
# imports so env vars are available when packages initialise.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
load_dotenv(_REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# Logging: emit INFO from our own loggers so step-level traces are visible.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logging.getLogger("autocodabench").setLevel(logging.INFO)

os.environ.setdefault("AUTOCODABENCH_HOME", str(_REPO_ROOT / ".autocodabench"))

# ---------------------------------------------------------------------------
# Chainlit + local modules
# ---------------------------------------------------------------------------

import chainlit as cl

from config import SHARED_PASSWORD
from phase_manager import PhaseManager
from session_manager import SessionManager
from upload_route import register_upload_route

log = logging.getLogger("autocodabench.web")

# Register the upload API route on Chainlit's FastAPI app at import time.
register_upload_route()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@cl.password_auth_callback
def auth_callback(username: str, password: str):
    """Single shared password gate. The username field is informational only."""
    if not SHARED_PASSWORD:
        return cl.User(identifier="anon", metadata={"warning": "SHARED_PASSWORD not set"})
    if password == SHARED_PASSWORD:
        return cl.User(identifier=username or "guest", metadata={})
    return None


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_chat_start():
    await SessionManager.on_chat_start()


@cl.on_message
async def on_message(msg: cl.Message):
    await SessionManager.on_message(msg)


@cl.on_settings_update
async def on_settings_update(settings: dict):
    """Backs the model selector docked at the input bar (cl.ChatSettings).

    Fires whenever the user changes a setting mid-conversation — we hot-swap
    the live SDK client's model with client.set_model(), preserving context.
    """
    await SessionManager.on_settings_update(settings)


@cl.on_chat_end
async def on_chat_end():
    await SessionManager.on_chat_end()


# ---------------------------------------------------------------------------
# Phase bar action callbacks
# ---------------------------------------------------------------------------

@cl.action_callback("ac_advance_phase")
async def on_advance_phase(action: cl.Action):
    """Backs the explicit '▶ Proceed to Phase N' buttons surfaced at phase boundaries."""
    target = (action.payload or {}).get("target")
    await PhaseManager.advance_to_phase(str(target))
