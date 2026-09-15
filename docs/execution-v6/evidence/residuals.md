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
