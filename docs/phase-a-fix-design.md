我查到问题点了：`convert_docx` 已经执行了一次 AST Phase A，随后又调用 `build_role_overrides_from_docx(..., llm_call=llm_call)` 再跑一次 LLM。第二次生成的 overrides 没进入 `report["llm_enhancer"]["applied"]`，所以会出现“报告只有 11 条 applied，但实际样式被大量覆盖”。

当前环境是只读沙箱，我不能直接改文件。建议最小修复如下：

1. 在 [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:37) 移除 `build_role_overrides_from_docx` import。
2. 在 `convert_docx` 中删除第二次 LLM 调用：

```python
role_overrides = build_role_overrides_from_docx(
    src_doc, strict_normalize, llm_call=llm_call
) if llm_call else None
```

改为从 Phase A 已应用决策提取：

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

3. 在 [scripts/main.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/main.py:300) 附近加 helper：

```python
def _extract_role_overrides_from_model(
    model: dict,
    report: dict,
    *,
    applied_start: int = 0,
) -> dict[int, str]:
    changed_roles: dict[str, str] = {}
    applied = report.get("llm_enhancer", {}).get("applied", [])[applied_start:]

    for dec in applied:
        if dec.get("operation") != "retype":
            continue
        to_type = dec.get("to", {}).get("block_type", "")
        from_type = dec.get("from", {}).get("block_type", "")
        if to_type in {"heading", "body", "list_item", "caption"} and to_type != from_type:
            bid = dec.get("block_id", "")
            if bid:
                changed_roles[bid] = to_type

    overrides: dict[int, str] = {}
    idx = 0
    for block in model.get("document", {}).get("blocks", []):
        if block.get("block_type") in {
            "heading", "body", "list_item", "caption",
            "table", "image", "appendix", "unknown",
        }:
            bid = block.get("id")
            if bid in changed_roles:
                overrides[idx] = changed_roles[bid]
            idx += 1

    return overrides
```

4. 同时收紧 [scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:757) 的 Phase A prompt，加上这几句：

```python
"只输出需要修改的 block；与当前 type 一致的 block 必须省略。",
"没有需要修改时返回 decisions: []。",
"不要对代码块、JSON 示例、普通说明句、引导句生成 list_item 或 caption。",
"源文档已有 Heading/Normal/List 样式且上下文没有强证据时保持原角色。",
```

验证情况：`python3 -m pytest` 当前不可用，报 `No module named pytest`。我用不落盘方式检查了现有 `scripts/llm_enhancer.py` 和 `scripts/main.py` 语法，当前代码语法正常。