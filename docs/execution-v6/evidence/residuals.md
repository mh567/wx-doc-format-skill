# execution-v6 残留收口证据（R1-R4）

本文件承接 `docs/execution-v6/README.md`（冻结 spec）、`docs/execution-v6/status.json` 与已关闭十张工单的残留清单，记录收口轮 R1-R4 的定案、实现与证明。按时间增量补全。

## 路径声明

按 AGENTS.md「改 .py 前必须先声明路径（→架构智能体定案 / →实施智能体实现 / →审查智能体独立审查）」。

架构定案（来源 `docs/execution-v6/README.md` 补充说明）：

- `scripts/wxdf/parsers.py`
- `scripts/wxdf/semantics/lists.py`
- `scripts/wxdf/semantic_modules.py`
- `scripts/wxdf/native_stages.py`

实施实现（本智能体实现）：

- `scripts/wxdf/parsers.py`（R1 新增缩进簇层级细化；R2 `numbering_definition_ref` 改写为派生视图）
- `scripts/wxdf/semantics/lists.py`（R2 新增 `_numbering_ids` 单一属主读取并改接全部消费方；R3 新增 `ambiguity_records` 歧义记录）
- `scripts/wxdf/semantic_modules.py`（R3 模块证据暴露 `ambiguity_record_count`）
- `scripts/wxdf/native_stages.py`（R4 把 `list_render_level_clamped` 写进报告可见的 `saved_output_audit`，非阻断）
- `tests/test_v6_residuals.py`（R1-R4 新增 10 项测试）

`scripts/wxdf/transaction.py` 与 `scripts/wxdf/run_result.py` 未改动（禁改范围）。

独立审查：由另一智能体对本 diff 执行 Standards + Spec 双轴审查，本文件实施者未自审。

## R1 缩进簇层级细化（FIXED，口径经复审更正）

### 现状与缺口

`wxdf.parsers._resolve_effective_ilvl` 只综合 `source_ilvl` 与 `pStyle` 关联；README 证据字段清单把 `effective_ilvl` 定义为「综合 `source_ilvl`、`pStyle` 继承、`w:ind` 与邻接关系后的有效层级」，缩进与邻接证据被采集（`paragraph_layout_evidence` 的 `left_twips`、编号级 `w:ind`）却从不参与裁决。这也是 T10 F4 记录的「缩进簇层级恢复随 `list_hierarchy` 删除且无等价物」缺口。

### 采用的规则（`rule = "declared_plus_indent_cluster"`）

`parsers._refine_indentation_levels` 在 `analyze_docx_numbering` 的分组完成后运行，对每个编号组（一段连续编号块）：

1. 组内按已声明的 `effective_ilvl` 分区，显式更深的声明层级永不被降低。
2. 分区内按源顺序遍历，对每块取解析后的左缩进 `layout.left_twips`；以 120 twips 容差聚类（`_INDENT_CLUSTER_TOLERANCE_TWIPS`，与已删 `list_hierarchy._layout_levels` 同规则），得到升序簇号。
3. 仅当某块的簇号严格深于此前出现过的最浅簇号时，把它加深「簇号差」；否则保持声明层级。`effective_ilvl = declared_effective_ilvl + indent_offset`。

该规则要求较浅的块先出现，这正是嵌套子表的形态；一个单独更缩进的起始块不会被提升。规则的判定过程逐块记录进证据 `indent_cluster_level`（`rule / declared_effective_ilvl / indent_left_twips / cluster_rank / indent_offset / tolerance_twips`），`evidence["effective_ilvl"]` 发布细化后的值。

### 语义流

`add_unit` 用分组裁决（`analyze_docx_numbering` 的 descriptor）的 `effective_ilvl` 覆盖 `_numbering_catalogue_evidence` 写入的逐段基值；`semantics/lists._raw_depth` 读到细化值，状态候选的 `semantic_level` 随之加深，隐式列表段的 `declared_depth` 变深并挂到先出现的父项。

### 守卫测试（红→绿）

- `test_v6_r1_indented_sublist_without_new_instance_resolves_deeper`：同 `numId`、同 `ilvl=0`，子块缩进更深 → 父 `effective_ilvl=0`、子 `effective_ilvl=1`，且 `analyze_lists` 中子项 `semantic_level=1` 且 `parent_item_id` 指向父项。**改动前 1 failed。**
- `test_v6_r1_same_indent_block_stays_a_sibling`：同缩进两块同为 0（回归护栏，改动前后均绿）。
- `test_v6_r1_flat_uniform_list_is_unaffected`：三块同缩进的平铺列表全部 0（回归护栏）。

## R2 `num_id` 双源收口（FIXED）

### 定案：属主为顶层 `num_id` / `abstract_num_id`

README 证据字段清单把 `num_id` 与 `abstract_num_id` 定义为顶层字段，故二者是属主。`numbering_definition_ref` 不是 spec 字段，作为「兼容视图」保留：`parsers.add_unit` 不再从 `descriptor` 单独写它，而是在顶层字段写好后由它们派生：

```python
evidence["numbering_definition_ref"] = {
    "num_id": evidence.get("num_id"),
    "abstract_num_id": evidence.get("abstract_num_id"),
}
```

因此两种拼写的值同源，`_numbering_catalogue_evidence` 与 `add_unit` 无法再漂移。

### 消费方更新

`semantics/lists.py` 新增 `_numbering_ids(evidence)`，属主优先、兼容视图兜底，并改接全部消费方：`_has_numbering_definition`、`_numbering_sequence`、有效实例身份提取（`effective_num_id` / `effective_abstract_id`）、`_contract_downgrades` 的 `missing_definition`。读取任一拼写的旧调用方继续工作。

### 合同版本

证据报文的键集合未变（两种拼写仍在），只是写入点收敛，故 capability 合同版本不升。

### 守卫测试

- `test_v6_r2_numbering_ids_prefers_the_owner_field`：属主优先；两拼写冲突时属主胜出。**改动前 2 failed（无该函数）。**
- `test_v6_r2_numbering_instance_identity_is_single_sourced`：真实 DOCX 转换的列表条目 `num_id` / `abstract_num_id` / `sequence` 单值。
- `test_v6_r2_numbering_definition_ref_is_derived_from_the_owner`：真实 DOCX 证据两种拼写相等。

## R3 约束 7 与不变量的证据类别口径（PARTIALLY FIXED，含 spec-vs-实现注记）

### 实现的最强读法

T04 已移除数值 `score`，改写为 Resolver 证据类别（类强度排序），故 README 约束 7 的字面「证据评分阈值」在实现上不可判定。在证据类别模型允许的范围内实现如下：

1. **不变式 6（低置信度决策可索引）**：新增 `list_semantics_result.ambiguity_records`，是每块列表决策的唯一索引。低置信度块记为 `kind="low_confidence_candidate"`，指向其 `low_confidence_list_marker` 降级，含 `resolution` 与 `candidate_states`；真正的 Resolver 平局记为 `kind="resolver_conflict"`，指向 `ambiguous_list_state`。模块证据暴露 `ambiguity_record_count`。原先低置信度决策只在 `downgrades` 里可索引，本轮把它同时纳入歧义记录。
2. **不变式 7（普通模式不静默接受歧义）**：真正的 Resolver 平局令 `state_unresolved` 非空、`status="unresolved"`（`!= "completed"`）。低置信度块被**裁定**为正文（非未解决候选），故不翻状态。

### 为何「保持正文」不是静默接受

低置信度块不进入 Resolver 冲突，是因为适配器已给出判决：正文。该判决有两条可索引记录（`downgrades` 的 `low_confidence_list_marker` 与 `ambiguity_records` 的对应条目），证明块未被静默提升为列表项，也未被静默丢弃（源内容保留为正文）。不变式 7 约束的是「未解决候选」，而该块已被解决并记录，故不要求 `status != "completed"`。

### spec 与实现的精确差距

README 约束 7 的字面语义是「证据评分低于阈值的候选必须进入歧义报告」。T04 移除数值评分后，阈值不存在；实现把「低置信度」表达为证据类别不足（`isolated_generic_numbering` / 未解析定义）并保持正文，同时以歧义记录留痕。该差距作为「spec 字面 vs 实现」的注记记录，并在 README 约束 7 处补一条不改动编号约束的事实注记。

### 守卫测试

- `test_v6_r3_low_confidence_decision_is_indexed_and_non_blocking`：低置信度块在 `ambiguity_records` 可索引、有降级、`state_unresolved == []`、`status == "completed"`、未成为列表项。**改动前 2 failed。**
- `test_v6_r3_resolver_conflict_is_indexed_and_blocks_completion`：真平局在 `ambiguity_records` 可索引且 `status == "unresolved"`。**改动前 2 failed。**

## R4 `list_render_level_clamped` 可见性（FIXED）

### 定案：报告可见、非阻断

`list_render_level_clamped` 只在 `render_result` 与模块证据（`native_module_evidence`，发布时被 sanitizer 剥离）里存在，作者看不到夹层。裁定：**报告可见、保持非阻断**。理由：夹层是 AGENTS.md 决策 1 的刻意二级行为，不是缺陷；阻断会否掉本可交付的文档。实现方式是在 `_native_auditor` 把渲染器夹层记录写进 `saved_output_audit["list_render_clamped"]`：

- 它不是 `issues` 条目，`passed` 不变，故不阻断发布、不因 `--fail-on-risk` 误伤；
- 它是顶层报告键，发布 sanitizer 不剥离；公开摘要的证据扫描会收集其 `code`，作者可在 `public_summary.diagnostic_codes` 看到。

### 守卫测试

- `test_v6_r4_clamped_list_is_report_visible_and_non_blocking`：三级有序列表 `status="completed"`、`saved_output_audit.passed=true` 且含一条 `list_render_level_clamped`（`semantic_level=2`、`render_level=1`）。**改动前 1 failed。**
- `test_v6_r4_two_level_list_has_no_clamp_record`：两级列表无该记录（回归护栏）。

## 关闭与重归属的残留

| 残留（来源） | 处置 |
| --- | --- |
| T10 F4「缩进簇层级恢复随 `list_hierarchy` 删除且无等价物」 | 部分关闭（R1 只在**编号组内**、且运行**回到更浅簇**时细化层级；编号组之外与不回头的运行不细化。这与 F4 记的「有意移除」并不冲突，但不构成完整等价） |
| 「README 字段定义称 `effective_ilvl` 综合 `w:ind`，实现只综合 `source_ilvl` 与 pStyle」 | 部分关闭（R1 综合的是**段落级**左缩进 `layout.left_twips` 与邻接，不是 README 字段清单里那个**编号级** `w:ind` 字段；两者在本规则里是不同证据，字段定义与实现的差异仍记录在案） |
| `num_id` 双源（T02/T03/T04/T05/T06/T10 各记一次） | 关闭（R2：属主顶层字段，ref 派生） |
| 「约束 7 字面阈值语义未实现，低置信度只记 info `low_confidence_list_marker`，不进歧义报告」 | 部分关闭（R3：证据类别口径实现 + 歧义记录索引 + spec-vs-实现注记；字面阈值语义在移除数值评分后不可判定，作为注记保留） |
| 「`list_render_level_clamped` 只进模块证据、不阻断发布」（T03/T04/T08/T09/T10） | 关闭（R4：进入 `saved_output_audit.list_render_clamped` 与 `public_summary.diagnostic_codes`，非阻断；副作用是 `public_summary.manual_review_required` 对夹层文档变为 `true`，已记录） |
| 「`LIST_END` 已枚举未发出」 | 重归属：未纳入本轮 R1-R4 范围，保留开放 |
| 缩进细化在运行**不回头**时不生效 | 本轮新增（复审发现）：`[父 720][子 1440][子 1440]` 这类以子表结尾的运行不再细化，属保守取舍；以闭合（回到更浅簇）为嵌套证据 |
| 缩进细化使用段落缩进而非编号级 `w:ind` | 本轮新增：README 字段清单的 `w:ind` 指编号级左缩进，规则使用的是段落级左缩进，字段语义差异保留 |
| 回读审计盲区、全矩阵缺口（WPS/LibreOffice、页眉页脚脚注文本框、3-8 级） | 重归属：未纳入本轮，保留开放 |
| `transaction.py` 排序修正欠独立工单 | 重归属：未纳入本轮，保留开放 |

## 红灯与绿灯

红灯（逐项中和后重跑 `tests/test_v6_residuals.py`，`scripts/.venv/bin/python3`）：

- R1 单独中和（容差设为 `10 ** 9`）：`1 failed, 2 passed, 7 deselected`，失败项 `test_v6_r1_indented_sublist_without_new_instance_resolves_deeper`。
- R2 单独中和（`_numbering_ids` 抛 `NotImplementedError`）：`2 failed, 1 passed, 7 deselected`。
- R3 单独中和（`ambiguity_records` 置空）：`2 failed, 8 deselected`。
- R4 单独中和（夹层记录不写）：`1 failed, 1 passed, 8 deselected`。

中和前该文件整体：`4 failed, 6 passed`（仅中和 R1/R3/R4）；`8 failed, 2 passed`（R1-R4 全中和）。中和后按字节复原。

绿灯：

- 聚焦：`tests/test_v6_residuals.py` 与全部列表相关文件 → `198 passed`（含新增 10 项）。
- 全量（仓库根，`scripts/.venv/bin/python3 -m pytest tests/ -q`）：`1253 passed, 0 failed`（基线 1243 加新增 10 项），连跑两次一致。

## 真实转换复核

| 输入 | status | saved_output_audit | unexpected_styles | risk_warnings |
| --- | --- | --- | --- | --- |
| `input/test_baseline.md` | completed | passed | 空 | 空（键缺省） |
| `input/benchmark_report.md` | completed | passed | 空 | 空（键缺省） |
| 两级嵌套列表 Markdown | completed | passed | 空 | 空（键缺省） |
| 软换行续接文档 Markdown | completed | passed | 空 | 空（键缺省） |

T07 章标题与 IP 地址样例：`tests/test_t07_heading_body_anti_ambiguity.py` `7 passed`，样例保持 `completed` 且无诊断。普通可转换文档仍为 `completed`、`risk_warnings` 为空。

## 未做

- 未关闭约束 7 的「更广歧义来源」（父项竞争等仍不可从真实输入达到），仍作为开放残留。
- `LIST_END`、回读审计盲区、全矩阵缺口、`transaction.py` 独立工单未纳入本轮。
- 本轮未提交、未推送、未执行任何远程操作。

## R5 归属缺口与每章节重启（A/B/C）

### A 运行后的注记归入列表项（FIXED）

`scripts/wxdf/semantics/lists.py` 的尾部扫描不再把 `note` / `numbered_note` 当作终止块：运行之后的注记归最后一个列表项，状态为 `ITEM_CHILD_BLOCK`（README 候选状态枚举把「注」列在该状态下），注记自身的受保护角色、样式与文本都不变（真实 DOCX 实测仍为注记样式、无 `w:numPr`）。注记是原先**完全无归属**的那一类；七类里 `formula` 仍作为段落续接（`ITEM_CONTINUATION`），因此「七类齐备」的说法已更正为「注记落地」。副作用：注记不再终止尾部扫描，因此注记**之后**的正文也会归入同一列表项（约束 5 要求注释不独立终止外层列表，故这是预期行为，此前未记录，现已记录）。复审判定该副作用与约束 5 一致。
护栏：`test_r5_note_after_implicit_run_is_owned_by_the_item`、`test_r5_note_not_after_a_run_stays_protected_outside_list`（不在运行之后的注记不受影响）、`test_r5_docx_note_after_run_renders_with_note_style_under_the_item`（真实 DOCX 端到端，注记仍为注记样式且挂在最后一个列表项下）。

### B 两个列表项之间的表格归前一项（FIXED）

`[列表项][表格][列表项]` 原先表格留在根节点无人归属，因为尾部扫描只覆盖运行**最后一项之后**的内容。现在运行内部的表格归属**前一个列表项**：表格不携带编号或层级证据，唯一可用证据是源相邻性，且 README 约束 5 要求表格不独立终止外层列表。运行本身的拆分/合并行为未变。复审补充两点：归属「前一项」是一致性约定而非相邻性强制（表格同样直接邻接后一项），本实现沿用了既有的尾部规则；另外**运行内**表格归前一项，而**作为运行尾部**的表格归该运行所属层级的父项，同一物理位置在两种情形下归属不同，此不对称已记录为已知行为。
护栏：`test_r5_table_between_two_items_is_owned_by_the_preceding_item`、`test_r5_body_between_items_keeps_two_runs_unchanged`（T05 的 `[项][正文][项]` 行为不变）、`test_r5_docx_table_between_items_hangs_under_the_preceding_item`（真实 DOCX 端到端）。

### C 每章节从 a) 重新开始（FIXED）

`normalization` 在**每个标题边界**为**每个有序角色**选择**新的编号实例**（同一抽象定义、独立计数器，写入 `w:startOverride=1`），`rendering` 把该实例写进 `w:numPr`。因此每章各自的列表都从 `a)` 开始，而不再全文档连续。复审更正：边界是**任意标题**（二级标题同样触发），作用对象是**任意有序角色**（深层的 `list_decimal` 也会拿到新实例），不是「章节边界 + 仅顶层」；代价是编号实例随「有有序列表的标题」线性增长（实测 150 章 / 900 项为 170 个 `w:num`，抽象定义不重复、id 不与模板冲突）。这也意味着跨标题的同 `numId` 续接会被重启，这是决策 1 与约束 6 的意图，但确实覆盖了约束 8 的同序列续接。
实测：`# 章一 / 1. 甲一 / 2. 甲二 / ## 章二 / 1. 乙一 / 2. 乙二` 保存后，第一章列表用 `numId=3`，第二章列表用新实例 `numId=22`（`abstract 4`，`startOverride=1`），两者计数器独立。
护栏：`test_r5_two_chapters_restart_top_level_ordered_numbering`（断言两章使用**不相交**的编号实例，且可见标记 `甲一`=`乙一`、`甲二`=`乙二`、`甲一`!=`甲二`）。该断言在 C 之前会失败：两章共用一个实例时集合不相交不成立。

### 本轮关闭与新增

| 残留 | 处置 |
| --- | --- |
| 「运行之后的注记不作为列表项内容」（T05/T06/T10） | 关闭（A） |
| 「两个列表项之间的表格无归属」（T05/T06/T10） | 关闭（B） |
| 「顶层有序编号全文档连续，未写 `w:startOverride`，决策 1 的每章节重启未落地」（T08/T10） | 关闭（C） |
| 约束 5 的「列表项内容」一半对注记与运行内表格 | 关闭（A/B）；表内嵌套列表与页眉页脚等容器仍归后续 |
| `tests/test_v6_residuals_r5.py` 初版未放行 `.gitignore`，fresh checkout 会丢失 | 已修正（放行并复核可见） |

复审另更正三处口径：`tests/test_v6_residuals_r6.py` 的 P4 说明原先写成「两种路径都不论段数」，与代码不符（DOCX 可见标记路径保留 3+ 段列表规则），已改；M3 的「样式 numPr 层级不匹配」用例原先只断言 `semantic_level <= 1`，在 `items` 为空时恒真，已改为断言「不产生列表项且留下编号诊断」；P1/P4 的护栏清单已补全。

仍未关闭：`tests/modular_support.py` 的编号模拟器仍不建模 `w:lvlOverride`/`w:startOverride`（R5 开始写出 `w:startOverride` 后这条更相关）、`LIST_END` 未发出、约束 7 的字面数值阈值、矩阵空白格（WPS/LibreOffice、页眉页脚/脚注/文本框、3 级以上层级）、`transaction.py` 独立工单、同类别相邻新 numId 的嵌套启发式、跨类别 `ilvl=0` 子表不自动嵌套、缩进细化在编号组之外与不回头运行时不生效。

追加轮 R6 见下节，本节对应的矩阵空白格在 R6 中重新处置。

## R6 受保护名单精度与全矩阵缺口（P/M）

本节承接 R1-R5 与 `status.json` 的开放残留，记录受保护名单四项精度（P1-P4）与 README 测试矩阵四处空白（M1-M4）的定案、实现与证明。按时间增量补全。

### 路径声明

按 AGENTS.md「改 .py 前必须先声明路径」。

架构定案（来源 `docs/execution-v6/README.md` 补充说明）：

- `scripts/wxdf/semantics/protected.py`（受保护名单单一所有者）
- `scripts/wxdf/semantics/lists.py`（`_is_protected` 消费方）

实施实现（本智能体实现）：

- `scripts/wxdf/semantics/protected.py`（新增 `protected_style_reason` 与 `_looks_like_list_style_name`；`_is_ipv4`、`_looks_like_formula` 与 `decimal_chain` 分支补记本轮口径）
- `scripts/wxdf/semantics/lists.py`（`_is_protected` 的样式保护改接 `protected_style_reason`）
- `tests/test_v6_residuals_r6.py`（本轮新增 22 项）
- `.gitignore`（放行新增测试文件）

`scripts/wxdf/transaction.py` 与 `scripts/wxdf/run_result.py` 未改动（禁改范围）。

独立审查：本轮由实施智能体单独完成，未在本轮内执行独立审查，作为剩余步骤保留。

### P1 低八位 IPv4 门限（KEEP，口径记录）

`_is_ipv4` 要求至少一段大于 9，因此 `1.2.3.4` / `4.2.1.3` 不作为地址，纯文本路径落到 `protected_text_reason` 的 `manual_heading_marker`。

定案：保留门限（任务允许「在证据中说明门限有意延迟该形态并给出理由」）。理由：

1. 该形态与四级章节号完全同形，任务原文与 `evidence/T07.md` 都把 `1.2.3.4` 记作 section number；
2. 任务明确要求不要把 `1.2 需求分析` 一类章节号文本保护为地址；
3. 延迟不丢内容：纯文本路径仍判为手工标题（受保护），块保持正文；
4. 矩阵依赖三段落以上的点号标记作为点号列表标记，`tests/test_modular_lists.py::test_docx_visible_list_markers_are_removed_when_list_semantics_materialize` 把 `4.2.1 复合编号需求` 计为列表项并剥离标记。

已记录的门限后果：DOCX visible_marker 路径下文本以低八位点号开头的段落（例如 `1.2.3.4 地址规划`）在段数小于 3 之外的情形下物化为列表项。`test_r6_p1_deferred_quad_in_the_visible_marker_path_is_a_dotted_list_marker` 锁定该行为，本轮记录为已知取舍（与 `4.2.1` 的处理一致）。

守卫测试（KEEP，钉住）：`test_r6_p1_low_octet_quad_is_deferred_to_a_manual_heading`、`test_r6_p1_real_addresses_are_caught_and_invalid_shapes_are_rejected`、`test_r6_p1_deferred_quad_in_the_visible_marker_path_is_a_dotted_list_marker`。`999.1.1.1`、`256.0.0.1`、`1.2.3`、`1.2.3.4.5` 保持拒绝，`1.2 需求分析` 保持 `manual_heading_marker`。

### P2 公式符号分支与 CJK 守卫（KEEP，口径记录）

`_looks_like_formula` 的显式符号分支不套 CJK 守卫，因此 `面积 = 长 × 宽` 判为 `formula`。`evidence/T07.md` 工作项 3 的表格已把这条记作有意设计（CJK 守卫只作用于纯等式分支）。本轮据此定案保留，并用 docstring 与测试让代码和证据一致：

- 含显式数学符号的 CJK 等式判为 `formula`；
- 只含 `=` 而无数学符号的 CJK 句子不判为公式。

守卫测试（KEEP，钉住）：`test_r6_p2_cjk_equation_with_a_math_symbol_is_a_formula`、`test_r6_p2_cjk_prose_with_only_an_equals_sign_is_not_a_formula`。

### P3 列表样式名不被标题吞掉（FIXED）

`lists.py._is_protected` 的 `"标题" in style` 把名字同时含列表词的样式 `列项标题` 也判为受保护标题，因此它不再是列表样式。定案：把样式保护收敛回单一所有者，在 `protected.py` 新增 `protected_style_reason`（含 `_looks_like_list_style_name`），其中标题分支跳过明显为列表样式的名字（`列项` 与 `List*` 族）。`章标题` 一类标题样式仍受保护。

红灯：改动前 `tests/test_v6_residuals_r6.py` 为 `1 failed, 21 passed`，失败项 `test_r6_p3_list_item_title_style_is_not_a_protected_heading`（样式 `列项标题` 的块没有成为列表项）。

守卫测试：`test_r6_p3_list_item_title_style_is_not_a_protected_heading` 与回归护栏 `test_r6_p3_chapter_heading_style_stays_protected`。参考模板无任何样式同时含列表词与标题（已核），故真实模板无行为变化。

### P4 手写点号标记视作手工标题（KEEP，取舍记录）

visible_marker 路径下 `decimal_chain` 且段数小于 3 的标记判为手工标题，`1.2 需求分析` 因此保持正文。取舍：真正的两段嵌套列表项若文本为 `1.2 需求分析` 会丢失编号。定案：保留。理由：两段点号是章节号的规范形态；README 约束 6 与 T07 把「标题永不成为列表项」定为硬不变量；块保留源文本，丢失的只是列表容器。段数是刻意的判定输入：三段落以上的点号标记在 DOCX 路径下作为点号列表标记剥离（`tests/test_modular_lists.py`），文本路径仍判为手工标题。本轮初版曾把规则统一为任意段数均为手工标题，`tests/test_modular_lists.py` 随即变红，故此路撤回，取舍按事实保留。

守卫测试（KEEP，钉住）：`test_r6_p4_two_segment_dotted_marker_stays_a_manual_heading`、`test_r6_p4_two_segment_dotted_marker_is_a_manual_heading_in_the_visible_marker_path`、`test_r6_p4_three_segment_marker_follows_the_dotted_list_rule`、`test_r6_p4_three_segment_marker_is_a_list_marker_end_to_end`、`test_r6_p4_single_level_markers_still_form_a_list`。

### M1 页眉 / 页脚 / 脚注 / 文本框 / 内容控件容器（RECORDED AS AN OWNED GAP）

源码事实：`wxdf.parsers.parse` 只遍历 `document.element.body.iterchildren()` 中的 `w:p` 与 `w:tbl`（`add_table` 再递归读单元格段落）。因此：

- 页眉、页脚、脚注、尾注：各自独立 part，源码解析器不读取；
- 文本框：内容在 `w:txbxContent` 内嵌于段落 run，不作为 body 直接子节点；
- 内容控件 SDT：`w:sdt` 是 body 直接子节点，但遍历只处理 `w:p` 与 `w:tbl`，`w:sdt` 被跳过。

后果：这些容器里的编号列表被静默丢弃（既不转换，也不产出诊断）。测试 `test_r6_m1_header_textbox_and_sdt_lists_are_not_read` 实测头部、文本框、SDT 三段文本均不在 facts 中，且每个 unit 的 `part` 都是 `word/document.xml`；正文列表仍被识别。修复需要读取额外 part 与嵌套元素，超出可安全容纳的范围，故记录为有主缺口（属主：后续全矩阵收口）。

矩阵命名复核：正文与表格单元格属于已读容器；表格单元格（含水平合并格）内的编号列表可被读为列表项（实测 2 项）。README「不在范围」继续覆盖「合并单元格内多级列表」与「内容控件内列表」，该注记保持不变。

### M2 三至八级夹层（COVERED）

两级策略把 `semantic_level >= 2` 夹到 `render_level = 1` 并产出 `list_render_level_clamped`。`test_r6_m2_levels_three_to_eight_clamp_to_the_second_level` 表驱动覆盖深度 3 至 8，逐深度断言 `status=completed`、`saved_output_audit.passed=true`、无 `risk_warnings`、夹层记录的 `semantic_level` 集合等于 `{2..depth-1}` 且 `render_level` 全为 1、`item_count` 等于深度。`test_r6_m2_two_level_list_emits_no_clamp` 为回归护栏。

### M3 合成 WPS / LibreOffice 形态（COVERED，真实验收仍为外部缺口）

按已知生产者形态构造合成 DOCX 并断言优雅行为：

- `w:num` 的 `abstractNumId` 指向缺失的 abstract：`test_r6_m3_missing_abstract_num_is_graceful`，记录 `invalid_numbering_definition`，块保持正文，端到端转换 `saved_output_audit.passed=true`；
- 段落样式 numPr 带越界 `w:ilvl`：`test_r6_m3_style_numpr_with_mismatched_ilvl_is_graceful`，块保持正文且不产生失控深层列表项；
- 一段列表被拆到多个 `numId`（带 `w:startOverride`）：`test_r6_m3_run_split_across_num_ids_is_graceful`，`status=completed`、5 项齐备、层级有界；
- `w:ilvl` 完全省略：`test_r6_m3_omitted_ilvl_defaults_to_level_zero`，读作 0 级列表。

真实 WPS 与 LibreOffice 文件不可得，真实生产者验收仍为外部缺口（属主：`kylin_v10_arm64_real_machine_acceptance` 与后续人工验收）。

### M4 相同缩进不同层级 / 不同缩进相同层级（COVERED）

在 R1 缩进规则下：

- 相同缩进、不同声明层级：声明层级成立（缩进簇只在前一更浅簇出现后才加深），`test_r6_m4_same_indent_different_level_keeps_the_declared_levels` 钉住 `effective_ilvl` 为 0/1/2 且逐级挂父；
- 不同缩进、相同声明层级：回到更浅簇时加深一级，`test_r6_m4_different_indent_same_level_resolves_deeper` 重新钉住（专门覆盖在 `tests/test_v6_residuals.py` 的 R1 三项内）。

### 关闭与重归属的残留

| 残留（来源） | 处置 |
| --- | --- |
| 「名字同时含列表词与标题的样式被当作受保护标题」（T07/T10） | 关闭（P3：样式保护收敛到 `protected_style_reason`，列表样式不再被标题吞掉） |
| 「文本以手写点号标记开头的块被判为手工标题」取舍（T07/T10） | 关闭（P4：按事实保留，段数作为判定输入，测试与证据记录取舍） |
| 「低八位 IPv4 落到 `manual_heading_marker` 而非 `ip_address`；公式符号分支未套 CJK 守卫」（T07/T10） | 关闭（P1 有意延迟并记录理由与后果；P2 保留并把意图写进 docstring 与测试） |
| 矩阵空白格：层级只覆盖 0 至 2 级 | 关闭（M2：表驱动覆盖 3 至 8 级） |
| 矩阵空白格：容器只覆盖正文与表格 | 部分关闭（M1：确立源码解析器只读 body 段落与表格，页眉/页脚/脚注/尾注/文本框/SDT 内的列表被静默丢弃，记录为有主缺口） |
| 矩阵空白格：WPS 与 LibreOffice 输入未覆盖 | 部分关闭（M3：合成生产者形态已覆盖并断言优雅；真实 WPS/LibreOffice 验收仍为外部缺口，属主 `kylin_v10_arm64_real_machine_acceptance`） |
| 矩阵项「相同缩进不同层级 / 不同缩进相同层级」 | 关闭（M4：两项都有测试钉住） |

### 红灯与绿灯

红灯（把 `lists.py._is_protected` 的样式判定临时还原为旧行后运行 `tests/test_v6_residuals_r6.py`）：

```
1 failed, 21 passed
FAILED tests/test_v6_residuals_r6.py::test_r6_p3_list_item_title_style_is_not_a_protected_heading
```

本轮唯一行为改动是 P3（P1/P2/P4 为保留口径，配钉住测试），故红灯只此一项。

绿灯：

- 聚焦：`tests/test_v6_residuals_r6.py` 为 `22 passed`；连同列表相关文件保持通过。
- 全量（仓库根，`scripts/.venv/bin/python3 -m pytest tests/ -q`）：`1283 passed, 0 failed`（基线 1261 加新增 22 项），连跑两次一致。

### 真实转换复核

| 输入 | status | saved_output_audit | unexpected_styles | risk_warnings |
| --- | --- | --- | --- | --- |
| `input/test_baseline.md` | completed | passed | 空 | 空 |
| `input/benchmark_report.md` | completed | passed | 空 | 空 |
| 两级嵌套（有序包子） | completed | passed | 空 | 空 |
| 两级无序 | completed | passed | 空 | 空 |
| 交叉嵌套（有序包无序） | completed | passed | 空 | 空 |
| 交叉嵌套（无序包有序） | completed | passed | 空 | 空 |
| 三级 | completed | passed | 空 | 空 |
| 列表后注 | completed | passed | 空 | 空 |
| 两项之间表格 | completed | passed | 空 | 空 |
| 两章各自重启 | completed | passed | 空 | 空 |
| T07 章标题样例（`章标题` 样式加 `w:numPr`） | completed | passed | 空 | 空 |
| T07 IP 地址样例（`192.168.1.1` 正文段） | completed | passed | 空 | 空 |

普通可转换文档保持 `completed`、`saved_output_audit.passed=true`、`unexpected_styles` 空、`risk_warnings` 空。

### 未做

- 未读取额外 part（页眉、页脚、脚注、尾注）与嵌套文本框、SDT 内的内容，故这些容器的列表仍被静默丢弃（M1，有主缺口）。
- 真实 WPS 与 LibreOffice 验收不可执行（M3，外部缺口）。
- 独立的审查轮未在本轮内执行。
- 本轮未提交、未推送、未执行任何远程操作。
