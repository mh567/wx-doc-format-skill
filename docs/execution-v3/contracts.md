# 接口落地约定

本文件将方案 3.0 的实现位置、初始选择与兼容约束固定下来。所有代码和 Schema 由任务卡创建，本文件不声称这些接口已存在。若后续证据要求调整，先记录 ADR、影响范围及测试，再同步本文件。

## 1. 包与数据表示

代码包采用 scripts/wxdf/。顶层旧入口、旧模型构造器和旧 CapabilityConfig 保留；新合同不通过修改旧 dict 含义偷偷接入。

运行期采用标准库 dataclass/TypedDict 加纯函数校验，序列化用 JSON。冻结对象的嵌套 dict 仍需只读视图或隔离复制，不能仅用 frozen=True 宣称深层不可变。序列化不携带 Python 活跃对象。

AST 使用 3.0 方案第 5 节的 blocks/containers/children/associations。children 是唯一结构事实；派生索引按 revision 绑定。inline_content 是段落内容唯一写入来源。Unknown 及 opaque 必须带诊断和源引用。

Schema 初始版本：AST 2.0、SourceFacts 1.0、新 Patch 2.0、新 capability_input 2.0、新 checkpoint 2.0。旧 Patch/文件传输 1.0 保持原路径。方案文件版本 3.0 不等于以上协议版本。

T01 必须定义角色特定字段：heading 的源语义 level，caption 的 caption_type/target，appendix 容器的源分类及标题引用，list/item 的源标记与序列，table 的语义类型及 cell 合并信息。禁止只验证全局字段而漏掉角色合同。

## 2. 稳定 Interface

| 所属文件 | Interface |
| :--- | :--- |
| kernel.py | ConversionKernel.convert(request: ConversionRequest) → RunResult |
| registry.py | build_plan(config, module_descriptors) → ExecutionPlan |
| parsers.py | parse(source, context) → SourceFacts |
| source_store.py | open_source(source)；resolve(source_ref)；close() |
| resolver.py | resolve(candidate_set, constraints, ledger_view) → DecisionSet |
| transaction.py | apply_stage_batch(snapshot, decisions, expected_revision) → CommitResult |
| decision_ledger.py | validate_revisions(facts, ledger)；replay(facts, ledger, plan) → SourceAST |
| normalization.py | normalize(source_ast, profile, overlay, presentation_override) → NormalizedResult |
| rendering.py | render(normalized, resources, profile) → RenderResult |
| auditing.py | check_model(model)；check_saved_output(path, expected, render_map) → Findings |
| enhancement.py | execute(request_batch, context) → CompletedResponses 或 WaitingExternal |
| legacy_capability.py | make_view(model) → LegacyView/TargetMap；translate(patch, target_map, snapshot) → CandidateSet |
| run_store.py | save_checkpoint(snapshot)；load_checkpoint(run_path, expected_fingerprints) |
| review.py | evaluate_candidate(baseline_bundle, candidate_bundle, findings) → AcceptanceDecision |
| publication.py | publish(bundle, destinations) → PublicationResult；recover(journal) |

contracts.py 定义上述输入/输出，不让调用方传完整内核。资源上下文、预算和能力以所需字段注入。SemanticModule 只提交候选，不能直接写 AST。模块的 requires 指向阶段产物或证据能力，最终角色只有裁决提交步骤可写。

## 3. 阶段与复杂度

固定阶段：source、scope、role、relation、normalize、render、audit、publish。LLM 证据收集嵌入对应阶段，复核最多一轮重跑受影响阶段及其下游。

每阶段所有候选合并后单次提交。LLM 按批次返回但阶段全部必要响应就绪才提交；部分等待保存在检查点，不每批重建模型。全量复制、图验证和索引重建次数以阶段和复核轮数为上界。

DecisionLedger 保存修订前置条件与 supersedes，不能只保存已应用事务 ID。重算前验证有效修订并参与裁决；源证据变化失效，原启发式不能静默覆盖有效修订。

## 4. 命令与运行选项

现有参数全部保留。新增参数由 T02/T09 实现，之前不能调用：

1. --pipeline legacy|modular：初始默认 legacy。
2. --list-policy legacy|wx-default-v2：初始默认 legacy；legacy pipeline 只接受 legacy，其他组合预检拒绝。
3. 原 --llm-enhance、--generate-requests、--resume 等组合先按能力清单预检。
4. 私有 --source-ast/--ast 在 modular 路径输出 AST 2.0，在 legacy 路径保持旧模型。release 模式仍拒绝这两参数。
5. --resume 使用检查点中已冻结的 pipeline、策略、合同版本和 TargetMap。请求时选项与恢复时显式传入选项冲突则拒绝。

RunResult 状态与退出码映射固定为：completed/completed_with_warnings 为 0；waiting_external 为 0 且报告明确未完成；参数、版本、模式不兼容为 2；阻断审计或执行失败为 1。legacy 保持已有退出行为，不反向统一修改。--fail-on-risk 在 modular 有风险时转失败并返回 1。cancelled 通过结构化状态及非零退出呈现，T01 固定平台中断映射。

所有测试显式指定有效模式并清除会改变行为的 WX 环境开关，测试旧 CLI 不传新增参数。

## 5. 候选、恢复与发布

Checkpoint 必须持有可恢复的 Facts 或验证重建描述、Ledger、Source AST、Normalized AST/派生描述、Overlay、PresentationOverride、TargetMap、资源摘要、模块及协议版本、请求和响应摘要、预算、轮数及候选基线。禁止仅保存 revision 数字。

响应持久化后应用幂等事务。事务 ID 相同而内容不同为冲突。暂停和恢复不能重复扣已完成请求预算，也不能抹掉已发生消耗。

语义与呈现都无变化才 no_effect。首期语义变化一律重渲染并完整审计，即使文本及外观相同。规范化不能丢掉新关系。

产物集合包含账本、模型、覆盖、DOCX、报告及清单。多目标路径发布采用日志与完成标记，只有全部必需输出就绪才 completed。读者以已提交清单判断整套版本，不能假称多路径写入具有单次文件重命名的原子性。

## 6. 首期范围控制

DocumentStructure/SourceRegion/ProtectedContent 的旧证据适配在 T05 提供，T13 再迁入正式 module。列表新路径在 T09 激活时不能因其他域尚未重写就丢掉已有标题、目录或注释能力。

T08 的 Renderer 可调用无语义判断的现有底层 OOXML/样式函数；若旧函数内部重判角色，先提取纯呈现部分。全图裁决、增量缓存和动态插件扫描均不扩大首期范围。

独立审计必须保存后回读输出并验证真实 OOXML；RenderMap 只作定位，不作为实际输出正确的唯一证据。所有 opaque 内容记录能力与保真范围。

源码单元验证使用现有开发 Python；Nuitka 生产构建遵循 packaging 的 Python 3.13 工具链，不能用当前 Python 3.14 冒烟替代正式平台验收。
