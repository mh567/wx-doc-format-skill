未能写入 `reviews/LLM-ENHANCE-review.md`：当前环境是只读沙箱，`apply_patch` 被拒绝。审查报告内容如下。

**审查结论：BLOCKER**

**逐项审查证据**

代码质量：基础函数有实现，但 DOCX 兼容包装存在全局索引错误，Phase A 增量过滤遗漏 heading block。

架构合理性：MD 路径接入较干净。DOCX 路径增强了 AST 后，实际仍用 direct render，Phase B/C 结果不会进入输出文档。

安全性：`--llm-hint` 直接拼接进 prompt，缺少长度限制、隔离封装和审计字段。

性能：有字符截断和表格 preview，但 auto 模式缺少设计中的 Phase A 修改率门控，会扩大 B/C 调用范围。

测试覆盖：48 个测试覆盖基础函数，缺少 main 集成、DOCX 输出、全局索引、prompt 注入、auto 门控等关键测试。

向后兼容：默认 off 基本保留原路径。启用 `WX_DOC_LLM_ENHANCE=1` 后，DOCX report 可能显示增强已应用，但输出未体现 Phase B/C。

设计一致性：实现合并了 C/C1，但 `main.py` 仍保留 C1 调用分支；设计文档中的 auto 跳过规则、修改率反馈、reason 枚举约束未实现。

**发现的问题**

LLM-ENHANCE-001  
严重等级：BLOCKER  
位置：[scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:261)  
描述：DOCX 路径执行 Phase A/B/C 后，实际渲染仍调用 `render_docx_direct(src_doc, ...)`。增强后的 `model` 没有参与输出，Phase B 的标题层级、列表 restart，以及 Phase C 的表格和题注修改不会反映到生成文档。  
修复建议：DOCX 增强路径统一使用增强后的 normalized AST 渲染，或扩展 `render_docx_direct()` 的覆盖能力。短期可限制 DOCX 只运行可落地的 Phase A 覆盖项，并在 report 标记未落地 phase。

LLM-ENHANCE-002  
严重等级：MAJOR  
位置：[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:1032)，[scripts/docx_render.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/docx_render.py:316)  
描述：`build_role_overrides_from_docx()` 保存了 `global_index`，应用时却用 section 内 `idx_int` 作为 overrides key。`render_docx_direct()` 查询全局 `_para_idx`，跨 section 或含表格时会把 LLM 修正应用到错误段落。  
修复建议：`paras` 保留 `global_index`，写入 `overrides[paras[idx_int]["global_index"]] = new_role`。补充跨 section、含表格、含空段落测试。

LLM-ENHANCE-003  
严重等级：MAJOR  
位置：[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:157)，[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:800)  
描述：Phase A 按 heading 分组时把 heading block 只保存为 `heading_text`，没有放入 prompt 的 block 列表。可疑 heading 无法被 LLM 返回 patch，削弱封面元信息降级和标题纠错。  
修复建议：section 保留 heading block 自身，疑似 heading 或相邻 heading 场景把 heading block id 放进 prompt。增加 prompt 包含目标 heading id 的测试。

LLM-ENHANCE-004  
严重等级：MAJOR  
位置：[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:926)，[docs/llm-enhancer-design.md](/Users/harris/Documents/zero项目/wx-doc-format-skill/docs/llm-enhancer-design.md:248)  
描述：设计要求 auto 模式根据 Phase A 修改率决定是否继续 B/C，并有短文档、标准 Heading、缺模板、无 LLM 后端等跳过条件。实现对所有 phase 只看同一个 suspicion score。  
修复建议：实现 phase-aware gate。A 用 suspicion score，B/C 用 Phase A applied rate 和文档条件。无 llm_call 时在 `main.py` 直接跳过。

LLM-ENHANCE-005  
严重等级：MAJOR  
位置：[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:733)  
描述：`--llm-hint` 未限制长度，未过滤控制字符，未隔离为数据块，可能诱导模型对大量 block 提交高置信修改。  
修复建议：限制长度，清理控制字符，使用明确 quoted data block，声明 hint 不能覆盖 schema 和操作限制。report 记录 sanitized_hint 与 truncated 标记。

LLM-ENHANCE-006  
严重等级：MINOR  
位置：[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:689)  
描述：`set_caption_text` 可作用于任意 caption，未校验 `_auto_generated`、原文本为空、关联 table 类型。与设计中只填充自动空题注的约束不一致。  
修复建议：为 `set_caption_text` 增加 operation-specific 校验，限制只作用于 `_auto_generated` 且空文本 caption，layout/code_sample 关联表不生成题注。

LLM-ENHANCE-007  
严重等级：MINOR  
位置：[tests/test_llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/tests/test_llm_enhancer.py:547)  
描述：测试缺少能抓住上述问题的断言。`_collect_suspicious_sections` 只检查长度，auto 只测 A，未覆盖 main 和 renderer。  
修复建议：增加 DOCX direct render 集成测试、prompt block id 断言、auto B/C 门控测试、默认 off 零调用测试、Phase C 输出题注生效测试。

验证情况：尝试运行 `PYTHONDONTWRITEBYTECODE=1 scripts/.venv/bin/python3 -m pytest tests/test_llm_enhancer.py -q -p no:cacheprovider`，pytest 初始化失败，原因是只读环境没有可用临时目录。