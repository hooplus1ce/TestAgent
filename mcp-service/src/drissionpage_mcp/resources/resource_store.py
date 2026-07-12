"""Resource path helpers for MCP evidence files."""
import heapq
import json
import os
import re
import threading
import urllib.parse
import tempfile

from ..core import config


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

    base_dir = os.path.realpath(os.path.abspath(config.SHOT_DIR))
    candidate = os.path.abspath(os.path.join(base_dir, *(prefix + parts)))
    parent = os.path.realpath(os.path.dirname(candidate) or base_dir)
    try:
        if os.path.commonpath([base_dir, parent]) != base_dir:
            raise ValueError("resource path escapes resource directory")
    except ValueError:
        raise ValueError("resource path escapes resource directory") from None
    os.makedirs(parent, exist_ok=True)
    return os.path.join(parent, os.path.basename(candidate))


def _resolve_existing_path(filename: str) -> str:
    decoded = urllib.parse.unquote(str(filename or ""))
    base_dir = os.path.realpath(os.path.abspath(config.SHOT_DIR))
    project_dir = os.path.realpath(os.path.abspath(config.PROJECT_ROOT))
    if os.path.isabs(decoded):
        candidate = os.path.abspath(decoded)
    else:
        parts = _sanitize_relative(decoded)
        candidate = os.path.abspath(os.path.join(base_dir, *parts))
    full_path = os.path.realpath(candidate)
    for root in {base_dir, project_dir}:
        try:
            if os.path.commonpath([root, full_path]) == root:
                return full_path
        except ValueError:
            continue
    raise ValueError("resource path escapes resource and project directories")


def list_resources(max_files: int = 200) -> dict:
    """以内存有界的最小堆返回资源目录中最新的 ``max_files`` 个文件。"""
    base_dir = os.path.realpath(os.path.abspath(config.SHOT_DIR))
    limit = max(int(max_files or 0), 1)
    if not os.path.isdir(base_dir):
        return {"ok": True, "base_dir": base_dir, "files": []}

    newest = []
    sequence = 0
    total = 0
    for root, _, names in os.walk(base_dir):
        for name in names:
            path = os.path.join(root, name)
            real_path = os.path.realpath(path)
            try:
                if os.path.commonpath([base_dir, real_path]) != base_dir:
                    continue
                stat = os.stat(real_path)
            except (OSError, ValueError):
                continue
            rel = os.path.relpath(path, base_dir).replace(os.sep, "/")
            item = {
                "path": rel,
                "uri": "drissionpage-mcp://resources/" + urllib.parse.quote(rel, safe=""),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
            total += 1
            entry = (stat.st_mtime, sequence, item)
            sequence += 1
            if len(newest) < limit:
                heapq.heappush(newest, entry)
            elif entry[:2] > newest[0][:2]:
                heapq.heapreplace(newest, entry)

    files = [entry[2] for entry in sorted(newest, reverse=True)]
    result = {"ok": True, "base_dir": base_dir, "files": files}
    if total > limit:
        result.update({"_truncated": True, "max_files": limit, "total_files": total})
    return result


def _reject_json_constant(value: str):
    raise ValueError("non-finite JSON number is not allowed: %s" % value)


def _strict_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _validate_json_shape(value, max_depth: int = 100, max_nodes: int = 1_000_000) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError("JSON resource exceeds %d nodes" % max_nodes)
        if depth > max_depth:
            raise ValueError("JSON resource exceeds nesting depth %d" % max_depth)
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def read_json_resource(filename: str, max_bytes: int = 50_000_000):
    """读取完整 JSON 资源，同时用文件大小上限防止无界内存占用。"""
    path = _resolve_existing_path(filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(filename)
    max_bytes = min(max(int(max_bytes or 0), 1), 200_000_000)
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ValueError("JSON resource exceeds %d bytes" % max_bytes)
    try:
        with open(path, "r", encoding="utf-8") as source:
            data = json.load(
                source, parse_constant=_reject_json_constant,
                object_pairs_hook=_strict_json_object,
            )
    except RecursionError as exc:
        raise ValueError("JSON resource nesting is too deep") from exc
    _validate_json_shape(data)
    return data


def write_json_atomic(path: str, data) -> str:
    """在目标目录内完整写入临时文件后原子替换，避免留下半截 JSON。"""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % os.path.basename(path),
                                              suffix=".tmp", dir=parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def write_text_atomic(path: str, content: str) -> str:
    """在目标目录内完整写入 UTF-8 临时文件后原子替换。"""
    if not isinstance(content, str):
        raise TypeError("content must be text")
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % os.path.basename(path),
                                              suffix=".tmp", dir=parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


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
