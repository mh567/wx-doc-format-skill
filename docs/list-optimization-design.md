当前环境只读，`apply_patch` 已被拒绝，所以我没法直接写入文件。定位到应改点：

1. [scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:829) 的 `_build_phase_a_prompt`
2. [tests/test_llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/tests/test_llm_enhancer.py:1279) 的 Phase A prompt 单测

建议改法已经明确：

1. 在 `_build_phase_a_prompt` 中遍历完整 `model["document"]["blocks"]`，维护 `section_by_id`，给每个 block 展示最近 heading 文本。
2. 从 `block["source"]` 展示 `source_style` 和 `source_role`，DOCX 解析已在 [scripts/docx_pipeline.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/docx_pipeline.py:172) 写入 `style` 和 `inferred_role`。
3. Prompt 规则补充：
   `Heading` 源样式保持 heading。
   `XX：长说明` 按说明体保持 body。
   超过 100 字且无列表标记保持 body。
   只有连续短句、平行结构、常见列表标记才转 `list_item`。

核心展示行应改成类似：

```python
f"  [{bid}] section={section} source_style={source_style}"
f" source_role={source_role} type={btype} role={role} level={level}"
f" list={list_type} cap={cap_type} | {text}"
```

并追加这些 prompt 约束：

```python
"源 DOCX 样式为 Heading 的 block 必须保持 heading，除非它明显是封面日期、版本号等元信息。",
"源 DOCX 样式为 List 或带编号信息的短句可优先判断为列表；源样式为 Normal 的长说明段落应优先保持 body。",
"说明体保持 body：形如“术语：说明文字”“属性名：说明文字”“原则名：长说明”的段落按正文处理。",
"长段落保持 body：超过 100 字且没有 a) 1) 1. • 等列表标记时，不要转为 list_item。",
"列表体才转 list_item：连续短句、平行结构、无冒号前缀，通常以 a) 1) 1. • 或短横线开头。",
```

我也验证了当前测试环境：`python3 -m pytest tests/test_llm_enhancer.py -q` 失败，原因是 `No module named pytest`。解除只读限制后，建议补 4 个单测：检查 prompt 包含 `section=接口设计原则`、`source_style=Normal`、`blocks_override` 仍能显示前置章节、说明体和长段落保护规则存在。