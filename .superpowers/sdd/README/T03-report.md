# T03 implementation report

## Status

Implementation complete and ready for main Codex review. Commits created: none.

## Scope delivered

- Added immutable AST 2.0 capability views and native request construction in `scripts/wxdf/enhancement.py`.
- Accepted validated CandidateSet 2.0 or Patch 2.0 responses and returned stable statuses for no response, timeout, invalid Patch, stale targets, exhausted budget, and missing capability.
- Added native enhancement candidates to the existing candidate aggregation stage before Resolver and TransactionManager.
- Persisted the committed AST and reported enhancement results beside the native decision set and transaction counts.
- Enabled `modular_online` for explicit injection.
- Allowed the native-v2 CLI path only when enhancement is off or the user supplies an explicit local `--llm-command`.
- Kept controlled online adapters as an explicit programmatic `enhancement_call` injection. Added no environment discovery, URL, credential, or network backend.

## Changed files

- `scripts/main.py`
- `scripts/wxdf/builtin_modules.py`
- `scripts/wxdf/enhancement.py`
- `scripts/wxdf/kernel.py`
- `scripts/wxdf/registry.py`
- `scripts/wxdf/review.py`
- `tests/test_modular_capability.py`
- `tests/test_modular_review.py`
- `docs/execution-v4/evidence/T03.md`
- `.superpowers/sdd/README/T03-report.md`

`scripts/wxdf/legacy_capability.py`, `scripts/wxdf/resolver.py`, and `scripts/wxdf/transaction.py` remained unchanged. Their stable candidate, decision, and commit contracts were reused directly.

## TDD evidence

### RED

Frozen acceptance before production changes:

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q -k t03
FAILED tests/test_native_skill_cutover.py::test_t03_native_command_enhancement_is_resolved_and_committed
AssertionError: legacy-full-v1
1 failed, 16 deselected in 0.78s
```

Focused edge tests initially failed at collection because `build_native_request` was absent. After the initial API was added, all new executor cases passed and the manifest test remained RED because `modular_online.status` was `closed`.

The Patch decision stale-target mutation was also verified independently:

```text
FAILED tests/test_modular_capability.py::test_native_enhancement_returns_stable_statuses[stale Patch decision target...]
E assert 'completed' == 'stale_target'
1 failed, 16 deselected in 0.14s
```

### GREEN

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q -k 't01 or t02 or t03'
6 passed, 11 deselected in 2.46s
```

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest tests/test_modular_capability.py tests/test_modular_review.py tests/test_modular_resolver.py tests/test_modular_contracts.py tests/test_modular_ast.py -q
49 passed in 0.21s
```

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest tests/test_modular_kernel.py -q -k 'not cli_modular_mode_has_explicit_preflight_failure'
10 passed, 1 deselected in 0.10s
```

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest tests/test_modular_conversion.py -q -k 'requires_complete_adapter or rejects_online_enhancement_without_legacy_fallback or nested_markdown_lists_require_complete_adapter'
3 passed, 4 deselected in 0.74s
```

Programmatic in-memory adapter integration:

```text
programmatic_adapter=passed transaction=committed
```

The native-v2 CLI with enhancement enabled and no explicit local command exits with code 2 and reports:

```text
main.py: error: native-v2 enhancement requires an explicit local --llm-command
```

## Self-review

- Stable interfaces remain in place. `EnhancementResult.candidates` was appended after existing dataclass fields to preserve previous positional construction.
- Adapter payloads are recursively frozen copies. The live Source AST cannot be mutated by an injected adapter.
- Patch schema, base revision, decision revision, node existence, source references, view digest, response identity, response count, and budget are checked before candidate publication.
- Patch decisions do not bypass Resolver. Only their validated candidates join the native candidate set, and TransactionManager remains the sole AST writer.
- Four registered capability names are selected through the existing mode mapping. Candidate IDs and module IDs are namespaced before aggregation to prevent cross-capability collisions.
- Enhancement off returns before adapter resolution in the native module, and main constructs a local adapter only for explicit `--llm-command` use.
- `git diff --check` passed. The `input` symlink still points to `/Users/harris/Documents/zero项目/wx-doc-format-skill/input`.

## Concerns and limits

- The worktree has no `scripts/.venv`. One older kernel CLI test hardcodes that path and fails with `FileNotFoundError`; it was deselected while all other relevant kernel tests ran through the shared interpreter required by the brief.
- Timeout returns stop accepting the late result, while Python callable cancellation remains best effort because the existing executor uses a daemon thread.
- CLI network backend auto-discovery stays unavailable for native-v2 by design under the approved authorization boundary. A caller must inject a controlled adapter programmatically.
