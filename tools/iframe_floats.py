#!/usr/bin/env python3
"""
iframe_floats.py — 独立脚本，通过 DrissionPage 原生 API 检测 iframe 内浮窗。

用法:
    /home/hoolinks/TestAgent/.venv/bin/python iframe_floats.py

依赖:
    DrissionPage>=4.2 (项目 .venv 中已安装)
"""

import sys
import json
from pathlib import Path

# 确保使用项目 venv 的 DrissionPage
VENV_PY = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"


def main():
    # ---------- 导入 ----------
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from DrissionPage import Chromium

    # ---------- 连接已有 Chrome ----------
    browser = Chromium(addr_or_opts=9222)

    # ---------- 获取当前活动 tab ----------
    tab = browser.latest_tab

    print(f"=== 页面信息 ===")
    print(f"URL:   {tab.url}")
    print(f"Title: {tab.title}")

    # ---------- 检测 iframe ----------
    iframes = tab.eles("tag:iframe")
    active_frame = None
    frame_url = None

    if iframes:
        print(f"\n=== iframe ({len(iframes)} 个) ===")
        for i, f in enumerate(iframes):
            src = f.attr("src") or ""
            print(f"  [{i}] src={src[:100]}")
            # 选中内容区域的 iframe（通常是唯一有内容的）
            if src and not active_frame:
                try:
                    tab.set.frame(f)
                    active_frame = f
                    frame_url = src
                    print(f"       → 已切入")
                except Exception as e:
                    print(f"       → 切入失败: {e}")

    target = tab if not active_frame else tab

    # ---------- 查找浮窗容器 ----------
    float_classes = [
        "ant-modal-wrap", "ant-drawer", "ant-popover",
        "ant-tooltip", "ant-dropdown", "ant-message",
        "ant-notification",
    ]

    # 构建 XPath
    xpath_parts = [f"contains(@class, '{c}')" for c in float_classes]
    xpath = f"//div[{' or '.join(xpath_parts)}]"

    wraps = target.eles(f"xpath:{xpath}")
    print(f"\n=== 浮窗容器: {len(wraps)} 个 ===")

    results = []
    for i, wrap in enumerate(wraps):
        try:
            rect = wrap.rect
            children = wrap.eles("xpath:.//*")
            texts_set = set()
            buttons = []
            title_text = ""
            table_text = ""

            for el in children:
                t = (el.text or "").strip()
                if not t:
                    continue
                texts_set.add(t)

                tag = el.tag
                cls = " ".join(el.attr("@class") or [])

                if not title_text and "ant-modal-title" in cls:
                    title_text = t
                if tag in ("button", "a") and "ant-modal-close" not in cls:
                    buttons.append(t)
                if "ant-table" in cls:
                    table_text = t

            if not title_text and texts_set:
                sorted_texts = sorted(texts_set, key=len, reverse=True)
                for s in sorted_texts:
                    if len(s.strip()) >= 4 and not s.strip().replace(" ", "").isdigit():
                        title_text = s
                        break

            full_text = max(texts_set, key=len) if texts_set else ""
            has_close = any(
                "ant-modal-close" in " ".join(el.attr("@class") or [])
                for el in children
            )

            info = {
                "index": i,
                "title": title_text or f"浮窗 #{i+1}",
                "type": "ant-modal",
                "element_count": len(children),
                "buttons": list(dict.fromkeys(buttons)),
                "has_table": bool(table_text),
                "has_close_button": has_close,
                "full_text_preview": full_text[:300] if full_text else "",
            }
            results.append(info)

            print(f"\n  [{i}] {info['title']}")
            print(f"       元素数: {info['element_count']}")
            print(f"       按钮: {info['buttons']}")
            print(f"       表格: {'✓' if info['has_table'] else '✗'}")
            print(f"       关闭: {'✓' if info['has_close_button'] else '✗'}")
            if info['full_text_preview']:
                print(f"       文本预览: {info['full_text_preview'][:120]}...")
        except Exception as e:
            print(f"\n  [{i}] 提取失败: {e}")

    print(f"\n=== 总计: {len(results)} 个浮窗 ===")

    # ---------- 恢复默认 frame ----------
    if active_frame:
        try:
            tab.set.frame(None)
        except Exception:
            pass

    # 输出 JSON 供其他工具消费
    output = {"total": len(results), "floats": results}
    print(f"\n--- JSON ---")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
