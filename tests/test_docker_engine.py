"""Tests for the Docker execution engine (keyless; no Docker daemon needed).

The engine's *selection* logic and *command construction* are tested
here; actually executing a container is verified manually and by the
experiment harness, for the same reason live-LLM calls are excluded
from the unit suite.
"""
from pathlib import Path

import pytest

from autocodabench.runner import execution as ex


# -- engine resolution --------------------------------------------------------

def test_auto_prefers_docker_when_available(monkeypatch):
    monkeypatch.setattr(ex, "_docker_available", lambda: True)
    r = ex.resolve_execution_engine("auto")
    assert r["engine"] == "docker" and r["error"] is None and r["note"] is None


def test_auto_falls_back_to_conda_with_note(monkeypatch):
    monkeypatch.setattr(ex, "_docker_available", lambda: False)
    r = ex.resolve_execution_engine("auto")
    assert r["engine"] == "conda"
    assert "Docker unavailable" in r["note"]


def test_explicit_docker_errors_without_daemon(monkeypatch):
    monkeypatch.setattr(ex, "_docker_available", lambda: False)
    r = ex.resolve_execution_engine("docker")
    assert r["engine"] is None and "Docker daemon" in r["error"]


def test_explicit_conda_carries_fidelity_note(monkeypatch):
    monkeypatch.setattr(ex, "_docker_available", lambda: True)
    r = ex.resolve_execution_engine("conda")
    assert r["engine"] == "conda"
    assert "docker_image" in r["note"]


def test_unknown_engine_rejected():
    r = ex.resolve_execution_engine("podman")
    assert r["engine"] is None and "unknown engine" in r["error"]


# -- docker command construction ----------------------------------------------

def test_docker_run_mirrors_worker_contract(tmp_path):
    # stage the minimum the mount builder inspects
    (tmp_path / "program" / "scoring_program").mkdir(parents=True)
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    cmd = ex._docker_run(
        "codalab/codalab-legacy:py37", tmp_path, "scoring_program",
        "python3 $program/score.py $input $output",
        {"OMP_NUM_THREADS": "2"}, has_ingestion=False,
    )
    assert cmd.startswith("docker run --rm ")
    # the active program dir is mounted at /app/program, as the worker does
    assert f"-v {tmp_path / 'program' / 'scoring_program'}:/app/program:rw" in cmd
    assert f"-v {tmp_path / 'input'}:/app/input:rw" in cmd
    assert f"-v {tmp_path / 'output'}:/app/output:rw" in cmd
    assert "-w /app/program" in cmd              # worker's working directory
    assert "codalab/codalab-legacy:py37" in cmd
    # canonical $variables resolved to container paths
    assert "/app/program/score.py /app/input /app/output" in cmd
    assert "$program" not in cmd and "$input" not in cmd
    assert "-e PYTHONUNBUFFERED=1" in cmd
    assert "-e OMP_NUM_THREADS=2" in cmd
    # the platform never installs requirements — neither may the engine
    assert "pip install" not in cmd


def test_conda_translate_maps_worker_paths_to_host(tmp_path):
    # the conda engine has no /app — both $var and /app/... spellings must
    # rewrite to real host paths, longest-token-first (input_data vs input)
    out = ex._resolve_command(
        "python3 $program/score.py $input_data $input $output",
        "conda", tmp_path, "scoring_program")
    assert str(tmp_path / "program" / "scoring_program") in out
    assert str(tmp_path / "input_data") in out
    assert str(tmp_path / "input") + " " in out  # /app/input not mismangled
    assert "/app/" not in out and "$" not in out


# -- docker_image resolution from competition.yaml ------------------------------

def test_bundle_docker_image_reads_declared_image(tmp_path):
    bundle = tmp_path / "demo"
    bundle.mkdir()
    (bundle / "competition.yaml").write_text(
        "title: t\ndocker_image: myorg/myimage:1.2\n", encoding="utf-8")
    assert ex.bundle_docker_image("demo", str(tmp_path)) == "myorg/myimage:1.2"


def test_bundle_docker_image_defaults_to_platform_default(tmp_path):
    bundle = tmp_path / "demo"
    bundle.mkdir()
    (bundle / "competition.yaml").write_text("title: t\n", encoding="utf-8")
    assert ex.bundle_docker_image("demo", str(tmp_path)) == "codalab/codalab-legacy:py37"


# -- engine plumbing through the sandbox runner ---------------------------------

def test_run_user_submission_requires_daemon_for_explicit_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_docker_available", lambda: False)
    bundle = tmp_path / "demo"
    (bundle / "scoring_program").mkdir(parents=True)
    sub = tmp_path / "sub"
    sub.mkdir()
    monkeypatch.setenv("AUTOCODABENCH_BUNDLES_ROOT", str(tmp_path))
    res = ex.run_user_submission("demo", env_name="unused",
                                 submission_dir=str(sub), label="t",
                                 engine="docker")
    assert res["ok"] is False and "Docker daemon" in res["error"]
