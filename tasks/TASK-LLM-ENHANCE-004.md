# TASK-LLM-ENHANCE-004：Phase 细化 + 效率优化 + 测试

## 状态

READY

## 目标

对 `llm_enhancer.py` 做以下完善：

1. 细化 Phase 的 prompt 模板
2. Phase C 和 C1 合并为一次 LLM 调用
3. 效率优化（缓存、模型分层、增量增强）
4. 编写完整的单元测试

## 输入

- `docs/llm-enhancer-design.md`
- `scripts/llm_enhancer.py`
- `tests/test_pipeline.py`（参考现有测试风格）

## 输出

修改 `scripts/llm_enhancer.py`，新增 `tests/test_llm_enhancer.py`

---

## Part 1: Phase C + C1 合并

当前 Phase C 和 C1 各调用一次 LLM。合并为一次：prompt 同时要求判断表格类型和生成题注文字。

在 `enhance_document_model()` 中，当 `phase="C"` 时自动包含 C1 的能力。删除独立的 `phase="C1"` 路径。

## Part 2: 效率优化

### 2a. 增量增强

Phase A 不扫描所有 section。只送包含以下信号的候选 section：
- `ambiguous_short_paragraphs` 中的段落所在的 section
- `suspect_visual_headings` 中的段落所在的 section
- `inferred_headings` 中的段落所在的 section

新增函数 `_collect_suspicious_sections(model, report)` → 返回 section 列表。

### 2b. Token 预算控制

- 每个段落 text 截断为 200 字符
- 表格只传前 3 行
- 单次 prompt ≤ 12000 字符，超限时采样（优先保留歧义高的 section）

### 2c. 模型分层

在 `_resolve_llm_call` 中增加模型分层：
- `LLM_MODEL_PHASE_A` 环境变量 → Phase A 专用模型（需要更强推理）
- `LLM_MODEL_PHASE_BC` 环境变量 → Phase B/C 专用模型（可以更快/更便宜）
- 未设置时使用 `LLM_MODEL` 或默认值

### 2d. 超时控制

LLM 调用增加 30 秒超时，超时后该 Phase 静默跳过。

---

## Part 3: 完善单元测试

创建 `tests/test_llm_enhancer.py`，覆盖以下场景：

```python
# 基础功能
def test_extract_json_object_basic()
def test_extract_json_object_with_markdown_wrapper()
def test_extract_json_object_empty_string_returns_none()
def test_extract_json_object_invalid_json_returns_none()

# Patch 校验
def test_validate_patch_valid_phase_a()
def test_validate_patch_invalid_schema_version()
def test_validate_patch_unknown_block_id()
def test_validate_patch_disallowed_operation()
def test_validate_patch_empty_decisions_is_valid()

# Patch 应用
def test_apply_retype_body_to_list_item()
def test_apply_retype_body_to_heading()
def test_apply_retype_heading_to_body()
def test_apply_adjust_level()
def test_apply_set_table_type()
def test_apply_set_caption_text()
def test_low_confidence_patch_is_skipped()
def test_unknown_block_id_is_error()

# suspicion_score
def test_suspicion_score_zero_for_clean_document()
def test_suspicion_score_nonzero_for_ambiguous_document()
def test_suspicion_score_range_0_to_1()

# should_enhance
def test_should_enhance_off_returns_false()
def test_should_enhance_abc_returns_true_for_all_phases()
def test_should_enhance_a_returns_true_only_for_phase_a()

# enhance_document_model
def test_enhance_no_llm_call_returns_original()
def test_enhance_unknown_phase_records_error()
def test_enhance_applies_valid_patch()
```

### 测试辅助

```python
# 构造 fake llm_call
def fake_llm(patch_json: str):
    def _call(prompt: str) -> str:
        return patch_json
    return _call

# 构造最小 model
def make_minimal_model(blocks=None):
    from document_model import new_document_model
    model = new_document_model("test.md", "md", "test")
    if blocks:
        model["document"]["blocks"] = blocks
    return model
```

---

## 允许修改

- `scripts/llm_enhancer.py`
- `tests/test_llm_enhancer.py`（新建）

## 禁止修改

- `scripts/main.py`
- `scripts/list_semantic_enhancer.py`
- 所有其他现有 .py 文件

## 验证命令

```bash
cd /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts

# 新测试
.venv/bin/python3 -m pytest ../tests/test_llm_enhancer.py -v

# 全部测试（新+旧）
.venv/bin/python3 -m pytest ../tests/ -q
```