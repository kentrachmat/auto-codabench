"""MCP tool for local bundle linting.

A thin ``@logged_tool`` wrapper over :func:`core.bundle_io.validate_bundle`
(the schema lint); the full three-tier check framework is the separate
``autocodabench validate-bundle`` surface. Keeping the wrapper logic-free
guarantees that agent-side and CLI-side linting cannot diverge.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...core.bundle_io import validate_bundle
from ..instance import mcp
from ...run_log import logged_tool

log = logging.getLogger("autocodabench.validate")


@mcp.tool()
@logged_tool("autocodabench_validate_bundle")
async def autocodabench_validate_bundle(
    slug: str,
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Lint a competition bundle locally. Returns a list of issues.

    The linter rejects the most common upload errors:
      - competition.yaml missing required keys or unparseable
      - referenced pages / image / terms / data paths do not exist
      - scoring_program directory missing metadata.yaml or `command:` key
      - leaderboard column.key not produced by any score.py in the bundle
      - phases reference unknown task indexes or overlap chronologically

    Args:
        slug:     bundle slug.
        root_dir: optional override of the bundles root.

    Returns:
        Dict with `ok` (bool, True iff no errors), `issues` (list of
        {severity, where, message}), and `leaderboard_keys_expected` for cross-
        referencing against the scoring program.
    """
    log.info("validate_bundle slug=%s", slug)
    try:
        return await asyncio.to_thread(validate_bundle, slug, root_dir)
    except Exception as e:
        return {"error": f"validate_bundle failed: {e}"}
