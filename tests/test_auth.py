"""Tests for auth resolution (filesystem + env detection only; no probing)."""
import json
from pathlib import Path

import pytest

from autocodabench import auth


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
    assert status.effective == "api_key"
    assert any("shadows" in w for w in status.warnings)


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
