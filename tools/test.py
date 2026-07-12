#!/usr/bin/env python3
"""get_iframe_floats — DrissionPage 原生 API 版浮窗检测（本地运行用）。

用法:
    python tools/get_iframe_floats.py
    # 或
    from tools.get_iframe_floats import get_iframe_floats
    result = get_iframe_floats()

    # 指定已有的 frame:
    from drissionpage_mcp.services.browser_session import get_active_frame
    fr = get_active_frame()
    result = get_iframe_floats(iframe_active=fr)

注意:
    - DrissionPage eles() 不支持逗号分隔的多值 CSS 选择器，
      故使用 XPath 替代。
    - 所有 eles() 使用 timeout=0 避免 10s 默认等待。
"""
import re
import time
from DrissionPage.items import ChromiumElement
from rich import print

_FLOAT_XPATH = (
    "//*[contains(concat(' ', @class, ' '), ' ant-modal ')"
    " or contains(concat(' ', @class, ' '), ' ant-drawer ')"
    " or contains(concat(' ', @class, ' '), ' ant-popover ')"
    " or contains(concat(' ', @class, ' '), ' ant-tooltip ')"
    " or contains(concat(' ', @class, ' '), ' ant-dropdown ')"
    " or contains(concat(' ', @class, ' '), ' ant-message-notice ')"
    " or contains(concat(' ', @class, ' '), ' ant-notification-notice ')]"
)
_TITLE_CSS = ".ant-modal-title, .ant-drawer-title, .ant-modal-header"
_CLOSE_CSS = ".ant-modal-close, .ant-drawer-close"
_TABLE_CSS = ".ant-table-wrapper"

# 活跃 iframe 定位符: 可见 tabpanel 内的 iframe
_ACTIVE_IFRAME_CSS = 'c:[role="tabpanel"][aria-hidden="false"] iframe'


def _txt(el, default=""):
    try:
        return (el.text or "").strip()
    except Exception:
        return default


def _attr(el, name):
    try:
        return el.attr(name) or ""
    except Exception:
        return ""


def _has_rect(el):
    try:
        s = el.rect.size
        return s[0] > 0 and s[1] > 0
    except Exception:
        return False


def _rect_dict(el: ChromiumElement, bt = None):
    try:
        if bt:
            print(f"midpoint-返回元素中间点的绝对坐标({bt}): {el.rect.midpoint}")
            print(f"click_point-返回元素接受点击的点的绝对坐标({bt}): {el.rect.click_point}")
            print(f"screen_click_point-返回元素中点在屏幕上坐标，左上角为(0, 0)({bt}): {el.rect.screen_click_point}")
            print(f"screen_location-返回元素左上角在屏幕上坐标，左上角为(0, 0)({bt}): {el.rect.screen_location}")
            print(f"viewport_click_point-返回元素接受点击的点视口坐标({bt}): {el.rect.viewport_click_point}")
            print(f"viewport_midpoint-返回元素中间点在视口中的坐标({bt}): {el.rect.viewport_midpoint}")
        loc, sz = el.rect.viewport_midpoint, el.rect.size
        return {"x": round(loc[0], 1), "y": round(loc[1], 1),
                "width": round(sz[0], 1), "height": round(sz[1], 1)}
    except Exception:
        return {"x": 0, "y": 0, "width": 0, "height": 0}


def _kind(el):
    cls = _attr(el, "class")
    if "ant-modal" in cls:          return "modal"
    if "ant-drawer" in cls:         return "drawer"
    if "ant-popover" in cls:        return "popover"
    if "ant-tooltip" in cls:        return "tooltip"
    if "ant-dropdown" in cls:       return "dropdown"
    if "ant-message-notice" in cls: return "message"
    if "ant-notification-notice" in cls: return "notification"
    return "unknown"


def _find_title(el):
    for t in el.eles(_TITLE_CSS, timeout=0):
        v = _txt(t)
        if v:
            return v
    text = _txt(el)
    if not text:
        return ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for p in lines[:10]:
        if re.search(r"[\u4e00-\u9fff\w]{2,}", p) and len(p) < 60:
            return p
    return lines[0][:60] if lines else ""


def get_iframe_floats(only_visible=True, iframe_active=None):
    """检测当前页面中的可见浮窗。

    Args:
        only_visible:   过滤无尺寸 / 空文本元素
        iframe_active:  ChromiumFrame/ChromiumTab 对象，None 则自动检测

    Returns:
        dict: {ok, count, _elapsed, floats: [{title, type, rect,
               buttons, closeButton, hasClose, tableCount, tables}]}
    """
    from DrissionPage import Chromium

    t0 = time.time()
    info = {"ok": True, "count": 0, "_elapsed": 0, "floats": []}

    try:
        browser = Chromium(addr_or_opts=9222)
    except Exception as e:
        info["ok"] = False
        info["error"] = f"连接失败: {e}"
        return info

    tab = browser.latest_tab

    # --- 确定目标 context ---
    if iframe_active is not None:
        target = iframe_active
    else:
        target = tab
        try:
            fr = tab.get_frame(_ACTIVE_IFRAME_CSS, timeout=2)
            if fr is not None:
                target = fr
        except Exception:
            pass

    # --- 查找浮窗 ---
    for el in target.eles(_FLOAT_XPATH, timeout=0):
        if only_visible and not _has_rect(el):
            continue
        text = _txt(el)
        if only_visible and not text:
            continue

        title = _find_title(el)
        kind = _kind(el)

        # 关闭按钮 (XPath: eles 不支持逗号 CSS)
        close_btn = None
        has_close = False
        try:
            for cb in el.eles("xpath:.//*[contains(@class, 'ant-modal-close') or contains(@class, 'ant-drawer-close')]", timeout=0):
                has_close = True
                tag = cb.tag.lower()
                cid = _attr(cb, "id")
                if cid:
                    hint = f"{tag}#{cid}"
                else:
                    cls = _attr(cb, "class")
                    hint = f"{tag}.{cls.split()[0]}" if cls else tag
                close_btn = {"selectorHint": hint, "rect": _rect_dict(cb)}
                break
        except Exception:
            pass

        # 操作按钮 (单值 CSS 逐个查询再合并)
        buttons = []
        seen = set()
        try:
            for css in ('button', '.ant-btn', 'a', '.ant-dropdown-trigger'):
                for bn in el.eles(css, timeout=0):
                    if only_visible and not _has_rect(bn):
                        continue
                    bt = _txt(bn)
                    if not bt or bt in seen:
                        continue
                    seen.add(bt)
                    buttons.append({
                        "text": bt,
                        "tag": bn.tag.lower(),
                        "rect": _rect_dict(bn, bt),
                        "disabled": _attr(bn, "disabled") == "true",
                    })
        except Exception:
            pass

        # 表格 (单值 CSS)
        tables = []
        table_count = 0
        try:
            for tw in el.eles('.ant-table-wrapper', timeout=0):
                table_count += 1
                headers = []
                try:
                    for htr in tw.eles('.ant-table-thead tr', timeout=0):
                        headers = [_txt(th) for th in htr.eles('th', timeout=0) if _txt(th)]
                        if headers:
                            break
                except Exception:
                    pass
                rows = []
                try:
                    rows = tw.eles('.ant-table-tbody tr', timeout=0)
                except Exception:
                    pass
                has_vtable = False
                try:
                    has_vtable = len(el.eles('canvas.vtable', timeout=0)) > 0
                except Exception:
                    pass
                tables.append({
                    "kind": "vtable" if has_vtable else "html",
                    "headers": headers,
                    "rowCount": len(rows),
                })
        except Exception:
            pass

        info["floats"].append({
            "title": title,
            "type": kind,
            "rect": _rect_dict(el),
            "buttons": buttons,
            "closeButton": close_btn,
            "hasClose": has_close,
            "tableCount": table_count,
            "tables": tables,
        })

    info["count"] = len(info["floats"])
    info["_elapsed"] = round(time.time() - t0, 3)
    return info


def main():
    r = get_iframe_floats(only_visible=True)
    print(f"引擎: DrissionPage 原生 API")
    print(f"耗时: {r.get('_elapsed', 'N/A')}s")
    print(f"浮窗: {r['count']} 个\n")
    for f in r.get("floats", []):
        print(f"  [{f['type']}] {f['title']}")
        print(f"      位置: ({f['rect']['x']:.0f},{f['rect']['y']:.0f})  {f['rect']['width']:.0f}x{f['rect']['height']:.0f}")
        print(f"      关闭: {f['closeButton']['selectorHint'] if f.get('closeButton') else '✗'}")
        btns = [b["text"] for b in f["buttons"][:8]]
        print(f"      按钮: {btns}")
        for t in f["tables"]:
            print(f"      表格 ({t['kind']})  {t['rowCount']}行  {t['headers'][:5]}")
        print()


if __name__ == "__main__":
    main()
