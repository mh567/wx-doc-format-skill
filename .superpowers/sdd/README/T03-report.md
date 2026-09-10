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

- The worktree has no resident `scripts/.venv`. Initial verification deselected one older kernel CLI test that hardcodes that path. Review-fix verification temporarily linked that path to the brief-mandated shared environment, ran the complete kernel and full regression suites, then removed the link.
- Timeout returns stop accepting the late result, while Python callable cancellation remains best effort because the existing executor uses a daemon thread.
- CLI network backend auto-discovery stays unavailable for native-v2 by design under the approved authorization boundary. A caller must inject a controlled adapter programmatically.

## Independent review fix report

### Fixes delivered

- Synchronized the distribution manifest and release smoke validator with the T03 `modular_online` contract. The release surface now records `EnhancementExecutor`, contract `T03`, and status `enabled`.
- Isolated native-v2 from the legacy `WX_DOC_LLM_ENHANCE` compatibility toggle. Native-v2 honors `--llm-enhance off` and does not resolve or invoke the explicit command. The legacy CLI retains its environment toggle behavior.
- Added a native-v2 kernel preflight failure when enhancement is enabled without a programmatically injected `enhancement_call`. It returns `NATIVE_ENHANCEMENT_ADAPTER_MISSING` before module execution and publication.
- Blocked `_FrozenDict.__ior__` so mapping union update cannot mutate the adapter payload.

Additional changed files for these fixes:

- `packaging/nuitka/build_distribution.py`
- `packaging/nuitka/smoke_distribution.py`
- `tests/test_modular_distribution.py`
- `tests/test_modular_kernel.py`

### Fix RED evidence

The five focused assertions were added before production changes:

```text
FAILED tests/test_modular_distribution.py::test_modular_manifest_matches_contracts_and_open_modes
FAILED tests/test_modular_distribution.py::test_release_smoke_accepts_explicit_native_online_adapter_mode
FAILED tests/test_modular_capability.py::test_native_enhancement_uses_an_immutable_versioned_view_and_accepts_candidates
FAILED tests/test_modular_kernel.py::test_cli_native_v2_explicit_enhancement_off_ignores_legacy_env_toggle
FAILED tests/test_modular_kernel.py::test_native_v2_enabled_enhancement_requires_programmatic_adapter_before_publish
5 failed in 1.90s
```

Observed causes matched the four review findings: closed release manifest, smoke rejection of the enabled mode, mutable `__ior__`, environment-triggered adapter execution, and completed publication without an adapter.

### Fix GREEN evidence

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest -q tests/test_modular_distribution.py::test_modular_manifest_matches_contracts_and_open_modes tests/test_modular_distribution.py::test_release_smoke_accepts_explicit_native_online_adapter_mode tests/test_modular_capability.py::test_native_enhancement_uses_an_immutable_versioned_view_and_accepts_candidates tests/test_modular_kernel.py::test_cli_native_v2_explicit_enhancement_off_ignores_legacy_env_toggle tests/test_modular_kernel.py::test_native_v2_enabled_enhancement_requires_programmatic_adapter_before_publish
5 passed in 0.93s
```

Legacy compatibility guard:

```text
tests/test_modular_kernel.py::test_cli_legacy_keeps_legacy_env_enhancement_toggle
1 passed in 2.30s
```

Requested modular suites:

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest -q tests/test_modular_capability.py tests/test_modular_review.py tests/test_modular_distribution.py tests/test_modular_kernel.py
42 passed in 5.55s
```

Frozen T01 through T03 acceptance:

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest -q tests/test_native_skill_cutover.py -k 't01 or t02 or t03'
6 passed, 11 deselected in 2.30s
```

Full regression excluding the performance suite:

```text
PYTHONPATH="$(pwd)/tests" /Users/harris/Documents/zero项目/wx-doc-format-skill/scripts/.venv/bin/python3 -m pytest tests/ --ignore=tests/test_modular_performance.py -q
11 failed, 552 passed in 34.37s
```

All 11 failures are the frozen T04 through T09 acceptance tests. No implemented T00 through T03 or non-frozen regression failed.

### Fix self-review

- `git diff --check` passed.
- The missing-adapter diagnostic is emitted before the staging path, SourceStore, module registry, renderer, and publisher are created or invoked.
- The native-v2 environment conflict test passes an explicit local command with a marker and proves the marker remains absent. Its paired legacy test proves the compatibility path still calls the command.
- The distribution build manifest exactly matches `wxdf.review.MODE_MANIFEST`, and the smoke validator requires the complete T03 online entry.
- The temporary `scripts/.venv` symlink used by the older hardcoded kernel subprocess test pointed to the brief-mandated shared environment and was removed after verification. The required `input` symlink remains unchanged.

### Remaining concerns

- The 11 frozen T04 through T09 failures remain intentional future-card gates.
- Native-v2 provides no environment, URL, credential, or network adapter discovery. Explicit local CLI command and explicit programmatic injection remain the only authorized enhancement seams.
