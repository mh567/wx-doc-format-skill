# 手工编号标题的归属与文本清理（execution-v7 执行包）

本包处理一类证据优先级缺陷：源文档里已经带标题样式的段落，只要文本以手工编号开头（`第一章 概述`、`1.2 需求分析`、`第一节 背景`），就会被判成受保护正文，标题角色与标题文本同时丢失。用户已于 2026-09-16 裁定处理方向（见「用户裁定」）。

## 问题

实测环境：`dev` 分支 HEAD `2faa8bc` 加未提交的图片段落呈现改动，默认 `--pipeline modular`（`--modular-profile native-v2`）。

**控制变量实测（DOCX，`collect_structure_candidates`）**

| 源段落 | `protect_semantics=False` | `protect_semantics=True`（生产默认） |
|--------|---------------------------|--------------------------------------|
| `Heading 1` + `第一章 概述` | `role=heading level=1`（证据 `docx_style`） | `role=body`（证据 `protected_content`） |
| `Heading 2` + `1.2 需求分析` | `role=heading level=2` | `role=body` |
| `Heading 2` + `第一节 背景` | `role=heading level=2` | `role=body` |
| `Heading 3` + `1.2.3 详细设计` | `role=heading level=3` | `role=heading level=3` |
| `Heading 1` + `概述` | `role=heading level=1` | `role=heading level=1` |

**端到端实测（同一份 DOCX 走 `python3 -m main`）**：被判降级的段落在输出里没有 `pStyle`，继承模板 `Normal`（含 2 字符首行缩进），决策账本为 `update_role role=body` 加 `overlay protected/reason=manual_heading_marker`，而 `status=completed`、`model_audit`、`saved_output_audit` 与 `list_readback_audit` 全部通过。缺陷对现有三道审计不可见。

**Markdown 同路径实测**：`# 1.2 需求分析` 作为首个 H1 时同样被判 `role=body`，于是 `title_seen` 为假，结构语义另按文件名生成 `role=title` 的块并写入 `markdown_title_generated` 风险提示。作者的一级标题变成正文，文档标题变成文件名。

**根因**

1. `scripts/wxdf/semantics/protected.py:173`、`:174` 的 `_MANUAL_HEADING_RE`（`^(?:第[一二三四五六七八九十百千万0-9]+[章节]|\d+(?:\.\d+)+)[\s、.．:：]`）把这类文本判为 `manual_heading_marker`，`protected_unit_semantics` 对该理由返回 `role=None`（`:206`、`:207`）。
2. `scripts/wxdf/semantics/structure.py:139` 的受保护分支排在显式样式分支之前，并把 `None` 兜成 `body`，显式标题样式因此失去效力。
3. 生产路径以 `protect_semantics=True` 调用结构语义（`scripts/wxdf/semantic_modules.py:104` 与 `:199`），所以该分支在真实转换里总是生效。

**与既有约定的冲突**

- `AGENTS.md` 输出验证不变量要求「标题文本不含手工编号」。
- `AGENTS.md` 关键决策 10 确立证据优先级：显式标题样式强于文本证据。
- 退役实现走的是另一条路：`scripts/docx_pipeline.py:294`、`:375` 与 `scripts/markdown_recognition.py:411`、`:636` 消费 `text_utils.strip_heading_marker`，去掉编号并保留标题。

三条约定都指向同一方向，本包据此定案。

## 用户裁定

2026-09-16 用户裁定方向 A：去编号保标题。方案 B（维持现状，把带标题样式的手工编号段当作受保护正文）被否决。

## 解决方案

**一、角色优先级（结构语义）**

显式标题样式先于文本型受保护理由裁决。文本型理由（`manual_heading_marker`、`date_or_version`、`legal_clause`、`api_label`、`api_path`、`ip_address`、`formula`）不得把带标题样式的段落降为正文，只作为该标题的附加证据留在角色候选上。带角色的受保护结论（`note`、`numbered_note`、`code_block`、公式原子）保持现有优先级，决策 10 关于注的边界（裸标记随标题样式、带序号的注仍是注）逐条不变。

没有标题样式的段落不受影响：`1.2 需求分析` 这类正文段仍是受保护正文，列表语义不得把它提升为列表项（README 约束 6 与 T07 的口径）。

**二、标题文本清理（结构语义）**

结构语义为「已定级标题且文本带手工标记」的块发布内容候选，写出去掉标记的 `inline_content`。标记词表复用既有单一所有者 `text_utils.strip_heading_marker`（`scripts/text_utils.py:84`）及其 `HEADING_PATTERNS`（`:8`）与 `_HIERARCHICAL_ARABIC_HEADING`（`:17`），不新造第二份规则。该词表覆盖 `第N章/节/条`、`N.N…`、`一、`、`(一)`、`（一）` 与 `^\d+\s+`，因此 `1 项目概述` 这类当前未被判保护的标题也会一并去标记。这是不变量的要求，属于本包的预期后果并单独用测试钉住。

**三、空结果守卫**

去标记后文本为空时保留源文本并产出非阻断诊断。退役实现曾把整段只有「第一章」的段落剥成空标题，本包不复制该缺陷。

**四、输入类型**

DOCX 与 Markdown 共用同一条结构语义路径，两者同时生效，两处都要有端到端测试。

## 归属

| 项 | 归属 | 说明 |
|----|------|------|
| 角色优先级 | `scripts/wxdf/semantics/structure.py` | 结构语义是标题的唯一所有者 |
| 标题文本清理候选 | `scripts/wxdf/semantics/structure.py` | 与角色裁决同模块，避免第二个所有者 |
| 手工标记词表 | `scripts/text_utils.py`（只读复用） | 既有单一所有者，不新增规则 |
| 列表负向输入 | `scripts/wxdf/semantics/protected.py`（不改） | `manual_heading_marker` 理由仍供 `lists._HEADING_PROTECTION_REASONS`（`scripts/wxdf/semantics/lists.py:134`）使用 |
| 渲染与审计 | 不改 | 无新增样式，无判定规则变更 |

## 字段级定义

| 项 | 形状 | 说明 |
|----|------|------|
| 角色候选证据 | `{"kind": "structural", "source": "docx_style", "style": <样式名>}` | 保留既有证据形状；标记理由以附加证据项记入 `manual_heading_marker`，便于账本追溯 |
| 内容候选 | `operation=update_node_content`，`property_scope=["inline_content"]`，`value={"inline_content": [...]}` | 与列表语义去掉可见标记的做法同形 |
| 内容候选证据 | `{"kind": "format_contract", "source": "heading_marker_stripped"}` 与负证据 `[]` | 正证据写明使用的词表条目 |
| 空结果诊断 | `code="heading_marker_only_text"`，`severity="info"`，`node_id`，`message` | 非阻断，进 `downgrades`，不进 `diagnostics` |
| 标记词表 | `text_utils.strip_heading_marker(text)` | 唯一入口 |

## 不变量

1. 已定级标题的渲染文本不含手工标记。
2. 无标题样式的正文段带手工编号时仍是受保护正文，列表语义不把它提升为列表项。
3. 带序号的注在标题样式下仍是注（决策 10）。
4. 去标记后为空时源文本不变，并留下 `heading_marker_only_text` 信息记录。
5. 审计位不变：三项审计 `passed`，`unexpected_styles` 为空，普通文档 `risk_warnings` 为空。

## 测试决策

红灯先行，新增 `tests/test_modular_heading_marker.py`（文件名命中既有 `.gitignore` 放行规则 `!tests/test_modular_*.py`，仍需 `git check-ignore -v` 复核可见）。覆盖：

1. 候选层：`protect_semantics=True` 下四种带标记的标题样式段落保持 `role=heading` 与样式层级。
2. 候选层：无标题样式的同形段落仍是 `role=body`（保护不被削弱）。
3. 候选层：`1 项目概述` 这类未被判保护的标题同样去标记。
4. 候选层：带序号的注在标题样式下仍是 `numbered_note`（决策 10 回归守卫）。
5. 端到端 DOCX：标题样式段落在输出里带标题样式、文本无标记，三项审计通过。
6. 端到端 DOCX：整段只有 `第一章` 的标题保留源文本并留下信息记录。
7. 端到端 Markdown：首个 H1 带标记时成为文档标题且文本无标记，不生成文件名标题、不写 `markdown_title_generated`。
8. 真实文档复核：带手工编号标题的样例转换 `completed`、三项审计 `passed`、`unexpected_styles` 为空。

## 不在范围

- 不改模板、样式表与编号定义。
- 附录标题的编号形态归附录语义所有，本包不动；附录标题带标记的情形记为残留。
- 不改渲染与审计的判定规则，不新增 LLM 能力。
- 不改 CLI 参数与公开报告合同，不重命名稳定接口。
- 不移动或删除 `_build/`、`paseo.json`、`.commandcode/`。
- 不执行远程 push、标签或 Release。

## 补充说明

- 路径声明（架构定案 / 实施实现 / 独立审查）：
  - 架构定案：`scripts/wxdf/semantics/structure.py`、`docs/execution-v7/README.md`、`AGENTS.md` 决策 10 修订。
  - 实施实现：`scripts/wxdf/semantics/structure.py`、`tests/test_modular_heading_marker.py`、`docs/execution-v7/evidence/T01.md`、`.gitignore` 放行。
  - 独立审查：由另一智能体对 diff 执行 Standards + Spec 双轴审查，复审确认。
- 本包状态唯一源：本目录 `status.json`。远程推送、标签、发布需单独授权。
- 治理文档同步：定案后修订 `AGENTS.md` 决策 10，把「显式标题样式先于文本型受保护理由」与「已定级标题去手工标记」写进决策记录，避免后续复核重复提出。
