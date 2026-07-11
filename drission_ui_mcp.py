#!/usr/bin/env python3
"""Compatibility entry point for the retired drission-ui MCP command."""
import os
import sys

# Keep existing user configuration working while enforcing one implementation.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mcp-servers/drissionpage-mcp"))

from server import mcp  # type: ignore


def main():
    """Run the unified drissionpage-mcp server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
