"""MCP tools for *writing* into a Codabench competition bundle.

This module is the thin shim between FastMCP (async, JSON-RPC) and
bundle_io (sync, file-system). The pattern matches the Semantic Scholar
reference repo: every tool wraps its body in try/except and returns a
structured error on failure instead of letting the exception propagate.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..bundle_io import (
    attach_data,
    init_bundle,
    write_competition_yaml,
    write_ingestion_program,
    write_page,
    write_scoring_program,
    write_solution,
)
from ..mcp import mcp

log = logging.getLogger("autocodabench.bundle")


@mcp.tool()
async def autocodabench_init_bundle(
    slug: str,
    root_dir: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create the empty directory skeleton for a Codabench competition bundle.

    Use this *first*, before writing any other file. The bundle root is
    `<root_dir>/<slug>/`; if root_dir is omitted, defaults to
    `auto_codabench/bundles/` inside the repo.

    Args:
        slug:       short kebab-case identifier; becomes the bundle's folder name.
        root_dir:   parent directory for the bundle. Optional.
        overwrite:  if True, an existing bundle dir is removed and recreated.

    Returns:
        Dict with `bundle_dir`, `created`, and the list of subdirectories that
        now exist (pages, scoring_program, etc.).
    """
    log.info("init_bundle slug=%s root_dir=%s overwrite=%s", slug, root_dir, overwrite)
    try:
        return await asyncio.to_thread(init_bundle, slug, root_dir, overwrite)
    except Exception as e:
        return {"error": f"init_bundle failed: {e}"}


@mcp.tool()
async def autocodabench_write_competition_yaml(
    slug: str,
    payload: dict[str, Any],
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Write `competition.yaml` at the bundle root.

    `payload` is the full YAML body as a dict. The writer rejects unknown
    top-level keys (typo guard) and fails if any required key is missing
    (version, title, image, terms, pages, phases, tasks, leaderboards).

    Args:
        slug:    bundle slug previously passed to autocodabench_init_bundle.
        payload: dict matching Codabench v2 schema. See codabench-bundle skill.
        root_dir: optional override of the bundles root.

    Returns:
        Dict with `bundle_dir` and the list of files written.
    """
    log.info("write_competition_yaml slug=%s keys=%s", slug, sorted(payload.keys()))
    try:
        return await asyncio.to_thread(write_competition_yaml, slug, payload, root_dir)
    except Exception as e:
        return {"error": f"write_competition_yaml failed: {e}"}


@mcp.tool()
async def autocodabench_write_page(
    slug: str,
    filename: str,
    body: str,
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Write a single markdown/HTML page under the bundle's `pages/` directory.

    If `filename` does not start with `pages/`, it is placed inside it
    automatically. The file extension must be `.md` or `.html`.

    Args:
        slug:     bundle slug.
        filename: e.g. `overview.md` or `pages/data.html`.
        body:     full file content (markdown or HTML).
        root_dir: optional override.

    Returns:
        Dict with `bundle_dir` and the relative path written.
    """
    log.info("write_page slug=%s filename=%s bytes=%d", slug, filename, len(body))
    try:
        return await asyncio.to_thread(write_page, slug, filename, body, root_dir)
    except Exception as e:
        return {"error": f"write_page failed: {e}"}


@mcp.tool()
async def autocodabench_write_scoring_program(
    slug: str,
    script: str,
    script_filename: str = "score.py",
    command: str | None = None,
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Write `scoring_program/<script_filename>` and its `metadata.yaml`.

    The default command mirrors the docker layout Codabench gives the worker:
    `python3 /app/program/<script_filename> /app/input /app/output`.

    The script must write a `scores.json` file in its output directory, whose
    top-level keys equal the `key:` of every non-computation leaderboard column.

    Args:
        slug:            bundle slug.
        script:          full Python source for the scoring script.
        script_filename: default `score.py`.
        command:         override the default `metadata.yaml` command.
        root_dir:        optional override.

    Returns:
        Dict with files written and the `command` line stored in metadata.yaml.
    """
    log.info("write_scoring_program slug=%s script=%s bytes=%d", slug, script_filename, len(script))
    try:
        return await asyncio.to_thread(
            write_scoring_program, slug, script, script_filename=script_filename, command=command, root_dir=root_dir
        )
    except Exception as e:
        return {"error": f"write_scoring_program failed: {e}"}


@mcp.tool()
async def autocodabench_write_ingestion_program(
    slug: str,
    script: str,
    script_filename: str = "ingestion.py",
    command: str | None = None,
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Write `ingestion_program/<script_filename>` and its `metadata.yaml`.

    Only needed for code-submission competitions (where participants submit
    a `submission.py` rather than predictions). The ingestion program is
    invoked with input_data, runs the submission, and writes predictions
    that the scoring program then reads.

    Args:
        slug:            bundle slug.
        script:          full Python source for the ingestion script.
        script_filename: default `ingestion.py`.
        command:         override the default `metadata.yaml` command.
        root_dir:        optional override.

    Returns:
        Dict with files written and the `command` line stored in metadata.yaml.
    """
    log.info("write_ingestion_program slug=%s script=%s bytes=%d", slug, script_filename, len(script))
    try:
        return await asyncio.to_thread(
            write_ingestion_program, slug, script, script_filename=script_filename, command=command, root_dir=root_dir
        )
    except Exception as e:
        return {"error": f"write_ingestion_program failed: {e}"}


@mcp.tool()
async def autocodabench_write_solution(
    slug: str,
    files: dict[str, str],
    subdir: str = "solution_baseline",
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Write a baseline solution (a folder of files) under `solutions/<subdir>/`.

    Use this for a "barely-passes" reference solution that organizers ship
    so participants have something to beat. The bundle's `solutions:` block
    in competition.yaml should reference `solutions/<subdir>/`.

    Args:
        slug:     bundle slug.
        files:    mapping of relative path inside the solution -> text body.
        subdir:   folder name under `solutions/`. Default `solution_baseline`.
        root_dir: optional override.

    Returns:
        Dict with files written.
    """
    log.info("write_solution slug=%s subdir=%s files=%d", slug, subdir, len(files))
    try:
        return await asyncio.to_thread(write_solution, slug, files, subdir=subdir, root_dir=root_dir)
    except Exception as e:
        return {"error": f"write_solution failed: {e}"}


@mcp.tool()
async def autocodabench_attach_data(
    slug: str,
    target: str,
    files: dict[str, str] | None = None,
    from_path: str | None = None,
    root_dir: str | None = None,
) -> dict[str, Any]:
    """Place data files into one of the bundle's data directories.

    Exactly one of `files` (text inline) or `from_path` (copy from disk)
    must be provided. Use `from_path` for binary data (images, parquet,
    large CSVs).

    Args:
        slug:      bundle slug.
        target:    one of `reference_data`, `input_data`, `starting_kit`, `public_data`.
        files:     mapping of relative path -> text body.
        from_path: absolute path to a file or directory tree to copy into target.
        root_dir:  optional override.

    Returns:
        Dict with `target` and the list of files written.
    """
    log.info("attach_data slug=%s target=%s mode=%s",
             slug, target, "files" if files is not None else "from_path")
    try:
        return await asyncio.to_thread(
            attach_data, slug, target=target, files=files, from_path=from_path, root_dir=root_dir
        )
    except Exception as e:
        return {"error": f"attach_data failed: {e}"}
