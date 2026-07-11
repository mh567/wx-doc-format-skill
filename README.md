# WX 文档格式转换

将 Markdown 或 DOCX 文档转换为模板驱动的 WX 格式 `.docx` 文件。

## 核心功能

- 理解文档语义：自动识别标题层级、列表、表格、题注，无论源文档格式如何
- 三段式流水线：解析 → 规范化 → 模板渲染，输出样式严格来自模板
- 可选 LLM 增强：自动修正功能点列表、章节层级、表格题注
- 审计报告：`unexpected_styles` 必须为空，结构风险一目了然

## 一键安装

对任何支持 skill 的 Agent 说：

> 从 GitHub 仓库 mh567/wx-doc-format-skill 安装 wx-doc-format skill

## 更新

重复安装指令即可，Agent 自动比对版本并拉取最新代码。

## 使用方法

触发词：**wx文档格式**

```
请把 xx.md 转换为 wx 文档格式
```

使用 LLM 增强：

```
请把 xx.docx 转换为 wx 文档格式，启用 LLM 增强
```

详细架构和完整 CLI 用法见 `SKILL.md`。
