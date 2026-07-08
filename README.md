<!-- sheeep-source
{
  "version": 1,
  "type": "page",
  "title": "TestAgent",
  "icon": {
    "type": "emoji",
    "value": "🐑"
  },
  "cover": null,
  "created": "2026-07-08T14:52:25.725Z",
  "modified": "2026-07-08T14:52:25.725Z",
  "blocks": [
    {
      "id": "md-heading-1",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 1
      },
      "content": [
        {
          "type": "text",
          "text": "TestAgent",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-paragraph-2",
      "type": "paragraph",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "基于 MCP（Model Context Protocol）+ DrissionPage 的企业系统（WMS/MOM/ERP）AI 驱动自动化测试工具集。",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-heading-3",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 2
      },
      "content": [
        {
          "type": "text",
          "text": "组成",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-bullet-4",
      "type": "bulletListItem",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "mcp-servers/drission-ui/ — drission-ui MCP 服务器：把 DrissionPage 浏览器自动化封装成一组精简的结构化 MCP 工具，供 AI 驱动的 UI 测试技能调用。详见 mcp-servers/drission-ui/README.md。",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-bullet-5",
      "type": "bulletListItem",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": ".claude/skills/ — 测试用例生成技能（test-case-generator-dp、test-case-generator-optimized）。",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-bullet-6",
      "type": "bulletListItem",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "tests/ — 单元测试。",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-heading-7",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 2
      },
      "content": [
        {
          "type": "text",
          "text": "快速开始",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-paragraph-8",
      "type": "paragraph",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "完整项目使用流程见 项目使用说明.md。",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-paragraph-9",
      "type": "paragraph",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "powershell",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-heading-10",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 1
      },
      "content": [
        {
          "type": "text",
          "text": "1. 安装依赖",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-paragraph-11",
      "type": "paragraph",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "uv sync",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-heading-12",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 1
      },
      "content": [
        {
          "type": "text",
          "text": "2. 以远程调试端口启动 Chrome",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-paragraph-13",
      "type": "paragraph",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "chrome --remote-debugging-port=9222",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-heading-14",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 1
      },
      "content": [
        {
          "type": "text",
          "text": "3. 注册 MCP 服务器（项目根 .mcp.json 已配置）",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-heading-15",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 1
      },
      "content": [
        {
          "type": "text",
          "text": "Claude Code 会自动加载",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-heading-16",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 1
      },
      "content": [
        {
          "type": "text",
          "text": "4. 跑单元测试",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-paragraph-17",
      "type": "paragraph",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "uv run pytest tests/ -v",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-heading-18",
      "type": "heading",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left",
        "level": 2
      },
      "content": [
        {
          "type": "text",
          "text": "配置",
          "styles": {}
        }
      ],
      "children": []
    },
    {
      "id": "md-paragraph-19",
      "type": "paragraph",
      "props": {
        "textColor": "default",
        "backgroundColor": "default",
        "textAlignment": "left"
      },
      "content": [
        {
          "type": "text",
          "text": "通过环境变量覆盖默认配置（URL/域名/端口等），详见 mcp-servers/drission-ui/README.md。",
          "styles": {}
        }
      ],
      "children": []
    }
  ],
  "comments": []
}
-->

> This README is auto-generated by Sheeep from the embedded source block.
> Edit it in the Sheeep editor, not in GitHub or a plain text editor.

# TestAgent

# TestAgent

基于 MCP（Model Context Protocol）+ DrissionPage 的企业系统（WMS/MOM/ERP）AI 驱动自动化测试工具集。

## 组成

- mcp-servers/drission-ui/ — drission-ui MCP 服务器：把 DrissionPage 浏览器自动化封装成一组精简的结构化 MCP 工具，供 AI 驱动的 UI 测试技能调用。详见 mcp-servers/drission-ui/README.md。

- .claude/skills/ — 测试用例生成技能（test-case-generator-dp、test-case-generator-optimized）。

- tests/ — 单元测试。

## 快速开始

完整项目使用流程见 项目使用说明.md。

powershell

# 1. 安装依赖

uv sync

# 2. 以远程调试端口启动 Chrome

chrome --remote-debugging-port=9222

# 3. 注册 MCP 服务器（项目根 .mcp.json 已配置）

# Claude Code 会自动加载

# 4. 跑单元测试

uv run pytest tests/ -v

## 配置

通过环境变量覆盖默认配置（URL/域名/端口等），详见 mcp-servers/drission-ui/README.md。
