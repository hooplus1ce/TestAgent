#!/usr/bin/env python3
"""获取页面详细信息"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "mcp-servers" / "drission-ui"))

from DrissionPage import Chromium, ChromiumOptions

DP_INI = str(Path(__file__).parent / "configs" / "dp_configs.ini")

print("正在连接浏览器...")
co = ChromiumOptions(read_file=True, ini_path=DP_INI)
co.set_address('127.0.0.1:9222')
browser = Chromium(co)
tab = browser.latest_tab

print("\n" + "="*60)
print(f"  页面标题: {tab.title}")
print(f"  页面 URL: {tab.url}")
print("="*60)

# 获取页面可见文本 - 使用 run_js
print("\n📄 页面可见文本 (前500字符):")
page_text = tab.run_js("return document.body.innerText || '';")
if page_text:
    print(page_text[:500])
    if len(page_text) > 500:
        print("... (已截断)")

# 查找常见元素
print("\n🔍 查找常见交互元素:")

buttons = tab.eles('tag:button')
print(f"  按钮数量: {len(buttons)}")
if buttons:
    for i, btn in enumerate(buttons[:8], 1):
        try:
            text = btn.attr('innerText') or btn.attr('textContent') or ""
            text = text.strip()
            if text:
                print(f"    [{i}] {text[:50]}")
        except:
            pass

links = tab.eles('tag:a')
print(f"\n  链接数量: {len(links)}")
if links:
    for i, link in enumerate(links[:5], 1):
        try:
            text = link.attr('innerText') or ""
            text = text.strip()
            href = link.attr('href') or ""
            if text or href:
                print(f"    [{i}] {text[:30] or '-'} -> {href[:60]}")
        except:
            pass

inputs = tab.eles('tag:input')
print(f"\n  输入框数量: {len(inputs)}")

# 检查是否有 iframe
iframes = tab.eles('tag:iframe')
print(f"\n  iframe 数量: {len(iframes)}")
if iframes:
    for i, iframe in enumerate(iframes, 1):
        try:
            src = iframe.attr('src') or ""
            name = iframe.attr('name') or ""
            print(f"    [{i}] name={name} src={src[:80]}")
        except:
            pass

# 运行 JS 获取更多信息
print("\n⚡ JavaScript 页面信息:")
js_info = tab.run_js("""
return JSON.stringify({
    title: document.title,
    url: location.href,
    userAgent: navigator.userAgent,
    width: window.innerWidth,
    height: window.innerHeight,
    readyState: document.readyState
});
""")
if js_info:
    info = json.loads(js_info)
    print(f"  视口: {info['width']} x {info['height']}")
    print(f"  状态: {info['readyState']}")

print("\n✓ 完成!")
