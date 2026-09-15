# 列表语义编译器设计（native-v2 版）

日期：2026-09-14
状态：设计稿，供场景化评审与后续拆票。未改任何 `.py`。

## 1. 目标

在当前的 native-v2 模块化流水线上，把列表处理从「连续 list_item 段 + 最近父项的局部修正」升级为统一的列表语义编译，使全部已知列表结构都有确定处理规则。本设计沿用并改写你在 0.12.19 讨论的「证据驱动的列表语义编译器」方案，但落点全部映射到当前 `scripts/wxdf/` 的模块、候选、Resolver 与事务接缝，不另起一套独立解析链。

完整覆盖判定沿用旧方案的五条，并落到本架构的可核验形式：

1. 所有已知结构都有确定处理规则。
2. 所有输入块都能归入列表项、列表项附属内容或列表外内容。
3. 证据冲突时保留多个候选并报告歧义。
4. 无充分证据时禁止静默猜测。
5. 输出 DOCX 重新解析后，列表树与编号序列与规范化列表语义一致。

## 2. 当前架构已具备的资产（直接复用）

- 一个语义模块 `list-semantics`（`scripts/wxdf/semantic_modules.py:114`）持有整个列表概念，产出 `list_semantics_result`、`list_semantics_candidates`、`list_item_map`、`list_semantics`。
- 源 AST 已建 `list` 容器与 `list_item` 节点（`scripts/wxdf/semantics/source_ast.py: materialize_list_semantics`），支持显式深层挂在列表容器下。
- 续接段落已建模：列表项源子 `children[1:]` 中无标记的正文段被物化为 `sequence_continuation`。
- 候选进入统一 `CandidateSet → Resolver → Transaction`，证据强度参与裁决，可报告 `unresolved`，适合承载「歧义保留」。
- 受保护名单已覆盖代码、标题样式、日期、版本号、API、手稿标题、公式、注释等（`scripts/wxdf/semantics/protected.py`）。
- `list_item_map` 已被附录消费（`appendices.py:316-369`），证明「列表语义结果供语义模块复用」的接缝真实存在。
- `list_detect` 能力（`handle_capability`）可以在有候选的歧义片段上回灌修正。

## 3. 现有差距（已逐个核对当前实现）

| 旧方案要求 | 当前实现 | 差距落点 |
| --- | --- | --- |
| 完整 OOXML 编号目录 | `wxdf/parsers.py:596` 复用旧 `list_detector` 的 `_numbering_maps` 与 `analyze_docx_lists` | 原生路径能力上限停在 `list_detector._level_values()`，未覆盖 `lvlRestart`、编号级 `w:ind`、`suffix`、`pStyle→段落样式` 继承 |
| 段落样式 numPr 时 ilvl 继承规则 | 直接 `explicit_depth=descriptor.get("ilvl")`（`parsers.py:639`） | 未实现「样式 numPr 的 ilvl 应忽略，实际层级由 pStyle 关联确定」 |
| 同一逻辑列表被拆分或复用 numId | `source_sequence_id = docx-sequence-{num_id}-{abstract_num_id}` | 固定 key 会把拆开的序列拆开、把复用的合成 |
| 子列表新 numId 且 ilvl=0 | `depth=explicit_depth`，父项用 `active_items.get(depth-1)`（`lists.py`） | 该子表被当同级项，是一级二级误判的主要来源 |
| 全局状态解码、可留 `AMBIGUOUS` | 循环 `for run in runs` 遇无 `marker_kind` 段即切断 | 连续段局部，正文插入即断，无全局上下文、无歧义候选 |
| 列表项复合内容（表/图/引用/代码/公式/注释） | 只把 `children[1:]` 的 paragraph 记为 continuation | 项内其他块无归属，AST 无法表达「列表项拥有多块内容」 |
| 重启建模 | `restart_hint` 布尔 + `source_start` | 缺少 `start_value / sequence_id / restart_scope` |
| 语义级与视觉级分离 | 层级直接由 `depth` 驱动，WX 样式映射写在 `normalization` 内 | 没有 `source_ilvl / effective_ilvl / semantic_level / render_level` 分离，策略不能独立替换 |
| 回读审计（重解析输出后比对树） | 仅核对 `numFmt / ilvl / numId` 与样式 | 无「重新解析输出 → 重建列表树 → 与语义列表比对」审计 |

## 4. 总体架构（适配当前 native-v2）

```
DOCX 编号目录（SourceFacts 证据层，含完整 OOXML 字段）
  + Markdown 编号证据
        ↓
list 语义模块（lists.py）：生成带证据评分的候选状态，不直接写 AST
        ↓
Resolver（全局约束裁决）：层级、父项、序列、归属唯一、顺序守恒
        ↓
Transaction 提交：物化成 list 树 + item 内容块 + 序列/重启字段
        ↓
Normalizer / ListPolicy：semantic_level → render_level 样式投影
        ↓
Render 输出真实编号；Audit 回读并比对列表树
```

关键点：当前架构里的「全局状态机」不需要独立重写一条流水线。把每个块的可选归属、可选父项、可选层级表达为该块的一组候选，交给 Resolver 的全局约束去选择、保合法结构、上报歧义。这正是 `CandidateSet / Resolver / unresolved` 存在的意义。

## 5. 证据层：统一编号目录

### 5.1 DOCX SourceAdapter

在 `wxdf/parsers.py` 的 DOCX 解析内，把编号目录从旧 `list_detector` 升级为原生「编号目录」产物，逐件读入 OOXML：

- 段落直接 `numPr` 与样式 `numPr` 并存时，按 OOXML 规则：样式 numPr 的 `ilvl` 忽略，实际层级由编号定义 `w:lvl/w:pStyle` 与段落样式关联确定。
- `abstractNum` 完整字段：`numFmt`（罗马、大小写字母、中文数字、自定义符号、图片项目符）、`lvlText`、`w:ind`（编号级缩进）、`suffix`、`lvlRestart`。
- `num` 的 `lvlOverride`（覆盖格式/缩进/起始）与 `startOverride`（非 1 起始）。
- 缺失、损坏或指向不存在 `abstractNumId`：保留原文证据 + 诊断，降级为受控正文（沿用当前 `downgrades`）。
- 自动编号与正文手工标记并存：标记进 `visible_marker`，自动编号进编号目录，二者共同参与「重复标记」裁决。

### 5.2 序列身份

把固定的 `docx-sequence-{num_id}-{abstract_num_id}` 升级为可合并、可拆分的序列身份：

- 每组列表携带 `source_sequence_id`，语义标识不再只等于 numId。
- 同一逻辑列表被 Word/WPS 拆成多个 numId 时，语义层合并回一个序列。
- 不同逻辑列表复用同一 numId 时，语义层拆成独立序列。
- 判定依据：`restart` 提示、格式一致性、跨界（标题、分节、正文、图片、表格）是否重启。

### 5.3 Markdown 证据

沿用 `markdown_recognition` 的环境数据，用同一份「证据形状」（`marker_kind / ordinal / depth` 等）产出。不复制 OOXML 细节，只共享证据字段与裁决逻辑。

## 6. 语义层：候选 + 全局解码

### 6.1 `lists.py` 改为候选状态编码

对每个列表候选块产生一份带证据评分的候选集合，而不是立即写死归属：

- 父项候选（最近项，或显式结构父项）。
- 是否成为新序列起点、是否沿用旧序列继续。
- 语义层级来自双通道：`source_ilvl`（目录证据）与结构约束。
- 证据冲突时保留多个合法候选并标 `ambiguity`，低置信度进入歧义报告。

### 6.2 收敛到现有 Resolver 的全局约束

不新增独立状态机，把这些约束作为候选裁决输入：

- 每个源块只能归属一个位置。
- 序列顺序守恒。
- 深层项必须有前置父项（允许多个父项候选，不允许直接落到不存在的父）。
- 层级通常逐级增加。
- 列表类别（有序/无序）与层级相互独立。
- 明确 `startOverride` / `lvlRestart` 优先保留。
- 分页、正文段、图片、表格、注释不会独立终止当前列表（可进入 continuation 或 child block）。
- 标题、章节/附录边界依据策略结束当前序列。
- 低置信度结构报告歧义，普通模式不静默接受。

这条改动把旧方案的「全局状态解码」翻译成「候选 + Resolver 约束」，复用既有能力。`lists.py` 从「选唯一的 Move 候选」改为「发一组可候选」，并把受保护结构（标题、列表外受保护段）的结果作为 Resolver 的负向输入，缓解已知问题 1 与 3 到 6。

## 7. 语义模型：列表项可拥有任意内容

在 `source_ast.materialize_list_semantics` 扩展列表项的内容归属：

- 列表项第一块为主块，后续正文为 `continuation`（已有）。
- 列表项内部出现 `table / 图片 / 引用 / 代码块 / 公式容器 / 注 / 示例 / API 样例 / 子列表 / 子列表后续接正文` 时，这些块明确作为该项的 `content_blocks` 或 `child_lists` 挂到 `list_item` 节点。
- 复用 `list_item_map` 的接缝：源列表项 → 所属列表容器，让附录、表格、题注、结构等语义模块读取同一归属，保证「一块只归属一个列表」不变式。

这样 AST 就能表达「一个列表项包含正文、表格、图片、子列表」，解决旧方案第 104 项以及常见 22 到 44 的多数。

## 8. 层级与序列分离，解耦 WX 策略

新增三态分离建模：

- `source_ilvl`：源 OOXML／Markdown 的原始深度证据，永不被规范化改写。
- `semantic_level`：证据目录 + 全局约束判定出的真实层级（0..N）。
- `render_level`：由 ListPolicy 把 `semantic_level` 映射为模板可见样式。现 WX 用「零级有序 → 一级字母，零级无序 → 一级无序，深层 → 二级」；模板若未来增加三级样式，只改 ListPolicy，不改 lists.py。

`restart` 从布尔升级为 `start_value / sequence / restart_scope` 的表达式，覆盖「整个列表从 1 开始、从指定值开始、同级继续、子表随父重启、子表跨父继续、正文图注释插入后继续、新 numId 续旧序列、同 numId 开启新序列、回到浅层后的值、章节附录范围重启」。

`docx_render` 输出真实多级编号（复用/拆分 numId，ilvl 按真实层级），不再把有序列表的所有 `w:ilvl` 写零。

## 9. 回读审计

在 `auditing` 增加专门的「列表回读审计」：

- 输出 DOCX 用同一套 SourceFacts 重新解析。
- 重建列表语义树，比较 `semantic_level`、序列 id、每级序号值、父项与子块关系。
- 与目标列表语义比对，不一致产出专项审计失败与诊断。

目标不变量：每个源块保留一次；深层项有父项；每个正文段有 `owner_item_id`；编号模拟值与源可见一致；语义 AST 与回读列表树一致；普通模式不静默接受歧义结构。

## 10. 与现有执行环境的边界

- `list_semantics` 模块保持单一接口（`result + candidates + item_map`），附录、题注等通过 `list_item_map` 复用，不改其数据面。
- `list_detect` 能力保留在 LLM 层：只作用于已存在多候选的歧义片段，受 Resolver 约束，不直接改写整棵列表。
- `list_style_mapping.py / list_group_detection.py / list_hierarchy.py` 在纯 native 链已不再必需，逐步收敛进 lists 模块。
- `list_detector.py` 仍以编号目录形式参与证据层，但最终把它具备的能力完整迁入原生证据层，避免 catalog 双源。

## 11. 测试矩阵

- 输入来源：Word、WPS、LibreOffice、Markdown。
- 编号实例：同 numId、拆 numId、复 numId、缺失、覆盖。
- 层级：0 到 8 级、缺父、深层带 numId+ilvl=0。
- 类别组合：有序父包无序子、无序父包有序子、同级切换、多父项、相同缩进不同层级、不同缩进同层级。
- 中间内容：正文续段、独立正文、空段、分页、分节、表、图、引用、代码、注释、题注。
- 连续性：继续、重启、指定起始值、跨 numId 续、同 numId 新起。
- 输出策略：两级 WX 投影、深层降级、真实多级编号（非零 ilvl）。
- 容器：正文、表格单元格、嵌套表、页眉页脚、脚注、文本框、内容控件、修订。
- 语义冲突：表头编号、标题章节、版本、日期、API、公式、法规条款。

输出保证套用第 1 节五个条件，并有共享失败样例库（Word/WPS/Markdown 加线上已知问题文档）。

## 12. 分阶段实施顺序

1. 冻结场景矩阵与本设计为 v0 底稿，拆票前补齐字段级定义。
2. 建立失败样例库。
3. 扩展源 AST 与语义模型：三级分离、序列/重启字段、列表项 content_blocks。
4. 实现统一 DOCX 编号目录并迁移修复（含 pStyle 关联、lvlRestart、startOverride、缺失损坏编号）。
5. 把 `lists.py` 改为输出候选集，全局约束放 Resolver，接入受保护与结构候选结果。
6. 增加保存后回读列表审计，`render` 输出真实多级编号。
7. 收敛：移除旧 `list_*` 重复逻辑与旧渲染修补。

优先的三项：语义层级与序列与视觉层级分离；对 `子表 new numId+ilvl=0` 等结构的全局候选裁决；列表项纳入 table/image/quote/code 等任意内容的规则。

## 13. 已知列表问题到本架构的落点

| 现象 | 根因 | 解决面 |
| --- | --- | --- |
| 标题误识别为列表 | 受保护判定与结构候选未充分并入列表裁决 | lists._is_protected + protected 名单，加上结构候选作 Resolver 负向输入 |
| 连续正文误识别为列表 | 连续 run 里含 marker 的正文被硬判 | 候选 + 全局约束；无 marker 正文不进列表；有 marker 正文由证据与上下文裁决 |
| 有序无序选择错误 | marker_kind 派生单一路径 | 用编号目录的 `list_family` + 全局约束 |
| 一级/二级有序与无序优先级错误 | `子列表 new numId + ilvl=0` 被当同级；最近父 | 全局候选 + 逐级约束 + Resolver 代替最近父 |
| 多层嵌套、交叉嵌套、列表穿插正文 | 连续 run 局部切断 | 候选全集 + 全局约束 + 列表项 content_blocks |