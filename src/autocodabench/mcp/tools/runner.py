"""MCP tools that *execute* a Codabench bundle.

These are the runtime counterparts to the file-writer tools in
`bundle.py`. The implementer skill (`autocodabench-implement`) uses
them to self-validate the bundle it just wrote: run the bundle's own
baseline through the scoring pipeline (inside the bundle's declared
`docker_image` when Docker is available — the platform-faithful path —
falling back to a per-run conda env otherwise) and execute the
starting-kit notebook end-to-end. The reformat-and-run skill uses
`run_user_submission` to score an external (ground-truth) submission
after it has been adapted to the bundle's interface.

The MCP wrappers are pure one-shots — the *skill* drives any retry
loop based on the returned `error` / `stderr_tail`. The loop belongs in
the skill, where a model session can read the traceback and decide how
to respond, not in this module, where it could only be a brittle regex
over stderr; a retry driven through logged tool calls is also an audit
trail, where one buried in library code would hide failures.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..instance import mcp
from ...run_log import logged_tool
from ...runner.execution import (
    install_env_extras,
    prepare_run_env,
    remove_run_env,
    run_baseline_submission,
    run_starting_kit,
    run_user_submission,
)

log = logging.getLogger("autocodabench.runner")


@mcp.tool()
@logged_tool("autocodabench_prepare_run_env")
async def autocodabench_prepare_run_env(
    slug: str,
    force_recreate: bool = False,
) -> dict[str, Any]:
    """Clone the base conda env and install the bundle's per-program requirements.

    Idempotent: if an env named `acb-run-<short>` already exists for
    this session's run, it is reused unless `force_recreate=True`. The
    requirements union covers `scoring_program/requirements.txt`,
    `ingestion_program/requirements.txt`, and any bundle-root
    `requirements.txt`.

    Use `uv pip install` when uv is on PATH (much faster), falls back
    to pip. Installer stdout/stderr are tee'd to `<run>/run_logs/<slug>/env/`.

    Args:
        slug:           bundle slug.
        force_recreate: if True, remove an existing env first.

    Returns:
        Dict: `ok`, `env_name`, `env_python`, `requirements_path`,
              `package_count`, `install_method` (uv|pip|skip),
              `duration_s`, `logs_dir`, `error`.
    """
    log.info("prepare_run_env slug=%s force_recreate=%s", slug, force_recreate)
    try:
        return await asyncio.to_thread(prepare_run_env, slug, force_recreate)
    except Exception as e:
        return {"ok": False, "error": f"prepare_run_env crashed: {e}"}


@mcp.tool()
@logged_tool("autocodabench_install_env_extras")
async def autocodabench_install_env_extras(
    env_name: str,
    packages: list[str],
) -> dict[str, Any]:
    """Install extra PyPI packages into an existing per-run env.

    Use when the implementer diagnoses a `ModuleNotFoundError` or a
    version conflict from a previous run's stderr — pass the PyPI
    name(s) and retry. Empty list is a logged no-op.

    Args:
        env_name: env returned by `autocodabench_prepare_run_env`.
        packages: list of PyPI specs (e.g. `["scikit-image", "tf_keras>=2.15"]`).

    Returns:
        Dict: `ok`, `installed`, `install_method`, `duration_s`,
              `stderr_tail`, `stdout_path`, `stderr_path`, `error`.
    """
    log.info("install_env_extras env=%s pkgs=%s", env_name, packages)
    try:
        return await asyncio.to_thread(install_env_extras, env_name, packages or [])
    except Exception as e:
        return {"ok": False, "error": f"install_env_extras crashed: {e}"}


@mcp.tool()
@logged_tool("autocodabench_run_baseline_submission")
async def autocodabench_run_baseline_submission(
    slug: str,
    env_name: str,
    subdir: str = "solution_baseline",
    extra_env: dict[str, str] | None = None,
    engine: str = "auto",
) -> dict[str, Any]:
    """Run the bundle's OWN baseline submission through ingestion + scoring.

    This is the bundle's self-test. The implementer skill calls this
    after writing the bundle to verify ingestion / scoring / metric
    plumbing actually works on real (toy) data, before any external
    submission ever touches it.

    Engine selection (`engine="auto"`, the default): when a Docker
    daemon is reachable, the programs run inside the bundle's declared
    `docker_image` exactly as the Codabench worker runs them — no
    dependency installation, working dir `/app/program` — so a clean
    run is evidence of platform behavior. Without Docker, the conda
    engine runs them in `env_name` with requirements installed; the
    result's `engine` / `engine_note` fields say which path ran. A
    failure under the docker engine that the conda engine does not show
    usually means a dependency is missing from the `docker_image` —
    fix the image choice in competition.yaml, not the env.

    Falls back gracefully to other common subdir names
    (`sample_code_submission`, `solution1`) if `subdir` does not exist.

    Under the conda engine, safe BLAS/OMP/TF single-thread defaults are
    exported at .so-load time (prevents the macOS libomp deadlock that
    hangs TF 2.21 + Keras 3 sessions); the docker engine keeps the
    image's own defaults, as the platform does. Pass `extra_env` ONLY
    to override (e.g. `{"OMP_NUM_THREADS": "4"}` for a perf test).

    Args:
        slug:      bundle slug.
        env_name:  env returned by `autocodabench_prepare_run_env`
                   (consumed only by the conda engine).
        subdir:    directory under `solutions/` containing the baseline.
        extra_env: optional env-var overrides applied at subprocess
                   start time (passed as `-e` under docker).
        engine:    "auto" (default) | "docker" | "conda".

    Returns:
        Dict: `ok`, `stage` ("ingestion"|"scoring"), `engine`,
              `docker_image`, `engine_note`, `ingestion`
              (`exit_code`/`stdout_tail`/`stderr_tail`/`duration_s`,
              `null` if λ-style), `scoring` (same shape), `scores`,
              `scores_format`, `sandbox_dir`, `logs_dir`, `error`.
              The `scores` dict mirrors the bundle's `scores.json`
              top-level keys verbatim.
    """
    log.info("run_baseline slug=%s env=%s subdir=%s engine=%s extra_env=%s",
             slug, env_name, subdir, engine, list((extra_env or {}).keys()))
    try:
        return await asyncio.to_thread(run_baseline_submission, slug, env_name, subdir,
                                       extra_env, engine)
    except Exception as e:
        return {"ok": False, "error": f"run_baseline_submission crashed: {e}"}


@mcp.tool()
@logged_tool("autocodabench_run_user_submission")
async def autocodabench_run_user_submission(
    slug: str,
    env_name: str,
    submission_dir: str,
    label: str,
    extra_env: dict[str, str] | None = None,
    engine: str = "auto",
) -> dict[str, Any]:
    """Run an external submission directory through ingestion + scoring.

    Used by `autocodabench-reformat-and-run` to score a ground-truth
    submission after it has been adapted to the bundle's interface. Same
    pipeline and engine semantics as `run_baseline_submission` (docker
    preferred — the bundle's declared `docker_image`, as the platform
    runs it; conda fallback consumes `env_name`), but the submission
    code is sourced from `submission_dir` instead of the bundle's
    `solutions/`.

    Under the conda engine, BLAS/OMP/TF thread pools default to
    single-threaded (set at .so-load time to avoid the macOS libomp
    deadlock); the docker engine keeps the image's defaults. Pass
    `extra_env` only to override per-call.

    Args:
        slug:           bundle slug.
        env_name:       env returned by `autocodabench_prepare_run_env`
                        (consumed only by the conda engine).
        submission_dir: absolute path to the submission folder.
        label:          short identifier scoping the run logs (e.g.
                        `"sub_1.attempt_2"`); appears in `logs_dir`.
        extra_env:      optional env-var overrides applied at subprocess
                        start time.
        engine:         "auto" (default) | "docker" | "conda".

    Returns:
        Same shape as `run_baseline_submission`.
    """
    log.info("run_user slug=%s env=%s sub=%s label=%s engine=%s extra_env=%s",
             slug, env_name, submission_dir, label, engine,
             list((extra_env or {}).keys()))
    try:
        return await asyncio.to_thread(run_user_submission, slug, env_name, submission_dir,
                                       label, extra_env, engine)
    except Exception as e:
        return {"ok": False, "error": f"run_user_submission crashed: {e}"}


@mcp.tool()
@logged_tool("autocodabench_run_starting_kit")
async def autocodabench_run_starting_kit(
    slug: str,
    env_name: str,
    notebook_path: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute the bundle's starting-kit notebook end-to-end.

    Looks for `README.ipynb` at the bundle root or any `.ipynb` under
    `starting_kit/`. Uses `jupyter execute --inplace` (no papermill
    dep) so any cell that errors causes the run to fail with the
    traceback in `stderr_tail`. The executed copy is saved under
    `<run>/run_logs/<slug>/starting_kit/executed.ipynb` for review.

    Default BLAS/OMP/TF thread pools are single-threaded. Pass
    `extra_env` only to override per-call.

    Args:
        slug:          bundle slug.
        env_name:      env returned by `autocodabench_prepare_run_env`.
        notebook_path: optional explicit path; otherwise auto-discovered.
        extra_env:     optional env-var overrides.

    Returns:
        Dict: `ok`, `notebook_source`, `executed_notebook`,
              `cells_executed`, `exit_code`, `duration_s`, `timed_out`,
              `stdout_tail`, `stderr_tail`, `stdout_path`, `stderr_path`,
              `logs_dir`, `error`.
    """
    log.info("run_starting_kit slug=%s env=%s nb=%s extra_env=%s",
             slug, env_name, notebook_path, list((extra_env or {}).keys()))
    try:
        return await asyncio.to_thread(run_starting_kit, slug, env_name, notebook_path,
                                       extra_env)
    except Exception as e:
        return {"ok": False, "error": f"run_starting_kit crashed: {e}"}


@mcp.tool()
@logged_tool("autocodabench_remove_run_env")
async def autocodabench_remove_run_env(env_name: str) -> dict[str, Any]:
    """Remove a per-run conda env (best-effort cleanup at session end).

    The orchestrator calls this at the end of a successful run to keep
    `~/anaconda3/envs/` from growing unboundedly. Failure is logged
    but not fatal — env hygiene is not a correctness gate.

    Args:
        env_name: env to remove.

    Returns:
        Dict: `ok`, `env_name`, `duration_s`, `stderr_tail`.
    """
    log.info("remove_run_env env=%s", env_name)
    try:
        return await asyncio.to_thread(remove_run_env, env_name)
    except Exception as e:
        return {"ok": False, "error": f"remove_run_env crashed: {e}"}
