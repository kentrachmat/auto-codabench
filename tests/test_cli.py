"""CLI surface tests — keyless paths only."""
import pytest

from autocodabench.cli.main import main

from conftest import DEMO_SLUG


def test_checks_list(capsys):
    assert main(["checks", "list"]) == 0
    out = capsys.readouterr().out
    assert "[deterministic]" in out
    assert "bundle-schema" in out


def test_demo_then_validate(tmp_path, capsys):
    out_dir = tmp_path / "demo-out"
    assert main(["demo", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "no LLM, no keys" in out
    assert "Bundle validation — ✅ PASS" in out

    assert main(["validate-bundle", str(out_dir / DEMO_SLUG)]) == 0
    assert "Bundle validation" in capsys.readouterr().out


def test_validate_json_output(demo_bundle, capsys):
    assert main(["validate-bundle", str(demo_bundle), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out


def test_validate_legacy_alias_still_works(demo_bundle, capsys):
    # `validate` is retained as a back-compatible alias for `validate-bundle`
    assert main(["validate", str(demo_bundle), "--json"]) == 0
    assert '"ok": true' in capsys.readouterr().out


def test_validate_exit_code_on_gate_failure(demo_bundle, capsys):
    (demo_bundle / "pages" / "terms.md").unlink()
    assert main(["validate-bundle", str(demo_bundle)]) == 1


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
