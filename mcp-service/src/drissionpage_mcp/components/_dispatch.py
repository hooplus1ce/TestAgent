"""Lazy dispatch into server callables for FileSystemProvider tools.

Implementations may stay in ``server.py`` (shared helpers / recipe dispatch)
while MCP registration lives under ``components/``. Import is deferred so
provider discovery does not create circular import cycles at module load time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def server_call(name: str, *args: Any, **kwargs: Any) -> Any:
    from drissionpage_mcp import server as server_module

    fn: Callable[..., Any] = getattr(server_module, name)
    return fn(*args, **kwargs)


def server_fn(name: str) -> Callable[..., Any]:
    from drissionpage_mcp import server as server_module

    return getattr(server_module, name)
