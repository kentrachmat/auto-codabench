"""Authentication status and preflight for the Claude Agent SDK runtime.

Two authentication paths exist. Subscription login (Claude Code, Pro/Max
plan) is the recommended path for local use: usage draws from the plan's
Agent SDK allowance and no key material is handled at all. The second path
is ``ANTHROPIC_API_KEY`` — and the *required* one for any hosted
multi-user deployment, since Anthropic's terms do not permit routing
requests through one person's subscription credentials on behalf of
other users.

The SDK resolves credentials itself, with the opposite precedence to our
recommendation: an exported ``ANTHROPIC_API_KEY`` silently takes priority
over a stored subscription login. Rather than make users delete a key to
fall back to their subscription, autocodabench stores an explicit
**auth preference** (``auto`` | ``subscription`` | ``api_key``) and
*realizes* it for the process: choosing ``subscription`` hides any
``ANTHROPIC_API_KEY`` from the SDK for the run, so the subscription login
is actually used — no manual unsetting. The preference lives at
``~/.config/autocodabench/auth.json`` (override per-invocation with the
``AUTOCODABENCH_AUTH`` environment variable) and is set from the CLI
(``autocodabench auth use <mode>`` or the picker in ``auth status``).

This module makes the resolution visible before it costs anything —
``resolve_auth()`` reports the active path (honoring the preference),
``apply_auth_preference()`` realizes it, ``ensure_live_auth()`` prints an
``INFO`` banner and fails fast with guidance (or walks an interactive
terminal through key entry) before a live session starts, and
``load_dotenv()`` gives the CLI the same ``.env`` convention as the web
UI. Static detection cannot prove a login is valid; ``probe()`` spends one
model turn to confirm end to end.
"""
from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


AUTH_MODES = ("auto", "subscription", "api_key")


def mask_secret(value: str | None, *, keep_start: int = 0, keep_end: int = 4) -> str:
    """Render a secret for display: a short, non-recoverable preview that
    confirms *which* value is configured without exposing it.

    ``keep_start`` / ``keep_end`` characters are shown verbatim (the start is
    useful for identifying a key by its scheme prefix, e.g. ``sk-ant-``; the
    end lets a user confirm they pasted the right value); the middle is
    elided and the total length is reported. A value too short to mask both
    ends is shown only as bullets, so nothing recoverable leaks. ``None`` and
    the empty string are reported as distinct states, not masked."""
    if value is None:
        return "(not set)"
    if value == "":
        return "(set but empty)"
    n = len(value)
    if n <= keep_start + keep_end:
        return f"{'•' * n} ({n} chars)"
    return f"{value[:keep_start]}…{value[-keep_end:]} ({n} chars)"


@dataclass
class AuthStatus:
    effective: str                       # "api_key" | "subscription" | "none"
    preference: str = "auto"             # "auto" | "subscription" | "api_key"
    api_key_set: bool = False
    subscription_login_detected: bool = False
    cli_path: str | None = None
    warnings: list[str] = field(default_factory=list)
    api_key_preview: str | None = None   # masked, for display only

    def to_dict(self) -> dict[str, Any]:
        return {
            "effective": self.effective,
            "preference": self.preference,
            "api_key_set": self.api_key_set,
            "api_key_preview": self.api_key_preview,
            "subscription_login_detected": self.subscription_login_detected,
            "cli_path": self.cli_path,
            "warnings": self.warnings,
        }

    def info_line(self) -> str:
        """One-line ``INFO:`` banner printed before any live model session."""
        pref = "" if self.preference == "auto" else f"  ·  preference: {self.preference}"
        if self.effective == "api_key":
            return ("INFO: Claude auth = ANTHROPIC_API_KEY "
                    f"(billed to your API account){pref}")
        if self.effective == "subscription":
            return ("INFO: Claude auth = subscription login "
                    f"(your plan's Agent SDK credit){pref}")
        return f"INFO: Claude auth = none set — live model commands will fail{pref}"

    def describe(self) -> str:
        lines = []
        if self.effective == "api_key":
            lines.append("Auth: ANTHROPIC_API_KEY (usage billed to the API account).")
        elif self.effective == "subscription":
            lines.append("Auth: Claude subscription login (usage draws from your "
                         "plan's Agent SDK credit).")
        else:
            lines.append(
                "Auth: none detected. Run `autocodabench auth use subscription` to "
                "sign in with your Pro/Max plan (it opens Claude Code's sign-in for "
                "you), or `autocodabench auth use api_key` to paste an API key. "
                "Keyless commands (validate-bundle, demo) still work.")
        cli = self.cli_path or ("(not found — the Agent SDK ships its own "
                                "runtime, so this is informational)")
        pref_note = {
            "auto": "auto (use a key if one is set, otherwise the subscription)",
            "subscription": "subscription (an API key, if any, is hidden from the SDK)",
            "api_key": "api_key (use ANTHROPIC_API_KEY)",
        }[self.preference]
        lines.append(f"  preference:            {pref_note}")
        key_display = self.api_key_preview if self.api_key_set else "(not set)"
        lines.append(f"  ANTHROPIC_API_KEY:     {key_display}")
        lines.append(f"  subscription login:    {self.subscription_login_detected} "
                     "(on-disk artifacts; verified only by a probe)")
        lines.append(f"  claude CLI on PATH:    {cli}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        lines.append("  change with: autocodabench auth use "
                     "<auto|subscription|api_key>")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persisted auth preference
# ---------------------------------------------------------------------------

def _pref_path() -> Path:
    """User-global preference file (XDG-aware), not per-project."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autocodabench" / "auth.json"


def get_auth_preference() -> str:
    """The persisted preference: ``auto`` | ``subscription`` | ``api_key``.

    An ``AUTOCODABENCH_AUTH`` environment variable overrides the stored
    file (useful for CI and one-off runs). Unknown values fall back to
    ``auto``.
    """
    env_pref = os.environ.get("AUTOCODABENCH_AUTH", "").strip().lower()
    if env_pref in AUTH_MODES:
        return env_pref
    path = _pref_path()
    if path.is_file():
        try:
            mode = json.loads(path.read_text(encoding="utf-8")).get("mode")
            if mode in AUTH_MODES:
                return mode
        except (json.JSONDecodeError, OSError):
            pass
    return "auto"


def set_auth_preference(mode: str) -> Path:
    """Persist the auth preference; returns the file path written."""
    if mode not in AUTH_MODES:
        raise ValueError(f"mode must be one of {AUTH_MODES}, got {mode!r}")
    path = _pref_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": mode}, indent=2) + "\n", encoding="utf-8")
    return path


def apply_auth_preference() -> AuthStatus:
    """Realize the preference in ``os.environ`` for this process.

    The only mutation is for ``subscription`` mode with a key present: the
    ``ANTHROPIC_API_KEY`` is removed so the SDK falls back to the
    subscription login. (``auto`` and ``api_key`` leave the environment
    untouched — the SDK already prefers a key.) Idempotent; returns the
    :class:`AuthStatus` being realized, whose ``info_line()`` is the banner.
    """
    status = resolve_auth()
    if status.effective == "subscription" and status.api_key_set:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    return status


def _subscription_login_detected() -> bool:
    """Best-effort: look for the artifacts `claude /login` leaves behind.

    On Linux the OAuth credential lives at ``~/.claude/.credentials.json``;
    on macOS it lives in the Keychain, but ``~/.claude.json`` records the
    ``oauthAccount`` after a successful login. Neither proves the token is
    still valid — ``probe()`` does that.
    """
    home = Path.home()
    if (home / ".claude" / ".credentials.json").is_file():
        return True
    cfg = home / ".claude.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            if data.get("oauthAccount"):
                return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


def resolve_auth() -> AuthStatus:
    """Report the auth path that *will* be used, honoring the preference.

    The preference decides between an API key and a subscription login when
    both are available, so the user never has to unset anything. A
    preference that cannot be satisfied (e.g. ``subscription`` with no
    login) falls back to whatever is available, with a warning.
    """
    pref = get_auth_preference()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    api_key_set = api_key is not None
    subscription = _subscription_login_detected()
    cli = shutil.which("claude")

    warnings: list[str] = []
    if api_key_set and not api_key:
        warnings.append("ANTHROPIC_API_KEY is set but EMPTY — it authenticates with "
                        "an empty key. Unset it, or run `autocodabench auth use "
                        "subscription` to ignore it.")

    if pref == "subscription":
        if subscription:
            effective = "subscription"
        elif api_key_set:
            effective = "api_key"
            warnings.append("preference is 'subscription' but no subscription login "
                            "was found — falling back to the API key. Run `claude` "
                            "then `/login`, or `autocodabench auth use api_key`.")
        else:
            effective = "none"
    elif pref == "api_key":
        if api_key_set:
            effective = "api_key"
        elif subscription:
            effective = "subscription"
            warnings.append("preference is 'api_key' but none is set — falling back "
                            "to the subscription login. Set ANTHROPIC_API_KEY "
                            "(`autocodabench auth use api_key` can paste one), or "
                            "`autocodabench auth use subscription`.")
        else:
            effective = "none"
    else:  # auto
        if api_key_set:
            effective = "api_key"
            if subscription:
                warnings.append("both an API key and a subscription login are "
                                "available; under the 'auto' preference the API "
                                "key takes precedence. To use the subscription "
                                "instead, run `autocodabench auth use "
                                "subscription` — the key need not be unset.")
        elif subscription:
            effective = "subscription"
        else:
            effective = "none"

    return AuthStatus(
        effective=effective,
        preference=pref,
        api_key_set=api_key_set,
        # Keep the recognizable `sk-ant-…` scheme prefix; mask the rest.
        api_key_preview=mask_secret(api_key, keep_start=10) if api_key_set else None,
        subscription_login_detected=subscription,
        cli_path=cli,
        warnings=warnings,
    )


class AuthRequiredError(RuntimeError):
    """A live command found no usable Claude auth path and the terminal is
    non-interactive, so the user could not be walked through setting one up."""


_AUTH_GUIDANCE = """\
No Claude auth detected. To run live agent commands, either:

  subscription (recommended for local use — usage draws from your plan's
  Agent SDK credit, no keys to manage):
      install Claude Code, run `claude` then `/login` (browser sign-in)

  API key (the required path for hosted / multi-user deployments):
      export ANTHROPIC_API_KEY=<your key>
      — or put `ANTHROPIC_API_KEY=<your key>` in a .env file in your
        working directory; the CLI loads .env automatically.

Keyless commands (`validate` without --judged, `demo`, `checks list`)
work without any auth."""


def load_dotenv(path: str | Path | None = None,
                environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Minimal stdlib ``.env`` loader: ``KEY=VALUE`` lines, with ``export ``
    prefixes and surrounding quotes tolerated; ``#`` comments and blank
    lines skipped. Never overrides a variable that is already set — the
    real environment always wins. Returns the names it set.

    Defaults to ``<cwd>/.env``, matching where the web UI and
    ``.env.example`` expect secrets to live.
    """
    env = os.environ if environ is None else environ
    env_path = Path(path) if path is not None else Path.cwd() / ".env"
    if not env_path.is_file():
        return []
    loaded: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or key in env:
            continue
        env[key] = value.strip().strip("'\"")
        loaded.append(key)
    return loaded


# ---------------------------------------------------------------------------
# Codabench publishing credentials (distinct from the Claude runtime auth
# above): the upload path reads these from the environment / .env. They are
# never required to author or validate a bundle, only to publish one, so they
# live outside AuthStatus — but a user still needs a way to confirm what is
# configured without printing the secrets.
# ---------------------------------------------------------------------------

def codabench_credentials_status() -> dict[str, str]:
    """Masked view of the Codabench upload credentials, keyed by env var.

    The username is not secret and is shown in full; the password and DRF
    token are masked. Each value reports one of three states — a masked
    preview, ``(not set)``, or ``(set but empty)`` — so a user can tell a
    missing credential from a present one without exposing it. Mirrors the
    resolution order in :mod:`autocodabench.upload.service`."""
    return {
        "CODABENCH_USERNAME": os.environ.get("CODABENCH_USERNAME") or "(not set)",
        "CODABENCH_PASSWORD": mask_secret(os.environ.get("CODABENCH_PASSWORD"),
                                          keep_end=2),
        "CODABENCH_TOKEN": mask_secret(os.environ.get("CODABENCH_TOKEN")),
    }


def describe_codabench_credentials() -> str:
    """Human-readable block for the masked Codabench credentials."""
    status = codabench_credentials_status()
    has_token = os.environ.get("CODABENCH_TOKEN")
    has_userpass = (os.environ.get("CODABENCH_USERNAME")
                    and os.environ.get("CODABENCH_PASSWORD"))
    note = ("codabench.org account login — used only to publish a bundle; "
            "never Claude or agent-SDK auth")
    if has_token or has_userpass:
        head = f"Codabench login ({note}):"
    else:
        head = (f"Codabench login ({note}) — none configured. Set "
                "CODABENCH_TOKEN, or CODABENCH_USERNAME + CODABENCH_PASSWORD "
                "(env or ./.env):")
    lines = [head]
    lines.append(f"  CODABENCH_USERNAME:    {status['CODABENCH_USERNAME']}")
    lines.append(f"  CODABENCH_PASSWORD:    {status['CODABENCH_PASSWORD']}")
    lines.append(f"  CODABENCH_TOKEN:       {status['CODABENCH_TOKEN']}")
    return "\n".join(lines)


def _append_env_var(path: Path, key: str, value: str) -> Path:
    """Append (or replace) ``key`` in a .env file, chmod 600."""
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = [ln for ln in existing.splitlines() if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _prompt_and_store_api_key() -> bool:
    """Prompt for an API key (hidden), set it for this process, and offer to
    persist it to ``./.env``. Returns True if a key was set. Lets the user
    add a key from the CLI without ever opening ``.env`` by hand."""
    key = getpass.getpass(
        "ANTHROPIC_API_KEY (input hidden, leave blank to skip): ").strip()
    if not key:
        print("No key entered.", file=sys.stderr)
        return False
    os.environ["ANTHROPIC_API_KEY"] = key
    if input("Save it to ./.env for future runs? [y/N]: ").strip().lower() == "y":
        saved = _append_env_var(Path.cwd() / ".env", "ANTHROPIC_API_KEY", key)
        print(f"Saved to {saved} (permissions 600). Keep it out of version control.",
              file=sys.stderr)
    else:
        print("Using the key for this run only.", file=sys.stderr)
    return True


def launch_claude_login() -> bool:
    """Offer to run Claude Code's own sign-in (``claude auth login --claudeai``)
    so the user can authenticate the subscription without leaving autocodabench.

    Always asks for explicit consent first — we never open a browser or shell
    out to the CLI silently. Declining is a first-class outcome: the user can
    quit and run ``claude auth login`` themselves. On consent, the subprocess
    inherits the terminal so its browser / device-code prompt is shown
    directly. Returns True only if a subscription login is detected afterward.

    We delegate to the official CLI rather than reimplement the OAuth dance:
    Claude Code owns the token format and storage location, and this keeps
    autocodabench out of the credential-handling path entirely."""
    cli = shutil.which("claude")
    if cli is None:
        print("The `claude` CLI is not on PATH. Install Claude Code from "
              "https://claude.com/claude-code, then choose subscription again.",
              file=sys.stderr)
        return False
    print("\nWe did not find your Claude credentials on this machine.",
          file=sys.stderr)
    try:
        ans = input("Sign in with Claude now? This opens Claude Code's sign-in "
                    "(browser / device code). [Y]es, or [n]/[q] to skip and run "
                    "`claude auth login` yourself: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    if ans not in ("", "y", "yes"):
        print("Skipped sign-in. Run `claude auth login` when ready, then re-run "
              "the command.", file=sys.stderr)
        return False
    print("\nOpening Claude sign-in (a browser window or device-code prompt "
          "will appear)…\n", file=sys.stderr)
    try:
        subprocess.run([cli, "auth", "login", "--claudeai"], check=False)
    except (OSError, KeyboardInterrupt) as exc:
        print(f"\nsign-in did not complete: {exc}", file=sys.stderr)
        return False
    if _subscription_login_detected():
        print("\nSubscription login detected — you are signed in.",
              file=sys.stderr)
        return True
    print("\nNo subscription login detected yet. If you completed sign-in, "
          "re-run the command; otherwise try `claude auth login` directly.",
          file=sys.stderr)
    return False


def choose_auth_interactively(current: AuthStatus) -> AuthStatus | None:
    """Picker shown by ``autocodabench auth status`` on a terminal.

    Lets the user set the preference, sign in to the subscription in place
    (launching ``claude auth login``), or paste a key — without editing files
    or unsetting variables. Returns the new :class:`AuthStatus` if the
    preference changed, else None.
    """
    print("\nChoose which auth autocodabench should use:", file=sys.stderr)
    print("  [s] subscription login    [k] API key    [a] auto    "
          "[Enter] keep current", file=sys.stderr)
    choice = input("choice [s/k/a, Enter=keep]: ").strip().lower()
    if choice in ("s", "subscription"):
        set_auth_preference("subscription")
        if not current.subscription_login_detected:
            launch_claude_login()  # asks for consent before doing anything
        return resolve_auth()
    if choice in ("a", "auto"):
        set_auth_preference("auto")
        return resolve_auth()
    if choice in ("k", "key", "api_key", "api"):
        set_auth_preference("api_key")
        if not current.api_key_set:
            _prompt_and_store_api_key()
        return resolve_auth()
    return None  # Enter / unrecognized → keep current


def ensure_live_auth(interactive: bool | None = None) -> AuthStatus:
    """Preflight for commands about to start a live Claude session.

    Realizes the auth preference (hiding a key when ``subscription`` is
    chosen), prints the ``INFO`` banner, and returns the
    :class:`AuthStatus`. If no auth resolves: on an interactive terminal,
    walks the user through the two paths — finish a subscription login and
    re-check, or paste an API key (input hidden, optionally persisted to
    ``./.env``). Non-interactive contexts get an :class:`AuthRequiredError`
    carrying guidance instead of a hang or an opaque SDK failure mid-run.
    """
    status = apply_auth_preference()
    if status.effective != "none":
        for w in status.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        print(status.info_line(), file=sys.stderr)
        return status

    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        raise AuthRequiredError(_AUTH_GUIDANCE)

    print("No Claude auth detected — this command needs a live model session.\n",
          file=sys.stderr)
    while True:
        print("How do you want to authenticate?\n"
              "  [1] Claude subscription — sign in now (opens Claude Code's\n"
              "      browser sign-in here; no need for a second terminal)\n"
              "  [2] Anthropic API key — paste it now (input stays hidden)\n"
              "  [q] quit", file=sys.stderr)
        choice = input("choice [1/2/q]: ").strip().lower()
        if choice == "q":
            raise AuthRequiredError(_AUTH_GUIDANCE)
        if choice == "1":
            # Launch the sign-in in place unless the token is already present.
            if not _subscription_login_detected():
                launch_claude_login()
            if _subscription_login_detected():
                set_auth_preference("subscription")
                status = apply_auth_preference()
                print("Subscription login detected — saved preference "
                      "'subscription'.", file=sys.stderr)
                print(status.info_line(), file=sys.stderr)
                return status
            print("\nStill no login detected. Try again, or choose [2] to use "
                  "an API key.\n", file=sys.stderr)
            continue
        if choice == "2":
            if not _prompt_and_store_api_key():
                continue
            set_auth_preference("api_key")
            status = apply_auth_preference()
            print(status.info_line(), file=sys.stderr)
            return status


async def probe(model: str | None = None) -> dict[str, Any]:
    """Spend one tiny turn to confirm auth works end to end."""
    from .backends.base import AgentTask
    from .backends.claude import ClaudeAgentBackend

    backend = ClaudeAgentBackend(model=model) if model else ClaudeAgentBackend()
    result = await backend.run(AgentTask(
        prompt="Reply with exactly: OK", allowed_tools=[]))
    return {
        "ok": result.ok and "OK" in (result.final_text or ""),
        "status": result.status,
        "cost_usd": result.total_cost_usd,
        "error": result.error,
    }
