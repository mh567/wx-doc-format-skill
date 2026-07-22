# AGENTS.md — wx-doc-format-skill

## 编码偏好

- 不出现"不是...，而是"的句式
- 不出现破折号
- 不随意重命名已稳定的接口

## 项目架构

三段式流水线：解析 → 规范化 → 模板渲染。详见 SKILL.md。

缺陷修复优先放在解析和 source AST 生成阶段。规范化、渲染和审计消费明确的 AST 语义并进行验证，finalizer 不承担可以前置的语义修补。

LLM 增强为 Capability 插拔架构：
- `list_detect` — 功能点列表识别
- `caption_gen` — 表格/图片题注生成
- 新增能力只需注册 `CapabilityConfig`

## 关键决策记录

### 1. 列表编号统一为一级
- 所有有编号的列项统一使用一级列表样式（a) b) c) 格式）
- 每章节遇到标题后从 a) 重新开始

### 2. 图片段落不继承任何样式
- pPr 只保留 jc=center

### 3. 题注用 SEQ 域代码
- SEQ Table 和 SEQ Figure 分开编号
- 表题注在表格上方，图题注在图片下方

### 4. API 文档特殊处理
- API 标签不识别为标题
- API 示例表格不插入题注

### 5. 排版格式
- 普通语义表格单元格使用 表正文 样式
- 普通语义表格使用全框线、`autofit` 和表级默认单元格边距
- 单元格不写入独立 `tcMar`，WPS 中继承整张表格的默认边距
- 公式编号容器等 `layout` 表在解析阶段确定角色，保留源宽度、边距、行高、无框线和段落格式

### 6. 目录
- 源文档自带目录被自动跳过
- 输出以模板自动生成的目录为准

### 7. 附录
- 附录段落清空正文，由模板 auto-numbering 显示编号

## 输出验证不变量

- unexpected_styles 必须为空
- 标题文本不含手工编号
- 普通语义表格单元格全部为 表正文
- `layout` 表不进入普通表格格式合同
- 列表每章节重启

## 多 Agent 协作分工

- Hermes = 总控：路由、任务管理、Git、测试、汇总
- Codex = 设计/审查：方案设计、任务拆分、代码审查
- Claude Code = 实现：按任务文件编码、测试

硬性规则：改 .py 前必须先声明路径（→Codex 设计 / →Claude 实现 / →Claude+Codex 审查）。

## 仓库管理

- 公开 mh567/wx-doc-format-skill — main（纯 skill）
- 私有 mh567/wx-doc-format-skill-dev — main（含全部开发文件）
- 本地 main → 公开，dev → 私有
- 发布用 git cherry-pick，严禁 merge dev→main

## 常用命令

```bash
cd scripts && .venv/bin/python3 -m main \
  --input ../input/xxx.docx \
  --output ../wx_output/xxx.docx \
  --template ../assets/技术文件格式及书写要求.docx \
  --report ../wx_output/report.json

.venv/bin/python3 -m pytest tests/ -q
```
