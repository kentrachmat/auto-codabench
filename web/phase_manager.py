"""Phase lifecycle management for the AutoCodabench web UI.

PhaseManager handles all phase transitions (advance / revert), the hidden
cl.Action buttons that back the phase pills, and the post-turn bundle-ready
notification. It is the single owner of phase state mutations in the
Chainlit user session.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import chainlit as cl
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from artifacts import PhaseState, PublicArtifacts, Transcript, utc_now
from config import (
    CONTEXT_WINDOW_TOKENS,
    DEFAULT_MODEL,
    MAX_USD_PER_SESSION,
    PHASE_BUNDLE,
    PHASE_ORDER,
    PHASE_PLAN,
    PHASE_TITLE,
    PHASE_VALIDATE,
    PUBLIC_SESSIONS,
    TOOLS_BY_PHASE,
)

log = logging.getLogger("autocodabench.web.phase_manager")


# ---------------------------------------------------------------------------
# SDK client helpers
# ---------------------------------------------------------------------------

def _build_sdk_options(
    run_dir: Path, phase: str, mcp_servers: dict, model: str | None = None
) -> ClaudeAgentOptions:
    """Build ClaudeAgentOptions for the given phase.

    Each phase gets its own system prompt and tool allowlist. The budget cap
    is shared across the whole session (not reset per phase). The model is the
    one the user picked at session start (falls back to DEFAULT_MODEL).
    """
    from phases.plan import Plan
    from phases.bundle import Bundle
    import os
    from config import REPO_ROOT

    system_prompts = {
        PHASE_PLAN:   Plan.system_prompt,
        PHASE_BUNDLE: Bundle.system_prompt,
    }
    prompt_fn = system_prompts.get(phase, Plan.system_prompt)

    return ClaudeAgentOptions(
        model=model or DEFAULT_MODEL,
        system_prompt=prompt_fn(),
        mcp_servers=mcp_servers,
        max_budget_usd=MAX_USD_PER_SESSION,
        permission_mode="bypassPermissions",
        cwd=str(REPO_ROOT),
        env={**os.environ, "AUTOCODABENCH_RUN_DIR": str(run_dir)},
        allowed_tools=TOOLS_BY_PHASE.get(phase, []),
    )


async def _switch_sdk_client(run_dir: Path, target: str, mcp_servers: dict) -> None:
    """Disconnect the current SDK client and stand up a fresh one for target."""
    old = cl.user_session.get("client")
    if old is not None:
        try:
            await old.disconnect()
        except Exception as e:
            log.warning("disconnect on phase switch failed: %s", e)
    model = cl.user_session.get("model") or DEFAULT_MODEL
    new_client = ClaudeSDKClient(options=_build_sdk_options(run_dir, target, mcp_servers, model))
    await new_client.connect()
    cl.user_session.set("client", new_client)


# ---------------------------------------------------------------------------
# PhaseManager
# ---------------------------------------------------------------------------

class PhaseManager:
    """Manages phase transitions, phase bar state, and bundle-ready offers."""

    @staticmethod
    def reset_phase_session_state() -> None:
        """Wipe per-phase ephemeral state on every phase transition."""
        cl.user_session.set("last_input_tokens", 0)
        cl.user_session.set("last_output_tokens", 0)
        cl.user_session.set("bundle_actions_offered", False)

    @staticmethod
    def delete_phase_artifact(run_dir: Path, phase: str) -> None:
        """Remove a phase's artifact and its public copies.

        Called on back-navigation: discards everything downstream so the
        next forward advance regenerates from the (possibly edited) earlier
        artifact. Plan is never auto-deleted — back-nav to Plan means EDIT.
        """
        try:
            if phase == PHASE_BUNDLE:
                session_bundles = run_dir / "bundles"
                if session_bundles.is_dir():
                    shutil.rmtree(session_bundles, ignore_errors=True)
                sid = cl.user_session.get("session_id") or ""
                if sid:
                    pub = PUBLIC_SESSIONS / sid / "bundle.zip"
                    if pub.is_file():
                        pub.unlink()
            elif phase == PHASE_VALIDATE:
                report = run_dir / "validation_report.md"
                if report.is_file():
                    report.unlink()
        except Exception as e:
            log.warning("delete artifact for %s failed: %s", phase, e)

    @staticmethod
    def write_state(run_dir: Path) -> None:
        """Write phase_state.json and public artifacts for the current session."""
        sid     = cl.user_session.get("session_id") or ""
        current = cl.user_session.get("phase") or PHASE_PLAN
        history = list(cl.user_session.get("phase_history") or [])
        PhaseState.write(
            run_dir, sid,
            current=current,
            history=history,
            last_input_tokens=int(cl.user_session.get("last_input_tokens") or 0),
            last_output_tokens=int(cl.user_session.get("last_output_tokens") or 0),
            cum_cost=float(cl.user_session.get("cum_cost_usd") or 0.0),
            max_usd=MAX_USD_PER_SESSION,
            context_window=CONTEXT_WINDOW_TOKENS,
            input_mode=str(cl.user_session.get("input_mode") or "normal"),
        )
        PublicArtifacts.write(run_dir, sid)

    @staticmethod
    async def maybe_offer_proceed_to_build(run_dir: Path) -> None:
        """Once Phase 1's plan exists, surface a one-time 'Proceed to Phase 2' button.

        Phase advancement in the guided wizard is driven by explicit buttons
        (not the progress-only pills). Fires at most once per session.
        """
        if cl.user_session.get("phase") != PHASE_PLAN:
            return
        if cl.user_session.get("proceed_to_build_offered"):
            return
        if not PhaseState.artifact_exists(run_dir, PHASE_PLAN):
            return
        cl.user_session.set("proceed_to_build_offered", True)
        await cl.Message(
            author="autocodabench",
            content=(
                "### ✅ Plan ready\n\n"
                "`implementation_plan.md` is saved (open it in the workspace "
                "panel). When you're happy with it, proceed to build the bundle."
            ),
            actions=[cl.Action(
                name="ac_advance_phase",
                payload={"target": PHASE_BUNDLE},
                label="▶ Proceed to Phase 2 — Build the bundle",
            )],
        ).send()

    @staticmethod
    async def advance_to_phase(target: str) -> None:
        """Move forward to target. Delegates to revert if target is behind current."""
        log.info("[phase] advance_to_phase called: target=%r", target)
        if target not in PHASE_ORDER:
            log.warning("[phase] advance_to_phase: unknown target %r — ignoring", target)
            return
        run_dir = Path(cl.user_session.get("run_dir"))
        current = cl.user_session.get("phase") or PHASE_PLAN
        log.info("[phase] advancing %r → %r", current, target)

        if target == current:
            log.info("[phase] already on %r — no-op", target)
            return
        tgt_idx = PHASE_ORDER.index(target)
        cur_idx = PHASE_ORDER.index(current)

        if tgt_idx < cur_idx:
            log.info("[phase] target is behind current — delegating to revert_to_phase")
            await PhaseManager.revert_to_phase(target)
            return

        log.info("[phase] checking artifact_exists for phase=%r in run_dir=%s", current, run_dir)
        if not PhaseState.artifact_exists(run_dir, current):
            log.warning("[phase] artifact missing for %r — blocking advance", current)
            await cl.Message(
                author="autocodabench",
                content=(
                    f"⚠ Can't advance to {PHASE_TITLE[target]} — "
                    f"{PHASE_TITLE[current]} hasn't produced "
                    f"`{cl.user_session.get('phase_artifact', '')}` yet."
                ),
            ).send()
            return

        history = list(cl.user_session.get("phase_history") or [])
        if current not in history:
            history.append(current)
        cl.user_session.set("phase_history", history)

        PhaseManager.reset_phase_session_state()
        cl.user_session.set("phase", target)

        # Phase 3 (validate) runs pure Python — no agent, no MCP subprocess.
        # Skip the SDK client switch to avoid spawning an unused subprocess.
        if target != PHASE_VALIDATE:
            mcp_servers = cl.user_session.get("mcp_servers") or {}
            log.info("[phase] switching SDK client to phase=%r", target)
            await _switch_sdk_client(run_dir, target, mcp_servers)
            log.info("[phase] SDK client switched")
        else:
            log.info("[phase] Phase 3 — skipping SDK client switch (no agent needed)")

        Transcript.append(run_dir, role="user", text=f"[ui] Advance to {PHASE_TITLE[target]}.")
        log.info("[phase] sending phase kickoff for target=%r", target)
        await PhaseManager._send_phase_kickoff(run_dir, target)
        log.info("[phase] phase kickoff complete for target=%r", target)

        PhaseManager.write_state(run_dir)
        await PhaseManager.maybe_offer_bundle_actions()
        log.info("[phase] advance_to_phase DONE: now on %r", target)

    @staticmethod
    async def revert_to_phase(target: str) -> None:
        """Move back to target, discarding all downstream artifacts."""
        if target not in PHASE_ORDER:
            return
        run_dir = Path(cl.user_session.get("run_dir"))
        current = cl.user_session.get("phase") or PHASE_PLAN

        if target == current:
            return
        tgt_idx = PHASE_ORDER.index(target)
        cur_idx = PHASE_ORDER.index(current)

        if tgt_idx >= cur_idx:
            await PhaseManager.advance_to_phase(target)
            return

        for ph in PHASE_ORDER[tgt_idx + 1:]:
            PhaseManager.delete_phase_artifact(run_dir, ph)

        history = [p for p in (cl.user_session.get("phase_history") or [])
                   if PHASE_ORDER.index(p) < tgt_idx]
        cl.user_session.set("phase_history", history)

        PhaseManager.reset_phase_session_state()
        cl.user_session.set("phase", target)

        mcp_servers = cl.user_session.get("mcp_servers") or {}
        await _switch_sdk_client(run_dir, target, mcp_servers)

        Transcript.append(
            run_dir, role="user",
            text=f"[ui] Back to {PHASE_TITLE[target]} — discarded downstream artifacts.",
        )
        await PhaseManager._send_phase_revisit(run_dir, target)

        PhaseManager.write_state(run_dir)

    @staticmethod
    async def _send_phase_kickoff(run_dir: Path, target: str) -> None:
        """Dispatch to the phase-specific kickoff handler."""
        log.info("[phase] _send_phase_kickoff: target=%r", target)
        client = cl.user_session.get("client")
        if target == PHASE_BUNDLE:
            from phases.bundle import Bundle
            log.info("[phase] calling Bundle.send_kickoff_message")
            await Bundle.send_kickoff_message(run_dir, client)
            log.info("[phase] Bundle.send_kickoff_message returned")
        elif target == PHASE_VALIDATE:
            from phases.validate import Validate
            log.info("[phase] calling Validate.send_kickoff_message")
            await Validate.send_kickoff_message(run_dir, client)
            log.info("[phase] Validate.send_kickoff_message returned")
        else:
            log.info("[phase] no kickoff handler for target=%r", target)

    @staticmethod
    async def _send_phase_revisit(run_dir: Path, target: str) -> None:
        """Dispatch to the phase-specific revisit handler (back-navigation)."""
        if target == PHASE_PLAN:
            from phases.plan import Plan
            await Plan.send_revisit_message()

    @staticmethod
    async def maybe_offer_bundle_actions() -> None:
        """Surface a one-time 'Bundle ready' message once Phase 2's zip exists.

        All download + publish UX lives in the workspace panel. This chat
        message is just a pointer; it fires at most once per session.
        Not shown in Phase 3 — the user is past the bundle step.
        """
        if cl.user_session.get("bundle_actions_offered"):
            return
        if cl.user_session.get("phase") == PHASE_VALIDATE:
            return
        run_dir    = Path(cl.user_session.get("run_dir") or ".")
        session_id = cl.user_session.get("session_id") or ""
        if not session_id:
            return
        public_zip = PUBLIC_SESSIONS / session_id / "bundle.zip"
        if not public_zip.is_file():
            return
        cl.user_session.set("bundle_actions_offered", True)

        size_mb      = public_zip.stat().st_size / (1024 * 1024)
        download_url = f"/public/sessions/{session_id}/bundle.zip"
        await cl.Message(
            author="autocodabench",
            content=(
                f"## Bundle ready ({size_mb:.1f} MB)\n\n"
                f"Open the **workspace panel on the right** to:\n\n"
                f"- **Download** `bundle.zip` (or `workspace.zip` for "
                f"everything: plan + transcript + cost + bundle).\n"
                f"- **Publish to Codabench**: enter your Codabench username + "
                f"password in the form at the bottom of the panel and "
                f"click *Upload &amp; publish*.\n\n"
                f"_Direct link:_ **[bundle.zip]({download_url})**\n\n"
                f"---\n\n"
                f"**Next — Phase 3 validation** runs the full check framework "
                f"and **executes your bundle in Docker** using the "
                f"`docker_image` from `competition.yaml` (~5–10 min for a "
                f"verified bundle; it pulls the image if needed)."
            ),
            actions=[cl.Action(
                name="ac_advance_phase",
                payload={"target": PHASE_VALIDATE},
                label="▶ Proceed to Phase 3 — Validate the bundle",
            )],
        ).send()
