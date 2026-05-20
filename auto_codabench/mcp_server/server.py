"""Entry point: registers all tools and runs the MCP server over stdio."""
from __future__ import annotations

import logging
import sys

from .mcp import mcp
from . import tools  # noqa: F401 — side-effect: registers @mcp.tool() functions


def main() -> None:
    # Log to stderr so it never corrupts the stdio JSON-RPC stream.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s autocodabench %(levelname)s %(name)s — %(message)s",
    )
    logging.getLogger("autocodabench").info("starting AutoCodabench MCP server (stdio)")
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
