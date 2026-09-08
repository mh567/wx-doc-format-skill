# 转换底座与模块化改造方案 3.0

日期：2026 年 9 月 8 日。状态：设计规格，待实施验证。

本文整合 2.0 及第二轮评审结果，取代前两版作为后续实施依据。前两版保留供对照，本文可独立阅读。方案版本为 3.0，拟定 AST schema 仍为 2.0，两者独立编号。本文描述目标合同，未表示当前代码已具备这些能力。执行效率以真实基准验收，不承诺未经测量的全局最优。

## 1. 设计决策

保留“解析 → 规范化 → 模板渲染”三段式架构。转换底座负责运行生命周期和合同执行；解析器及语义 module 负责源事实与语义；模板策略负责呈现；渲染 module 负责输出和保真。

1. Source AST 是当前生效源语义的唯一读取入口。SourceFacts 保存不可变源证据，DecisionLedger 保存受验证的裁决修订；二者可重建 Source AST。角色及关系修复以候选修订事务提交，禁止直接改模型后丢失修订依据。
2. Normalized AST 是 Source AST、模板策略、ContentOverlay 和受限 PresentationOverride 的确定性派生产物。语义等价、呈现等价与输出复用分别判断。
3. AST 内容存储与关系存储分离，关系只有一个写入来源；索引可重建。
4. 语义 module 提交候选，统一裁决步骤提交角色与关系，避免执行顺序决定结果。
5. 插拔采用进程内显式注册和依赖注入，发布制品携带明确模块清单。
6. 保留 CLI、稳定函数入口、CapabilityConfig、文件式 LLM 协议及现有 WX 模板的兼容路径。
7. 首期采用固定阶段、确定顺序、共享索引和完整验证。细粒度增量调度、跨运行缓存、动态插件发现及写时复制后置。

沿用 codebase-design 的深 module 原则：整体解析器与完整语义能力提供小 Interface，内部规则保持集中。Markdown 解析器外侧的替换接口兼容 ADR 0001 的内部单一识别 module 设计；实施时新增 ADR 记录扩展范围。

## 2. 运行架构与阶段产物

```text
CLI / 兼容入口
       ↓
ConversionKernel：配置、能力预检、运行状态、预算
       ↓
SourceParser + SourceStore → SourceFacts
       ↓
范围及保护候选 + 有效裁决修订 → 裁决范围与保护约束
       ↓
结构及角色候选 + 有效裁决修订 → 跨模块裁决 → 关系候选及裁决
       ↓
Source AST → 核心合同校验
       ↓
NormalizationPolicy + TemplateProfile + ContentOverlay + PresentationOverride
       ↓
Normalized AST → 模型专项审计
       ↓
Renderer + SourceStore → 格式收尾 → 完整输出审计
       ↓
候选接受与产物发布 → DOCX、AST、报告
```

SourceFacts 保存 token/OOXML 提取事实、源范围、源资源引用、格式证据及输入诊断。它是解析阶段内部产物，不能被渲染器用来重新决定角色。

范围裁决确定目录排除、前置内容、附录候选范围和代码等保护上下文。角色裁决确定标题、正文、列表、题注等语义。关系裁决建立列表归属、题注目标、章节及附录关联。

阶段间产物带版本。范围修复使下游角色与关系产物失效；角色修复使相关关系产物失效。首期重新执行失效阶段及其全部下游阶段，读取候选 DecisionLedger 中有效修订。每轮每阶段至多执行一次，阶段内证据收集与裁决属于单个批次；新增回溯需求进入下一次有界修复轮次或返回未决诊断，不采用无限固定点循环。

跨阶段依赖必须显式申明。附录范围与标题证据可能互相依赖时，先使用 SourceFacts 中的原始标题证据产生范围候选，再输出范围内相对标题层级，不让两个模块互相等待最终结果。

## 3. 底座职责与接口

ConversionKernel 仅编排各 module 并执行合同，不包含标题正则、列表评分、模板样式名等领域规则。下列底座服务可独立测试，保持明确职责：

| 底座服务 | 职责 |
| :--- | :--- |
| PlanBuilder | 验证模块清单与依赖，生成固定执行计划 |
| ContractValidator | 验证版本、唯一归属、关系合法性及修改权限 |
| RunStore | 持久化运行清单、检查点、请求及提交记录 |
| SourceStore | 管理源包、资源引用和资源生命周期 |
| TransactionManager | 提交 AST 修改、接受候选及发布版本化产物 |
| MetricsCollector | 汇总计时、内存、调用预算和诊断 |

| 可替换或注入的 Module | 核心 Interface | 结果 |
| :--- | :--- | :--- |
| SourceParser | parse(source, context) | SourceFacts、资源描述、诊断 |
| SemanticModule | analyze(facts, resolved_view, context) | CandidateSet |
| SemanticResolver | resolve(candidates, constraints, context) | DecisionSet 与未决问题 |
| NormalizationPolicy | normalize(source_ast, profile, overlay, presentation_override, context) | Normalized AST 与映射诊断 |
| TemplateProvider | load(template, context) | TemplateProfile 与模板资源 |
| EnhancementExecutor | execute(request_batch, context) | 已完成响应或 waiting_external |
| Renderer | render(model, resources, profile, context) | 文档与节点到输出位置映射 |
| AuditModule | check(artifact_view, context) | Findings |

SemanticResolver 首期只有一个受控实现，通过注入接入；领域证据权重和裁决规则封装在规则集合中。替换裁决器必须通过相同合同测试，不允许绕过底座强校验。

模块描述包含 id、version、contract_version、stage、requires、provides、supported_source_types、supported_node_kinds、failure_policy；解析器及增强 adapter 另声明 SourceFacts、AST、Patch 和 capability_input 的支持版本范围。版本不兼容时预检拒绝，禁止凭接口同名推定兼容。可选配置只声明该 module 自身需要的选项。

requires/provides 表达阶段产物和能力依赖；候选角色可以重叠，不将“提供候选”解释为“获得块写入权”。最终语义只有裁决提交步骤可写，修改层级由底座限制。

统一结果状态为 completed、no_candidates、unresolved、unsupported、waiting_external、failed。no_candidates 属于正常结果；unresolved 必须附诊断和保真表示；unsupported 触发预配置的失败或降级策略；failed 不得伪装成无候选。

执行前验证缺失依赖、循环依赖、版本不兼容、能力缺口及同阶段互斥实现。内容相关能力不足在解析后再次验证。用户要求的功能缺失时停止；明确允许的保真降级输出 completed_with_warnings，并在报告列出范围。

所有替换实现运行相同 Interface 合同测试。模块访问只读阶段视图和类型化资源，禁止直接修改共享字典或访问整个内核。CPU 规则默认顺序执行；独立外部请求允许有界并发，结果提交顺序固定。

### 3.1 SourceFacts 公共合同

SourceFacts 使用独立 facts_schema_version，是解析器的公开输出合同。消费者不得读取原生 tokenizer、python-docx 对象或未声明的私有扩展。

| 必需字段 | 合同 |
| :--- | :--- |
| source | 输入格式、内容摘要、编码或包标识 |
| parser | 解析器 ID、版本、方言及配置摘要 |
| units | 按 ID 索引的源单元，包含 kind、source_ref、有序行内内容或资源引用 |
| containers | 可直接从源证据确认的物理或语法容器，如单元格、引用、显式列表 |
| children | 源容器内的有序引用，是源结构的唯一存储；不确定归属保留在可确认的外层 |
| evidence | 按目标引用索引的结构、标记、编号、样式、缩进和空行证据，记录来源与作用域 |
| resources | 资源 ID、类型、摘要、源定位及依赖描述 |
| diagnostics | 未知语法、损坏结构、能力不足及保真处理记录 |
| extensions | 按格式和模块命名的扩展，读取方必须声明能力依赖 |

源容器事实与最终语义容器分别表达。Markdown list token 的包围关系属于强语法证据；DOCX 视觉缩进属于布局证据，不直接决定父列项。DOCX 无显式列表容器时保留物理段落顺序，交由语义 module 提交关系候选。

列表公共证据包括 marker_kind、marker_text、ordinal、explicit_depth、indent_value、indent_unit、numbering_definition_ref、restart_hint、blank_before、continuation_hint。未知值用 null 或省略，禁止以零替代。缩进携带单位；格式专有单位转换由 parser adapter 完成，原值留作证据。

源样式继承等专有能力在 requires 中明确声明，例如 docx.style_inheritance。公共列表算法消费归一化证据，避免依赖 DOCX 私有对象。

source_ref 包含源摘要、part 或文件标识、源位置及可选子范围。字符偏移统一采用 Unicode 码点和半开区间；DOCX 子范围采用有序 run/子节点定位及摘要。同一输入、解析器版本、方言和配置下确定生成 ID。跨解析器无需 ID 相同，比较公共事实与源范围等价性。

拆分源对象时由源引用和子范围派生单元 ID；角色重分类保留内容块 ID；新增容器 ID 由确定性阶段键或事务 ID 与操作序号派生并持久化。恢复优先加载快照；重新解析时须证明节点与源范围可匹配。

SourceFacts 完成后不可变。目录排除、保护范围和附录范围等决定保存于裁决产物与 DecisionLedger，以源引用关联事实，禁止把新裁决写回源事实。

合同测试使用两个独立 parser adapter 输出公共事实，再运行同一语义 module，证明没有私有对象依赖。测试 adapter 可受控构造事实，无需为测试新增第二套生产 tokenizer。

## 4. 模块覆盖与现有代码映射

| 能力 | 目标归属 | 现有代码及迁移要点 |
| :--- | :--- | :--- |
| Markdown 输入 | Markdown SourceParser | md_pipeline.py、markdown_recognition.py；整体可替换，内部 token 调度保持封装 |
| DOCX 输入 | DOCX SourceParser | docx_pipeline.py；提取编号、样式、源对象和容器事实 |
| 封面、修订记录、源目录 | SourceRegion module | front_matter.py、toc_detector.py；排除必须有范围与理由 |
| 标题、章节、正文 | DocumentStructure module | 归并 docx_pipeline.py、text_utils.py 及规范化中的角色判断 |
| 有序、无序、嵌套及续段 | ListSemantics module | list_detector.py、list_group_detection.py、list_hierarchy.py、unordered_lists.py；规则集中 |
| 普通表格、API 示例表、布局表 | TableSemantics module | table_semantics.py；保留普通表格与 layout 合同差异 |
| 已有题注、目标关联、位置语义 | CaptionSemantics module | caption_placement.py 及解析器中的题注判断 |
| 附录范围和相对标题 | AppendixSemantics module | appendix_semantics.py；范围先决策，标题后派生 |
| 注释、代码、引用及公式保护 | ProtectedContent module | note_semantics.py、解析器规则；模块内部按角色分派，完整保留注释编号 |
| 图片、链接、域、书签及行内格式 | SourceParser、SourceStore、Renderer | 提取及复制物理内容，能力清单声明结构化处理或 opaque 保真 |
| 样式、编号、目录和分页策略 | NormalizationPolicy | model_normalization.py、list_style_mapping.py；移除已迁移的源角色重判 |
| 模板加载及样式能力 | TemplateProvider | template_profile.py、现有模板资源加载；缺失必需样式时预检失败或使用明确兼容策略 |
| LLM 功能 | CapabilityConfig 与 LegacyCapabilityAdapter | 保留能力名与阶段别名，分别适配读取视图和 Patch 写入；无法无歧义转换的操作拒绝 |
| 在线调用、命令调用、文件回放 | EnhancementExecutor adapters | llm_bridge.py、llm_file_protocol.py、main.py 中调用桥接 |
| 内容渲染与格式收尾 | Renderer 内部 | word_model_renderer.py、docx_render.py、template_finalizer.py、table_formatting.py |
| 结构、保真、模板专项审计 | 核心校验及 AuditModule | audit.py、toc_contract.py、note_semantics.py 等专项审计 |
| 候选接受与修订重放 | TransactionManager、DecisionLedger 与接受策略 | review_loop.py、main.py；接受完整产物集合，重算保留有效修订 |
| 报告及 AST 序列化 | RunStore、报告输出 adapter | reporting.py、document_model.py、现有报告脱敏路径 |
| 模型版本迁移 | 底座迁移服务 | 新增单向迁移与兼容视图，禁止丢失表达能力后静默降级 |
| 编译和公开分发 | 构建合同 | packaging/nuitka 下流程；显式模块清单纳入编译及运行时验收 |

脚注、尾注、文本框、批注、修订标记等复杂 DOCX 内容必须在能力清单中逐项标明支持程度。首期无结构化能力的内容进入明确的 opaque 保真路径；复制依赖无法满足时报告 unsupported，禁止声称完整支持。

核心图结构校验、内容守恒检查、必需样式和输出包完整性不可关闭。专项审计可注册；启用对应语义能力时，其必需审计同步启用。

## 5. AST 2.0 的唯一事实来源

建议目标 schema_version 为 2.0。以下是目标合同，字段由 JSON Schema 和合同测试固定后进入实现。

1. document.blocks 为按 ID 索引的内容块表，保存 paragraph、image、table、opaque 等内容。paragraph 使用 role 表达 body、heading、caption、note 等，内容由有序 inline_content 保存；列表编号属于 list_item 容器，避免把续段变成新列项。
2. document.containers 保存 document、section、appendix、quote、list、list_item、table_row、table_cell 等容器元数据。内容块 ID 和容器 ID 在文档内全局唯一。
3. document.children 保存容器及 table 块的有序子 ID，是结构归属与阅读顺序的唯一事实来源。除 root 外，每个节点恰有一个结构父节点，结构图无环。
4. parent_id、owner_item_id、list_id、section_id、semantic_depth 都由 children 派生为只读索引，不持久化为第二份可修改事实。
5. document.associations 保存 caption_target、sequence_continuation 等非所有权关系，不改变结构归属。编号续接关系不得成环。
6. 列表 kind、源起始值、源重启定义保存在 list 元数据；source_marker、source_ordinal 保存在 list_item。sequence_id 只标识源编号链，目标计数器由模板策略派生。
7. source_ref 是源内容引用，source_order 和 source_position 用于审计，不控制最终树的阅读顺序。生成节点带 generated_by 和依据，不伪造源位置。
8. Source AST 不存模板样式名。Normalized AST 引用 source_revision，提供不可变语义视图、presentation 映射和生成内容节点。跨文件序列化允许携带语义快照，避免为了节省存储破坏报告可独立读取能力。

表格迁移后，table 的 children 引用行，行引用单元格，单元格引用内容块或嵌套容器。合并信息用行列坐标、row_span、col_span 表示，覆盖位置只引用锚单元格，禁止重复保存合并单元格内容。旧 rows 文本仅由兼容视图生成。

封面或目录的原始内容保存在 SourceFacts，排除决定保存在裁决记录，包含源范围、哈希、原因及生效版本；内容守恒校验允许这些明确排除及题注等有来源的新增。

以下完整最小模型包含父列项正文、子列表、表格单元格正文、父列项续段和下一个父列项。示例省略的是可选证据字段，所有结构引用均可解析。

```json
{
  "schema_version": "2.0",
  "revision": 1,
  "document": {
    "root_id": "root",
    "blocks": {
      "p1": {"block_type": "paragraph", "role": "body", "inline_content": [{"kind": "text", "text": "父项一", "marks": []}]},
      "p2": {"block_type": "paragraph", "role": "body", "inline_content": [{"kind": "text", "text": "子项", "marks": []}]},
      "t1": {"block_type": "table", "table_type": "data"},
      "p3": {"block_type": "paragraph", "role": "body", "inline_content": [{"kind": "text", "text": "单元格内容", "marks": []}]},
      "p4": {"block_type": "paragraph", "role": "body", "inline_content": [{"kind": "text", "text": "父项一的续段", "marks": []}]},
      "p5": {"block_type": "paragraph", "role": "body", "inline_content": [{"kind": "text", "text": "父项二", "marks": []}]}
    },
    "containers": {
      "root": {"kind": "document"},
      "l1": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s1"},
      "i1": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1},
      "l2": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s2"},
      "i2": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1},
      "r1": {"kind": "table_row"},
      "c1": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1},
      "i3": {"kind": "list_item", "source_marker": "2.", "source_ordinal": 2}
    },
    "children": {
      "root": ["l1"],
      "l1": ["i1", "i3"],
      "i1": ["p1", "l2", "t1", "p4"],
      "l2": ["i2"],
      "i2": ["p2"],
      "t1": ["r1"],
      "r1": ["c1"],
      "c1": ["p3"],
      "i3": ["p5"]
    },
    "associations": []
  }
}
```

强校验包括：所有引用存在、ID 唯一、父归属唯一、图无环、子类型合法、列表只直接包含列项、表格只直接包含行、行只直接包含单元格、表格跨度不冲突、题注目标类型合法。

空列项允许存在；规范化阶段声明带标记的空承载段落，渲染器按模型呈现该真实列项。对于缺失父级的异常输入，保留可确认的外层容器和源层级证据，标为 unresolved，不构造不存在的父列项。

1.0 迁移保留源标记和 source_ref。旧块序列可生成根 children；列表归属缺少证据时只建立可证明关系。旧 rows 可转为单元格段落，但无法恢复的段落边界必须诊断。旧 list_item 块转换为 list_item 容器与内容段落；旧消费者使用派生兼容视图。2.0 无法无损表示为旧格式的特性应拒绝旧路径或使用事先声明的保真方案。

### 5.1 行内内容与编号承载合同

paragraph.inline_content 为有序行内节点数组，是段落内容唯一可修改来源。段落 text、旧 inline_runs 和搜索文本都由它派生；兼容序列化携带 text 时必须验证其等于约定的纯文本投影。

| 行内 kind | 内容与顺序要求 |
| :--- | :--- |
| text | 文本及允许的 marks，禁止隐式丢失换行 |
| break、tab | 显式软换行或制表，不与段落边界混淆 |
| image | 资源引用、替代文本与源引用；浮动定位另存布局证据 |
| link | 目标及有序子行内节点，禁止嵌套 link |
| field、bookmark、opaque_inline | 明确结构或受控源片段引用及依赖；无法保真时 unsupported |

以下为行内数组片段，所属文档必须提供 resources.img1，完整模型验证资源引用存在。

```json
[
  {"kind": "text", "text": "见", "marks": []},
  {"kind": "image", "resource_id": "img1", "alt_text": "示意图"},
  {"kind": "link", "target": "https://example.org/spec", "children": [
    {"kind": "text", "text": "说明", "marks": ["emphasis"]}
  ]},
  {"kind": "text", "text": "。", "marks": []}
]
```

文档 children 管理块和容器，行内 children 仅管理同一段落内行内序列，两个类型与校验空间分离。跨段域或书签属于非所有权范围关联；不具备结构化能力时须按完整源范围受控保真，禁止拆分后丢失配对关系。

迁移保留旧 inline_runs 的 marks、href；仅有 text 时生成单个 text 节点。资源、域和换行通过源引用保留。源范围与资源审计独立于文本投影，不能仅凭字符拼接判定保真。

每个 list_item 的 presentation 明确 marker_anchor、target_sequence、target_level、marker_kind：

1. 首个直属子节点是可编号普通 paragraph 时，由它承载唯一标记。
2. 首个直属节点为表格、块图片、子列表、quote、受保护代码或禁止编号的段落时，在列项开头生成空承载段落。禁止将标记延后挂到后面的续段。
3. 真正空列项同样生成承载段落；只含行内图片的普通 paragraph 可以承载编号。
4. generated_anchor 由规范化加入 Normalized AST 或呈现计划，ID 由 item_id 与策略版本派生，记录 generated_by。Renderer 不临时发明模型外段落。
5. 每项恰有一个承载位置，续段无新增标记，子列项有自己的承载位置。生成承载段落不被内容守恒误报为源内容新增。

对应夹具覆盖混合行内内容、链接格式、域及书签配对、表格起始、子列表起始、空列项与续段。

## 6. 跨模块裁决与修改事务

Candidate 包含 candidate_id、module_id/version、目标源引用或节点 ID、候选角色或关系、证据来源、正负证据、适用范围、约束、理由及可选置信度。不同 module 的置信度不能直接比较；优先使用证据类别和明示规则，数值评分须在同一裁决规则内校准。

裁决次序为：显式用户覆盖及格式合同、已验证的结构约束、组合上下文证据、启发式证据、可选 LLM 候选。用户覆盖也必须满足图结构和输出可表达性校验。源样式名或单个正则命中不能独立成为全局强约束。

例如“1. 系统要求”同时产生 heading/list 候选时，使用编号定义、标题样式、相邻结构和所属范围裁决；代码容器内的“1.”按字面保留；“注1：说明”结合注释样式和源编号定义裁决，单纯文本前缀不足以覆盖强列表结构证据。

无法消解的冲突输出 unresolved、候选及证据。保留源顺序和内容，不强行选取高分角色。LLM 仅补充裁决证据，响应通过相同约束验证。

DecisionSet 转为 ChangeSet，包含事务 ID、目标层、expected_revision、操作、前置条件、依据及失效阶段。允许的结构操作包括创建节点、改角色、移动节点、更新关联和受限删除；删除必须解释内容去向或授权排除。

同一阶段的候选汇总并裁决为一个 DecisionSet，以单个批次事务提交。在隔离候选中完成全部操作，再验证并提交。移动节点的旧父删除和新父插入属于同一原子修改。相同事务及相同内容重复提交返回既有结果；相同 ID 携带不同内容返回冲突；版本过期返回冲突；非法操作整批拒绝。语义 module 无权直接写 Normalized AST 或输出 XML。禁止逐列项复制完整模型或提交结构事务。

首期每个阶段批次提交后重建一次索引，缓存键包含模型 revision。标题及附录范围变化从范围阶段重算，读取有效裁决修订。候选内部可批量原位修改，对外保持快照隔离，阶段结束才执行一次完整结构校验。不得在旧索引上执行下游 module。

### 6.1 DecisionLedger 与修复持久性

DecisionLedger 保存受验证修订及重放约束，Source AST 是修订生效后的语义读取视图。消费者不能绕过 Source AST 自行合并修订。初始识别证据进入 DecisionSet 审计记录；需要跨重算保留的用户覆盖及已接受复核修订进入 DecisionLedger。

每条修订包含 revision_id、parent_revision、transaction_id、target_source_refs、target_node_ids、property_scope、decision、preconditions、evidence、origin、resolver_version、status、supersedes。status 为 active、superseded、invalidated 或 conflicted。修订只表达语义属性，禁止携带任意可执行代码。

重算顺序为：验证源及能力版本 → 定位目标 → 验证源摘要、范围及前置条件 → 生成普通候选 → 将有效修订作为受约束决定参与本阶段裁决 → 验证合同 → 派生 Source AST。仍然有效的已接受修订不能被同一套启发式规则静默覆盖。

源内容、目标范围、依赖关系或规则合同变化，使前置条件不成立时，修订标为 invalidated 并产生诊断。新指令或有效复核要替代旧修订时显式建立 supersedes 链。相互冲突的有效修订返回 conflicted，禁止按时间戳或模块加载顺序任取结果。所有修订受不可绕过的图结构校验约束。

前置条件针对源事实及明确依赖，不能要求“当前角色等于修复后的角色”，避免重算初始状态使修订失效。关联依赖通过稳定源引用及已接受修订版本验证。

新修订先进入候选账本，再沿失效阶段重建候选模型；整套候选接受后才提交账本。候选拒绝不污染基线。检查点保存完整修订链或经校验的快照加日志，不能只保存事务 ID。

必需验收：heading 改为 list 后完整重算保持；父归属修复跨进程恢复保持；相同事务重放无重复变化；源变化使修订失效；冲突拒绝；候选拒绝后基线账本及模型摘要不变。

## 7. 模板策略与列表的可观察行为

以下 wx_default_v2 是依据 AGENTS.md 对未明细情形给出的目标设计，需作为显式行为变更通过黄金样本验收。纯架构提取阶段维持原实现；不得提前切换默认值。

1. Source AST 完整保留多级有序与无序关系。
2. 目标有序列项统一使用一级 a)、b)、c) 编号，按输出阅读顺序计数。子有序列项消耗同一目标作用域的计数；返回父级后继续计数。
3. 无序列项、正文续段、图片和表格不消耗外层有序计数。无序符号及缩进由 TemplateProfile 显式定义。
4. 正文区遇到最终 heading 或 appendix 标题后重启。列项内或引用内的局部标题不重启外层计数，避免容器内部结构污染正文计数。
5. 表格单元格和引用使用独立编号作用域；正文列表下的混合嵌套不另开目标有序作用域。单元格中的局部标题只影响本作用域。
6. 源跳号、重复标记和中途重启保存在 Source AST，wx_default_v2 目标计数遵循上述统一规则。保留源编号另设策略，通过选择项启用。
7. 续段归属及语义层级仍写入输出映射和报告。一级显示会弱化源层级的视觉表达，报告记录该呈现变换；层级保留策略必须另有模板能力和验收样本。

| 用例与输入结构 | 预期归属 | 源编号 | wx_default_v2 输出 | 必需诊断 |
| :--- | :--- | :--- | :--- | :--- |
| 父项一含子项，随后父项二 | 子项属于父项一，父项二与父项一同级 | 1 → 子1 → 2 | a)父项一、b)子项、c)父项二 | 记录层级呈现映射 |
| 父项一含无序子项后接续段，再接父项二 | 无序列表及续段属于父项一 | 1 → 无序 → 2 | a)、无序、无编号续段、b) | 无误加编号 |
| 列表后有容器外正文，再出现编号项 | 正文留在外层，两段列表分别建组 | 1、正文、2 | a)、正文、b) | 续接不确定时保留源序列歧义 |
| 连续三个一级项后出现二级项 | 二级项属于第三个有效父项 | 1、2、3、子1 | a)、b)、c)、d) | 缩进冲突时 unresolved |
| 源列表为 3、7、1，随后正文标题及新列表 | 源列表保留标记；标题开启目标编号作用域 | 3、7、1；1 | a)、b)、c)；a) | 源跳号证据，不能误报内容丢失 |
| 父项内表格单元格含两个列项，随后父项二 | 单元格列表独立 | 外1、单元格1/2、外2 | 外a)、单元格a)/b)、外b) | 容器隔离 |
| 代码内有 1. 与任务复选框样式文本 | 内容属于代码 | 字面标记 | 原样代码 | 禁止列表误分类 |
| 首个可见块具有孤立深层编号 | 保留可确认外层归属，不生成假父项 | 源 ilvl 保留 | 按允许的保真降级策略呈现 | unresolved 必须可见 |

测试夹具必须使用实际 Markdown 字符串或 DOCX 文件固定缩进、空行、numId、ilvl、样式和容器信息，不能只用这张语义说明表代替输入。每个夹具保存源事实、预期 AST、呈现映射和审计断言。

扩展矩阵包含同级类型切换、多个空行、制表符、编号后空内容、多段续写、子列表后回退多级、任务列表、引用内列表、合并单元格、嵌套表格、分页和分节、附录切换、API 标签、版本号、日期及手工标题冲突。采用针对性反例、组合测试和结构性质测试，未知输入必须保真或显式拒绝。

## 8. LLM 暂停、恢复与内容补充

在线调用、命令调用和文件请求回放共享 EnhancementExecutor Interface。CapabilityConfig 保留能力名与注册入口，模型读取和语义回写使用版本化 adapter。新能力消费新合同，旧能力进入 LegacyCapabilityAdapter；禁止将 AST 2.0 字典直接传给依赖旧块数组的 collector。

运行状态为 created、running、waiting_external、completed、completed_with_warnings、failed、cancelled。waiting_external 保存 resume_stage；它可从范围复核、角色复核、题注生成或审计后复核阶段产生。

检查点记录 run_id、阶段、SourceFacts 快照或可验证重建描述、DecisionLedger 快照及日志、Source AST 快照与 revision、ContentOverlay、PresentationOverride、规范化派生版本、输入及外部资源摘要、模板摘要、有效配置、各合同版本、模块清单与版本、请求 ID、响应摘要、已应用事务及内容摘要、预算消耗和候选基线标识。JSON/JSONL 只保存可序列化产物，禁止序列化活跃 python-docx 对象。候选阶段暂停须保存基线及候选的独立引用，响应先持久化，再通过幂等事务应用。

恢复先校验输入、模板、配置和模块版本。发生变化时拒绝旧响应并要求新运行，除非存在经过测试的显式检查点迁移。相同 request_id 和相同响应重放不重复应用；相同 ID 配不同内容返回冲突。

SourceStore 每次进程执行打开源包一次；跨进程恢复重新打开并校验摘要，依据 source_ref 重建资源映射。源引用由包内 part、稳定路径或源范围及内容摘要构成，同一个源对象拆成多个块时附子范围标识。

LLM 等待时间与活跃执行预算分开记录。重试计入总调用预算；超时停止采纳迟到结果及继续派发。取消由 adapter 尽力执行，不能把线程返回超时等同于网络请求已终止。

LLM 生成题注文本保存在版本化 ContentOverlay，记录目标 ID、目标内容摘要、能力版本及已验证文本。它可进入 Normalized AST，Source AST 保持源内容事实。目标内容或关联发生变化时 overlay 失效并重新验证或生成。

### 8.1 旧 Capability 与 Patch 兼容合同

方案版本、AST schema、SourceFacts schema、Patch 协议、文件传输协议和 capability_input 合同分别编号。保留能力名不代表旧模型、旧 Patch 和新关系图可直接互通。

LegacyCapabilityAdapter 为每次请求生成只读 LegacyView 和 TargetMap。前者按阅读顺序投影旧块数组，后者将旧请求块 ID 映射到新节点、list_item、源引用及模型 revision。旧 ID 不得根据回复时的数组下标重新推断。列表首段、续段、生成承载段落及单元格内列表分别声明映射能力；无法无损投影的范围禁止交给相应旧能力。

旧 collector、prompt_builder、validator 运行于隔离兼容视图。会修改旧模型的 after_phase 回调运行于独立副本，变更经受限 diff 提取为候选；不能约束修改范围的回调标为 unsupported。旧代码不得直接修改新 Source AST。

| 旧操作 | 转换合同 |
| :--- | :--- |
| retype 为 heading/body/caption | 提交角色候选，附带关联及保护约束；列表首段退出列表须同时具备有效关系重建，不能只改 role |
| retype 为 list_item | 容器、组及归属证据充分时生成节点与关系候选，否则 ambiguous_legacy_patch |
| adjust_level 用于 heading | 映射标题语义层级修订，重新生成章节关系 |
| adjust_level 用于 list_item | level 仅作线索，须唯一可证明父容器；多解或缺失证据时拒绝转换 |
| set_restart | 显式区分源编号语义和呈现属性；旧上下文无法确定时拒绝，禁止默认为全局重启 |
| set_table_type | 生成受表格及 layout 保护约束的候选 |
| caption 文本生成 | 写入经校验 ContentOverlay，绑定目标摘要与关联版本 |

TargetMap、各合同版本、视图摘要及请求内容摘要随文件请求检查点保存。旧历史 run.json 缺少映射时使用匹配版本的旧执行路径，或经过显式可证明的检查点迁移；禁止直接用历史回复更新 AST 2.0。

翻译失败返回结构化错误及目标范围。文件模式生成新版本请求并进入 waiting_external；在线模式在剩余预算内使用新协议重发，否则保留确定性结果及未决诊断。必需能力失败则运行失败，允许降级须符合配置合同。

兼容测试覆盖旧 collector 读取、每类操作翻译、过期 revision、歧义父项、生成块、续段、单元格列表及历史响应重放。版本与适配声明可由旁路描述提供，保留原 CapabilityConfig 构造方式。

## 9. 候选修复与一致性提交

审计发现语义问题时，建立独立候选 DecisionLedger 和模型分支，验证新修订后沿失效阶段派生候选 Source AST，再重新规范化和渲染。纯呈现问题修改候选 PresentationOverride，限制可写属性、标记来源并作为显式规范化输入；禁止用它改角色或父子关系。

候选流程为：验证修改 → 重建索引和失效阶段 → 派生 Normalized AST → 模型审计 → 新目标 DOCX 渲染及格式收尾 → 完整审计 → 接受或拒绝。

接受条件必须全部满足：核心不变量通过、无新增阻断问题、无未授权内容变化、被修复目标有实际改善，且既有保护性审计没有退化。沿用 review_loop.py 的接受策略并增加新关系合同测试，不能仅凭总分增加接受。

接受单位包含 DecisionLedger、Source AST、ContentOverlay、PresentationOverride、Normalized AST、DOCX、报告及模块清单。拒绝时基线整套保持不变，诊断日志可以追加。

语义等价、呈现等价及输出复用分别判定：

| 情形 | 处理 |
| :--- | :--- |
| 语义和呈现均无变化 | no_effect，跳过候选渲染；纯证据及诊断变化可追加审计记录 |
| 语义变化，呈现计划相同 | 验收真实语义改善；首期仍重新渲染并完整审计 |
| 语义不变，呈现变化 | 验证 PresentationOverride 后重新渲染及完整审计 |
| 语义和呈现均变化 | 验证源修订，重新规范化、渲染及完整审计 |
| 规范化丢失新语义 | 违反语义保留合同，拒绝候选并报告 normalization_semantic_loss |

语义等价比较内容、角色、层级、结构归属、源编号链、题注及附录关联，排除时间戳和纯运行指标。呈现等价比较完整渲染输入：输出顺序、行内格式、标记承载、资源摘要、模板与策略版本、字段、书签、布局和呈现覆盖。语义等价不能因一级编号显示而忽略父子关系。

首期只对语义及呈现均无变化的候选跳过渲染。未来如支持语义改变时复用 DOCX，必须证明完整 RenderPlan 等价、校验资源与输出包摘要、重建节点映射并重跑受影响审计。不能只比较文本或样式；复用证明不成立则完整渲染。

关键反例：子列表从错误父项移至正确父项，一级 a)/b)/c) 外观可能完全相同，仍属于有效语义修复，不能记为 no_effect。

完整产物先写入同文件系统的候选版本目录，校验后原子切换当前版本清单。多个独立输出路径无法通过单次 rename 实现全体原子替换，兼容 CLI 输出使用发布日志和完成标记，崩溃恢复时补齐或撤回未完成发布。只有清单提交及必需输出完成后报告 completed。

默认最多一轮审计后候选修复；文件恢复沿用同一轮次计数。无有效修改时跳过重渲染。基线结果存在阻断问题且候选未修复时返回 failed，并保留可诊断产物。

## 10. 保真、模板与渲染合同

TemplateProfile 声明语义角色到样式、编号定义、目录层级、分页规则、表格规则和可用能力。模板加载一次，候选输出使用新的目标文档树。

Renderer 返回节点到输出段落、表格及关系 ID 的映射，审计使用该映射核对 AST 和输出。一个源对象拆成多个块时必须按子范围复制或重建，防止整段 OOXML 重复复制。

首期以 adapter 包装现有两条渲染路径，记录差异。随后逐类迁移到 children 顺序驱动：正文与列表优先，表格和图片保留受控物理复制。最终语义仅从 AST 读取。

资源复制需处理图片、超链接、书签、域及关联 part 的映射。复杂对象使用声明的 opaque 路径时也需复制依赖闭包；无法保证完整性则报告 unsupported。源对象只读，目标 OOXML 必须独立拥有节点。

格式收尾属于 Renderer 内部，仅允许应用 AST/TemplateProfile 明示的样式、表格属性、目录字段、图片段落和分页合同。它不能重分类正文、增加未经模型声明的题注或恢复已排除内容。

最终校验保留现有 unexpected_styles、标题手工编号、普通表格与 layout 区分、目录专项、附录专项、列表重启及注释保真要求。包结构和语义审计与页面视觉检查分别报告。

## 11. 性能合同与首期实现范围

令 N 为内容及容器节点数，E 为结构及关联边数，K 为候选证据数。列表结构处理目标为 O(N+E+K)，候选生成使用有界邻域、容器栈和共享编号/样式索引，禁止对每个块重复全篇搜索。图片及 XML 复制按字节量独立计量。

每个稳定阶段版本构建一次 ID、父归属、章节和编号作用域索引。阶段内全部候选汇总、裁决和批量应用后，进行一次完整结构校验及线性重建，旧缓存随 revision 失效。禁止逐节点复制全模型、全图校验或全量重建。

令 S 为一次运行的固定阶段数，R 为修复轮数上限。完整模型复制、全图校验及全索引重建次数目标为 O(S×(1+R))，不能随列项数增长。S 在运行开始时由计划确定，R 默认最多一轮。LLM 请求分批只影响证据收集，不能每批回复都触发完整语义提交。

增加 full_model_copy_count、full_graph_validation_count、index_rebuild_count、stage_commit_count 指标。相同计划及轮数下，从 100 项扩大至 1000 项时，这些次数保持计划确定的上界，耗时与空间随数据量增长。模块数变化则按新的 S 单独比较。

批内操作使用隔离候选的可变 builder，对外仅暴露已验证快照，首期不需要逐操作持久化数据结构。只有基准证明必要后才引入增量索引，并以全量结果作为等价性判据。

普通纯规则成功路径目标：每次进程执行一次源包打开、一次输出渲染、零 LLM 请求。候选修复允许额外一次输出渲染。Source AST、候选和报告的必要隔离保留，先去除可证明冗余的复制。

性能基准按 Markdown/DOCX、规则/LLM、冷/热启动、文本/列表/图片/复杂表格分组。记录各阶段耗时、总耗时 P50/P95、峰值内存、输入字节数、N/E/K、资源复制字节数、渲染次数和 LLM 调用量。

先固定环境、样本哈希、预热和重复次数，再比较改造前后。初始门槛为纯规则 P95 回退不超过 5%、峰值内存回退不超过 10%；短任务使用基线阶段确定的绝对容差。列表规模增长试验检查耗时趋势，不把单次计时当作复杂度证明。

首期不实现跨运行结果缓存、动态扫描第三方 Python 插件、通用事件总线、任意 DAG 调度、细粒度写时复制或局部 DOCX 重排。模块依赖仅用于预检和固定阶段内稳定排序。外部 LLM 并发有界且不操作共享 XML。

## 12. 迁移计划与验收门禁

| 阶段 | 交付 | 必须通过的门禁 |
| :--- | :--- | :--- |
| A | 基线、行为差异表、SourceFacts/AST/行内 schema 与 Interface 夹具 | 固定编号差异，事实可替换性、恢复和保真夹具可复测 |
| B | ConversionKernel、现有 parser/renderer/executor adapters | CLI 与旧结果兼容，旧在线及文件生成/恢复均通过 |
| C | AST 2.0、迁移、SourceStore、DecisionLedger、阶段批事务和索引 | 完整模型及行内结构、修订重算保持、次数上界、失败原子性通过 |
| D | ListSemantics、裁决、编号承载渲染、审计及纯规则产物发布 | 新路径仅开放纯规则模式；混合嵌套端到端通过，未支持模式预检阻止 |
| E | LegacyCapabilityAdapter、新协议检查点、候选接受与恢复发布 | 在线/文件/复核合同通过后逐模式开放；修复跨恢复保持 |
| F | wx_default_v2 显式策略及其他语义 module | 行为变更样本验收后切默认，注释与附录等保护语义无退化 |
| G | 双渲染路径逐类收敛、热点优化及分发 | 性能门槛、真实 DOCX、页面抽检及编译产物能力一致 |

各阶段可独立回退，旧入口委托新实现。AST 2.0 关系能力不能交给无法表达它的旧 renderer；阶段 C 保持旧生产路径。阶段 D 完成列表解析、规范化、渲染、审计及纯规则发布后，只开放新纯规则路径。缺少门禁的模式执行前报告不支持，不静默切换算法。

| 模式 | AST 2.0 开放条件 |
| :--- | :--- |
| 纯规则转换 | 阶段 D 全部门禁通过，包括模型及产物一致性提交 |
| 在线 LLM 增强 | 阶段 E 中新能力或兼容 adapter、预算、候选修复及接受合同通过 |
| 文件请求生成与恢复 | 阶段 E 中 TargetMap、账本快照、合同版本和幂等恢复通过 |
| 审计后语义复核 | 阶段 E 中修订重放、语义/呈现独立判断、回退及发布通过 |
| 旧检查点恢复 | 使用匹配版本旧路径，存在经过验证的迁移才进入新路径 |

版本化能力清单列出允许的模式集合。阶段 D 关闭新路径增强及审计后自动修复，完整审计始终执行。用户可显式选择已验证旧运行时处理尚未开放的模式，同一次执行不得混用模型合同。

测试分为模块替换合同、核心结构性质、真实输入黄金样本、恢复和故障注入、性能基准、编译运行时验证。必须覆盖同一模块不同 adapter 的等价能力、模块缺失、unsupported、超时、重复响应、旧 schema、缺失资源、无效 Patch 和源文件变化。

内容守恒验证以源范围及资源映射为依据，允许显式编号替换、批准排除和有来源新增；不能只比较文本总数。规范化幂等以语义及 presentation 等价判断，不要求运行时间戳一致。

构建使用私有源码仓库的既有流程。模块静态导入和清单进入 Nuitka 制品，源码模式与编译模式比较模块 ID、版本和支持能力，并执行真实转换及文件式恢复冒烟。公开 Skill 仍仅消费生成树，禁止包含私有 Python 源文件。现有报告脱敏合同继续执行。

## 13. 两轮评审意见闭环

| 评审问题 | 2.0 落点 | 验收证据 |
| :--- | :--- | :--- |
| 跨语义模块角色冲突 | 第 2、6 节分阶段裁决及统一提交 | 标题/列表、注释/列表冲突夹具 |
| AST 存储与关系不完整 | 第 5 节唯一 children、完整 JSON、单元格迁移 | 图合同及表格嵌套用例 |
| 文件式暂停恢复遗漏 | 第 8 节运行状态、检查点与幂等 | 多阶段恢复及摘要不匹配测试 |
| 修复层级与候选一致性 | 第 9 节源模型修复和产物集合提交 | 规范化后修复保持、回退及崩溃恢复 |
| 可替换性合同不足 | 第 3、4 节能力与状态声明 | adapter 合同及能力缺失测试 |
| 索引与增量失效冲突 | 第 6、11 节按版本重建，增量后置 | 修改后索引与全量结果等价 |
| 模块覆盖及编译遗漏 | 第 4、12 节完整职责表及分发门禁 | 模块清单和编译运行时冒烟 |
| 列表策略与验收不具体 | 第 7 节目标策略和场景断言 | 实际源文件、AST、输出及诊断对照 |
| 重算覆盖语义修复 | 第 6.1、8、9 节修订持久化与重放 | 角色及父归属修复跨重算、跨恢复保持 |
| 输出等价错误判为 no_effect | 第 9 节语义与呈现独立判断 | 外观相同的父项修复仍被接受 |
| CapabilityConfig 读写不兼容 | 第 8.1 节 LegacyView、TargetMap、Patch 翻译 | 旧读取、操作转换、歧义拒绝及历史响应测试 |
| 事务粒度导致平方级开销 | 第 6、11 节阶段批提交与次数门槛 | 列项增长时全量操作次数有固定上界 |
| SourceFacts 合同不完整 | 第 3.1 节公共事实及扩展能力 | 独立 adapter 输出可被同一语义 module 消费 |
| 行内与编号承载缺失 | 第 5.1 节行内序列及 generated_anchor | 行内混排、表格起始、子列表起始及空列项 |
| 新路径过早开放恢复模式 | 第 12 节执行模式门禁 | 缺失门禁预检拒绝，通过后逐模式开放 |

本次交付为方案 3.0 文档，前两版文件保持原样。Python 实现、可执行 schema、迁移器、生产测试夹具及性能结果由实施阶段交付；目标合同与建议性能门槛均需通过相应门禁验证。文中 JSON 的语法及引用检查只证明示例自洽，不表示真实转换已通过验收。
