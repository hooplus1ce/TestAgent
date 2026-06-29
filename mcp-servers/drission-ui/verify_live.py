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

print("\n==== 3. frame_offset ====")
if fr is not None:
    print("  offset=%s" % (B.frame_offset(tab),))

print("\n==== 4. scan_page_elements (top, 递归穿透 iframe) ====")
script = B.load_js("element-scan.js") + "\nreturn JSON.stringify(scanInteractiveControls());"
res = tab.run_js(script)
data = json.loads(res) if isinstance(res, str) else res
print("  total=%s groups=%s" % (data.get("total"), list({e.get("frame") or "(main)" for e in data.get("elements", [])})))

print("\n==== 5. check_session ====")
print("  ", session_auth.check_session())

print("\n==== 6. detect_modal ====")
print("  ", modal.detect_modal())

print("\n==== 7. VTable 链路 (最高风险) ====")
if fr is not None:
    mv = step("mount_vtable", lambda: vtable.mount_vtable())
    if mv and mv.get("ok"):
        step("scan_vtable_columns(max_col=8)", lambda: vtable.scan_vtable_columns(8))
        step("get_column_values('制令单号')", lambda: vtable.get_column_values("制令单号"))
    else:
        print("  mount 失败，当前活动 iframe 可能非 VTable 页面（跳过扫描）")
else:
    print("  无活动 iframe，跳过")

print("\n==== 验证完成 ====")
