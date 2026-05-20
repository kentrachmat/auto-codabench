"""Centralized FastMCP instance (avoids circular imports across tool modules).

Uses the standalone `fastmcp` 2.x package — same import that the
semantic-scholar server uses, so both servers can share one conda env.
"""
from fastmcp import FastMCP

mcp = FastMCP("autocodabench")
