"""Runtime counterpart of core: scoring/ingestion execution.

Two engines: docker (the bundle's declared ``docker_image``, run as the
Codabench worker runs it — preferred) and conda (per-run cloned env —
the fallback, and the host for the starting-kit notebook).
"""
from .execution import (
    bundle_docker_image,
    install_env_extras,
    prepare_run_env,
    remove_run_env,
    resolve_execution_engine,
    run_baseline_submission,
    run_starting_kit,
    run_user_submission,
)

__all__ = [
    "bundle_docker_image",
    "install_env_extras",
    "prepare_run_env",
    "remove_run_env",
    "resolve_execution_engine",
    "run_baseline_submission",
    "run_starting_kit",
    "run_user_submission",
]
