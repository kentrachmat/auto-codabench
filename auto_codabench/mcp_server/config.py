"""Filesystem layout and constants for the AutoCodabench MCP server.

All paths are resolved relative to the repo root by default so that generated
bundles live alongside the agent's other artifacts and can be inspected /
diffed without leaving the project.
"""
from __future__ import annotations

import os
from pathlib import Path

# Repo root = three levels up from this file:
#   auto_codabench/mcp_server/config.py -> auto_codabench/mcp_server -> auto_codabench -> repo
REPO_ROOT = Path(__file__).resolve().parents[2]

# Default location for generated bundles. Override with AUTOCODABENCH_BUNDLES_ROOT.
DEFAULT_BUNDLES_ROOT = Path(
    os.environ.get("AUTOCODABENCH_BUNDLES_ROOT", REPO_ROOT / "auto_codabench" / "bundles")
).resolve()

# Where this package's text templates live.
TEMPLATES_DIR = (Path(__file__).resolve().parent / "templates").resolve()

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
         and points to a valid run dir. This is the web-UI path —
         scoping bundles per-session prevents concurrent sessions from
         clobbering each other's zips.
      3. Global `DEFAULT_BUNDLES_ROOT` as a final fallback (no active
         run, e.g. one-off CLI usage).
    """
    if root_dir:
        root = Path(root_dir).resolve()
    else:
        inherited = os.environ.get("AUTOCODABENCH_RUN_DIR")
        if inherited and Path(inherited).is_dir():
            root = (Path(inherited).resolve() / "bundles")
        else:
            root = DEFAULT_BUNDLES_ROOT
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        raise ValueError(f"Invalid bundle slug: {slug!r}")
    return root / slug
