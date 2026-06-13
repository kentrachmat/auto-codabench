"""MCP tool for zipping a built bundle into its uploadable archive.

A thin ``@logged_tool`` wrapper over :func:`core.bundle_io.zip_bundle`;
the logic stays in core so the operation behaves identically from the
CLI, the web UI, and the agent.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...core.bundle_io import zip_bundle
from ..instance import mcp
from ...run_log import logged_tool

log = logging.getLogger("autocodabench.package")


@mcp.tool()
@logged_tool("autocodabench_zip_bundle")
async def autocodabench_zip_bundle(
    slug: str,
    root_dir: str | None = None,
    output: str | None = None,
) -> dict[str, Any]:
    """Produce the final `<slug>.zip` with `competition.yaml` AT THE ZIP ROOT.

    This is the single most common upload pitfall — zipping the containing
    folder instead of its contents puts `competition.yaml` one level too deep
    and Codabench will reject it. This tool always zips contents-only.

    Args:
        slug:     bundle slug.
        root_dir: optional override of the bundles root.
        output:   absolute path for the .zip. Defaults to `<root>/<slug>.zip`.

    Returns:
        Dict with `zip_path` and `size_bytes`.
    """
    log.info("zip_bundle slug=%s output=%s", slug, output)
    try:
        return await asyncio.to_thread(zip_bundle, slug, root_dir, output)
    except Exception as e:
        return {"error": f"zip_bundle failed: {e}"}
