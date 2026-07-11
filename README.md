# WX 文档格式转换

将 Markdown 或 DOCX 文档转换为模板驱动的 WX 格式 `.docx` 文件。

## 核心功能

- 源文档无论格式多乱，标题、列表、表格、题注自动识别重排
- 图片、表格完整保留，自动编号，输出样式与模板完全一致
- 可选 LLM 增强：功能点列表识别、表格题注自动生成

## 一键安装

对任何支持 skill 的 Agent 说：

> 从 GitHub 仓库 mh567/wx-doc-format-skill 安装 wx-doc-format skill

更新时重复上述指令即可。

## 使用方法

触发词：**wx文档格式**

```
请把 xx.md 转换为 wx 文档格式
```

启用 LLM 增强：

```
请把 xx.docx 转换为 wx 文档格式，启用 LLM 增强
```

详细架构见 `SKILL.md`。
