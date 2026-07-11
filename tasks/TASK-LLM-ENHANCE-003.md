# TASK-LLM-ENHANCE-003：触发策略 + main.py 集成

## 状态

READY

## 目标

将 `llm_enhancer.py` 的触发策略和所有 Phase 集成到 `main.py` 中，替换旧的 `WX_DOC_LLM_ENHANCE` 逻辑。

## 输入

- `docs/llm-enhancer-design.md`：完整设计方案（主要看"main.py 集成方案"章节）
- `scripts/llm_enhancer.py`：已完成的核心模块
- `scripts/main.py`：当前 CLI 入口

## 输出

修改 `scripts/main.py`

## 实现要求

### 1. 新增 import

```python
from llm_enhancer import (
    enhance_document_model,
    build_role_overrides_from_docx,
    compute_suspicion_score,
    should_enhance,
)
```

### 2. 新增 CLI 参数

```python
parser.add_argument(
    "--llm-enhance",
    choices=["off", "auto", "a", "ab", "abc", "force-a", "force-ab", "force-abc"],
    default="off",
    help="LLM semantic enhancement level (default: off)"
)
parser.add_argument(
    "--llm-hint",
    type=str,
    default=None,
    help="Natural-language hint injected into LLM enhancement prompts"
)
```

### 3. 保留并改造 auto_llm_call

将 `convert_docx()` 中内联的 `_auto_llm` 提取为模块级函数 `_resolve_llm_call(args)`，保持原有的 Anthropic → OpenAI → codex CLI 探测逻辑。

### 4. 修改 convert_docx()

在源文档解析后、规范化前插入 Phase A，规范化后插入 Phase B+C，替换旧的 `WX_DOC_LLM_ENHANCE` 逻辑：

```python
def convert_docx(src, dst_doc, row_height_cm, row_height_rule,
                 strict_normalize, report, numbering_ids, args):
    # ... existing parse logic ...

    summarize_source_document_model(report, source_model)

    # ── LLM Enhancement ──
    enhance_mode = args.llm_enhance
    llm_call = _resolve_llm_call(args) if enhance_mode != "off" else None

    # Phase A: block role review (before normalization)
    if should_enhance(report, "A", enhance_mode):
        source_model = enhance_document_model(
            source_model, report, phase="A",
            llm_call=llm_call, hint=args.llm_hint,
        )

    model = normalize_document_model(source_model, report)

    # Phase B + C: structure and table enhancement (after normalization)
    if should_enhance(report, "B", enhance_mode):
        model = enhance_document_model(
            model, report, phase="B",
            llm_call=llm_call, hint=args.llm_hint,
        )
    if should_enhance(report, "C", enhance_mode):
        model = enhance_document_model(
            model, report, phase="C",
            llm_call=llm_call, hint=args.llm_hint,
        )
    if should_enhance(report, "C1", enhance_mode):
        model = enhance_document_model(
            model, report, phase="C1",
            llm_call=llm_call, hint=args.llm_hint,
        )

    # role_overrides for direct rendering path
    role_overrides = build_role_overrides_from_docx(
        src_doc, strict_normalize, llm_call=llm_call
    ) if llm_call else None

    render_docx_direct(
        src_doc, dst_doc, report, row_height_cm, row_height_rule,
        numbering_ids,
        template_profile=report.get("template_profile"),
        strict_normalize=strict_normalize,
        role_overrides=role_overrides,
    )
    return {"source": source_model, "normalized": model}
```

### 5. 修改 convert_md()

同样加入 LLM 增强（Phase A 在 `parse_md_to_model` 之后、`normalize_document_model` 之前，Phase B/C 在规范化之后）。MD 路径没有 `role_overrides`，跳过即可。

### 6. 修改函数签名

`convert_docx()` 和 `convert_md()` 需要接收额外的 `args` 参数（或 `enhance_mode` / `llm_call` / `llm_hint`）。

### 7. 修改 main() 调用

将 `convert_docx()` 和 `convert_md()` 的调用处传入 `args`。

### 8. 删除旧代码

删除 `convert_docx()` 中断掉的 `WX_DOC_LLM_ENHANCE` 逻辑块（原第 155-207 行附近的大段 if 块）。

## 允许修改

- `scripts/main.py`

## 禁止修改

- `scripts/llm_enhancer.py`
- `scripts/list_semantic_enhancer.py`
- `scripts/document_model.py`
- 所有其他现有 .py 文件

## 验收标准

- AC-01: `python3 -m main --help` 显示 `--llm-enhance` 和 `--llm-hint` 参数
- AC-02: `--llm-enhance off`（默认）不触发 LLM 调用
- AC-03: `--llm-enhance auto` 在低 suspicion 文档上跳过 LLM
- AC-04: `--llm-enhance abc` 强制运行所有 Phase
- AC-05: 不带 `--llm-enhance` 时行为与以前完全一致（向后兼容）
- AC-06: `WX_DOC_LLM_ENHANCE=1` 环境变量继续工作（等价于 `abc`）
- AC-07: Phase A 在规范化之前、Phase B/C/C1 在规范化之后执行
- AC-08: 所有原有功能不受影响

## 验证命令

```bash
cd /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts

# Help 包含新参数
.venv/bin/python3 -m main --help | grep -E "llm-enhance|llm-hint"

# 现有测试不受影响
.venv/bin/python3 -m pytest ../tests/ -q

# 默认模式下 import 无误
.venv/bin/python3 -c "from main import main; print('main imports OK')"
```