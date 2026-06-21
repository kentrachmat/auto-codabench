"""Session lifecycle management for the AutoCodabench web UI.

SessionManager owns everything that happens at chat start, per message, and
chat end. It sets up the isolated run dir, probes MCP imports, builds the
first SDK client, routes user messages, handles file attachments, and
triggers post-turn artifact writes and HF persistence.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import chainlit as cl
from chainlit.input_widget import Select
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from artifacts import PublicArtifacts, Transcript, utc_now
from config import (
    CONTEXT_WINDOW_TOKENS,
    DEFAULT_MODEL,
    MAX_USD_PER_SESSION,
    MODEL_CHOICES,
    MODEL_LABELS,
    PHASE_PLAN,
    PHASE_TITLE,
    PHASE_VALIDATE,
    PYTHON_BIN,
    REPO_ROOT,
    TOOLS_BY_PHASE,
)
from hf_persist import persist_to_hf
from phase_manager import PhaseManager, _build_sdk_options
from streaming import run_agent_turn

log = logging.getLogger("autocodabench.web.session")

_ATTACHMENT_MAX_CHARS = 60_000
_PDF_MIME = "application/pdf"

# ---------------------------------------------------------------------------
# MCP server configuration
# ---------------------------------------------------------------------------

def build_mcp_servers(run_dir: Path) -> dict:
    """Return stdio MCP server configs scoped to this session's run dir."""
    env_for_mcp = {**os.environ, "AUTOCODABENCH_RUN_DIR": str(run_dir)}
    return {
        "autocodabench": {
            "type": "stdio",
            "command": PYTHON_BIN,
            "args": ["-m", "autocodabench.mcp.server"],
            "env": env_for_mcp,
        },
        "alex-mcp": {
            "type": "stdio",
            "command": PYTHON_BIN,
            "args": ["-m", "alex_mcp.server"],
            "env": env_for_mcp,
        },
    }


def probe_mcp_imports() -> list[str]:
    """Test that both MCP server modules import cleanly in a subprocess.

    Returns a list of human-readable error lines. An empty list means both
    servers are importable. Runs in a subprocess so an ImportError in one
    module can't crash the web process.
    """
    diag_snippet = (
        "import fastmcp, pathlib;"
        "p = pathlib.Path(fastmcp.__file__).parent;"
        "print('fastmcp', fastmcp.__version__);"
        "print('oauth_proxy as file:', (p / 'server/auth/oauth_proxy.py').is_file());"
        "print('oauth_proxy as pkg:', (p / 'server/auth/oauth_proxy/__init__.py').is_file())"
    )
    probes = {
        "autocodabench": "import autocodabench.mcp.server",
        "alex-mcp":      "import alex_mcp.server",
    }
    failures: list[str] = []
    for name, snippet in probes.items():
        try:
            result = subprocess.run(
                [PYTHON_BIN, "-c", snippet],
                capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"`{name}`: import probe timed out after 15s")
            continue
        if result.returncode != 0:
            err  = (result.stderr or result.stdout or "").strip().splitlines()
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


# ---------------------------------------------------------------------------
# Attachment extraction
# ---------------------------------------------------------------------------

def _extract_attachment_text(element) -> tuple[str, str] | None:
    """Return (label, body_text) for one attached file element, or None.

    Supported: PDF (via pypdf), .md, .txt. Other types are skipped silently.
    """
    path = getattr(element, "path", None)
    name = getattr(element, "name", None) or (Path(path).name if path else "<unknown>")
    mime = (getattr(element, "mime", "") or "").lower()
    if not path or not Path(path).exists():
        return None
    try:
        if mime == _PDF_MIME or name.lower().endswith(".pdf"):
            from pypdf import PdfReader
            reader  = PdfReader(path)
            pages   = []
            for i, page in enumerate(reader.pages):
                try:
                    pages.append(page.extract_text() or "")
                except Exception as e:
                    pages.append(f"[page {i + 1}: extraction failed: {e}]")
            body    = "\n\n".join(pages).strip()
            label   = f"{name} (PDF, {len(reader.pages)} pages)"
        elif mime in ("text/plain", "text/markdown") or name.lower().endswith((".md", ".txt")):
            body  = Path(path).read_text(encoding="utf-8", errors="replace")
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
            f"\n\n[…truncated at {_ATTACHMENT_MAX_CHARS:,} chars]"
        )
    return (label, body)


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    """Handles the Chainlit session lifecycle and per-turn message routing."""

    @staticmethod
    async def on_chat_start() -> None:
        """Initialise a new chat session.

        1. Guard against duplicate firings (reconnect / second tab).
        2. Create an isolated run dir with the layout the MCP server expects.
        3. Probe MCP imports and surface any startup failures immediately.
        4. Build the Phase 1 SDK client and send the greeting.
        5. Pre-write public artifacts so the workspace panel paints on load.
        """
        # Guard: on_chat_start re-fires on websocket reconnect or duplicate tabs.
        if cl.user_session.get("session_id"):
            log.info("on_chat_start re-fired for existing session %s — skipping",
                     cl.user_session.get("session_id"))
            cl.user_session.set("ready", True)
            return

        cl.user_session.set("ready", False)

        # 1. Per-session isolated run dir.
        from autocodabench.core.config import runs_root as _acb_runs_root
        RUNS_ROOT  = _acb_runs_root()
        session_id = uuid.uuid4().hex[:12]
        user       = cl.user_session.get("user")
        user_id    = (user.identifier if user else "anon").replace("/", "_")
        runtime_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        run_dir    = RUNS_ROOT / f"web_{user_id}_{runtime_id}_{session_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        for sub in ("tool_calls", "specs", "specs_history", "mcp_stderr"):
            (run_dir / sub).mkdir(exist_ok=True)

        meta = {
            "started_at": utc_now(),
            "branch_id":  f"web-{user_id}",
            "runtime_id": runtime_id,
            "slug":       f"web_{session_id}",
            "session_id": session_id,
            "user":       user_id,
            "git_sha":    None,
            "cwd":        str(REPO_ROOT),
            "pid":        os.getpid(),
            "created_by": "web/session_manager.py:on_chat_start",
        }
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        cl.user_session.set("run_dir",          str(run_dir))
        cl.user_session.set("session_id",       session_id)
        cl.user_session.set("started_at",       utc_now())
        cl.user_session.set("had_user_message", False)

        # 2. MCP probe.
        mcp_servers   = build_mcp_servers(run_dir)
        cl.user_session.set("mcp_servers", mcp_servers)

        mcp_failures = probe_mcp_imports()
        if mcp_failures:
            await cl.Message(
                content=(
                    "**⚠️ MCP servers failed to start.** Tools will be "
                    "unavailable in this session.\n\n"
                    + "\n\n".join(mcp_failures)
                ),
                author="autocodabench",
            ).send()

        # 3. Per-session bookkeeping.
        cl.user_session.set("phase_history",      [])
        cl.user_session.set("last_input_tokens",  0)
        cl.user_session.set("last_output_tokens", 0)
        cl.user_session.set("cum_cost_usd",       0.0)
        cl.user_session.set("input_mode",         "normal")

        # 4. Upfront wizard: what does the user want to do this session?
        mode = await SessionManager._ask_entry_mode()
        cl.user_session.set("entry_mode", mode)

        # Dock a model selector at the input bar (Claude.ai-style). The user can
        # switch model any time mid-conversation; on_settings_update hot-swaps it
        # on the live SDK client via set_model(), so context is preserved.
        settings = await SessionManager._send_model_settings()
        model = settings.get("model") or DEFAULT_MODEL
        cl.user_session.set("model", model)

        if mode == "validate":
            # Option B — validate an existing bundle. Land on Phase 3, no agent;
            # the user uploads a .zip and we run the check framework on it.
            cl.user_session.set("phase",                PHASE_VALIDATE)
            cl.user_session.set("client",               None)
            cl.user_session.set("awaiting_bundle_upload", True)
            cl.user_session.set("input_mode",           "attach_only")
            await SessionManager._send_validate_greeting(session_id)
        else:
            # Option A — create from scratch. Phase 1 agent session.
            cl.user_session.set("phase", PHASE_PLAN)
            client = ClaudeSDKClient(
                options=_build_sdk_options(run_dir, PHASE_PLAN, mcp_servers, model))
            await client.connect()
            cl.user_session.set("client", client)
            await SessionManager._send_create_greeting(session_id)

        cl.user_session.set("ready", True)

        # 5. Pre-write public artifacts and phase state.
        PublicArtifacts.write(run_dir, session_id)
        PhaseManager.write_state(run_dir)

    # ----- wizard entry -----------------------------------------------------

    @staticmethod
    async def _ask_entry_mode() -> str:
        """Ask the user, upfront, which path they want. Returns 'create'|'validate'."""
        # Labels are intentionally short, clean titles — the per-card
        # description line + icon are added by CSS (login.css, keyed off the
        # data-ac-entry tag chat.js sets), so the landing reads like a product
        # chooser rather than two emoji chat buttons.
        res = await cl.AskActionMessage(
            content=(
                "## AutoCodabench\n\n"
                "Choose how you'd like to start. You can switch any time "
                "with **New Chat**."
            ),
            actions=[
                cl.Action(name="entry_mode", payload={"mode": "create"},
                          label="Create from scratch"),
                cl.Action(name="entry_mode", payload={"mode": "validate"},
                          label="Validate a bundle"),
            ],
            timeout=900,
        ).send()
        return ((res or {}).get("payload", {}) or {}).get("mode") or "create"

    @staticmethod
    async def _send_model_settings(initial: str | None = None) -> dict:
        """Dock the model selector (cl.ChatSettings) at the input composer.

        Renders a Select mapping friendly labels → model ids. The user can
        change it any time mid-conversation; on_settings_update applies it live.
        Returns the resolved settings dict (Chainlit echoes back the initial
        values; it does NOT fire on_settings_update for this initial send).
        """
        initial = initial if initial in MODEL_LABELS else DEFAULT_MODEL
        settings = await cl.ChatSettings([
            Select(
                id="model",
                label="Model",
                items={m["label"]: m["id"] for m in MODEL_CHOICES},
                initial_value=initial,
            )
        ]).send()
        return settings or {"model": initial}

    @staticmethod
    async def on_settings_update(settings: dict) -> None:
        """Apply a mid-conversation model change from the docked selector.

        Hot-swaps the live SDK client's model via set_model() (context is
        preserved). In the validate path there's no client — we just record
        the choice so the LLM-judged pass uses it.
        """
        model = (settings or {}).get("model") or DEFAULT_MODEL
        if model not in MODEL_LABELS:
            model = DEFAULT_MODEL
        prev = cl.user_session.get("model")
        cl.user_session.set("model", model)
        if model == prev:
            return

        client = cl.user_session.get("client")
        if client is not None:
            try:
                await client.set_model(model)
            except Exception as e:
                log.warning("set_model(%s) failed: %s", model, e)
                await cl.Message(
                    author="autocodabench",
                    content=(
                        f"_Couldn't switch model live (`{type(e).__name__}`); "
                        f"it'll take effect at the next phase._"
                    ),
                ).send()
                return

        label = MODEL_LABELS.get(model, model)
        await cl.Message(
            author="autocodabench",
            content=f"_Model switched to **{label}** (`{model}`)._",
        ).send()

    @staticmethod
    def _meta_footer(pairs: list[tuple[str, str]]) -> str:
        """A clean metadata chip row (session / model / budget …).

        Rendered as inline-styled HTML — config enables unsafe_allow_html, and
        inline styles (unlike CSS classes) survive Chainlit's HTML sanitiser.
        Colours use the app's theme variables so it tracks light/dark.
        """
        chip = (
            '<span style="display:inline-flex;align-items:center;gap:6px;'
            'padding:3px 10px;border-radius:999px;background:hsl(var(--accent))">'
            '<b style="font-size:10px;font-weight:600;text-transform:uppercase;'
            'letter-spacing:.4px;opacity:.7">{k}</b>'
            '<code style="font-family:ui-monospace,SFMono-Regular,monospace;'
            'background:none;color:hsl(var(--foreground))">{v}</code></span>'
        )
        chips = "".join(chip.format(k=k, v=v) for k, v in pairs)
        return (
            '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:16px;'
            'padding-top:12px;border-top:1px solid hsl(var(--border));'
            'font-size:12px">' + chips + "</div>"
        )

    @staticmethod
    async def _send_create_greeting(session_id: str) -> None:
        """Option A greeting. Must contain the READY_PHRASE for the init-gate."""
        model = cl.user_session.get("model") or DEFAULT_MODEL
        await cl.Message(
            content=(
                "## Create from scratch\n\n"
                "Tell me a competition idea — a sentence is enough. I'll explore "
                "the design space with you, cite the relevant literature, and "
                "turn it into a plan. You can also drop a PDF or markdown design "
                "doc and I'll fill in the gaps.\n\n"
                "**Not sure where to start? Try one of these:**\n"
                "- *A fair chest-X-ray pneumonia challenge, scored by balanced "
                "accuracy with a penalty for performance gaps across age groups.*\n"
                "- *Forecast next-day household energy use from smart-meter "
                "history, ranked by MASE.*\n"
                "- *Low-resource Swahili→English machine translation, evaluated "
                "with chrF.*\n\n"
                "When the plan is ready, a **▶ Proceed to Phase 2** button "
                "appears. The bar above tracks your progress; switch models "
                "anytime from **⚙ settings** by the message box.\n\n"
                + SessionManager._meta_footer([
                    ("session", session_id),
                    ("model", model),
                    ("budget", f"${MAX_USD_PER_SESSION:.2f}"),
                ])
            ),
            author="autocodabench",
        ).send()

    @staticmethod
    async def _send_validate_greeting(session_id: str) -> None:
        """Option B greeting. Contains the attach-mode READY_PHRASE."""
        model = cl.user_session.get("model") or DEFAULT_MODEL
        await cl.Message(
            content=(
                "## Validate a bundle\n\n"
                "**Attach your bundle `.zip`** below and press send. I'll run the "
                "autocodabench checks against it — including a Docker execution "
                "of the baseline (this can take a few minutes and may pull the "
                "bundle's `docker_image`) — then write you a report.\n\n"
                "_Typing stays disabled until validation runs — just attach the "
                "file and send._\n\n"
                "**Don't have a bundle handy?** Download this small example "
                "competition, then attach it above to see validation in action:\n\n"
                '<a href="/public/examples/example-bundle-survival.zip" download '
                'target="_blank" style="display:inline-flex;align-items:center;'
                'gap:8px;padding:9px 16px;border-radius:10px;'
                'background:hsl(var(--primary));color:#fff;font-weight:600;'
                'font-size:13px;text-decoration:none">&#11015; Download example '
                'bundle <span style="opacity:.75;font-weight:400">survival '
                '&middot; 80&nbsp;KB</span></a>\n\n'
                + SessionManager._meta_footer([
                    ("session", session_id),
                    ("judge model", model),
                ])
            ),
            author="autocodabench",
        ).send()

    @staticmethod
    async def on_message(msg: cl.Message) -> None:
        """Handle one user message: augment with attachments, stream response."""
        phase = cl.user_session.get("phase") or "unknown"
        log.info("[session] on_message — phase=%r content=%.80r", phase, msg.content)
        if not cl.user_session.get("ready"):
            log.warning("[session] on_message called before session ready — dropping")
            await cl.Message(
                content="_Still initializing — give me a few more seconds._",
                author="autocodabench",
            ).send()
            return

        run_dir = Path(cl.user_session.get("run_dir"))

        # Option B — validate-existing-bundle: expect a .zip attachment.
        if (cl.user_session.get("entry_mode") == "validate"
                and cl.user_session.get("awaiting_bundle_upload")):
            await SessionManager._handle_bundle_upload(run_dir, msg)
            return

        client = cl.user_session.get("client")
        if client is None:
            # Phase 3 (validate) has no agent. Any further chat just guides.
            await cl.Message(
                author="autocodabench",
                content=(
                    "Validation runs without a chat agent. Start a **New Chat** "
                    "to validate another bundle or to create one from scratch."
                ),
            ).send()
            return

        cl.user_session.set("had_user_message", True)

        augmented_text = SessionManager._augment_user_message(run_dir, msg)
        Transcript.append(run_dir, role="user", text=augmented_text)

        response_msg = cl.Message(content="", author="autocodabench")
        await response_msg.send()
        log.info("[session] starting run_agent_turn for user message")
        await run_agent_turn(client, augmented_text, run_dir, response_msg)
        log.info("[session] run_agent_turn complete — writing state and checking bundle")

        PhaseManager.write_state(run_dir)
        await PhaseManager.maybe_offer_proceed_to_build(run_dir)
        await PhaseManager.maybe_offer_bundle_actions()
        log.info("[session] on_message DONE")
        asyncio.create_task(persist_to_hf(run_dir))

    @staticmethod
    async def _handle_bundle_upload(run_dir: Path, msg: cl.Message) -> None:
        """Option B: take the attached .zip and run Phase 3 validation on it."""
        zip_path = SessionManager._find_zip_attachment(run_dir, msg)
        if zip_path is None:
            await cl.Message(
                author="autocodabench",
                content="Please **attach a competition bundle `.zip`** and press send.",
            ).send()
            return

        cl.user_session.set("had_user_message", True)
        cl.user_session.set("awaiting_bundle_upload", False)
        cl.user_session.set("input_mode", "locked")
        PhaseManager.write_state(run_dir)
        Transcript.append(run_dir, role="user",
                          text=f"[ui] Uploaded `{zip_path.name}` for validation.")

        from phases.validate import Validate
        await Validate.run_validation(run_dir, zip_path, executed_in_phase2=False)

        cl.user_session.set("input_mode", "normal")
        PhaseManager.write_state(run_dir)
        asyncio.create_task(persist_to_hf(run_dir))

    @staticmethod
    def _find_zip_attachment(run_dir: Path, msg: cl.Message) -> Path | None:
        """Return the path to the first .zip attachment, mirrored into uploads/."""
        uploads_dir = run_dir / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        for el in (getattr(msg, "elements", None) or []):
            src = getattr(el, "path", None)
            name = getattr(el, "name", None) or (Path(src).name if src else "")
            if not src or not Path(src).exists():
                continue
            if not name.lower().endswith(".zip"):
                continue
            dest = uploads_dir / Path(name).name
            try:
                shutil.copy2(src, dest)
                return dest
            except Exception as e:
                log.warning("failed to mirror uploaded zip %s: %s", src, e)
                return Path(src)
        return None

    @staticmethod
    async def on_chat_end() -> None:
        """Disconnect the SDK client and do a final HF persist."""
        run_dir_str  = cl.user_session.get("run_dir")
        had_activity = cl.user_session.get("had_user_message", False)
        if run_dir_str and had_activity:
            try:
                await persist_to_hf(Path(run_dir_str))
            except Exception as e:
                log.warning("final HF persist failed: %s", e)
        client = cl.user_session.get("client")
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

    @staticmethod
    def _augment_user_message(run_dir: Path, msg: cl.Message) -> str:
        """Prepend extracted attachment text to the user's message.

        Also mirrors each file into <run_dir>/uploads/ so the agent can
        re-read it later via the Read tool.
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
            f"_The user attached {len(extracted_blocks)} document(s). "
            f"Use the extracted text below as reference for the plan._"
        )
        return f"{msg.content or ''}\n\n{head}\n\n" + "\n\n".join(extracted_blocks)
