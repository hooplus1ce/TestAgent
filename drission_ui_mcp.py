"""drission-ui MCP server 入口包装。

供 `uv run drission-ui-mcp` 命令使用，等价于
`uv run python mcp-servers/drission-ui/server.py`。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-servers", "drission-ui"))

from server import mcp


def main():
    mcp.run()


if __name__ == "__main__":
    main()
