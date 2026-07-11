当前环境是只读，我无法直接创建 `docs/optimization-plan.md`。下面是建议写入该文件的内容。

```markdown
# 优化项实现规划

## 当前状态

已确认 `scripts/benchmark.py` 当前版本已经在 `collect_metrics()` 中写入 `wall_time_sec` 和 `output_size_kb`，并在 `run_benchmark()` 中补充 `input_size_kb`。第 4 项重点调整为补齐错误场景字段、同步 `run_bench_standalone.py`，并在报告中明确字段已存在。

## 1. Phase B 按章节分片调用

修改点：

1. 在 `scripts/llm_enhancer.py` 增加 `PHASE_B_SECTION_BATCH_SIZE = 20`。
2. 新增 `_collect_phase_b_sections(model)`，按标题切章节。封面或标题前内容作为第 0 个 section。
3. 新增 `_iter_batches(items, size)`。
4. 修改 `_build_phase_b_prompt()`，支持 `sections_override` 和 `batch_meta`。
5. 在 `enhance_document_model()` 中对 Phase B 走批处理，每批调用一次 LLM，逐批校验并应用。
6. `phase_summaries` 对同一 Phase 聚合统计，同时在 `phase_metrics` 记录每个 batch。

代码 diff 预览：

```diff
+import math
+import time
+
+PHASE_B_SECTION_BATCH_SIZE = 20
+PHASE_C_TABLE_BATCH_SIZE = 10
+
+def _iter_batches(items: list[Any], size: int) -> list[list[Any]]:
+    return [items[i:i + size] for i in range(0, len(items), size)]
+
+def _collect_phase_b_sections(model: dict) -> list[dict]:
+    sections = []
+    current = {"heading_text": "", "blocks": []}
+    for b in model.get("document", {}).get("blocks", []):
+        if b.get("block_type") == "heading" and current["blocks"]:
+            sections.append(current)
+            current = {"heading_text": b.get("text", ""), "blocks": [b]}
+        elif b.get("block_type") in ("heading", "list_item"):
+            current["blocks"].append(b)
+    if current["blocks"]:
+        sections.append(current)
+    return sections
```

风险点：

1. 分片后跨章节层级判断可能丢失上下文。每批 prompt 需要带前序标题路径摘要。
2. 批次之间连续列表的 `set_restart` 可能误判。Phase B prompt 应强调只调整本批出现的 block。
3. 逐批应用会让后续批次看到已修改模型。建议这是可接受行为，并在 `phase_metrics` 中记录 batch 顺序。

## 2. Phase C 按表格批次调用和 JSON schema 预校验

修改点：

1. 新增 `PHASE_C_TABLE_BATCH_SIZE = 10`。
2. 新增 `_collect_phase_c_table_groups(model)`，每个 group 包含表格、邻近 caption、最近 heading、自动生成空 caption。
3. 修改 `_build_phase_c_prompt()`，支持 `groups_override`。
4. 新增 `_prevalidate_patch_schema(patch, phase, allowed_ops)`，在 `validate_patch()` 前执行结构校验。
5. Phase C 每批最多 10 个 table group，合并方式为逐批应用 patch。

代码 diff 预览：

```diff
+def _collect_phase_c_table_groups(model: dict) -> list[dict]:
+    blocks = model.get("document", {}).get("blocks", [])
+    groups = []
+    current_heading = None
+    for i, b in enumerate(blocks):
+        if b.get("block_type") == "heading":
+            current_heading = b
+        if b.get("block_type") == "table":
+            related = []
+            if current_heading:
+                related.append(current_heading)
+            if i > 0 and blocks[i - 1].get("block_type") == "caption":
+                related.append(blocks[i - 1])
+            related.append(b)
+            if i + 1 < len(blocks) and blocks[i + 1].get("block_type") == "caption":
+                related.append(blocks[i + 1])
+            groups.append({"table_id": b.get("id"), "blocks": related})
+    return groups
+
+def _prevalidate_patch_schema(patch: dict, phase: str, allowed_ops: set[str]) -> list[dict]:
+    errors = []
+    if not isinstance(patch, dict):
+        return [{"field": "$", "message": "patch must be object"}]
+    if patch.get("schema_version") != PATCH_SCHEMA_VERSION:
+        errors.append({"field": "schema_version", "message": "invalid schema version"})
+    if patch.get("phase") != phase:
+        errors.append({"field": "phase", "message": "phase mismatch"})
+    decisions = patch.get("decisions")
+    if not isinstance(decisions, list):
+        errors.append({"field": "decisions", "message": "must be list"})
+        return errors
+    for idx, dec in enumerate(decisions):
+        if not isinstance(dec, dict):
+            errors.append({"decision_index": idx, "message": "decision must be object"})
+            continue
+        if dec.get("operation") not in allowed_ops:
+            errors.append({"decision_index": idx, "message": "operation not allowed"})
+        if not isinstance(dec.get("to", {}), dict):
+            errors.append({"decision_index": idx, "message": "to must be object"})
+    return errors
```

风险点：

1. 表格和题注跨 batch 时可能分离。必须按 table group 切分，不能只切 table block。
2. API 示例表应继续避免自动题注。既有 `validate_patch()` 中 `layout/code_sample` 限制要保留。
3. 严格预校验可能拒绝旧模型返回。建议只拒绝结构错误，字段缺失交给现有 `validate_patch()` 输出详细原因。

## 3. per-phase 耗时和 token 估算记录

修改点：

1. 在 `enhance_document_model()` 内记录每次 LLM 调用的 wall time。
2. 在 `report["llm_enhancer"]["phase_metrics"]` 追加每批信息。
3. 字段包括：`phase`、`batch_index`、`batch_count`、`prompt_chars`、`estimated_tokens`、`wall_time_sec`、`status`、`applied_count`、`skipped_count`、`error_count`。
4. `estimated_tokens` 使用 `math.ceil(prompt_chars / 4)`，作为粗略估算。

代码 diff 预览：

```diff
+def _record_phase_metric(enh: dict, metric: dict) -> None:
+    enh.setdefault("phase_metrics", []).append(metric)
+
+start = time.perf_counter()
+prompt_chars = len(prompt)
+try:
+    raw = timed_call(prompt)
+    call_status = "ok"
+except Exception as exc:
+    call_status = "error"
+    raw = ""
+    enh["errors"].append({"phase": phase, "message": f"LLM call failed: {exc}"})
+finally:
+    _record_phase_metric(enh, {
+        "phase": phase,
+        "batch_index": batch_index,
+        "batch_count": batch_count,
+        "prompt_chars": prompt_chars,
+        "estimated_tokens": math.ceil(prompt_chars / 4),
+        "wall_time_sec": round(time.perf_counter() - start, 3),
+        "status": call_status,
+    })
```

风险点：

1. 当前 `phase_summaries` 只在成功应用后写入。实现时需要保证失败批次也有 `phase_metrics`。
2. `_prev_applied` 这种累计差分方式在多 batch 下容易误算。建议封装一个 `_append_phase_summary()`，按单次调用前后的计数差计算。
3. `estimated_tokens` 只是字符估算，字段名保持明确，避免被误认为真实 API token。

## 4. benchmark.py 补充 wall_time 和 output_size

当前主脚本已具备：

1. `collect_metrics()` 写入 `wall_time_sec`。
2. 输出文件存在时写入 `output_size_kb`。
3. `run_benchmark()` 写入 `input_size_kb`。

建议修改点：

1. 错误场景也补充 `output_size_kb`，不存在时写 `0.0`。
2. `llm_enhancement` 增加 `phase_metrics` 摘要，便于分析分片效果。
3. 同步修正 `scripts/run_bench_standalone.py` 中 Phase B/C 统计减差逻辑，当前会对已经是单 Phase 计数的 `phase_summaries` 再次减差。

代码 diff 预览：

```diff
 if not report or report.get("_benchmark_error"):
-    return {
+    metrics = {
         "status": "error",
         "message": report.get("_benchmark_error", "Empty report"),
         "wall_time_sec": round(wall_time_sec, 3),
     }
+    metrics["output_size_kb"] = (
+        round(output_path.stat().st_size / 1024, 1) if output_path.exists() else 0.0
+    )
+    return metrics
```

## 实现顺序

1. 先加通用 batch、计时、phase metric 辅助函数。
2. 实现 Phase B 分章节批处理，保持单批行为与当前输出一致。
3. 实现 Phase C table group 批处理，再加入 schema 预校验。
4. 更新 benchmark 指标聚合和 standalone 脚本。
5. 增加单元测试：Phase B 超过 20 个 section 时调用多次；Phase C 超过 10 个 table group 时调用多次；schema 预校验拒绝错误结构；`phase_metrics` 在成功和失败时均存在。
6. 运行 `.venv/bin/python3 -m pytest tests/ -q`，再运行小样本文档 benchmark 验证 JSON 字段。
```