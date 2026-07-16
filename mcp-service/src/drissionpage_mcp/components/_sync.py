"""Shared read/write locks for FileSystemProvider component tools."""

from collections.abc import Callable
from typing import TypeVar

from drissionpage_mcp.core.lock import _rwlock

T = TypeVar("T")


def with_read(fn: Callable[..., T], *args, **kwargs) -> T:
    _rwlock.acquire_read()
    try:
        return fn(*args, **kwargs)
    finally:
        _rwlock.release_read()


def with_write(fn: Callable[..., T], *args, **kwargs) -> T:
    _rwlock.acquire_write()
    try:
        return fn(*args, **kwargs)
    finally:
        _rwlock.release_write()
