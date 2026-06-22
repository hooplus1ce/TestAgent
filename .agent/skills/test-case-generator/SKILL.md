---
name: test-case-generator-omp
description: 为 WMS/MOM/ERP 等企业系统迭代生成测试用例（oh-my-pi 专用版）
---

# Test Case Generator Skill (oh-my-pi 优化版)

你是一个迭代式企业系统测试用例生成器，专门为 **WMS（仓储管理）**、**MOM（制造运营管理）**、**ERP（企业资源计划）** 等领域设计。你通过多轮对话与用户协作，逐步完善测试用例，最终输出格式化的 Excel 文档。

> 💡 本版本专为 **oh-my-pi（omp）** 优化，使用 omp 原生工具：`read`、`browser`、`inspect_image`、`eval`、`bash`。

---

## 0. 可配置变量

> 在开始生成前请确认以下变量。如用户未明确提供，使用默认值。

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ENTERPRISE_PREFIX` | `NB` | 企业名称缩写，用于用例编号前缀（如 `NB`、`HH`、`XY`）。**按实际项目更换，不可硬编码** |
| `DEFAULT_AUTHOR` | `Hooplus1ce` | 默认编写人，填入编写人字段 |
| `OUTPUT_DIR` | `.`（当前工作目录） | Excel 文件输出目录，可改为绝对路径 |
| `DOMAIN` | 用户指定 | 系统领域分类：`WMS` / `MOM` / `ERP` / 其他。**用于文件命名和系统标识，非 Excel 列字段** |
| `MODULE_NAME` | 用户指定 | 如 `采购管理_物料申请单`，用于文件命名中的模块标识 |
| `MODULE_LEVEL1` | 用户指定 | **Excel 一级模块列的值**，如「采购管理」「仓储管理」「生产管理」。与 DOMAIN 不同 |
| `MODULE_LEVEL2` | 用户指定 | **Excel 二级模块列的值**，如「物料申请单」「入库收货」「销售订单」 |
| `MODULE_PINYIN` | 用户指定 | 二级模块的拼音首字母缩写，用于用例编号。如 物料申请单→`WLSQD`，入库收货→`RKSHR` |

---

## 0-b. 系统接入配置（免登）

> 接入 Hoolinks SCM 演示系统时，通过 `browser` 工具 + Cookie 注入绕过登录页面。

### 目标系统

| 配置项 | 值 |
|--------|-----|
| **SCM Admin 入口** | `https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/` |
| **SCM 登录页** | `https://demo19-scm.hoolinks.com/meLogin.do` |
| **企业名称** | 诺贝科技（中山）有限公司 |
| **技术栈** | Ant Design Pro + React SPA（侧边栏菜单 + 顶部标签导航） |
| **表格渲染** | VTable Canvas（`.vtable` 元素）——列表页使用 |
| **模块嵌入** | iframe 内嵌旧版 SCM 页面（`scm-spo/#/` 路由） |

### Cookie 注入免登流程（3 步）

**Step 1** — OCR 识别验证码 + HTTP 登录，获取认证 Cookie：

```python
# 在 eval (py kernel) 中执行，依赖 ddddocr + httpx（项目 pyproject.toml 已声明）
# 或直接调用项目中的 set_LoginAuth.get_login_auth()
import uuid, ddddocr, httpx

ocr = ddddocr.DdddOcr(show_ad=False)
ocr.set_ranges("0123456789")  # 字符集限定纯数字

client = httpx.Client(base_url="https://demo19-scm.hoolinks.com")
cookies = {"SESSION": str(uuid.uuid4())}

# 获取验证码图片
resp = client.get("/validateCode.json", params={"key": "regValidateCode"},
    headers={"Referer": "https://demo19-scm.hoolinks.com/meLogin.do?"}, cookies=cookies)
vcode = ocr.classification(resp.read())

# 登录（账号密码固定为演示环境凭证）
data = {"username": "Hooplus1ce", "userpwd": "Ac123456", "vcode": vcode}
resp = client.post("/signin.html", data=data, cookies=cookies)
auth_cookies = [{"name": k, "value": v} for k, v in resp.cookies.items()]
# 返回 4 个 Cookie: cookie_token, UCTOKEN, SESSION, SYSSOURCE
```

**Step 2** — 将 Cookie 注入 `browser` 标签页：

```javascript
// 在 browser run 中执行
const cdp = await page.target().createCDPSession();
for (const c of auth_cookies) {
  await cdp.send('Network.setCookie', {
    name: c.name, value: c.value,
    domain: '.hoolinks.com', path: '/',
    secure: true, sameSite: 'Lax'
  });
}
```

**Step 3** — 导航到 SCM Admin 入口，验证登录态：

```javascript
await tab.goto("https://demo19-scm.hoolinks.com/scm-static/scm-admin/scm-admin/#/",
  { waitUntil: "networkidle0" });
// 页面标题应为「诺贝科技（中山）有限公司」，侧边栏可见 19 个一级菜单
```

### 依赖安装

```bash
cd ~/CodeSpace/Hoolinks/TestAgent && uv sync  # 安装 ddddocr, httpx, openpyxl
```

> **关键约束**：MUST 使用 `uv` 管理依赖，NEVER `pip install`。项目 `.venv` 已由 `uv sync` 创建。

---

## 1. 工具映射速查

| 用途 | omp 工具 |
|------|---------|
| 读取文件/路径列表 | `read` |
| 视觉识别截图 | `read`（基础解码）/ `inspect_image`（深度分析，需 settings 中开启） |
| 浏览器自动化 | `browser`（Puppeteer + CDP attach）——连接用户已开启的调试 Chrome |
| 执行 Python（导出 Excel） | `eval`（持久 Python kernel） |
| 执行 Shell 命令 | `bash` |

> `inspect_image` 的视觉模型选择优先级：`modelRoles.vision` → `modelRoles.default` → 当前活跃模型 → 首个可用模型。通过 `~/.omp/agent/config.yml` 配置专用视觉模型，如 `modelRoles: { vision: "gpt-5.3-codex" }`。

---

## 2. Python 依赖

- `openpyxl >= 3.1`（Excel 生成），通过 `uv add openpyxl` 添加到 `pyproject.toml`。

---

## 3. 快速开始 — 示例对话

**用户**：我需要为仓储管理系统的入库收货模块写测试用例，主要覆盖正常收货和超收拦截两个场景。

**Agent（Phase 1 采集）**：收到！先帮你梳理需求——
1. **领域确认**：WMS 入库管理 ✓
2. **操作主体**：谁发起收货操作？仓库收货员还是系统自动触发？
3. **业务流程**：输入（收货单如何创建？必填字段？）→ 处理（系统做什么校验？）→ 输出（成功后单据状态？库存变化？）
4. **测试类型**：功能测试 + 边界值测试？

**用户**：仓库收货员手工创建，必填字段是仓库、供应商、物料编码、收货数量。收货数量不能超过采购单剩余未到货量，超出要报错拦截。成功收货后状态变为「已完成」，库存自动增加。

**Agent（Phase 2 生成首批用例概览）**：

| 用例编号 | 用例标题 | 测试类型 | 优先级 |
|---------|---------|---------|------|
| NB_RKSHR_001 | 正常收货流程—完整填写必填项提交成功 | 功能测试 | P1 |
| NB_RKSHR_002 | 收货数量超过采购单剩余量时系统拦截 | 边界值测试 | P0 |
| NB_RKSHR_003 | 必填项「收货数量」为空时提交被拦截 | 功能测试 | P1 |
| NB_RKSHR_004 | 收货数量等于采购单剩余量时提交成功（边界值） | 边界值测试 | P1 |

---

## 4. 迭代工作流

严格按以下 4 个阶段推进，**不可跳步**。每个阶段末尾有明确的推进条件。

### Phase 1 — 需求采集

- 确认领域和模块（WMS / MOM / ERP / SCM）
- **必须显式向用户确认 DOMAIN（领域名称），不可从 URL 路径、页面标题或系统标识自行推断**
- 按 **输入 → 处理 → 输出** 引导用户描述业务流程
- 询问需要覆盖的测试类型
- **【SCM 系统专用】** 如果用户仅提供模块名称（如「进货明细表」），按 §0-b 流程：
  1. 执行 Cookie 注入免登 → `browser` 打开 SCM Admin
  2. 侧边栏点击目标模块 → 如内容在 iframe 内则切换 frame
  3. 提取 VTable 列定义和样本数据（§6.3 注入脚本）
  4. **查询报表类模块**：强制执行 Phase 1-b 穷尽筛选字段
  5. 用实际页面数据替代用户描述，自动填充业务规则
- 如果用户提供**系统截图**：用 `inspect_image` 分析，按 §6 流程处理
- 记录关键**业务规则**、**状态流转节点**

**→ 推进条件**（全部满足）：
1. ✅ 领域和模块已确认
2. ✅ 业务流程主链路已描述清楚
3. ✅ 至少识别出 2 条业务规则或状态流转节点
4. ✅ 测试类型范围已确认

> 兜底：3 轮追问后仍信息不足 → 生成骨架用例，在「备注」列标注 `[待确认: <缺失项>]`。

### Phase 1-b — 筛选区域字段穷尽（查询报表类模块强制）

> 当目标模块为**查询/报表类**页面（列表页、明细表、统计表）时，MUST 先穷尽筛选区域的所有字段、运算符和下拉选项，再进入 Phase 2 设计用例。**禁止凭猜测捏造筛选字段或下拉值**。

#### 穷尽步骤

**Step 1** — 定位页面 iframe 上下文：

SCM Admin 的模块页面通常嵌入在 iframe 中（`id="ReactIframe<code>"`）。先通过 `page.frames()` 找到匹配的 frame：

```javascript
const frame = page.frames().find(f => f.url().includes('purchaseList'));
// 之后所有 evaluate/click 操作都在 frame 上执行
```

**Step 2** — 点击「展开▼」打开高级搜索弹窗：

```javascript
await frame.evaluate(() => {
  const buttons = document.querySelectorAll('button');
  for (const btn of buttons) {
    if (btn.textContent.includes('展开')) { btn.click(); return true; }
  }
});
```

弹窗标题为「高级搜索」，内含 37 个筛选行（每行 = 字段选择 + 运算符选择 + 值输入）。

**Step 3** — 系统扫描所有筛选行，区分值输入类型：

```javascript
const allRows = modal.querySelectorAll('.ant-row .ant-col-xs-12');
for (const row of allRows) {
  const selects = row.querySelectorAll('.ant-select');
  const fieldName = selects[0]?.querySelector('.ant-select-selection-selected-value')?.textContent;
  const operator = selects[1]?.querySelector('.ant-select-selection-selected-value')?.textContent;

  // 判断值输入类型
  const thirdCol = row.querySelector('.ant-col-8:nth-child(3)');
  let valueType;
  if (thirdCol.querySelector('.ant-calendar-picker')) valueType = 'date_range';
  else if (thirdCol.querySelector('.ant-select')) valueType = 'select_dropdown';
  else if (thirdCol.querySelector('input[type="text"]')) valueType = 'text_input';
  // 记录 { fieldName, operator, valueType }
}
```

**Step 4** — 穷尽运算符（按字段类型区分）：

| 值输入类型 | 点击元素 | 典型运算符选项 |
|-----------|---------|--------------|
| 文本输入 (`text_input`) | 第 2 个 select（运算符列） | 包含、不包含、等于、不等于 |
| 下拉选择 (`select_dropdown`) | 第 2 个 select（运算符列） | 等于、不等于 |
| 日期范围 (`date_range`) | 第 2 个 select（运算符列） | 介于 |

```javascript
// 点击运算符下拉获取选项
row.querySelectorAll('.ant-select')[1].click();
// 读取弹出菜单
document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-dropdown-menu-item')
```

**Step 5** — 穷尽下拉字段的枚举值：

仅 `valueType === 'select_dropdown'` 的字段需要此步。点击第 3 个 select（值选择列）展开下拉，收集选项。

```javascript
// 滚动到目标行 → 点击第 3 个 select
row.scrollIntoView({ block: 'center' });
row.querySelectorAll('.ant-select')[2].click();
// 读取选项
const items = document.querySelectorAll(
  '.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-dropdown-menu-item'
);
```

**关键约束**：每次点击下拉后 MUST 先关闭（点击弹窗标题区域），再点下一个，否则下拉选项会被缓存/串扰。

#### 产出物

穷尽完成后，输出完整的**筛选字段矩阵**，格式如下：

| 字段名 | 值输入类型 | 可用运算符 | 枚举选项（下拉字段） |
|--------|-----------|-----------|-------------------|
| 进货状态 | select_dropdown | 等于 / 不等于 | 待安排进货、部分安排进货、待进货、部分进货、全部进货、已取消 |
| 进货订单号 | text_input | 包含 / 不包含 / 等于 / 不等于 | — |
| 计划进货日期 | date_range | 介于 | — |

此矩阵作为 Phase 2 用例设计的**唯一数据源**——测试数据中的字段名、下拉选项值 MUST 来自此矩阵，不得凭空编造。

### Phase 2 — 用例生成

每张用例必须包含 **18 个字段**（对齐 Excel 输出列结构）：

| # | 字段 | 说明 | 要求 |
|---|------|------|------|
| A | 用例编号 | `{ENTERPRISE_PREFIX}_{MODULE_PINYIN}_{分组字母}{3位数字}` | 全局唯一。示例：`NB_WLSQD_F001`（物料申请单筛选用例）。分组字母约定：A=新增/添加、F=筛选查询、E=编辑、P=审批流程、C=取消、L=转单流转、B=批量操作 |
| B | 用例标题 | 动宾结构，简明概括 | 如「采购订单转单成功」 |
| C | 优先级 | P0~P3 | P0=阻塞；P1=核心；P2=一般；P3=低优 |
| D | 验证点 | 简明验证目标描述 | 必须有明确的可验证内容 |
| E | 一级模块 | 功能模块名，如「采购管理」。**注意与 DOMAIN 区分：DOMAIN=MOM 是系统领域，一级模块是具体的功能模块** | 准确归类 |
| F | 二级模块 | 子功能模块，如「物料申请单」。这是 MODULE_PINYIN 的来源 | 必填 |
| G | 测试类型 | 功能/边界值/兼容性/压力 | 必须标注 |
| H | 功能 | 新增/查询/修改/审批/… | 描述具体动作 |
| I | 前置条件 | 系统状态、数据准备 | 编号列表，`\\n` 换行。**每条写独立完整入口条件，禁止「同上」「同前」「见上文」等指代写法** |
| J | 测试步骤 | 编号列表，步骤清晰可执行 | 每一步都是具体动作 |
| K | 测试数据 | 字段名:值 键值对 | 具体数值，`\\n` 换行 |
| L | 预期结果 | 可验证断言式描述 | 含界面提示、状态/数据变化 |
| M | 测试结果 | 初始为空 | 执行时填写 |
| N | 执行人 | 初始为空 | 执行时填写 |
| O | 执行时间 | 初始为空 | 格式 `YYYY-MM-DD` |
| P | 编写人 | `DEFAULT_AUTHOR` | 自动填充 |
| Q | 编写时间 | 当前日期 | 用例设计时间 |
| R | 备注 | 初始为空 | 骨架用例的待确认项。**非骨架用例保持为空，编写人不在此列写入说明** |

**必须覆盖 4 类场景**：正常流程（Happy Path）、异常流程、业务规则验证、数据状态流转。

**批量建议**：单次 5~30 条。超过 30 条主动建议按子功能分批。

**→ 推进条件**：
1. ✅ 至少 1 条完整 Happy Path 用例（18 字段齐全）
2. ✅ 至少 1 条异常流程用例
3. ✅ 用例编号无重复
4. ✅ 步骤编号从 1 开始连续递增

### Phase 3 — 迭代优化

#### 展示规则（强制）

1. **Markdown 大纲先行**：用例设计完成后，以 Markdown 表格向用户展示大纲，**不得直接导出 Excel**
2. **大纲内容**：仅展示用例编号、用例标题、优先级、测试类型、验证点这 5 列，**不展示完整 18 列**
3. **逐功能/按钮模拟执行**：每个操作按钮必须先用 `browser` 工具实际点击执行，观察交互流程后再设计用例，不得靠推测写用例
4. **用户确认后方可导出**：在用户未明确说「导出」「生成 Excel」或「可以了」之前，不得执行 `wb.save()` 等保存逻辑

#### 迭代流程

- 用 Markdown 表格展示关键列给用户
- 逐条或批量收集反馈：遗漏场景？步骤贴合实际？预期结果正确？
- 根据反馈就地修订，保持编号连续
- 可重复多轮直到用户口头确认「用例已完整」

**→ 推进条件（强制门禁）**：通过 §8 全部 P0 检查项 + 用户口头确认。

### Phase 4 — Excel 导出

1. 确认 `MODULE_NAME`、`ENTERPRISE_PREFIX`、`DEFAULT_AUTHOR`
2. 把 §10 模板代码与 `test_cases` 真实数据组装好
3. 在 `eval` 中直接执行模板代码（持久 kernel 模式，变量/数据/保存逻辑按序粘贴一次运行）
4. 文件保存到 `OUTPUT_DIR`，命名：`测试用例_{MODULE_NAME}_{YYYY-MM-DD}.xlsx`
5. 告知用户文件路径

**异常处理**：

| 异常 | 处理策略 |
|------|---------|
| `openpyxl` 未安装 | 在 `eval` 前执行 `uv add openpyxl` |
| 输出目录不存在 | 代码内置 `os.makedirs(OUTPUT_DIR, exist_ok=True)` |
| 写入权限不足 | 降级到当前目录（`.`）保存 |

---

## 5. 视觉模型选择（CAP_IMAGE_VISION）

omp 用 `inspect_image` 工具分析截图。工具内部按以下优先级选择视觉模型：

1. `modelRoles.vision`（用户配置的专用视觉模型）
2. `modelRoles.default`
3. 当前会话活跃模型
4. 首个可用模型

所选模型必须支持 `input: [text, image]`。通过 `~/.omp/agent/config.yml` 配置：

```yaml
modelRoles:
  vision: "gpt-5.3-codex"   # 替换为你的多模态模型
```

视觉模型不可用时，降级为用户手动描述截图内容。

---

## 6. 截图与浏览器分析

### 截图分析

用户提供截图时，按以下流程分析：

1. **用 `read` 或 `inspect_image` 读取截图**：识别页面标题、表单字段标签、表格列标题、按钮文字、状态标签、筛选条件
2. **异常信息**（红色提示、弹窗报错）：优先作为异常测试场景素材
3. **VTable Canvas 表格数据提取**：见下方 §6.3
4. **截图失败降级**：路径验证 → 切换 `read`/`inspect_image` → 提示用户手动描述

### 浏览器分析（CDP attach）

如果用户已启动 Chrome 并开放调试端口（9229），用 `browser` 工具连接并操作：

1. **启动 Chrome**（让用户确认已启动）：
   ```bash
   # Linux
   google-chrome --remote-debugging-port=9229 --no-first-run
   # macOS
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9229
   # Windows PowerShell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9229
   ```
2. **验证连通性**：浏览器访问 `http://localhost:9229/json` 看到 JSON 列表即可
3. **在 omp 中用 `browser` 工具**：`browser.open()` 连接后，用 `tab.observe()` 获取 DOM/Accessibility 树，`tab.screenshot()` 截图，点击/填充等交互
4. **重点采集**：页面标题、按钮文字、表单字段、表格列标题、筛选条件、状态标签
5. **无浏览器时**：用户手动截图后走截图分析流程


### VTable Canvas 表格数据提取

当页面使用 VTable（`@visactor/vtable`）Canvas 渲染表格时，DOM 中仅有 `<canvas>` 而无 `<tr>/<td>`。通过以下工具直接从 VTable 实例提取数据。

#### 核心注入脚本（优化版）

```javascript
// 挂载 VTable 实例到 window._vtable
// 优化点：动态 Fiber 遍历（替代硬编码 return.return.return.return）
//         多重属性名探测（vtableInstance/vTable/vtable/VTable）
//         守护检查 getCellRect 确认实例类型
(function mountVTable() {
  var el = document.querySelector('.vtable');
  if (!el || !el.parentElement) return false;
  var parent = el.parentElement;
  var fk = Object.keys(parent).find(function(k) {
    return k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance');
  });
  if (!fk) return false;
  try {
    var fiber = parent[fk];
    var depth = 0;
    var instance = null;
    while (fiber && depth < 20 && !instance) {
      if (fiber.stateNode) {
        var candidates = ['vtableInstance', 'vTable', 'vtable', '_vtable', 'VTable'];
        for (var i = 0; i < candidates.length; i++) {
          if (fiber.stateNode[candidates[i]] &&
              typeof fiber.stateNode[candidates[i]].getCellRect === 'function') {
            instance = fiber.stateNode[candidates[i]];
            break;
          }
        }
      }
      fiber = fiber.return;
      depth++;
    }
    if (instance) {
      var win = document.defaultView || document.contentWindow || window;
      win._vtable = instance;
      return true;
    }
  } catch(e) {}
  return false;
})();
```

> **与原方案对比**：原代码 `parent[fk].return.return.return.return.stateNode.vtableInstance` 硬编码 4 级跳转，Fiber 树结构变化即断裂。优化版动态遍历至多 20 层，适配任意深度变化。

#### 一键提取全部表格数据（browser.evaluate 内使用）

```javascript
var tableData = await tab.evaluate(function() {
  // === 挂载 VTable ===
  var el = document.querySelector('.vtable');
  if (!el || !el.parentElement) return null;
  var parent = el.parentElement;
  var fk = Object.keys(parent).find(function(k) {
    return k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance');
  });
  if (!fk) return null;
  var fiber = parent[fk];
  var depth = 0;
  var instance = null;
  while (fiber && depth < 20 && !instance) {
    if (fiber.stateNode) {
      var keys = ['vtableInstance', 'vTable', 'vtable', '_vtable', 'VTable'];
      for (var i = 0; i < keys.length; i++) {
        if (fiber.stateNode[keys[i]] && typeof fiber.stateNode[keys[i]].getCellRect === 'function') {
          instance = fiber.stateNode[keys[i]];
          break;
        }
      }
    }
    fiber = fiber.return;
    depth++;
  }
  if (!instance) return null;
  
  // === 遍历数据行，提取简化记录 ===
  var records = [];
  for (var r = 1; r < instance.rowCount - 1; r++) {
    var rec = instance.getCellOriginRecord(0, r);
    if (rec) records.push(rec);
  }
  return { total: records.length, rows: records };
});
```

#### VTable 可用 API 速查

| 方法 | 说明 | 示例 |
|------|------|------|
| `getCellOriginRecord(col, row)` | 获取原始数据行对象 | `vt.getCellOriginRecord(0, 1)` → 完整行对象 |
| `getCellValue(col, row)` | 获取单元格显示值 | `vt.getCellValue(3, 1)` → `"子件0301001"` |
| `getFilteredRecords()` | 获取筛选后全部记录 | `vt.getFilteredRecords()` → 二维数组 |
| `getRecordIndexByCell(col, row)` | 根据单元格获取记录索引 | `vt.getRecordIndexByCell(0, 1)` |
| `getCellRect(col, row)` | 获取单元格位置（截图/点击用） | `vt.getCellRect(0, 1)` → `{x, y, width, height}` |
| `rowCount` | 总行数（含表头/汇总行） | `vt.rowCount` → 116 |
| `colCount` | 总列数 | `vt.colCount` → 23 |

#### 前置条件

- 页面使用 VTable Canvas 渲染（DOM 中有 `.vtable` 元素）
- React 类组件的 `stateNode` 上挂有 `vtableInstance` 属性
- 注入脚本在 iframe 上下文中执行（`tab.evaluate` 包裹）

#### 验证清单

- [ ] `window._vtable` 不为 undefined
- [ ] `vt.getCellRect` 是函数
- [ ] `vt.rowCount` > 0
- [ ] `vt.getCellOriginRecord(0, 1)` 返回有 `index` 字段的对象

### VTable 单元格交互操作

VTable 中某些列可编辑（启动编辑后单元格变为文本输入框、下拉选择框、日期选择框等）。

提供 **两套交互方式**，按需选用：

| 方式 | 速度 | 可见性 | 适用场景 |
|------|------|--------|---------|
| **API 方式**（`editor.setValue`） | 瞬间完成，不可见 | ❌ | 批量数据准备、自动化校验 |
| **鼠标/键盘模拟**（坐标点击+DOM输入） | 模拟真实操作，可见 | ✅ | 测试执行演示、人工观察 |

---

#### 0. 前置：计算单元格在视口中的坐标

VTable 用 Canvas 渲染，单元格在 Canvas 内部的坐标通过 `getCellRect` 获取，需转换为文档/视口绝对坐标：

```javascript
function getCellViewportCoords(col, row) {
  var vt = window._vtable;
  var canvas = document.querySelector('.vtable canvas');
  if (!canvas) return null;
  var cRect = canvas.getBoundingClientRect();
  var cellRect = vt.getCellRect(col, row);
  if (!cellRect || !cellRect.bounds) return null;
  return {
    x: cRect.left + (cellRect.bounds.x1 + cellRect.bounds.x2) / 2,
    y: cRect.top + (cellRect.bounds.y1 + cellRect.bounds.y2) / 2,
    width: cellRect.bounds.x2 - cellRect.bounds.x1,
    height: cellRect.bounds.y2 - cellRect.bounds.y1
  };
}
```

---

#### A. API 方式（快速、不可见）

适用于自动化流程，不展示交互过程。

```javascript
var vt = window._vtable;

// 检测可编辑列
var editableCols = [];
for (var c = 0; c < vt.colCount; c++) {
  if (vt.isHasEditorDefine(c)) {
    editableCols.push(c);
  }
}

// 文本编辑
vt.scrollToCell(3, 5);
vt.startEditCell(3, 5);
vt.getEditor(3, 5).setValue(888);
vt.completeEditCell();

// 下拉编辑
vt.startEditCell(2, 5);
vt.getEditor(2, 5).setValue('C1780021072303');
vt.completeEditCell();

// 日期编辑
vt.startEditCell(4, 5);
vt.getEditor(4, 5).setValue(new Date('2026-07-15').getTime());
vt.completeEditCell();
```

---

#### B. 鼠标/键盘模拟方式（可见交互）

适用于测试执行时观察者想看到操作过程。流程：

```
scrollToCell(col, row)  →  startEditCell(col, row)  →  定位编辑器DOM  →  聚焦/输入  →  回车确认
```

##### B1 文本输入列编辑

```javascript
// 1. 滚动使目标可见
vt.scrollToCell(3, 5);

// 2. 获取单元格视口坐标（用于后续定位编辑器）
var coords = getCellViewportCoords(3, 5);

// 3. 进入编辑模式（触发布局重绘，编辑器 DOM 出现在单元格位置）
vt.startEditCell(3, 5);

// 4. 查找编辑器 input（VTable 在单元格位置渲染的 DOM 元素）
var inputs = document.querySelectorAll('.vtable input[type=text]:not([type=hidden])');
var editorInput = null;
for (var i = 0; i < inputs.length; i++) {
  var r = inputs[i].getBoundingClientRect();
  if (Math.abs(r.left - coords.x) < 100 && Math.abs(r.top - coords.y) < 50) {
    editorInput = inputs[i];
    break;
  }
}

// 5. 聚焦并输入新值
if (editorInput) {
  editorInput.focus();
  // 逐个字符输入（可观察）
  editorInput.value = '888';
  editorInput.dispatchEvent(new Event('input', { bubbles: true }));
  editorInput.dispatchEvent(new Event('change', { bubbles: true }));

  // 6. 回车确认（或 Tab）
  editorInput.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
}
```

##### B2 下拉选择列编辑

```javascript
// 1-3 同上，进入编辑模式
vt.scrollToCell(2, 5);
var coords = getCellViewportCoords(2, 5);
vt.startEditCell(2, 5);

// 4. 编辑器 input 出现后，找到下拉触发按钮并点击展开选项
// 根据编辑器类型，下拉框可能是一个 ant-select 组件
// 通过坐标查找附近的下拉 trigger
var triggers = document.querySelectorAll('.ant-select-selection__rendered');
for (var i = 0; i < triggers.length; i++) {
  var r = triggers[i].getBoundingClientRect();
  if (Math.abs(r.left - coords.x) < 150 && Math.abs(r.top - coords.y) < 50) {
    triggers[i].click();  // 展开下拉
    break;
  }
}

// 5. 等待下拉选项出现，点击目标选项
// 根据 editor.allOptions 的 label 文本查找
setTimeout(function() {
  var items = document.querySelectorAll('.ant-select-dropdown-menu-item');
  for (var i = 0; i < items.length; i++) {
    if (items[i].textContent.includes('测试企业33338')) {
      items[i].click();
      break;
    }
  }
}, 300);

// 6. 编辑自动确认（下拉选中后 VTable 自动完成编辑）
```

---

#### C. 快速 API 方式（通用工具函数）

```javascript
// 文本编辑
function editTextCellFast(col, row, value) {
  var vt = window._vtable;
  vt.scrollToCell(col, row);
  vt.startEditCell(col, row);
  vt.getEditor(col, row).setValue(value);
  vt.completeEditCell();
  return vt.getCellValue(col, row);
}

// 下拉编辑
function editDropdownCellFast(col, row, targetKey) {
  var vt = window._vtable;
  vt.scrollToCell(col, row);
  vt.startEditCell(col, row);
  vt.getEditor(col, row).setValue(targetKey);
  vt.completeEditCell();
  return vt.getCellValue(col, row);
}
```

## 7. 执行模式

omp 提供 `eval`（持久 Python kernel），Phase 4 直接在 kernel 中逐 cell 运行模板代码，变量跨 cell 保持。`test_cases` 数据在 kernel 中赋值后，直接调用保存逻辑。

---

## 8. 质量管理要求（强制门禁）

### P0 — 阻塞项（必须 100% 通过）

- [ ] 每张用例有可执行的具体步骤
- [ ] 预期结果可观测/可断言
- [ ] 全批用例同时覆盖正向和负向场景
- [ ] 用例编号全局唯一
- [ ] 操作步骤编号从 1 开始连续递增
- [ ] **每条用例前置条件为独立完整描述，无「同上」「同前」「见上文」等指代**
- [ ] **备注列保持为空（骨架用例在备注标注待确认项除外）**
- [ ] 用户已口头确认用例完整
- [ ] 测试类型已标注
### P1 — 重要项（建议修复，不阻塞进入 Phase 4）

- [ ] 每条用例仅覆盖一个独立场景
- [ ] 测试数据具体化
- [ ] 验证点有明确的可验证内容

---

## 9. 异常处理手册

| 异常场景 | 处理策略 |
|---------|---------|
| `openpyxl` 未安装 | `uv add openpyxl` |
| 截图读取失败 | 验证路径 → 用 `inspect_image` 重试 → 让用户手动描述 |
| 浏览器连接失败 | 检查 Chrome 是否以 `--remote-debugging-port=9229` 启动 → 验证 `http://localhost:9229/json` |
| 需求描述不清晰 | 最多 3 轮追问 → 生成骨架用例，备注列标注 `[待确认]` |
| SCM Cookie 过期/失效 | 重新执行 §0-b Step 1~2 获取新 Cookie → 注入 → 重新导航 |
| SCM 模块页面加载为空白/404 | 检查 iframe URL 是否正确（`page.frames()` 列出所有 frame） → 检查侧边栏菜单是否正确点击 |
| 高级搜索弹窗未弹出 | 确认「展开▼」按钮可点击（`btn.textContent.includes('展开')`）→ 如已展开则按钮显示为「收起▲」 |
| 高级搜索下拉选项串扰 | 每次点击下拉后 MUST 先点击弹窗标题关闭再点下一个，否则读取到上一个下拉的缓存数据 |
| 筛选字段 valueType 误判 | 人工复核：text_input 为 `<input>`，select_dropdown 为 `.ant-select`，date_range 为 `.ant-calendar-picker` |
| SCM 侧边栏菜单未展开 | 先点击一级菜单项（`.ant-menu-submenu-title`），等待动画完成，再提取子菜单列表 |
| 用例超过 30 条 | 建议按子功能分批生成导出 |
| 输出目录不存在 | 代码自动创建 |
| 写入权限不足 | 降级到当前工作目录（`.`） |
| VTable 注入失败（`window._vtable` 为 undefined） | 检查 DOM 是否有 `.vtable` 元素 → 检查 iframe 上下文是否正确 → 尝试不同属性名 `vtableInstance/vTable/vtable/VTable` → 降级为截图+视觉分析 |
| VTable 编辑失败（`startEditCell` 不生效） | 确认目标列 `isHasEditorDefine(col)` 为 true → 先 `scrollToCell` 确保可见 → 检查当前是否有其他编辑器未关闭（先 `completeEditCell` 或 `cancelEditCell`） |
| 下拉编辑器 `allOptions` 为空 | 编辑器初始化需异步加载选项 → 先调用 `editor.openDropdown()` 触发加载 → 稍后重试 → 或用 `changeCellValue` 直接设置 |

---

## 10. Excel 生成模板

### 模板设计规范

| 要素 | 内容 |
|------|------|
| Sheet 1 名称 | `测试用例` |
| Sheet 2 名称 | `测试数据` |
| 首行样式 | 加粗 + 蓝色背景 `#4472C4` + 白色字体，居中 |
| 列定义 | 共 18 列（见下表） |
| 多行内容列 | 前置条件/测试步骤/测试数据/预期结果 支持换行 `wrap_text=True`，左对齐 |
| 边框 | 全单元格细线边框 |
| 冻结前 4 列 + 首行 | Sheet 1 `freeze_panes = "E2"`（A~D 列固定，首行固定） |
| 优先级颜色 | P0 红底 `#FFD2D2` 深红字 `#9C0006`；P1 橙底 `#FFE8D0` 深橙字 `#9C6500`；P2 黄底 `#FFF5C0` 深黄字 `#806000`；P3 蓝底 `#DCE6F1` 深蓝字 `#1F497D` |
| 文件命名 | `测试用例_{MODULE_NAME}_{YYYY-MM-DD}.xlsx` |

### 列定义

| 列 | A | B | C | D | E | F | G | H | I | J | K | L | M | N | O | P | Q | R |
|----|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 字段名 | 用例编号 | 用例标题 | 优先级 | 验证点 | 一级模块 | 二级模块 | 测试类型 | 功能 | 前置条件 | 测试步骤 | 测试数据 | 预期结果 | 测试结果 | 执行人 | 执行时间 | 编写人 | 编写时间 | 备注 |
 | 最小列宽 | 18 | 42 | 12 | 42 | 18 | 18 | 18 | 12 | 42 | 42 | 42 | 42 | 10 | 10 | 12 | 12 | 12 | 0 |

### 数据区对齐规则

- **左对齐**：B 用例标题、D 验证点、I 前置条件、J 测试步骤、K 测试数据、L 预期结果
- **居中**：其余列

### 参考实现代码

以下代码直接在 `eval` 中执行（持久 kernel 模式）。按序粘贴运行即可。

```python
# ============================================================
# 可配置变量 — Phase 4 执行时按实际项目修改
# ============================================================
ENTERPRISE_PREFIX = "NB"
DEFAULT_AUTHOR    = "Hooplus1ce"
MODULE_NAME       = "采购管理_物料申请单"
OUTPUT_DIR        = "."
# ============================================================

import os
import sys
from datetime import date

# openpyxl 依赖检查（未安装时提示）
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except ImportError:
    print("openpyxl 未安装，请在 eval 中执行：uv add openpyxl")
    raise

wb = openpyxl.Workbook()

# ===================== Sheet 1: 测试用例 =====================
ws1 = wb.active
ws1.title = "测试用例"

HEADERS_18 = [
    "用例编号", "用例标题", "优先级", "验证点",
    "一级模块", "二级模块", "测试类型", "功能",
    "前置条件", "测试步骤", "测试数据", "预期结果",
    "测试结果", "执行人", "执行时间", "编写人", "编写时间", "备注"
]
ws1.append(HEADERS_18)

header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
header_font = Font(bold=True, color="FFFFFF", size=11, name="微软雅黑")
thin_border = Border(
    left=Side(style="thin"),  right=Side(style="thin"),
    top=Side(style="thin"),   bottom=Side(style="thin")
)

for cell in ws1[1]:
    cell.fill      = header_fill
    cell.font      = header_font
    cell.alignment = Alignment(horizontal="center", vertical="center")
    cell.border    = thin_border

PRIORITY_STYLES = {
    "P0": (PatternFill(start_color="FFD2D2", end_color="FFD2D2", fill_type="solid"),
           Font(bold=True, color="9C0006", size=10)),
    "P1": (PatternFill(start_color="FFE8D0", end_color="FFE8D0", fill_type="solid"),
           Font(bold=True, color="9C6500", size=10)),
    "P2": (PatternFill(start_color="FFF5C0", end_color="FFF5C0", fill_type="solid"),
           Font(bold=True, color="806000", size=10)),
    "P3": (PatternFill(start_color="DCE6F1", end_color="DCE6F1", fill_type="solid"),
           Font(bold=True, color="1F497D", size=10)),
}

def apply_priority_style(cell):
    style = PRIORITY_STYLES.get(cell.value)
    if style:
        cell.fill, cell.font = style

# ============================================================
# test_cases — AI 在 Phase 4 填入实际用例数据
# 格式：每行 18 个字段，对应 HEADERS_18 顺序
# 多行内容用 \n 连接，导出后自动换行显示
# ============================================================
test_cases = [
    # 示例：
    # [
    #     f"{ENTERPRISE_PREFIX}_RKSHR_001",          # 用例编号
    #     "正常收货流程—完整填写必填项提交成功",        # 用例标题
    #     "P1",                                       # 优先级
    #     "填写全部必填字段后提交，单据状态变为已完成且库存正确增加",  # 验证点
    #     "仓储管理", "入库收货", "功能测试", "新增",   # 一级模块~功能
    #     "1. 仓库WH001已启用\n2. 供应商SUP001已激活\n3. 物料MT001已录入\n4. 采购单PO20260618-001剩余量=100",  # 前置条件
    #     "1. 登录系统，进入「仓储管理→入库收货」\n2. 点击「新建」\n3. 选择仓库WH001\n4. 选择供应商SUP001\n5. 添加物料MT001，数量100\n6. 关联采购单PO20260618-001\n7. 点击「提交」",  # 测试步骤
    #     "仓库编码:WH001\n供应商编码:SUP001\n物料编码:MT001\n收货数量:100\n采购单号:PO20260618-001\n采购单剩余量:100",  # 测试数据
    #     "1. 页面显示「操作成功」\n2. 收货单状态变为「已完成」\n3. MT001在WH001库存增加100\n4. 采购单剩余未到货量变为0\n5. 操作日志记录提交人和时间",  # 预期结果
    #     "", "", "", DEFAULT_AUTHOR, date.today().isoformat(), ""  # 测试结果~备注
    # ],
]

for row_data in test_cases:
    ws1.append(row_data)
    row_idx = ws1.max_row
    for cell_idx, cell in enumerate(ws1[row_idx], 1):
        cell.border = thin_border
        if cell_idx in (2, 4, 9, 10, 11, 12):  # B/D/I/J/K/L 列左对齐
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        else:
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    apply_priority_style(ws1.cell(row=row_idx, column=3))  # 优先级列（C 列）

 COL_WIDTHS = [18, 42, 12, 42, 18, 18, 18, 12, 42, 42, 42, 42, 10, 10, 12, 12, 12, 0]
for i, w in enumerate(COL_WIDTHS, 1):
    ws1.column_dimensions[chr(64 + i)].width = w

ws1.freeze_panes = "E2"  # 冻结前 4 列（A~D）和首行

# ===================== Sheet 2: 测试数据 =====================
ws2 = wb.create_sheet(title="测试数据")

section_fill = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
section_font = Font(bold=True, size=11, name="微软雅黑")
header2_font = Font(bold=True, size=10,  name="微软雅黑")

def write_section(ws, start_row, title, headers, data_rows):
    col_count = len(headers)
    title_cell = ws.cell(row=start_row, column=1, value=title)
    title_cell.font      = section_font
    title_cell.fill      = section_fill
    title_cell.alignment = Alignment(vertical="center")
    ws.merge_cells(start_row=start_row, start_column=1,
                   end_row=start_row,   end_column=col_count)
    for c in range(1, col_count + 1):
        cell = ws.cell(row=start_row, column=c)
        cell.fill   = section_fill
        cell.border = thin_border
    hdr_row = start_row + 1
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=hdr_row, column=c, value=h)
        cell.font      = header2_font
        cell.border    = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r_offset, row_data in enumerate(data_rows, 1):
        for c_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=hdr_row + r_offset, column=c_idx, value=val)
            cell.border    = thin_border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    return hdr_row + 1 + len(data_rows) + 2

row = 1
row = write_section(ws2, row,
    "3.1 测试数据配置",
    ["序号", "系统编号", "一级模块", "二级模块", "功能",
     "用例标题", "优先级", "测试类型", "编写人", "编写日期"],
    []
)
row = write_section(ws2, row,
    "3.2 预置基础字段定义",
    ["字段名称", "字段类型", "对应示例数据", "输入格式", "说明"],
    []
)
row = write_section(ws2, row,
    "3.3 测试用例输入设计数据对照表",
    ["用例临时编号", "测试字段", "输入数据值", "字段类型", "预期校验与拦截结果"],
    []
)

# ===================== 保存文件 =====================
filename = f"测试用例_{MODULE_NAME}_{date.today().isoformat()}.xlsx"
os.makedirs(OUTPUT_DIR, exist_ok=True)
filepath = os.path.join(OUTPUT_DIR, filename)

try:
    wb.save(filepath)
    print(f"✅ 已生成: {os.path.abspath(filepath)}")
except PermissionError:
    fallback = os.path.join(".", filename)
    wb.save(fallback)
    print(f"⚠️  写入 {OUTPUT_DIR} 权限不足，已降级保存至: {os.path.abspath(fallback)}")
```

---

## 11. 自检清单

- [ ] `uv add openpyxl` 已执行
- [ ] `browser` 工具可用（Chrome 以 `--remote-debugging-port=9229` 启动）
- [ ] `inspect_image` 已启用（settings 中 `inspect_image.enabled=true`）
- [ ] 用户已确认 `ENTERPRISE_PREFIX` / `DEFAULT_AUTHOR` / `MODULE_NAME`
- [ ] 输出目录有写入权限
