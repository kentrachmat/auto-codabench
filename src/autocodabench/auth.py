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
over a stored subscription login. This module exists to make that
resolution visible before it costs anything — ``resolve_auth()`` reports
the active path and warns on shadowing, ``ensure_live_auth()`` fails fast
with guidance (or walks an interactive terminal through key entry) before
a live session starts, and ``load_dotenv()`` gives the CLI the same
``.env`` convention as the web UI. Static detection cannot prove a login
is valid; ``probe()`` spends one model turn to confirm end to end.
"""
from __future__ import annotations

import getpass
import json
import os
import shutil
import sys
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AuthStatus:
    effective: str                       # "api_key" | "subscription" | "none"
    api_key_set: bool = False
    subscription_login_detected: bool = False
    cli_path: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "effective": self.effective,
            "api_key_set": self.api_key_set,
            "subscription_login_detected": self.subscription_login_detected,
            "cli_path": self.cli_path,
            "warnings": self.warnings,
        }

    def describe(self) -> str:
        lines = []
        if self.effective == "api_key":
            lines.append("Auth: ANTHROPIC_API_KEY (usage billed to the API account).")
        elif self.effective == "subscription":
            lines.append("Auth: Claude subscription login (usage draws from your "
                         "plan's Agent SDK credit).")
        else:
            lines.append(
                "Auth: none detected. Either log in to Claude Code (`claude` then "
                "`/login`) to use your Pro/Max subscription, or export "
                "ANTHROPIC_API_KEY. Keyless commands (validate, demo --replay) "
                "still work.")
        cli = self.cli_path or ("(not found — the Agent SDK ships its own "
                                "runtime, so this is informational)")
        lines.append(f"  api key set:           {self.api_key_set}")
        lines.append(f"  subscription login:    {self.subscription_login_detected}")
        lines.append(f"  claude CLI on PATH:    {cli}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


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
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    api_key_set = api_key is not None
    subscription = _subscription_login_detected()
    cli = shutil.which("claude")

    warnings: list[str] = []
    if api_key_set and not api_key:
        warnings.append("ANTHROPIC_API_KEY is set but EMPTY — it still wins the "
                        "precedence slot and authenticates with an empty key. "
                        "Unset it (don't just blank it).")
    if api_key_set and subscription:
        warnings.append("ANTHROPIC_API_KEY shadows your subscription login: usage "
                        "will bill the API account, not your plan's Agent SDK "
                        "credit. Unset the key to use the subscription.")

    if api_key_set:
        effective = "api_key"
    elif subscription:
        effective = "subscription"
    else:
        effective = "none"

    return AuthStatus(
        effective=effective,
        api_key_set=api_key_set,
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


def _append_env_var(path: Path, key: str, value: str) -> Path:
    """Append (or replace) ``key`` in a .env file, chmod 600."""
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = [ln for ln in existing.splitlines() if not ln.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def ensure_live_auth(interactive: bool | None = None) -> AuthStatus:
    """Preflight for commands about to start a live Claude session.

    If a usable auth path exists, returns the :class:`AuthStatus` (echoing
    any configuration-hazard warnings to stderr). If none does: on an interactive
    terminal, walks the user through the two auth paths — finish a
    subscription login and re-check, or paste an API key (input hidden,
    optionally persisted to ``./.env``). Non-interactive contexts get an
    :class:`AuthRequiredError` carrying the same guidance instead of a
    hang or an opaque SDK failure mid-run.
    """
    status = resolve_auth()
    if status.effective != "none":
        for w in status.warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
        return status

    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        raise AuthRequiredError(_AUTH_GUIDANCE)

    print("No Claude auth detected — this command needs a live model session.\n",
          file=sys.stderr)
    while True:
        print("How do you want to authenticate?\n"
              "  [1] Claude subscription — run `claude` then `/login` in another\n"
              "      terminal (browser sign-in), come back and press 1 to re-check\n"
              "  [2] Anthropic API key — paste it now (input stays hidden)\n"
              "  [q] quit", file=sys.stderr)
        choice = input("choice [1/2/q]: ").strip().lower()
        if choice == "q":
            raise AuthRequiredError(_AUTH_GUIDANCE)
        if choice == "1":
            if _subscription_login_detected():
                print("Subscription login detected — continuing.", file=sys.stderr)
                return resolve_auth()
            print("\nNo login artifacts found yet (~/.claude). Finish `/login` in "
                  "the other terminal, then press 1 again.\n", file=sys.stderr)
            continue
        if choice == "2":
            key = getpass.getpass("ANTHROPIC_API_KEY (input hidden): ").strip()
            if not key:
                print("Empty key — nothing set.\n", file=sys.stderr)
                continue
            os.environ["ANTHROPIC_API_KEY"] = key
            if input("Save it to ./.env for future runs? [y/N]: ").strip().lower() == "y":
                saved = _append_env_var(Path.cwd() / ".env", "ANTHROPIC_API_KEY", key)
                print(f"Saved to {saved} (permissions 600). Keep that file out of "
                      "version control.", file=sys.stderr)
            else:
                print("Using the key for this run only. To persist it, export it "
                      "in your shell profile or add it to a .env file here.",
                      file=sys.stderr)
            return resolve_auth()


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
