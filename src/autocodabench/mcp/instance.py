"""Centralized FastMCP instance (avoids circular imports across tool modules).

Uses the standalone `fastmcp` 2.x package, pinned in pyproject.toml.
"""
from fastmcp import FastMCP

mcp = FastMCP("autocodabench")
