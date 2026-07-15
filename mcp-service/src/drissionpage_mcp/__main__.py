"""Module entry point for the stdio MCP server."""

import os
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    os.chdir(SERVICE_ROOT)
    from .server import main as run_server

    run_server()


if __name__ == "__main__":
    main()
