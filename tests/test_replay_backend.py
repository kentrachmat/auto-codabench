"""Tests for the replay backend and fixture loading."""
import asyncio
import json

from autocodabench.backends.base import AgentTask
from autocodabench.backends.replay import ReplayBackend, load_fixture

from conftest import DEMO_SLUG, FIXTURE


def test_replay_rebuilds_validates_and_zips(tmp_path):
    result = asyncio.run(ReplayBackend(FIXTURE, out_dir=tmp_path).run(AgentTask(prompt="x")))
    assert result.ok
    assert result.total_cost_usd == 0.0
    bundle = tmp_path / DEMO_SLUG
    assert (bundle / "competition.yaml").is_file()
    assert (bundle.parent / f"{DEMO_SLUG}.zip").is_file()


def test_replay_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for out in (a, b):
        asyncio.run(ReplayBackend(FIXTURE, out_dir=out).run(AgentTask(prompt="x")))
    ya = (a / DEMO_SLUG / "competition.yaml").read_bytes()
    yb = (b / DEMO_SLUG / "competition.yaml").read_bytes()
    assert ya == yb


def test_unknown_and_session_tools_skipped(tmp_path):
    fixture = tmp_path / "f.jsonl"
    records = [
        {"tool": "autocodabench_open_run", "args": {}},
        {"tool": "autocodabench_init_bundle", "args": {"slug": "t", "overwrite": True}},
        {"tool": "made_up_tool", "args": {}},
    ]
    fixture.write_text("\n".join(json.dumps(r) for r in records))
    out = tmp_path / "out"
    result = asyncio.run(ReplayBackend(fixture, out_dir=out).run(AgentTask(prompt="x")))
    assert result.ok
    assert result.usage["replayed_calls"] == 1
    assert set(result.usage["skipped_calls"]) == {"autocodabench_open_run", "made_up_tool"}


def test_load_fixture_from_run_dir(tmp_path):
    run = tmp_path / "run"
    (run / "tool_calls").mkdir(parents=True)
    (run / "tool_calls" / "0001_autocodabench_init_bundle.json").write_text(json.dumps({
        "tool": "autocodabench_init_bundle",
        "args": {"slug": "t"},
        "result": {},
    }))
    (run / "final_text.md").write_text("done")
    records, final_text = load_fixture(run)
    assert records == [{"tool": "autocodabench_init_bundle", "args": {"slug": "t"}}]
    assert final_text == "done"


def test_on_text_callback_fires(tmp_path):
    seen = []
    task = AgentTask(prompt="x", on_text=seen.append)
    asyncio.run(ReplayBackend(FIXTURE, out_dir=tmp_path).run(task))
    assert any("write_competition_yaml" in t for t in seen)
