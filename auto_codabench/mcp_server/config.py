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
    """Return the absolute path to a bundle root, creating no directories."""
    root = Path(root_dir).resolve() if root_dir else DEFAULT_BUNDLES_ROOT
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        raise ValueError(f"Invalid bundle slug: {slug!r}")
    return root / slug
