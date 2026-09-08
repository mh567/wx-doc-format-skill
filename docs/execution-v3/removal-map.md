# 新路径重复判定迁移记录

| 旧位置 | 新所有者 | 当前状态 | 保留原因 |
| :--- | :--- | :--- | :--- |
| `front_matter.py` 的范围判定 | `wxdf.semantics.regions` | SourceRegion 统一产出前置范围、规范标题与排除位置，Adapter 在 DOCX 解析前消费 | 旧检测函数作为 SourceRegion 内部 Adapter 和 legacy 默认路径回退 |
| `toc_detector.py` 的目录范围判定 | `wxdf.semantics.regions` | 带封面和手工目录的 DOCX 端到端排除 6 个源位置，目录替换和结构等价通过 | 目录检测实现仍保留给 SourceRegion 内部 Adapter、LLM 候选确认和 legacy 回退 |
| `note_semantics.py` 的注释角色判定 | `wxdf.semantics.protected` | 新路径已消费 SourceFacts | 旧模型审计和样式检查继续保留 |
| `appendix_semantics.py` 的附录候选 | `wxdf.semantics.compat` | 正式迁移待 T14 | 附录结构和题注暂未进入本任务范围 |
| `model_normalization.py` 的启发式角色修补 | `wxdf.semantics.structure` | 原生候选已在规范化前注入旧 Source Model，Markdown 19 个和真实 DOCX 52 个顶层决策全部消费，结构等价通过 | legacy 默认路径仍保留原逻辑作为回退，Adapter 已移除 `document_structure` 能力声明 |
| `docx_pipeline.py` 与 `md_pipeline.py` 的源模型构建 | `wxdf.parsers` 及待实现的 SourceFacts 输出 Adapter | 已进入 ExecutionPlan 并建立独立解析覆盖审计；真实 DOCX 为 `shadow_mismatch` | 文本结构变换和列表数量尚未等价，旧 parser 继续拥有输出语义 |

新模块只提交候选并保留源引用。渲染层不承担新的范围或保护角色重判。
