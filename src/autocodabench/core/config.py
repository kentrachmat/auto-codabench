"""Filesystem layout and constants for autocodabench.

autocodabench is a pip-installable library, so nothing here may assume it
lives inside a checked-out repo. All artifact roots resolve, in order:

  1. an explicit argument from the caller,
  2. a scoping environment variable,
  3. a per-project workspace directory under the current working dir
     (``./.autocodabench/``).

Environment variables:

  AUTOCODABENCH_HOME          workspace root (default ``<cwd>/.autocodabench``)
  AUTOCODABENCH_BUNDLES_ROOT  where bundles land outside any run
  AUTOCODABENCH_RUNS_ROOT     where per-session run dirs land
  AUTOCODABENCH_RUN_DIR       the *active* run dir — set by ``open_run`` and
                              inherited by every child process so MCP
                              subprocesses adopt their parent's session
"""
from __future__ import annotations

import os
from pathlib import Path


def workspace_root() -> Path:
    """The per-project workspace dir. Not created until something writes."""
    return Path(os.environ.get("AUTOCODABENCH_HOME", Path.cwd() / ".autocodabench")).resolve()


def bundles_root() -> Path:
    env = os.environ.get("AUTOCODABENCH_BUNDLES_ROOT")
    return Path(env).resolve() if env else workspace_root() / "bundles"


def runs_root() -> Path:
    env = os.environ.get("AUTOCODABENCH_RUNS_ROOT")
    return Path(env).resolve() if env else workspace_root() / "runs"


# Subdirectory names inside each bundle. Mirrors Codabench's expected layout
# (see Yaml-Structure.md and Competition-Bundle-Structure.md).
BUNDLE_LAYOUT = {
    "pages": "pages",
    "scoring_program": "scoring_program",
    "ingestion_program": "ingestion_program",
    "solutions": "solutions",
    "input_data": "input_data",
    "reference_data": "reference_data",
    "starting_kit": "starting_kit",
    "public_data": "public_data",
}


def resolve_bundle_dir(slug: str, root_dir: str | None = None) -> Path:
    """Return the absolute path to a bundle root, creating no directories.

    Resolution order:
      1. Explicit `root_dir` arg (typically only the CLI passes this).
      2. `<AUTOCODABENCH_RUN_DIR>/bundles/<slug>/` if the env var is set
         and points to a valid run dir. This is the session path —
         scoping bundles per-session prevents concurrent sessions from
         clobbering each other's zips.
      3. Global `bundles_root()` as a final fallback (no active run,
         e.g. one-off CLI usage).
    """
    if root_dir:
        root = Path(root_dir).resolve()
    else:
        inherited = os.environ.get("AUTOCODABENCH_RUN_DIR")
        if inherited and Path(inherited).is_dir():
            root = (Path(inherited).resolve() / "bundles")
        else:
            root = bundles_root()
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        raise ValueError(f"Invalid bundle slug: {slug!r}")
    return root / slug
