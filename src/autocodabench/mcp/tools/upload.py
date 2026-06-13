"""MCP tool: publish a built bundle to Codabench.

Thin wrapper around :func:`autocodabench.upload.upload_zip`. Credentials
come from the environment (CODABENCH_USERNAME/PASSWORD or CODABENCH_TOKEN);
see that module for the resolution rules.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ...core.config import resolve_bundle_dir
from ...run_log import logged_tool
from ...upload.service import upload_zip
from ..instance import mcp

log = logging.getLogger("autocodabench.upload")


def _do_upload(slug: str, root_dir: str | None) -> dict[str, Any]:
    """Resolve slug → zip path, then delegate to upload_zip (env-creds)."""
    bundle_dir = resolve_bundle_dir(slug, root_dir)
    zip_path = bundle_dir.parent / f"{slug}.zip"
    return upload_zip(zip_path)


@mcp.tool()
@logged_tool("autocodabench_upload_bundle")
async def autocodabench_upload_bundle(
    slug: str,
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Publish the bundle's .zip to Codabench and return its public URL.

    Use this **only on explicit user request**, after
    `autocodabench_validate_bundle` is clean and `autocodabench_zip_bundle`
    has produced `<bundles_root>/<slug>.zip`.

    Args:
        slug:     bundle slug previously passed to autocodabench_init_bundle.
        root_dir: optional override of the bundles root.

    Returns:
        Dict with `competition_id`, `competition_url`, and the raw Codabench
        creation-status payload. On failure, `error` describes what went wrong.
    """
    log.info("upload_bundle requested slug=%s root_dir=%s", slug, root_dir)
    try:
        return await asyncio.to_thread(_do_upload, slug, root_dir)
    except Exception as e:
        return {"error": f"upload_bundle failed: {e}"}
