"""Page DOM scan helpers used by MCP tools and explore flows."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time

from ..core import ui_contract
from ..resources import resource_store
from . import browser_session, interaction, observe, page_model

logger = logging.getLogger("drissionpage-mcp")




def _xpath_literal(v):
    return interaction._xpath_literal(v)


def _clickable_text_locators(raw_text: str) -> list[str]:
    return interaction._clickable_text_locators(raw_text)


def _extract_text_locator(locator: str) -> str | None:
    return interaction._extract_text_locator(locator)


def dom_tree(selector: str = "", max_depth: int = 6, max_children: int = 50,
             text: bool = False, text_limit: int = 100, show_hidden: bool = False,
             filename: str = None, save_path: str = "", save_format: str = "yml",
             max_chars: int = 8000) -> dict:
    """打印页面或元素的 DOM 树结构（结构化 JSON，便于 AI 识别）。

    Args:
        selector: CSS 选择器，为空则从 body 开始
        max_depth: 最大递归深度（默认 6）
        max_children: 每节点最多收录子节点数（默认 50），超出在 _more 标注
        text: 是否提取元素文本
        text_limit: 每节点文本最大字符数（默认 100），同时整树文本总量限制 5000 字符
        show_hidden: 是否包含 script/style/comment 等隐藏节点（默认 False）
        filename: 优先保存到指定文件名（相对于截图目录），提供时不返回大文本
        save_path: 指定文件路径则同时写入磁盘（如 "screenshots/dom-tree.yml"）
        save_format: 输出格式，"json" 或 "yml"（默认 yml，更省 token）
        max_chars: 输出字符串最大字符数（默认 8000），超出截断并标 _truncated
    """
    max_depth = min(max(int(max_depth or 0), 0), 20)
    max_children = min(max(int(max_children or 0), 0), 500)
    text_limit = min(max(int(text_limit or 0), 0), 1000)
    max_chars = min(max(int(max_chars or 0), 0), 1_000_000)
    save_format = str(save_format or "yml").lower()
    if save_format not in {"json", "yml"}:
        return {"ok": False, "reason": "save_format 必须为 json 或 yml"}
    tab = browser_session.get_tab()
    fr = browser_session.get_active_frame_ro(tab, timeout=0.5)
    target = fr if fr is not None else tab
    try:
        if selector:
            root = target.ele(f'c:{selector}', timeout=3)
            if not root:
                return {"ok": False, "reason": f"selector 未匹配: {selector}"}
        else:
            root = target

        # json.dumps 生成合法 JavaScript 字符串，避免引号/反斜杠导致选择器注入。
        find_el = (
            "var el = document.querySelector(%s);" % json.dumps(selector)
            if selector else "var el = document.body;"
        )

        # 跳过标签列表
        skip_tags = "" if show_hidden else (
            "var SKIP = {'script':1,'style':1,'link':1,'meta':1,'noscript':1,"
            "'template':1,'#comment':1};")
        text_budget = "var TEXT_LEFT = 5000;"

        js = r"""
        (function walk(el, depth, maxD, maxC, showT, txtLim) {
            if (!el || depth > maxD) return null;
            var tag = (el.tagName || '#text').toLowerCase();
            """ + ("" if show_hidden else "if (SKIP[tag]) return null;") + r"""
            var node = { tag: tag };
            if (el.id) node.id = el.id;
            if (el.className && typeof el.className === 'string') {
                var cls = el.className.trim().split(/\s+/).filter(Boolean);
                if (cls.length > 0) node.classes = cls.slice(0, 5);
            }
            var role = el.getAttribute('role');
            if (role) node.role = role;
            var name = el.getAttribute('name');
            if (name) node.name = name;
            var typ = el.getAttribute('type');
            if (typ) node.type = typ;
            var href = el.getAttribute('href');
            if (href) node.href = href.substring(0, 120);
            var src = el.getAttribute('src');
            if (src) node.src = src.substring(0, 120);
            var placeholder = el.getAttribute('placeholder');
            if (placeholder) node.placeholder = placeholder;
            if (el.disabled) node.disabled = true;
            var val = el.getAttribute('value');
            if (val && tag === 'input' && typ !== 'hidden') node.value = val.substring(0, 60);

            // 文本提取：非 script/style 的任意元素，取 textContent 前 N 字符
            if (showT && TEXT_LEFT > 0 && tag !== 'script' && tag !== 'style') {
                var take = Math.min(txtLim, TEXT_LEFT);
                var t = (el.textContent || '').trim().substring(0, take);
                if (t) { node.text = t; TEXT_LEFT -= t.length; }
            }

            if (depth < maxD && el.children && el.children.length > 0) {
                var children = [];
                for (var i = 0; i < el.children.length && children.length < maxC; i++) {
                    var child = walk(el.children[i], depth + 1, maxD, maxC, showT, txtLim);
                    if (child) children.push(child);
                }
                if (children.length > 0) node.children = children;
                if (el.children.length > maxC) node._more = el.children.length - maxC;
            }
            return node;
        })(el, 0, MAXD, MAXC, SHOWT, TXTLIM)
        """
        js = (find_el + skip_tags + text_budget + "return JSON.stringify(" +
              js.replace('MAXD', str(max_depth))
                .replace('MAXC', str(max_children))
                .replace('SHOWT', 'true' if text else 'false')
                .replace('TXTLIM', str(text_limit)) + ")")
        res = target.run_js(js)

        tree_dict = json.loads(res) if isinstance(res, str) else res
        if not isinstance(tree_dict, dict):
            return {"ok": False, "reason": "DOM tree scan returned no object"}
        result = {"ok": True, "save_format": save_format}

        # 生成文本内容
        content_str = ""
        if save_format == "yml":
            def _yaml(obj, i=0):
                p = "  " * i
                if isinstance(obj, dict):
                    r = []
                    for k, v in obj.items():
                        if isinstance(v, (dict, list)) and v:
                            r.append(f"{p}{k}:")
                            r.append(_yaml(v, i + 1))
                        elif isinstance(v, list) and not v:
                            r.append(f"{p}{k}: []")
                        else:
                            x = _yaml(v, i + 1).strip()
                            r.append(f"{p}{k}: {x}")
                    return "\n".join(r)
                elif isinstance(obj, list):
                    r = []
                    for x in obj:
                        if isinstance(x, (dict, list)):
                            r.append(f"{p}-")
                            r.append(_yaml(x, i + 1))
                        else:
                            r.append(f"{p}- {_yaml(x, 0).strip()}")
                    return "\n".join(r)
                else:
                    return json.dumps(obj, ensure_ascii=False)
            content_str = _yaml(tree_dict)
        else:
            content_str = json.dumps(tree_dict, ensure_ascii=False, indent=2)

        # filename 参数优先：直接保存到文件，不返回大文本
        if filename:
            full_path = resource_store.resolve_path(filename)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content_str)
            return {
                "ok": True,
                "saved_to": os.path.abspath(full_path),
                "save_format": save_format,
                "content_length": len(content_str),
            }

        # 无 filename 时：正常返回，带截断保护
        result["tree"] = content_str
        if len(content_str) > max_chars:
            result["tree"] = content_str[:max_chars] + (
                f"\n...(_truncated at {max_chars} chars, original {len(content_str)})")
            result["_truncated"] = True
            result["_original_chars"] = len(content_str)

        if save_path:
            resolved_save_path = resource_store.resolve_path(save_path)
            with open(resolved_save_path, "w", encoding="utf-8") as f:
                f.write(content_str)
            result["saved_to"] = os.path.abspath(resolved_save_path)

        return result
    except Exception as e:
        return {"ok": False, "reason": str(e)}

# ==================== 通用 DOM 原语 ====================

_INTERACTIVE_SELECTOR = ui_contract.INTERACTIVE_CONTROLS


def _attr(ele, name: str):
    try:
        return ele.attr(name)
    except Exception:
        return None


def _element_text(ele) -> str:
    for name in ("aria-label", "title", "placeholder", "value"):
        val = _attr(ele, name)
        if val:
            return str(val).strip().replace("\n", " ")[:40]
    try:
        text = ele.text or ""
    except Exception:
        text = ""
    return " ".join(text.split())[:40]


def _element_locator_candidates(ele) -> list[str]:
    """Build framework-neutral locators ordered from stable attributes to text."""
    tag = str(getattr(ele, "tag", "") or "*").lower()
    candidates = []
    for attr_name in ("data-testid", "data-test", "id", "name", "aria-label"):
        value = _attr(ele, attr_name)
        if value:
            candidates.append(
                "xpath://%s[@%s=%s]" % (tag, attr_name, _xpath_literal(value))
            )
    placeholder = _attr(ele, "placeholder")
    if placeholder and tag in {"input", "textarea"}:
        candidates.append(
            "xpath://%s[@placeholder=%s]" % (tag, _xpath_literal(placeholder))
        )
    text = _element_text(ele)
    if text and (
        tag in {"button", "a", "option"}
        or _attr(ele, "role") in {"button", "link", "menuitem", "tab", "option"}
    ):
        candidates.extend(_clickable_text_locators(text))
    return list(dict.fromkeys(candidates))


def _scan_controls_in_context(target, frame_label: str, start_seq: int, max_items: int):
    """Scan visible controls and return top-viewport center coordinates.

    DrissionPage's ``rect.viewport_midpoint`` already accounts for iframe
    offset, so returned ``cx/cy`` can be passed directly to ``click_xy``.
    """
    out = []
    seq = start_seq
    try:
        nodes = target.eles(f"c:{_INTERACTIVE_SELECTOR}", timeout=2)
    except Exception:
        return out, seq

    for ele in nodes:
        if len(out) >= max_items:
            break
        try:
            w, h = ele.rect.size
            if not w or not h:
                continue
            vx, vy = ele.rect.viewport_midpoint
        except Exception:
            continue

        seq += 1
        cls = _attr(ele, "class") or ""
        role = _attr(ele, "role") or ""
        typ = _attr(ele, "type") or role
        disabled = bool(_attr(ele, "disabled") or _attr(ele, "aria-disabled") == "true")
        locator_candidates = _element_locator_candidates(ele)
        item = {
            "ref": f"e{seq}",
            "frame": frame_label,
            "tag": ele.tag,
            "type": typ or "",
            "text": _element_text(ele),
            "cls": str(cls)[:50],
            "disabled": disabled,
            # Backward-compatible names, now top-viewport absolute coordinates.
            "cx": round(float(vx), 1),
            "cy": round(float(vy), 1),
            "viewportX": round(float(vx), 1),
            "viewportY": round(float(vy), 1),
            "coordinate_space": "top-viewport",
            "coord_source": "DrissionPage.Element.rect.viewport_midpoint",
        }
        if locator_candidates:
            item["locator"] = locator_candidates[0]
            item["locatorCandidates"] = locator_candidates
        out.append(item)
    return out, seq



def scan_page_elements(include_iframe: bool = True, max_items: int = 200, filename: str = None) -> dict:
    """扫描页面所有可见交互控件(button/a/input/role=*/canvas)，递归穿透同源 iframe，
    按 frame 分组返回，含可直接传给 click/input 的 locatorCandidates、顶层视口坐标和扫描 ref。
    locatorCandidates 优先 data-testid/data-test/id/name/ARIA，再回退可点击文本，适配常见组件框架。
    进入模块后第一件事。
    max_items 限制返回元素数（超出截断并标 _truncated），避免吃尽上下文。
    filename 提供时保存到文件，不返回大 JSON。"""
    tab = browser_session.get_tab()
    elements, seq = _scan_controls_in_context(tab, "", 0, max_items)

    if include_iframe and len(elements) < max_items:
        fr = browser_session.get_active_frame(tab)
        if fr is not None:
            frame_name = getattr(fr, "name", "") or getattr(fr, "id", "") or "active_iframe"
            iframe_elements, seq = _scan_controls_in_context(
                fr, frame_name, seq, max_items - len(elements)
            )
            elements.extend(iframe_elements)

    data = {
        "url": tab.url,
        "title": tab.title,
        "total": len(elements),
        "elements": elements,
        "coordinate_space": "top-viewport",
        "coord_source": "DrissionPage.Element.rect.viewport_midpoint",
    }

    if len(elements) >= max_items:
        data["_truncated"] = True
        data["returned"] = max_items

    # filename 参数优先
    if filename:
        full_path = resource_store.resolve_path(filename)
        with open(full_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {
            "ok": True,
            "saved_to": os.path.abspath(full_path),
            "element_count": len(data.get("elements", []) if isinstance(data, dict) else []),
        }

    return data


def capture_page_model(include_filters: bool = True, include_tables: bool = True,
                       include_table_data: bool = True, max_table_rows: int = 80,
                       max_elements: int = 120, filename: str = None) -> dict:
    """聚合采集当前页面模型：URL/frame、工具栏动作、字段、弹窗/抽屉、分页、表格结构和可选表格数据。

    这是测试用例设计的高信息密度入口。`include_filters=True` 会展开筛选区并读取下拉选项；
    `filename` 提供时保存大 JSON 到截图目录而不直接返回。
    """
    return page_model.capture_page_model(
        include_filters=include_filters,
        include_tables=include_tables,
        include_table_data=include_table_data,
        max_table_rows=max_table_rows,
        max_elements=max_elements,
        filename=filename,
    )


def scan_toolbar_actions(scope: str = "page", in_frame: bool = True, max_items: int = 120) -> dict:
    """扫描页面可见动作按钮/链接，返回文本、禁用态、下拉提示、区域归属和矩形位置。

    scope: page=页面主动作，toolbar=尽量聚焦工具栏，all=包含弹窗/筛选/分页等区域。
    """
    return page_model.scan_toolbar_actions(scope=scope, in_frame=in_frame, max_items=max_items)


def scan_form_fields(scope: str = "page", include_hidden: bool = False,
                     in_frame: bool = True, max_fields: int = 200) -> dict:
    """扫描通用表单字段。scope: page/filter/modal/drawer/layer/all 或自定义 CSS。

    layer 会自动进入 layer.js 嵌套 iframe 表单（账号新增/编辑等）。
    """
    return page_model.scan_form_fields(scope=scope, include_hidden=include_hidden,
                                       in_frame=in_frame, max_fields=max_fields)







def scan_floats(only_visible: bool = True, include_table_data: bool = True) -> dict:
    """扫描所有可见浮窗（modal/drawer/popover/tooltip/dropdown/calendar/message/notification/VTable 浮层）。
    单次 JS 注入完成。返回浮窗内所有操作按钮的位置（可点击关闭）、
    关闭按钮的 CSS 定位符（可用于 click 工具）、日历面板摘要、内部表格结构和可选的全量行数据。
    """
    return page_model.scan_floats(only_visible=only_visible,
                                  include_table_data=include_table_data)


def scan_modal(max_items: int = 20) -> dict:
    """扫描当前可见 Ant Design 弹窗，返回标题、正文摘要、字段、按钮和表格数量。"""
    return page_model.scan_modal(max_items=max_items)


def scan_drawer(max_items: int = 20) -> dict:
    """扫描当前可见 Ant Design 抽屉，返回标题、正文摘要、字段、按钮和表格数量。"""
    return page_model.scan_drawer(max_items=max_items)


def scan_pagination(in_frame: bool = True) -> dict:
    """扫描页面分页器，返回当前页、页大小、总数文本、上一页/下一页可用状态。"""
    return page_model.scan_pagination(in_frame=in_frame)


def select_option(field_name: str, option_text: str, select_index: int = 0,
                  scope: str = "auto", timeout: float = 5.0) -> dict:
    """按字段名选择下拉项（内部/配方；MCP 注册见 components.filter）。"""
    return page_model.select_option(field_name=field_name, option_text=option_text,
                                    select_index=select_index, scope=scope, timeout=timeout)


def get_all_table_data(kind: str = "auto", table_index: int = 0, max_pages: int = 1,
                       max_rows: int = 1000, max_columns: int = 50,
                       raw: bool = False, filename: str = None) -> dict:
    return page_model.get_all_table_data(kind=kind, table_index=table_index,
                                         max_pages=max_pages, max_rows=max_rows,
                                         max_columns=max_columns, raw=raw, filename=filename)


def dom_overview(max_buttons: int = 100) -> dict:
    """页面俯瞰：顶部页签 + 可见按钮文本（含 disabled）。"""
    tab = browser_session.get_tab()
    script = browser_session.load_js("element-scan.js") + "\nreturn JSON.stringify(domOverview());"
    res = tab.run_js(script)
    data = json.loads(res) if isinstance(res, str) else res
    if isinstance(data, dict):
        btns = data.get("buttons", [])
        if len(btns) > max_buttons:
            data["buttons"] = btns[:max_buttons]
            data["_truncated"] = True
    return data


