# T04 implementation report

## Status

Implementation complete and ready for main Codex review. Commits created: none.

## Scope delivered

1. Native v2 `--generate-requests` now runs the registered parser and candidate stages, serializes complete versioned requests, writes Checkpoint 2.0 state, and exits with `waiting_external` before resolution, transaction, rendering, or publication.
2. Native v2 `--resume` restores its saved CLI configuration, validates checkpoint identities before accepting responses, records available responses atomically, waits while any request is unanswered, and runs the registered native chain after all responses are present.
3. The checkpoint contains profile, list policy, configuration, source and template fingerprints, SourceFacts and binding, Source AST, normalized state, decision ledger, content and presentation overlays, module identities, contracts, request and response identities, transactions, budget, round, and candidate baseline identity.
4. Request and response content digests enforce idempotence. Reused identities with changed content return stable checkpoint conflicts. Completed checkpoints are replay safe and remain byte stable for equivalent responses.
5. Source, template, configuration, contract, and module drift is rejected before response recording or publication.
6. Runtime and release manifests now expose `modular_file` through `CheckpointStore` under contract `T04`.
7. No network, URL, credential, or environment adapter discovery was added.

## Changed files

1. `scripts/wxdf/enhancement.py`
2. `scripts/wxdf/run_store.py`
3. `scripts/wxdf/builtin_modules.py`
4. `scripts/wxdf/kernel.py`
5. `scripts/main.py`
6. `scripts/wxdf/review.py`
7. `packaging/nuitka/build_distribution.py`
8. `packaging/nuitka/smoke_distribution.py`
9. `docs/contracts/modular-v3/checkpoint.schema.json`
10. `tests/test_modular_file_protocol.py`
11. `tests/test_modular_review.py`
12. `tests/test_modular_distribution.py`
13. `docs/execution-v4/evidence/T04.md`
14. `.superpowers/sdd/README/T04-report.md`

`scripts/wxdf/decision_ledger.py` did not require modification. Its existing `source_binding` and `accept_decisions` contracts are consumed by the checkpoint store.

## TDD evidence

### RED

The frozen baseline supplied by the brief was:

```text
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q -k t04
1 failed, 16 deselected
```

The first new focused collection failed before production implementation because `wxdf.run_store.request_digest` was absent:

```text
ImportError: cannot import name 'request_digest' from 'wxdf.run_store'
1 error during collection
```

After the request identity API was introduced, the broader focused file protocol suite remained RED:

```text
1 passed, 8 failed
```

Those failures covered missing native checkpoint generation and resume orchestration, identity drift, idempotence, response conflicts, and multi stage equivalence.

### GREEN

T04 focused suites:

```text
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest -q tests/test_modular_file_protocol.py tests/test_modular_resume.py tests/test_modular_contracts.py tests/test_modular_capability.py tests/test_modular_kernel.py tests/test_modular_review.py tests/test_modular_distribution.py
63 passed in 10.32s
```

Frozen T04 acceptance:

```text
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q -k t04
1 passed, 16 deselected in 0.29s
```

Frozen T00 through T04 acceptance:

```text
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q -k 't01 or t02 or t03 or t04'
7 passed, 10 deselected in 2.60s
```

All available modular suites, excluding the intentionally absent future T07 helper:

```text
modular_tests=(tests/test_modular_*.py)
modular_tests=(${modular_tests:#tests/test_modular_performance.py})
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest ${modular_tests[@]} -q
161 passed in 17.05s
```

Full regression, excluding the same absent helper:

```text
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/ --ignore=tests/test_modular_performance.py -q
10 failed, 562 passed in 38.29s
```

The ten remaining failures are frozen future card gates:

1. `test_t05_native_mode_manifest_enables_modular_review`
2. `test_t05_publication_requires_an_accepted_candidate`
3. `test_t06_default_cli_invocation_uses_native_pipeline_and_policy`
4. `test_t07_public_skill_supported_targets_exclude_windows`
5. `test_t07_public_skill_manifest_reports_per_platform_status`
6. `test_t07_public_skill_launcher_consumes_platform_status`
7. `test_t08_release_manifests_exclude_legacy_production_surface`
8. `test_t09_distribution_smoke_accepts_native_only_module_set`
9. `test_t09_distribution_smoke_rejects_legacy_production_module`
10. `test_t09_distribution_smoke_rejects_legacy_production_mode`

No implemented T00 through T04 or nonfrozen regression failed.

## Self review

1. Checkpoint serialization and identity validation are concentrated in `run_store.py`. The kernel change is limited to selecting generate or resume behavior, supplying the registered module plan, and handing the completed transaction snapshot back to the store.
2. Generate mode raises a dedicated internal pause after request capture and before Resolver, TransactionManager, normalization, rendering, and publishing. The focused test proves that no output exists in the waiting state.
3. Resume validates saved content and current source, template, contract, and registered module fingerprints before parsing external response lines. Invalid or conflicting content leaves the checkpoint and output unchanged.
4. Partial response files are recorded without applying any response. The checkpoint remains `waiting_external`, keeps all transactions empty, and resumes only when every saved request identity has one response.
5. Equivalent completed replay returns without rerunning conversion or rewriting the checkpoint. Changed response content returns a stable conflict and preserves the completed checkpoint bytes.
6. The multi stage test supplies one response, observes a stable waiting checkpoint, supplies the second response, and compares saved Source AST plus semantic and presentation digests against a single process native execution using the same responses.
7. Existing public CheckpointStore functions remain compatible. New schema fields are optional at schema level so earlier valid Checkpoint 2.0 fixtures continue to load, while native generation always writes the complete T04 state.
8. `tests/test_native_skill_cutover.py`, `docs/execution-v4/status.json`, and `AGENTS.md` have no diff.
9. `git diff --check` passed before report generation.
10. Worktree only `input` and `scripts/.venv` symlinks remain untracked as required test fixtures.

## Concerns and limits

1. `tests/test_modular_performance.py` still cannot collect because the future T07 `scripts/modular_benchmark.py` helper is absent. The brief explicitly permits excluding this suite.
2. The full regression remains red only at the ten frozen T05 through T09 gates listed above.
3. The current T04 resume stage is the enhancement boundary. No response is applied until the complete request set is present, so partial multi stage rounds persist validated response state without intermediate semantic transactions.
4. Implementation was completed by the assigned Codex implementation agent. No model upgrade or fallback occurred within this delegated task.

## Independent review fix round 1

### Status

All five review findings are closed in the working tree. This fix round created no Git commit.

### Failure first evidence

The review tests were added before the corresponding production changes and produced these RED results:

1. A complete Normalized 1.0 checkpoint snapshot failed validation because it was treated as Source AST 2.0. The focused contract test failed once.
2. Stale view digests, invalid Patch payloads, and removed targets still published output. The focused protocol group failed three tests.
3. A preloaded checkpoint AST, ledger, overlays, presentation overrides, and transaction digest were ignored. The focused restore test failed once with `STALE_TARGET`.
4. Explicit input, output, template, profile, policy, and enhancement mode changes were overwritten from the checkpoint. The focused configuration group failed six tests.
5. Parser identity, the real requests file, and external resources were not all bound before response recording. The focused identity group failed three tests.
6. The first normalized equivalence check failed because the checkpoint stored Source AST under `normalized_ast`. After that value was corrected, canonical DOCX member comparison stayed RED because table cell wrappers were keyed with reusable numeric Python object identities.
7. The completed checkpoint replay test later exposed one final RED. It incorrectly compared the original request AST to the final committed AST and returned `request_identity_conflict`.

### Fixes

1. `run_store.py` now validates the active source, parser, resources, request file, configuration, contracts, and modules before reading responses. It restores the saved AST, active ledger, overlays, presentation overrides, transaction digests, and resume stage as explicit native inputs.
2. `main.py` preserves every explicitly supplied checkpoint configuration option. Omitted values are restored from the checkpoint. The current effective configuration digest is passed to `prepare_native_resume` for comparison.
3. Every enhancement result whose status differs from `completed` stops the native chain before Resolver, transaction, rendering, publication, and checkpoint completion. Only completed response requests are marked applied.
4. Completed checkpoints persist the full Normalized 1.0 object. The schema and runtime validator accept that object, `None`, and the established Source AST compatibility shape.
5. `rendering.py` retains each table cell's underlying OOXML element as the lifecycle key. This prevents object identity reuse from changing which initial cell paragraph is consumed.
6. Completed replay continues to bind the exact request file and recorded response identity. It skips the invalid original AST versus final AST comparison, so equivalent replay is idempotent and changed response content remains a conflict.

### Output equivalence definition

The multi stage test requires all of the following to match the same response set executed online:

1. Full Normalized 1.0 JSON, final Source AST, semantic digest, and presentation digest.
2. The complete `saved_output_audit` result on both outputs, with `passed=true`.
3. Table count, every table cell's paragraph count, empty paragraph count, paragraph style sequence, numbering property count, and caption `SEQ` field instructions.
4. A SHA 256 digest over every uncompressed OPC member name and byte payload in sorted member order.

The OPC digest excludes ZIP entry order, compression method details, and ZIP timestamps. It currently excludes no OOXML relationship IDs or XML ordering, so it is stricter than canonical semantic equivalence.

### GREEN verification

```text
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_modular_file_protocol.py tests/test_modular_contracts.py -q
30 passed in 9.96s

PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_modular_rendering.py tests/test_modular_table_consumption.py tests/test_table_formatting.py tests/test_table_semantics.py tests/test_caption_placement.py tests/test_modular_caption_consumption.py tests/test_modular_conversion.py -q
45 passed in 3.95s

PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest -q tests/test_modular_file_protocol.py tests/test_modular_resume.py tests/test_modular_contracts.py tests/test_modular_capability.py tests/test_modular_kernel.py tests/test_modular_review.py tests/test_modular_distribution.py
77 passed in 15.63s

PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q -k t04
1 passed, 16 deselected in 0.27s

PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q -k 't00 or t01 or t02 or t03 or t04'
7 passed, 10 deselected in 2.38s

modular_tests=(tests/test_modular_*.py)
modular_tests=(${modular_tests:#tests/test_modular_performance.py})
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest ${modular_tests[@]} -q
175 passed in 22.03s

PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/ --ignore=tests/test_modular_performance.py -q
10 failed, 576 passed in 43.12s
```

The ten full suite failures remain the frozen T05 through T09 gates listed earlier in this report. `git diff --check` passed. The protected T04 test, status file, and `AGENTS.md` have no working tree difference.

### Fix round changed files

1. `docs/contracts/modular-v3/checkpoint.schema.json`
2. `scripts/main.py`
3. `scripts/wxdf/builtin_modules.py`
4. `scripts/wxdf/kernel.py`
5. `scripts/wxdf/rendering.py`
6. `scripts/wxdf/run_store.py`
7. `scripts/wxdf/validation.py`
8. `tests/test_modular_contracts.py`
9. `tests/test_modular_file_protocol.py`

The worktree only `input` and `scripts/.venv` symlinks remain untracked. The intentionally absent future `tests/test_modular_performance.py` helper remains the only test collection exclusion.

## Independent review fix round 2

### Status

The embedded response identity, relation resume gate, state consistency, candidate baseline, and verified byte binding findings are closed. This fix round created no Git commit.

### Failure first evidence

1. Waiting and completed checkpoint response tampering, an unsupported resume stage, and six inconsistent relation state components initially produced `9 failed, 22 deselected`.
2. Resource and template replacement after resume preparation initially produced `2 failed, 31 deselected` because rendering reopened the original paths.
3. The first template snapshot fix exposed nondeterministic candidate ordering after a JSON round trip. Canonical candidate ordering made the candidate baseline process stable.
4. A malformed ledger transaction escaped the checkpoint error boundary and produced `1 failed, 33 deselected` with `MODULAR_NATIVE_TRANSACTION_CONFLICT`.
5. An external resource removed after parsing and before byte binding allowed response recording before render failure. The focused RED was `1 failed, 34 deselected`.

### Fixes

1. Every stored response is rehashed from its canonical response content during checkpoint load. Only `response_sha256`, `applied`, `apply_started`, `transaction_id`, and `transaction_content_sha256` are excluded. A mismatch returns `response_identity_mismatch` before response map construction or completed replay.
2. Waiting checkpoints can resume only at `relation`. Other stages return `unsupported_resume_stage`. Relation recovery rebuilds Source AST from current SourceFacts, replays the decision ledger, and compares the saved AST, content overlay, presentation override, applied transactions, and transaction digests before execution.
3. Candidate baselines use canonical candidate ordering. The checkpoint run identity is checked before response recording, and the live semantic candidate set is checked before enhancement consumption.
4. Transaction replay errors are normalized to `resume_snapshot_mismatch`. A valid nonempty state is created with the actual transaction and ledger APIs and resumes successfully; each independently inconsistent component is rejected.
5. Resume captures external resource bytes and template bytes once. `SourceStore.bind_part` retains verified resources, `TemplateBundle` retains the template snapshot, and normalization plus rendering consume those exact bytes. Path based callers remain compatible.
6. A resource that disappears after parsing but before binding now returns `fingerprint_mismatch` before response recording. Resources already classified as unresolved remain compatible and later fail the existing render contract if required.

### Final focused verification

```text
PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest -q tests/test_modular_file_protocol.py tests/test_modular_resume.py tests/test_modular_contracts.py tests/test_modular_capability.py tests/test_modular_kernel.py tests/test_modular_review.py tests/test_modular_distribution.py
90 passed in 24.37s

PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_native_skill_cutover.py -q -k 't00 or t01 or t02 or t03 or t04'
7 passed, 10 deselected in 3.16s

PYTHONPATH="$(pwd)/tests" scripts/.venv/bin/python3 -m pytest tests/test_modular_rendering.py tests/test_modular_table_consumption.py tests/test_table_formatting.py tests/test_table_semantics.py tests/test_caption_placement.py tests/test_modular_caption_consumption.py tests/test_modular_conversion.py -q
45 passed in 4.61s
```

Before the two final self review cases were added, all modular tests excluding the future performance helper passed `186 passed in 30.38s`. The full regression at that point was `10 failed, 587 passed in 55.23s`; all ten failures were the frozen T05 through T09 gates already listed above. The main Codex owns the final broad rerun requested after this fix round.

### Fix round changed files

1. `scripts/wxdf/run_store.py`
2. `scripts/wxdf/builtin_modules.py`
3. `scripts/wxdf/source_store.py`
4. `scripts/wxdf/template.py`
5. `scripts/wxdf/rendering.py`
6. `tests/test_modular_file_protocol.py`

The protected test, status file, and `AGENTS.md` remain unchanged. The worktree only `input` and `scripts/.venv` symlinks remain untracked.

## Independent review fix round 3

### Failure first evidence

1. Eight CLI resume cases changed `applied`, `apply_started`, `transaction_id`, or `transaction_content_sha256` in waiting and completed checkpoints. All eight initially resumed successfully, so the focused RED was `8 failed, 35 deselected`.
2. A live candidate drift test injected an additional deterministic built in candidate during resume. The existing late check returned `candidate_baseline_mismatch` only after recording the external response, so the RED was `1 failed, 43 deselected` with changed checkpoint bytes.

### Fixes

1. Checkpoint validation now identifies native v2 file checkpoints from their profile or effective configuration and validates every response application state against the checkpoint status.
2. Waiting responses must remain unapplied, cannot have an application in progress, and cannot carry transaction application metadata. Completed responses must be applied, must have `apply_started=false`, and must bind to the exact transaction digest present in both checkpoint transaction collections.
3. `run_store.py` preserves `response_application_state_mismatch` as a stable checkpoint error. The validation runs during initial load, before the completed shortcut, response map construction, or any checkpoint and output write.
4. `build_native_candidate_baseline` reconstructs the deterministic scope, structure, protected content, list, appendix, table, and caption candidates from current SourceFacts and Source AST. Resume hashes this live set before reading or recording external responses and compares it to the saved candidate baseline.
5. The existing run identity check and the later module boundary check remain defense in depth. The new live check establishes semantic candidate identity independently of `run_id`.

### Verification

```text
Response application state RED: 8 failed, 35 deselected
Response application state GREEN: 8 passed, 40 deselected
Live candidate drift RED: 1 failed, 43 deselected
Live candidate drift GREEN: 1 passed, 43 deselected
Protocol, resume, and contract integration: 57 passed in 25.75s
T04 focused and related modules: 99 passed in 43.96s
Frozen T00 through T04: 7 passed, 10 deselected in 5.96s
Rendering, table, caption, and conversion regression: 45 passed in 8.69s
All modular tests excluding the future performance helper: 197 passed in 54.12s
Full regression excluding the future performance helper: 10 failed, 598 passed in 84.71s
```

The ten full regression failures remain the frozen T05 through T09 gates listed above. No implemented T00 through T04 behavior or other regression failed.

### Fix round changed files

1. `scripts/wxdf/run_store.py`
2. `scripts/wxdf/validation.py`
3. `scripts/wxdf/builtin_modules.py`
4. `tests/test_modular_file_protocol.py`
5. `.superpowers/sdd/README/T04-report.md`
6. `docs/execution-v4/evidence/T04.md`

This fix round created no Git commit. The protected test, status file, and `AGENTS.md` remain unchanged.
