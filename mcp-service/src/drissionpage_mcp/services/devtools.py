"""Browser debug, downloads, tabs, console, and element-state helpers."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time

from ..core import tool_metadata
from ..resources import resource_store
from . import browser_session, interaction, modal

logger = logging.getLogger("drissionpage-mcp")


def run_js(script: str, in_frame: bool = True, max_chars: int = 4000) -> dict:
    """执行显式调试脚本，并按序列化后的真实体积限制返回。"""
    max_chars = min(max(int(max_chars or 0), 0), 1_000_000)
    tab = browser_session.get_tab()
    target = browser_session.get_active_frame_ro(tab, timeout=0.5) if in_frame else None
    target = target or tab
    try:
        result = target.run_js(str(script or ""))
        try:
            serialized = json.dumps(result, ensure_ascii=False)
            output = result
        except (TypeError, ValueError):
            output = str(result)
            serialized = output
        if len(serialized) > max_chars:
            return {
                "ok": True,
                "result": serialized[:max_chars],
                "_truncated": True,
                "_original_chars": len(serialized),
            }
        return {"ok": True, "result": output}
    except Exception as exc:
        return {"ok": False, "reason": "run_js failed: %s" % exc}


def mouse_trail(on: bool = True) -> dict:
    """开启/关闭鼠标轨迹可视化(红色圆点跟踪 mousemove/click)。调试 canvas 点击落点用。"""
    return modal.mouse_trail(on)


# ==================== 4.2 新增工具 ====================

def click_to_download(locator: str, save_path: str = None, rename: str = None,
                      suffix: str = None, new_tab: bool = False,
                      by_js: bool = False, in_frame: bool = True,
                      timeout: float = 30) -> dict:
    """点击元素触发浏览器下载（无需预知 URL），等待完成并返回文件路径。

    内部调用 DrissionPage 的 click.to_download()，自动拦截浏览器下载任务。
    适合下载模板、导出文件等场景——只需提供触发下载的按钮/链接定位符。
    """
    if not isinstance(locator, str) or not locator.strip():
        return {"ok": False, "reason": "locator 必须是非空字符串"}
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout)) or timeout < 0 or timeout > 3600
    ):
        return {"ok": False, "reason": "timeout 必须是 0 到 3600 的有限数值"}

    element = browser_session.find(
        locator, in_frame=in_frame, timeout=max(min(timeout, 10), 1.0)
    )
    if not element:
        return {"ok": False, "reason": "元素未找到: %s（超时 %.1fs）" % (locator, timeout)}
    try:
        kwargs = {"timeout": float(timeout)}
        if save_path:
            kwargs["save_path"] = os.fspath(save_path)
        if rename:
            kwargs["rename"] = str(rename)
        if suffix:
            kwargs["suffix"] = str(suffix)
        if new_tab:
            kwargs["new_tab"] = True
        if by_js:
            kwargs["by_js"] = True
        mission = element.click.to_download(**kwargs)
        completed_path = mission.wait(show=False, timeout=float(timeout))
        raw_path = completed_path or getattr(mission, "final_path", "") or ""
        path_value = os.path.abspath(os.fspath(raw_path)) if raw_path else ""
        ok = bool(completed_path) and os.path.isfile(path_value)
        result = {
            "ok": ok,
            "path": path_value,
            "file_size": getattr(mission, "total_bytes", None),
            "state": str(getattr(mission, "state", "") or ""),
            "name": str(getattr(mission, "name", "") or ""),
        }
        if not ok:
            result["reason"] = "下载未完成或目标文件不存在"
        return result
    except Exception as exc:
        return {"ok": False, "reason": "单击下载失败: %s" % exc}


def click_to_upload(locator: str, file_paths: str, by_js: bool = False,
                    in_frame: bool = True, timeout: float = 10) -> dict:
    """点击元素触发文件上传，自动拦截文件选择框并填入路径。

    内部调用 DrissionPage 的 click.to_upload()，模拟真实用户操作：
    点击按钮 → 拦截系统文件对话框 → 自动填入文件路径。
    适合 Ant Design Upload 等复杂上传组件的自动化。
    多文件路径用 \\n 分隔。
    """
    if not isinstance(locator, str) or not locator.strip():
        return {"ok": False, "reason": "locator 必须是非空字符串"}
    if not file_paths or not isinstance(file_paths, str):
        return {"ok": False, "reason": "file_paths 必须是非空字符串（多文件用 \\n 分隔）"}

    element = browser_session.find(
        locator, in_frame=in_frame, timeout=max(min(timeout, 8), 1.0)
    )
    if not element:
        return {"ok": False, "reason": "元素未找到: %s（超时 %.1fs）" % (locator, timeout)}
    try:
        element.click.to_upload(file_paths, by_js=by_js)
        file_list = [p.strip() for p in file_paths.split("\n") if p.strip()]
        return {
            "ok": True,
            "locator": locator,
            "file_count": len(file_list),
            "file_paths": file_list,
        }
    except Exception as exc:
        return {"ok": False, "reason": "文件上传失败: %s" % exc}


def download_by_browser(url: str, save_path: str = None, rename: str = None,
                        suffix: str = None, timeout: float = 30,
                        file_exists: str = "rename") -> dict:
    """触发浏览器下载，等待完成并返回可序列化的绝对文件路径。"""
    if not isinstance(url, str) or not url.strip():
        return {"ok": False, "reason": "url 必须是非空字符串"}
    if len(url) > 100_000:
        return {"ok": False, "reason": "url 超过 100000 字符"}
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout)) or timeout < 0 or timeout > 3600
    ):
        return {"ok": False, "reason": "timeout 必须是 0 到 3600 的有限数值"}
    if file_exists not in {"rename", "overwrite", "skip", "r", "o", "s"}:
        return {"ok": False, "reason": "file_exists 必须是 rename/overwrite/skip 或 r/o/s"}

    tab = browser_session.get_tab()
    download = getattr(tab, "download", None)
    by_browser = getattr(download, "by_browser", None) if download is not None else None
    if not callable(by_browser):
        return {
            "ok": False,
            "reason": "当前 DrissionPage 版本未提供 tab.download.by_browser；可改用 click_to_download 工具",
        }
    kwargs = {"url": url, "timeout": float(timeout), "file_exists": file_exists}
    if save_path:
        kwargs["save_path"] = os.fspath(save_path)
    if rename:
        kwargs["rename"] = str(rename)
    if suffix:
        kwargs["suffix"] = str(suffix)
    try:
        mission = by_browser(**kwargs)
        # show=False：wait() 默认 print 进度到 stdout，会污染 MCP 协议帧。
        completed_path = mission.wait(show=False)
        raw_path = completed_path or getattr(mission, "final_path", "") or ""
        path_value = os.path.abspath(os.fspath(raw_path)) if raw_path else ""
        ok = bool(completed_path) and os.path.isfile(path_value)
        result = {
            "ok": ok,
            "path": path_value,
            "file_size": getattr(mission, "total_bytes", None),
            "url": url,
            "state": str(getattr(mission, "state", "") or ""),
            "name": str(getattr(mission, "name", "") or ""),
        }
        if not ok:
            result["reason"] = "下载未完成或目标文件不存在"
        return result
    except Exception as exc:
        return {"ok": False, "reason": "下载失败: %s" % exc}


def browser_list_caps() -> dict:
    """列出当前启用的能力分组和可用的工具分组。

    使用能力分组减少 LLM 上下文 token 消耗。

    使用方式：
        export DRISSIONPAGE_MCP_CAPS=core,vtable,filter  # 启用指定分组
        export DRISSIONPAGE_MCP_CAPS=all                 # 启用所有分组
    """
    return {
        "ok": True,
        "profile": tool_metadata.ENABLED_PROFILE,
        "enabled_caps": sorted(tool_metadata.ENABLED_CAPS),
        "available_caps": {
            cap: tools for cap, tools in tool_metadata.CAP_GROUPS.items()
        },
        "env_hint": "The default full profile exposes every grouped tool. Use DRISSIONPAGE_MCP_PROFILE=enterprise only for explicit context reduction; DRISSIONPAGE_MCP_CAPS further narrows groups.",
    }


# ==================== 新增：滚动操作工具 ====================

def browser_scroll(direction: str = "down", pixel: int = 300, locator: str = None,
                   x: int = None, y: int = None, timeout: float = 5) -> dict:
    """滚动活动 iframe；``see`` 按 iframe → 顶层顺序定位并保留真实作用域。"""
    directions = {"top", "bottom", "half", "up", "down", "left", "right", "see", "location"}
    if direction not in directions:
        return {"ok": False, "reason": "Invalid direction: %s" % direction}
    if direction in {"up", "down", "left", "right"} and (
        isinstance(pixel, bool) or not isinstance(pixel, int) or pixel < 0
    ):
        return {"ok": False, "reason": "pixel 必须是非负整数"}
    if direction == "see" and not str(locator or "").strip():
        return {"ok": False, "reason": "see 方向必须提供 locator"}
    if direction == "location" and (
        isinstance(x, bool) or isinstance(y, bool)
        or not isinstance(x, (int, float)) or not isinstance(y, (int, float))
        or not math.isfinite(float(x)) or not math.isfinite(float(y))
    ):
        return {"ok": False, "reason": "location 方向必须提供有限 x/y"}
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout)) or timeout < 0
    ):
        return {"ok": False, "reason": "timeout 必须是非负有限数值"}

    timeout = min(float(timeout), 120.0)
    tab = browser_session.get_tab()
    target = browser_session.get_active_frame(tab) or tab
    try:
        if direction == "top":
            target.scroll.to_top()
        elif direction == "bottom":
            target.scroll.to_bottom()
        elif direction == "half":
            target.scroll.to_half()
        elif direction == "up":
            target.scroll.up(pixel)
        elif direction == "down":
            target.scroll.down(pixel)
        elif direction == "left":
            target.scroll.left(pixel)
        elif direction == "right":
            target.scroll.right(pixel)
        elif direction == "see":
            deadline = time.monotonic() + timeout
            try:
                element = target.ele(locator, timeout=max(timeout * 0.8, 0.0))
            except Exception:
                element = None
            if not element and target is not tab:
                target = tab
                element = tab.ele(locator, timeout=max(deadline - time.monotonic(), 0.0))
            if not element:
                return {"ok": False, "reason": "Element not found: %s" % locator}
            target.scroll.to_see(element)
        else:
            target.scroll.to_location(float(x), float(y))
        return {
            "ok": True,
            "direction": direction,
            "scope": "iframe" if target is not tab else "top",
            "pixel": pixel if direction in {"up", "down", "left", "right"} else None,
        }
    except Exception as exc:
        logger.error("Scroll error: %s", exc)
        return {"ok": False, "reason": str(exc)}


# ==================== 新增：标签页管理工具 ====================

def browser_tabs(action: str = "list", index: int = None, url: str = None) -> dict:
    """用 DrissionPage 管理零基索引标签页，并保持关闭后的业务目标。"""
    if action not in {"list", "new", "close", "select"}:
        return {"ok": False, "reason": "Invalid action: %s" % action}
    if action in {"close", "select"} and (
        isinstance(index, bool) or not isinstance(index, int)
    ):
        return {"ok": False, "reason": "index 必须是整数"}
    if action == "new" and url is not None and not isinstance(url, str):
        return {"ok": False, "reason": "url 必须是字符串"}

    browser = browser_session.get_browser()
    try:
        if action == "list":
            return {
                "ok": True,
                "tabs": [dict(item, index=i) for i, item in enumerate(browser_session.list_tabs())],
            }

        if action == "new":
            new_tab = browser.new_tab(url=url)
            browser_session.set_tab(new_tab)
            return {"ok": True, "url": new_tab.url, "tab_id": new_tab.tab_id}

        tab_ids = list(browser.tab_ids)
        if not 0 <= index < len(tab_ids):
            return {"ok": False, "reason": "Invalid index: %s, total: %d" % (index, len(tab_ids))}
        tab_id = tab_ids[index]
        if action == "select":
            selected = browser.get_tab(tab_id)
            browser.activate_tab(selected)
            browser_session.set_tab(selected)
            return {
                "ok": True,
                "tab_id": selected.tab_id,
                "url": selected.url,
                "title": selected.title,
            }

        current_id = getattr(browser_session.get_tab(), "tab_id", None)
        browser.close_tabs(tab_id)
        replacement = None
        if tab_id == current_id:
            try:
                replacement = browser_session._pick_tab(browser, browser_session._target_hint)
            except Exception:
                replacement = None
            if replacement is None:
                replacement = browser.new_tab()
            browser_session.set_tab(replacement)
        return {
            "ok": True,
            "closed_tab_id": tab_id,
            "active_tab_id": getattr(replacement, "tab_id", current_id),
        }
    except Exception as exc:
        logger.error("Browser tabs error: %s", exc)
        return {"ok": False, "reason": str(exc)}


# ==================== 新增：PDF 导出工具 ====================

def browser_save_pdf(path: str = None, filename: str = None) -> dict:
    """将当前页面保存为 PDF，并验证 DrissionPage 确实生成了文件。"""
    try:
        tab = browser_session.get_tab()
        raw_name = os.path.basename(str(filename or "page_%d.pdf" % int(time.time())))
        pdf_filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name).strip(" .")
        if not pdf_filename:
            return {"ok": False, "reason": "filename 必须是有效文件名"}
        if not pdf_filename.lower().endswith(".pdf"):
            pdf_filename += ".pdf"

        if path:
            save_dir = os.path.abspath(str(path))
            os.makedirs(save_dir, exist_ok=True)
        else:
            save_path = resource_store.resolve_path(pdf_filename, category="pdf")
            save_dir = os.path.dirname(save_path)
            pdf_filename = os.path.basename(save_path)

        returned = tab.save(path=save_dir, name=pdf_filename, as_pdf=True)
        expected = os.path.abspath(os.path.join(save_dir, pdf_filename))
        candidates = []
        if isinstance(returned, (str, os.PathLike)):
            returned_path = os.fspath(returned)
            if not os.path.isabs(returned_path):
                returned_path = os.path.join(save_dir, returned_path)
            candidates.append(os.path.abspath(returned_path))
        candidates.append(expected)
        candidate = next((item for item in candidates if os.path.isfile(item)), None)
        if candidate is None and isinstance(returned, (bytes, bytearray)):
            with open(expected, "wb") as output:
                output.write(returned)
            candidate = expected
        if candidate is None:
            return {"ok": False, "reason": "PDF 未生成文件", "path": expected}
        return {
            "ok": True,
            "path": candidate,
            "dir": os.path.dirname(candidate),
            "filename": os.path.basename(candidate),
            "size": os.path.getsize(candidate),
        }
    except Exception as exc:
        logger.error("Save PDF error: %s", exc)
        return {"ok": False, "reason": str(exc)}


def _console_arg_text(arg):
    if not isinstance(arg, dict):
        return str(arg)
    if "value" in arg:
        return str(arg.get("value"))
    if arg.get("description"):
        return str(arg.get("description"))
    if arg.get("unserializableValue"):
        return str(arg.get("unserializableValue"))
    return json.dumps(arg, ensure_ascii=False)


def _console_message_to_dict(message) -> dict:
    data = getattr(message, "data", None) or {}
    args = data.get("args") or []
    text = data.get("text") or ""
    if not text and args:
        text = " ".join(_console_arg_text(a) for a in args)
    stack = data.get("stackTrace") or {}
    call_frames = stack.get("callFrames") or []
    first_frame = call_frames[0] if call_frames else {}
    return {
        "level": data.get("level") or data.get("type") or "",
        "type": data.get("type") or data.get("source") or "",
        "text": str(text)[:2000],
        "url": data.get("url") or first_frame.get("url", ""),
        "line": data.get("lineNumber", first_frame.get("lineNumber")),
        "column": first_frame.get("columnNumber"),
        "timestamp": data.get("timestamp"),
        "arg_count": len(args),
    }


def browser_console_messages(level: str = "", timeout: float = 0.0, start: bool = True,
                             clear: bool = False, stop: bool = False,
                             max_messages: int = 50, filename: str = None) -> dict:
    """读取并按级别筛选 DrissionPage 控制台队列；过滤先于数量上限。"""
    tab = browser_session.get_tab()
    console = None
    try:
        console = tab.console
        if (start or timeout > 0) and not getattr(console, "listening", False):
            console.start()
        if clear:
            console.clear()

        limit = max(int(max_messages or 0), 0)
        wanted = {
            item.strip().lower() for item in str(level or "").split(",") if item.strip()
        }
        items = []

        def append_if_wanted(message):
            item = _console_message_to_dict(message)
            if wanted and not (
                (item.get("level") or "").lower() in wanted
                or (item.get("type") or "").lower() in wanted
            ):
                return
            if len(items) < limit:
                items.append(item)

        timeout = max(float(timeout or 0), 0.0)
        deadline = time.monotonic() + timeout
        while limit and len(items) < limit and time.monotonic() < deadline:
            remaining = min(0.5, max(deadline - time.monotonic(), 0.0))
            message = console.wait(timeout=remaining)
            if message:
                append_if_wanted(message)

        if limit and len(items) < limit:
            for message in console.messages:
                append_if_wanted(message)
                if len(items) >= limit:
                    break

        result = {"ok": True, "count": len(items), "messages": items}
        if filename:
            full_path = resource_store.resolve_path(filename)
            with open(full_path, "w", encoding="utf-8") as file:
                json.dump(result, file, ensure_ascii=False, indent=2)
            return {"ok": True, "saved_to": os.path.abspath(full_path), "count": len(items)}
        return result
    except Exception as exc:
        logger.error("Console messages error: %s", exc)
        return {"ok": False, "reason": str(exc)}
    finally:
        if stop and console is not None:
            try:
                console.stop()
            except Exception:
                logger.debug("停止控制台监听失败", exc_info=True)


# ==================== 新增：按键操作工具 ====================

def browser_press_key(key: str, modifiers: list[str] = None, interval: float = 0.01) -> dict:
    """在活动业务 iframe 发送官方 Keys 动作，并校验组合键参数。"""
    if not isinstance(key, str) or not key:
        return {"ok": False, "reason": "key 必须是非空字符串"}
    if modifiers is None:
        modifiers = []
    if not isinstance(modifiers, list) or any(not isinstance(item, str) for item in modifiers):
        return {"ok": False, "reason": "modifiers 必须是字符串列表"}
    allowed_modifiers = {"alt", "control", "ctrl", "meta", "command", "shift"}
    normalized_modifiers = [re.sub(r"[\s_-]+", "", item).lower() for item in modifiers]
    if any(item not in allowed_modifiers for item in normalized_modifiers):
        return {"ok": False, "reason": "modifiers 仅支持 Ctrl/Alt/Shift/Meta/Command"}
    if (
        isinstance(interval, bool) or not isinstance(interval, (int, float))
        or not math.isfinite(float(interval)) or interval < 0 or interval > 10
    ):
        return {"ok": False, "reason": "interval 必须是 0 到 10 的有限数值"}

    tab = browser_session.get_tab()
    target = browser_session.get_active_frame(tab) or tab
    try:
        result = interaction._press_key_raw(target, key, modifiers=modifiers, interval=float(interval))
        result["scope"] = "iframe" if target is not tab else "top"
        return result
    except Exception as exc:
        logger.error("Press key error: %s", exc)
        return {"ok": False, "reason": str(exc)}


# ==================== 新增：元素状态查询工具 ====================

def browser_get_element_state(locator: str, state: str = None) -> dict:
    """读取 DrissionPage 元素状态；派生 hidden/disabled 并按需求值。"""
    ele = browser_session.find(locator, wait_clickable=False)
    if not ele:
        return {"ok": False, "reason": "Element not found: %s" % locator}

    try:
        element_states = ele.states
        getters = {
            "displayed": lambda: bool(element_states.is_displayed),
            "hidden": lambda: not bool(element_states.is_displayed),
            "enabled": lambda: bool(element_states.is_enabled),
            "disabled": lambda: not bool(element_states.is_enabled),
            "selected": lambda: bool(element_states.is_selected),
            "checked": lambda: bool(element_states.is_checked),
            "clickable": lambda: bool(element_states.is_clickable),
            "covered": lambda: bool(element_states.is_covered),
            "alive": lambda: bool(element_states.is_alive),
            "in_viewport": lambda: bool(element_states.is_in_viewport),
            "whole_in_viewport": lambda: bool(element_states.is_whole_in_viewport),
            "has_rect": lambda: bool(element_states.has_rect),
        }
        if state:
            getter = getters.get(state)
            if getter is None:
                return {
                    "ok": False,
                    "reason": "Invalid state: %s" % state,
                    "available_states": list(getters),
                }
            return {"ok": True, "locator": locator, "state": state, "value": getter()}
        return {
            "ok": True,
            "locator": locator,
            "states": {name: getter() for name, getter in getters.items()},
        }
    except Exception as exc:
        logger.error("Get element state error: %s", exc)
        return {"ok": False, "reason": str(exc)}


