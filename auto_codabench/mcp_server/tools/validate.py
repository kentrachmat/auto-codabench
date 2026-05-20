"""MCP tool for local bundle linting."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..bundle_io import validate_bundle
from ..mcp import mcp

log = logging.getLogger("autocodabench.validate")


@mcp.tool()
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
