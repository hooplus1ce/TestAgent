---
name: vtable-dropdown-search-strategy
description: VTable表格下拉框搜索策略：点击VTable单元格后，优先搜索virtual-option而非假设是Ant Design组件
metadata:
  type: reference
---

# VTable 下拉框搜索策略

## 问题总结

点击VTable单元格后,交互产生的下拉选项不一定是Ant Design组件,可能是VTable自定义组件。

## 核心教训

❌ **错误**: 假设所有下拉都是 Ant Design 的 `.ant-select-dropdown-menu-item`

✅ **正确**: 优先搜索与VTable相关的元素

## 搜索顺序(MUST)

1. **第一步**: `.virtual-option` - VTable自定义下拉选项
2. **第二步**: 包含 `virtual` 的class
3. **第三步**: 包含 `option` 的class
4. **第四步(最后)**: Ant Design组件

## 快速定位技巧

```javascript
// 优先搜 virtual-option
document.querySelectorAll('.virtual-option')

// 如果无结果,按文本特征搜索(如商品编码:数字+连字符)
[...document.querySelectorAll('*')].filter(el => 
  el.textContent.trim().match(/\d+\.\d+\.\d+/) ||
  el.textContent.trim().match(/\d+-\d+-\d+/)
)

// 再无结果,最后才搜 Ant Design
document.querySelectorAll('.ant-select-dropdown-menu-item,.ant-select-item-option')
```

## 典型场景

| 场景 | 正确class | 说明 |
|-----|----------|-----|
| 商品编码选择 | `virtual-option` | 有完整可见坐标 (真实DOM元素 |
| 其他选择型字段 | 同上 | 同上 |

## Python定位示例

```python
# 点击后立即搜索
result = run_js("""
#   var opts = document.querySelectorAll('.virtual-option');
#   return {
#     count: opts.length,
#     visible: [...opts].filter(o => o.offsetParent !== null).map(o => o.textContent.trim())
#   };
# """)

# 找到后直接点击
click(locator="x://div[@class='virtual-option' and contains(text(), '7001-0687-01')]")
```

## 参考
- [vtable-interaction.md (相关)
- [SCM-MOM系统操作手册.md (业务场景)]
