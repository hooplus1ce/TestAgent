# 工具增强行动计划（实用版）

基于对 Playwright MCP 和 DrissionPage 的深入分析，这是真正可行的高价值功能列表。

## 一、已完成的 P0 功能 ✅

### 1. 标签页管理工具 - `browser_tabs()`
### 2. 滚动操作工具 - `browser_scroll()`
### 3. PDF 导出工具 - `browser_save_pdf()`
### 4. 按键操作工具 - `browser_press_key()`
### 5. 元素状态查询工具 - `browser_get_element_state()`

---

## 二、真正可行的下一步（P1 - 经核实）

### 6. Cookie 操作工具 ⭐⭐⭐⭐
**可行性：** ✅ 高，DrissionPage 有完整 API
**相关 DrissionPage API：**
- `tab.cookies()` - 获取所有 cookies
- `tab.set.cookies()` - 设置 cookie
- `tab.cookies_to_session()` - 同步到 session
- `tab.cookies_to_browser()` - 同步到 browser
- `tab.get_cookies()` - 另一种获取方式

### 7. 控制台消息工具 ⭐⭐⭐
**可行性：** ✅ 高，DrissionPage 有 Console 单元
**相关 DrissionPage API：**
- `tab.console` - Console 对象
- 可以获取控制台消息

### 8. 元素等待工具 ⭐⭐⭐⭐
**可行性：** ✅ 高，DrissionPage 有强大的 Waiter
**相关 DrissionPage API：**
- `tab.wait.ele_displayed(locator)`
- `tab.wait.ele_hidden(locator)`
- `tab.wait.ele_enabled(locator)`
- `tab.wait.ele_deleted(locator)`
- `tab.wait.ele_clickable(locator)`
- `tab.wait.url_change(old_url)`
- `tab.wait.title_change(old_title)`

### 9. 元素属性工具 ⭐⭐⭐
**可行性：** ✅ 高，DrissionPage 有完整 API
**相关 DrissionPage API：**
- `ele.attr(attr_name)` - 获取属性
- `ele.attrs` - 获取所有属性
- `ele.text` - 获取文本
- `ele.html` - 获取 HTML
- `ele.value` - 获取值
- `ele.rect` - 获取位置大小

### 10. 页面保存工具 ⭐⭐⭐
**可行性：** ✅ 高，DrissionPage 已有
**相关 DrissionPage API：**
- `tab.save(path, name)` - 保存为 HTML

---

## 三、经核实不可行或不推荐的功能 ❌

### ❌ 屏幕录制工具（Screencast）
**原因：**
- 需要 `cv2` 和 `numpy` 额外依赖
- 主要是截图序列模拟视频，不是真正的 CDP 录制
- MCP 场景很少需要屏幕录制
- 已有 `screenshot()` 工具

### ❌ 网络路由拦截
**原因：**
- 需要深入研究 Listener 的内部实现
- 复杂性高，价值不明确

### ❌ 测试断言工具
**原因：**
- 现有工具已足够组合使用
- 专门的断言工具价值不大

---

## 四、推荐的下一步实现

让我们先实现最实用的：**Cookie 操作工具** 和 **元素等待工具**

参见：`mcp-servers/drission-ui/server.py` 中的已有工具
