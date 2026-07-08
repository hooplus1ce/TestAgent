#!/usr/bin/env python3
"""scan_floats_native — DrissionPage 原生 API 版浮窗检测（无 JS 注入）。

依赖: DrissionPage>=4.2 (项目 .venv)
对比基准: page_model.scan_floats() (JS 注入版)
"""

import re
import time
from DrissionPage import Chromium


# 浮窗类型 → CSS 选择器
FLOAT_SELECTORS = [
    ".ant-modal", ".ant-drawer", ".ant-popover", ".ant-tooltip",
    ".ant-dropdown", ".ant-message-notice", ".ant-notification-notice",
]
FLOAT_CSS = ", ".join(FLOAT_SELECTORS)

# 按钮关键词检测
BUTTON_PATTERNS = re.compile(
    r"(取\s*消|确\s*定|关\s*闭|保\s*存|提\s*交|返\s*回|重\s*置|导\s*出|"
    r"物料查询|Close|OK|Cancel|Save|Submit)"
)


def _visible(el):
    """通过 rect 判断元素是否可见"""
    try:
        rect = el.rect
        sz = rect.size
        return sz[0] > 0 and sz[1] > 0
    except Exception:
        return False


def _text(el):
    return (el.text or "").strip()


def _attr(el, name):
    try:
        return el.attr(name) or ""
    except Exception:
        return ""


def _rect_dict(el):
    try:
        r = el.rect
        loc, sz = r.location, r.size
        return {"x": loc[0], "y": loc[1], "width": sz[0], "height": sz[1]}
    except Exception:
        return {"x": 0, "y": 0, "width": 0, "height": 0}


def _css_hint(el):
    """简化版 duCssHint"""
    tag = el.tag.lower()
    if el.attr("id"):
        return f"{tag}#{el.attr('id')}"
    for name in ("data-row-key", "data-testid", "name", "data-key"):
        v = el.attr(name)
        if v:
            return f"{tag}[{name}='{v}']"
    cls = _attr(el, "class")
    if cls:
        return f"{tag}.{cls.split()[0]}"
    if el.attr("type"):
        return f'{tag}[type="{el.attr("type")}"]'
    return tag


def scan_floats(only_visible=True, include_table_data=True):
    """DrissionPage 原生 API 版 scan_floats。

    逻辑与 JS 注入版 page_model.scan_floats() 一致，但全部用
    DrissionPage 的 eles() / attr() / rect 等原生 API 实现。
    """
    t0 = time.time()

    browser = Chromium(addr_or_opts=9222)
    tab = browser.latest_tab

    info = {
        "ok": True, "count": 0, "floats": [],
        "_elapsed": 0, "_engine": "drissionpage-native",
    }

    # --- 切入 iframe ---
    target = tab
    for f in tab.eles("tag:iframe"):
        src = _attr(f, "src")
        if src and "workbench" not in src:
            try:
                target = tab.get_frame(f)
            except Exception:
                continue

    # --- 查找所有浮窗容器 ---
    floats_raw = target.eles(FLOAT_CSS)
    all_wrappers = target.eles(".ant-table-wrapper")
    all_wrapper_list = list(all_wrappers)

    for el in floats_raw:
        if only_visible and not _visible(el):
            continue
        text = _text(el)
        if only_visible and not text:
            continue

        # --- 类型推断 ---
        cls = _attr(el, "class")
        kind = "unknown"
        if "ant-modal" in cls: kind = "modal"
        elif "ant-drawer" in cls: kind = "drawer"
        elif "ant-popover" in cls: kind = "popover"
        elif "ant-tooltip" in cls: kind = "tooltip"
        elif "ant-dropdown" in cls: kind = "dropdown"
        elif "ant-message-notice" in cls: kind = "message"
        elif "ant-notification-notice" in cls: kind = "notification"

        # --- 标题 ---
        title = ""
        title_el = el.eles("tag:.ant-modal-title,.ant-drawer-title,.ant-modal-header")
        if title_el:
            title = _text(title_el[0])
        if not title:
            parts = [l.strip() for l in text.split("\n") if l.strip()]
            for p in parts[:10]:
                if re.search(r'[\u4e00-\u9fff\w]{2,}', p) and len(p) < 60:
                    title = p
                    break
            if not title and parts:
                title = parts[0][:60]

        # --- 按钮 (duScanButtons 近似) ---
        buttons = []
        btn_nodes = el.eles("tag:button,.ant-btn,a,.ant-dropdown-trigger")
        seen_texts = set()
        for bn in btn_nodes:
            if not _visible(bn): continue
            bt = _text(bn)
            if not bt: continue
            if bt in seen_texts: continue
            seen_texts.add(bt)
            buttons.append({
                "text": bt,
                "tag": bn.tag.lower(),
                "rect": _rect_dict(bn),
                "selectorHint": _css_hint(bn),
            })

        # --- 额外可点击元素 (a:not([href]) 等) ---
        extra_nodes = el.eles("tag:a,span")
        for en in extra_nodes:
            if not _visible(en): continue
            if en.tag == "a" and _attr(en, "href"):
                continue  # 已在 buttons 中
            # 跳过已被 buttons 捕获的祖先
            if en.closest("button,.ant-btn,a[href],.ant-dropdown-trigger"):
                continue
            # 判断可点击: 通过获取 style
            is_clickable = False
            if _attr(en, "onclick"):
                is_clickable = True
            elif _attr(en, "tabindex"):
                is_clickable = True
            if not is_clickable:
                # DrissionPage 没有 getComputedStyle，跳过 cursor 检测
                pass
            if not is_clickable:
                continue
            et = _text(en)
            if not et or len(et) > 40:
                continue
            if et in seen_texts:
                continue
            seen_texts.add(et)
            buttons.append({
                "text": et,
                "tag": en.tag.lower(),
                "rect": _rect_dict(en),
                "selectorHint": _css_hint(en),
                "extra": True,
            })

        # --- 关闭按钮 ---
        close_btn = el.eles("tag:.ant-modal-close,.ant-drawer-close")
        close_button = None
        if close_btn:
            cb = close_btn[0]
            close_button = {
                "selectorHint": _css_hint(cb),
                "rect": _rect_dict(cb),
            }

        # --- 表格检测 ---
        tables = []
        table_wrappers = el.eles("tag:.ant-table-wrapper")
        for tw in table_wrappers:
            # 全局索引
            try:
                global_idx = all_wrapper_list.index(tw)
            except ValueError:
                global_idx = -1

            # 列头
            headers = []
            header_tr = tw.eles("tag:.ant-table-thead tr")
            if header_tr:
                ths = header_tr[0].eles("tag:th")
                headers = [_text(th) for th in ths if _text(th)]

            # 行数
            body_rows = tw.eles("tag:.ant-table-tbody tr.ant-table-row")
            if not body_rows:
                body_rows = tw.eles("tag:.ant-table-tbody tr")
            row_count = len(body_rows)

            # VTable
            has_vtable = len(el.eles("tag:canvas.vtable")) > 0

            # 行数据
            row_data = []
            if include_table_data:
                body_table = tw.eles("tag:.ant-table-body table,.ant-table-content table")
                if body_table:
                    trs = body_table[0].eles("tag:tbody > tr.ant-table-row,tbody > tr")
                    for tr in trs:
                        cells = tr.eles("tag:td")
                        row = [_text(c) for c in cells]
                        if any(row):
                            row_data.append(row)

            tables.append({
                "index": global_idx,
                "kind": "vtable" if has_vtable else "html",
                "headers": headers,
                "rowCount": row_count,
                "data": row_data,
            })

        info["floats"].append({
            "title": title,
            "type": kind,
            "rect": _rect_dict(el),
            "buttons": buttons,
            "closeButton": close_button,
            "hasClose": close_button is not None,
            "tableCount": len(table_wrappers),
            "tables": tables,
        })

    info["count"] = len(info["floats"])
    info["_elapsed"] = round(time.time() - t0, 3)
    return info


if __name__ == "__main__":
    r = scan_floats(only_visible=True, include_table_data=True)
    print(f"引擎: {r['_engine']}")
    print(f"耗时: {r['_elapsed']}s")
    print(f"浮窗: {r['count']} 个\n")
    for f in r['floats']:
        print(f"  [{f['type']}] {f['title']}")
        print(f"  关闭: {f['closeButton']['selectorHint'] if f.get('closeButton') else '✗'}")
        print(f"  按钮: {[b['text'] for b in f['buttons'][:6]]}")
        extras = [b for b in f['buttons'] if b.get('extra')]
        if extras: print(f"  ⚡链接: {[b['text'] for b in extras]}")
        for t in f['tables']:
            print(f"  表格 @#{t['index']}  {t['rowCount']}行  {len(t['headers'])}列")
