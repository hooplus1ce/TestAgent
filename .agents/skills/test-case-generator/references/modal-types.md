# 弹窗类型参考（SCM 系统）

系统页面中每点击一个按钮后，MUST 先检测是否有弹窗弹出，判断其类型后再决定下一步处理。

## 检测优先级

每次点击操作后，按以下顺序检测：

1. iframe 内检测交互弹窗 / 消息提醒 → 无则
2. top 层（主页面）检测系统级确认弹窗 → 无则
3. 正常继续

原因：业务操作的弹窗（交互弹窗、消息提醒）都在 iframe 内部。top 层的系统级确认弹窗由 session/权限等全局事件触发，iframe 内无法感知。

## 三种弹窗类型

### 1. 交互弹窗 / 业务确认弹窗

| 属性 | 值 |
|------|-----|
| 检测位置 | iframe 内 |
| CSS 选择器 | `.ant-modal-content`（含 `.ant-confirm-body-wrapper`） |
| 特征 | 有标题 `.ant-modal-title` / `.ant-confirm-title` + 内容区 + 操作按钮（确定/取消/关闭×） |
| 处理方式 | DFS 探索弹窗内所有按钮和字段后，点击关闭×或取消返回 |

**判断逻辑**：iframe 内的 `.ant-modal-content` **全部**视为业务弹窗（无论是否包含 `.ant-confirm-body-wrapper`）。只有 top 层的 `.ant-confirm` 才视为系统级确认弹窗。
### 2. 消息提醒

| 属性 | 值 |
|------|-----|
| 检测位置 | iframe 内 |
| CSS 选择器 | `.ant-notification-notice` / `.ant-message-notice` |
| 特征 | 只有文字提示（如「请勾选一项」「正在导出，请稍等...」），无业务操作按钮 |
| 处理方式 | 提取文字内容后关闭或等待自动消失 |

### 3. 系统级确认弹窗

| 属性 | 值 |
|------|-----|
| 检测位置 | top 层主页面 |
| CSS 选择器 | `.ant-modal-content:has(.ant-confirm-body-wrapper)` |
| 特征 | 警告图标 + 文字提示 + 单一操作按钮（如「登录」），出现在主页面顶层 |
| 处理方式 | 按需点击确认/登录按钮处理，或记录后关闭 |

## 弹窗内可交互元素的识别

检测到交互弹窗后，需识别其中哪些元素可点击。使用组合规则：

```
可点击判定 =
  ① 原生交互标签 (button / a / select / textarea / input[type=checkbox/radio/submit] / [role="button"] / [onclick])
  ② OR 计算样式 cursor === 'pointer'（捕获样式驱动的可点击元素，如 label、绑了 click 的 div/span）
```

识别出候选元素后，MUST 检查 className 确认其语义：

| className 关键词 | 实际功能 | 容易误判为 |
|-----------------|---------|-----------|
| `ant-table-row-expand-icon` | 行展开/收起按钮 | 排序图标 |
| `ant-select` | 下拉选择器 | — |
| `ant-calendar-picker` / `ant-picker` | 日期选择器 | — |
| `ant-checkbox` / `ant-radio` | 复选框/单选框 | — |
| `ant-btn` | 按钮 | — |

## 检测代码模板

```javascript
// Step 1: iframe 内检测
var f = page.frames().find(function(fr){ return fr.url().includes('makerTable'); });
var modalInfo = await f.evaluate(function(){
  var modal = document.querySelector('.ant-modal-content');
  if (modal && modal.offsetParent !== null) {
    var isConfirm = !!modal.querySelector('.ant-confirm-body-wrapper');
    return {
      type: isConfirm ? 'business_confirm' : 'interactive',
      title: (modal.querySelector('.ant-modal-title') || modal.querySelector('.ant-confirm-title'))?.textContent?.trim() || '',
      content: modal.querySelector('.ant-confirm-content')?.textContent?.trim().replace(/\s+/g, ' ') || '',
      buttons: [...modal.querySelectorAll('.ant-btn, button')].filter(function(b){ return b.offsetParent !== null; }).map(function(b){ return b.textContent.trim().replace(/\s+/g, ''); }),
      hasClose: !!modal.querySelector('.ant-modal-close')
    };
  }
  // 消息提醒 - notification
  var notif = document.querySelector('.ant-notification-notice');
  if (notif && notif.offsetParent !== null) {
    return { type: 'notification', message: notif.querySelector('.ant-notification-notice-message')?.textContent?.trim() || '' };
  }
  // 消息提醒 - message
  var msg = document.querySelector('.ant-message-notice');
  if (msg && msg.offsetParent !== null) {
    return { type: 'message', text: msg.textContent.trim().substring(0, 100) };
  }
  return { type: 'none' };
});

// Step 2: top 层检测系统级确认弹窗（iframe 内无弹窗时）
if (modalInfo.type === 'none') {
  var topModal = await page.evaluate(function(){
    var m = document.querySelector('.ant-modal-content');
    if (m && m.offsetParent !== null && m.querySelector('.ant-confirm-body-wrapper')) {
      return {
        type: 'system_confirm',
        message: m.querySelector('.ant-confirm-body')?.textContent?.trim().replace(/\s+/g, ' ') || '',
        buttons: [...m.querySelectorAll('.ant-btn, button')].filter(function(b){ return b.offsetParent !== null; }).map(function(b){ return b.textContent.trim().replace(/\s+/g, ''); })
      };
    }
    return { type: 'none' };
  });
  if (topModal.type === 'system_confirm') {
    // 处理 session 超时：见 scripts/scm-login.js -> refreshSession()
  }
}
```

## 各类型处理逻辑

| 类型 | 检测到后做什么 |
|------|--------------|
| **交互弹窗** | 1. 记录弹窗标题和内容 → 2. DFS 探索弹窗内所有下拉字段、输入框、子按钮 → 3. 测试弹窗「取消」和「关闭×」行为 → 4. 关闭后回到列表页 |
| **业务确认弹窗** | 1. 记录弹窗标题、提示文字、按钮 → 2. 点击「取消」或关闭× → 弹窗关闭，数据无变化 → 3. 点击「确定」→ 执行操作，表格刷新 |
| **消息提醒** | 1. 提取消息文字写入预期结果 → 2. 点击关闭×或等待自动消失 → 3. 继续下一步操作 |
| **系统级确认弹窗** | 1. 记录弹窗提示文字 → 2. 通过 CDP Cookie 注入刷新 session（见 scripts/scm-login.js） → 3. 刷新页面后继续操作 → 4. 在用例备注标注「需重新登录」 |

## 弹窗 / 通知关闭规则（MUST）

每次交互操作后，无论弹窗（`.ant-modal-content`）、通知（`.ant-notification-notice`）还是消息（`.ant-message-notice`），在提取完必要数据后 **必须关闭，不得残留**：

```javascript
// 关闭弹窗：点击关闭×或取消按钮，禁止用 .remove() 绕过
var modal = document.querySelector('.ant-modal-content');
if (modal && modal.offsetParent !== null) {
  var closeBtn = modal.querySelector('.ant-modal-close') ||
                 [...modal.querySelectorAll('button.ant-btn')].find(function(b){ return /取消|返回/.test(b.textContent.trim()); }) ||
                 [...modal.querySelectorAll('button.ant-btn')].find(function(b){ return b.textContent.trim() === '取消'; });
  if (closeBtn) closeBtn.click();
}

// 关闭通知提醒（需手动点击×关闭）
var notif = document.querySelector('.ant-notification-notice');
if (notif && notif.offsetParent !== null) {
  var nClose = notif.querySelector('.ant-notification-notice-close');
  if (nClose) nClose.click();
}

// 消息提醒（ant-message）几秒后自动消失，可不清除
```

> ⚠️ 原代码用 jQuery 伪选择器 `button.ant-btn:contains(取消)`，在 `evaluate` 中不可用，已改为等价的 `querySelectorAll` + 文本匹配。

**禁止**使用 `el.remove()` 或 `el.parentNode.removeChild(el)` 直接移除 DOM 节点——必须模拟真实关闭交互，否则 React 状态不同步。
