# 新路径重复判定迁移记录

| 旧位置 | 新所有者 | 当前状态 | 保留原因 |
| :--- | :--- | :--- | :--- |
| `front_matter.py` 的范围判定 | `wxdf.semantics.regions` | SourceRegion 统一产出前置范围、规范标题与排除位置，Adapter 在 DOCX 解析前消费 | 旧检测函数作为 SourceRegion 内部 Adapter 和 legacy 默认路径回退 |
| `toc_detector.py` 的目录范围判定 | `wxdf.semantics.regions` | 带封面和手工目录的 DOCX 端到端排除 6 个源位置，目录替换和结构等价通过 | 目录检测实现仍保留给 SourceRegion 内部 Adapter、LLM 候选确认和 legacy 回退 |
| `note_semantics.py` 的注释角色判定 | `wxdf.semantics.protected` 与 `wxdf.semantics.protected_bridge` | 原生 Provider 已进入 ExecutionPlan，真实 Markdown 442 个、真实 DOCX 9 个文档级候选完整消费 | 旧函数暂作为共享文本合同、审计入口和 legacy 回退，表内注释归 Table Provider 管理 |
| `list_detector.py`、`list_group_detection.py` 与 `list_hierarchy.py` 的列表判定 | `wxdf.semantics.lists` 与 `wxdf.semantics.list_bridge` | SourceFacts 仅对 detected 编号声明列表语义；真实 DOCX 27 个列表项完整消费，AST 与输出均为 27 项 | 旧检测器暂作为 DOCX 编号证据 Adapter 和 legacy 回退，旧层级函数消费明确的原生裁决后不再重判 |
| `table_semantics.py` 与表格规范化中的类型判定 | `wxdf.semantics.tables` 与 `wxdf.semantics.table_bridge` | 原生 Provider 已进入 ExecutionPlan；真实 DOCX 4 张表全部消费，2 张 data 与 2 张 layout 的专项审计通过 | 旧函数暂作为兼容消费、共享格式合同和 legacy 回退 |
| `caption_placement.py` 与题注规范化中的关联判定 | `wxdf.semantics.captions` 与 `wxdf.semantics.caption_bridge` | 原生 Provider 已进入 ExecutionPlan；真实 DOCX 5 个题注全部关联，模型和保存后位置审计通过 | 旧函数消费明确目标关系并保留 legacy 回退 |
| `appendix_semantics.py` 的附录候选与范围重分类 | `wxdf.semantics.appendices` 与 `wxdf.semantics.appendix_bridge` | 原生 Provider 已进入 ExecutionPlan；真实 DOCX 1 个有效范围、1 个负例和 3 个范围内标题裁决全部消费 | 旧函数暂作为候选 Adapter、兼容消费和 legacy 回退 |
| `model_normalization.py` 的启发式角色修补 | `wxdf.semantics.structure` | 原生候选已在规范化前注入旧 Source Model，Markdown 19 个和真实 DOCX 52 个顶层决策全部消费，结构等价通过 | legacy 默认路径仍保留原逻辑作为回退，Adapter 已移除 `document_structure` 能力声明 |
| `docx_pipeline.py` 与 `md_pipeline.py` 的源模型构建 | `wxdf.semantics.source_projection` 与 `wxdf.semantics.source_projection_bridge` | 输出链已消费原生投影；真实 Markdown 和 DOCX 完整消费，四域等价，SourceParser 为 `native_consumed` | 旧 parser 暂时提供语义骨架与 legacy 回退；重复实现删除需要单独测试和默认切换门禁 |

新模块只提交候选并保留源引用。渲染层不承担新的范围或保护角色重判。
