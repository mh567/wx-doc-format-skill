# Issue #1 修复规格

## 1. 目的和范围

本规格处理 `b44de5f` 与 `abe7f49` 的双轴审查发现，使 `native-v2` 模式满足 Issue #1 已定义的语义事务主链合同。实现基线为 `abe7f49`，原始需求以 GitHub Issue #1 和 `docs/execution-v3/acceptance.md` 为准。

修复目标：

1. 在显式 `native-v2` 模式下，Markdown 和 DOCX 都经过完整主链：SourceParser、SourceFacts、SemanticModule CandidateSet、SemanticResolver DecisionSet、TransactionManager Source AST revision、NormalizationPolicy、RenderPlan、Renderer、AuditModule、Publisher。
2. Source AST 在事务提交后成为下游唯一的活动语义视图，SourceFacts 保持不可变。
3. 列表层级、续段、生成锚点和附录关系都通过候选、裁决和单批事务进入 Source AST。
4. 模块契约、能力消费状态和保存后审计采用真实运行结果计算。
5. L01 至 L16 建立可追溯的 Markdown、DOCX、Source AST、呈现计划和保存后 OOXML 验收覆盖。

本规格不切换默认流水线。`legacy-full-v1` 继续为默认模式，旧 CLI、公开 AST 名称和回退路径保持兼容。

## 2. 已确认缺口

| 编号 | 现状 | 修复条件 |
| :--- | :--- | :--- |
| R01 | `main.py` 通过 Markdown 正则和文件类型决定是否进入 native-v2 | 显式 `--modular-profile native-v2` 对 Markdown 与 DOCX 统一进入完整主链，入口不复制列表语义识别 |
| R02 | `_materialize_list_semantics` 在 Resolver 和 TransactionManager 前修改 Source AST | 列表结构变化先表示为 CandidateSet 和 DecisionSet，再由一个阶段批事务原子提交 |
| R03 | 列表续段、生成锚点和附录未完整进入事务路径 | 每种关系具备候选、决策、标准事务操作、Source AST 断言和审计证据 |
| R04 | preflight 以合成模块描述符宣称兼容，状态与失败策略不一致 | 注册真实模块描述符，执行前校验输入、节点、审计和失败策略，运行状态由实际结果生成 |
| R05 | legacy `retype_list_item` 被降为当前父节点的 move | 采用标准语义更新操作，或由注册 adapter 原子展开为可验证的标准操作 |
| R06 | 仅有 3 个 Issue 专项测试，L01 至 L16 无完整追溯 | 每个编号有独立输入、公共事实、Source AST、呈现和保存后输出断言 |
| R07 | ISSUE1 证据与 `abe7f49`、复测及审查状态不一致 | 在修复完成后更新证据和 status.json，记录真实命令、提交、结果和残留门禁 |

## 3. 架构约束

### 3.1 路由与兼容

1. `--modular-profile native-v2` 是 native 主链的唯一显式选择条件。路由只检查配置和受支持的源类型，不能读取 Markdown 文本或正则判断列表。
2. 未指定该 profile 时保留既有 legacy 默认路径。不得以本次修复改变默认 profile、删除 legacy 实现或重命名稳定 CLI 参数。
3. 不受支持的源类型由 profile 预检产生明确错误和报告诊断，不允许静默回退到另一条语义链。

### 3.2 语义候选和 Source AST 修订

1. Markdown 与 DOCX parser 只产生不可变 SourceFacts 和稳定 source reference。
2. ListSemantics、标题、注释、保护内容、题注和附录模块提交 CandidateSet。候选须包含模块版本、证据、作用域、稳定来源引用、语义意图和冲突约束。
3. 一阶段内所有 CandidateSet 收集完成后才能调用 Resolver。注册顺序或输入候选顺序不得改变相同输入的 DecisionSet。
4. Resolver 只能产生标准事务意图，例如创建节点、移动节点、更新角色、写入语义关系、写入呈现覆盖和拒绝诊断。自定义操作进入事务前必须由已注册 adapter 展开。
5. TransactionManager 对一个 DecisionSet 执行一次隔离复制、一次图校验和一次索引重建。任何必需操作失败、目标过期、关系不唯一或图非法时，整批 revision、ledger 和发布物均不得落地。
6. 已拒绝的候选保留决策与证据。被标为 `failure_policy=fail` 的未决或不支持候选终止发布；诊断型候选的可继续条件须在模块描述符中明确。
7. 事务提交后，NormalizationPolicy、Renderer、AuditModule 和 Publisher 只能读取该 revision、DecisionLedger 和允许的资源引用，不能从 SourceFacts、bridge 元数据或模块顺序补写语义。

### 3.3 列表语义与 legacy adapter

1. ListSemantics 候选必须覆盖列表容器、列项 owner、父列项、源 marker、源 sequence、续段、生成锚点、作用域和 unresolved 原因。
2. Source AST 保留真实嵌套关系。`wx-default-v2` 的一级 a)、b)、c) 展平、目标序号、重启、marker anchor 和模板样式只在 NormalizationPolicy 和 RenderPlan 表达。
3. 续段保留 owner，且不产生第二个编号承载。首块为表格、图片、子列表或引用的列项只在证据充分时生成一个锚点。
4. LegacyCapabilityAdapter 的 `retype_list_item` 和层级调整不能映射为无语义变化的 move。它们须映射至标准 `update_node_semantics`、`move_node`、`create_node` 或 `set_presentation_override` 组合，并携带 revision-bound TargetMap 和 source reference 预条件。
5. 有歧义的 legacy 映射、过期 revision 和不支持操作必须拒绝整个批次并保留结构化诊断。

### 3.4 模块契约、审计和消费状态

1. 每个执行模块在 Registry 注册真实 `ModuleDescriptor`，至少声明 contract version、支持的 source type、支持的 node kind、输出候选类型、failure policy 和必需 audit binding。
2. preflight 从实际注册表构建 ExecutionPlan。缺失模块、版本不匹配、source 或 node 不兼容、缺少 audit binding 都在候选执行前失败。
3. 能力状态只能在保存后计算：

   | 状态 | 条件 |
   | :--- | :--- |
   | `delivered` | 模块已注册并纳入执行计划，但本次没有可处理候选 |
   | `partial` | 模块参与运行，存在明确的延后、拒绝或未覆盖能力 |
   | `consumed` | 候选已裁决、事务已提交、Source AST 关系正确、规范化完成、模型审计和保存后审计均通过 |
   | `consumed_mismatch` | 候选或事务已执行，但 Source AST、规范化结果或保存后审计与模块声明不一致 |

4. `completed_with_warnings` 只能用于模块描述符允许继续的诊断项。`failure_policy=fail` 的失败结果必须阻止 Publisher 产出完成制品。

## 4. 实施任务卡

| 任务 | 依赖 | 允许修改范围 | 完成条件 |
| :--- | :--- | :--- | :--- |
| R01 路由和真实 preflight | 无 | `scripts/main.py`、`scripts/wxdf/registry.py`、对应测试 | native-v2 不再以源文本推断列表；真实 descriptor 预检拒绝不兼容组合；legacy 默认回归通过 |
| R02 候选到单批事务 | R01 | `scripts/wxdf/kernel.py`、`scripts/wxdf/transaction.py`、`scripts/wxdf/resolver.py`、事务测试 | Source AST 在 Resolver 后首次修改；一批只生成一次 revision、校验和索引；失败无半写结果 |
| R03 列表和 legacy adapter | R02 | `scripts/wxdf/semantics/lists.py`、`scripts/wxdf/legacy_capability.py`、`scripts/wxdf/resolver.py`、列表测试 | 真实层级、续段、锚点和附录都由事务写入；retype 和层级操作保留语义意图；歧义映射原子拒绝 |
| R04 运行状态和审计 | R02、R03 | `scripts/wxdf/kernel.py`、Registry、Audit 相关测试 | 模块状态仅由 resolve、commit、AST、normalization、保存后 audit 生成；fail 策略不发布制品 |
| R05 L01 至 L16 与证据闭环 | R01 至 R04 | `tests/`、`docs/execution-v3/evidence/ISSUE1.md`、`docs/execution-v3/status.json` | L01 至 L16 的双 parser 覆盖和所有系统合同通过；证据与状态反映真实提交和命令 |

每次修改 `.py` 前遵循 `AGENTS.md` 声明路径和分工。实现任务完成前不暂存、提交、推送、发布或修改公开 Skill。

## 5. 验收矩阵和验证命令

1. L01 至 L16 每项使用实际 Markdown 字符串或动态 DOCX 夹具，断言 SourceFacts、Source AST 关系、RenderPlan 和保存后 DOCX。
2. L01 至 L16 在两种 parser 都适用时需提供等价公共事实的独立输入。语法差异不可表达时记录原因和替代断言。
3. 额外验证 C03、C04、C05、C07、C08、C09、C12 和 C13：候选顺序确定性、原子失败、legacy 映射拒绝、语义 digest、发布失败恢复、OOXML 保存后审计、CLI 兼容和真实运行时。
4. 真实转换至少包含一个嵌套 Markdown 列表和一个 DOCX 列表夹具。输出须检查 CLI 返回码、JSON 报告、`unzip -t`、`numbering.xml` 和段落 `numPr`。

最低命令集：

```bash
scripts/.venv/bin/python3 -m pytest tests/test_issue1_native_pipeline.py -q
scripts/.venv/bin/python3 -m pytest tests/test_modular_lists.py tests/test_modular_resolver.py tests/test_modular_ast.py tests/test_modular_capability.py tests/test_modular_conversion.py -q
scripts/.venv/bin/python3 -m pytest tests/ -q
git diff --check
```

实际执行结果写入 `docs/execution-v3/evidence/ISSUE1.md`。在全部必需用例、真实转换和保存后审计完成前，相关任务状态保持 `in_progress` 或 `implemented`，不能标记为 `done`。

## 6. 非目标和门禁

以下事项不属于本修复：默认切换、删除 legacy 回退、Kylin V10 ARM64 真机验收、Windows x86_64 打包、公开 Skill 生成、标签、Release、安装副本同步和 WX 模板视觉设计调整。

本地架构修复完成后，Kylin 真机与页面视觉验收继续作为独立外部门禁记录，不能以本机单元测试替代。
