# TASK-FIX-003：修复 Phase A 双 LLM 调用导致全局覆盖

## 根因

`scripts/main.py` 的 `convert_docx` 函数调了**两次** LLM：
1. `enhance_document_model(phase="A")` — 正确运行 Phase A，decisions 记入 report
2. `build_role_overrides_from_docx(llm_call=llm_call)` — 再次调用 LLM，生成全量段落角色映射，**不记入 report**，直接覆盖所有段落样式

第二次调用产生了 51 处错误（19 heading 变 list、22 body 变 list、4 JSON 变 caption 等）。

## 修复

按照 `docs/phase-a-fix-design.md` 方案：

### 1. main.py：删除第二次 LLM 调用

移除 `build_role_overrides_from_docx` 的 import 和调用，改为从 Phase A 已 applied decisions 提取 role_overrides：

```python
phase_a_role_overrides: dict[int, str] = {}

if should_enhance(report, "A", enhance_mode):
    applied_start = len(report.get("llm_enhancer", {}).get("applied", []))
    source_model = enhance_document_model(
        source_model, report, phase="A",
        llm_call=llm_call, hint=llm_hint,
    )
    phase_a_role_overrides = _extract_role_overrides_from_model(
        source_model, report, applied_start=applied_start,
    )

role_overrides = phase_a_role_overrides or None
```

### 2. main.py：新增 helper

```python
def _extract_role_overrides_from_model(
    model: dict, report: dict, *, applied_start: int = 0,
) -> dict[int, str]:
    """从 Phase A 的 applied decisions 提取段落级别的 role_overrides。
    只提取 retype 操作中与原始类型不同的映射。"""
    ...
```

实现见 `docs/phase-a-fix-design.md:34-66`

### 3. llm_enhancer.py：收紧 Phase A prompt

在 `_build_phase_a_prompt` 中追加约束：

```
"只输出需要修改的 block；与当前 type 一致的 block 必须省略。",
"没有需要修改时返回 decisions: []。",
"不要对代码块、JSON 示例、普通说明句、引导句生成 list_item 或 caption。",
"源文档已有 Heading/Normal/List 样式且上下文没有强证据时保持原角色。",
```

## 验证

```bash
cd scripts && .venv/bin/python3 -m main \
  --input ../input/方案类/生物特征认证系统集成方案.docx \
  --output ../wx_output/bio_test_fix.docx \
  --template ../assets/技术文件格式及书写要求.docx \
  --report ../wx_output/bio_test_fix_report.json \
  --llm-enhance abc

# 然后对比 bio_test_fix.docx 与 bio_test_off.docx 的样式差异
```
