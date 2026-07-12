"""Portable project entry point shared by Claude, Codex, Trae, and local runs."""
import os
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parent


def main() -> None:
    # Relative paths in dp_configs.ini are always resolved inside mcp-service.
    os.chdir(SERVICE_ROOT)
    from drissionpage_mcp.__main__ import main as run_server  # ty:ignore[unresolved-import]

    run_server()


if __name__ == "__main__":
    main()
