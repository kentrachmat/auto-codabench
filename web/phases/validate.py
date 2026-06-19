"""Phase 3 — Validation (Chainlit UI layer only).

The validation *feature* lives in the package (`autocodabench.checks`): running
the framework (`validate_bundle_path`), loading the Phase-1 design scorecard
(`load_design_assessment`), and rendering the report (`render_report_markdown`,
`render_judged_section`). This module is just the web presentation:

  - `send_kickoff_message` — Option A (create-from-scratch): validate the
    bundle Phase 2 produced.
  - `run_validation` — shared driver; also called for Option B (validate an
    uploaded bundle) from session_manager.
  - the interactive "also run LLM-judged checks?" prompt (UI-only).
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import chainlit as cl

from artifacts import PublicArtifacts, Transcript
from config import PHASE_VALIDATE, PHASE_TITLE

log = logging.getLogger("autocodabench.web.validate")


class Validate:
    """Encapsulates everything specific to Phase 3 (Validation)."""

    PHASE_ID = PHASE_VALIDATE

    @staticmethod
    def system_prompt() -> str:
        """Stub — Phase 3 runs the check framework directly, no agent."""
        return "(Phase 3 runs the deterministic check framework directly — no agent.)"

    @staticmethod
    async def send_kickoff_message(run_dir: Path, client) -> None:  # noqa: ARG004
        """Option A entry: validate the bundle Phase 2 produced."""
        log.info("[validate] send_kickoff_message start — run_dir=%s", run_dir)
        bundle_zip = PublicArtifacts.find_bundle_zip(run_dir)
        if bundle_zip is None:
            await cl.Message(
                author="autocodabench",
                content=(
                    f"# {PHASE_TITLE[PHASE_VALIDATE]}\n\n"
                    "⚠️ No bundle found. Go back to **Phase 2** and make sure the "
                    "agent finished and zipped the bundle."
                ),
            ).send()
            return
        await Validate.run_validation(run_dir, bundle_zip, executed_in_phase2=True)

    @staticmethod
    async def run_validation(run_dir: Path, bundle_path: Path, *,
                             executed_in_phase2: bool = False) -> None:
        """Run the check framework on `bundle_path` and surface the report.

        Always runs `execute=True` (Docker baseline). For Option A the build
        phase's run is reused when the bundle is unchanged.
        """
        session_id = cl.user_session.get("session_id") or ""
        bundle_path = Path(bundle_path)
        if not bundle_path.exists():
            await cl.Message(
                author="autocodabench",
                content=f"**Validation error:** bundle not found at `{bundle_path}`.",
            ).send()
            return

        await cl.Message(
            author="autocodabench",
            content=(
                f"# {PHASE_TITLE[PHASE_VALIDATE]}\n\n"
                f"Running the autocodabench check framework against "
                f"`{bundle_path.name}` — executing the baseline in **Docker** "
                f"(this can take several minutes; it pulls the bundle's "
                f"`docker_image` if needed)…"
            ),
        ).send()

        # --- deterministic + execution pass (off the event loop) ---
        # Animated "Running checks…" indicator so the UI doesn't look frozen
        # during the (potentially multi-minute) Docker baseline run — the same
        # moving-blob feedback Phases 1/2 give while the agent works.
        from streaming import RunningIndicator
        indicator = RunningIndicator(
            "Running checks", status="executing the baseline in Docker")
        await indicator.start()
        try:
            report = await asyncio.to_thread(_run_validation_sync, bundle_path, True)
            log.info("[validate] done: ok=%s counts=%s", report.ok, report.counts)
        except Exception as e:
            log.exception("[validate] validation raised: %s", e)
            await cl.Message(
                author="autocodabench",
                content=(
                    f"**Validation error:** `{type(e).__name__}: {e}`\n\n"
                    "You can still download the bundle and validate it locally:\n"
                    "```\nautocodabench validate <path/to/bundle.zip>\n```"
                ),
            ).send()
            return
        finally:
            await indicator.stop()

        # --- design scorecard (Phase-1 artifact; absent for Option B) ---
        from autocodabench.checks import load_design_assessment, render_report_markdown
        assessment = load_design_assessment(run_dir)

        # --- write artifacts (same rich report the CLI produces) ---
        _write_reports(run_dir, report, assessment)
        Transcript.append(run_dir, role="claude",
                          text=report.to_markdown(design_assessment=assessment))
        if session_id:
            PublicArtifacts.write(run_dir, session_id)

        # --- chat: the full report + a pointer to the panel ---
        body = render_report_markdown(report, design_assessment=assessment)
        await cl.Message(
            author="autocodabench",
            content=(
                body + "\n\n_Open the **✅ validation_report.md** tab in the "
                "workspace panel for the downloadable report._"
            ),
        ).send()

        # --- offer the LLM-judged step ---
        await Validate._offer_judged(run_dir, bundle_path, session_id)

    @staticmethod
    async def _offer_judged(run_dir: Path, bundle_path: Path, session_id: str) -> None:
        """Ask whether to also run LLM-judged checks; run + append if yes."""
        res = await cl.AskActionMessage(
            content=(
                "### Also run LLM-judged validation?\n\n"
                "It runs `judged-docs-config-consistency` — an LLM reads your "
                "participant-facing `pages/*.md` and flags **contradictions** "
                "against `competition.yaml` (e.g. a page promises a metric or "
                "submission limit the config doesn't declare). It's **advisory "
                "only** — it never changes the pass/fail verdict above. Needs "
                "Claude auth, ~30–60s and a small token cost."
            ),
            actions=[
                cl.Action(name="judged", payload={"run": "yes"}, label="✨ Yes, run LLM-judged checks"),
                cl.Action(name="judged", payload={"run": "no"}, label="Skip"),
            ],
            timeout=300,
        ).send()
        if (((res or {}).get("payload") or {}).get("run")) != "yes":
            await cl.Message(author="autocodabench",
                             content="_Skipped LLM-judged validation._").send()
            return

        from streaming import RunningIndicator
        indicator = RunningIndicator(
            "Running LLM-judged checks", status="reading pages vs competition.yaml")
        await indicator.start()
        try:
            from autocodabench.checks import (
                validate_bundle_path_async, render_judged_section,
            )
            from autocodabench.backends import resolve_backend
            from config import DEFAULT_MODEL

            model   = cl.user_session.get("model") or DEFAULT_MODEL
            backend = resolve_backend("claude", model=model)
            jreport = await validate_bundle_path_async(
                bundle_path, execute=False, judged=True, backend=backend,
            )
        except Exception as e:
            log.warning("[validate] judged pass failed: %s", e)
            await cl.Message(
                author="autocodabench",
                content=(
                    f"_LLM-judged validation unavailable: "
                    f"`{type(e).__name__}: {e}`. (Needs Claude auth.)_"
                ),
            ).send()
            return
        finally:
            await indicator.stop()

        section = render_judged_section(jreport)
        _append_to_report(run_dir, "\n\n---\n\n" + section + "\n")
        if session_id:
            PublicArtifacts.write(run_dir, session_id)
        await cl.Message(author="autocodabench", content=section).send()


# ---------------------------------------------------------------------------
# Module helpers (thin glue over the package)
# ---------------------------------------------------------------------------

def _run_validation_sync(bundle_path: Path, execute: bool):
    """Sync wrapper run inside a thread so the event loop stays responsive."""
    from autocodabench.checks import validate_bundle_path
    return validate_bundle_path(bundle_path, execute=execute)


def _write_reports(run_dir: Path, report, assessment) -> None:
    try:
        (run_dir / "validation_report.md").write_text(
            report.to_markdown(design_assessment=assessment), encoding="utf-8")
        (run_dir / "validation_report.json").write_text(
            json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    except OSError as e:
        log.warning("[validate] failed to write report files: %s", e)


def _append_to_report(run_dir: Path, text: str) -> None:
    try:
        path = run_dir / "validation_report.md"
        prior = path.read_text(encoding="utf-8") if path.is_file() else ""
        path.write_text(prior + text, encoding="utf-8")
    except OSError as e:
        log.warning("[validate] failed to append to report: %s", e)
