# 附录范围的落位与附录标题的目录层级（execution-v9 执行包）

本包处理用户在用 `技术文件格式及书写要求.docx` 转换时发现的两个问题：附录之前的正文段落被挤到文末；附录大标题在目录里成了二级。

## 问题

### 问题一：附录之前的正文段落被挤到文末

源文档顺序：`其他说明`（正文块 102）→ `此模板还设置了页码…`（103）→ `正文的一章设置。`（104）→ 空段（105）→ `附录示例`（106）。输出变成 `其他说明` → 附录 → 那三段出现在全文最后。

实测证据：

- 附录容器的创建决策为 `create_node native-appendix-a, parent=root, index=127`。
- 创建时 AST root 有 138 个子节点，附录标题在 AST root 中的索引是 130；容器被插到 127，早了 3 格。
- 插桩 transaction 的轨迹：`CREATE native-appendix-a 申请 index=127，父节点现有子节点数=138`，容器落在第 127 位；六次 move 之后 root 尾部为 `[134] 其他说明 [135] native-appendix-a [136][137][138]` 三段被挤出的正文。

根因：索引的计量单位不一致。

- `semantics/appendices.py::collect_source_appendix_semantics` 在 **SourceFacts 的 root 子节点列表**上枚举，得到 `start_order = 127`（SourceFacts root 共 135 个子节点，附录标题在其中正是第 127）。
- 同文件 `collect_native_appendix_candidates` 把 `start_order` 直接当成 `create_node` 的 `index`，而该 index 的语义是**父节点（AST root）子节点列表**的插入位置。
- 两份列表长度不同：`build_source_ast` 会为带内联图片的段落额外物化 `*-image` 节点，本文档在附录之前多出 3 个，AST root 为 138。偏移量恰好等于多出的节点数，因此恰好 3 段被挤出。

影响面：凡在附录范围之前存在「AST 比 SourceFacts 多出节点」（内联图片、列表容器等）的文档，都会把附录之前的块按多出条数挤到文末。

既有门禁为何没拦住：`saved_output_audit.paragraph_order_changed` 比较的是渲染期望顺序与落盘顺序，两者都来自同一份 AST，顺序错在 AST 内部所以一致；`audit_native_appendix` 只校验范围归属与元数据的集合关系，不校验容器在 root 中的位置。这一层目前没有门禁。

### 问题二：附录大标题在目录里是二级

| | `附录标题` 样式 outlineLvl | 目录缓存里的层级 |
|---|---|---|
| 模板 | 1（Word 第 2 级） | 末尾三条缓存为 toc 1 / toc 2 / toc 3，对应附录标题、附录一级、附录二级 |
| 源文档 | 1 | `附录A（资料性） 附录示例` → toc 1；`A.1 …` → toc 2；`A.1.1 …` → toc 3 |

根因：输出的目录是模板原样复制的域 `TOC \o "1-3" \h \u`（按既有裁定不重写模板域指令），该域按**大纲级别**生成条目。模板的 `附录标题` 样式 `outlineLvl=1`，因此 Word 重算后把附录标题排到目录第 2 级。而模板与源文档自己的目录缓存把附录标题放在第 1 级（缓存由客户工具的自定义样式映射生成），用户在 Word 里看到的正是缓存，所以预期是第 1 级。

## 用户裁定

2026-09-16 用户选 A：给附录标题写直接大纲级别 0，使模板的目录域把它排在第 1 级；附录一级/二级标题沿用样式的大纲级别，正好得到第 2、3 级；附录三级标题仍在 `\o "1-3"` 之外，与源文档缓存一致。B（维持第 2 级）与 C（重写目录域为 `\t` 映射）均被否决。

## 解决方案

### 一、附录容器按 AST 位置落位（T01）

`semantics/appendices.py::collect_native_appendix_candidates` 的创建索引改为**范围内第一个节点在 AST root 子节点列表中的索引**，取不到时回落到列表末尾。`start_order` 继续作为来源位置保留在 `source_range` 里，不再兼作父节点索引。

### 二、附录标题的直接大纲级别（T02）

附录标题的目录层级是呈现事实：规范化阶段为 `appendix_role == "title"` 的块记录 `outline_level = 0`，渲染阶段按记录写出直接 `w:outlineLvl`。附录一级至三级标题不写直接级别，保持由样式提供（1 / 2 / 3 → 目录第 2 / 3 / 4 级），因此交付目录与源文档缓存一致。

写出的 `w:outlineLvl` 放在 `w:rPr` 之前，符合 CT_PPr 的元素顺序。

## 归属

| 项 | 归属 | 说明 |
|----|------|------|
| 附录容器落位 | `scripts/wxdf/semantics/appendices.py` | 候选生产者负责提供正确的父节点索引 |
| 附录标题呈现记录 | `scripts/wxdf/normalization.py` | 与 `presentation.paragraphs` 既有呈现同处 |
| 直接大纲级别写出 | `scripts/wxdf/rendering.py` | 渲染消费呈现记录，通用的段落属性写出 |
| 治理文档 | `AGENTS.md` 决策 7 | 记录附录标题的直接级别来源与取值 |
| 事务与渲染判定 | 不改 | 不改 index 语义，不改样式选择 |

## 字段级定义

| 项 | 形状 | 说明 |
|----|------|------|
| 创建索引 | `create_node.value.index` = 范围内首个节点在 AST root 中的位置 | 回落值为 root 子节点数（追加到末尾） |
| 来源范围 | `container.source_range = {"start_order": <SourceFacts 位置>, "end_order": <同>}` | 语义不变，仅供追溯 |
| 段落呈现 | `presentation.paragraphs[node_id] = {"outline_level": 0}` | 与既有 `alignment` 同键空间，按节点 id 消费 |
| 直接级别 | `w:pPr/w:outlineLvl w:val="0"` | 位于 `w:rPr` 之前 |

## 不变量

1. 附录容器在 AST root 中的位置不早于源顺序中位于附录范围之前的任何块。
2. 附录范围节点仍全部由容器拥有，`root_node_ids` 与容器子节点集合一致（既有附录审计口径）。
3. 附录标题在交付文档中带直接大纲级别 0，且不带其它直接大纲级别。
4. 附录一级至三级标题不写直接大纲级别。
5. 附录范围之前的内容不因落位修正而改变自身角色、样式或文本。
6. 审计位不变：三项审计 `passed`，`unexpected_styles` 为空，普通文档 `risk_warnings` 为空。

## 测试决策

红灯先行，新增 `tests/test_modular_appendix_placement.py`（文件名命中既有 `!tests/test_modular_*.py` 放行规则）。

T01 覆盖：

1. 合成 DOCX：附录标题前有 1 个内联图片段与 3 个正文块，断言附录容器在 AST root 中的位置等于「范围内首个节点在源顺序中的位置」，且三个正文块仍在容器之前。
2. 合成 DOCX：不带内联图片时（多出 0 个节点）落位不变，作为对照。
3. 端到端：转换后输出里附录之前的段落顺序与源顺序一致（不缺、不后移）。

T02 覆盖：

4. 呈现层：附录标题块在 `presentation.paragraphs` 中带 `outline_level = 0`，附录一级标题不带。
5. 端到端：输出中附录标题段落的 `w:pPr` 含 `w:outlineLvl w:val="0"`，且该元素位于 `w:rPr` 之前；附录一级标题段落不含直接 `w:outlineLvl`。
6. 端到端：真实文档 `技术文件格式及书写要求.docx` 转换后，附录标题带直接级别 0，附录之前的正文段落顺序与源一致。

## 不在范围

- 不改模板文件、样式表与目录域指令。
- 不改索引的语义（`index` 仍是父节点子节点列表的位置）。
- 不改 legacy 诊断链路（`toc_detector` 与 `template_finalizer` 的 `\t` 映射保持原样，`toc_contract.TOC_CUSTOM_STYLE_LEVELS` 的取值为 legacy 服务；若将来 legacy 用于交付需另行裁定）。
- 不改 CLI 参数与公开报告合同。
- 不移动或删除 `_build/`、`paseo.json`、`.commandcode/`。
- 不执行远程 push、标签或 Release。

## 补充说明

- 路径声明（架构定案 / 实施实现 / 独立审查）：
  - 架构定案：`scripts/wxdf/semantics/appendices.py`、`scripts/wxdf/normalization.py`、`scripts/wxdf/rendering.py`、`AGENTS.md` 决策 7、`docs/execution-v9/README.md`。
  - 实施实现：上述文件与 `tests/test_modular_appendix_placement.py`、`docs/execution-v9/evidence/`、`.gitignore` 放行。
  - 独立审查：由另一智能体对 diff 执行 Standards + Spec 双轴审查，复审确认。
- 本包状态唯一源：本目录 `status.json`。
- 外部门禁：`word_toc_and_appendix_page_manual_check`。目录层级与附录分页的最终效果需要用户在 Word/WPS 中打开确认；本机只能验证写出的直接大纲级别与文档顺序。
