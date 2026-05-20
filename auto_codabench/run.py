#!/usr/bin/env python3
"""Thin wrapper to launch the AutoCodabench MCP server from a Claude config.

Usage in claude_desktop_config.json (or any MCP host):

    "autocodabench": {
      "command": "/path/to/conda/envs/semantic-scholar/bin/python",
      "args": ["/abs/path/to/auto-codabench/auto_codabench/run.py"]
    }
"""
from auto_codabench.mcp_server.server import main

if __name__ == "__main__":
    main()
