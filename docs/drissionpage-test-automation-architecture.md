# SCM-MOM 测试自动化重构设计

## 1. 现状分析

截至 2026-07-11，项目包含两套实现：

- `mcp-servers/drission-ui`：早期 MCP 服务，当前 pytest 通过 `pythonpath` 实际加载这一套。
- `mcp-servers/drissionpage-mcp`：Codex 与 Claude 配置实际启用的服务，已包含 iframe、表单、弹窗、VTable、网络监听、截图和观察器等增强能力。

两套服务已发生功能分叉：`drissionpage-mcp/server.py` 为 3561 行，`drission-ui/server.py` 为 3114 行。继续双写会让已部署能力、单元测试与文档失去一致性。因此，`drissionpage-mcp` 作为唯一核心实现；`drission-ui` 仅保留兼容入口，不再承载业务逻辑。

已有能力可以完成大部分页面原子操作：`connect`、`browser_tabs`、`get_active_frame`、`click`、`input`、`set_date`、`select_option`、`scan_floats`、`scan_table`、`vtable_action`、`listen_*` 和 `network_record_*`。现有缺口是这些结果彼此独立保存，缺少一个可查询、可回放的执行证据模型；同时项目尚无用例执行编排、Markdown 报告和历史结果比较层。

## 2. 重构目标与边界

目标是建立下面的可追溯闭环：

```text
真实浏览器动作
  -> 页面/元素快照 + 网络请求/响应 + 截图 + 性能数据
  -> 业务流证据包（flow evidence）
  -> 覆盖矩阵和已验证测试用例
  -> 自动执行结果
  -> Markdown 测试报告 / 与基线的回归差异
```

浏览器操作只由 `drissionpage-mcp` 负责；技能只使用结构化工具与证据生成业务语言测试用例；报告与回归层不直接执行页面脚本，也不绕过浏览器改变业务状态。

## 3. 统一数据契约

业务流、截图等浏览器证据保存于 `HL_SHOT_DIR` 下的模块目录；正式用例、执行结果和报告分别保存到项目级 `test_cases/`、`test_results/executions/`、`test_results/reports/`。每个证据包为一个 JSON 文件，包含：

```json
{
  "schema_version": "1.0",
  "flow_id": "唯一标识",
  "module": "模块名称",
  "started_at": "ISO-8601 时间",
  "steps": [
    {
      "sequence": 1,
      "action": {"name": "click", "target": {"locator": "..."}, "input": {}},
      "element": {"text": "...", "locator": "...", "frame": "active"},
      "observation": {"type": "modal", "payload": {}},
      "network": [{"api_target": "...", "status": 200, "request": {}, "response": {}}],
      "artifacts": {"screenshot": "...", "page_model": "..."},
      "performance": {"elapsed_ms": 123},
      "outcome": "passed"
    }
  ]
}
```

`action -> element -> observation -> network -> artifacts` 是元素、数据和业务流程的最小映射单元。敏感头、Cookie、授权字段在写入证据前必须脱敏；请求/响应正文由大小上限保护。

测试用例仍保持技能约定的 19 字段 JSON。要实现可执行回归，允许额外增加 `automation_recipe` 字段；其中每一步使用 MCP 的稳定动作名与结构化参数，不能把裸 JavaScript 或坐标换算写入用例正文。

## 4. 模块职责

| 模块 | 职责 | 输入 | 输出 |
|---|---|---|---|
| `drissionpage-mcp` | 真实浏览器、iframe、表单、浮层、VTable 和网络原子能力 | MCP 参数 | 结构化操作结果 |
| `flow_evidence` | 记录动作、绑定观测/网络/截图、持久化与脱敏 | 原子操作结果 | 证据包 JSON |
| `testcase_generation` | 从页面快照、真实动作和接口证据建立覆盖矩阵，生成并合并 19 字段用例 | 证据包 | 用例 JSON、未覆盖清单 |
| `test_execution` | 回放 `automation_recipe`、收集步骤结果与性能 | 用例集、动作执行器 | 执行结果 JSON |
| `test_reporting` | 汇总通过率、覆盖率、缺陷、证据、性能与回归差异 | 执行结果、基线 | Markdown 报告 |
| `drission-ui` | 兼容旧命令与旧配置 | 旧入口 | 转发到唯一核心 |

## 5. 工作流与门禁

1. 就绪门禁：`connect -> browser_tabs -> check_session -> refresh_session(必要时) -> check_session -> get_active_frame`。最终会话或 iframe 无效时停止。
2. 启动一次业务流记录，同时启动页面快照和网络记录。
3. 每个可交互动作遵循 `observe_start -> action -> observe_wait`，并向证据包写入元素、观察、网络、截图和耗时。
4. 基于页面快照及步骤/接口证据生成资产清单和覆盖矩阵。只有状态为“已验证”的场景可生成正式用例；其他场景作为缺口保留。多个业务流通过 `combine_test_case_files` 去重合并。
5. 用例执行器只回放显式 `automation_recipe`，支持运行期引用动态表格行；每步记录实际数据、当次截图、性能和结构化失败原因。破坏性流必须显式声明并提供始终执行的清理段。
6. 报告生成器输出需求场景、风险维度、页面/接口资产三类覆盖率，以及缺陷、P50/P95、当次证据和完整基线差异。

## 6. 实施顺序与验收

1. 统一 MCP 核心及测试加载路径。
   验收：同一套服务同时被 MCP 配置和 pytest 测试。
2. 实现证据包、数据脱敏、流记录 MCP 工具，并接入结构化探索动作。
   验收：模拟动作可产出含元素、网络、截图、耗时的 JSON；敏感字段不落盘。
3. 实现覆盖矩阵和证据驱动的 19 字段用例候选生成。
   验收：未验证场景不会被标记为正式用例，且每个用例能追溯到 evidence step。
4. 实现配方回放、Markdown 测试报告与基线差异。
   验收：模拟执行可生成通过率、缺陷、覆盖率、性能、证据链接和回归差异。
5. 在已登录 SCM-MOM 浏览器上执行代表性真实业务路径。
   验收：编辑后恢复、必填拦截和已授权的删除缺陷复现均可从证据生成用例并独立回放；执行报告引用本轮证据并诚实列出模块级覆盖缺口。

## 7. 风险控制

- 不自动执行删除、提交、审批等破坏性操作；配方必须显式声明、由调用方确认，并提供确定性清理或状态恢复核验。
- 不将用户名、密码、Cookie、Authorization、Token 或超大响应正文写入证据文件。
- VTable 继续只通过 MCP façade 操作，技能和用例正文不暴露私有 DOM、JavaScript 或坐标细节。
- 回归基线不可被当前失败结果自动覆盖，避免将缺陷固化为预期。
- 已知缺陷复现不得反转业务 oracle 后计为正常通过；使用 `xfailed` 独立统计。若修复后的成功分支无法确定性恢复夹具，则只保留缺陷证据并从正式回归套件排除。
