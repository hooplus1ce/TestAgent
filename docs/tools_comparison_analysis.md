# DrissionPage vs Playwright MCP 工具对比分析报告

## 一、当前项目已有的工具清单

### 1.1 连接与会话（core caps）
- `connect()` - 连接浏览器
- `refresh_session()` - 刷新会话（含 OCR 登录）
- `check_session()` - 检查会话是否过期
- `browser_list_caps()` - 列出启用的能力分组（新增）

### 1.2 导航与 Frame（core caps）
- `enter_module(menu_text)` - 点击菜单进入模块
- `get_active_frame()` - 获取活动的 iframe
- `expand_filter_area()` - 展开筛选区

### 1.3 页面理解（core caps）
- `scan_page_elements()` - 扫描页面交互元素
- `dom_tree()` - 获取 DOM 树结构
- `find_elements(locator)` - 查找多个元素
- `find_batch(locators)` - 批量查找多个定位符
- `find_static(locator)` - 查找静态元素（不交互）
- `get_frame(locator)` - 获取 iframe
- `dom_overview()` - 页面俯瞰（顶部页签+按钮文本）

### 1.4 通用交互（core caps）
- `click(locator)` - 点击元素
- `click_xy(x, y)` - 按坐标点击
- `input(locator, text)` - 在输入框输入文本
- `insert_text(text)` - 在当前焦点插入文本
- `hover(locator)` - 悬停元素
- `close_modal()` - 关闭弹窗

### 1.5 表格操作（vtable caps）
- `scan_table()` - 统一扫描表格（VTable/HTML）
- `get_table_values(column_title)` - 获取列值
- `get_table_data()` - 获取完整表格数据
- `click_table_cell(row, col)` - 点击表格单元格
- `hover_table_cell(row, col)` - 悬停表格单元格
- `resize_table_column(width)` - 调整表格列宽

### 1.6 筛选区操作（filter caps）
- `scan_filter_fields()` - 扫描筛选区字段
- `select_date_range(field_name, start_date, end_date)` - 选择日期范围

### 1.7 观察器（observe caps）
- `observe_start()` - 启动观察器（点击前）
- `observe_wait()` - 等待观察器结果（点击后）

### 1.8 网络监听（network caps）
- `listen_start(targets)` - 启动网络监听
- `listen_wait(count)` - 等待网络数据包
- `listen_stop()` - 停止网络监听
- `listen_ws_start(targets)` - 启动 WebSocket 监听
- `listen_ws_wait(count)` - 等待 WebSocket 数据包

### 1.9 存储/上下文（storage caps）
- `new_context(proxy)` - 创建新的浏览器上下文
- `switch_context(context_id)` - 切换上下文
- `list_contexts()` - 列出所有上下文
- `set_permission(perm, allow)` - 设置浏览器权限

### 1.10 高级/调试（devtools caps）
- `screenshot(path, locator)` - 截图（全页/元素）
- `run_js(script)` - 执行任意 JavaScript
- `mouse_trail(on)` - 鼠标轨迹可视化
- `download_by_browser(url)` - 浏览器触发下载

---

## 二、Playwright MCP 有但我们缺失的工具

### 2.1 标签管理（Tab Management）
**Playwright MCP 功能：**
- `browser_tabs('list')` - 列出所有标签页
- `browser_tabs('new', url)` - 新建标签页并导航
- `browser_tabs('close', index)` - 关闭指定标签页
- `browser_tabs('select', index)` - 切换到指定标签页

**我们当前的状态：**
- 没有专门的标签页管理工具
- `browser_session.list_tabs()` 内部可用，但未暴露为 MCP 工具
- `connect()` 时可获取所有标签信息，但无法操作

**可以借鉴添加：**
```python
@mcp.tool()
@write_synchronized
def browser_tabs(action='list', index=None, url=None):
    """标签页管理工具
    action: 'list'|'new'|'close'|'select'
    """
```

### 2.2 存储操作（Storage Operations）
**Playwright MCP 功能（storage caps）：**
- Cookie 操作（新增/删除/获取所有）
- LocalStorage 操作
- SessionStorage 操作
- `browser_storage_state()` - 导出存储状态
- `browser_set_storage_state()` - 导入存储状态

**我们当前的状态：**
- 只有会话级别的 cookie 注入（在 `refresh_session()` 中）
- 没有专门的 cookie/localStorage/sessionStorage 操作工具
- 没有存储状态的导入/导出功能

**DrissionPage 已有能力（查看 _units/cookies_setter.py）：**
```python
# DrissionPage 已有这些能力：
tab.cookies()  # 获取 cookies
tab.cookies_to_session()  # cookies 同步到 session
tab.cookies_to_browser()  # cookies 同步到 browser
```

**可以借鉴添加：**
```python
@mcp.tool()
@read_synchronized
def browser_get_cookies(all_domains=False, all_info=False):
    """获取当前页面的 cookies"""
```

### 2.3 PDF 导出（PDF Export）
**Playwright MCP 功能（pdf caps）：**
- `browser_pdf_save()` - 将页面保存为 PDF

**我们当前的状态：**
- 没有 PDF 导出功能
- 但 DrissionPage 已经有 `save(as_pdf=True)` 功能！

**可以借鉴添加：**
```python
@mcp.tool()
@write_synchronized
def browser_save_pdf(path=None, filename=None):
    """将当前页面保存为 PDF"""
    # 利用 tab.save(as_pdf=True) 实现
```

### 2.4 控制台消息（Console Messages）
**Playwright MCP 功能：**
- `browser_console_messages(level)` - 获取控制台消息
- 支持 'error'|'warning'|'info'|'debug' 级别

**我们当前的状态：**
- 没有控制台消息工具
- DrissionPage 有 `Console` 单元！（查看 _units/console.py）

**可以借鉴添加：**
```python
@mcp.tool()
@read_synchronized
def browser_console_messages(level='info', all=False, filename=None):
    """获取控制台消息"""
    # 利用 tab.console 实现
```

### 2.5 测试断言工具（Testing Assertions）
**Playwright MCP 功能（testing caps）：**
- `browser_verify_element_visible()` - 验证元素可见
- `browser_verify_text_visible()` - 验证文本可见
- `browser_verify_value()` - 验证元素值
- `browser_verify_list_visible()` - 验证列表可见
- `browser_generate_locator()` - 生成元素定位符

**我们当前的状态：**
- 没有专门的断言工具
- 但我们可以用已有的工具组合实现类似功能
- 这些工具主要用于给 AI 提供验证能力，减少 token 消耗

### 2.6 网络高级功能（Network Advanced）
**Playwright MCP 功能（network caps）：**
- `browser_route()` - 设置网络路由（拦截请求）
- `browser_route_list()` - 列出路由
- `browser_unroute()` - 移除路由
- `browser_network_state_set()` - 设置网络状态（离线/在线等）

**我们当前的状态：**
- 已有 `listen_start/wait/stop` 可以监听
- 但没有请求拦截/修改能力
- 没有网络状态模拟能力

**DrissionPage 已有能力（通过 Listener）：**
```python
# Listener 可能有更多能力
# 可以查看 _units/listener.py 了解更多
```

### 2.7 开发工具功能（DevTools）
**Playwright MCP 功能（devtools caps）：**
- 屏幕录制（screencast）
- 元素高亮（highlight）
- 页面标注（annotate）
- 脚本断点恢复

**我们当前的状态：**
- 已有 `mouse_trail()` 鼠标轨迹可视化
- 但 DrissionPage 有 `Screencast` 单元！（刚刚查看了 _units/screencast.py）
- 可以实现屏幕录制功能！

**可以借鉴添加：**
```python
@mcp.tool()
@write_synchronized
def browser_screencast_start(save_path=None, mode='video'):
    """开始屏幕录制"""
    # 利用 tab.screencast.start() 实现

@mcp.tool()
@write_synchronized
def browser_screencast_stop(video_name=None):
    """停止屏幕录制"""
    # 利用 tab.screencast.stop() 实现
```

### 2.8 更多交互工具
**Playwright MCP 还有：**
- `browser_drag()` - 拖拽元素
- `browser_drop(paths)` - 拖放文件
- `browser_select_option()` - 选择下拉选项
- `browser_press_key(key)` - 按键操作
- `browser_fill_form()` - 表单填充

**我们当前的状态：**
- 已有 `click/input/hover` 基础操作
- `insert_text()` 可插入文本
- 但没有专门的按键/拖拽/下拉选择工具

**DrissionPage 已有 Actions 单元！（已查看）：**
- `tab.actions.key_down() / key_up()` - 按键操作
- `tab.actions.drag_in()` - 拖放文件/文本
- `tab.actions.hold() / release()` - 拖拽元素

---

## 三、DrissionPage 有但我们还未封装的能力

### 3.1 滚动操作（Scroller）
**DrissionPage 已有功能（_units/scroller.py）：**
```python
tab.scroll.to_top()      # 滚动到顶部
tab.scroll.to_bottom()   # 滚动到底部
tab.scroll.to_half()     # 滚动到中间
tab.scroll.to_location(x, y)  # 滚动到指定位置
tab.scroll.up(300)       # 向上滚动
tab.scroll.down(300)     # 向下滚动
tab.scroll.left(300)     # 向左滚动
tab.scroll.right(300)    # 向右滚动
tab.scroll.to_see(ele)   # 滚动到看见元素
```

**可以封装为：**
```python
@mcp.tool()
@write_synchronized
def browser_scroll(direction='down', pixel=300, locator=None):
    """滚动操作
    direction: 'top'|'bottom'|'up'|'down'|'left'|'right'|'see'|'location'
    """
```

### 3.2 动作链（Actions）
**DrissionPage 已有功能（_units/actions.py）：**
```python
# 鼠标操作
tab.actions.move_to(ele, duration=0.5)
tab.actions.click(ele)
tab.actions.r_click(ele)  # 右键
tab.actions.m_click(ele)  # 中键
tab.actions.hold(ele)
tab.actions.release()
tab.actions.scroll(delta_y=300)

# 键盘操作
tab.actions.key_down('ctrl')
tab.actions.key_up('ctrl')
tab.actions.type('text', interval=0.01)
tab.actions.input('text')

# 拖放
tab.actions.drag_in(ele, files=['/path/file.txt'])
```

**可以封装为：**
```python
@mcp.tool()
@write_synchronized
def browser_drag_drop(from_locator, to_locator, duration=0.5):
    """拖拽元素"""

@mcp.tool()
@write_synchronized
def browser_drop_files(locator, file_paths):
    """拖放文件到元素"""

@mcp.tool()
@write_synchronized
def browser_press_key(key, modifiers=None):
    """按键操作（支持组合键）"""
```

### 3.3 元素状态查询（States）
**DrissionPage 已有功能（_units/states.py）：**
```python
ele.states.is_displayed  # 是否显示
ele.states.is_hidden     # 是否隐藏
ele.states.is_enabled    # 是否可用
ele.states.is_disabled   # 是否禁用
ele.states.is_selected   # 是否选中
ele.states.is_checked    # 是否勾选
ele.states.is_clickable  # 是否可点击
ele.states.is_covered    # 是否被覆盖
```

**可以封装为：**
```python
@mcp.tool()
@read_synchronized
def browser_get_element_state(locator, state=None):
    """获取元素状态
    state: 'displayed'|'hidden'|'enabled'|'disabled'|'selected'|'checked'|'clickable'|'covered'
    """
```

### 3.4 元素属性操作
**DrissionPage 已有功能：**
```python
ele.attr('id')              # 获取属性
ele.attrs                   # 获取所有属性
ele.text                    # 获取文本
ele.html                    # 获取 HTML
ele.inner_html              # 获取内部 HTML
ele.value                   # 获取值
ele.rect                    # 获取元素位置大小
```

**我们已有 `find_elements()` 返回部分信息，但可以更丰富：**
```python
@mcp.tool()
@read_synchronized
def browser_get_element_attrs(locator, attrs=None):
    """获取元素的属性"""

@mcp.tool()
@read_synchronized
def browser_get_element_rect(locator):
    """获取元素的位置和大小"""
```

### 3.5 页面状态/等待
**DrissionPage 已有功能（_units/waiter.py）：**
```python
tab.wait.ele_displayed(locator)
tab.wait.ele_hidden(locator)
tab.wait.ele_enabled(locator)
tab.wait.ele_loaded(locator)
tab.wait.ele_deleted(locator)
tab.wait.ele_disabled(locator)
tab.wait.url_change(old_url)
tab.wait.title_change(old_title)
tab.wait.alert_closed()
```

**可以封装为：**
```python
@mcp.tool()
@read_synchronized
def browser_wait_for_element(locator, state='displayed', timeout=10):
    """等待元素达到某个状态"""
```

### 3.6 页面保存
**DrissionPage 已有功能（已查看）：**
```python
tab.save(path, name)        # 保存为 HTML
tab.save(as_pdf=True)       # 保存为 PDF
```

**可以封装为：**
```python
@mcp.tool()
@write_synchronized
def browser_save_page(path=None, filename=None, as_html=True, as_pdf=False):
    """保存页面"""
```

### 3.7 等待工具（Wait）
**DrissionPage 已有丰富的等待功能（_units/waiter.py）：**
```python
# 元素等待
tab.wait.ele_displayed()
tab.wait.ele_hidden()
tab.wait.ele_enabled()
tab.wait.ele_disabled()
tab.wait.ele_loaded()
tab.wait.ele_deleted()
tab.wait.ele_clickable()

# 页面等待
tab.wait.url_change()
tab.wait.title_change()
tab.wait.load_start()
tab.wait.doc_loaded()
tab.wait.new_tab()
tab.wait.alert_closed()

# 自定义等待
tab.wait(lambda: ...)
```

---

## 四、改进优先级建议

### P0 - 高价值低投入（建议立即做）
1. **标签页管理工具** - `browser_tabs()` - 复用 browser_session 的能力
2. **滚动操作工具** - `browser_scroll()` - 封装 Scroller 单元
3. **按键操作工具** - `browser_press_key()` - 封装 Actions 单元
4. **PDF 导出工具** - `browser_save_pdf()` - 用 tab.save(as_pdf=True)

### P1 - 中价值中投入（建议近期做）
1. **Cookie/LocalStorage 操作** - 需要查看 cookies_setter.py 了解 API
2. **元素状态查询** - `browser_get_element_state()`
3. **等待工具增强** - `browser_wait_for_element()`
4. **屏幕录制** - `browser_screencast_start/stop()` - 封装 Screencast
5. **控制台消息** - `browser_console_messages()` - 封装 Console 单元

### P2 - 高价值高投入（建议规划做）
1. **网络路由拦截** - 需要深入研究 Listener 的能力
2. **表单填充工具** - `browser_fill_form()` - 高级封装
3. **拖拽操作工具** - `browser_drag_drop()` / `browser_drop_files()`
4. **测试断言工具** - `browser_verify_*()` 系列

---

## 五、具体实现建议

### 示例：滚动操作工具
```python
# mcp-servers/drission-ui/scroller.py（新增）

@mcp.tool()
@write_synchronized
def browser_scroll(direction='down', pixel=300, locator=None, x=None, y=None):
    """滚动操作
    direction: 'top'|'bottom'|'half'|'up'|'down'|'left'|'right'|'see'|'location'
    pixel: 滚动像素数（用于 up/down/left/right）
    locator: 目标元素（用于 see）
    x/y: 滚动位置（用于 location）
    """
    tab = browser_session.get_tab()

    if direction == 'top':
        tab.scroll.to_top()
    elif direction == 'bottom':
        tab.scroll.to_bottom()
    elif direction == 'half':
        tab.scroll.to_half()
    elif direction == 'up':
        tab.scroll.up(pixel)
    elif direction == 'down':
        tab.scroll.down(pixel)
    elif direction == 'left':
        tab.scroll.left(pixel)
    elif direction == 'right':
        tab.scroll.right(pixel)
    elif direction == 'see' and locator:
        ele = browser_session.find(locator)
        if ele:
            tab.scroll.to_see(ele)
        else:
            return {'ok': False, 'reason': 'Element not found'}
    elif direction == 'location' and x is not None and y is not None:
        tab.scroll.to_location(x, y)
    else:
        return {'ok': False, 'reason': 'Invalid direction or missing parameters'}

    return {'ok': True, 'direction': direction}
```

### 示例：标签页管理工具
```python
# mcp-servers/drission-ui/browser_session.py（增强）

@mcp.tool()
@write_synchronized
def browser_tabs(action='list', index=None, url=None):
    """标签页管理
    action: 'list'|'new'|'close'|'select'
    """
    browser = browser_session.get_browser()

    if action == 'list':
        tabs = []
        for i, tid in enumerate(browser.tab_ids):
            t = browser.get_tab(tid)
            tabs.append({
                'index': i,
                'tab_id': tid,
                'url': t.url,
                'title': t.title,
                'is_current': t.tab_id == browser_session.get_tab().tab_id
            })
        return {'ok': True, 'tabs': tabs}

    elif action == 'new':
        new_tab = browser.new_tab(url)
        # 更新当前活动 tab
        browser_session._tab = new_tab
        return {'ok': True, 'url': new_tab.url, 'tab_id': new_tab.tab_id}

    elif action == 'close' and index is not None:
        tabs = browser.tab_ids
        if 0 <= index < len(tabs):
            browser.close_tabs(tabs[index])
            return {'ok': True}
        else:
            return {'ok': False, 'reason': 'Invalid index'}

    elif action == 'select' and index is not None:
        tabs = browser.tab_ids
        if 0 <= index < len(tabs):
            selected_tab = browser.get_tab(tabs[index])
            browser_session._tab = selected_tab
            return {'ok': True, 'url': selected_tab.url}
        else:
            return {'ok': False, 'reason': 'Invalid index'}

    else:
        return {'ok': False, 'reason': 'Invalid action'}
```

---

## 六、总结

### 我们已有的优势
1. ✅ VTable 深度支持（canvas 表格操作）
2. ✅ 筛选区封装（Ant Design 日期选择等）
3. ✅ 观察器（MutationObserver 事件驱动）
4. ✅ 网络监听（含 WebSocket）
5. ✅ OCR 登录集成
6. ✅ 最近已实现：能力分组、输出重定向

### Playwright MCP 可借鉴的点
1. ✅ 能力分组（caps）- 已实现
2. ✅ 输出重定向（filename）- 已实现
3. ⏳ 标签页管理 - 建议添加
4. ⏳ 存储操作（Cookie/LocalStorage）- 建议添加
5. ⏳ 滚动操作 - 建议添加
6. ⏳ PDF 导出 - 建议添加
7. ⏳ 屏幕录制 - 建议添加
8. ⏳ 控制台消息 - 建议添加
9. ⏳ 按键/拖拽等高级交互 - 可以规划添加

### DrissionPage 还有的潜力
1. **动作链（Actions）** - 完整的鼠标/键盘操作支持
2. **滚动器（Scroller）** - 丰富的滚动能力
3. **屏幕录制（Screencast）** - 视频录制功能
4. **控制台（Console）** - 控制台消息获取
5. **等待器（Waiter）** - 丰富的等待条件
6. **状态查询（States）** - 元素状态查询
7. **Cookies 管理** - Cookie 操作

### 建议的下一步
1. **短期（1-2周）** - 添加 P0 工具（标签、滚动、按键、PDF）
2. **中期（1个月）** - 添加 P1 工具（Cookie、元素状态、等待、录制、控制台）
3. **长期（规划）** - 逐步完善 P2 工具
