# 当前架构收敛进度

## 2026-09-15 execution-v6 T05 列表项混合内容

- 首轮新增红灯测试文件 `tests/test_t05_mixed_content.py`，实施前 5 项失败：隐式列表段后的正文段没有归属记录、`child_blocks` 为空、`owner_item_id` 查询返回 `None`。首轮实现后 6 项通过。
- `lists.py` 新增 `_tail_content_owner` 与 `_implicit_tail_content`：隐式列表运行之后的正文段记为 `ITEM_CONTINUATION`、表格/图片/引用/代码为 `ITEM_CHILD_BLOCK`（注记属受保护块，当前不归入列表项，见残留 14），遇新列表段、受保护块或运行结束即停止；`analyze_lists` 结果新增 `child_blocks` 与源序 `owned_content`，续接与子块按同一源序追加，`continuations` / `child_blocks` 保留为同一记录的过滤视图。
- `source_ast.materialize_list_semantics` 统一隐式与显式列表项的归属模型，DOCX 派生项由 `owned_content` 组装 `content_blocks`，内容移动候选携带 `source_index`。
- FIX 轮：端到端验证发现测试未覆盖的顺序回归。样例 DOCX（`[num 第一项][num 第二项][续接段][说明段][2x2 表][尾段]`）修复前渲染为 `第二项, tbl, 尾部正文, 续接正文, 表格前的说明, 表 1`，第 2 项 `content_blocks` 把所有正文段排在所有非正文块之前，AST `children` 又按节点 id 排序，三处都不是源序且题注落在表格之后（违反 AGENTS.md 决策 3）。确认机制：归属记录在源序前被拼接，内容移动无排序键；题注在基础 revision 上按目标父节点与槽位锚定，而表在同一批次被移走、创建先于移动落地。证伪题目提示中「拼接也解释了题注落在表格之后」的猜测。
- FIX 轮修复落在归属方：`owned_content` 保持源序；内容移动按 `source_index` 排序追加，AST children 与源序一致；生成题注候选携带 `anchor_node_id` / `anchor_position`，`transaction._order_stage_decisions` 把锚定创建分流到移动之后、`_apply_create_node` / `_applied_create_node` 在锚点当前父节点下解析槽位，题注紧贴其表格上方。FIX 未改写 T04 的两条创建排序规则（新建父节点升序、既有父节点降序），只叠加移动排序键与锚定创建分流。
- 护栏测试：`test_t05_docx_item_tail_renders_in_source_order_with_caption_above_table` 等 7 项 FIX 轮测试在修复前工作树 7 failed、修复后 13 passed；源序作为一般不变量覆盖交错尾部 `[段落, 表格, 段落, 图片]` 与以表格开头的尾部两种形态，不针对具体样例特判。首轮 `test_t05_docx_item_owns_table_image_quote_code_content` 的断言由旧拼接顺序更正为源序。
- Markdown 显式容器尾部归属判定：Markdown 适配器产出显式 `list` / `list_item` 容器，其后的非缩进正文段、引用与表格是真正的文档级块（缩进续写已并入列表项段落文本），证据不支持把根兄弟尾部吸收进末项，故记录为残留交 T10 全矩阵收口，不实现。
- 真实转换：修复后样例 `status=completed`、`exit_code=0`、`source_ast_revision=2`、`model_audit.passed=true`、`saved_output_audit.passed=true`，`list_semantics` 为 `list_count=1 / item_count=2 / continuation_count=3 / diagnostics=[]`，渲染为 `第一项 / 第二项 / 续接正文 / 说明 / 表 1 / 表格 / 尾部正文`。
- 全量测试 `1201 passed, 0 failed`（`ca27223` 基线 `1186 passed`，首轮 6 项至 1192，FIX 轮 7 项，复审护栏 2 项）；`tests/test_t01_list_fixtures.py` 保持 11 passed，`tests/test_caption_placement.py` 8 passed，`tests/test_t04_nesting.py` 12 passed，`tests/test_t03_single_level_lists.py` 21 passed。
- 已记录残留：Markdown 显式容器尾部归属与显式列表项不记录 `content_blocks`（T10）、同 sequence 续接（T06）、`lvlRestart` 与 `LIST_END`（T06/T07）、同类别相邻新 numId 启发式与跨类别 ilvl=0 子表（T06/T08）、歧义报告可达性（T09）、pStyle 层级继承与 `num_id` 双源（T10）、产品路径非零多级输出受阻（T08）、父链比对不含每级序号值（T09）、渲染夹层口径（T08）、`transaction._order_stage_decisions` 的文件归属（T10）。
- 证据见 `docs/execution-v6/evidence/T05.md`，状态见 `docs/execution-v6/status.json`。未提交、未推送；预存 `_build/` 与 `paseo.json` 未处理。

## 2026-09-15 execution-v6 T04 多层嵌套与父项裁决

- 新增红灯测试文件 `tests/test_t04_nesting.py`（10 项），实施前 9 项失败：新 numId 在 ilvl=0 未识别为子级、显式列表容器不做约束校验、子项先于父项无显式诊断、回读审计无父链比对、候选带未被消费的 `score`。
- `lists.py` 新增 `_numbering_sequence`（numId+abstractNumId 实例身份）、`_resolve_sublist`（新实例在 ilvl=0 的嵌套裁决，含同类别约束）、`_structural_list_level`（祖先 `list` 容器计数）；隐式列表段循环改为先取声明层级、再按实例身份判定子表层级与父项、再走孤儿与跳级降级、最后对运行起始孤儿做滞后父项检测。
- 冻结红灯转绿：`test_t01_sublist_new_numid_ilvl0` 与聚合用例 `test_t01_failing_samples_satisfy_expected_semantics` 由 2 failed 转为 10 passed，冻结样例文件未改动。
- 工作项 3：运行起始的深层孤儿追加 `late_list_parent`（warning，含 `declared_level` 与 `parent_appears_after`），不再静默挂到最近项。
- 承 T03 残留 9：`_validate_state_constraints` 新增显式 `list` 容器的约束 3 校验；约束 4 对结构层级不可达已在 docstring 说明。
- 承 T03 残留 6：`audit_list_readback` 改用 `_expected_list_tree` 与 `_level_parent_chain` 追加父链比对，不一致产出 `list_parent_chain_mismatch`，与 `list_readback_mismatch` 一同并入 `saved_output_audit` 与发布门禁。
- 承 T03 残留 7：删除 `_state_candidate.score` 与每块最高分预过滤，候选完全由 Resolver 的证据类别裁决。
- `transaction._order_stage_decisions` 修正结构创建顺序：同批次新建父节点的子创建按升序 index 落地（空子列表填充即源顺序），既有父节点保持降序（题注锚点依赖尾部回插）。复审更正口径：该修正不是嵌套样例顺序的前提（嵌套容器只有两个子项，升降序一致），实际修复的是三项以上新建容器（如平坦多项 DOCX 列表）的源顺序；护栏 `test_t04_created_container_preserves_source_order_for_three_or_more_items` 在旧排序下失败、新排序下通过。该文件不在 README 架构定案清单，已声明并记录为独立工单候选。
- 真实转换：三层有序加交叉嵌套 Markdown 与三个新 numId 的 DOCX 样例树形正确（深度逐层递增），`status=completed`、`saved_output_audit.passed=true`、无列表诊断。
- 全量测试 `1186 passed, 0 failed`，基线为 `2 failed, 1172 passed`；T01 冻结库由 `2 failed, 9 passed` 转为 `11 passed`。
- 复审补充护栏：三层纯无序嵌套端到端、三项以上新建容器源顺序回归（后者在旧排序下实测失败）。
- 已记录残留：同类别相邻新 numId 的嵌套判定仍为启发式（T06）、结构创建排序修正缺少工单归属（T10）、pStyle 层级继承仍未端到端生效（T10）、产品路径非零多级输出受阻于有序钉死规则（T08）、跨类别 ilvl=0 子表与隐式段尾部续接（T05）、`lvlRestart` 与 `LIST_END`（T06/T07）、`num_id` 双源与显式容器声明层级（T10/T09）、歧义报告可达性（T05）、渲染夹层口径（T08）、父链比对不含每级序号值（T09）。
- 证据见 `docs/execution-v6/evidence/T04.md`，状态见 `docs/execution-v6/status.json`。未提交、未推送；预存 `_build/` 与 `paseo.json` 未处理。

## 2026-09-15 execution-v6 T03 第一刀：单级有序/无序列表端到端

- 新增红灯测试文件 `tests/test_t03_single_level_lists.py`，实施前多项失败：候选状态编码缺失、Resolver 未裁决状态集合、回读审计函数缺失、渲染器从不写显式编号、约束 3/4 无执行者。
- `lists.py` 从「发唯一 move 候选」扩展为「按原始证据发一组候选状态」：新增 `LIST_STATE_ENUM / _state_candidate / _state_candidates_for_paragraph / _build_state_candidates / _validate_state_enum / _arbitrate_state_candidates / _validate_state_constraints`。候选只消费 `SourceFacts`，`items / lists / continuations` 由裁决结果物化。
- 复用既有 `wxdf.resolver.resolve` 裁决状态候选：每块唯一归属、证据强度优先级、同分同强度冲突进 `unresolved`。`ambiguous_list_state` 与 `status="unresolved"` 只保留给同强度平局（`test_t03_genuine_equal_strength_tie_is_unresolved`）；该平局当前仅由合成证据触发，真实输入可达性归 T04/T05。
- 解析器的 `numbering_status="ambiguous"`（`isolated_generic_numbering`）表示证据不足且解析器已裁决为正文：该块保持正文（`OUTSIDE_LIST`）并产出 `low_confidence_list_marker`（info）降级记录，不进 `unresolved`。第四轮 FIX 1 按 README 不变量 7 重标定该门控（红灯先行：单个编号段与 `[编号][正文][编号]` 修改前均被判 `completed_with_warnings`），普通文档（单个编号段、`[编号][正文][编号]`、`input/test_baseline.md`、`input/benchmark_report.md`）保持 `completed` 且 `risk_warnings` 为空；`tests/test_issue3_acceptance.py` 的 L09 恢复 `_assert_native_completed`。
- `_validate_state_constraints` 显式承担 README 约束 3（深层须有父项）与约束 4（逐级递增），违规并入 `result["diagnostics"]`，因此会设置状态并进入发布阻断集，且层级状态按运行与作用域复位以与物化结果一致。第四轮 FIX 2：同一节点的跳级只报一次约束 4，不再同时产出约束 3 的「无父项」发现；作用域仅限隐式列表段（`_implicit_list_runs_for_parent` 对 `list`/`list_item` 父项返回 `[]`）。
- `rendering.py` 写出真实编号：按 CT_PPr 顺序插入直接 `w:numPr`（`w:numId` 复用 `numbering_ids`，`w:ilvl` 按 render level）；层级未被目标编号实例定义时发出 `list_render_level_clamped` 诊断，不再静默夹回。
- `auditing.py` 的 `audit_list_readback` 读取保存文件的 `w:numPr/w:numId` 与 `w:numPr/w:ilvl`，与语义列表比对扁平 `(list_kind, numId, ilvl)` 读取序列；三类篡改（改 ilvl、改 numId、删 numPr）均失败并阻断发布。`check_saved_output` 先认直接 `w:numPr`，不再与渲染结果冲突。
- 真实转换（`input/test_baseline.md`、`input/benchmark_report.md`、单级有序+无序 md、两层有序 md）均 `status=completed`、`saved_output_audit.passed=true`；单级有序输出 `numId=3 ilvl=0`，无序输出 `numId=5 ilvl=0`。单级有序+无序 md（3 项有序+2 项无序）的列表语义计数复核为 item 5、列表 2、候选 16、裁决 13、outside 3、conflict 0。
- 全量测试 `1172 passed, 2 failed`；两项剩余红灯仍为 T04 工作项 2 的 `numId+ilvl=0` 子列表样例。第四轮 FIX 1 的普通文档回归并入既有测试（`test_t03_ambiguous_numbering_reaches_published_report`、`test_t03_real_conversion_surfaces_readback_and_render_clamp`），未新增测试函数，通过数不变。
- 已记录残留：pStyle 层级继承尚未端到端生效（T04/T10）、非零多级输出的产品路径受 `normalization.normalize` 的有序钉死规则限制（有序项 `target_level=0`，只用 `list_letter`/`numId=3`，`list_decimal`/`numId=8` 从不被使用）而模板本身已有多级实例（T04/T08）、候选评分只做每块最高分预过滤（T04）、`num_id` 双源与 `lvlRestart` 未消费沿用 T02 残留（T10/T06）、`LIST_END` 未发出与回读比对粒度归属 T06/T07/T04/T09、隐式列表段无尾部续接（约束 5 缺口，T05）、显式列表容器未做约束 3/4 校验（T04/T05）。第四轮 FIX 3 删除死代码 `lists._reading_order` 并把约束表第 2 行改为按源顺序物化与渲染的不重排机制。
- 证据见 `docs/execution-v6/evidence/T03.md`，状态见 `docs/execution-v6/status.json`。未提交、未推送；预存 `_build/` 与 `paseo.json` 未处理。

## 2026-09-15 execution-v6 T02 原生编号目录 + 列表语义模型扩展

- 接手时 T02 处于中断状态：`parsers.py` 的 `_parse_docx` 三分支迭代引用了未定义的 `body_paragraphs`，DOCX 解析整体抛 `NameError`，全量 67 项失败。
- 已把编号分析恢复为「单次构建原生目录 + 单次分组裁决」：`build_numbering_catalog` 产出 `num_to_abstract / abstract_levels / abstract_pstyle / num_level_overrides`，正文段落由同一份目录驱动。
- 原生目录覆盖 `w:start / w:numFmt / w:lvlText / w:pStyle / w:suff / w:lvlRestart / w:lvl/w:pPr/w:ind / w:lvlOverride / w:startOverride / w:abstractNumId` 与缺失损坏定义的降级诊断。
- 段落样式 `numPr` 的 `ilvl` 按 MS NumberingProperties 规则忽略，`effective_ilvl` 由 `w:lvl/w:pStyle` 关联（样式 id 或样式名）确定。
- 证据字段经 `lists.py` 透传到列表项：`source_ilvl / effective_ilvl / semantic_level / render_level` 分开保存，`start_value / sequence / restart_scope` 取代布尔 `restart`，隐式列表组带 `restart_expression`。
- 新增 `tests/test_t02_numbering_catalog.py` 14 项目录与语义模型测试，全部通过。
- 独立双轴审查发现并已修复：编号级 `w:ind` 与 `lvlRestart` 读错嵌套层、`sequence` 相等性把手工标记列表拆成多个、以及若干死字段与死合同项。复审确认修复并给出复现反例。
- 全量测试 `1151 passed, 2 failed`；两项剩余红灯为 T04 工作项 2 的 `numId+ilvl=0` 子列表样例，属 T01 冻结的失败样例库。
- 已记录残留：正文证据的 `num_fmt / lvl_text / numbering_status` 仍来自按声明 ilvl 的 legacy 分组裁决，pStyle 层级继承尚未端到端生效，并入 T03/T04 与 T10。
- 证据见 `docs/execution-v6/evidence/T02.md`，状态见 `docs/execution-v6/status.json`。未提交、未推送；预存 `_build/` 与 `paseo.json` 未处理。

## 2026-09-14 真实 DOCX 基础格式语义修复

- 用户确认开始处理奇安信真实文件暴露的基础格式问题，要求按问题类型归入对应模块，减少对 AST 到文档渲染逻辑的干扰。
- 已读取 implement、tdd、planning-with-files-zh 和 codebase-design 技能；继续遵循 AST-first、红灯先行、模块深接口和独立审查规则。
- 已确认四类实现归属：可见列表标记归 DOCX SourceFacts 与 list semantics，正文首部制表符归段落呈现语义，作者题注归 caption semantics，深层标题归 structure semantics。
- 当前仅完成诊断和红灯确认，未修改 Python 源码。
- 列表切片已完成：DOCX SourceFacts 识别无 `numPr` 的 `1.`、`a)` 等可见标记，列表物化阶段清除源文本编号，保留源证据，专项测试 14 项通过。

2026-09-12：完成当前仓库状态、T09 证据、Registry、Kernel、ModuleExecutionContext 和 native-reviewer 的架构核对。确认 T00 至 T09 已完成，架构基本合理，但复核链重复编排是首要收敛风险。

2026-09-12：新增 `docs/execution-v4/architecture-convergence.md`，将后续工作收敛为 AC-1 至 AC-4 四张大工单。当前只固化方案和完成定义，未修改 Python 代码，未执行远程发布动作。

2026-09-12：建立统一复审规则：固定候选提交、红灯测试、实现、独立审查、修复、复审、证据记录。复审发现必须修复、有明确外部归属，或转为阻断性后续工单。

# 历史进度：Skill 新架构完整切换

2026-09-10：Codex 完成 Skill 视角审查并建立 execution-v4 十张串行工单。本地计划提交为 `fdfdaa3`。已创建新的项目任务，由 Codex 总控并委派 Claude 实施，从 T00 开始，每单执行审查、修复和复审。

# 历史进度：方案 3.1 完整能力迁移

2026-09-08：已形成 `docs/modular-pipeline-migration-v3.1.md`，完成真实模板红灯、可执行 ModuleRegistry、`wx_full_rules` 必需能力预检和 `legacy-full-v1` 过渡 module。模块化 CLI 已恢复旧链路的标题、目录、题注、表格、finalizer、审计和文件协议能力。

2026-09-08：AST 2.0 已补充 title、header_rows、cell_role、生成题注来源和 caption_target 关联，模块化调试产物已切换至新 Schema。聚焦红灯全部转绿，全量测试 353 项通过。

2026-09-08：使用 `input/benchmark_report.md` 和 `assets/wx_template.docx` 完成真实转换、OOXML 检查和三页页面渲染。结构和专项审计通过，页面渲染的中文缺字在 legacy 对照件同样存在，已记录为当前验收环境限制。仍待完成真实 DOCX 输入和逐 module Native 替换。

2026-09-08：阶段 6 完成。TemplateBundle 已封装真实模板的样式、编号、表格、目录片段、附录分页和题注序列合同；Normalizer 与原生 Renderer 已通过该 interface 消费模板能力。下一阶段从 SourceRegion 开始逐项替换 `legacy-full-v1` 内部能力。

# 方案 3.0 执行包编制进度

2026-09-08：已核对仓库约束、运行入口和测试布局。使用 writing-for-agents 的分层任务入口和可核验完成条件，使用 planning-with-files-zh 保存当前编制进度。正在生成执行任务包，尚未运行实现基线或修改 Python。历史记录保留如下。

2026-09-08 编制完成：新增 docs/execution-v3/README.md、contracts.md、acceptance.md、status.json、evidence/README.md 及 T00 至 T16 任务卡，共 22 个文件。17 项均 pending，implementation_started=false。校验通过：方案 SHA256、无环顺序依赖、任务读路径、计划测试路径、全部 Markdown 链接、用语和空白。git diff --check 通过。版本和 Python 入口已实测，源码测试、真实转换和性能测试未执行。本轮文件受既有忽略规则影响，未暂存、提交或发布。

# 进度

## 2026-07-27

- 已确认修复范围和现有未跟踪文件 `_build/`、`paseo.json`。
- 当前处于阶段 2：实现统一列表层级解析。
- 已完成统一解析器和三组缩进型嵌套回归测试。系统 Python 缺少 pytest，待使用项目虚拟环境验证。
- 已通过列表专项 48 项测试与完整测试集 174 项测试。
- 静态编译与 `git diff --check` 已通过，完整测试集通过 173 项。
- 修复完成，未触碰预存的 `_build/` 与 `paseo.json`。
- 已修复审查发现的两项缺陷，完整测试集通过 175 项。

## 2026-08-23

- 已复现 Markdown 表题注“表  表 1 接口清单”，确认 source AST 题注缺少语义拆分。
- 已确认 Markdown 图片语法未生成 image 块，图题注会成为孤立题注。开始在解析阶段修复，并将补充端到端回归测试。
- 已新增 Markdown 表题注回归测试，初始失败确认解析阶段未生成 `caption` 块。Word `fldSimple` 的缓存编号不进入 python-docx 的 `paragraph.text`，测试改为核验 SEQ 域 XML 和缓存值。
- 已完成 Markdown 图表题注修复。新增表题和本地图题端到端回归测试，聚焦测试 10 项通过，全量测试 180 项通过，编译、OOXML ZIP 完整性和差异空白检查通过。

## 2026-08-24

- 启动阶段 25。用户指出将离线归档手工放入薄 Skill `artifacts/` 目录不符合常规 Skill 安装体验。
- 已确认现有 macOS ARM64 和 Kylin V10 ARM64 Release 归档本身就是完整离线 Skill，内含 `SKILL.md`、README、模板、启动脚本和 Nuitka 运行时。
- 公开 README 已改为直接将平台安装包交给 Agent，由 Agent 解压到自身 skills 目录。`artifacts/` 只作为薄 Skill 的平台清单和 SHA256 元数据目录。
- 已删除 POSIX 和 PowerShell 薄启动器的内置 `artifacts/` 归档分支及其回归用例。全量 261 项、Skill validator、shell 语法和差异检查通过。
- 已将现有 macOS ARM64 包直接解压到临时 `agent/skills/wx-doc-format`，零 `WX_DOC_FORMAT_*` 变量下 Skill validator 通过且 `--version` 返回 `0.12.18`。
- 已使用未变的 Nuitka 运行时重新封装两个候选资产。macOS 候选包在 ASCII 临时路径通过版本、真实转换和 Skill 校验；Linux 运行时与旧包逐文件一致，保持真机候选门禁。
- 新候选 SHA256：macOS `16891562fb46160d42f35858a241b85e36540380a600fac81136b83e19407b5c`，Kylin `9ae6498df81c66ae354ad9bade8dbf1c6ae3b90cdd8987dfe50d50ca57650817`。替换同版本 Release 资产需要用户明确授权。
- 私有提交 `bcad438` 已推送到 `dev/main`。公开仓库已用当前 Release 旧 SHA256 发布安全的说明和启动器收口提交 `85abfcc`，联网安装保持可用，公开历史三个提交且无 Python 文件。
- 用户已明确授权替换 `v0.12.18` 同版本 Release 资产。已替换 macOS ARM64、Kylin V10 ARM64 和 `SHA256SUMS.txt`，标签未移动，Release 保持正式状态。
- 新资产 GitHub digest 与本地候选一致，公开校验提交为 `a18ce0c`。公开历史共四个提交，仍无 Python 源码或字节码。
- 在线薄 Skill 空缓存启动已从 GitHub 下载新 macOS 包并返回 `0.12.18`。远程离线包已直接解压到 `agent/skills/wx-doc-format`，Skill validator、`--version`、Markdown 转 DOCX 和 ZIP 完整性全部通过。
- 发布用隔离 clone 和本地重封装候选目录已清理，`dist/local/` 原始建立制品保留。
- 启动阶段 24。确认公开 README 将 CLI 当成用户使用方法，并将离线介质环境变量写成安装要求。
- 公开 README 模板已改为触发词、普通模式、LLM 增强模式和交付结果说明。公开 `SKILL.md` 已补齐交互分支和 LLM 文件协议。
- POSIX 和 Windows 启动器现在优先读取 Skill 自身 `artifacts/` 的平台归档，缺失时才访问 GitHub Release。POSIX 和 Windows 对非 ASCII 默认缓存路径增加自动回退。
- 新增零 `WX_DOC_FORMAT_*` 变量、中文 HOME 和内置归档的首次启动回归。Nuitka 专项 40 项、全量 262 项、Skill validator、shell 语法和差异检查通过。
- 已使用真实 macOS ARM64 编译归档在空缓存、中文 HOME 和零 `WX_DOC_FORMAT_*` 变量环境完成自动安装、`--version` 和 Markdown 转 DOCX，成品 ZIP 完整。基准 Markdown 本身仍有两项守恒风险，未将其记为排版质量通过。
- 私有 `dev/main` 已推送 `217a1d9`。公开 `origin/main` 已通过全新隔离 clone 发布 `7c7f8af`，只修改 README、SKILL 和 POSIX/PowerShell 启动器。
- 公开仓库在线首次运行已从 GitHub Release 自动下载 macOS 归档，零 `WX_DOC_FORMAT_*` 变量返回 `0.12.18`。公开历史共两个提交且无 Python 源码或字节码，Release 三个资产保持 uploaded。
- 启动阶段 23，目标是在私有仓库明确源码开发、本地构建、薄 Skill 生成、公开发布和历史保护流程。
- 新增 `docs/development-workflow.md` 作为私有开发和发布流程的单一事实源，已覆盖仓库边界、日常开发、三平台本地构建、公开 Skill 组装、发布门禁和回滚。
- `AGENTS.md` 已改为强制读取流程文档，删除过时的公开分支 cherry-pick 规则；私有 README 已增加文档入口。
- Nuitka 分发专项 39 项通过，组装器 `--help`、文档链接、构建入口、禁用句式、破折号和 `git diff --check` 已核验。
- 流程文档已以提交 `0fe9ea6` 推送到私有 `dev/main`。GitHub 实时确认仓库为 private，默认分支为 `main`，本地 `dev` 与 `dev/main` 一致。
- 用户已明确授权强制重写公开 `main` 和替换 `v0.12.18` 标签，同时要求 README 只保留核心能力、安装方法和使用方法。
- README 已收口为三个标题：核心能力、安装方法、使用方法。私有模板提交 `bbfcaf1` 已推送到 `dev/main`。
- 已创建公开无父根提交 `a28f347`，使用显式 lease 和 atomic 强制推送同时更新 `main` 和 `v0.12.18`。Release 和三个资产保持有效。
- 全新完整克隆的 `git rev-list --all --count` 为 1，`main`、`origin/main` 和 `v0.12.18` 全部指向 `a28f347`，跟踪树不含 Python 源码或字节码。Skill validator 和线上首次运行通过。
- GitHub API 目前仍能通过已知旧 SHA `58b1498` 读取不可达提交，它已不存在于任何公开分支、标签、fork 或 PR 历史中。
- 本机旧公开工作树和两个发布分支已删除，本地 `main` 和 `v0.12.18` 已对齐新根历史，私有 `dev` 历史保持完整。
- 启动阶段 21。用户允许 Windows x86_64 制品暂缺，目标是先发布 macOS ARM64 和 Linux ARM64 可安装 Skill，同时保留可识别的 Windows 未提供状态。
- 新增公开薄 Skill 组装器、POSIX 平台引导器和 PowerShell 引导器。发布清单只列出实际存在的平台归档，Windows 缺失时返回 `WXDF-E-RUNTIME-UNAVAILABLE` 和退出码 3。
- 已生成 `dist/public-skill`，清单包含 `macos-arm64` 和 `kylin-v10-arm64`，`windows-x86_64` 列为不可用。公开目录无 Python 源码或字节码。
- 离线介质引导验证通过：macOS 启动器从 `WX_DOC_FORMAT_ARCHIVE_DIR` 获取归档，完成归档 SHA256、包内 SHA256、版本检查、安装和 Markdown 转 DOCX，`unexpected_styles=[]`。
- 分发专项 39 项和全量 261 项测试通过，公开 Skill validator、POSIX shell 语法、Python 编译与差异空白检查通过。组装器仅允许覆盖带生成标记的输出目录。
- 私有 `dev/main` 已推送提交 `7d69d79`。公开隔离工作树通过 `cherry-pick --no-commit` 引入发布意图，解决公开基线缺少私有 packaging 和测试的 modify/delete 冲突后，收口为只含 11 个文件的薄 Skill。
- 公开 `origin/main` 已推送提交 `5b257f6`，标签为 `v0.12.18`。GitHub Release 为正式非草稿、非预发布状态，包含 macOS ARM64、Kylin V10 ARM64 和 `SHA256SUMS.txt`，未包含 Windows 资产。
- 线上新鲜浅克隆验证通过：远程树不含 `.py`、`.pyc`、`.pyo`，Skill validator 通过，首次运行成功从 GitHub Release 下载并验证 macOS 归档，安装后 `--version` 返回 `0.12.18`。
- annotated tag `v0.12.18` 解析到公开提交 `5b257f6`。GitHub 将该提交标记为 `unsigned`，本次来源真实性依赖 GitHub 仓库权限和 Release 资产 SHA256，待后续版本配置签名发布。
- 用户发现公开历史仍可查看旧代码。已确认根因是薄 Skill 提交继承原历史，`main` 和 `v0.12.18` 同时保留旧提交可达性。当前无 fork 和 PR，历史重写范围为一个分支和一个标签，等待用户明确授权强制推送和标签替换。
- 启动阶段 19，开始实现 Nuitka standalone 闭源核心和内网离线分发最小闭环。
- 已复核 Git 基线，当前 `dev` 与 `dev/main` 一致，工作区只有预存 `_build/` 与 `paseo.json` 未跟踪项。
- 首次创建 `codex/nuitka-offline-distribution` 分支因 `.git/refs` 写权限失败，已记录为环境权限问题，将在经授权的 Git 环境重试。
- 授权环境又因当前额度限制拒绝创建分支。按安全替代路径，继续在 `dev` 工作区保留未提交改动，不再尝试写入 `.git`。
- 已盘点本机 Nuitka 构建环境：macOS arm64 与 clang 可用，项目 Python 为 3.14.4 且未安装 Nuitka。生产工作流将锁定 Python 3.13，本机 3.14 仅用于冒烟。
- Claude 已完成首轮实现，新增发布入口、打包器、安装 Adapter、五平台工作流和 6 项测试；首轮为 6 passed，全量 228 passed。
- Codex 独立复核发现公开 Skill contract、旧源码残留、Kylin glibc 基线、编译后冒烟、源码泄漏阻断、归档权限、依赖锁定与签名真实性等问题，已要求 Claude 进入第二轮修复。
- 启动阶段 18，分析闭源核心与内网离线分发改造。已读取 `planning-with-files-zh` 和 `codebase-design` 指南，并恢复仓库既有规划文件。
- 已确认现有双仓库远程、公开 Skill 文档、CLI 入口和依赖。本阶段将产出方案，不修改 Python 源码。
- 已核对 Nuitka 官方的 standalone、onefile、数据文件和路径约束，以及 GitHub Actions 当前的五平台 runner 能力。
- 已确认公开 `origin/main` 包含完整 Python 源码历史，私有 `dev/main` 包含开发文档和测试。
- 已完成双仓库职责、standalone 离线制品、五平台矩阵、联网与离线安装 Adapter、发布报告收口、签名与供应链门禁、分阶段迁移与回滚方案设计。

- 已用真实 Markdown 文件复现数字列表误识别，红灯命令约 0.5 秒稳定失败。
- 已确认 41 个 Markdown 标题和 49 个有序列表项被解析为 90 个 heading 块，list_item 为 0。
- 已确认 5 张表格共丢失 5 个表头行，成品保留 14 个行内代码反引号。
- 已运行小输入探针，确认标准无序列表、代码块、引用、Setext 标题、软换行、行内格式、多个 H1 和对齐表格存在同类问题。
- 已运行完整测试集，180 项通过，证明当前测试门禁未覆盖 Markdown 解析缺陷。
- 已启动阶段 7，由 Sol 负责 DOCX 复用分析和分层规划，后续由 Terra 实现。
- 已启动阶段 17，将 Markdown 修复收口为系统化 AST 识别框架。已读取 planning-with-files-zh、codebase-design 和 domain-modeling 规范，恢复现有计划上下文并完成初步模块盘点。
- 阶段 17 的目标 seam 已确定为 Markdown token 流到统一 `DocumentModel`，保留 `parse_md_to_model()` 稳定 interface。
- 已完成现有 `md_pipeline.py` 职责盘点，确认 token 遍历、六类容器状态、行内识别、资源解析、语义裁决、报告副作用和守恒审计集中在同一 module。
- 已并行启动三种 interface 设计，分别优化最小 interface、高扩展性和默认调用路径。
- 已枚举当前 CommonMark 加 table 方言的全部启用规则，并发现链接目标语义与可见文本投影仍耦合，需要进入识别规范。
- 已完成三种 interface 方案比较并选定最小核心 interface。新增 `CONTEXT.md`、Markdown AST 识别规范和 ADR，准备交由 Terra 实现。
- 已调整 `.gitignore` 精确放行识别规范、ADR 和新的识别 interface 测试文件。
- Codex 复审阶段 17 首版，确认外层 interface 已建立，但 implementation 仍依赖 `_recognize_legacy`，尚未达到系统化识别框架要求。已记录循环依赖、Diagnostic 类型、合同深度和兼容投影问题。
- Terra 已完成实际 implementation 迁移，聚焦 16 项通过；Codex 独立复跑识别、Markdown pipeline 和转换测试共 27 项通过。当前进入合同与语法矩阵补强。
- 类型化 Diagnostic、未知语法降级和 Recognition Contract 已完成，相关 35 项通过。Codex 额外探针发现松散列表多段列项合同失败，进入最后的 AST 装配与单一合同编译器修复。
- 阶段 17 Skill 实现已通过 Codex 独立复核。识别、Markdown pipeline 和转换专项 37 项通过，全量 222 项通过，`py_compile` 与 `git diff --check` 通过。
- 两份真实 Markdown 已重新转换。两份报告均包含 JSON 可序列化的 `markdown_recognition_contract`，语义守恒审计通过，分隔符未产生可见文字。
- 已进入 Desktop 同步阶段，由 Terra 调整显式 module 清单并增加 Desktop 入口回归，待 Codex 复核和打包验证。
- Desktop 同步和 Codex 独立复核完成。全量 33 项测试在允许本地端口的环境中通过，版本合同为 0.12.18，`git diff --check` 通过。
- 已重新构建 macOS arm64 `MagicFormat.app`，打包后 Markdown 转换冒烟通过。Desktop 源码入口重跑两份真实文件，Recognition Contract 和守恒审计均通过。
- Sol 已完成只读规划，明确 source adapter seam、可复用 DOCX 深模块、不可复用 OOXML 逻辑和六阶段实施方案。
- 已进入阶段 8，准备由 Terra 添加 Markdown 专项测试并实现结构化解析。
- Terra 已读取实现约束、计划、发现和转换技能。基线完整测试为 180 项通过；`.venv` 导入 `markdown_it` 失败，已通知总控，等待依赖安装授权。未触碰已有未跟踪 `_build/`、`paseo.json`，也未修改 Python 代码。
- 总控已安装 `markdown-it-py` 4.2.0。Terra 先新增三项红灯测试，确认表头、Setext、围栏、引用、软换行、嵌套列表和行内标记存在缺陷；随后以 token adapter 替换行级正则解析。
- 已新增 Markdown 源语义与输出守恒审计，扩展成品 Markdown 残留检测至段落和表格单元格，并修复下划线标识符误报。
- 专项测试 4 项通过，全量测试 184 项通过，`py_compile` 与 `git diff --check` 通过。真实 Markdown 样例已转换并达到 title 1、正文 heading 40、list_item 49、5 表 32 行、表头 5、残留 0、`unexpected_styles=[]`。
- 待 Sol 复审与 DOCX 实际输入审计。新增 `requirements.txt`、`tests/test_md_pipeline.py`、`tests/test_md_conversion.py` 受 `.gitignore` 影响未显示在 Git 状态，按总控要求未暂存、未改 ignore 规则。
- 已完成真实 DOCX 回归：ZIP 完整性、列表、表格、注语义和附录审计均通过，`unexpected_styles=[]`。该输入保留既有题注位置和模型差异风险，Markdown adapter 未参与 DOCX 入口。
- 独立复测发现真实 Markdown 的 `--fail-on-risk` 只因输出 AST 多计一个无文本的目录分页布局段而失败。Terra 在输出模型构造阶段排除无文本、无媒体布局段，并新增真实标题丢失的负向测试。
- 修复后真实 Markdown 严格模式成功，`risk_warnings=[]`，source、normalized、rendered 块数均为 166。专项测试 15 项与全量测试 187 项通过，编译和差异空白检查通过。
- 主 Agent 独立复跑 187 项测试和真实 Markdown 严格模式，均通过；成品 49 个列表、32 个表格行、反引号残留 0。
- Sol 终审构造出代码块重分类、缩进代码丢失、容器语义丢失和守恒审计假绿反例，阶段 11 判定不通过。
- 已进入阶段 11A，由 Terra 修复全部发布阻断项，再交 Sol 复审。
- 阶段 11A 已完成：`.gitignore` 已明确放行 Markdown 测试与 requirements，token source 语义保护、未知内容降级、链接和断行保真、矩阵级守恒审计及 DOCX 残留隔离均已实现。
- 当前 Markdown 专项和关联测试 19 项通过。真实问题分析2 的 `--fail-on-risk` 成功，Markdown 守恒、列表、表格、样式和残留审计均通过。真实 DOCX 回归 ZIP 完整且列表、表格、注语义、附录和样式审计通过，保留该源已有 caption_placement 风险。
- 阶段 11C 已处理 Sol 11B 的三个阻断项。新增列表内引用、引用列表、渲染模型篡改和代码反引号残留反例；真实 Markdown 严格模式再次通过，全量测试为 194 项通过，编译和差异空白检查通过。DOCX 实际回归已重跑。
- 阶段 11E 已修复 rendered mismatch 的 `after` 未赋值异常，收紧为有序、显式角色的 Markdown 语义通道快照。新增保存再回读的围栏代码、引用和引用列表端到端测试，以及结构化代码段落残留豁免反例。
- 验证完成：Markdown 聚焦 13 项通过，全量 195 项通过，`问题分析2_正式版.md --fail-on-risk` 成功，真实 DOCX 回归完成，`py_compile` 与 `git diff --check` 通过。未提交、未暂存。
- 阶段 11G 已新增 heading 与 body、paragraph 与 table 的 source、normalized、rendered 跨类型交换反例。Markdown 聚焦 14 项通过，全量 196 项通过，真实 Markdown 严格模式通过，差异检查通过。
- 阶段 11I 已新增保存后修改 OOXML heading、body、list、quote、table 物理顺序的反例，并新增 image/caption 删除和顺序交换反例。输出模型按 OOXML body 子节点读取。聚焦 16 项通过，全量 198 项通过，真实 Markdown 严格模式通过；DOCX 实际回归仍仅保留既有 caption_placement 风险。
- 阶段 11K 已新增两图 relationship 交换、缺失 relationship、附录 A、附录 B 带标题与空标题附录 render-save-readback 反例。聚焦 18 项通过，全量 200 项通过，真实 Markdown 严格模式通过；DOCX 实际回归仍仅保留既有 caption_placement 风险。
- 阶段 11M 已新增错误 reltype、百分号编码中文空格文件名和纯“附录” parse-normalize-render-save-readback 反例。聚焦 19 项通过，全量 201 项通过，真实 Markdown 严格模式通过；DOCX 实际回归仍仅保留既有 caption_placement 风险。
- 阶段 11N Sol 最终复审通过，确定性阻断项为 0。主 Agent 独立复跑全量测试，201 项通过；`git diff --check` 通过。真实样例报告保持 title 1、正文 heading 40、list_item 49、5 张表、32 行、5 个表头，守恒审计通过且风险为空。
- 阶段 12 已完成。工作树未提交、未暂存；预存 `_build/` 与 `paseo.json` 未处理，新增依赖和 Markdown 测试文件已解除忽略，等待后续提交时纳入。
- 阶段 13 已完成。`scripts/.venv` 安装 `markdown-it-py 4.2.0` 后全量 201 项测试通过；真实 Markdown 在标准环境中以严格模式转换成功，ZIP、语义守恒和风险门禁通过。已将本机安装副本从 0.12.15 更新到 0.12.16，备份位于 `/Users/harris/.codex/skills/.skill-backups/wx-doc-format-20260824-134031`。
- 阶段 14 已完成。版本升级为 0.12.17，标准环境全量 201 项测试通过，真实样例严格转换和 ZIP 检查通过。本机 Skill 已从 0.12.16 同步到 0.12.17，备份位于 `/Users/harris/.codex/skills/.skill-backups/wx-doc-format-20260824-135627`。本地提交为 `5487f2a` 和 `5439356`，未推送远程。
- 阶段 15 已完成。私有 `dev/main` 已推送到 `5439356`；公开工作树仅保留运行 Skill 文件和 `requirements.txt`，真实 Markdown 严格转换通过后推送 `origin/main` 到 `f4045d5`。GitHub 远程 `VERSION` 已复核为 0.12.17。未创建标签或 GitHub Release，符合 Skill 仓库既定发布方式。
- 阶段 16 已先以 red test 复现 separator、后置 H1 标题 fallback 和 inline run 丢失；现已实现 AST、渲染与 Markdown 列表保护，版本升为 0.12.18，待最终全量复测。未提交、未发布。
- 阶段 19 实现完成。发布入口固定离线模式、版本资源、AST 导出限制和报告脱敏；公开 Skill 仅保留调用协议。
- 已完成 Nuitka standalone 打包器、Windows x86_64、macOS arm64、Linux arm64 三目标工作流、Kylin manylinux2014 ARM64 候选构建、POSIX/PowerShell 原子安装器、完整文件集合与 SHA256 校验。
- 本机使用 Nuitka 4.1.2 和 Python 3.14.4 成功编译 macOS arm64 standalone。发现并修复 python-docx `docx/parts/../templates` 对空中间目录的运行时要求。
- 最终 macOS arm64 离线目录为 59 MB，tar.gz 为 21 MB。安装器安装到纯 ASCII 临时目录后，版本检查、Markdown 到 DOCX 转换、报告脱敏和完整性检查均通过。
- 专项测试 30 项、全量测试 252 项通过；公开 Skill validator、Python 编译、POSIX shell 语法、Workflow YAML、归档源码泄漏扫描和 `git diff --check` 全部通过。
- macOS 和 Linux 的 Python 3.13 制品均复现中文可执行路径 SIGABRT。安装器与启动器现在拒绝非 ASCII 安装路径，公开 README 和 Skill 已同步该边界。
- 当前改动留在 `dev` 工作区，未提交、未推送；预存 `_build/` 与 `paseo.json` 未处理。
- 阶段 20 已启动。新增 macOS ARM64、Linux ARM64 和 Windows x86_64 三个本地构建入口，统一执行锁定依赖、测试、Nuitka 编译、归档和冒烟。
- 三目标本地构建入口合同测试通过，Nuitka 专项当前为 33 项通过。
- 已通过 Homebrew 安装 Python 3.13.15，系统默认 Python 3.14 保持不变。macOS 本地构建将使用项目专用 Python 3.13 虚拟环境。
- macOS ARM64 本地构建完成。Python 3.13.15 环境全量测试 255 项通过，Nuitka 4.1.2 编译 278 个 C 文件并生成 standalone 目录与 tar.gz，真实 Markdown 转 DOCX 冒烟通过。
- Linux ARM64 首次容器构建在 Pillow 12.3.0 源码编译阶段停止。manylinux2014 ARM64/Python 3.13 没有可用 Pillow wheel，容器缺少 JPEG 开发头文件；其余依赖下载正常。
- Linux ARM64 第二次构建已成功编译 Pillow，255 项测试通过。Nuitka 随后发现 manylinux 默认未展开静态 libpython；构建入口已加入镜像自带静态库的展开步骤。
- Linux ARM64 第三次构建成功生成 standalone 和 tar.gz，普通路径真实转换通过；中文路径启动在 macOS Python 3.13 和 Linux Python 3.13 上均触发 Nuitka SIGABRT，进入编译器版本探针和安装路径保护阶段。
- Nuitka 4.1.3 最小 standalone 探针仍复现中文路径 SIGABRT，因此保留已验证的 4.1.2 锁定版本。
- 已用更新后的公开文件重新封装 macOS ARM64 和 Kylin V10 ARM64 归档。两份制品均通过真实 Markdown 转 DOCX、SHA256 全文件集校验、安装和版本检查。
- 分发专项测试 35 项和全量测试 257 项通过。Linux 构建镜像已固定 SHA256 摘要，并补齐 Pillow 构建依赖和静态 libpython 展开步骤。
- macOS ARM64 制品的 Mach-O `minos` 为 26.0，只能当作当前本机验证件。面向更旧 macOS 交付前需使用低版本构建环境重建。
# 2026-09-08 方案 3.1 完整能力迁移

- 已核对方案 3.0、execution-v3 任务卡、验收矩阵、removal map 和当前调用链。
- 已确认方案设计包含完整迁移意图，实施缺少 module 接线和端到端能力门禁。
- 已建立阶段 1 至阶段 8 的新计划，下一步编写方案 3.1 文档和真实模板红灯。
- 本阶段尚未修改 Python 源码。
- 阶段 7.1 已完成：SourceFacts parser、SourceRegion 和 DocumentStructure 已作为三个独立可执行 module 进入 ConversionKernel 的真实 ExecutionPlan，legacy full Adapter 调整到 normalize 及后续阶段执行。
- 三个内置 module 与共享执行上下文已提取到 `scripts/wxdf/builtin_modules.py`。Kernel 仅负责注册、执行和结果收口，后续增加同类 module 可通过工厂返回 `RegisteredModule` 接入。
- 新增每次转换共享的原生事实上下文。SourceRegion 产出范围与保护候选，DocumentStructure 消费范围结果并跳过 excluded 节点，报告记录 module 数量证据和 `shadow_verified` 状态。
- registry 新增执行顺序依赖门禁，拒绝 capability provider 排在 consumer 之后的计划。
- TDD 红灯覆盖模块接线、排除范围不得被结构重分类、依赖顺序错误。聚焦 13 项、模块关联 36 项和全量 359 项测试通过。
- 真实 `benchmark_report.md` 使用 `assets/wx_template.docx` 严格转换成功，报告状态 completed、`risk_warnings=[]`，执行计划包含四个 module。当前最终成品仍由 Adapter 生成，阶段 7.2 将增加逐节点语义等价审计。
- 阶段 7.2 已完成：新增 `semantic-equivalence-audit` 必需 module，按文档顺序和同文本出现次数对照原生结构候选与迁移后的旧 Source AST，检查角色、层级、节点缺失和排除范围残留。
- 首次真实 Markdown 对照成功关联 146 个节点，暴露 1 个 title 角色差异和 6 个标题层级差异。DocumentStructure 已迁入旧 Markdown 合同，文首有效 H1 定稿为 title，后续 Markdown 标题层级下移一级。
- 修复后 `benchmark_report.md` 严格转换再次成功，146 个节点全部匹配，等价审计 `passed=true`、issues 为空，三项能力状态为 `shadow_verified`。
- 临时真实 DOCX 探针仍为 `shadow_mismatch`。原生 SourceFacts 未识别 Title 和 Heading 样式；旧链把“第一章”整段剥离为空标题。该差异不复制到原生路径，等待真实模板 DOCX 样本裁决。
- 新增红灯覆盖角色与层级差异、等价通过路径和 Kernel 必需审计接线。全量测试 361 项通过。阶段 7.3 暂不切换能力所有权。
- 真实 DOCX 首次在 LegacyView 迁移阶段失败。根因是中文附录分类被直接写入 AST 2.0 英文枚举。迁移 Adapter 现将“资料性”映射为 `informative`、“规范性”映射为 `normative`，保留原标签，未知值降级为 `unknown` 并输出诊断。
- 迁移 Adapter 已按旧模型显式 `context.scope=appendix` 和 `appendix_id` 把附录范围节点挂入 appendix 容器，修复附录范围在 AST 2.0 children 图中丢失的问题。
- DOCX DocumentStructure 现消费 SourceFacts 的样式证据，识别 Title、文档标题和 Heading 1 至 Heading 6；题注、列项、注释及附录专用样式留给对应 module。空标题样式段落不再占用 title 槽位。
- SourceRegion 的纯文本“目录/目次”启发式已限定为 Markdown，避免把 DOCX 正文标题“目次”误判为源目录。
- 等价审计已限定结构 module 的职责，强校验 title 和 heading，双方存在时比较普通 body，并排除 appendix 子树与其他语义 module 的角色。
- 148 KB 真实 DOCX 转换 completed。结构域 56 个节点全部匹配，issues 为空，三项能力为 `shadow_verified`。成品 ZIP 完整，仍保留源文档既有的图片、标题层级、附录目录映射和题注位置风险。
- 全量测试 369 项通过，Python 编译、DOCX ZIP 完整性和 `git diff --check` 通过。阶段 7.3 仍等待 Adapter 真正消费原生决策后再切换 capability 所有权。
- 阶段 7.3 的 DocumentStructure 子项已将原生候选注入 legacy Adapter，旧 Source Model 在规范化前消费同一裁决。Adapter 已移除 `document_structure` provider 声明。
- 新增 DOCX 坐标反例和容器边界反例。修正后 Markdown 真实基准消费 19 个顶层决策，真实 DOCX 消费 52 个顶层决策，无 unmatched 或 ambiguous，两者结构等价审计均通过。
- 真实 DOCX 中的空二级列表项经边界修正后保持 `list_item`，不再被 body 候选覆盖。成品 ZIP 完整，原有图片、标题层级、附录、目录更新及题注位置风险继续如实保留。
- 阶段 7.3 的 DocumentStructure 子项全量回归 372 项通过，相关 Python 文件编译通过，Markdown 与真实 DOCX 成品均通过 ZIP 完整性检查，`git diff --check` 通过。
- 阶段 7.3 的 SourceRegion 子项已完成 provider 切换。Adapter 在 Markdown 规范化前移除排除段落，在 DOCX 解析前消费目录、前置页和规范标题上下文。
- 聚焦回归 53 项通过。真实样式 DOCX 消费 2 个前置位置并保持结构等价；带封面和手工目录的 DOCX 探针消费 6 个位置，Source AST 仅保留唯一标题与正文，目录替换审计通过。
- SourceRegion 子项全量回归 377 项通过，相关 Python 文件编译、三份 DOCX ZIP 完整性、`git diff --check` 和状态 JSON 语法检查通过。
- SourceParser 已建立独立解析覆盖审计。旧 Source Model 通过运行内存工件供审计消费，无需用户导出 Source AST。
- 真实模板 DOCX 转换 completed，ZIP 完整。新门禁将 SourceParser 标记为 `shadow_mismatch`，具体为文本 127 对 118、列表 27 对 28，表格 4 对 4、图片 3 对 3。
- 真实 Markdown 基准的解析覆盖审计通过，文本 146 对 146、表格 2 对 2，SourceParser 保持 `shadow_verified`。本次全量回归为 380 项通过。
- SourceFacts 语义投影已接入 SourceParser 独立审计，题注前缀、附录分类、单元格多段合并都使用显式转换合同。
- 旧解析器对公式编号 `(3)` 的空列表项误判现以受限条件记录为批准差异，原生源文本保留。
- 真实模板 DOCX 转换 completed，文本 118 对 118、表格 4 对 4、图片 3 对 3、列表 27 对 27，四域均为 `equivalent`，成品 ZIP 完整。
- 新增及相关聚焦测试 25 项通过，全量回归 385 项通过，Python 编译和 `git diff --check` 通过。SourceParser 仍保持 `shadow_verified`，下一子项是输出链直接消费原生投影。
- SourceParser 内容所有权已切换：输出链在规范化前消费原生 SourceProjection，真实 Markdown 为 19 个段落和 2 张表，真实 DOCX 为 99 个段落和 4 张表，均完整消费且无歧义。
- 真实 DOCX 的文本、表格、图片和列表四域等价，公式编号“(3)”恢复为正文公式角色，Source AST 模型问题为空。全量回归 389 项通过。
- 旧 parser 仍提供语义骨架和 legacy 回退。下一道闸门迁移 ProtectedContent、List、Table、Caption 和 Appendix provider，完成后再收敛重复解析实现。
- ProtectedContent Provider 已完成真实接线与所有权切换。真实 Markdown 442 个、真实 DOCX 9 个文档级候选全部消费，未匹配与歧义均为 0。
- 表内注释已从文档级保护候选排除并交由 Table Provider 管理，真实 DOCX 保持 4 张表，解析四域等价，注释保真和表格语义审计通过。
- 当前全量回归为 393 项通过。下一子项进入 List Provider 所有权迁移。
- List Provider 已完成真实接线与所有权切换。DOCX ignored 编号候选不再产生列表语义，27 个 detected 列表项全部消费。
- 有编号列项统一为一级并按章节重启，公式编号保持公式角色。真实 Source AST、归一化 AST 和输出均为 27 个列表项，专项审计通过。
- 全量回归为 395 项通过。下一子项进入 Table Provider 所有权迁移。
- Table Provider 已完成所有权迁移。真实 DOCX 的 4 张表全部消费，2 张 data 与 2 张 layout 分类正确，表格格式合同通过。
- Caption Provider 已完成所有权迁移。真实 DOCX 的 5 个题注全部消费并关联，原 `b0029` 图题风险消除，模型和保存后审计通过。
- Appendix Provider 已完成所有权迁移。1 个正文多级编号候选被负证据拒绝，1 个有效附录范围与 3 个范围内标题裁决全部消费。
- 模板 TOC 片段已补齐 1 至 4 级和附录样式映射。目录专项测试与真实 DOCX 审计通过。
- 全量回归更新为 402 项通过。真实 Markdown 风险为空，真实 DOCX 八项能力均为 `native_consumed`。
- 性能基准首次 large 档未通过，同配置复测三档通过。全局默认保持 legacy。
- macOS ARM64 编译产物已重建。构建前 402 项测试通过，源码与运行时模块均为 45 项，ASCII 安装冒烟通过。
- 编译产物显式 modular 真实 DOCX 转换通过，全部专项审计通过，`unexpected_styles=[]`，输出共 6 页。
- 页面抽检和公式 layout OOXML 比对完成。剩余项为 Kylin V10 ARM64 与 Windows x86_64 外部环境验收，未推送、未发布。
- 用户确认后续不再打包或验收 Windows x86_64。当前唯一外部平台门禁为 Kylin V10 ARM64，Windows 构建入口仅作历史兼容保留。

## 2026-09-14 真实 DOCX 基础格式语义修复

- 列表模块已支持无 `numPr` 的 DOCX 可见编号和字母标记，解析证据保留原标记，一级列表语义在 Source AST 中清理正文前缀。
- 段落模块已消费根级 DOCX 的展示性首部空白，表格单元格和图片混合段落不做全局清理。
- 题注模块已支持图片后无编号标题、表格前短表名、括号包裹的混合图片题注，并收紧题注与结构候选的写集竞争。
- 结构模块已将 `Heading 7` 及以上样式映射到模板可支持的六级标题。
- 定向测试 42 项通过。真实奇安信 DOCX 转换成功，退出码 0，模型、保存输出、附录审计均通过。输出抽查确认手工列表前缀已去除，题注文本进入 Caption 样式，深层标题进入 Heading 6。
- 当前待办为独立代码审查、全量测试、修复复审和提交。
