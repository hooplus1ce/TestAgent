"""端到端验证：直接调用模块函数对 9222 浏览器跑只读链路。
不做任何点击/输入，仅验证连接、frame 解析、VTable 挂载与扫描（最高风险）。
"""
import sys, os, json, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import browser_session as B
import vtable, session_auth, modal


def step(name, fn):
    try:
        r = fn()
        print("✓ %s -> %s" % (name, json.dumps(r, ensure_ascii=False)[:300]))
        return r
    except Exception as e:
        print("✗ %s -> %s" % (name, e))
        traceback.print_exc()
        return None


print("==== 1. connect ====")
tab = B.connect(9222, "诺贝科技")
print("  url=%s title=%s" % (tab.url, tab.title))

print("\n==== 2. get_active_frame ====")
fr = B.get_active_frame(tab)
print("  active_frame=%s url=%s" % (fr, getattr(fr, "url", "") if fr else ""))

print("\n==== 3. scan_page_elements (top, 递归穿透 iframe) ====")
script = B.load_js("element-scan.js") + "\nreturn JSON.stringify(scanInteractiveControls());"
res = tab.run_js(script)
data = json.loads(res) if isinstance(res, str) else res
print("  total=%s groups=%s" % (data.get("total"), list({e.get("frame") or "(main)" for e in data.get("elements", [])})))

print("\n==== 4. check_session ====")
print("  ", session_auth.check_session())

print("\n==== 5. detect_modal ====")
print("  ", modal.detect_modal())

print("\n==== 6. VTable 链路 (最高风险) ====")
if fr is not None:
    mv = step("mount_vtable", lambda: vtable.mount_vtable())
    if mv and mv.get("ok"):
        step("scan_vtable_columns(max_col=8)", lambda: vtable.scan_vtable_columns(8))
        step("get_column_values('制令单号')", lambda: vtable.get_column_values("制令单号"))
    else:
        print("  mount 失败，当前活动 iframe 可能非 VTable 页面（跳过扫描）")
else:
    print("  无活动 iframe，跳过")

print("\n==== 7. 4.2 新增: mouse_trail (原生 tab.set.show_trail) ====")
try:
    tab.set.show_trail()
    print("  show_trail() 调用成功")
except Exception as e:
    print("  show_trail() 失败(可能版本不支持): %s" % e)
    modal.mouse_trail(True)

print("\n==== 8. 4.2 新增: listen 链式 API ====")
try:
    # 4.2：set_method 是 property，返回 MethodSetter 对象，用属性名链式调用
    # tab.listen.set_method.GET(only=True) 只监听 GET；旧写法 set_method("GET") 会抛 TypeError
    tab.listen.set_method.GET(only=True)
    tab.listen.start("hoolinks")
    print("  listen.set_method.GET(only=True) + start 调用成功")
    tab.listen.stop()
except Exception as e:
    print("  listen 新 API 失败: %s" % e)

print("\n==== 9. 4.2 新增: BrowserContext ====")
try:
    browser = B.get_browser()
    print("  get_browser() 成功, browser=%s" % type(browser).__name__)
except Exception as e:
    print("  get_browser() 失败: %s" % e)

print("\n==== 10. 4.2 新增: ChromiumOptions ====")
try:
    import config
    co = config.make_chromium_options()
    print("  make_chromium_options() 成功, type=%s" % type(co).__name__)
except Exception as e:
    print("  make_chromium_options() 失败: %s" % e)

print("\n==== 11. e2e 写路径 (HL_E2E=1 启用，会点击/导航，非只读) ====")
if os.environ.get("HL_E2E", "").lower() in ("1", "true", "yes"):
    import server
    # 覆盖曾因缺 import time 而 NameError 的路径：enter_module / reset_to_initial / screenshot(无path)
    mod = os.environ.get("HL_E2E_MODULE", "")
    if mod:
        step("enter_module(%r)" % mod, lambda: server.enter_module(mod, expand_filter=False))
        step("screenshot(无path，触发 time.time 默认路径)", lambda: server.screenshot())
        step("reset_to_initial(%r)" % mod, lambda: server.reset_to_initial(mod))
    else:
        print("  未设置 HL_E2E_MODULE（菜单文字），跳过 enter_module 链路")
    # click_cell 落点截图核对（README 标的最高优先级）——验证 _wait_cell_center_stable 坐标路径
    if fr is not None:
        mv = step("mount_vtable(e2e)", lambda: vtable.mount_vtable())
        if mv and mv.get("ok"):
            cols = step("scan_vtable_columns(8, e2e)", lambda: vtable.scan_vtable_columns(8))
            if cols and cols.get("columns"):
                c0 = cols["columns"][0]
                step("click_cell落点", lambda: server.click_cell(c0["col"], c0.get("row", 0)))
                step("screenshot(落点核对)", lambda: server.screenshot())
else:
    print("  跳过（设置 HL_E2E=1 启用；HL_E2E_MODULE 指定菜单文字）")

print("\n==== 验证完成 ====")
