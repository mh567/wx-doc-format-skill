# 列表语义编译器执行包

## 问题

当前 native-v2 的列表处理是「连续 list_item 段 + 最近父项」的局部修正模型。它把列表知识分散在 `semantics/lists.py`、`semantics/source_ast.py`、旧 `list_detector.py`、`model_normalization` 与多个渲染器里，带来三类持续复发的问题：

- 该识别为列表的没识别：深层嵌套、列表项内含正文/表格/图片/引用/代码、子列表新 numId 但 ilvl=0、同一逻辑列表被拆成多个 numId、不同列表复用同一 numId。
- 不该识别成列表的被误认：标题被当列表项、连续正文段被当列表、有序/无序选错、一级与二级优先级选错。
- 列表与它周围语义缠绕：列表项的续接正文、子列表后的正文归属不明；层级、编号序列和 WX 视觉样式混在一起，模板要加三级样式就得改解析器。

根本原因是现有 AST 是扁平块序列、非列表块会立即切断列表段、编号重启只用布尔值、编号目录能力停在旧 `list_detector._level_values()`（缺少 `lvlRestart / startOverride / w:ind / pStyle→段落样式继承`）。继续在局部上加邻接规则只会让规则互相冲突。

## 解决方案

建立一个深模块「列表语义编译器」。对调用方只暴露稳定接口，把列表知识收束到一个模块内；解析器、规范化器、渲染器只消费它的输出。整体沿用当前流水线的「证据 → 候选 → Resolver 全局约束 → 事务 → 渲染 → 回读审计」接缝，不另建独立链路：

- DOCX 与 Markdown 的 Source Adapter 输出统一证据；`parsers.py` 把编号目录能力从旧 `list_detector` 迁入原生证据层。
- `semantics/lists.py` 从「选唯一的 move 候选」改为「发一组带证据评分的候选状态」，由现有 Resolver 按全局约束裁决，冲突进歧义报告。
- `semantics/source_ast.materialize_list_semantics` 把列表项升级为可拥有子内容的列表树（table / image / quote / code / formula / 注 / 子列表）。
- `source_ilvl / semantic_level / render_level` 三级分离；WX 样式投影抽成可换的 `ListPolicy`；重启从布尔升级为带 `start_value / sequence / restart_scope` 的表达式。
- 输出真实多级编号（不把所有有序 `ilvl` 写零）；新增保存后回读列表树比对审计。

## 用户故事

1. 作为文档转换使用者，我希望深层嵌套、列表项内含正文/表格/图片/引用/代码的源文档转换后保留完整列表结构，不再把列表项内的内容丢到列表外面。
2. 作为使用者，我希望标题不再被误识为列表项，连续正文段不再被误识为列表。
3. 作为使用者，我希望有序/无序类别与一级/二级优先级的判断符合真实文档语义，不再把二级子项当同级项、不再把同级项硬挂到最近父项。
4. 作为使用者，我希望同一逻辑列表被拆成多个 numId 时仍能续上，不同列表复用同一 numId 时能正确拆开。
5. 作为使用者，我希望模板未来增加三级列表样式时只改策略配置，不用动解析器。
6. 作为维护者，我希望所有列表结构都有确定规则，证据冲突保留多个候选并报告歧义，无充分证据时禁止静默猜测。
7. 作为维护者，我希望输出 DOCX 重新解析后，列表树与编号序列和规范化的列表语义一致，由回读审计自动守住。
8. 作为维护者，我希望编号目录覆盖 OOXML 全部字段（lvlRestart / startOverride / 编号级 w:ind / pStyle 关联继承），段落样式 numPr 的 ilvl 按规则忽略。

## 实现决策

- 模块入口统一在 `list-semantics`（`semantic_modules.py`），产出 `list_semantics_result / list_semantics_candidates / list_semantics / list_item_map`；附录、表格、题注等语义模块通过 `list_item_map` 复用列表归属。
- DOCX 编号目录在原生 `parsers.py` 内实现，替代对旧 `list_detector._numbering_maps / _level_values` 的依赖；保留原诊断/降级能力。
- `lists.py` 改为候选状态编码（每块的父项候选、是否新起点、是否续序列、候选层级），受保护名单与结构候选作为 Resolver 负向输入。
- 全局约束复用现有 Resolver：每块唯一归属、顺序守恒、深层须有父项、逐级递增、分页/正文/图/表/注释不独立终止、标题与附录边界按策略结束、低置信度进歧义报告。
- AST 列表树支持任意子内容块（table / image / quote / code / formula / 注 / 子列表 / 续接正文），沿用 `list_item_map` 接缝。
- 层级三级分离（`source_ilvl / semantic_level / render_level`）；WX 策略抽为 `ListPolicy`；重启表达式含 `start_value / sequence / restart_scope`。
- 渲染输出真实多级编号（复用/拆分 numId，ilvl 按真实层级）。
- LLM 仅作用于存在多候选的歧义片段，受 Resolver 约束，不直接重写列表结构。

## 字段级定义

### 证据字段清单

每条列表项证据必须携带以下字段，由 Source Adapter 在证据层填写，Resolver 不得回填：

| 字段 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `source_ilvl` | `int` | 解析器从 `w:numPr/w:ilvl` 或段落样式继承链读取 | 源文档声明的编号层级（0-based），缺失时为 `None` |
| `effective_ilvl` | `int` | 解析器裁决 | 综合 `source_ilvl`、`pStyle` 继承、`w:ind` 与邻接关系后的有效层级，始终为非负整数 |
| `semantic_level` | `int` | 列表语义模块 | 规范化后的逻辑层级，用于 AST 列表树构建，始终为非负整数 |
| `render_level` | `int` | ListPolicy 投影 | 输出端层级，受 WX 策略上限约束（例如两级投影时 `render_level = min(semantic_level, 1)`） |
| `start_value` | `int` | 解析器从 `w:start` 或 `w:startOverride` 读取 | 当前编号序列的起始序号，缺省为 1 |
| `sequence` | `str` | 解析器生成 | 编号实例的稳定标识，同 `sequence` 的项共享续接与重启判定 |
| `restart_scope` | `str` | 解析器生成 | 重启作用域标识（如 `num_id` 或 `abstract_num_id`），用于判定 `lvlRestart` 边界 |
| `num_id` | `int \| None` | 解析器读取 | 源文档 `w:numId`，缺失或损坏时为 `None` |
| `abstract_num_id` | `int \| None` | 解析器读取 | 源文档 `w:abstractNumId`，缺失或损坏时为 `None` |
| `num_fmt` | `str \| None` | 解析器读取 | 编号格式（`decimal` / `bullet` / `lower_letter` 等），缺失时为 `None` |
| `lvl_text` | `str \| None` | 解析器读取 | 级别文本模板（如 `%1.`、`%1.%2`），缺失时为 `None` |
| `w:ind` | `int \| None` | 解析器读取 | 编号级左缩进（twips），缺失时为 `None` |
| `suffix` | `str \| None` | 解析器读取 | 编号后缀分隔符（`tab` / `space` / `none`），缺失时为 `None` |
| `p_style_ref` | `str \| None` | 解析器读取 | 段落样式名称，用于 `pStyle→段落样式` 继承链，无样式引用时为 `None` |

### 候选状态枚举

列表语义模块为每块输出的候选状态，由 Resolver 裁决：

| 状态 | 含义 |
|------|------|
| `OUTSIDE_LIST` | 当前块不归属任何列表，保持正文段落 |
| `LIST_ITEM` | 当前块是一个列表项的起始段落，开启或延续一个编号序列 |
| `ITEM_CONTINUATION` | 当前块是某列表项的续接正文（无独立编号，归属 `owner_item_id`） |
| `ITEM_CHILD_BLOCK` | 当前块是某列表项的子内容（表格 / 图片 / 引用 / 代码 / 公式 / 注），不参与编号序列 |
| `NESTED_LIST_ITEM` | 当前块是一个嵌套子列表的项，父项须已存在于列表中 |
| `LIST_END` | 当前块显式终止一个列表段（如分页、分节、样式切换） |
| `AMBIGUOUS` | 证据冲突或不足，存在多个等价候选，须进歧义报告 |

### 全局约束列表

Resolver 对列表候选施加以下全局约束：

1. 每块唯一归属：任何源块至多归属一个列表项或续接正文，禁止一稿两投。
2. 顺序守恒：列表项在输出序列中的相对顺序与源文档一致，禁止重排。
3. 深层须有父项：`effective_ilvl > 0` 的项必须存在同列表内 `effective_ilvl - 1` 的父项。
4. 逐级递增：层级从 0 开始逐级递增，禁止跳级（`ilvl=2` 不得紧接 `ilvl=0`，除非中间 `ilvl=1` 已存在）。
5. 分页 / 正文 / 图 / 表 / 注释不独立终止：这些块可作为列表项子内容，但不单独结束外层列表。
6. 标题与附录边界按策略结束：标题、附录起始块按 `ListPolicy` 显式终止其前的列表段。
7. 低置信度进歧义报告：证据评分低于阈值的候选必须进入歧义报告，普通模式不静默接受。
8. 同 `sequence` 续接：同 `sequence` 且类别相同的连续项默认续接前一项的编号序列。
9. 类别切换重启：同层级有序/无序切换时开启新列表，不强制合并。
10. `restart_scope` 边界：`lvlRestart` 或 `startOverride` 触发时，在 `restart_scope` 边界处重置 `start_value`。

> 实现注记（2026-09-16，R3 收口）：约束 7 的字面「证据评分阈值」在 T04 移除数值 `score`、改用 Resolver 证据类别后不再可判定。当前实现取证据类别模型支持的最强读法：真正的证据类别平局进入歧义报告并令 `list_semantics.status != "completed"`；证据类别不足（`isolated_generic_numbering` 或未解析定义）的候选保持正文，不静默提升，并以 `list_semantics_result.ambiguity_records` 与 `low_confidence_list_marker` 降级留痕，因此普通可转换文档仍为 `completed`。详见 `evidence/residuals.md`。

### 不变量清单

转换全链路必须守住以下不变量：

1. 每个源块恰好保留一次（不丢失、不重复）。
2. 深层列表项必有父项（`semantic_level > 0` → 存在 `parent_item_id`）。
3. 续接正文必有明确 `owner_item_id`。
4. 编号模拟值与源可见序列一致（`target_ordinal` 与源文档视觉序号一致）。
5. AST 列表树与回读 DOCX 的列表树一致（回读审计通过）。
6. 低置信度决策都有诊断记录（`diagnostics` / `downgrades` 可索引）。
7. 普通模式不静默接受歧义结构（`status != "completed"` 当存在未解决候选）。
8. `num_fmt` 与 `list_kind` 一致（`decimal` 类映射到 `ordered`，`bullet` 类映射到 `unordered`）。
9. `source_ilvl` 缺失时由 `effective_ilvl` 兜底，两者不同时以 `effective_ilvl` 为准。
10. 列表项子内容（table / image / quote / code / formula / 注）的 `semantic_level` 与父项一致或深一层。

## 测试决策

- 维度化矩阵：输入来源（Word / WPS / LibreOffice / Markdown）；编号实例（同/拆/复/缺/override）；层级（0-8 级、缺父、numId+ilvl=0 子表）；类别组合（有序包无序子、无序包有序子、同级切换、多父项、相同缩进不同层级、不同缩进同层级）；中间内容（正文/空段/分页/分节/表/图/引用/代码/注释/题注）；连续策略（续/重启/指定起始值）；输出策略（两级 WX 投影、深层降级、真实多级编号）；容器（正文/表格单元格/页眉页脚/脚注）；语义冲突（标题、版本号、公式、题注、API、法规条款）。
- 四层测试：纯解析（证据提取）、状态解码（列表树与歧义）、DOCX 渲染（numId / ilvl / 样式 / 重启）、保存回读（重解析输出并比对列表树）。
- 关键不变量：每个源块恰好保留一次；深层列表有父项；续接正文有明确 owner_item_id；编号模拟值与源可见序列一致；AST 与回读 DOCX 的列表树一致；低置信度决策都有诊断记录；普通模式不静默接受歧义结构。
- 建立失败样例库覆盖 Word / WPS / Markdown 与当前真实问题文档。

## 不在范围

- 不新增模板能力或 LLM 能力。
- 不改变稳定 CLI 参数与公开报告合同。
- 不移动或删除 `_build/`、`paseo.json`。
- 不执行远程 push、标签或 Release。
- 不在首轮覆盖修订记录、合并单元格内多级列表、内容控件内列表（产品范围待明确）。

## 补充说明

- 路径声明（架构定案 / 实施实现 / 独立审查）：
  - 架构定案：`scripts/wxdf/semantics/lists.py`、`scripts/wxdf/semantics/source_ast.py`、`scripts/wxdf/parsers.py`、`scripts/wxdf/semantics/regions.py`、`scripts/wxdf/normalization.py`、`scripts/wxdf/rendering.py`、`scripts/wxdf/auditing.py`、`scripts/wxdf/semantic_modules.py`、`scripts/wxdf/native_stages.py`、`scripts/wxdf/contracts.py`。
  - 实施实现：上述文件与 `scripts/wxdf/semantics/protected.py`、`scripts/wxdf/semantic_modules.py` 内 `list_semantics_module`。
  - 独立审查：由另一智能体对 diff 执行 Standards + Spec 双轴审查。
- 本包状态唯一源：本目录 `status.json`；远程推送、标签、发布需单独授权。
