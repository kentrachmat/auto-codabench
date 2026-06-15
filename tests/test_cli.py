"""CLI surface tests — keyless paths only."""
import pytest

from autocodabench.cli.main import main, _make_progress_renderer

from conftest import DEMO_SLUG


# --- create progress renderer (default vs --debug) -------------------------

_EVENTS = [
    {"kind": "phase", "index": 2, "total": 3, "title": "Building the bundle",
     "detail": "writing files; linting; zipping"},
    {"kind": "tool_use", "name": "mcp__autocodabench__autocodabench_init_bundle",
     "input": {"slug": "create"}},
    {"kind": "tool_use", "name": "mcp__autocodabench__autocodabench_log_event",
     "input": {"kind": "progress", "message": "Wrote the scoring program."}},
    {"kind": "tool_result", "is_error": True,
     "preview": "TypeError: unexpected keyword argument 'multi_class'"},
    {"kind": "tool_result", "is_error": True,
     "preview": "Cancelled: parallel tool call Bash(...) errored"},
    {"kind": "tool_use", "name": "mcp__autocodabench__autocodabench_log_event",
     "input": {"kind": "deviation", "message": "Removed multi_class; acc 0.92."}},
    {"kind": "text", "text": "Internal reasoning the user need not see."},
    {"kind": "phase_done", "phase": "build", "ok": True, "num_turns": 23},
]


def _render(events, *, debug):
    import io
    import contextlib
    buf = io.StringIO()
    r = _make_progress_renderer(debug=debug)
    with contextlib.redirect_stdout(buf):
        for e in events:
            r(e)
    return buf.getvalue()


def test_default_renderer_is_user_oriented():
    out = _render(_EVENTS, debug=False)
    # User-facing milestone + deviation messages are shown…
    assert "Wrote the scoring program." in out
    assert "Removed multi_class; acc 0.92." in out
    # …but raw tool calls, raw errors, cancellations, and internal reasoning
    # are suppressed.
    assert "init_bundle" not in out
    assert "TypeError" not in out
    assert "Cancelled" not in out
    assert "Internal reasoning" not in out


def test_debug_renderer_shows_full_trace_and_softens_cancellations():
    out = _render(_EVENTS, debug=True)
    assert "init_bundle(create)" in out
    assert "TypeError" in out                      # genuine error shown
    assert "Internal reasoning" in out             # narration shown
    assert "Cancelled" not in out                  # cascade is reworded…
    assert "retried" in out                        # …as a benign retry


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


# ---------------------------------------------------------------------------
# create-bundle argument guards (keyless: rejected before any live-auth probe
# or backend call, so these run without credentials)
# ---------------------------------------------------------------------------

def test_create_bundle_requires_a_plan_source(capsys):
    assert main(["create-bundle", "--yes"]) == 2
    assert "plan file" in capsys.readouterr().err


def test_create_bundle_rejects_both_sources(tmp_path, capsys):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan", encoding="utf-8")
    code = main(["create-bundle", str(plan), "--run-dir", str(tmp_path), "--yes"])
    assert code == 2
    assert "not both" in capsys.readouterr().err


def test_create_bundle_missing_run_dir(tmp_path, capsys):
    missing = tmp_path / "nope"
    assert main(["create-bundle", "--run-dir", str(missing), "--yes"]) == 2
    assert "run dir not found" in capsys.readouterr().err


def test_create_bundle_run_dir_without_plan(tmp_path, capsys):
    (tmp_path / "specs").mkdir()
    assert main(["create-bundle", "--run-dir", str(tmp_path), "--yes"]) == 2
    assert "implementation_plan.md" in capsys.readouterr().err


def test_create_bundle_missing_plan_file(tmp_path, capsys):
    assert main(["create-bundle", str(tmp_path / "absent.md"), "--yes"]) == 2
    assert "plan file not found" in capsys.readouterr().err
