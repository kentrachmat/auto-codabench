import asyncio
from pathlib import Path

import pytest

from autocodabench.backends.base import AgentTask
from autocodabench.backends.replay import ReplayBackend

FIXTURE = (Path(__file__).resolve().parents[1]
           / "src" / "autocodabench" / "backends" / "fixtures" / "demo_bundle.jsonl")

DEMO_SLUG = "demo-ai-text-detection"


@pytest.fixture()
def demo_bundle(tmp_path: Path) -> Path:
    """The demo bundle, rebuilt from the shipped replay fixture."""
    result = asyncio.run(ReplayBackend(FIXTURE, out_dir=tmp_path).run(AgentTask(prompt="demo")))
    assert result.ok, result.error
    return tmp_path / DEMO_SLUG
