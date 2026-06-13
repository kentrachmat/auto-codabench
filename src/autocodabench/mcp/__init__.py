"""MCP stdio server — one *interface* over the core/runner layers.

The tools here contribute typing and logging, never logic: the CLI, the
web UI, and the agent all reach the same underlying functions, so this
surface is an observation and capability boundary rather than a second
implementation that could drift.

Run with ``python -m autocodabench.mcp.server`` (or the
``autocodabench-mcp-server`` console script).
"""
