#!/usr/bin/env python3
"""drissionpage-mcp server entry point (uv run drissionpage-mcp)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mcp-servers/drissionpage-mcp"))

from server import mcp  # type: ignore


def main():
    """Run the drissionpage-mcp server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
