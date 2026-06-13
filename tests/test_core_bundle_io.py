"""Unit tests for the pure file-I/O core."""
import zipfile
from pathlib import Path

import pytest
import yaml

from autocodabench.core import bundle_io
from autocodabench.core.config import resolve_bundle_dir


def _minimal_bundle(tmp_path: Path, slug: str = "demo") -> Path:
    root = str(tmp_path)
    bundle_io.init_bundle(slug, root_dir=root, overwrite=True)
    bundle = tmp_path / slug
    (bundle / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    bundle_io.write_page(slug, "terms.md", "# Terms", root_dir=root)
    bundle_io.write_page(slug, "overview.md", "# Overview", root_dir=root)
    bundle_io.write_scoring_program(slug, "import json\njson.dump({'accuracy': 1}, open('x', 'w'))\n", root_dir=root)
    bundle_io.attach_data(slug, target="reference_data", files={"truth.csv": "1\n0\n"}, root_dir=root)
    bundle_io.write_solution(slug, files={"predictions.csv": "1\n0\n"}, root_dir=root)
    bundle_io.write_competition_yaml(slug, {
        "version": 2,
        "title": "Demo",
        "image": "logo.png",
        "terms": "pages/terms.md",
        "pages": [{"title": "Overview", "file": "pages/overview.md"}],
        "tasks": [{"index": 0, "name": "t", "scoring_program": "scoring_program/",
                   "reference_data": "reference_data/"}],
        "phases": [{"index": 0, "name": "P1", "start": "2026-01-01 00:00:00",
                    "end": "2026-12-31 00:00:00", "tasks": [0]}],
        "leaderboards": [{"title": "R", "key": "main",
                          "columns": [{"title": "Acc", "key": "accuracy", "index": 0,
                                       "sorting": "desc"}]}],
        "solutions": [{"index": 0, "path": "solutions/solution_baseline/", "tasks": [0]}],
    }, root_dir=root)
    return bundle


def test_init_bundle_creates_layout(tmp_path):
    out = bundle_io.init_bundle("demo", root_dir=str(tmp_path))
    assert out["created"] is True
    for d in ("pages", "scoring_program", "reference_data", "solutions"):
        assert (tmp_path / "demo" / d).is_dir()


def test_init_bundle_refuses_overwrite_by_default(tmp_path):
    bundle_io.init_bundle("demo", root_dir=str(tmp_path))
    out = bundle_io.init_bundle("demo", root_dir=str(tmp_path))
    assert out["created"] is False


def test_competition_yaml_rejects_unknown_keys(tmp_path):
    bundle_io.init_bundle("demo", root_dir=str(tmp_path))
    with pytest.raises(ValueError, match="unknown competition.yaml keys"):
        bundle_io.write_competition_yaml("demo", {
            "version": 2, "title": "x", "image": "logo.png", "terms": "t",
            "pages": [], "phases": [], "tasks": [], "leaderboards": [],
            "definitely_not_a_key": 1,
        }, root_dir=str(tmp_path))


def test_competition_yaml_requires_keys(tmp_path):
    bundle_io.init_bundle("demo", root_dir=str(tmp_path))
    with pytest.raises(ValueError, match="missing required"):
        bundle_io.write_competition_yaml("demo", {"title": "x"}, root_dir=str(tmp_path))


def test_page_path_traversal_rejected(tmp_path):
    bundle_io.init_bundle("demo", root_dir=str(tmp_path))
    with pytest.raises(ValueError):
        bundle_io.write_page("demo", "../evil.md", "x", root_dir=str(tmp_path))


def test_validate_clean_bundle(tmp_path):
    _minimal_bundle(tmp_path)
    report = bundle_io.validate_bundle("demo", root_dir=str(tmp_path))
    assert report["ok"] is True
    assert report["issues"] == []
    assert report["leaderboard_keys_expected"] == ["accuracy"]


def test_validate_catches_missing_page(tmp_path):
    bundle = _minimal_bundle(tmp_path)
    (bundle / "pages" / "overview.md").unlink()
    report = bundle_io.validate_bundle("demo", root_dir=str(tmp_path))
    assert report["ok"] is False
    assert any("overview.md" in i["message"] for i in report["issues"])


def test_validate_warns_on_unwritten_leaderboard_key(tmp_path):
    bundle = _minimal_bundle(tmp_path)
    score = bundle / "scoring_program" / "score.py"
    score.write_text(score.read_text().replace("accuracy", "acc"))
    report = bundle_io.validate_bundle("demo", root_dir=str(tmp_path))
    assert any("accuracy" in i["message"] and i["severity"] == "warning"
               for i in report["issues"])


def test_zip_puts_yaml_at_root(tmp_path):
    _minimal_bundle(tmp_path)
    out = bundle_io.zip_bundle("demo", root_dir=str(tmp_path))
    with zipfile.ZipFile(out["zip_path"]) as zf:
        assert "competition.yaml" in zf.namelist()


def test_resolve_bundle_dir_rejects_bad_slugs(tmp_path):
    for bad in ("", "a/b", "..", ".hidden"):
        with pytest.raises(ValueError):
            resolve_bundle_dir(bad, str(tmp_path))


def test_resolve_bundle_dir_prefers_run_dir(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setenv("AUTOCODABENCH_RUN_DIR", str(run))
    assert resolve_bundle_dir("x") == run / "bundles" / "x"


def test_validate_accepts_legacy_metadata_filename(tmp_path):
    """Production Codabench accepts an extensionless `metadata` file
    (verified against the STYLE-TRANS-FAIR reference bundle)."""
    bundle = _minimal_bundle(tmp_path)
    meta = bundle / "scoring_program" / "metadata.yaml"
    meta.rename(bundle / "scoring_program" / "metadata")
    report = bundle_io.validate_bundle("demo", root_dir=str(tmp_path))
    assert report["ok"] is True, report["issues"]
