"""Resource path helpers for MCP evidence files."""
import os
import re
import threading
import urllib.parse

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


def _resolve_existing_path(filename: str) -> str:
    decoded = urllib.parse.unquote(str(filename or ""))
    base_dir = os.path.abspath(config.SHOT_DIR)
    project_dir = os.path.abspath(config.PROJECT_ROOT)
    if os.path.isabs(decoded):
        full_path = os.path.abspath(decoded)
    else:
        parts = _sanitize_relative(decoded)
        full_path = os.path.abspath(os.path.join(base_dir, *parts))
    allowed = False
    for root in {base_dir, project_dir}:
        try:
            if os.path.commonpath([root, full_path]) == root:
                allowed = True
                break
        except ValueError:
            continue
    if not allowed:
        raise ValueError("resource path escapes resource and project directories")
    return full_path


def list_resources(max_files: int = 200) -> dict:
    """List saved evidence files under config.SHOT_DIR without reading contents."""
    base_dir = os.path.abspath(config.SHOT_DIR)
    files = []
    if not os.path.isdir(base_dir):
        return {"ok": True, "base_dir": base_dir, "files": files}

    for root, _, names in os.walk(base_dir):
        for name in names:
            path = os.path.join(root, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            rel = os.path.relpath(path, base_dir).replace(os.sep, "/")
            files.append({
                "path": rel,
                "uri": "drissionpage-mcp://resources/" + urllib.parse.quote(rel, safe=""),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
            if len(files) >= max_files:
                return {
                    "ok": True,
                    "base_dir": base_dir,
                    "files": files,
                    "_truncated": True,
                    "max_files": max_files,
                }

    files.sort(key=lambda item: item["modified"], reverse=True)
    return {"ok": True, "base_dir": base_dir, "files": files}


def read_text_resource(filename: str, max_chars: int = 500_000) -> str:
    """Read a saved text evidence file from config.SHOT_DIR."""
    path = _resolve_existing_path(filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(filename)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(max_chars + 1)
    if len(content) > max_chars:
        return content[:max_chars] + f"\n...(_truncated at {max_chars} chars)"
    return content
