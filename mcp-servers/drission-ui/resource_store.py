"""Resource path helpers for MCP evidence files."""
import os
import re
import threading

import config


_lock = threading.RLock()
_current_module = ""


def _sanitize_segment(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._/\\")
    return text or "default"


def _sanitize_relative(path: str) -> list[str]:
    raw_parts = re.split(r"[\\/]+", str(path or ""))
    parts = []
    for part in raw_parts:
        if not part or part in {".", ".."}:
            continue
        parts.append(_sanitize_segment(part))
    return parts or ["resource"]


def set_module(module_name: str) -> dict:
    """Set the active module folder used for simple filename saves."""
    with _lock:
        global _current_module
        _current_module = _sanitize_segment(module_name)
        return {"ok": True, "module": _current_module}


def clear_module() -> dict:
    with _lock:
        global _current_module
        _current_module = ""
        return {"ok": True}


def get_context() -> dict:
    with _lock:
        return {"ok": True, "base_dir": os.path.abspath(config.SHOT_DIR), "module": _current_module}


def resolve_path(filename: str = None, default_name: str = None, category: str = None) -> str:
    """Resolve a safe path under config.SHOT_DIR.

    Simple filenames are saved under the active module folder after enter_module().
    Explicit relative paths with directories are respected and not auto-prefixed.
    Absolute filename values are treated as relative filenames by design.
    """
    rel = filename or default_name
    if not rel:
        rel = "resource"

    parts = _sanitize_relative(rel)
    explicit_dir = len(parts) > 1

    with _lock:
        module = _current_module

    prefix = []
    if module and not explicit_dir:
        prefix.append(module)
    if category and not explicit_dir:
        prefix.append(_sanitize_segment(category))

    full_path = os.path.join(config.SHOT_DIR, *(prefix + parts))
    os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)
    return full_path
