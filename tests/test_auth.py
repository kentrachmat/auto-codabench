"""Tests for auth resolution (filesystem + env detection only; no probing)."""
import json
import os
from pathlib import Path

import pytest

from autocodabench import auth


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Isolate the auth-preference file (env override + XDG dir) per test.
    monkeypatch.delenv("AUTOCODABENCH_AUTH", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    return home


def test_none_detected(fake_home):
    status = auth.resolve_auth()
    assert status.effective == "none"
    assert not status.warnings


def test_api_key_wins(fake_home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    status = auth.resolve_auth()
    assert status.effective == "api_key"


def test_subscription_via_credentials_file(fake_home):
    creds = fake_home / ".claude" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text("{}")
    status = auth.resolve_auth()
    assert status.effective == "subscription"


def test_subscription_via_oauth_account(fake_home):
    (fake_home / ".claude.json").write_text(json.dumps({"oauthAccount": {"id": "x"}}))
    status = auth.resolve_auth()
    assert status.effective == "subscription"


def test_api_key_shadows_subscription_with_warning(fake_home, monkeypatch):
    creds = fake_home / ".claude" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text("{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    status = auth.resolve_auth()
    assert status.effective == "api_key"          # auto: the key takes precedence
    # The guidance points at `auth use subscription`, not "delete your key".
    assert any("takes precedence" in w and "auth use subscription" in w
               for w in status.warnings)


def test_empty_api_key_warns(fake_home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    status = auth.resolve_auth()
    assert status.effective == "api_key"
    assert any("EMPTY" in w for w in status.warnings)


def test_describe_renders(fake_home):
    assert "Auth:" in auth.resolve_auth().describe()


# -- load_dotenv -------------------------------------------------------------

def test_load_dotenv_parses_and_never_overrides(tmp_path):
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "export ANTHROPIC_API_KEY='sk-from-dotenv'\n"
        "ALREADY=overwritten\n"
        'QUOTED="v1"\n'
        "not a kv line\n")
    env = {"ALREADY": "original"}
    loaded = auth.load_dotenv(tmp_path / ".env", environ=env)
    assert loaded == ["ANTHROPIC_API_KEY", "QUOTED"]
    assert env["ANTHROPIC_API_KEY"] == "sk-from-dotenv"
    assert env["QUOTED"] == "v1"
    assert env["ALREADY"] == "original"


def test_load_dotenv_missing_file_is_noop(tmp_path):
    env: dict[str, str] = {}
    assert auth.load_dotenv(tmp_path / ".env", environ=env) == []
    assert env == {}


# -- ensure_live_auth --------------------------------------------------------

def test_ensure_live_auth_noninteractive_raises_with_guidance(fake_home):
    with pytest.raises(auth.AuthRequiredError, match="ANTHROPIC_API_KEY"):
        auth.ensure_live_auth(interactive=False)


def test_ensure_live_auth_passes_through_existing_key(fake_home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert auth.ensure_live_auth(interactive=False).effective == "api_key"


def test_ensure_live_auth_interactive_api_key_entry(fake_home, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    answers = iter(["2", "y"])  # choose API key, then save to .env
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr(auth.getpass, "getpass", lambda *a: "sk-ant-pasted")
    try:
        status = auth.ensure_live_auth(interactive=True)
        assert status.effective == "api_key"
        import os
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-pasted"
        dotenv = tmp_path / ".env"
        assert "ANTHROPIC_API_KEY=sk-ant-pasted" in dotenv.read_text()
        assert (dotenv.stat().st_mode & 0o777) == 0o600
    finally:
        import os
        os.environ.pop("ANTHROPIC_API_KEY", None)


def test_ensure_live_auth_interactive_quit_raises(fake_home, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "q")
    with pytest.raises(auth.AuthRequiredError):
        auth.ensure_live_auth(interactive=True)


# -- auth preference ---------------------------------------------------------

def test_preference_defaults_to_auto_and_roundtrips(fake_home):
    assert auth.get_auth_preference() == "auto"
    auth.set_auth_preference("subscription")
    assert auth.get_auth_preference() == "subscription"


def test_preference_env_override_beats_file(fake_home, monkeypatch):
    auth.set_auth_preference("api_key")
    monkeypatch.setenv("AUTOCODABENCH_AUTH", "subscription")
    assert auth.get_auth_preference() == "subscription"


def test_set_preference_rejects_unknown_mode(fake_home):
    with pytest.raises(ValueError):
        auth.set_auth_preference("bogus")


def test_subscription_preference_picks_subscription_over_key(fake_home, monkeypatch):
    creds = fake_home / ".claude" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text("{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    auth.set_auth_preference("subscription")
    assert auth.resolve_auth().effective == "subscription"


def test_apply_subscription_preference_hides_key_for_process(fake_home, monkeypatch):
    creds = fake_home / ".claude" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text("{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    auth.set_auth_preference("subscription")
    status = auth.apply_auth_preference()
    assert status.effective == "subscription"
    # The key is removed from the process env so the SDK uses the subscription.
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_api_key_preference_without_key_falls_back_with_warning(fake_home):
    creds = fake_home / ".claude" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text("{}")
    auth.set_auth_preference("api_key")
    status = auth.resolve_auth()
    assert status.effective == "subscription"
    assert any("preference is 'api_key'" in w for w in status.warnings)


def test_info_line_reports_effective_auth(fake_home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    line = auth.resolve_auth().info_line()
    assert line.startswith("INFO:") and "ANTHROPIC_API_KEY" in line


# -- secret masking ----------------------------------------------------------

def test_mask_secret_states_and_partial_reveal():
    # Distinct states for absent vs. present-but-empty.
    assert auth.mask_secret(None) == "(not set)"
    assert auth.mask_secret("") == "(set but empty)"
    # A long value reveals only the requested ends; the middle never appears.
    masked = auth.mask_secret("sk-ant-api03-SECRETMIDDLE-tail", keep_start=10)
    assert masked.startswith("sk-ant-api") and masked.endswith("tail (30 chars)")
    assert "SECRETMIDDLE" not in masked
    # A value too short to mask both ends leaks nothing recoverable.
    short = auth.mask_secret("abcd", keep_start=0, keep_end=4)
    assert "abcd" not in short and "4 chars" in short


def test_resolve_auth_exposes_masked_key_only(fake_home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-abcdefghijklmnop")
    status = auth.resolve_auth()
    assert status.api_key_preview is not None
    # The full secret is never carried on the status object or its dict.
    assert "abcdefghijklmnop" not in status.api_key_preview
    assert "abcdefghijklmnop" not in str(status.to_dict())
    assert status.api_key_preview in status.describe()


def test_codabench_credentials_status_masks_password_and_token(fake_home, monkeypatch):
    monkeypatch.setenv("CODABENCH_USERNAME", "alice")
    monkeypatch.setenv("CODABENCH_PASSWORD", "hunter2secret")
    monkeypatch.setenv("CODABENCH_TOKEN", "tok_abcdef123456")
    s = auth.codabench_credentials_status()
    assert s["CODABENCH_USERNAME"] == "alice"          # username is not secret
    assert "hunter2secret" not in s["CODABENCH_PASSWORD"]
    assert "abcdef123456" not in s["CODABENCH_TOKEN"]
    block = auth.describe_codabench_credentials()
    assert "alice" in block and "hunter2secret" not in block


def test_codabench_credentials_status_reports_unset(fake_home, monkeypatch):
    for var in ("CODABENCH_USERNAME", "CODABENCH_PASSWORD", "CODABENCH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    s = auth.codabench_credentials_status()
    assert s["CODABENCH_USERNAME"] == "(not set)"
    assert s["CODABENCH_TOKEN"] == "(not set)"
    assert "none configured" in auth.describe_codabench_credentials()


# -- launch_claude_login (subprocess mocked; never opens a real browser) ------

def test_launch_claude_login_missing_cli_returns_false(fake_home, monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: None)
    called = {"run": False}
    monkeypatch.setattr(auth.subprocess, "run",
                        lambda *a, **k: called.__setitem__("run", True))
    assert auth.launch_claude_login() is False
    assert called["run"] is False        # never shells out without the CLI


def test_launch_claude_login_asks_consent_before_shelling_out(fake_home, monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr("builtins.input", lambda *a: "n")   # user declines
    called = {"run": False}
    monkeypatch.setattr(auth.subprocess, "run",
                        lambda *a, **k: called.__setitem__("run", True))
    assert auth.launch_claude_login() is False
    assert called["run"] is False        # consent is required before sign-in


def test_launch_claude_login_invokes_claude_auth_login_on_consent(fake_home, monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr("builtins.input", lambda *a: "y")   # user consents
    seen = {}
    monkeypatch.setattr(auth.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd))
    # Simulate the token appearing only after sign-in completes.
    monkeypatch.setattr(auth, "_subscription_login_detected", lambda: True)
    assert auth.launch_claude_login() is True
    assert seen["cmd"] == ["/usr/bin/claude", "auth", "login", "--claudeai"]


def test_launch_claude_login_reports_failure_when_still_absent(fake_home, monkeypatch):
    monkeypatch.setattr(auth.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    monkeypatch.setattr(auth.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(auth, "_subscription_login_detected", lambda: False)
    assert auth.launch_claude_login() is False
