"""Thread-local recipe execution flags shared by server and services."""

from __future__ import annotations

import threading

_recipe_context = threading.local()


def values() -> dict:
    stored = getattr(_recipe_context, "values", None)
    if stored is None:
        stored = {}
        _recipe_context.values = stored
    return stored


def reset() -> None:
    _recipe_context.values = {}
    _recipe_context.destructive_allowed = False


def allows_destructive() -> bool:
    return bool(getattr(_recipe_context, "destructive_allowed", False))


def set_destructive_allowed(value: bool) -> None:
    _recipe_context.destructive_allowed = bool(value)


def requires_native_actions() -> bool:
    """True only while ``run_test_cases`` is replaying a browser recipe."""
    return bool(getattr(_recipe_context, "native_actions_only", False))


def set_native_actions_only(value: bool) -> None:
    _recipe_context.native_actions_only = bool(value)


def get_native_actions_only() -> bool:
    return bool(getattr(_recipe_context, "native_actions_only", False))
