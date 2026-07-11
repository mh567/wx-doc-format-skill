当前环境是只读沙箱，无法实际创建 `reviews/TASK-FIX-001-review.md`。审查内容如下：

**Overall Result**

FAIL

6 项修复中，F1 基本正确，F6 基本覆盖。F2 和 F4 存在阻断问题，会导致 auto 模式异常或修复在主流程中不生效。F3、F5 属于部分完成。

**逐项检查**

F1 benchmark per-phase 统计: PASS  
[scripts/benchmark.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/benchmark.py:199) 已直接读取 `phase_summaries[].applied_count`，符合任务要求。当前逻辑与 `llm_enhancer.py` 中 per-phase summary 的差分记录方式一致。

F2 MD 路径输出 parse_report: FAIL  
[scripts/md_pipeline.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/md_pipeline.py:100) 开始写入 `report["parse_report"]`，但字段 `ambiguous_short_paragraphs`、`inferred_headings`、`inferred_lists` 是 list。[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:1003) 的 `compute_suspicion_score()` 直接把这些值参与除法，传入 list 会触发 `TypeError`，auto 模式会崩。另一个问题是 [scripts/benchmark.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/benchmark.py:172) 仍从 report 顶层取信号，没有读 `report["parse_report"]`，MD 的 ambiguous 和 unstyled 指标会被算成 0。

F3 封面元信息识别增强: PARTIAL  
[scripts/text_utils.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/text_utils.py:234) 增加了版本、文档编号、文件编号、密级、状态、作者、日期等前缀，方向正确。[scripts/text_utils.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/text_utils.py:290) 的视觉标题排除也覆盖了大部分前缀。但任务要求的“封面首几个 block 中连续居中短文本”没有实现，因为当前函数没有上下文窗口，只能单段判断。另外 `is_front_matter_text()` 包含 `修订记录`，`looks_like_visual_heading()` 未包含，覆盖不完全一致。

F4 连续短段落推断列表: FAIL  
[scripts/model_normalization.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/model_normalization.py:181) 的逻辑只在 `strict_normalize=True` 时启用，但主流程 [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:213) 和 [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:256) 调用 `normalize_document_model()` 时没有传 `strict_normalize`，所以实际转换路径不会触发该修复。即使触发，[scripts/model_normalization.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/model_normalization.py:213) 把所有新列表项的 `restart` 都设为 `False`，且这段代码位于原有列表重启规范化之后，首项不会被修正为重启，可能破坏列表编号不变量。

F5 LLM 超时动态调整: PARTIAL  
[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:109) 实现了基于 block 数的动态 timeout，并在 [scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:112) 写入报告。问题是使用 `setdefault` 只记录第一次调用的 timeout，Phase B/C 若需要单独诊断不会被覆盖或追加。`int(block_count / 20)` 也会向下取整，和任务里的公式略有偏差，但影响较小。

F6 XML tag 兼容性: PASS  
[scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:1120) 和 [scripts/template_finalizer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/template_finalizer.py:381) 使用 `endswith(("}...","}..."))` 兼容 Clark notation 的 `}p`、`}tbl`、`}sectPr`。对 python-docx 直接创建和 save/load 后的常见 tag 形式可用。未发现新增风格问题。