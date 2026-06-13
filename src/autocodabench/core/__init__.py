"""Pure core: bundle-authoring file I/O and schema lint.

No LLM, no network, no MCP, no subprocess — by design: purity is what
makes this layer unit-testable without keys and trustworthy as the
foundation that every higher layer (checks, MCP tools, replay) builds on.
"""
from .bundle_io import (
    attach_data,
    init_bundle,
    validate_bundle,
    write_competition_yaml,
    write_ingestion_program,
    write_page,
    write_scoring_program,
    write_solution,
    zip_bundle,
)
from .config import BUNDLE_LAYOUT, bundles_root, resolve_bundle_dir, runs_root, workspace_root

__all__ = [
    "BUNDLE_LAYOUT",
    "attach_data",
    "bundles_root",
    "init_bundle",
    "resolve_bundle_dir",
    "runs_root",
    "validate_bundle",
    "workspace_root",
    "write_competition_yaml",
    "write_ingestion_program",
    "write_page",
    "write_scoring_program",
    "write_solution",
    "zip_bundle",
]
