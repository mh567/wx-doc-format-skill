建议把改动集中在 [scripts/llm_enhancer.py](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:1009)。现状硬编码点主要是 [A/B 分支](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:1077) 和 [常量注册](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:24)。

**核心设计**

引入 `PhaseDescriptor`，把 Phase 差异变成数据和少量 hook：

```python
from dataclasses import dataclass
from typing import Callable, Literal, Any

BatchStrategy = Literal["single", "by_targets", "by_sections"]

@dataclass(frozen=True)
class PhaseDescriptor:
    name: str
    allowed_ops: set[str]
    prompt_builder: Callable[..., str]
    collector: Callable[[dict, dict], list[Any]]
    batching: BatchStrategy
    batch_size: int | None = None
    empty_status: str = "no_targets"
    prevalidate: bool = False
    prompt_preview_text: str | None = None
    after_phase: Callable[[dict, dict, dict], None] | None = None
```

全局注册表：

```python
PHASE_REGISTRY: dict[str, PhaseDescriptor] = {}

def register_phase(desc: PhaseDescriptor) -> PhaseDescriptor:
    if desc.name in PHASE_REGISTRY:
        raise ValueError(f"Duplicate phase {desc.name!r}")
    PHASE_REGISTRY[desc.name] = desc
    return desc
```

为了兼容现有测试和外部 import，保留旧常量名，但从注册表派生：

```python
def _refresh_phase_exports() -> None:
    global PHASE_NAMES, ALLOWED_OPS_BY_PHASE, _BUILD_PROMPT
    PHASE_NAMES = frozenset(PHASE_REGISTRY)
    ALLOWED_OPS_BY_PHASE = {
        name: desc.allowed_ops for name, desc in PHASE_REGISTRY.items()
    }
    _BUILD_PROMPT = {
        name: desc.prompt_builder for name, desc in PHASE_REGISTRY.items()
    }
```

**A/B 注册方式**

Phase A 的 collector 负责复用现有 `_collect_suspicious_sections`，并转换成 prompt 所需的 `blocks_override`：

```python
def _collect_phase_a_batches(model: dict, report: dict) -> list[dict]:
    sections = _collect_suspicious_sections(model, report)
    filtered_blocks = []
    for sec in sections:
        filtered_blocks.extend(sec.get("blocks", []))
    blocks_override = filtered_blocks if len(filtered_blocks) >= 3 else None
    return [{"blocks_override": blocks_override}]
```

Phase B 直接收集 targets：

```python
def _collect_phase_b_batches(model: dict, report: dict) -> list[dict]:
    return [{"targets": t} for t in _collect_caption_targets(model)]
```

注册：

```python
register_phase(PhaseDescriptor(
    name="A",
    allowed_ops={"retype"},
    prompt_builder=_build_phase_a_prompt,
    collector=_collect_phase_a_batches,
    batching="single",
    after_phase=_record_phase_a_applied_rate,
))

register_phase(PhaseDescriptor(
    name="B",
    allowed_ops={"set_caption_text"},
    prompt_builder=_build_phase_b_prompt,
    collector=_collect_phase_b_batches,
    batching="by_targets",
    batch_size=PHASE_B_CAPTION_BATCH_SIZE,
    empty_status="no_targets",
    prevalidate=True,
    prompt_preview_text="(no caption targets)",
))
_refresh_phase_exports()
```

**统一调度**

`enhance_document_model` 只保留公共流程：

1. sanitize hint
2. 查 `PHASE_REGISTRY[phase]`
3. collector 收集 items
4. strategy 生成 batches
5. build prompt
6. call `llm_call`
7. extract JSON
8. prevalidate 可选
9. validate
10. apply
11. metrics
12. summary 和 after_phase hook

伪代码结构：

```python
desc = PHASE_REGISTRY.get(phase)
if desc is None:
    record unknown phase
    return model

items = desc.collector(model, report)
batches = _make_phase_batches(desc, items)

if not batches:
    record prompt preview
    record no_targets metric
    _append_phase_summary(enh, phase)
    return model

for batch_idx, batch in enumerate(batches):
    prompt = _build_prompt_for_batch(desc, model, hint, batch, batch_idx, len(batches))
    raw = timed_call(prompt)
    patch = extract_json_object(raw)

    if desc.prevalidate:
        pre_errors = _prevalidate_patch_schema(patch, phase, desc.allowed_ops)

    validation_errors = validate_patch(patch, model, desc.allowed_ops)
    apply_patch_to_model(model, patch, report)
    record metric

_append_phase_summary(enh, phase)
if desc.after_phase:
    desc.after_phase(model, report, enh)
return model
```

**Batching 抽象**

建议单独做一个 `_make_phase_batches`，新增 `by_sections` 时只扩展这里：

```python
def _make_phase_batches(desc: PhaseDescriptor, items: list[Any]) -> list[list[Any]]:
    if desc.batching == "single":
        return [items]
    if desc.batching in {"by_targets", "by_sections"}:
        if not items:
            return []
        return _iter_batches(items, desc.batch_size or len(items))
    raise ValueError(f"Unknown batching strategy {desc.batching!r}")
```

prompt 参数适配放进一个小函数，避免把差异塞回主流程：

```python
def _build_prompt_for_batch(desc, model, hint, batch, batch_idx, batch_count):
    if desc.name == "A":
        payload = batch[0] if batch else {}
        return desc.prompt_builder(
            model, hint=hint,
            blocks_override=payload.get("blocks_override"),
        )

    if desc.batching == "by_targets":
        targets = [item["targets"] for item in batch]
        return desc.prompt_builder(
            model,
            hint=hint,
            targets_override=targets,
            batch_meta={"batch_index": batch_idx, "batch_count": batch_count},
        )

    return desc.prompt_builder(model, hint=hint, batch_items=batch)
```

后续要做到完全无 phase name 判断，可以把 `prompt_args_builder` 也放进 descriptor。第一步迁移保留这个小适配函数，风险更低。

**兼容性要点**

现有行为保持不变的关键：

- Phase A 仍然单次调用，仍然使用 `_collect_suspicious_sections` 和 `blocks_override`。
- Phase B 仍然按 `PHASE_B_CAPTION_BATCH_SIZE = 15` 分批，空 targets 时不调用 LLM。
- `llm_call` 仍然是 `Callable[[str], str]`。
- `validate_patch`、`apply_patch_to_model`、`extract_json_object` 复用现有实现。
- `ALLOWED_OPS_BY_PHASE`、`PHASE_NAMES`、`_BUILD_PROMPT` 保留导出，避免测试和外部调用破坏。
- Phase A 的 `phase_a_applied_rate` 放进 `after_phase` hook，保持 [should_enhance](/Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/llm_enhancer.py:1394) 的现有 gate 行为。

**测试建议**

补三类回归：

1. 注册表测试：`PHASE_REGISTRY["A"]` 和 `["B"]` 存在，旧常量与 registry 一致。
2. 行为不变测试：复用现有 Phase A 应用 patch、Phase B 多批、Phase B 空 targets 不调用 LLM。
3. 扩展性测试：临时注册一个 fake Phase `T`，使用 `single` strategy，确认 `enhance_document_model` 不需要新增 `if phase == "T"` 也能调用 prompt、校验 allowed ops、应用 patch。