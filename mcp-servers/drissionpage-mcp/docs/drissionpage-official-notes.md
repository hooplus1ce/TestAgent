# DrissionPage 官方文档要点

来源均为 DrissionPage 官方文档站，当前稳定文档适用版本为 4.1.1.4。

## 已核对页面

- 首页与版本提示：https://www.drissionpage.cn/
- 浏览器控制概述：https://www.drissionpage.cn/browser_control/intro
- 页面运行 JS：https://www.drissionpage.cn/browser_control/page_operation
- 元素操作：https://www.drissionpage.cn/browser_control/ele_operation
- iframe：https://www.drissionpage.cn/browser_control/iframe
- 动作链：https://www.drissionpage.cn/browser_control/actions
- 网络监听：https://www.drissionpage.cn/browser_control/listener
- 元素定位语法：https://www.drissionpage.cn/browser_control/get_elements/intro

## 实现约束

- 浏览器对象使用 `Chromium()` / `ChromiumOptions`，可接管端口或按配置启动。
- 页面、元素和 frame 都以 DrissionPage 对象为主，不使用 Selenium、Playwright 或 Puppeteer runtime。
- `ele()` 带内置等待；批量读取用 `eles()` / `s_ele()`，交互前按需等待 `ele.wait.clickable()`。
- iframe 优先用 `get_frame()` 取得 `ChromiumFrame`，再在 frame 内定位或执行 JS。
- 坐标点击使用 DrissionPage `actions.move_to(...).click()`；普通元素优先使用 DrissionPage 元素 API。
- `run_js()` 要读取返回值时必须写顶层 `return`。IIFE 内部 `return` 不作为 DrissionPage 返回值。
- 监听器 4.1.1.4 支持 `listen.start(targets, method, res_type, ws_only)` 和 `listen.wait()`；4.2 beta 将 method/resourceType 迁移到独立 setter，本服务按运行时能力探测。
- 下载优先支持 4.2 的 `download.by_browser`；稳定 API 下可通过点击触发后 `wait.download_begin()` / `wait.downloads_done()` 观察。

## 项目侧补充规则

- VTable canvas 只能通过 MCP facade 访问；底层实例发现、坐标换算和编辑状态同步封装在 `vtable.py` 与 `js/`。
- 弹窗/浮层/消息/通知统一由观察器捕获：当前状态用 `observe_snapshot`，交互结果用 `observe_start -> action -> observe_wait` 或 `explore_action`。
- 筛选区必须优先切到内联模式，并把字段名、操作符和值控件模式绑定返回。
- 保存按钮操作前读取 class，区分普通按钮与 `ant-dropdown-trigger` 下拉按钮。
