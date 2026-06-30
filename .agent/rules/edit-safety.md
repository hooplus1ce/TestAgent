# 编辑安全 — 区间行号确认铁律

## 背景

2026-06-30: 因未 re-read 确认行号就执行 `DEL`，误删了 SKILL.md 中重要的 §4.2 iframe 节。

## 规则

在同一 snapshot tag 上执行过 `INS.PRE` / `INS.POST` / `INS.HEAD` / `INS.TAIL` 等改变行数的操作后，**再次执行 `DEL` / `SWAP` / `SWAP.BLK` 前，必须先 `read` 确认当前的最新行号**。

```
第一刀: INS.POST 180:          ← 插入了 N 行，行号后移
         └─ 此时 tag 仍有效，但 180 之后的实际行号已 +N
第二刀: DEL 201-207            ← ❌ 凭记忆下刀，实际内容与预期不符
         └─ 必须先 read 确认当前哪几行是目标内容
```

## 原因

- `INS.*` 操作不改变 snapshot tag，只改变文件内容
- 后续 `DEL` / `SWAP` 的区间行号基于**当前（已插入后）的文件状态**，不是基于第一次 read 时的状态
- 不 re-read 就下刀 = 用旧地图找新路

## 例外

如果上一次操作后立即收到了包含新 tag 的 edit 响应（`apply 后返回新 #TAG`），且直接引用该 tag 内的行号，则无需额外 read。
