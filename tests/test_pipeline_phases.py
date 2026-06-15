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
