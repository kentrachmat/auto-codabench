"""Keyless tests for the standalone plan/bundle pipeline phases.

These exercise the argument contracts of the phase-split API without a
backend or any credentials — the guards fire before any agent runs.
"""
import asyncio

import pytest

from autocodabench.agent.pipeline import bundle_async


def test_bundle_async_requires_exactly_one_source():
    # neither run_dir nor plan_path
    with pytest.raises(ValueError, match="exactly one"):
        asyncio.run(bundle_async())


def test_bundle_async_rejects_both_sources(tmp_path):
    plan = tmp_path / "implementation_plan.md"
    plan.write_text("# plan", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        asyncio.run(bundle_async(run_dir=tmp_path, plan_path=plan))


def test_plan_prompt_save_call_matches_tool_and_pipeline():
    """Regression for #32: 'plan phase finished without saving
    specs/implementation_plan.md'.

    The plan skill told the agent to save with
    `autocodabench_snapshot_spec(name="implementation_plan", ...)` — but the tool
    parameter is `filename` (no `name` alias) and `snapshot_spec` writes the
    name verbatim, so the file landed at `specs/implementation_plan` (no `.md`)
    or the call errored. The pipeline then looked for
    `specs/implementation_plan.md` and reported the plan as never saved.

    The system prompt the plan agent actually sees must therefore (a) use the
    real `filename` parameter and (b) include the `.md` the pipeline checks for.
    """
    import inspect

    from autocodabench.run_log import snapshot_spec
    from autocodabench.agent.prompts import plan_system_prompt

    # The save tool's first parameter is `filename` (no `name`).
    assert list(inspect.signature(snapshot_spec).parameters)[0] == "filename"

    prompt = plan_system_prompt()
    # Must not instruct a kwarg/value that yields the wrong path.
    assert 'name="implementation_plan"' not in prompt, (
        "plan prompt still tells the agent to save with `name=` / without `.md`"
    )
    # Must instruct the exact save the pipeline can find.
    assert 'filename="implementation_plan.md"' in prompt


def test_resolve_plan_file_promotes_extensionless(tmp_path):
    """Defense-in-depth: a plan saved as `implementation_plan` (no `.md`) is
    promoted to the canonical `implementation_plan.md` that Phase 2 reads."""
    from autocodabench.agent.pipeline import _resolve_plan_file

    specs = tmp_path / "specs"
    specs.mkdir()
    assert _resolve_plan_file(tmp_path) is None  # nothing saved

    (specs / "implementation_plan").write_text("# plan", encoding="utf-8")
    resolved = _resolve_plan_file(tmp_path)
    assert resolved == specs / "implementation_plan.md"
    assert resolved.is_file()
    assert not (specs / "implementation_plan").exists()  # renamed, not copied


def test_resolve_plan_file_prefers_canonical(tmp_path):
    from autocodabench.agent.pipeline import _resolve_plan_file

    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "implementation_plan.md").write_text("# canonical", encoding="utf-8")
    (specs / "implementation_plan").write_text("# stale", encoding="utf-8")
    resolved = _resolve_plan_file(tmp_path)
    assert resolved == specs / "implementation_plan.md"
    assert resolved.read_text(encoding="utf-8") == "# canonical"  # didn't clobber
