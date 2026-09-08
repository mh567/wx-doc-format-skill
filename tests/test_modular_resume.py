from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_contracts import _valid_checkpoint
from wxdf.run_store import (
    CheckpointError,
    apply_response,
    load_checkpoint,
    record_response,
    save_checkpoint,
    transition_status,
)


def _checkpoint() -> dict:
    checkpoint = _valid_checkpoint()
    checkpoint["resource_summary"]["fingerprints"] = {
        "source": "sha256:" + "b" * 64,
        "template": "sha256:" + "c" * 64,
        "resources": "sha256:" + "d" * 64,
        "configuration": "sha256:" + "e" * 64,
        "parser": "markdown-parser@1.0.0",
        "protocol": "1.0",
    }
    return checkpoint


def test_checkpoint_status_transitions_and_round_trip_preserve_full_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    checkpoint = _checkpoint()
    checkpoint["status"] = "created"
    checkpoint["resume_stage"] = None

    save_checkpoint(checkpoint, path)
    running = transition_status(checkpoint, "running")
    waiting = transition_status(running, "waiting_external", resume_stage="role")
    save_checkpoint(waiting, path)

    restored = load_checkpoint(
        path,
        expected_fingerprints=checkpoint["resource_summary"]["fingerprints"],
        expected_contracts=checkpoint["contracts"],
    )
    assert restored == waiting
    assert restored["source_facts"]
    assert restored["decision_ledger"]
    assert restored["source_ast"]
    assert restored["target_map"]
    assert restored["budget"] == checkpoint["budget"]

    with pytest.raises(CheckpointError, match="invalid status transition"):
        transition_status(waiting, "created")
    with pytest.raises(CheckpointError, match="resume stage"):
        transition_status(running, "waiting_external")


def test_fingerprint_or_contract_drift_blocks_restore(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    checkpoint = _checkpoint()
    save_checkpoint(checkpoint, path)

    expected = dict(checkpoint["resource_summary"]["fingerprints"])
    expected["template"] = "sha256:" + "f" * 64
    with pytest.raises(CheckpointError, match="template"):
        load_checkpoint(path, expected_fingerprints=expected)

    contracts = dict(checkpoint["contracts"])
    contracts["ast"] = "9.0"
    with pytest.raises(CheckpointError, match="contract"):
        load_checkpoint(path, expected_contracts=contracts)


def test_response_persistence_is_idempotent_and_conflicts_on_different_content() -> None:
    checkpoint = _checkpoint()
    response = {"request_id": "role-0000", "raw_response": {"decision": "keep"}, "input_hash": "sha256:" + "a" * 64}

    first, state = record_response(checkpoint, response)
    second, repeated = record_response(first, copy.deepcopy(response))
    assert state == "recorded"
    assert repeated == "already_recorded"
    assert second == first

    changed = dict(response)
    changed["raw_response"] = {"decision": "change"}
    with pytest.raises(CheckpointError, match="response conflict"):
        record_response(first, changed)


def test_response_is_saved_before_apply_and_crash_leaves_pending_response(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    checkpoint = _checkpoint()
    save_checkpoint(checkpoint, path)
    response = {"request_id": "role-0000", "raw_response": {"decision": "keep"}, "input_hash": "sha256:" + "a" * 64}
    observed: list[dict] = []

    def fail_after_persist(saved: dict, _response: dict) -> dict:
        observed.append(json.loads(path.read_text(encoding="utf-8")))
        raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected crash"):
        apply_response(checkpoint, response, apply=fail_after_persist, path=path)
    assert observed[0]["responses"][0]["applied"] is False
    pending = load_checkpoint(path)
    assert pending["responses"][0]["request_id"] == "role-0000"
    assert pending["responses"][0]["applied"] is False

    def commit(saved: dict, _response: dict) -> dict:
        saved["status"] = "completed"
        saved["resume_stage"] = None
        return saved

    applied = apply_response(pending, response, apply=commit, path=path)
    assert applied["responses"][0]["applied"] is True
    assert load_checkpoint(path)["status"] == "completed"


def test_atomic_checkpoint_write_keeps_previous_commit_on_precommit_failure(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    checkpoint = _checkpoint()
    save_checkpoint(checkpoint, path)
    changed = copy.deepcopy(checkpoint)
    changed["round"] = 1

    with pytest.raises(CheckpointError, match="before commit"):
        save_checkpoint(changed, path, crash_at="before_commit")
    assert load_checkpoint(path)["round"] == 0

    with pytest.raises(CheckpointError, match="after commit"):
        save_checkpoint(changed, path, crash_at="after_commit")
    assert load_checkpoint(path)["round"] == 1
