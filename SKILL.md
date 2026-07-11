---
name: wx-doc-format
description: Convert Markdown or DOCX documents into a template-formatted .docx using a three-stage pipeline: parse to AST, normalize and audit AST, render with template styles. Includes full audit reports and style compliance checking.
metadata:
  short-description: Convert MD or DOCX into template-formatted DOCX
---

# WX 文档格式

将 Markdown 或 DOCX 文档转换为模板格式的 Word (.docx) 文档。采用三段式流水线架构（解析→规范化→模板渲染），所有输出样式严格来自模板文件，不写入任何手动格式。适用于任何支持 Python 脚本调用的 Agent 或自动化流程。

## 架构概览

```
输入 (MD / DOCX)
  │
  ▼
Step 1: 解析为 source AST
  │  scripts/md_pipeline.py 或 scripts/docx_pipeline.py
  │  产出: 语义块模型（标题、列项、表格、正文、题注、附录等）
  │  可用 --source-ast 导出
  │
  ▼
Step 2: 规范化 AST + 审计修补 + LLM 语义增强（可选）
  │  scripts/model_normalization.py
  │  清理手工编号、重定型误判块、修正表格角色、标记列表重启
  │  scripts/list_semantic_enhancer.py（可选）
  │  大模型补充识别：无标记列表项、隐式功能点段落
  │  可用 --ast 导出
  │
  ▼
Step 3: 模板驱动渲染
  │  1. scripts/main.py 从模板 .docx 创建空文档
  │  2. scripts/word_model_renderer.py 渲染 AST → 段落（只设样式，不写手动格式）
  │  3. scripts/docx_render.py 渲染 DOCX 直接路径（图片/表格克隆）
  │  4. scripts/template_finalizer.py 格式收口：样式别名、附录合并、run 字体、表格
  │  5. scripts/audit.py + scripts/reporting.py 输出审计和报告
  │
  ▼
输出: 样式与模板完全一致的 .docx + JSON/MD 报告
```

## 模块边界

- 中间结构定义和校验 — `scripts/document_model.py`
- 纯文本解析工具（标题/列项/公式/附录/题注识别与清理） — `scripts/text_utils.py`
- Markdown 解析到 source AST — `scripts/md_pipeline.py`
- DOCX 解析（`infer_docx_role` 语义角色推断） — `scripts/docx_pipeline.py`
- AST 规范化与审计修补（Step 2） — `scripts/model_normalization.py`
- AST → Word 渲染（只设模板样式） — `scripts/word_model_renderer.py`
- DOCX 直接路径渲染（图片/表格克隆、题注 SEQ 自动编号、图片段落归一化） — `scripts/docx_render.py`
- 模板样式和编号定义读取 — `scripts/template_profile.py`
- 模板格式收口（附录合并、样式别名、run 字体重刷、表格属性） — `scripts/template_finalizer.py`
- Word 输出审计与内容型复核 — `scripts/audit.py`
- 报告结构、风险提示和 Markdown 报告输出 — `scripts/reporting.py`
- 非模板模式的 fallback 样式创建 — `scripts/fallback_styles.py`
- 大模型语义增强（可选，纯函数接口，无 LLM 依赖） — `scripts/list_semantic_enhancer.py`
- CLI 编排入口 — `scripts/main.py`
- 应急兜底路径（python-docx 不可用时） — `scripts/format_docx_ooxml.py`

## 转换流程

### 第一步：解析到 AST

输入文档被解析为语义块模型（source AST）。模型包含以下块类型：

- `heading` — 标题块，含层级（1-6）和编号模式
- `body` — 正文块
- `list_item` — 列项块，含层级、列表类型和重启标志
- `table` — 表格块，含行/列/单元格角色
- `caption` — 题注块（图表编号和正文分离）
- `appendix` — 附录块
- `image` — 图片块

Markdown 路径（`scripts/md_pipeline.py`）支持：
- `#` Markdown 标题
- Markdown 表格
- 数字/中文/罗马数字标题推断
- `a)`、`1)`、`（1）`、`——`、`•` 列项识别

DOCX 路径（`scripts/docx_pipeline.py`）支持：
- 原生 Heading 样式标题
- 源文档自动编号（numId/ilvl）推断
- 正文视觉标题推断（`scripts/text_utils.py` 中的 `looks_like_visual_heading`）
- 单元格角色推断
- 图片段落和混合内容段落识别

### 第二步：规范化与审计修补

`scripts/model_normalization.py` 对 source AST 做以下修补：

- 标题手工编号清理（`1.1 `、`第一章 `、`一、`、`1.1　` 等前缀移除）
- 正文块误判为标题/列项的重定型
- 列项手工序号清理
- 列项重启标记（小节后从 a)/1) 重新开始）
- 注释/公式/附录/题注角色提升
- 代码示例表格 `header_rows=0` 强制
- 表格单元格角色修正（data 表按 header_rows 修正 header/body）
- 图片居中语义统一
- 表格自适应和最小行高语义

规范化后的模型可通过 `--ast` 导出为 JSON。

### 第三步：模板驱动渲染

#### 模板模式（推荐，`--template`）

1. 从模板 `.docx` 创建目标文档，**保留模板的所有样式和编号定义**
2. 清空模板示例正文，保留 section、页眉页脚、页面设置
3. 按 AST 块类型映射到模板样式（只设段落样式）

**AST 到模板样式映射表：**

| AST 块类型 | 角色 | 模板样式 | 编号来源 |
|---|---|---|---|
| heading | title/level 0 | `文档标题` | 无编号 |
| heading | level 1-6 | `heading 1` ~ `heading 6` | 模板多级编号 (numId=1) |
| list_item | lower_letter_paren | `1.1一级列项-编号` | 模板 numId=3 |
| list_item | decimal_paren | `2.1二级列项-有编号` | 模板 numId=8 |
| list_item | dash | `1.2一级列项-无编号` | 无编号 |
| list_item | bullet_dot | `2.2二级列项-无编号` | 无编号 |
| body | body | `Normal` | 无 |
| body | note | `3.1注-无编号注` | 无编号 |
| body | numbered_note | `3.2注-有编号注` | 模板 numId=6 |
| body | formula | `Normal` | 无 |
| table | — | `表正文`（单元格） | 无 |
| table | — | 表题注：无题注表格自动插入 | SEQ Table 域 |
| figure | — | 图题注：按源文档题注文字 | SEQ Figure 域 |
| appendix | — | `附录标题` | 模板 numId=7 |
| caption | — | `caption`（`Caption`/`题注`） | Word SEQ 域自动编号 |

> **列表策略**: 所有有编号的列项统一使用一级列表样式（`1.1一级列项-编号`，`a) b) c)` 格式），每章节遇到标题后从 `a)` 重新开始。编号格式由模板的 `lowerLetter` abstract numbering 定义，不根据源文档标记（`1.`/`a)`）区分层级。

4. 列项编号：
   - 按 `(level, kind)` 键管理，letter（a/b/c）和 decimal（1/2/3）独立编号
   - 每个标题后创建新 num 实例并设置 `lvlOverride startOverride=1` 实现重启
5. 表格：创建表格，单元格样式设为 `表正文`，清除 run 级格式使样式定义生效
   - 无题注表格自动插入 `SEQ Table` 域代码题注
   - 源文档已有题注的编号文字被剥离，改用 SEQ 域
   - 图和表分别使用 `SEQ Figure` / `SEQ Table`，打开 Word 后更新域即可自动编号
6. 图片：从源文档克隆并归一化：
   - 非 PNG/JPEG 格式用 Pillow 转换为 PNG
   - EMF/WMF 保留原格式（Pillow 跨平台 WMF 渲染不稳定）
   - 图片段落居中、清除缩进和段间距
   - 通过 `v:imagedata` / `a:blip` / `o:OLEObject` 等通用扫描支持所有嵌入方式

#### 非模板模式（不传 `--template`）

使用 `scripts/fallback_styles.py` 创建内置样式和编号定义。不作为正式交付用途，仅用于预览。

#### 模板格式收口

渲染完成后，`scripts/template_finalizer.py` 执行：
- **样式别名收口**：`正文`→`Normal`、`题注`→`Caption`、`3.1 注`→`3.1注` 等
- **附录结构合并**：三段式附录（`附录 A` + `（规范性）` + 标题）合并为一个 `附录标题` 段落
- **run 字体重刷**：按模板样式的字体定义重刷每个 run 的 eastAsia/ascii/hAnsi/字号/粗体
- **表格属性**：行高最小值、autofit、单元格样式
- **样式审计**：标记所有非模板样式的段落

#### 输出审计

`scripts/audit.py` 检查：
- 标题层级跳跃（如 level 1 → 3）
- 列表编号是否从 1 重启
- 表格段落是否使用 `表正文` 样式
- 代码示例表格是否左对齐
- 标题文本是否残留手工编号
- Markdown 标记残留
- 表格单元格可能截断

## CLI 用法

```bash
python3 -m main \\
  --input source.md \\            # 或 source.docx
  --output output.docx \\
  --template template.docx \\     # 必选，正式使用
  --report report.json \\         # 可选，JSON 审计报告
  --report-md report.md \\        # 可选，可读报告
  --ast model.json \\             # 可选，规范化后的 AST（Step 2 产物）
  --source-ast source.json \\     # 可选，解析后的 AST（Step 1 产物）
  --fail-on-risk                  # 可选，检测到风险时退出码非 0
```

默认参数：
- `--table-row-height-cm 0.69`
- `--table-row-height-rule at-least`
- `--strict-normalize True`

## 样式合规不变量

- `--template` 模式下，输出文档只使用模板中定义的样式。JSON 报告的 `template_finalizer.style_audit.unexpected_styles` 必须为空。
- 模板的 `resolved_styles` 必须覆盖所有角色（`missing_roles` 为空）。
- 模板的 `numbering_ids` 必须包含 `heading`、`list_letter_abstract`、`list_decimal_abstract`。
- Step 2 规范化后的 AST 中，标题 `numbering.mode` 应为 `auto`，标题文本不含手工编号。
- Step 2 规范化后的 AST 中，列项文本不含 `a)`、`1)` 等手工序号。
- 代码示例表格 `table_type` 应为 `code_sample`，`header_rows` 为 0。
- `audit.heading_hierarchy_warnings` 应为空（源文档结构问题会在这里体现）。
- `audit.ordered_list_nums_without_restart` 必须为空。
- `audit.table_paragraphs_not_table_body` 必须为空。
- `audit.code_sample_table_alignment_issues` 必须为空。
- `audit.markdown_residue` 应为空。
- 渲染后反向审计的 `rendered_document_model_summary.issue_count` 应为 0。
- 页眉页脚、页码分节、复杂目录域、附录自动编号等内容不由本脚本自动生成，需要人工复核或另行处理。

## 标题识别规则

标题推断综合多个维度，借鉴旧版成熟逻辑：

- 源文档 Heading 样式优先，保留层级关系
- 视觉标题推断（`looks_like_visual_heading`）：`mostly_bold or centered or max_size >= 14pt`
- 功能标题过滤（`is_compact_function_heading_text`）：≤24 字、无标点符号、不匹配"支持/要求"等前缀
- 封面元信息排除：版本/日期/编号/属性描述/API 标签（请求参数、返回示例等）
- 括号说明排除：仅特定关键词（仅供参考/示例/待补充/待定），不含正常括号
- 封面副标题自动降级为 body

## API 文档特殊处理

- API 标签段落（请求参数、返回示例、接口说明等）降级为 body
- 请求/响应示例表格（1×1 单元格含 JSON/HTTP/Plain Text）识别为 API 示例表
  - 不自动插入题注，不清除已有题注
  - 不套用 `表正文` 样式，保持左对齐
- 普通表格单元格：设 `表正文` 样式 + 清除 run 级格式（bold/size/rFonts）

## 已知边界

- 文本框、形状、SmartArt、批注、修订记录、复杂域代码、页眉页脚中的正文内容不会完整重建。
- 图片在 Markdown 路径中不会自动嵌入（需源文档已有图片引用）。
- DOCX 路径的图片克隆依赖源文档的媒体关系映射，复杂嵌套表格仍需渲染复核。
- 强规范化会根据文本和格式推断标题，疑似标题会写入报告，不能完全替代人工确认。
- 应急兜底路径（`scripts/format_docx_ooxml.py`）仅在 python-docx 依赖故障时启用，用于尽量保留图片、页眉页脚、域、批注等对象。

## 使用要求

- Python 3.10+
- python-docx
- lxml
- macOS 用户可运行 `scripts/bootstrap_macos_lxml.sh` 创建隔离环境
- 环境变量 `WX_DOC_FORMAT_VENV` 可指定 venv 路径

## 测试迭代流程

每次转换后按以下三步检查框架排查问题，不要乱猜：

### Step 1 — 解析检查

检查 `infer_docx_role` / heading-level / list-识别 是否正确：

1. 逐一比对源样式和推断 role：源样式为 Heading 而推断不是、或源样式为列项而推断不是，都算推断错误
2. 检查 `inferred_headings`（从文本推断的标题）和 `suspect_visual_headings`（视觉标题嫌疑）是否合理
3. 检查 `inferred_lists`（从文本推断的列项）是否合理
4. 常见误判修复方向：
   - 封面元信息（版本/日期/编号）被误判为标题 → `looks_like_visual_heading` 加排除条件
   - 属性描述（"身份状态：在职、离职"）被误判为标题 → 正则加守卫
   - 括号说明（"（仅供参考）"）被误判为标题 → 正则加守卫
   - 源文档列表样式被推断为 body → `infer_docx_role` 加样式名匹配

### Step 2 — 规范化检查

检查 `model_normalization.py` 的修补是否到位和渲染后的反向验证：

1. 输出标题文本是否残留手工编号（"第一章"、"1.1 " 等）→ `strip_heading_marker` 是否被调用
2. 列表 numId 是否跨章节独立（每个 Heading 后有新 numId）
3. 新 numId 是否有 `lvlOverride > startOverride`（否则编号不会重启）
4. `heading_auto_numbering` 警告是否合理（模板样式绑定编号则不应报警）
5. `heading_manual_number_text` 警告是否合理

### Step 3 — 渲染检查

检查模板样式套用和收口是否生效：

1. `unexpected_styles` 必须为空（输出只使用模板定义的样式）
2. 样式名映射是否正确（源 `Heading 1` 应映射到模板的 `heading 1`）
3. 图片段落：居中 + 无缩进 + 无段间距（`w:ind` 和 `w:spacing` 被删除）
4. 表格：单元格样式为 `表正文`，run 级格式被清除由样式定义接管
5. 题注：使用 SEQ 域代码（`SEQ Table` / `SEQ Figure`），无题注表格自动插入
6. `template_finalizer` corrections 是否包含预期外的修复
7. 最终审计 `risk_warnings` 是否包含预期外的风险

## LLM 语义增强（Step 2 扩展）

纯规则列表识别无法覆盖所有场景（如无编号标记的功能点列表）。本 step 提供可选的 LLM 增强：

### 脚本层接口

`scripts/list_semantic_enhancer.py` 是纯函数模块，不依赖任何 LLM 库：

- `_build_context_for_llm(heading_text, heading_level, paragraphs)` → 构造 prompt 字符串
- `enhance_with_llm(heading_text, heading_level, paragraphs, llm_call=callback)` → 调用回调获取 LLM 响应，提取 JSON 修正角色。`llm_call` 为 None 时静默返回原列表
- `build_role_overrides_from_docx(src_doc, strict_normalize, llm_call=callback)` → 批量处理所有 section，返回 `{para_index: corrected_role}` 映射

### Agent 集成方式

调用方（CLI / agent）负责注入 `llm_call` 回调。回调签名：

```python
def llm_call(prompt: str) -> str:
    """Send prompt to LLM, return raw text response."""
```

调用方可通过任一 LLM 服务实现：OpenAI API、Anthropic API、本地模型、或 Agent 自身的大模型能力。

### 在 skill 流程中的位置

在 Step 2（规范化）和 Step 3（渲染）之间：

1. 规则推断每个段落的 role（`infer_docx_role`）
2. 按 heading 分组收集 section 段落
3. 每组构造 prompt → 注入 `llm_call` → 获取 JSON 修正
4. 修正后的 role 传入 `render_docx_direct` 的 `role_overrides` 参数
5. 渲染时 `role_overrides` 覆盖规则推断的 role

LLM 调用失败或 `llm_call` 为 None 时，退化为纯规则渲染。
