#!/usr/bin/env python3
"""drission-ui MCP server entry point (uv run drission-ui-mcp)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mcp-servers/drission-ui"))

from server import mcp

mcp.run()
