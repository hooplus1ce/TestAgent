# Playwright MCP 借鉴分析与改进方案

## 一、当前项目 vs Playwright MCP 对比

| 维度 | 当前项目（drission-ui） | Playwright MCP | 差距 |
|------|---------------------|---------------|-----|
| **工具组织** | 平铺式注册，无分组 | 分层能力分组（`--caps`） | ⭐⭐⭐⭐ 大 |
| **参数设计** | locator 直接定位 | `target`+`element` 双参数，权限+执行分离 | ⭐⭐⭐ 中 |
| **输出重定向** | 无 | `filename` 参数分流大输出 | ⭐⭐⭐⭐ 大 |
| **会话模式** | 单一接管模式 | Persistent/Isolated/Extension 三种模式 | ⭐⭐⭐⭐ 大 |
| **配置系统** | 仅环境变量 | CLI → ENV → Config File 三级合并 | ⭐⭐⭐ 中 |
| **安全边界** | 无声明 | 明确声明安全边界 + 多层防护 | ⭐⭐⭐ 中 |
| **Workspace 隔离** | 无 | hash-based 配置路径隔离 | ⭐⭐ 小 |
| **类型化 Schema** | FastMCP 自动生成 | 完善的 TypeScript 类型定义 | ⭐⭐ 小 |

---

## 二、Playwright MCP 优秀设计详解

### 1. 能力分层（Capability Tiers）

**核心设计**：通过 `--caps` 参数控制工具暴露范围，避免所有工具一次性加载到 LLM 上下文。

```
--caps=vision     # 启用坐标操作
--caps=network    # 启用网络拦截
--caps=storage    # 启用存储操作
--caps=testing    # 启用测试断言
```

**借鉴理由**：
- 我们当前有 40+ 工具，全部暴露消耗大量 token
- 可按场景分组：`core`、`vtable`、`network`、`storage`、`devtools`

---

### 2. 输出重定向模式

**设计模式**：工具支持可选 `filename` 参数，当提供时写入文件而非返回文本。

```python
# Playwright MCP 风格
def browser_snapshot(filename: str = None):
    result = snapshot()
    if filename:
        with open(filename, 'w') as f:
            f.write(result)
        return {"ok": True, "saved_to": filename}
    return {"ok": True, "snapshot": result}  # 仅当无 filename 时返回
```

**借鉴理由**：
- 我们的 `dom_tree`、`scan_table`、`get_table_data` 返回大量数据
- 避免 LLM 上下文被大型响应填满

---

### 3. 配置三级合并

```
CLI 参数 (最高优先级)
    ↓
环境变量 (PLAYWRIGHT_MCP_*)
    ↓
配置文件 (JSON/YAML)
    ↓
默认值 (最低优先级)
```

**借鉴理由**：
- 当前仅支持环境变量，不够灵活
- 开发/测试/生产场景需要不同配置

---

### 4. 引用+描述双参数

```python
# Playwright MCP 风格
def browser_click(
    target: str,          # 快照引用或选择器（执行用）
    element: str = None,  # 人类可读描述（权限/意图声明用）
):
    pass
```

**设计精妙**：强制 LLM 先声明意图，再执行操作，减少盲操作风险。

---

### 5. 会话模式三选一

| 模式 | 持久性 | 适用场景 |
|------|-------|---------|
| **Persistent**（默认） | 磁盘持久化，workspace 隔离 | 日常自动化，保留登录态 |
| **Isolated**（`--isolated`） | 内存仅，关闭销毁 | 测试、多客户端并发 |
| **Extension** | 连接已有浏览器 | 复用用户真实会话 |

---

### 6. 诚实标注安全边界

```
⚠️ Playwright MCP is NOT a security boundary
   - `allowedHosts` 是便利措施，不是安全边界
   - `allowUnrestrictedFileAccess` 同理
   - 高风险工具命名含 `unsafe`
```

**借鉴理由**：管理用户预期，明确告知什么安全什么不安全。

---

## 三、我们的改进方案（按优先级）

### P0 - 立即实施（高价值低投入）

#### 1. 工具输出重定向

**改动文件**：`server.py`

**目标工具**：
- `dom_tree` - 已有部分支持，完善
- `scan_table` - 新增
- `get_table_data` - 新增
- `scan_page_elements` - 新增

**实现**：
```python
@mcp.tool()
@read_synchronized
def dom_tree(
    selector: str = "", 
    max_depth: int = 6, 
    max_children: int = 50,
    text: bool = False, 
    text_limit: int = 100, 
    show_hidden: bool = False,
    filename: str = None,  # 新增
    save_path: str = "", 
    save_format: str = "yml", 
    max_chars: int = 8000
):
    # ... 现有逻辑 ...
    
    result = {"ok": True, "save_format": save_format, ...}
    
    if filename:  # 优先使用 filename，输出到文件
        full_path = os.path.join(config.SHOT_DIR, filename)
        os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(result.get("tree", ""))
        return {"ok": True, "saved_to": full_path}
    
    # ... 原返回逻辑 ...
```

---

#### 2. 引入能力分组（Caps）

**改动文件**：`server.py`、新增 `caps.py`

**分组设计**：
```python
# caps.py
CAP_GROUPS = {
    "core": [
        "connect", "refresh_session", "check_session",
        "enter_module", "get_active_frame",
        "click", "input", "hover",
        "scan_page_elements", "find_elements", "find_batch",
        "screenshot", "dom_tree",
        "close_modal",
    ],
    "vtable": [  # canvas 表格相关
        "scan_table", "get_table_values", "get_table_data",
        "click_table_cell", "hover_table_cell", "resize_table_column",
    ],
    "filter": [  # 筛选区相关
        "scan_filter_fields", "select_date_range",
    ],
    "observe": [  # 观察相关
        "observe_start", "observe_wait",
    ],
    "network": [  # 网络监听相关
        "listen_start", "listen_wait", "listen_stop",
        "listen_ws_start", "listen_ws_wait",
    ],
    "storage": [  # 存储/上下文相关
        "new_context", "switch_context", "list_contexts",
        "set_permission",
    ],
    "devtools": [  # 调试/高级功能
        "run_js", "mouse_trail", "download_by_browser",
    ],
}

ENABLED_CAPS = set(os.environ.get("DRISSION_UI_CAPS", "core,vtable,filter").split(","))

def is_tool_enabled(tool_name: str) -> bool:
    if "all" in ENABLED_CAPS:
        return True
    for cap, tools in CAP_GROUPS.items():
        if cap in ENABLED_CAPS and tool_name in tools:
            return True
    return False
```

**在 server.py 中集成**：
```python
# 动态决定哪些工具注册
for name, func in list(globals().items()):
    if hasattr(func, '_mcp_tool') and not is_tool_enabled(name):
        continue  # 跳过未启用的工具
```

---

### P1 - 短期实施（价值中等，投入可控）

#### 3. 配置三级合并

**改动文件**：重构 `config.py` → `config/__init__.py` + `config/schema.py`

**配置层级**：
```python
# 优先级：CLI 占位（未来）→ ENV → 配置文件 → 默认值

# 支持 DRISSION_UI_CONFIG=/path/to/config.json
CONFIG_PATH = os.environ.get("DRISSION_UI_CONFIG", "")

# 配置文件 Schema（JSON/YAML）
CONFIG_SCHEMA = {
    "browser": {
        "port": int,
        "target_hint": str,
        "headless": bool,
        "user_data_dir": str,  # 新增：持久化路径
    },
    "scm": {
        "url": str,
        "base_url": str,
        "login_page": str,
        "username": str,
        "password": str,
        "cookie_domain": str,
        "access_domain": str,
    },
    "paths": {
        "shot_dir": str,
        "chrome_path": str,
    },
    "caps": list[str],  # 能力分组
}
```

---

#### 4. Workspace 级配置隔离

**设计**：
```python
# 根据当前工作目录计算 hash，隔离不同项目的配置
import hashlib

def get_workspace_hash() -> str:
    cwd = os.getcwd()
    return hashlib.md5(cwd.encode()).hexdigest()[:8]

# 用户数据目录包含 workspace hash
def get_user_data_dir() -> str:
    base = os.environ.get("DRISSION_UI_USER_DATA_DIR", "")
    if base:
        return base
    # 默认路径：~/.drission-ui/workspace-{hash}
    hash = get_workspace_hash()
    return os.path.join(os.path.expanduser("~"), ".drission-ui", f"workspace-{hash}")
```

---

### P2 - 中期实施（高价值高投入）

#### 5. 会话模式支持

**模式设计**：
| 模式 | 说明 |
|------|-----|
| `takeover`（默认） | 当前行为：接管 9222 端口 Chrome |
| `persistent` | 启动/使用持久化配置文件的 Chrome |
| `isolated` | 临时配置文件，关闭即销毁 |

**新增工具**：
```python
@mcp.tool()
def browser_get_config() -> dict:
    """返回当前生效配置（用于调试）"""
    return {"ok": True, "config": get_current_config()}

@mcp.tool()
def browser_storage_state(filename: str = None) -> dict:
    """导出当前会话存储状态（cookies + localStorage）"""
    tab = get_tab()
    cookies = tab.cookies()
    # ... 导出 localStorage ...
    state = {"cookies": cookies, "localStorage": ...}
    # ... 存文件或返回 ...
    return {"ok": True, "storage_state": state}

@mcp.tool()
def browser_set_storage_state(storage_state: dict | str) -> dict:
    """从文件或字典恢复存储状态"""
    pass
```

---

#### 6. 语义化安全标记与边界声明

**文档改进**：
```python
# 新增 SECURITY.md
⚠️ drission-ui MCP 安全边界声明

本工具并非安全边界。以下措施为便利性质，非安全保证：

- `--allowed-hosts` - 防止意外访问，非 CORS 保护
- 环境变量中的密码 - 仅为方便配置，非安全存储

高风险工具命名含 "unsafe"（未来）：
- `browser_run_js_unsafe` - 替代当前 `run_js`，明确警示
```

---

## 四、迁移路线图

### 阶段 1：输出重定向 + 能力分组（1-2 周）
- [ ] 为 4 个大数据工具添加 `filename` 参数
- [ ] 定义 CAP_GROUPS 并实现动态注册
- [ ] 添加 `DRISSION_UI_CAPS` 环境变量支持

### 阶段 2：配置系统升级（2-3 周）
- [ ] 重构 config 模块，支持配置文件
- [ ] 实现三级合并逻辑
- [ ] 添加 `browser_get_config` 工具

### 阶段 3：会话模式扩展（3-4 周）
- [ ] 实现 persistent/isolated 模式
- [ ] 添加存储状态导入导出
- [ ] Workspace 级配置隔离

---

## 五、文件清单（新增/修改）

### 新增文件
```
docs/
├── playwright-mcp-借鉴分析.md          # 本文档
├── SECURITY.md                         # 安全边界声明

mcp-servers/drission-ui/
├── caps.py                             # 能力分组定义
├── config/
│   ├── __init__.py                     # 重构配置模块
│   └── schema.py                       # 配置 Schema
└── migrations/                         # 迁移脚本（如有）

configs/
└── drission-ui.example.json            # 配置文件示例
```

### 修改文件
```
mcp-servers/drission-ui/
├── server.py          # 添加 filename、cap 过滤
├── config.py          # 重构（或删除，迁移到 config/）
├── browser_session.py # 支持多会话模式
└── README.md          # 更新文档
```

---

## 六、总结

Playwright MCP 的核心优势不在于工具数量，而在于：

1. **Token 效率优先** - 能力分组 + 输出重定向，避免浪费 LLM 上下文
2. **明确的安全边界声明** - 诚实告知用户什么安全什么不安全
3. **灵活的配置系统** - 三级合并 + Workspace 隔离
4. **渐进式能力暴露** - 按需启用，不一次性加载所有工具

我们的改进应聚焦于这几点，而非盲目模仿 Playwright 的所有功能。
