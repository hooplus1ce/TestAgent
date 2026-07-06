#!/usr/bin/env python3
"""drission-ui MCP server entry point (uv run drission-ui-mcp)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mcp-servers/drission-ui"))

from server import mcp


def main():
    """Run the drission-ui MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
