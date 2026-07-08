"""get_iframe_floats_v2 — DrissionPage 原生 API 版浮窗检测。

直接使用 DrissionPage 连接浏览器，不经 MCP 子调用。
适合作为 MCP 服务固定工具使用（0.2s 内完成）。

用法:
    from tools.iframe_floats_v2 import get_iframe_floats
    result = get_iframe_floats()
"""
import re, time
from DrissionPage import Chromium

BUTTON_PATTERNS = re.compile(
    r"(取\s*消|确\s*定|关\s*闭|保\s*存|提\s*交|返\s*回|重\s*置|导\s*出|"
    r"物料查询|Close|OK|Cancel|Save|Submit)"
)
FLOAT_TYPES = [
    "ant-modal-wrap", "ant-drawer", "ant-popover",
    "ant-tooltip", "ant-dropdown", "ant-message", "ant-notification",
]
FLOAT_XPATH = f"//div[{' or '.join(f'contains(@class, \"{t}\")' for t in FLOAT_TYPES)}]"


def get_iframe_floats(only_visible=True, browser=None):
    """检测当前页面中所有可见浮窗。

    Args:
        only_visible: 过滤无 rect / 无文本的 DOM 残留
        browser: 已有 Chromium 实例（MCP 工具中传入），None 则新建

    Returns:
        dict: { total, has_iframe, url, floats: [{ title, rect, buttons,
               has_close_button, has_table, full_text_preview }], elapsed }
    """
    BUTTON_PATTERNS = re.compile(
    r"(取\s*消|确\s*定|关\s*闭|保\s*存|提\s*交|返\s*回|重\s*置|导\s*出|"
    r"物料查询|Close|OK|Cancel|Save|Submit)"
    )
    FLOAT_TYPES = [
        "ant-modal-wrap", "ant-drawer", "ant-popover",
        "ant-tooltip", "ant-dropdown", "ant-message", "ant-notification",
    ]
    FLOAT_XPATH = f"//div[{' or '.join(f'contains(@class, \"{t}\")' for t in FLOAT_TYPES)}]"

    t0 = time.time()
    info = {"total": 0, "has_iframe": False, "url": "", "floats": [], "elapsed": 0}

    own_browser = browser is None
    if own_browser:
        try:
            browser = Chromium(addr_or_opts=9222)
        except Exception as e:
            info["error"] = f"连接浏览器失败: {e}"
            return info

    tab = browser.latest_tab
    info["url"] = tab.url

    # 切入内容 iframe
    target = tab
    for f in tab.eles("tag:iframe"):
        src = f.attr("src") or ""
        if src and "workbench" not in src:
            try:
                target = tab.get_frame(f)
                info["has_iframe"] = True
                info["url"] = src
                break
            except Exception:
                continue

    # 查找浮窗容器
    for el in target.eles(f"xpath:{FLOAT_XPATH}"):
        try:
            rect = el.rect
            sz, loc = rect.size, rect.location
            if only_visible and (sz[0] <= 0 or sz[1] <= 0):
                continue
        except Exception:
            continue

        text = el.text or ""
        if only_visible and not text.strip():
            continue

        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title = next((l for l in lines if len(l) >= 2), "未命名浮窗")
        buttons = list(dict.fromkeys(BUTTON_PATTERNS.findall(text)))

        try:
            close_btns = el.eles("xpath:.//*[contains(@class, 'ant-modal-close')]")
        except Exception:
            close_btns = []
        try:
            tables = el.eles("xpath:.//*[contains(@class, 'ant-table')]")
        except Exception:
            tables = []

        info["floats"].append({
            "title": title,
            "rect": {"x": loc[0], "y": loc[1], "w": sz[0], "h": sz[1]},
            "buttons": buttons,
            "has_close_button": len(close_btns) > 0,
            "has_table": len(tables) > 0,
            "full_text_preview": text[:300],
        })

    info["total"] = len(info["floats"])
    info["elapsed"] = round(time.time() - t0, 3)

    if own_browser:
        browser.quit()
    return info


if __name__ == "__main__":
    r = get_iframe_floats()
    print(f"耗时: {r['elapsed']}s  |  浮窗: {r['total']}个  |  iframe={'✓' if r['has_iframe'] else '✗'}\n")
    for f in r["floats"]:
        print(f"  [{f['title'][:45].strip():45s}]  关闭={'✓' if f['has_close_button'] else '✗'}  表格={'✓' if f['has_table'] else '✗'}")
