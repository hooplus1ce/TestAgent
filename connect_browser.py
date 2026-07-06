#!/usr/bin/env python3
"""连接到 9222 端口的 Chrome 浏览器"""
import sys
from pathlib import Path

# 添加 mcp-servers/drission-ui 到路径
sys.path.insert(0, str(Path(__file__).parent / "mcp-servers" / "drission-ui"))

from DrissionPage import Chromium, ChromiumOptions

# 配置文件路径
DP_INI = str(Path(__file__).parent / "configs" / "dp_configs.ini")

print("正在连接到 127.0.0.1:9222 ...")

try:
    co = ChromiumOptions(read_file=True, ini_path=DP_INI)
    co.set_address('127.0.0.1:9222')

    # 尝试连接
    browser = Chromium(co)

    print("\n✓ 连接成功！")
    print(f"  浏览器版本: {browser.version}")

    # 列出所有标签页
    print("\n📋 所有标签页:")
    for i, tid in enumerate(browser.tab_ids, 1):
        try:
            tab = browser.get_tab(tid)
            url = (tab.url or "")[:80]
            title = (tab.title or "")[:50]
            print(f"  [{i}] {title}")
            print(f"      {url}")
        except Exception as e:
            print(f"  [{i}] <无法读取: {e}>")

    # 获取当前标签页
    tab = browser.latest_tab
    print(f"\n📍 当前标签页:")
    print(f"  标题: {tab.title}")
    print(f"  URL: {tab.url}")

except Exception as e:
    print(f"\n✗ 连接失败: {e}")
    print("\n请确认:")
    print("  1. Chrome 已启动并开启远程调试端口 9222")
    print("  2. 启动参数包含: --remote-debugging-port=9222")
    sys.exit(1)
