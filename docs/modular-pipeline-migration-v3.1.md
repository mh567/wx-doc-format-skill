# 转换底座完整能力迁移方案 3.1

## 1. 修订目标

方案 3.1 补充方案 3.0 的实施闭环。目标是让旧架构全部已验证能力通过新 ConversionKernel、ExecutionPlan 和版本化合同运行。迁移先使用 Adapter 保持行为，再逐项替换内部实现。module 文件存在、单元测试通过或编译清单包含均不能单独证明能力迁移完成。

## 2. 迁移原则

1. 旧 legacy 默认路径保持稳定，直到 modular 通过完整等价门禁。
2. Source AST 保存内容、角色、结构和关系。模板样式名只进入 Normalized AST 与 RenderPlan。
3. 语义缺陷在 Parser、SemanticModule、Resolver 或 Transaction 中修复。
4. Renderer 和 finalizer 只消费明确语义与模板合同。
5. 每个启用能力必须绑定至少一个保存后独立审计。
6. 缺少必需 module、Adapter 或审计时，计划预检拒绝运行。
7. 采用显式注册和依赖注入，不使用动态目录扫描。

## 3. 深 module 与 Interface

| Module | Interface | 首期 Adapter | 必需产物 |
| :--- | :--- | :--- | :--- |
| SourceParser | `parse(source, context) -> SourceFacts` | 现有 Markdown 与 DOCX 识别器 | 完整源事实、资源和诊断 |
| SemanticModule | `collect(stage_view, context) -> CandidateSet` | 旧范围、结构、列表、表格、题注、附录和保护规则 | 带证据的候选 |
| Resolver | `resolve(candidates, constraints, ledger) -> DecisionSet` | 现有新底座 | 唯一裁决结果 |
| TransactionManager | `apply(snapshot, decisions) -> CommitResult` | 现有新底座 | 新 revision Source AST |
| TemplateProvider | `load(template, context) -> TemplateBundle` | template_profile 与目录片段提取 | profile、fragments、编号和资源 |
| NormalizationPolicy | `normalize(ast, template, overlay, override) -> NormalizedResult` | 旧规范化规则 Adapter | 完整 RenderPlan |
| Renderer | `render(normalized, template_bundle, resources) -> RenderResult` | 旧 Markdown 与 DOCX renderer Adapter | DOCX 与 RenderMap |
| AuditModule | `check(model, output, context) -> Findings` | 旧结构、模板及保真审计 Adapter | 独立 Findings |
| EnhancementExecutor | `execute(requests, context) -> Responses` | 在线、命令和文件协议 Adapter | 版本化响应 |

## 4. 必需能力配置

`wx_full_rules` 计划必须包含 source parser、source region、document structure、protected content、list semantics、table semantics、caption semantics、appendix semantics、WX normalization、WX template provider、renderer、核心审计和发布 module。

Markdown 与 DOCX parser 属于同一 exclusive group。Renderer Adapter 可以按源类型选择。LLM module 只在增强模式加入计划。每个 descriptor 声明 `requires`、`provides`、版本范围、失败策略和配套审计。

## 5. AST 2.0 补充合同

1. paragraph role 增加 `title`。
2. table 记录 `header_rows`，table_cell 记录 `cell_role`。
3. 已有题注表示为 caption paragraph 和 `caption_target` association。
4. 确定性新增题注表示为带 `generated_by` 和 `generation_basis` 的生成节点。
5. LLM 题注文本进入 ContentOverlay，绑定目标摘要和关联 revision。
6. 模板目录片段属于 TemplateBundle，不进入 Source AST。
7. TemplateProfile 完整携带样式、编号、表格、目录、分页和附录能力。

## 6. 迁移顺序

### M1 能力基线

冻结 legacy 真实样本的 SourceModel、NormalizedModel、DOCX、报告和页面。建立能力清单与批准差异表。

### M2 可执行计划

扩展 registry，使 descriptor 能创建或引用 module 实现。ConversionKernel 必须执行 ExecutionPlan。启用能力未提供或审计未绑定时预检失败。

### M3 完整旧能力 Adapter

将旧 Markdown/DOCX parser、语义规则、renderer、template finalizer 和审计包装到新 Interface。此阶段允许 Adapter 内部使用旧模型，通过版本化 LegacyView 和 TargetMap 与 AST 交互。

### M4 AST 表达完整性

补齐 title、表头、题注关系、模板呈现和资源闭包。用真实样本证明 SourceFacts、Source AST、Normalized AST 和 RenderPlan 语义一致。

### M5 原生 module 替换

按照 SourceRegion、DocumentStructure、ProtectedContent、TableSemantics、CaptionSemantics、AppendixSemantics、Renderer 的顺序替换 Adapter。每次只切换一个 provider，并运行同一 Interface 合同和黄金回归。

### M6 收敛与删除

removal map 中仅当旧实现无调用方、Native module 等价门禁通过、回退方案可用时，状态才改为 removable。删除前保留一个版本的 Adapter 回退能力。

## 7. 真实模板验收

1. 文档标题使用模板文档标题样式，不带 Heading 编号。
2. 模板目录片段按既定策略复制，打开文档时允许更新域。
3. 标题层级、手工编号清理和模板编号一致。
4. Markdown 表头及表格矩阵完整。
5. 数据表题注位于表格上方，图片题注位于图片下方，SEQ 域独立编号。
6. 普通表格、API 表格和 layout 表分别遵守角色合同。
7. 列表、注释、公式、代码、引用、附录、图片、链接、域和书签通过专项审计。
8. `unexpected_styles` 为空，语义、呈现和视觉结果分别报告。
9. 任一必需审计未执行时，结果不能为 completed。

## 8. 切换门禁

只有 M1 至 M6 全部通过、真实 Markdown 与 DOCX 黄金样本无未批准退化、全量测试和页面抽检完成后，才允许评估 modular 作为默认路径。发布、标签和安装副本同步仍需单独授权。
