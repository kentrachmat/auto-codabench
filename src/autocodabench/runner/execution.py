"""Execution-side helpers for the autocodabench MCP server.

The bundle-write side (`bundle_io.py`) only knows about *files*. This
module is the runtime counterpart: it stages the Codabench sandbox
layout, invokes the bundle's scoring / ingestion programs end-to-end,
and executes the bundle's `starting_kit` notebook.

Two execution engines exist, with different fidelity to the platform:

- **docker** (preferred when a daemon is reachable) — runs each program
  inside the bundle's declared ``docker_image`` exactly as the Codabench
  compute worker does: working directory ``/app/program``, the sandbox
  mounted under ``/app``, and **no dependency installation** (the worker
  never installs ``requirements.txt``; dependencies must be baked into
  the image). A clean run under this engine is evidence the bundle will
  execute on the platform; a subsequent platform failure points at the
  server, not the bundle.
- **conda** (fallback; required for the notebook) — a per-run cloned
  conda env with the bundle's ``requirements.txt`` installed. Strictly
  *more permissive* than the platform, so it verifies the programs but
  not the image; results carry an explicit fidelity note.

Used by the `autocodabench-implement` skill so it can self-validate
the bundle it just wrote (run its own sample submission + starting
kit) and by `autocodabench-reformat-and-run` (run an external user
submission through the bundle's scoring pipeline).

Design rules:

- **Pure one-shot.** Each function does one operation and returns;
  iteration is the model's job. No internal retry loops.
- **No model in this file.** Diagnosis of stderr lives in the skill
  prompt, which is where the actual Claude session can reason about
  the failure.
- **Bounded output.** stdout/stderr are tee'd to disk in full, but
  the returned dict carries only the last ~80 lines of each, keeping
  subprocess noise out of the model's context while preserving the
  complete record on disk.
- **Env names are deterministic.** `acb-run-<short>` derived from the
  active run's `branch_id_runtime_id` — so the same skill invocation
  always finds the same env on retry.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import threading
import time
import warnings
from collections import deque
from pathlib import Path
from typing import Any, IO, TextIO

import yaml  # PyYAML — already a dependency via fastmcp

from ..core.config import resolve_bundle_dir
from ..run_log import current_run, log_event

# How many tail lines of stdout/stderr to return inline. The full streams
# are always tee'd to disk; this only affects the in-message preview.
_TAIL_LINES = 80

# Cap individual subprocess wall-clock to 30 min by default. Long enough
# for a CPU-side baseline epoch but short enough to fail loud if the
# scoring program hangs.
_DEFAULT_TIMEOUT_S = 1800

# Defaults set in the OS environ for every subprocess we launch. These
# MUST be set before python starts because:
#
# 1. libomp / OpenBLAS / MKL read their thread-count vars at .so-load
#    time. By the time `import numpy` returns (which itself pulls libomp
#    in), the thread pools are already sized. Setting OMP_NUM_THREADS=1
#    in Python via `os.environ.setdefault` is too late — that was the
#    failure mode that hung the sub_1 run on macOS/arm64 for 24 minutes
#    using ~10s of CPU.
# 2. TF reads its inter/intra-op thread vars at session-creation time;
#    similar story.
# 3. PYTHONUNBUFFERED=1 makes child python flush stdout/stderr promptly
#    so the tee-to-disk output is closer to live.
# 4. TF_CPP_MIN_LOG_LEVEL=2 silences TF's INFO chatter; warnings/errors
#    still surface.
#
# Single-threading BLAS/OMP is a defensive default for the developer
# laptop case (small, toy-sized data — multi-threading overhead would
# dominate anyway). On Codabench's Linux workers + Docker the bundle
# will run with the docker_image's default settings, not these — the
# values live in the *harness's* subprocess env, not in the bundle.
# Per-call `env=` overrides still win (e.g. `extra_env={"OMP_NUM_THREADS": "8"}`).
_SUBPROCESS_DEFAULTS = {
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "TF_NUM_INTEROP_THREADS": "1",
    "TF_NUM_INTRAOP_THREADS": "1",
    "TF_CPP_MIN_LOG_LEVEL": "2",
    "PYTHONUNBUFFERED": "1",
}


# ---------------------------------------------------------------------------
# Execution engines
# ---------------------------------------------------------------------------

# Default docker_image for a bundle that declares none.
#
# autocodabench ships two purpose-built base images (docker/*.Dockerfile):
# autocodabench-base-cpu (Codabench py312 + the essential scientific stack and
# a pinned starting-kit notebook toolchain) and autocodabench-base-gpu (the
# gpu310 worker image plus the same stack). Pre-baking the dependencies means
# the great majority of generated bundles run inside the exact image
# Codabench's worker will use, with no per-run installation — the platform
# installs nothing, so a clean local run is evidence of a clean platform run.
#
# The names below are the *intended* published locations; they resolve only
# after the images are built and pushed (docker/build_and_push.sh) under a
# namespace you control. Override per-environment with AUTOCODABENCH_DOCKER_IMAGE
# / AUTOCODABENCH_DOCKER_IMAGE_GPU, or set AUTOCODABENCH_DOCKER_NAMESPACE to
# rewrite just the namespace. Until then, set the env var to a stock image
# (e.g. codalab/codalab-legacy:py312) to run without the custom base.
_DOCKER_NAMESPACE = os.environ.get("AUTOCODABENCH_DOCKER_NAMESPACE", "autocodabench")
_DEFAULT_DOCKER_IMAGE = os.environ.get(
    "AUTOCODABENCH_DOCKER_IMAGE", f"{_DOCKER_NAMESPACE}/autocodabench-base-cpu:latest")
_DEFAULT_DOCKER_IMAGE_GPU = os.environ.get(
    "AUTOCODABENCH_DOCKER_IMAGE_GPU", f"{_DOCKER_NAMESPACE}/autocodabench-base-gpu:latest")

_ENGINES = ("auto", "docker", "conda")

# Worker-faithful container paths. The Codabench compute worker mounts the
# *active program directory* (scoring OR ingestion, run as separate
# invocations) at /app/program, with the working directory set there, and
# the data/output trees at the paths below (compute_worker.py). It also
# substitutes legacy $variables in metadata commands with these paths
# before execution. We honor both spellings — the literal /app/... path
# and its $variable — so a bundle authored either way runs unchanged.
#
# `_WORKER_PATHS` maps each (variable, absolute) spelling to the role used
# to resolve it per engine. Order is longest-first so that, e.g.,
# `/app/input_data` is matched before `/app/input`.
_WORKER_ROLES = (
    # (role, container_abs_path, variable_alias)
    ("input_data", "/app/input_data", "$input_data"),
    ("ingested_program", "/app/ingested_program", "$ingested_program"),
    ("submission", "/app/submission", "$submission"),
    ("program", "/app/program", "$program"),
    ("output", "/app/output", "$output"),
    ("input", "/app/input", "$input"),
)


def _docker_available() -> bool:
    """True when a Docker CLI and a reachable daemon are both present."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=10,
        )
        return probe.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def bundle_docker_image(slug: str, root_dir: str | None = None) -> str:
    """The image competition.yaml declares; Codabench's default otherwise."""
    yaml_path = resolve_bundle_dir(slug, root_dir) / "competition.yaml"
    if yaml_path.is_file():
        try:
            comp = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            image = comp.get("docker_image")
            if isinstance(image, str) and image.strip():
                return image.strip()
        except yaml.YAMLError:
            pass
    return _DEFAULT_DOCKER_IMAGE


# The conda engine is deprecated and scheduled for removal: Docker is the only
# platform-faithful path, so the project is consolidating on it. conda still
# runs (so Docker-less hosts — CI, HF Spaces — keep working today), but every
# use emits this notice so operators can migrate before it is removed.
_CONDA_DEPRECATION = (
    "the conda execution engine is deprecated and will be removed; it does not "
    "reflect how Codabench runs programs (inside the bundle's docker_image, "
    "installing nothing). Install a Docker daemon to use the platform-faithful "
    "engine. See docker/README.md."
)


def _warn_conda_deprecation() -> None:
    """Emit the conda-engine deprecation once per process."""
    warnings.warn(_CONDA_DEPRECATION, DeprecationWarning, stacklevel=3)


def resolve_execution_engine(engine: str = "auto") -> dict[str, Any]:
    """Select the engine for scoring/ingestion runs.

    Docker is the platform-faithful path: the Codabench worker executes
    programs inside the competition's ``docker_image`` and never installs
    ``requirements.txt``. The conda engine is **deprecated** and kept only as a
    fallback for hosts without a Docker daemon (CI, HF Spaces); selecting it
    emits a ``DeprecationWarning`` and an explanatory note.

    Returns ``{"engine": "docker"|"conda"|None, "note": ..., "error": ...}``.
    """
    if engine not in _ENGINES:
        return {"engine": None, "note": None,
                "error": f"unknown engine {engine!r}; expected one of {_ENGINES}"}
    if engine == "docker":
        if _docker_available():
            return {"engine": "docker", "note": None, "error": None}
        return {"engine": None, "note": None,
                "error": "engine='docker' requested but no Docker daemon is "
                         "reachable (is Docker installed and running?)"}
    if engine == "conda":
        _warn_conda_deprecation()
        return {"engine": "conda",
                "note": "conda engine requested explicitly (DEPRECATED). "
                        + _CONDA_DEPRECATION,
                "error": None}
    if _docker_available():
        return {"engine": "docker", "note": None, "error": None}
    _warn_conda_deprecation()
    return {"engine": "conda",
            "note": "Docker unavailable — fell back to the conda engine "
                    "(DEPRECATED). " + _CONDA_DEPRECATION,
            "error": None}


def _host_path_for_role(role: str, sandbox: Path, program_subdir: str) -> Path:
    """Real on-disk path the worker would mount for a container role."""
    if role == "program":
        return sandbox / "program" / program_subdir
    if role == "ingested_program":
        return sandbox / "program" / "ingestion_program"
    return sandbox / role  # input, output, input_data, submission


def _resolve_command(cmd: str, eng: str, sandbox: Path, program_subdir: str) -> str:
    """Resolve worker path tokens in a metadata command for the given engine.

    Under docker, `$program`/`$input`/... become the worker's absolute
    container paths (the mounts make them real); literal `/app/...` paths
    are already correct and left untouched. Under conda there is no
    `/app`, so both the `$variable` and the absolute `/app/...` spellings
    are rewritten to real host sandbox paths. Longest tokens are replaced
    first so prefixes (``/app/input`` vs ``/app/input_data``) do not clash.
    """
    if eng == "docker":
        for _role, abspath, var in _WORKER_ROLES:
            cmd = cmd.replace(var, abspath)
        return cmd
    # conda: map every spelling to the host path
    pairs: list[tuple[str, str]] = []
    for role, abspath, var in _WORKER_ROLES:
        host = str(_host_path_for_role(role, sandbox, program_subdir))
        pairs.append((abspath, host))
        pairs.append((var, host))
    for token, host in sorted(pairs, key=lambda kv: -len(kv[0])):
        cmd = cmd.replace(token, host)
    return cmd


def _docker_run(image: str, sandbox: Path, program_subdir: str, cmd: str,
                extra_env: dict[str, str] | None, has_ingestion: bool) -> str:
    """Build the ``docker run`` invocation that mirrors the compute worker.

    The active program directory is mounted at ``/app/program`` with the
    working directory set there, and the data/output trees at
    ``/app/input`` / ``/app/output`` / ``/app/input_data`` /
    ``/app/submission`` — exactly the worker's layout, so a bundle's
    ``/app/...`` metadata command runs verbatim. Nothing is installed
    into the container: the worker never installs ``requirements.txt``,
    so neither do we. The first run of a new image pulls it, which can
    take minutes and counts against the timeout.
    """
    mounts: list[tuple[Path, str, str]] = [
        (_host_path_for_role("program", sandbox, program_subdir), "/app/program", "rw"),
        (sandbox / "input", "/app/input", "rw"),
        (sandbox / "output", "/app/output", "rw"),
    ]
    for role in ("input_data", "submission", "public_data", "sample_data"):
        if (sandbox / role).exists():
            mounts.append((sandbox / role, f"/app/{role}", "rw"))
    if has_ingestion and program_subdir != "ingestion_program":
        mounts.append((sandbox / "program" / "ingestion_program",
                       "/app/ingested_program", "ro"))

    env = {"PYTHONUNBUFFERED": "1"}
    if extra_env:
        env.update(extra_env)
    env_flags = " ".join(f"-e {shlex.quote(f'{k}={v}')}" for k, v in env.items())
    vol_flags = " ".join(
        f"-v {shlex.quote(str(h))}:{c}:{m}" for h, c, m in mounts)
    resolved = _resolve_command(cmd, "docker", sandbox, program_subdir)
    return (f"docker run --rm {env_flags} {vol_flags} -w /app/program "
            f"{shlex.quote(image)} bash -c {shlex.quote(resolved)}")


# ---------------------------------------------------------------------------
# Env management
# ---------------------------------------------------------------------------

def _env_name_for_run() -> str:
    """Derive a stable conda env name from the active run.

    Returns `acb-run-<short>` where `<short>` is the first 24 chars of
    the run's `branch_id_runtime_id`. Conda envs are tied to one
    autocodabench session for cache reuse on retry; the orchestrator
    cleans them up at finalize.
    """
    run = current_run()
    if run is None:
        # No run open — fall back to a fixed dev env. The MCP returns
        # an error from the tool layer if this is hit unexpectedly.
        return "acb-run-default"
    return f"acb-run-{run.name[:24]}"


def _pump(src: IO[str], sink: TextIO | None, tail: deque[str], counter: list[int]) -> None:
    """Daemon-thread body: copy one stream line-by-line to disk + ring buffer.

    `counter` is a single-element list used as a thread-safe lines-seen
    counter (GIL atomicity is enough — only one writer per stream). The
    ring buffer (`tail`, bounded `deque(maxlen=_TAIL_LINES)`) keeps the
    last N lines for the inline return.

    Each line is flushed to disk immediately. `bufsize=1` on the Popen
    + `flush()` here is what makes the on-disk file actually live —
    important for long-running TF/torch jobs where the only signal that
    they are alive is the steady drip of progress lines.
    """
    try:
        for line in iter(src.readline, ""):
            tail.append(line.rstrip("\n"))
            counter[0] += 1
            if sink is not None:
                sink.write(line)
                sink.flush()
    finally:
        try:
            src.close()
        except Exception:
            pass


def _bash(cmd: str, cwd: Path | str | None = None, timeout: int | None = None,
          stdout: Path | None = None, stderr: Path | None = None,
          env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run a shell command. Tee streams to disk live (line-by-line).

    The on-disk files at `stdout` / `stderr` paths grow as the
    subprocess runs, so `tail -f` works from outside this process —
    important for long-running ingestion / training where the only
    signal the process is alive is its steady output. The
    `stdout_tail` / `stderr_tail` returned in the result dict are the
    last `_TAIL_LINES` lines collected by per-stream daemon threads.

    Returns a dict with exit_code, duration_s, stdout_tail, stderr_tail,
    stdout_path, stderr_path, stdout_lines, stderr_lines, command,
    timed_out.
    """
    timeout = timeout or _DEFAULT_TIMEOUT_S
    t0 = time.perf_counter()
    timed_out = False

    proc_env = os.environ.copy()
    # Set our subprocess defaults BEFORE merging caller's env, so explicit
    # caller values (e.g. extra_env={"OMP_NUM_THREADS": "8"}) override.
    for k, v in _SUBPROCESS_DEFAULTS.items():
        proc_env.setdefault(k, v)
    if env:
        proc_env.update(env)

    out_tail: deque[str] = deque(maxlen=_TAIL_LINES)
    err_tail: deque[str] = deque(maxlen=_TAIL_LINES)
    out_count = [0]
    err_count = [0]

    fout = stdout.open("w", encoding="utf-8") if stdout else None
    ferr = stderr.open("w", encoding="utf-8") if stderr else None

    try:
        # `start_new_session=True` makes the child a process-group leader, so
        # on timeout we can SIGKILL the whole group via `os.killpg` and reap
        # grandchildren (conda run → bash → python). Without this, `p.kill()`
        # only hits the direct child and leaves orphans pinning CPU/memory
        # — observed in the 6/3 run where the python ingestion process was
        # still alive 30+ minutes after its parent was killed.
        p = subprocess.Popen(
            cmd, shell=True, cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=proc_env, bufsize=1,
            start_new_session=True,
        )
        t_out = threading.Thread(target=_pump, args=(p.stdout, fout, out_tail, out_count),
                                 daemon=True)
        t_err = threading.Thread(target=_pump, args=(p.stderr, ferr, err_tail, err_count),
                                 daemon=True)
        t_out.start()
        t_err.start()

        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()
            p.wait()
            timed_out = True

        # Let pump threads drain whatever buffered output the child wrote
        # between its last flush and exit. A short join is enough — the
        # child's pipes are closed once it exits, so `iter(readline, "")`
        # terminates.
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        exit_code = p.returncode
    finally:
        if fout: fout.close()
        if ferr: ferr.close()

    duration_s = round(time.perf_counter() - t0, 2)
    return {
        "command": cmd,
        "exit_code": exit_code,
        "duration_s": duration_s,
        "timed_out": timed_out,
        "stdout_tail": "\n".join(out_tail),
        "stderr_tail": "\n".join(err_tail),
        "stdout_path": str(stdout) if stdout else None,
        "stderr_path": str(stderr) if stderr else None,
        "stdout_lines": out_count[0],
        "stderr_lines": err_count[0],
    }


def _run_logs_dir(slug: str) -> Path:
    """Per-session run-logs root: <run>/run_logs/<slug>/ when a run is open."""
    run = current_run()
    if run is None:
        # CLI fallback: write next to the bundle.
        return resolve_bundle_dir(slug).parent / f"{slug}_run_logs"
    d = run / "run_logs" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _conda_run_prefix(env_name: str) -> str:
    """The prefix every in-env python invocation uses.

    `--no-capture-output` so child stdout/stderr stream straight through
    instead of being buffered until the env exits (which masked the
    abseil-deadlock SIGTERM in the 6/2 run).
    """
    return f"conda run --no-capture-output -n {shlex.quote(env_name)}"


def _gather_requirements(slug: str) -> list[Path]:
    """Find every per-program requirements.txt in the bundle.

    Codabench convention: each `scoring_program/` and `ingestion_program/`
    may carry its own `requirements.txt`. We install the union.
    """
    bundle_dir = resolve_bundle_dir(slug)
    out = []
    for name in ("scoring_program", "ingestion_program"):
        p = bundle_dir / name / "requirements.txt"
        if p.is_file() and p.stat().st_size > 0:
            out.append(p)
    # also a bundle-root requirements.txt if some implementer chose to use one
    root_req = bundle_dir / "requirements.txt"
    if root_req.is_file() and root_req.stat().st_size > 0:
        out.append(root_req)
    return out


# ---------------------------------------------------------------------------
# prepare_run_env
# ---------------------------------------------------------------------------

def prepare_run_env(slug: str, force_recreate: bool = False) -> dict[str, Any]:
    """Clone base conda env and install the bundle's per-program requirements.

    The env serves the conda fallback engine and the starting-kit
    notebook (which runs participant-side, not on the platform worker).
    Scoring/ingestion runs prefer the docker engine, which uses the
    bundle's declared image and needs no env.

    Returns `{env_name, requirements_path, package_count, install_method,
    duration_s, logs_dir, ok, error}`.
    """
    if shutil.which("conda") is None:
        return {"ok": False, "error": "conda not on PATH; cannot prepare per-run env"}

    env_name = _env_name_for_run()
    logs = _run_logs_dir(slug) / "env"
    logs.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    # Check if env already exists
    list_res = _bash("conda env list --json", stdout=logs / "env_list.stdout",
                     stderr=logs / "env_list.stderr")
    exists = False
    try:
        envs = json.loads(list_res["stdout_tail"] or "{}").get("envs", [])
        exists = any(Path(e).name == env_name for e in envs)
    except json.JSONDecodeError:
        pass

    if exists and force_recreate:
        rm = _bash(f"conda env remove -n {shlex.quote(env_name)} -y",
                   stdout=logs / "remove.stdout", stderr=logs / "remove.stderr")
        if rm["exit_code"] != 0:
            return {"ok": False, "env_name": env_name,
                    "error": f"failed to remove existing env: {rm['stderr_tail']}"}
        exists = False

    if not exists:
        clone = _bash(f"conda create -n {shlex.quote(env_name)} --clone base -y",
                      stdout=logs / "clone.stdout", stderr=logs / "clone.stderr",
                      timeout=600)
        if clone["exit_code"] != 0:
            return {"ok": False, "env_name": env_name,
                    "error": f"conda clone failed: {clone['stderr_tail']}",
                    "logs_dir": str(logs)}

    # Resolve env python
    which = _bash(f"{_conda_run_prefix(env_name)} which python",
                  stdout=logs / "which.stdout", stderr=logs / "which.stderr")
    env_python = (which["stdout_tail"].splitlines() or [""])[0].strip()
    if not env_python:
        return {"ok": False, "env_name": env_name,
                "error": "could not resolve python path in cloned env",
                "logs_dir": str(logs)}

    # Union of all per-program requirements
    req_paths = _gather_requirements(slug)
    union: list[str] = []
    for p in req_paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and s not in union:
                union.append(s)

    requirements_file = logs / "requirements.txt"
    requirements_file.write_text("\n".join(union) + ("\n" if union else ""), encoding="utf-8")

    install_method = "skip"
    install_res = None
    if union:
        if shutil.which("uv"):
            install_method = "uv"
            install_res = _bash(
                f"uv pip install --python {shlex.quote(env_python)} -r {shlex.quote(str(requirements_file))}",
                stdout=logs / "install.stdout", stderr=logs / "install.stderr",
                timeout=1200,
            )
        else:
            install_method = "pip"
            install_res = _bash(
                f"{_conda_run_prefix(env_name)} pip install -r {shlex.quote(str(requirements_file))}",
                stdout=logs / "install.stdout", stderr=logs / "install.stderr",
                timeout=1800,
            )
        if install_res["exit_code"] != 0:
            return {"ok": False, "env_name": env_name,
                    "install_method": install_method,
                    "error": f"requirements install failed: {install_res['stderr_tail']}",
                    "logs_dir": str(logs),
                    "package_count": len(union)}

    duration_s = round(time.perf_counter() - t0, 2)
    return {
        "ok": True,
        "env_name": env_name,
        "env_python": env_python,
        "requirements_path": str(requirements_file),
        "package_count": len(union),
        "install_method": install_method,
        "duration_s": duration_s,
        "logs_dir": str(logs),
    }


def install_env_extras(env_name: str, packages: list[str]) -> dict[str, Any]:
    """Install extra PyPI packages into an existing per-run env.

    Used when the implementer diagnoses a missing package from stderr
    (e.g. `ModuleNotFoundError: skimage` → `["scikit-image"]`) and
    needs to retry without recreating the env.
    """
    if not packages:
        return {"ok": True, "installed": [], "note": "empty package list — no-op"}

    logs = _run_logs_dir("__extras__") / "env"
    logs.mkdir(parents=True, exist_ok=True)

    # Find env python
    which = _bash(f"{_conda_run_prefix(env_name)} which python",
                  stdout=logs / "which.stdout", stderr=logs / "which.stderr")
    env_python = (which["stdout_tail"].splitlines() or [""])[0].strip()
    if not env_python:
        return {"ok": False, "error": f"env {env_name!r} not found or has no python"}

    pkgs = " ".join(shlex.quote(p) for p in packages)
    if shutil.which("uv"):
        install_res = _bash(
            f"uv pip install --python {shlex.quote(env_python)} {pkgs}",
            stdout=logs / "extras_install.stdout", stderr=logs / "extras_install.stderr",
            timeout=1200,
        )
        method = "uv"
    else:
        install_res = _bash(
            f"{_conda_run_prefix(env_name)} pip install {pkgs}",
            stdout=logs / "extras_install.stdout", stderr=logs / "extras_install.stderr",
            timeout=1800,
        )
        method = "pip"

    return {
        "ok": install_res["exit_code"] == 0,
        "installed": packages if install_res["exit_code"] == 0 else [],
        "install_method": method,
        "duration_s": install_res["duration_s"],
        "stderr_tail": install_res["stderr_tail"],
        "stdout_path": install_res["stdout_path"],
        "stderr_path": install_res["stderr_path"],
        "error": None if install_res["exit_code"] == 0
                 else f"install failed: {install_res['stderr_tail']}",
    }


# ---------------------------------------------------------------------------
# Subprocess scoring/ingestion runner
# ---------------------------------------------------------------------------

def _read_scores(output_dir: Path) -> dict[str, Any]:
    """Parse `scores.json` or `scores.txt` from the scoring output dir."""
    j = output_dir / "scores.json"
    if j.is_file():
        try:
            return {"format": "json", "scores": json.loads(j.read_text(encoding="utf-8"))}
        except json.JSONDecodeError as e:
            return {"format": "json", "scores": None,
                    "parse_error": f"scores.json malformed: {e}"}
    t = output_dir / "scores.txt"
    if t.is_file():
        scores: dict[str, Any] = {}
        for line in t.read_text(encoding="utf-8").splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                try:
                    scores[k.strip()] = float(v.strip())
                except ValueError:
                    scores[k.strip()] = v.strip()
        return {"format": "txt", "scores": scores}
    return {"format": None, "scores": None,
            "parse_error": "neither scores.json nor scores.txt found"}


def _run_submission_in_sandbox(
    slug: str, env_name: str, submission_dir: Path,
    label: str,
    extra_env: dict[str, str] | None = None,
    engine: str = "auto",
) -> dict[str, Any]:
    """Stage a sandbox, run ingestion (if defined) + scoring, parse scores.

    `label` is used to scope the run-logs dir (e.g. "baseline", "sub_1.attempt_1").
    `engine` selects how the programs execute: "docker" (inside the
    bundle's declared docker_image, as the platform does), "conda" (the
    per-run env named by `env_name`), or "auto" (docker when available,
    conda otherwise). The result records which engine ran.

    Layout inside the sandbox (mounted at /app under the docker engine):
      sandbox/
        program/                 # scoring_program (and ingestion_program if present)
        input/
          res/                   # ingestion output OR submission's prediction file(s)
          ref/                   # reference_data (held-out labels)
        output/                  # scoring writes scores.json here
        submission/              # the submission code being run
    """
    bundle_dir = resolve_bundle_dir(slug)
    if not bundle_dir.exists():
        return {"ok": False, "error": f"bundle dir not found: {bundle_dir}"}
    if not submission_dir.exists():
        return {"ok": False, "error": f"submission dir not found: {submission_dir}"}

    resolved = resolve_execution_engine(engine)
    if resolved["error"]:
        return {"ok": False, "error": resolved["error"]}
    eng: str = resolved["engine"]
    engine_note = resolved["note"]
    image = bundle_docker_image(slug) if eng == "docker" else None

    def _run_stage(program_subdir: str, raw_cmd: str,
                   out: Path, err: Path) -> dict[str, Any]:
        """Run one program stage under the active engine, worker-faithfully."""
        if eng == "docker":
            full = _docker_run(image, sandbox, program_subdir, raw_cmd,
                               extra_env, has_ingestion)
            cwd = None  # working dir is set inside the container (/app/program)
            env = None
        else:
            translated = _resolve_command(raw_cmd, "conda", sandbox, program_subdir)
            full = f"{_conda_run_prefix(env_name)} bash -c {shlex.quote(translated)}"
            cwd = sandbox / "program" / program_subdir
            env = extra_env or None
        return _bash(full, cwd=cwd, stdout=out, stderr=err, env=env)

    logs = _run_logs_dir(slug) / label
    logs.mkdir(parents=True, exist_ok=True)
    sandbox = logs / "sandbox"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)

    # Stage the bundle pieces.
    (sandbox / "program").mkdir()
    (sandbox / "input" / "res").mkdir(parents=True)
    (sandbox / "input" / "ref").mkdir(parents=True)
    (sandbox / "output").mkdir()

    scoring_src = bundle_dir / "scoring_program"
    if scoring_src.exists():
        shutil.copytree(scoring_src, sandbox / "program" / "scoring_program")
    ingestion_src = bundle_dir / "ingestion_program"
    # An ingestion program "exists" only if its directory holds runnable
    # content. `init_bundle` creates an empty `ingestion_program/` skeleton
    # for every bundle, so testing the directory alone misclassifies a
    # λ-style (prediction-file) competition as γ-style and then fails on a
    # nonexistent ingestion script. Require an actual file.
    has_ingestion = ingestion_src.is_dir() and any(ingestion_src.iterdir())
    if has_ingestion:
        shutil.copytree(ingestion_src, sandbox / "program" / "ingestion_program")

    ref_src = bundle_dir / "reference_data"
    if ref_src.exists():
        for p in ref_src.iterdir():
            if p.is_file():
                shutil.copy2(p, sandbox / "input" / "ref" / p.name)
            else:
                shutil.copytree(p, sandbox / "input" / "ref" / p.name)
    input_src = bundle_dir / "input_data"
    if input_src.exists():
        shutil.copytree(input_src, sandbox / "input_data")
    public_src = bundle_dir / "public_data"
    if public_src.exists():
        shutil.copytree(public_src, sandbox / "public_data")
    sample_src = bundle_dir / "sample_data"
    if sample_src.exists():
        shutil.copytree(sample_src, sandbox / "sample_data")

    shutil.copytree(submission_dir, sandbox / "submission")

    # --- Stage 1: ingestion (γ-style) or copy predictions (λ-style) ---
    if has_ingestion:
        # ingestion_program/metadata.yaml has a `command:` we honor; the
        # fallback uses the canonical worker tokens so it resolves under
        # either engine.
        meta_path = sandbox / "program" / "ingestion_program" / "metadata.yaml"
        ing_cmd = _read_command_from_metadata(meta_path,
                    fallback="python3 $program/ingestion.py "
                             "$input_data $submission $input/res")
        ing = _run_stage("ingestion_program", ing_cmd,
                         logs / "ingestion_stdout.txt", logs / "ingestion_stderr.txt")
        if ing["exit_code"] != 0 or ing["timed_out"]:
            return {
                "ok": False, "stage": "ingestion",
                "engine": eng, "docker_image": image, "engine_note": engine_note,
                "ingestion": ing,
                "scoring": None,
                "score": None, "scores": None,
                "sandbox_dir": str(sandbox), "logs_dir": str(logs),
                "error": f"ingestion exit {ing['exit_code']} (timeout={ing['timed_out']})",
            }
    else:
        # λ-style: submission must contain predictions.* files
        for p in (sandbox / "submission").iterdir():
            if p.is_file() and p.name.startswith("predictions"):
                shutil.copy2(p, sandbox / "input" / "res" / p.name)
        # Also accept a `res/` subdir
        sub_res = sandbox / "submission" / "res"
        if sub_res.is_dir():
            for p in sub_res.iterdir():
                shutil.copy2(p, sandbox / "input" / "res" / p.name)

    # --- Stage 2: scoring ---
    meta_path = sandbox / "program" / "scoring_program" / "metadata.yaml"
    score_cmd = _read_command_from_metadata(meta_path,
                fallback="python3 $program/score.py $input $output")
    score_run = _run_stage("scoring_program", score_cmd,
                           logs / "scoring_stdout.txt", logs / "scoring_stderr.txt")

    scores_blob = _read_scores(sandbox / "output")

    return {
        "ok": score_run["exit_code"] == 0 and not score_run["timed_out"]
              and scores_blob.get("scores") is not None,
        "stage": "scoring",
        "engine": eng, "docker_image": image, "engine_note": engine_note,
        "ingestion": ing if has_ingestion else None,
        "scoring": score_run,
        "scores": scores_blob.get("scores"),
        "scores_format": scores_blob.get("format"),
        "scores_parse_error": scores_blob.get("parse_error"),
        "sandbox_dir": str(sandbox),
        "logs_dir": str(logs),
        "error": None if (score_run["exit_code"] == 0 and not score_run["timed_out"]
                          and scores_blob.get("scores") is not None)
                 else (f"scoring exit {score_run['exit_code']}"
                       + (f" (timeout)" if score_run['timed_out'] else "")
                       + (f"; {scores_blob.get('parse_error')}" if scores_blob.get('parse_error') else "")),
    }


def _read_command_from_metadata(meta_path: Path, fallback: str) -> str:
    """Read `command:` from a Codabench metadata.yaml. Cheap parse — no PyYAML dep."""
    if not meta_path.is_file():
        return fallback
    for line in meta_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("command:"):
            cmd = s.split(":", 1)[1].strip()
            cmd = cmd.strip('"').strip("'")
            return cmd or fallback
    return fallback


def run_baseline_submission(slug: str, env_name: str,
                            subdir: str = "solution_baseline",
                            extra_env: dict[str, str] | None = None,
                            engine: str = "auto",
                            ) -> dict[str, Any]:
    """Run the bundle's own baseline through its scoring pipeline.

    The bundle ships `solutions/<subdir>/` as a working example. Under
    the docker engine (preferred; selected by `engine="auto"` whenever a
    daemon is reachable) it runs inside the bundle's declared
    `docker_image` exactly as Codabench's worker would run a participant
    submission, so a clean run is evidence of platform behavior.
    `env_name` is used only by the conda fallback engine.

    `extra_env` (optional) overrides per-subprocess env vars at process
    start time (passed as `-e` flags under docker). Use sparingly — the
    conda engine has sane defaults for BLAS/OMP/TF thread pools, and the
    docker engine deliberately keeps the image's own defaults, as the
    platform does.
    """
    bundle_dir = resolve_bundle_dir(slug)
    candidates = [
        bundle_dir / "solutions" / subdir,
        bundle_dir / "solutions" / "sample_code_submission",
        bundle_dir / "solutions" / "solution1",
        bundle_dir / "solution" / "sample_code_submission",
    ]
    sub_dir = next((c for c in candidates if c.is_dir()), None)
    if sub_dir is None:
        return {"ok": False,
                "error": f"no baseline submission found under bundle's solutions/ "
                         f"(checked: {[str(c) for c in candidates]})"}
    log_event("run_baseline_started", slug=slug, env_name=env_name,
              submission=str(sub_dir), engine=engine)
    res = _run_submission_in_sandbox(slug, env_name, sub_dir, label="baseline",
                                     extra_env=extra_env, engine=engine)
    log_event("run_baseline_finished", slug=slug, ok=res["ok"],
              engine=res.get("engine"), error=res.get("error"),
              score=res.get("scores"))
    return res


def run_user_submission(slug: str, env_name: str, submission_dir: str,
                        label: str,
                        extra_env: dict[str, str] | None = None,
                        engine: str = "auto",
                        ) -> dict[str, Any]:
    """Run an arbitrary submission directory through the bundle's scoring pipeline.

    `label` namespaces this run's logs (e.g. "sub_1.attempt_2"). Used
    by the reformat-and-run skill against a ground-truth submission
    after it has been adapted to the bundle's interface. Engine
    semantics match `run_baseline_submission`: docker preferred,
    `env_name` consumed only by the conda fallback.

    `extra_env` (optional) overrides per-subprocess env vars at process
    start time.
    """
    sub_dir = Path(submission_dir).resolve()
    log_event("run_user_started", slug=slug, env_name=env_name,
              submission=str(sub_dir), label=label, engine=engine)
    res = _run_submission_in_sandbox(slug, env_name, sub_dir, label=label,
                                     extra_env=extra_env, engine=engine)
    log_event("run_user_finished", slug=slug, label=label, ok=res["ok"],
              engine=res.get("engine"), error=res.get("error"),
              score=res.get("scores"))
    return res


# ---------------------------------------------------------------------------
# Starting-kit notebook runner
# ---------------------------------------------------------------------------

def run_starting_kit(slug: str, env_name: str,
                     notebook_path: str | None = None,
                     extra_env: dict[str, str] | None = None,
                     ) -> dict[str, Any]:
    """Execute the bundle's starting-kit notebook end-to-end in the cloned env.

    Looks for `README.ipynb` or `starting_kit/*.ipynb` at the bundle root.
    Uses `jupyter execute` (papermill-free, works with stock jupyter).
    Writes the executed notebook back to `<bundle>/run_logs/.../executed.ipynb`
    so a reviewer can scroll through cell outputs.
    """
    bundle_dir = resolve_bundle_dir(slug)
    if notebook_path:
        nb = Path(notebook_path).resolve()
    else:
        nb_candidates: list[Path] = [bundle_dir / "README.ipynb"]
        kit_dir = bundle_dir / "starting_kit"
        if kit_dir.is_dir():
            nb_candidates.extend(sorted(kit_dir.glob("*.ipynb")))
        nb = next((c for c in nb_candidates if c.is_file()), None)
    if nb is None or not nb.is_file():
        return {"ok": False,
                "error": "no starting-kit notebook found "
                         "(looked for README.ipynb / starting_kit/*.ipynb)"}

    logs = _run_logs_dir(slug) / "starting_kit"
    logs.mkdir(parents=True, exist_ok=True)
    executed = logs / "executed.ipynb"
    shutil.copy2(nb, executed)

    cmd = (f"{_conda_run_prefix(env_name)} "
           f"jupyter execute --inplace --NbClientApp.allow_errors=False "
           f"{shlex.quote(str(executed))}")
    log_event("run_starting_kit_started", slug=slug, notebook=str(nb))
    res = _bash(cmd, cwd=bundle_dir,
                stdout=logs / "stdout.txt", stderr=logs / "stderr.txt",
                timeout=3600, env=extra_env or None)

    # Count cells executed by re-reading the notebook (best-effort, no nbformat dep)
    cells_executed = None
    try:
        nb_json = json.loads(executed.read_text(encoding="utf-8"))
        cells = nb_json.get("cells", [])
        cells_executed = sum(1 for c in cells
                             if c.get("cell_type") == "code"
                             and c.get("execution_count") is not None)
    except Exception:
        pass

    ok = res["exit_code"] == 0 and not res["timed_out"]
    log_event("run_starting_kit_finished", slug=slug, ok=ok,
              cells_executed=cells_executed, error=None if ok else res["stderr_tail"][:200])
    return {
        "ok": ok,
        "notebook_source": str(nb),
        "executed_notebook": str(executed),
        "cells_executed": cells_executed,
        "exit_code": res["exit_code"],
        "duration_s": res["duration_s"],
        "timed_out": res["timed_out"],
        "stdout_tail": res["stdout_tail"],
        "stderr_tail": res["stderr_tail"],
        "stdout_path": res["stdout_path"],
        "stderr_path": res["stderr_path"],
        "logs_dir": str(logs),
        "error": None if ok else
                 ("notebook timed out" if res["timed_out"]
                  else f"jupyter execute exit {res['exit_code']}: {res['stderr_tail'][:200]}"),
    }


# ---------------------------------------------------------------------------
# Env teardown
# ---------------------------------------------------------------------------

def remove_run_env(env_name: str) -> dict[str, Any]:
    """Remove a per-run conda env. Best-effort; never raises."""
    if shutil.which("conda") is None:
        return {"ok": False, "error": "conda not on PATH"}
    res = _bash(f"conda env remove -n {shlex.quote(env_name)} -y", timeout=600)
    return {"ok": res["exit_code"] == 0,
            "env_name": env_name,
            "duration_s": res["duration_s"],
            "stderr_tail": res["stderr_tail"]}
