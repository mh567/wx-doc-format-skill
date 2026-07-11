未能写入 `reviews/pluggable-review.md`：当前沙箱是只读，`apply_patch` 被拒绝。以下是审查内容，可直接作为该文件内容。

```md
# scripts/llm_enhancer.py pluggable refactor review

## Findings

### High: Phase A accepts cross-phase patches

`enhance_document_model()` only runs `_prevalidate_patch_schema()` when `desc.prevalidate` is true. `list_detect` has `prevalidate=False`, and `validate_patch()` only checks that `patch["phase"]` is a non-empty string. A Phase A run can apply a patch whose phase is `"B"` if the operation is allowed.

Evidence:
- scripts/llm_enhancer.py:1209 registers `list_detect` without prevalidation.
- scripts/llm_enhancer.py:1415 gates phase matching behind `desc.prevalidate`.
- scripts/llm_enhancer.py:705 validates only presence/type of `phase`.

Recommendation: validate expected patch phase for every capability, derived from `_resolve_legacy_phase()` or an explicit `patch_phase` field on `CapabilityConfig`.

### High: Caption validation misses code/layout tables in normalized order

`_collect_caption_targets()` associates a caption with a following table, but `validate_patch()` checks only the preceding table type. In the normalized caption-before-table order, `code_sample` and `layout` guards are bypassed.

Evidence:
- scripts/llm_enhancer.py:365 looks forward from caption to table.
- scripts/llm_enhancer.py:731 builds caption table type from preceding table.
- scripts/llm_enhancer.py:809 relies on that map to block code/layout captions.

Recommendation: resolve the associated table in the same direction as the collector, or carry associated table type from collector into validation.

### Medium: Prompt adapter still has hard-coded capability branches

`_build_prompt_for_batch()` contains `cap_name == "list_detect"` and strategy-specific argument wiring. This works for built-ins, but new capabilities must fit the fallback `batch_items` shape or the existing `by_targets` convention.

Evidence:
- scripts/llm_enhancer.py:268 contains the adapter.
- scripts/llm_enhancer.py:278 marks it as transitional.

Recommendation: add `prompt_args_builder` or `build_prompt_for_batch` to `CapabilityConfig`, then remove name checks from dispatcher code.

### Medium: CLI aliases are normalized only in `main()`

`--llm-enhance list_detect|caption_gen|all` works through argparse, but helper callers that pass `args.llm_enhance="all"` directly into `convert_md()` or `convert_docx()` do not get normalization.

Evidence:
- scripts/main.py:448 accepts new choices.
- scripts/main.py:476 normalizes aliases only in `main()`.
- scripts/main.py:187 and scripts/main.py:239 read `args.llm_enhance` directly.
- scripts/llm_enhancer.py:1560 does not include `all`, `list_detect`, or `caption_gen` as modes.

Recommendation: move mode normalization into a shared helper used by `main()`, conversion helpers, and possibly `should_enhance()`.

### Medium: Tests contain a failing prompt assertion

Phase B prompt includes forbidden operation names in a prohibition sentence, while the test asserts those strings are absent.

Evidence:
- scripts/llm_enhancer.py:1144 contains `set_table_type`, `set_header_rows`, `set_caption_type`.
- tests/test_llm_enhancer.py:1416 asserts those strings are absent.
- `tests/verify_changes.py` reported `Prompt check: contains forbidden set_table_type`.

Recommendation: assert the prompt forbids those operations, and separately assert the JSON example only uses `set_caption_text`.

### Low: `CapabilityConfig` is frozen but contains mutable sets

`CapabilityConfig(frozen=True)` still holds mutable `set` values, and `_refresh_phase_exports()` exposes the same set through `ALLOWED_OPS_BY_PHASE`.

Evidence:
- scripts/llm_enhancer.py:69 defines the dataclass.
- scripts/llm_enhancer.py:168 exports `desc.allowed_ops` directly.
- scripts/llm_enhancer.py:1211 and scripts/llm_enhancer.py:1220 register mutable sets.

Recommendation: use `frozenset[str]`, or copy sets when exporting and validating.

## Coverage Assessment

Covered well:
- Registry entries and legacy alias maps.
- A/B and capability names in `enhance_document_model()`.
- `abc` and `force-abc` compatibility.
- Caption batching and empty-target skip.
- Basic fake capability registration.

Coverage gaps:
- Wrong `patch["phase"]` rejection for Phase A.
- Caption-before-code/layout-table rejection.
- Parser-level CLI tests for `list_detect`, `caption_gen`, `all`.
- Helper-level alias normalization tests.
- Fake capability tests with real mutation and custom prompt argument mapping.

## Validation Notes

- `python3 -m pytest tests/test_llm_enhancer.py -q` failed because `pytest` is not installed.
- `python3 -m py_compile ...` was blocked by read-only `__pycache__` writes.
- `python3 tests/verify_changes.py` ran and reported 3 errors.
```