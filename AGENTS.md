# AGENTS.md — wx-doc-format-skill

## 编码偏好

- 不要出现"不是...，而是"的句式
- 不要出现破折号
- 搜索时尽量不使用中文网站信源
- 类/函数命名已确定，不随意重命名或调整已稳定的接口

## 项目架构

三段式流水线：解析 → 规范化 → 模板渲染。详见 SKILL.md。

## 关键决策记录

### 1. 列表编号统一为一级
- 所有有编号的列项统一使用一级列表样式 `1.1一级列项-编号`（`a) b) c)` 格式）
- 每个章节遇到标题后从 `a)` 重新开始
- 列表键改为 `(0, "letter")` 而非 `(level, kind)`

### 2. 图片段落不继承任何样式
- pPr 只保留 `jc=center`，不写 pStyle
- wp:inline 的 distL/distR 归零
- 图片段落使用 No Spacing 或空 pStyle 避免继承 Normal 的 firstLine=640

### 3. 题注用 SEQ 域代码
- `SEQ Table` 和 `SEQ Figure` 分开编号
- 题注前缀 `表 ` / `图 ` 在 finalizer 的 normalize_caption_prefixes 中添加
- `caption_parts` 中 `图表X` 格式返回空 caption_text

### 4. API 文档特殊处理
- API 标签（请求参数/返回示例等）不识别为标题
- 单格 JSON/HTTP/Plain Text 表格不插入题注、不套用表正文
- `looks_like_api_example_table` 识别条件包括 `{/"/PLAIN TEXT` 等关键词

### 5. 标题推断借鉴旧版逻辑
- `looks_like_visual_heading`：mostly_bold or centered or max_size >= 14
- `is_compact_function_heading_text`：≤24字、无标点、排除特定前缀
- `is_front_matter_text`：居中且 ≤80 字

### 6. 排版格式
- 表格单元格设置 `表正文` 样式后清除 run 级格式（rPr 只保留 rStyle/lang/bCs/iCs）
- 代码示例表格左对齐；API 示例表格左对齐不套用表正文
- 图片段落拉伸到内容区全宽

### 7. 目录生成
- `insert_table_of_contents`：目次（Normal+黑体+居中）+ TOC 域 + 分页符
- TOC 样式 `toc 1/2/3` 加入审计白名单

### 8. LLM 增强模块
- `llm_enhancer.py` 是核心模块，Phase A/B/C 三级增强
- `list_semantic_enhancer.py` 是兼容包装，内部转调 llm_enhancer
- Phase B/C 支持分片批处理，避免大文档超时
- `--llm-enhance off/auto/a/ab/abc` CLI 控制

## 输出验证不变量

- `unexpected_styles` 必须为空
- 标题文本中不含手工编号（"第一章"、"1.1 " 等）
- 表格单元格全部为 `表正文`（API 示例表除外）
- 图片段落 pPr 只有 `jc=center`
- API 示例表格前无题注
- 列表每章节重启，新 numId 带 `lvlOverride > startOverride`
- 题注 run 文本为 `['表 ', '1', ' caption_text']` 或 `['图 ', '1', ' caption_text']`
- 表题注在表格上方，图题注在图片下方

## 多 Agent 协作分工

- Hermes = 总控：理解需求、分类任务、路由、管理任务状态/Git、验证结果
- Codex = 规划/设计/审查：方案设计、架构、任务拆分、代码审查
- Claude Code = 实现：按任务文件编码、测试、验证

## 常用命令

```bash
cd scripts && .venv/bin/python3 -m main \
  --input ../input/xxx.docx \
  --output ../wx_output/xxx.docx \
  --template ../assets/技术文件格式及书写要求.docx \
  --report ../wx_output/report.json

# 测试
.venv/bin/python3 -m pytest tests/ -q
```

## 仓库管理

- 公开仓库 `mh567/wx-doc-format-skill`（main，仅 skill 文件）
- 私有仓库 `mh567/wx-doc-format-skill-dev`（main，含全部开发文件）
- 本地 `main` → 公开仓库，`dev` → 私有仓库
