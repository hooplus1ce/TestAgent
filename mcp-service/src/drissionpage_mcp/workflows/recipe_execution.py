"""Recipe execution, artifact paths, test generation glue, and reporting.

Extracted from server.py so the MCP entrypoint stays assembly-oriented.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..core import config, recipe_context
from ..resources import resource_store
from ..services import (
    browser_session,
    filter_area,
    interaction,
    observe,
    page_model,
    role_sessions,
    session_auth,
    network_record,
    table_facade,
    vtable,
)
from . import flow_evidence, test_execution, test_reporting, testcase_generation

logger = logging.getLogger("drissionpage-mcp")


def _get_active_frame() -> dict:
    fr = browser_session.get_active_frame()
    if fr is None:
        return {"ok": False, "reason": "未找到活动 iframe，请先 enter_module"}
    return {
        "ok": True,
        "url": getattr(fr, "url", "") or "",
        "tab_name": browser_session.get_active_tab_name(),
    }


def _role_op(operation, role_id: str, **kwargs) -> dict:
    try:
        return operation(role_id, **kwargs)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}


def role_session_open(role_id: str, proxy: str = None) -> dict:
    return _role_op(role_sessions.open_role, role_id, proxy=proxy)


def role_session_login(role_id: str) -> dict:
    return _role_op(role_sessions.login_role, role_id)


def role_session_start(role_id: str) -> dict:
    return _role_op(role_sessions.start_role, role_id)


def role_session_activate(role_id: str) -> dict:
    return _role_op(role_sessions.activate_role, role_id)


def role_session_list() -> dict:
    return {"ok": True, "roles": role_sessions.list_roles()}


def role_session_close(role_id: str) -> dict:
    return _role_op(role_sessions.close_role, role_id)


def network_trace_start(targets=None, method: str = None) -> dict:
    return network_record.start(targets=targets, method=method)


def network_trace_stop(timeout: float = 3.0, max_packets: int = 50,
                       fit_count: bool = False, max_body_chars: int = 12000) -> dict:
    return network_record.stop(
        timeout=timeout, max_packets=max_packets,
        fit_count=fit_count, max_body_chars=max_body_chars,
    )


def browser_get_element_state(locator: str, state: str = None) -> dict:
    """Lazy import avoids circular dependency with services.devtools."""
    from ..services import devtools

    return devtools.browser_get_element_state(locator=locator, state=state)


def connect(port: int = None, target_hint: str = None) -> dict:
    tab = browser_session.connect(port, target_hint)
    return {
        "ok": True,
        "url": tab.url,
        "title": tab.title,
        "tabs": browser_session.list_tabs(),
    }


def check_session() -> dict:
    return session_auth.check_session()


def refresh_session() -> dict:
    return session_auth.refresh_session()


def get_active_frame() -> dict:
    return _get_active_frame()


def enter_module(menu_text: str, timeout: float = 8, expand_filter: bool = True) -> dict:
    return interaction.enter_module(menu_text, timeout=timeout, expand_filter=expand_filter)


def _read_json_resource(filename: str) -> tuple[dict | None, str | None]:
    try:
        value = resource_store.read_json_resource(filename)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, str(exc)
    return value, None


def _safe_artifact_segment(value: str, fallback: str = "default") -> str:
    value = re.sub(r'[^0-9A-Za-z_\-\u4e00-\u9fff]+', "_", str(value or "").strip())
    return value.strip("._-") or fallback


def _artifact_root() -> str:
    """Use the project root normally, while respecting isolated test resource roots."""
    project_root = os.path.abspath(config.PROJECT_ROOT)
    shot_root = os.path.abspath(config.SHOT_DIR)
    try:
        return project_root if os.path.commonpath([project_root, shot_root]) == project_root else shot_root
    except ValueError:
        return shot_root


def _module_artifact_name(module_info: dict | None) -> str:
    info = module_info or {}
    level1 = (info.get("module_level1_pinyin") or info.get("level1_pinyin") or
              info.get("module_level1") or "")
    module = (info.get("module_pinyin") or info.get("module_level2") or
              info.get("module_name") or "default")
    parts = [_safe_artifact_segment(item) for item in (level1, module) if str(item or "").strip()]
    return "_".join(parts) or "default"


def _resolve_artifact_path(filename: str | None, category: str, module_info: dict | None,
                           default_name: str) -> str:
    root = os.path.realpath(os.path.abspath(_artifact_root()))
    requested = str(filename or "").strip()
    if requested and os.path.isabs(requested):
        candidate = os.path.abspath(requested)
    elif requested and os.path.dirname(requested):
        candidate = os.path.abspath(os.path.join(root, requested))
    else:
        candidate = os.path.join(
            root, category, _module_artifact_name(module_info), requested or default_name,
        )
    parent = os.path.realpath(os.path.dirname(candidate) or root)
    try:
        if os.path.commonpath([root, parent]) != root:
            raise ValueError("artifact path escapes project directory")
    except ValueError:
        raise ValueError("artifact path escapes project directory") from None
    os.makedirs(parent, exist_ok=True)
    return os.path.join(parent, os.path.basename(candidate))


def _report_bundle_path(filename: str | None, module_info: dict | None,
                        execution_file: str) -> tuple[str, str]:
    """Return ``(report.md, bundle_dir)`` with every report asset kept locally."""
    requested = str(filename or "").strip()
    report_name = os.path.basename(requested) if requested else "test_report_%d.md" % int(time.time())
    if not report_name.lower().endswith(".md"):
        report_name += ".md"
    if requested and (os.path.isabs(requested) or os.path.dirname(requested)):
        direct = _resolve_artifact_path(requested, "", module_info, report_name)
        bundle_dir = os.path.dirname(direct)
        return direct, bundle_dir
    run_name = _safe_artifact_segment(
        os.path.splitext(os.path.basename(str(execution_file)))[0], "execution",
    )
    report_path = _resolve_artifact_path(
        os.path.join("test_results", "reports", _module_artifact_name(module_info),
                     run_name, report_name),
        "", module_info, report_name,
    )
    return report_path, os.path.dirname(report_path)


def _bundle_report_assets(execution: dict, execution_file: str, bundle_dir: str) -> dict:
    """复制受信目录内的真实位图证据，并在报告目录写入执行快照。"""
    bundled = flow_evidence.sanitize_artifact(execution)
    if not isinstance(bundled, dict):
        raise ValueError("execution must be an object")
    bundle_root = os.path.realpath(os.path.abspath(bundle_dir))
    assets_dir = os.path.realpath(os.path.join(bundle_root, "assets"))
    if os.path.commonpath([bundle_root, assets_dir]) != bundle_root:
        raise ValueError("report assets path escapes bundle directory")
    os.makedirs(assets_dir, exist_ok=True)
    copied, missing, used_names = [], [], set()
    copied_sources = {}
    allowed_roots = {
        os.path.realpath(os.path.abspath(config.SHOT_DIR)),
        os.path.realpath(os.path.abspath(config.PROJECT_ROOT)),
    }
    try:
        execution_dir = os.path.dirname(resource_store._resolve_existing_path(execution_file))
    except (OSError, ValueError):
        execution_dir = ""

    def allowed_source(path: str) -> bool:
        for root in allowed_roots:
            try:
                if os.path.commonpath([root, path]) == root:
                    return True
            except ValueError:
                continue
        return False

    def image_kind(path: str) -> str | None:
        try:
            size = os.path.getsize(path)
            if size <= 0 or size > 20_000_000:
                return None
            with open(path, "rb") as source:
                data = source.read()
        except OSError:
            return None
        if (len(data) >= 45 and data.startswith(b"\x89PNG\r\n\x1a\n")
                and data[12:16] == b"IHDR"
                and int.from_bytes(data[16:20], "big") > 0
                and int.from_bytes(data[20:24], "big") > 0
                and data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")):
            return ".png"
        if len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            return ".jpg"
        if (len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
                and int.from_bytes(data[4:8], "little") == len(data) - 8):
            return ".webp"
        return None

    def resolve_screenshot(raw: str) -> str | None:
        candidates = [str(raw)] if os.path.isabs(str(raw)) else [
            *([os.path.join(execution_dir, str(raw))] if execution_dir else []),
            os.path.join(config.SHOT_DIR, str(raw)),
            os.path.join(config.PROJECT_ROOT, str(raw)),
        ]
        for candidate in candidates:
            source = os.path.realpath(os.path.abspath(candidate))
            if os.path.isfile(source) and allowed_source(source):
                return source
        return None

    def update_ref_path(ref: dict, original: str, relative: str | None) -> None:
        ref["source_screenshot"] = original
        artifacts = ref.get("artifacts") if isinstance(ref.get("artifacts"), dict) else None
        if relative:
            ref["screenshot"] = relative
            ref.pop("screenshot_missing", None)
            if artifacts is not None and "screenshot" in artifacts:
                artifacts["screenshot"] = relative
        else:
            ref.pop("screenshot", None)
            ref["screenshot_missing"] = True
            if artifacts is not None:
                artifacts.pop("screenshot", None)


    def copy_ref(ref: dict) -> None:
        artifacts = ref.get("artifacts") if isinstance(ref, dict) else None
        screenshot = ref.get("screenshot") if isinstance(ref, dict) else None
        if not screenshot and isinstance(artifacts, dict):
            screenshot = artifacts.get("screenshot")
        if not screenshot:
            return
        original = str(screenshot)
        source = resolve_screenshot(original)
        if not source:
            missing.append(original)
            update_ref_path(ref, original, None)
            return
        if source in copied_sources:
            update_ref_path(ref, original, copied_sources[source])
            return
        ext = image_kind(source)
        if not ext:
            missing.append(original)
            update_ref_path(ref, original, None)
            return
        stem = _safe_artifact_segment(os.path.splitext(os.path.basename(source))[0], "evidence")
        base = stem + ext
        candidate, index = base, 2
        while candidate in used_names:
            candidate = "%s_%d%s" % (stem, index, ext)
            index += 1
        used_names.add(candidate)
        destination = os.path.join(assets_dir, candidate)
        relative = "assets/%s" % candidate
        if os.path.realpath(destination) != source:
            temp_path = None
            try:
                descriptor, temp_path = tempfile.mkstemp(prefix=".asset-", dir=assets_dir)
                os.close(descriptor)
                shutil.copy2(source, temp_path)
                os.replace(temp_path, destination)
                temp_path = None
            except OSError:
                missing.append(original)
                update_ref_path(ref, original, None)
                return
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
        copied_sources[source] = relative
        update_ref_path(ref, original, relative)
        copied.append(relative)

    for result in bundled.get("results", []):
        if not isinstance(result, dict):
            continue
        refs = result.get("evidence_refs")
        for ref in refs if isinstance(refs, list) else []:
            if isinstance(ref, dict):
                copy_ref(ref)
        defect = result.get("known_defect")
        if isinstance(defect, dict):
            refs = defect.get("evidence_refs")
            for ref in refs if isinstance(refs, list) else []:
                if isinstance(ref, dict):
                    copy_ref(ref)
    coverage_matrix = bundled.get("coverage_matrix")
    for row in coverage_matrix if isinstance(coverage_matrix, list) else []:
        if not isinstance(row, dict):
            continue
        refs = row.get("asset_evidence_refs") or row.get("evidence_refs") or []
        for ref in refs if isinstance(refs, list) else []:
            if isinstance(ref, dict):
                copy_ref(ref)
    known_defects = bundled.get("known_defects")
    for defect in known_defects if isinstance(known_defects, list) else []:
        if isinstance(defect, dict):
            refs = defect.get("evidence_refs")
            for ref in refs if isinstance(refs, list) else []:
                if isinstance(ref, dict):
                    copy_ref(ref)
    snapshot_path = os.path.join(bundle_dir, "execution.json")
    resource_store.write_json_atomic(snapshot_path, bundled)
    return {"execution": bundled, "execution_copy": snapshot_path,
            "assets_dir": assets_dir, "copied": copied,
            "missing": list(dict.fromkeys(missing))}


def _next_case_id_start(case_dir: str, exclude_path: str = None) -> dict:
    highest = {}
    if not os.path.isdir(case_dir):
        return {"default": 1}
    excluded = os.path.abspath(exclude_path) if exclude_path else None
    scanned_files = 0
    for root, _, names in os.walk(case_dir):
        for name in names:
            if not name.lower().endswith(".json"):
                continue
            if scanned_files >= 10_000:
                return {**{prefix: value + 1 for prefix, value in highest.items()},
                        "default": 1}
            scanned_files += 1
            source_path = os.path.abspath(os.path.join(root, name))
            if excluded and source_path == excluded:
                continue
            try:
                payload = resource_store.read_json_resource(source_path, max_bytes=10_000_000)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            raw_cases = payload.get("test_cases") if isinstance(payload, dict) else None
            for case in raw_cases if isinstance(raw_cases, list) else []:
                if not isinstance(case, dict):
                    continue
                match = re.search(r"([A-Za-z])(\d+)$", str(case.get("case_id", "")))
                if match:
                    prefix = match.group(1).upper()
                    highest[prefix] = max(highest.get(prefix, 0), int(match.group(2)))
    return {**{prefix: value + 1 for prefix, value in highest.items()}, "default": 1}


def generate_test_cases_from_flow(flow_file: str, module_info: dict = None,
                                  filename: str = None) -> dict:
    """由已保存的真实证据生成覆盖矩阵和 19 字段用例候选，仅输出已验证场景为正式用例。"""
    loaded = flow_evidence.load(flow_file)
    if not loaded.get("ok"):
        return loaded
    if module_info is not None and not isinstance(module_info, dict):
        return {"ok": False, "reason": "module_info must be an object"}
    info = dict(module_info or {})
    default_name = "cases_%s.json" % loaded["flow"].get("flow_id", "evidence")
    try:
        path = _resolve_artifact_path(filename, "test_cases", info, default_name)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    info.setdefault("case_id_start", _next_case_id_start(os.path.dirname(path), exclude_path=path))
    generated = testcase_generation.generate_verified_cases(loaded["flow"], info)
    persisted = flow_evidence.sanitize_artifact(generated)
    try:
        resource_store.write_json_atomic(path, persisted)
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": "test case persistence failed: %s" % exc}
    persisted["saved_to"] = path
    return persisted


def combine_test_case_files(case_files: list[str], filename: str = None,
                            module_info: dict = None, exclude_case_ids: list[str] = None,
                            exclude_known_defects: bool = False) -> dict:
    """合并多个真实 flow 用例文件，并按资产/场景去重汇总覆盖率。"""
    if not isinstance(case_files, list) or not case_files or len(case_files) > 100:
        return {"ok": False, "reason": "case_files must contain 1 to 100 files"}
    if any(not isinstance(item, str) or not item.strip() for item in case_files):
        return {"ok": False, "reason": "case_files entries must be non-empty strings"}
    if module_info is not None and not isinstance(module_info, dict):
        return {"ok": False, "reason": "module_info must be an object"}
    if exclude_case_ids is not None and not isinstance(exclude_case_ids, list):
        return {"ok": False, "reason": "exclude_case_ids must be a list"}
    if exclude_case_ids is not None and (
        len(exclude_case_ids) > 100_000
        or any(not isinstance(item, str) or not item.strip() for item in exclude_case_ids)
    ):
        return {"ok": False, "reason": "exclude_case_ids entries must be non-empty strings"}
    if not isinstance(exclude_known_defects, bool):
        return {"ok": False, "reason": "exclude_known_defects must be a boolean"}
    total_bytes = 0
    payloads = []
    for case_file in case_files:
        try:
            total_bytes += os.path.getsize(resource_store._resolve_existing_path(case_file))
        except (OSError, ValueError):
            pass
        if total_bytes > 200_000_000:
            return {"ok": False, "reason": "combined case files exceed 200000000 bytes"}
        payload, error = _read_json_resource(case_file)
        if error:
            return {"ok": False, "reason": "%s: %s" % (case_file, error)}
        payloads.append(payload)
    if not payloads:
        return {"ok": False, "reason": "case_files is empty"}
    merged = testcase_generation.merge_generated_suites(
        payloads, module_info=module_info,
        exclude_case_ids=exclude_case_ids,
        exclude_known_defects=exclude_known_defects,
    )
    if not isinstance(merged, dict):
        return {"ok": False, "reason": "test suite merger returned an invalid result"}
    if not merged.get("ok"):
        return merged
    info = merged.get("module_info")
    if not isinstance(info, dict):
        return {"ok": False, "reason": "merged module_info must be an object"}
    try:
        path = _resolve_artifact_path(
            filename, "test_cases", info, "test_suite_%d.json" % int(time.time()),
        )
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    persisted = flow_evidence.sanitize_artifact(merged)
    try:
        resource_store.write_json_atomic(path, persisted)
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": "test suite persistence failed: %s" % exc}
    persisted["saved_to"] = path
    return persisted


def _recipe_values() -> dict:
    return recipe_context.values()


def _reset_recipe_context() -> None:
    recipe_context.reset()


def _recipe_allows_destructive() -> bool:
    return recipe_context.allows_destructive()


def _recipe_requires_native_actions() -> bool:
    return recipe_context.requires_native_actions()


def _recipe_ref_value(path: str):
    parts = str(path or "").strip(".").split(".")
    value = _recipe_values()
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, (list, tuple)) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise KeyError(path)
    return value


def _resolve_recipe_refs(value, _depth: int = 0):
    if _depth > 20:
        raise ValueError("recipe reference nesting exceeds 20 levels")
    if isinstance(value, dict):
        if len(value) > 2_000:
            raise ValueError("recipe argument object is too large")
        if set(value) == {"$ref"}:
            return _resolve_recipe_refs(_recipe_ref_value(value["$ref"]), _depth + 1)
        if any(not isinstance(key, str) or len(key) > 256 for key in value):
            raise ValueError("recipe argument keys must be short strings")
        return {key: _resolve_recipe_refs(item, _depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 2_000:
            raise ValueError("recipe argument list is too large")
        return [_resolve_recipe_refs(item, _depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > 12_000:
            raise ValueError("recipe argument text is too large")
        return value
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("recipe argument number must be finite")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ValueError("recipe argument contains an unsupported value type")


def _recipe_element_click(locator: str, in_frame: bool = True, timeout: float = 5,
                          double_click: bool = False) -> dict:
    """Formal-replay click using only DrissionPage element APIs."""
    element = browser_session.find(locator, in_frame=in_frame, timeout=timeout, wait_clickable=False)
    if not element:
        return {"ok": False, "reason": "元素未找到: %s" % locator}
    try:
        element.wait.clickable(timeout=timeout, wait_stop=True, raise_err=False)
        if not element.states.is_clickable:
            return {"ok": False, "reason": "元素不可点击: %s" % locator}
        if double_click:
            element.click.multi(times=2)
        else:
            element.click(by_js=False, wait_stop=True)
        return {"ok": True, "locator": locator,
                "method": "element.click.multi" if double_click else "element.click"}
    except Exception as exc:
        return {"ok": False, "locator": locator,
                "reason": "DrissionPage 原生点击失败: %s" % exc}


def _recipe_double_click(locator: str, in_frame: bool = True, timeout: float = 5) -> dict:
    return _recipe_element_click(locator, in_frame=in_frame, timeout=timeout, double_click=True)


def _run_recipe_action(action: str, args: dict) -> dict:
    actions = {
        "click": _recipe_element_click,
        "double_click": _recipe_double_click,
        "explore_action": interaction.explore_action,
        "set_field_value": interaction.set_field_value,
        "reset_to_initial": interaction.reset_to_initial,
        "enter_module": interaction.enter_module,
        "get_active_frame": get_active_frame,
        "check_session": check_session,
        "get_table_values": table_facade.get_table_values,
        "query_table": table_facade.query_table,
        "find_vtable_row": table_facade.find_vtable_row,
        "count_vtable_rows": table_facade.count_vtable_rows,
        "get_vtable_row_values": table_facade.get_vtable_row_values,
        "scan_table": table_facade.scan_table,
        "get_vtable_cell_render_info": table_facade.get_vtable_cell_render_info,
        "inspect_table_cell": table_facade.inspect_table_cell,
        "vtable_action": table_facade.vtable_action,
        "click_table_cell": table_facade.click_table_cell,
        "table_action": table_facade.table_action,
        "select_option": page_model.select_option,
        "select_date_range": interaction.select_date_range,
        "set_date": interaction.set_date,
        "query_filter": _query_filter,
        "verify_filter_query": _verify_filter_query,
        "observe_snapshot": observe.observe_snapshot,
        "network_trace_start": network_trace_start,
        "network_trace_stop": network_trace_stop,
        "browser_get_element_state": browser_get_element_state,
        "find_elements": interaction.find_elements,
        "input": interaction.input,
        "insert_text": interaction.insert_text,
        "role_session_open": role_session_open,
        "role_session_login": role_session_login,
        "role_session_start": role_session_start,
        "role_session_activate": role_session_activate,
        "role_session_list": role_session_list,
        "role_session_close": role_session_close,
    }
    runner = actions.get(action)
    if runner is None:
        return {"ok": False, "reason": "unsupported recipe action: %s" % action}
    if not isinstance(args, dict):
        return {"ok": False, "reason": "recipe arguments must be an object"}
    recorded_args = dict(args)
    effective_args = dict(recorded_args)
    save_as = str(effective_args.pop("save_as", "") or "").strip()
    if save_as:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,127}", save_as):
            return {"ok": False, "reason": "save_as must be a short identifier"}
        values = _recipe_values()
        if save_as in values:
            return {"ok": False, "reason": "save_as already exists: %s" % save_as}
        if len(values) >= 100:
            return {"ok": False, "reason": "recipe context exceeds 100 saved values"}
    try:
        effective_args = _resolve_recipe_refs(effective_args)
    except (KeyError, ValueError) as exc:
        reason = ("recipe reference not found: %s" % exc.args[0]
                  if isinstance(exc, KeyError) else str(exc))
        return {"ok": False, "reason": reason}
    if action == "explore_action" and _recipe_requires_native_actions():
        if effective_args.get("by_js"):
            return {"ok": False, "reason": "run_test_cases 禁止 by_js 点击；请使用 DrissionPage 原生元素或动作链"}
        action_name = str(effective_args.get("action") or "").lower()
        target = effective_args.get("target")
        target_type = str(target.get("type") or "").lower() if isinstance(target, dict) else ""
        if action_name == "click_xy" or target_type in {"xy", "point", "coord", "coordinate"}:
            return {"ok": False,
                    "reason": "run_test_cases 禁止普通坐标点击；VTable 请使用 vtable_action 或 click_table_cell"}
    if (_recipe_requires_native_actions()
            and test_execution.is_destructive_command({"action": action, "args": effective_args})
            and not _recipe_allows_destructive()):
        return {"ok": False, "reason": "运行期解析出的破坏性操作要求 destructive=true"}
    for name in (
        "by_js", "in_frame", "clear", "raw", "allow_empty", "clean_overlays",
        "include_snapshot", "include_table_data", "only_visible", "hover_first",
        "select_row",
    ):
        if name in effective_args and effective_args[name] is not None and not isinstance(effective_args[name], bool):
            return {"ok": False, "reason": "%s must be a boolean" % name}
    for name, lower, upper in (("timeout", 0.1, 120.0), ("duration", 0.0, 30.0)):
        if name not in effective_args:
            continue
        if isinstance(effective_args[name], bool):
            return {"ok": False, "reason": "%s must be numeric" % name}
        try:
            numeric = float(effective_args[name])
        except (TypeError, ValueError):
            return {"ok": False, "reason": "%s must be numeric" % name}
        if not math.isfinite(numeric) or numeric < lower or numeric > upper:
            return {"ok": False, "reason": "%s must be between %s and %s" %
                    (name, lower, upper)}
        effective_args[name] = numeric
    started = time.perf_counter()
    try:
        result = runner(**effective_args)
    except TypeError as exc:
        result = {"ok": False, "reason": "invalid recipe arguments for %s: %s" % (action, exc)}
    except Exception as exc:
        result = {"ok": False, "reason": "recipe action %s failed: %s: %s" %
                  (action, type(exc).__name__, exc)}
    if save_as and isinstance(result, dict) and result.get("ok") is True:
        _recipe_values()[save_as] = flow_evidence.sanitize(result)
        result = dict(result)
        result["saved_as"] = save_as
    if action == "explore_action" or not flow_evidence.is_active() or not isinstance(result, dict):
        return result

    screenshot_path = None
    if flow_evidence.wants_screenshot(result):
        try:
            screenshot_path = resource_store.resolve_path(
                "execution_%d.png" % time.time_ns(),
                category="screenshots",
            )
            browser_session.get_tab().get_screenshot(path=screenshot_path)
        except Exception as exc:
            logger.debug("recipe evidence screenshot failed: %s", exc)
            screenshot_path = None
    reference = flow_evidence.record_exploration(
        {"action": action, **recorded_args},
        {
            "action": {"ok": bool(result.get("ok")), "action": action,
                       "reason": result.get("reason", "")},
            "signal": {"type": "structured_result", "payload": result},
        },
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        screenshot=screenshot_path,
    )
    if isinstance(reference, dict) and reference.get("ok") is False:
        result = dict(result)
        result["ok"] = False
        result["reason"] = "evidence recording failed: %s" % reference.get("reason", "unknown error")
        result["flow_recording"] = reference
    elif reference:
        result = dict(result)
        result["flow_step"] = reference
    return result


def _http_success(status) -> bool:
    try:
        return 200 <= int(status) < 300
    except (TypeError, ValueError):
        return False


def _response_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "ok", "success", "succeeded"}:
            return True
        if normalized in {"false", "0", "no", "n", "failed", "failure", "error"}:
            return False
    return False


def _business_response_success(body) -> bool:
    if not isinstance(body, dict):
        return True
    for key in ("ok", "success"):
        if key in body:
            return _response_flag(body[key])
    if "code" in body:
        return str(body["code"]).strip().lower() in {
            "0", "200", "20000", "00000", "000000", "ok", "success", "succeeded",
        }
    if "status" in body:
        status_value = body["status"]
        if isinstance(status_value, bool):
            return status_value
        if isinstance(status_value, (int, float)):
            return status_value in {0, 1, 200, 20000}
        if isinstance(status_value, str):
            normalized = status_value.strip().lower()
            if normalized in {"true", "ok", "success", "succeeded", "0", "1", "200", "20000"}:
                return True
            return False
        return False

    return True

def _wait_query_table(frame, timeout: float = 10) -> tuple[bool, str]:
    if frame is None:
        return False, "none"
    try:
        limit = min(max(float(timeout or 0), 0.1), 120.0)
    except (TypeError, ValueError):
        return False, "none"
    deadline = time.perf_counter() + limit

    def remaining(cap: float) -> float:
        return max(0.05, min(cap, deadline - time.perf_counter()))

    if vtable.is_loading_complete(frame, remaining(limit)):
        return True, "vtable"
    try:
        if time.perf_counter() >= deadline:
            return False, "none"
        table = frame.ele("c:.ant-table-wrapper", timeout=remaining(0.5))
        if not table:
            return False, "none"
        spinner = table.ele("c:.ant-spin-spinning", timeout=remaining(0.3))
        if spinner and spinner.states.is_displayed:
            if not spinner.wait.hidden(timeout=remaining(limit), raise_err=False):
                return False, "html"
        if time.perf_counter() >= deadline:
            return False, "html"
        stable = table.wait.stop_moving(timeout=remaining(3), raise_err=False)
        return stable is not False and time.perf_counter() <= deadline, "html"
    except Exception:
        return False, "none"


def _query_filter(timeout: float = 10, listen_targets: str = "gateway") -> dict:
    """提交筛选，等待 2xx 业务响应，再被动等待 VTable 或 HTML 表格稳定。"""
    try:
        timeout = min(max(float(timeout or 0), 0.1), 120.0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "timeout 必须为正数"}
    began = time.perf_counter()
    started = observe.observe_start(
        signals=["network"], listen_targets=listen_targets,
        native_wait=_recipe_requires_native_actions(),
    )
    observe_started_at = time.perf_counter()
    if not started.get("ok"):
        return {"ok": False, "reason": "无法开始查询网络监听", "observe_start": started}
    click_result = filter_area.submit_filter_area()
    clicked_at = time.perf_counter()
    observed = observe.observe_wait(
        timeout=timeout if click_result.get("ok") else 0.1,
        include_snapshot=False, detail="summary",
        native_wait=_recipe_requires_native_actions(),
    )
    network_finished_at = time.perf_counter()
    packet = observed.get("packet") or observed.get("payload") or {}
    packet = packet if isinstance(packet, dict) else {}
    response = packet.get("response") if isinstance(packet.get("response"), dict) else {}
    status = packet.get("status", response.get("status", observed.get("status")))
    body = packet.get("body", response.get("body"))
    body = body if isinstance(body, dict) else {}
    http_ok = _http_success(status)
    business_ok = _business_response_success(body)
    loading_complete = False
    table_kind = "none"
    if (click_result.get("ok") and observed.get("type") == "network"
            and http_ok and business_ok):
        frame = browser_session.get_active_frame()
        remaining_timeout = max(0.1, timeout - (network_finished_at - observe_started_at))
        loading_complete, table_kind = _wait_query_table(frame, timeout=remaining_timeout)
    loading_finished_at = time.perf_counter()
    ok = bool(
        click_result.get("ok") and observed.get("type") == "network"
        and http_ok and business_ok and loading_complete
    )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    network_summary = {
        "type": observed.get("type"),
        "url": packet.get("url") or observed.get("url"),
        "method": packet.get("method") or observed.get("method"),
        "api_target": packet.get("api_target") or observed.get("api_target"),
        "status": status, "elapsedMs": observed.get("elapsedMs"),
        "event_count": observed.get("event_count"),
        "request": packet.get("post_data"),
        "response": {
            "ok": body.get("ok"), "status": body.get("status"),
            "message": body.get("msg") or body.get("message"),
            "total": data.get("total"),
        },
    }
    timings = {
        "observe_start_ms": round((observe_started_at - began) * 1000, 2),
        "locate_and_click_ms": round((clicked_at - observe_started_at) * 1000, 2),
        "network_wait_ms": round((network_finished_at - clicked_at) * 1000, 2),
        "table_wait_ms": round((loading_finished_at - network_finished_at) * 1000, 2),
        "vtable_wait_ms": round((loading_finished_at - network_finished_at) * 1000, 2),
        "total_ms": round((loading_finished_at - began) * 1000, 2),
    }
    result = {
        "ok": ok, "click": click_result, "network": network_summary,
        "query_completed": ok, "loading_complete": loading_complete,
        "table_kind": table_kind, "http_ok": http_ok,
        "business_ok": business_ok, "timings": timings,
    }
    if not ok:
        result["reason"] = click_result.get("reason") or (
            "查询未在 %.1fs 内获得网络响应" % timeout
            if observed.get("type") != "network"
            else "查询接口返回 HTTP %s" % status
            if not http_ok
            else "查询接口业务响应失败"
            if not business_ok
            else "查询接口返回后 VTable 未稳定完成，且未识别到稳定的 HTML 表格"
        )
    return result


def _verify_filter_query(filters: list[dict], timeout: float = 10,
                         listen_targets: str = "gateway",
                         allow_empty: bool = False, raw: bool = False) -> dict:
    """Set filter conditions, submit once, then verify every corresponding table column."""
    if not isinstance(allow_empty, bool) or not isinstance(raw, bool):
        return {"ok": False, "verified": False,
                "reason": "allow_empty 和 raw 必须是布尔值"}
    if isinstance(timeout, bool):
        return {"ok": False, "verified": False, "reason": "timeout 必须为正数"}
    try:
        timeout = min(max(float(timeout or 0), 0.1), 120.0)
    except (TypeError, ValueError):
        return {"ok": False, "verified": False, "reason": "timeout 必须为正数"}
    if isinstance(filters, list) and len(filters) > 100:
        return {"ok": False, "verified": False, "reason": "filters 最多支持 100 项"}
    began = time.perf_counter()
    if not isinstance(filters, list) or not filters:
        return {"ok": False, "verified": False, "reason": "filters 必须是非空列表"}
    for index, condition in enumerate(filters):
        if not isinstance(condition, dict):
            return {"ok": False, "verified": False,
                    "reason": "filters[%d] 必须是对象" % index}
        if "allow_empty" in condition and not isinstance(condition["allow_empty"], bool):
            return {"ok": False, "verified": False,
                    "reason": "filters[%d].allow_empty 必须是布尔值" % index}
        field = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        if not field or not operator:
            return {"ok": False, "verified": False,
                    "reason": "filters[%d] 缺少 field/operator" % index}
        if test_execution.normalize_filter_operator(operator) is None:
            return {"ok": False, "verified": False,
                    "reason": "不支持的筛选操作符: %s" % operator}
    expanded = filter_area.expand_filter_area()
    if not expanded.get("ok"):
        return {"ok": False, "verified": False,
                "reason": "筛选区展开失败: %s" % expanded.get("reason", "")}
    configured = []
    for condition in filters:
        field = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "").strip()
        setup_started = time.perf_counter()
        setup = filter_area.set_filter_condition(
            field, operator, condition.get("value"), timeout=min(timeout, 5.0),
            ensure_expanded=False,
        )
        setup = dict(setup)
        setup["elapsed_ms"] = round((time.perf_counter() - setup_started) * 1000, 2)
        configured.append(setup)
        if not setup.get("ok"):
            return {
                "ok": False, "verified": False,
                "reason": "筛选条件设置失败: %s" % setup.get("reason", field),
                "configured": configured,
            }
    configured_at = time.perf_counter()

    query = _query_filter(timeout=timeout, listen_targets=listen_targets)
    queried_at = time.perf_counter()
    if not query.get("ok"):
        return {"ok": False, "verified": False, "reason": query.get("reason", "查询失败"),
                "configured": configured, "query": query}

    comparisons = []
    for condition in filters:
        comparison_started = time.perf_counter()
        field = str(condition.get("field") or "").strip()
        column_title = str(condition.get("column_title") or field).strip()
        table_values = table_facade.get_table_values(column_title, kind="auto", raw=raw)
        if not table_values.get("ok"):
            return {
                "ok": False, "verified": False,
                "reason": "读取筛选对应列失败: %s" % column_title,
                "configured": configured, "query": query,
                "comparisons": comparisons,
                "table_values": {key: table_values.get(key) for key in ("ok", "kind", "reason")},
            }
        evaluation = test_execution.evaluate_filter_values(
            table_values.get("values"), condition.get("operator"), condition.get("value"),
            allow_empty=condition.get("allow_empty", allow_empty),
        )
        comparison = {
            "field": field, "column_title": column_title,
            "operator": condition.get("operator"), "expected": condition.get("value"),
            "evaluation": evaluation,
            "elapsed_ms": round((time.perf_counter() - comparison_started) * 1000, 2),
        }
        comparisons.append(comparison)
        if not evaluation.get("ok"):
            return {
                "ok": False, "verified": False, "reason": evaluation.get("reason"),
                "configured": configured, "query": query, "comparisons": comparisons,
            }

    finished_at = time.perf_counter()
    verified = all(item["evaluation"].get("matched") for item in comparisons)
    result = {
        "ok": verified, "verified": verified, "configured": configured,
        "query": query, "comparisons": comparisons,
        "condition_count": len(comparisons),
        "timings": {
            "configure_filters_ms": round((configured_at - began) * 1000, 2),
            "query_and_wait_ms": round((queried_at - configured_at) * 1000, 2),
            "read_and_compare_ms": round((finished_at - queried_at) * 1000, 2),
            "total_ms": round((finished_at - began) * 1000, 2),
        },
    }
    if not verified:
        failed_fields = [item["field"] for item in comparisons if not item["evaluation"].get("matched")]
        result["reason"] = "筛选结果列校验失败: %s" % ", ".join(failed_fields)
    return result


def _execution_module_text(payload: dict) -> str:
    info = payload.get("module_info") if isinstance(payload, dict) else {}
    info = info if isinstance(info, dict) else {}
    return str(info.get("menu_text") or info.get("module_level2") or "").strip()


def _browser_connection_gate() -> dict:
    """Connect without checking or refreshing a default account session.

    Role recipes establish their own BrowserContext and credentials in their setup
    commands. Refreshing the inherited/default tab here could otherwise inject the
    wrong account before the recipe activates its first role.
    """
    try:
        connection = connect()
        if not connection.get("ok"):
            return {"ok": False, "reason": "browser connection failed", "connection": connection}
        return {
            "ok": True,
            "connection": connection,
            "skipped": True,
            "reason": "role recipe owns session and module preparation",
        }
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}


def _browser_ready_gate(module_text: str) -> dict:
    """连接浏览器、确认会话与业务 iframe；模块名存在时再精确导航。"""
    try:
        connection = connect()
        if not connection.get("ok"):
            return {"ok": False, "reason": "browser connection failed",
                    "connection": connection}
        first_session = check_session()
        refresh = None
        if first_session.get("expired"):
            refresh = refresh_session()
        final_session = check_session()
        if final_session.get("expired"):
            return {"ok": False, "reason": "session remains expired after refresh",
                    "connection": connection, "session": final_session,
                    "refresh": refresh}
        frame = get_active_frame()
        entered = {"ok": True, "skipped": True,
                   "reason": "module navigation not requested" if not module_text
                             else "target module already active"}
        if module_text and (
            not frame.get("ok") or str(frame.get("tab_name") or "").strip() != module_text
        ):
            entered = enter_module(module_text, timeout=12)
            frame = get_active_frame()
        if not entered.get("ok") or not frame.get("ok"):
            reason = ("target module iframe is not ready" if module_text
                      else "module_info is absent and no active business iframe is ready")
            return {"ok": False, "reason": reason, "connection": connection,
                    "session": final_session, "entered": entered, "frame": frame,
                    "refresh": refresh}
        return {"ok": True, "connection": connection,
                "initial_session": first_session, "session": final_session,
                "entered": entered, "frame": frame, "refresh": refresh}
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}


def run_test_cases(case_file: str, filename: str = None) -> dict:
    """回放 automation_recipe；支持 role_session_* 步骤进行顺序式审批回归。"""
    payload, error = _read_json_resource(case_file)
    if error:
        return {"ok": False, "reason": error}
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "case file root must be an object"}
    cases = payload.get("test_cases", [])
    if not isinstance(cases, list) or not cases:
        return {"ok": False, "reason": "case file contains no test_cases"}
    if len(cases) > 1_000:
        return {"ok": False, "reason": "case file exceeds 1000 test cases"}
    # Refuse to turn known stale-table patterns into a green result. Keep the
    # affected cases in the execution artifact as skipped, with a repair reason.
    trusted_cases = []
    preflight_results = []
    result_slots = []
    seen_case_ids = set()
    for case in cases:
        reasons = test_execution.weak_recipe_reasons(case)
        case_id = case.get("case_id") if isinstance(case, dict) else None
        case_key = case_id.strip() if isinstance(case_id, str) else ""
        if not isinstance(case_id, str):
            reasons.append("case_id 必须是字符串")
        elif not case_key:
            reasons.append("case_id 不能为空")
        elif case_key == "__HARNESS__":
            reasons.append("case_id __HARNESS__ 为执行框架保留编号")
        elif case_key in seen_case_ids:
            reasons.append("case_id 重复: %s" % case_key)
        else:
            seen_case_ids.add(case_key)
        if reasons:
            rejected = {
                "case_id": case_id,
                "case_title": case.get("case_title", "") if isinstance(case, dict) else "",
                "status": "skipped",
                "reason": "执行配方可信度不足：" + "；".join(dict.fromkeys(reasons)),
                "failure_type": "recipe_quality", "steps": [], "evidence_refs": [],
            }
            preflight_results.append(rejected)
            result_slots.append(rejected)
        else:
            trusted_cases.append(case)
            result_slots.append(None)
    module_info = payload.get("module_info") if isinstance(payload.get("module_info"), dict) else {}
    module_text = _execution_module_text(payload)
    role_session_mode = any(
        test_execution.uses_role_session_actions(case) for case in trusted_cases
    )
    if trusted_cases and flow_evidence.is_active():
        return {"ok": False, "reason": "an evidence flow is already active; stop it before execution"}

    prior_native_actions = _recipe_requires_native_actions()
    _reset_recipe_context()
    recipe_context.set_native_actions_only(True if trusted_cases else prior_native_actions)
    ready_gate = (
        _browser_connection_gate() if role_session_mode else _browser_ready_gate(module_text)
    ) if trusted_cases else {"ok": True, "skipped": True, "reason": "all cases rejected by preflight"}
    if not ready_gate.get("ok"):
        recipe_context.set_native_actions_only(prior_native_actions)
        return {"ok": False, "reason": "browser ready gate failed: %s" % ready_gate.get("reason", ""),
                "ready_gate": ready_gate}

    started_flow = {"ok": True, "skipped": True,
                    "reason": "all cases rejected by preflight"}
    if trusted_cases:
        started_flow = flow_evidence.start(
            module_text or str(module_info.get("module_name") or "automated_execution"),
            "automated_execution_%d" % int(time.time()), capture_screenshots=True,
            scenario_type="自动化回归测试", risk_type="执行复验",
            destructive=any(bool(case.get("destructive")) for case in trusted_cases),
            cleanup_strategy="automation_recipe.cleanup + after_case overlay cleanup",
        )
        if not started_flow.get("ok"):
            recipe_context.set_native_actions_only(prior_native_actions)
            return started_flow

    def reset_case_filters(submit: bool) -> dict:
        began = time.perf_counter()
        observer_active = False

        def drain_observer() -> None:
            nonlocal observer_active
            if not observer_active:
                return
            try:
                observe.observe_wait(
                    timeout=0.1, include_snapshot=False, detail="summary", native_wait=True,
                )
            except Exception:
                pass
            observer_active = False

        try:
            started = ({"ok": True, "skipped": True} if not submit else
                       observe.observe_start(
                           signals=["network"], listen_targets="gateway", native_wait=True,
                       ))
            if not started.get("ok"):
                return {"ok": False, "reason": "无法监听筛选重置请求",
                        "observe_start": started}
            observer_active = bool(submit)
            reset = filter_area.reset_filter_area(submit=submit)
            if not reset.get("ok") or not submit:
                drain_observer()
                return {"ok": bool(reset.get("ok")), "reset": reset,
                        "query_deferred": not submit,
                        "reason": reset.get("reason", "") if not reset.get("ok") else ""}
            observed = observe.observe_wait(
                timeout=10, include_snapshot=False, detail="summary", native_wait=True,
            )
            observer_active = False
            packet = observed.get("packet") or observed.get("payload") or {}
            packet = packet if isinstance(packet, dict) else {}
            response = packet.get("response") if isinstance(packet.get("response"), dict) else {}
            status = packet.get("status", response.get("status", observed.get("status")))
            body = packet.get("body", response.get("body"))
            http_ok = _http_success(status)
            business_ok = _business_response_success(body)
            frame_object = browser_session.get_active_frame()
            remaining = max(0.1, 10 - (time.perf_counter() - began))
            loading_complete, table_kind = _wait_query_table(frame_object, remaining)
            ok = bool(observed.get("type") == "network" and http_ok
                      and business_ok and loading_complete)
            return {
                "ok": ok, "reset": reset, "network": observed,
                "http_ok": http_ok, "business_ok": business_ok,
                "loading_complete": loading_complete, "table_kind": table_kind,
                "reason": "" if ok else "重置后的业务查询未稳定完成",
            }
        except Exception as exc:
            drain_observer()
            return {"ok": False, "reason": "reset_filter_area 失败: %s" % exc}

    def reload_case_frame() -> dict:
        try:
            frame_object = browser_session.get_active_frame()
            if frame_object is None:
                return {"ok": False, "reason": "active iframe is unavailable"}
            frame_object.refresh()
            frame_object.wait.doc_loaded(timeout=10, raise_err=False)
            frame = get_active_frame()
            return {"ok": bool(frame.get("ok")), "frame": frame,
                    "reason": "" if frame.get("ok") else "active iframe reload failed"}
        except Exception as exc:
            return {"ok": False, "reason": "active iframe reload failed: %s" % exc}

    def fallback_case_reset(reset: dict) -> dict:
        if reset.get("ok"):
            return reset
        logger.warning("reset_filter_area 失败，回退到 iframe 刷新: %s", reset.get("reason", ""))
        return (_run_recipe_action("reset_to_initial", {"module_text": module_text})
                if module_text else reload_case_frame())

    def before_case(case):
        _reset_recipe_context()
        recipe_context.set_destructive_allowed(
            bool(isinstance(case, dict) and case.get("destructive") is True)
        )
        if test_execution.uses_role_session_actions(case):
            return {
                "ok": True,
                "skipped": True,
                "reason": "role recipe performs its own role/session/module preparation",
            }
        if role_session_mode:
            standard_ready = _browser_ready_gate(module_text)
            if not standard_ready.get("ok"):
                return standard_ready
        overlay_cleanup = table_facade.pre_click_cleanup(True)
        if overlay_cleanup.get("errors"):
            return {"ok": False, "reason": "; ".join(
                str(item) for item in overlay_cleanup["errors"]
            ), "cleanup": overlay_cleanup}
        recipe = case.get("automation_recipe") if isinstance(case, dict) else None
        if isinstance(recipe, list):
            setup_commands, step_commands = [], recipe
        elif isinstance(recipe, dict):
            setup_commands = recipe.get("setup") or []
            step_commands = recipe.get("steps") or []
        else:
            setup_commands, step_commands = [], []
        defer_query = bool(
            not setup_commands and step_commands and isinstance(step_commands[0], dict)
            and step_commands[0].get("action") in {"query_filter", "verify_filter_query"}
        )
        reset = fallback_case_reset(reset_case_filters(submit=not defer_query))
        if not reset.get("ok"):
            return reset
        frame = get_active_frame()
        if not frame.get("ok"):
            return frame
        return {
            "ok": True, "reset": reset, "frame": frame,
            "query_deferred": defer_query, "flow_step": reset.get("flow_step"),
        }

    def after_case(_case, _result):
        if test_execution.uses_role_session_actions(_case):
            return {
                "ok": True,
                "skipped": True,
                "reason": "role recipe owns cleanup; no default-page reset applied",
            }
        overlay_cleanup = table_facade.pre_click_cleanup(True)
        reset = fallback_case_reset(reset_case_filters(submit=True))
        errors = [str(item) for item in overlay_cleanup.get("errors", [])]
        if not reset.get("ok"):
            errors.append(str(reset.get("reason") or "页面状态重置失败"))
        response = {"ok": not errors, "cleanup": overlay_cleanup, "reset": reset,
                    "flow_step": reset.get("flow_step")}
        if errors:
            response["reason"] = "; ".join(errors)
        return response

    execution_flow = None
    harness_failures = []
    try:
        execution = test_execution.execute_cases(
            trusted_cases, _run_recipe_action, before_case=before_case, after_case=after_case,
        )
        if not isinstance(execution, dict) or not isinstance(execution.get("results"), list):
            raise TypeError("execution engine returned an invalid result")
    except Exception as exc:
        now = datetime.now().astimezone().isoformat()
        execution = {"schema_version": "1.0", "started_at": now,
                     "finished_at": now, "results": []}
        harness_failures.append("execution engine failed: %s: %s" % (type(exc).__name__, exc))
    finally:
        recipe_context.set_native_actions_only(prior_native_actions)
        _reset_recipe_context()
        if trusted_cases:
            execution_flow = flow_evidence.stop() if flow_evidence.is_active() else {
                "ok": False, "reason": "execution evidence flow ended unexpectedly",
            }
        else:
            execution_flow = {"ok": True, "skipped": True,
                              "reason": "all cases rejected by preflight"}
    if not execution_flow.get("ok"):
        harness_failures.append("execution evidence failed: %s" %
                                execution_flow.get("reason", "unknown error"))

    # Reinsert preflight rejections into their original positions. Reports and
    # external result consumers must see the same order as the source suite.
    trusted_results = list(execution.get("results", []))
    trusted_index = 0
    ordered_results = []
    for source_case, slot in zip(cases, result_slots):
        if slot is not None:
            ordered_results.append(slot)
        elif trusted_index < len(trusted_results):
            ordered_results.append(trusted_results[trusted_index])
            trusted_index += 1
        else:
            ordered_results.append({
                "case_id": source_case.get("case_id") if isinstance(source_case, dict) else None,
                "case_title": source_case.get("case_title", "") if isinstance(source_case, dict) else "",
                "status": "failed", "failure_type": "harness",
                "reason": "execution engine did not return a result for this case",
                "steps": [], "evidence_refs": [],
            })
    ordered_results.extend(trusted_results[trusted_index:])
    if harness_failures:
        ordered_results.append({
            "case_id": "__HARNESS__", "case_title": "回放执行框架",
            "status": "failed", "failure_type": "harness",
            "reason": "；".join(harness_failures), "steps": [], "evidence_refs": [],
        })
    execution["results"] = ordered_results
    execution["module_info"] = module_info
    try:
        execution["source_case_file"] = resource_store._resolve_existing_path(case_file)
    except (OSError, ValueError):
        execution["source_case_file"] = str(case_file)
    execution["ready_gate"] = ready_gate
    execution["role_mode"] = role_session_mode
    execution["evidence_flow"] = execution_flow
    if preflight_results:
        execution["recipe_quality_gate"] = {
            "trusted": len(trusted_cases), "rejected": len(preflight_results),
            "policy": "原生动作 + 网络同步 + 全量业务断言 + 可验证清理",
        }
    if payload.get("coverage_summary") is not None:
        execution["coverage_summary"] = payload["coverage_summary"]
    if payload.get("coverage_matrix") is not None:
        execution["coverage_matrix"] = payload["coverage_matrix"]
    try:
        path = _resolve_artifact_path(
            filename, os.path.join("test_results", "executions"), module_info,
            "execution_%d.json" % int(time.time()),
        )
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    sanitized_execution = flow_evidence.sanitize_artifact(execution)
    try:
        resource_store.write_json_atomic(path, sanitized_execution)
    except (OSError, TypeError, ValueError) as exc:
        return {"ok": False, "reason": "execution persistence failed: %s" % exc}
    counts = {state: sum(1 for item in execution["results"] if item.get("status") == state)
              for state in ("passed", "failed", "xfailed", "skipped")}
    return {"ok": True, "saved_to": path, "counts": counts, "execution": sanitized_execution}


# run_test_cases MCP 注册见 components.workflow（async + progress）。


def generate_test_report(execution_file: str, coverage_file: str = None,
                         baseline_file: str = None, filename: str = None,
                         defects_file: str = None,
                         supplemental_execution_files: list[str] = None) -> dict:
    """生成包含执行结果、覆盖率、缺陷、证据、性能和回归差异的 Markdown 报告。"""
    execution, error = _read_json_resource(execution_file)
    if error:
        return {"ok": False, "reason": error}
    if not isinstance(execution, dict):
        return {"ok": False, "reason": "execution file root must be an object"}
    if not isinstance(execution.get("results", []), list):
        return {"ok": False, "reason": "execution.results must be a list"}
    if any(not isinstance(item, dict) for item in execution.get("results", [])):
        return {"ok": False, "reason": "execution.results entries must be objects"}

    def coverage_input_error(value, label: str) -> str | None:
        matrix = value if isinstance(value, list) else value.get("coverage_matrix") if isinstance(value, dict) else None
        summary = value.get("coverage_summary") if isinstance(value, dict) else None
        if matrix is not None and not isinstance(matrix, list):
            return "%s.coverage_matrix must be a list" % label
        if isinstance(matrix, list) and any(not isinstance(item, dict) for item in matrix):
            return "%s.coverage_matrix entries must be objects" % label
        if summary is not None and not isinstance(summary, (dict, list)):
            return "%s.coverage_summary must be an object or list" % label
        return None

    coverage_payload, coverage_error = _read_json_resource(coverage_file) if coverage_file else ({}, None)
    if coverage_error:
        return {"ok": False, "reason": coverage_error}
    if not isinstance(coverage_payload, (dict, list)):
        return {"ok": False, "reason": "coverage file root must be an object or list"}
    coverage_shape_error = coverage_input_error(coverage_payload, "coverage")
    if coverage_shape_error:
        return {"ok": False, "reason": coverage_shape_error}
    baseline, baseline_error = _read_json_resource(baseline_file) if baseline_file else (None, None)
    if baseline_error:
        return {"ok": False, "reason": baseline_error}
    if baseline is not None and not isinstance(baseline, dict):
        return {"ok": False, "reason": "baseline file root must be an object"}
    defects_payload, defects_error = _read_json_resource(defects_file) if defects_file else ({}, None)
    if defects_error:
        return {"ok": False, "reason": defects_error}
    if not isinstance(defects_payload, (dict, list)):
        return {"ok": False, "reason": "defects file root must be an object or list"}

    supplemental_files = [] if supplemental_execution_files is None else supplemental_execution_files
    if not isinstance(supplemental_files, list) or len(supplemental_files) > 100:
        return {"ok": False, "reason": "supplemental_execution_files must contain at most 100 files"}
    current = dict(execution)
    supplemental_results = []
    supplemental_sources = []
    supplemental_bytes = 0
    module_identity_keys = ("system_name", "domain", "module_level1", "module_level2", "module_name", "menu_text")
    module_values = {key: set() for key in module_identity_keys}
    base_module_info = execution.get("module_info") if isinstance(execution.get("module_info"), dict) else {}
    for key in module_identity_keys:
        if base_module_info.get(key):
            module_values[key].add("".join(str(base_module_info[key]).split()).lower())
    for supplemental_file in supplemental_files:
        if not isinstance(supplemental_file, str) or not supplemental_file.strip():
            return {"ok": False, "reason": "supplemental execution file paths must be non-empty strings"}
        try:
            resolved_supplemental = resource_store._resolve_existing_path(supplemental_file)
            supplemental_bytes += os.path.getsize(resolved_supplemental)
        except (OSError, ValueError):
            resolved_supplemental = None
        if supplemental_bytes > 200_000_000:
            return {"ok": False, "reason": "supplemental execution files exceed 200000000 bytes"}
        supplemental, supplemental_error = _read_json_resource(supplemental_file)
        if supplemental_error:
            return {"ok": False, "reason": "%s: %s" % (supplemental_file, supplemental_error)}
        if not isinstance(supplemental, dict) or not isinstance(supplemental.get("results", []), list):
            return {"ok": False, "reason": "%s: execution root/results is invalid" % supplemental_file}
        if any(not isinstance(item, dict) for item in supplemental.get("results", [])):
            return {"ok": False, "reason": "%s: execution results must contain objects" % supplemental_file}
        supplemental_info = supplemental.get("module_info") if isinstance(supplemental.get("module_info"), dict) else {}
        for key in module_identity_keys:
            if supplemental_info.get(key):
                module_values[key].add("".join(str(supplemental_info[key]).split()).lower())
        conflicts = [key for key, values in module_values.items() if len(values) > 1]
        if conflicts:
            return {"ok": False, "reason": "supplemental execution module_info conflicts: %s" % ", ".join(conflicts)}
        supplemental_results.extend(supplemental.get("results", []))
        supplemental_sources.append(resolved_supplemental or os.path.abspath(supplemental_file))

    base_results = list(execution.get("results", []))
    if len(base_results) + len(supplemental_results) > 10_000:
        return {"ok": False, "reason": "report input exceeds 10000 execution results"}
    current["results"] = base_results + supplemental_results
    if supplemental_results:
        current["supplemental_execution_files"] = supplemental_sources

    seen_case_ids = set()
    duplicate_case_ids = []
    for result in current["results"]:
        case_id = result.get("case_id")
        if case_id is None:
            continue
        case_key = case_id.strip() if isinstance(case_id, str) else str(case_id)
        if case_key in seen_case_ids:
            duplicate_case_ids.append(case_key)
        seen_case_ids.add(case_key)
    if duplicate_case_ids:
        return {"ok": False, "reason": "duplicate case ids across executions: %s" %
                ", ".join(str(item) for item in dict.fromkeys(duplicate_case_ids))}

    if isinstance(coverage_payload, list):
        current["coverage_matrix"] = coverage_payload
    elif coverage_payload:
        if coverage_payload.get("coverage_summary") is not None:
            current["coverage_summary"] = coverage_payload["coverage_summary"]
        if coverage_payload.get("coverage_matrix") is not None:
            current["coverage_matrix"] = coverage_payload["coverage_matrix"]
    final_coverage_error = coverage_input_error(current, "execution")
    if final_coverage_error:
        return {"ok": False, "reason": final_coverage_error}
    if defects_payload:
        current["known_defects"] = (
            defects_payload if isinstance(defects_payload, list)
            else defects_payload.get("known_defects", [])
        )
        if not isinstance(current["known_defects"], list):
            return {"ok": False, "reason": "known_defects must be a list"}
        if any(not isinstance(item, dict) for item in current["known_defects"]):
            return {"ok": False, "reason": "known_defects entries must be objects"}

    regression = test_reporting.compare_regression(current, baseline)
    if baseline is not None and regression.get("ok") is not True:
        return {"ok": False, "reason": "regression comparison failed: %s" % regression.get("reason", "unknown error"),
                "regression": regression}
    module_info = execution.get("module_info") if isinstance(execution.get("module_info"), dict) else {}
    try:
        path, bundle_dir = _report_bundle_path(filename, module_info, execution_file)
        bundle = _bundle_report_assets(current, execution_file, bundle_dir)
        markdown = test_reporting.render_markdown(
            bundle["execution"], bundle["execution"], regression,
        )
        resource_store.write_text_atomic(path, markdown)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": "report generation failed: %s" % exc}
    return {"ok": True, "saved_to": path, "regression": regression,
            "has_regressions": bool(regression.get("has_regressions")),
            "coverage_summary": current.get("coverage_summary"),
            "bundle_dir": bundle_dir, "execution_copy": bundle["execution_copy"],
            "assets_dir": bundle["assets_dir"], "copied_screenshots": len(bundle["copied"]),
            "missing_screenshots": bundle["missing"]}


def compare_regression_report(execution_file: str, baseline_file: str) -> dict:
    """比较当前执行结果与历史基线，识别状态变化和超过 20% 的性能回退。"""
    execution, error = _read_json_resource(execution_file)
    if error:
        return {"ok": False, "reason": error}
    baseline, error = _read_json_resource(baseline_file)
    if error:
        return {"ok": False, "reason": error}
    return test_reporting.compare_regression(execution, baseline)

