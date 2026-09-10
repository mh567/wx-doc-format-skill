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
    complete_pending_response,
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
    expected = copy.deepcopy(waiting)
    expected["applied_transactions"] = []
    expected["transaction_content_digests"] = {}
    expected["transaction_digest_version"] = "2.0"
    assert restored == expected
    assert restored["source_facts"]
    assert restored["decision_ledger"]
    assert restored["source_ast"]
    assert restored["target_map"]
    assert restored["applied_transactions"] == []
    assert restored["transaction_content_digests"] == {}
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
    response = {
        "request_id": "role-0000",
        "raw_response": {"decision": "keep"},
        "input_hash": "sha256:" + "a" * 64,
        "transaction_id": "response-tx",
        "transaction_content_sha256": "sha256:" + "f" * 64,
    }

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
    checkpoint["applied_transactions"] = [{"transaction_id": "response-tx", "content_sha256": "sha256:" + "f" * 64}]
    checkpoint["transaction_content_digests"] = {"response-tx": "sha256:" + "f" * 64}
    save_checkpoint(checkpoint, path)
    response = {
        "request_id": "role-0000",
        "raw_response": {"decision": "keep"},
        "input_hash": "sha256:" + "a" * 64,
        "transaction_id": "response-tx",
        "transaction_content_sha256": "sha256:" + "f" * 64,
    }
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

    with pytest.raises(CheckpointError, match="application is pending") as caught:
        apply_response(pending, response, apply=lambda saved, _response: saved, path=path)
    assert caught.value.code == "response_application_pending"
    with pytest.raises(CheckpointError, match="not bound"):
        complete_pending_response(pending, "role-0000", transaction_id="other-tx", completed_checkpoint=pending)
    replacement = copy.deepcopy(pending)
    replacement["run_id"] = "other-run"
    with pytest.raises(CheckpointError, match="identity") as identity_error:
        complete_pending_response(pending, "role-0000", transaction_id="response-tx", completed_checkpoint=replacement)
    assert identity_error.value.code == "checkpoint_identity_mismatch"
    replacement = copy.deepcopy(pending)
    replacement["responses"][0]["raw_response"] = {"decision": "changed"}
    with pytest.raises(CheckpointError, match="completed response") as response_error:
        complete_pending_response(pending, "role-0000", transaction_id="response-tx", completed_checkpoint=replacement)
    assert response_error.value.code == "response_identity_mismatch"
    applied_without_transaction = copy.deepcopy(pending)
    applied_without_transaction["responses"][0]["applied"] = True
    applied_without_transaction["responses"][0]["apply_started"] = False
    applied_without_transaction["applied_transactions"] = [{"transaction_id": "other-tx", "content_sha256": "sha256:" + "f" * 64}]
    applied_without_transaction["transaction_content_digests"] = {"other-tx": "sha256:" + "f" * 64}
    with pytest.raises(CheckpointError, match="not recorded") as applied_error:
        complete_pending_response(pending, "role-0000", transaction_id="response-tx", completed_checkpoint=applied_without_transaction)
    assert applied_error.value.code == "transaction_state_missing"
    completed = complete_pending_response(
        pending,
        "role-0000",
        transaction_id="response-tx",
        completed_checkpoint=pending,
        path=path,
    )
    assert completed["responses"][0]["applied"] is True
    assert load_checkpoint(path)["responses"][0]["applied"] is True


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


def test_checkpoint_load_recovers_previous_generation_after_process_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import publication, run_store

    path = tmp_path / "run.json"
    checkpoint = _checkpoint()
    save_checkpoint(checkpoint, path)
    changed = copy.deepcopy(checkpoint)
    changed["round"] = 1
    real_replace = publication.os.replace
    interrupted = False

    def interrupt_after_checkpoint_install(source: Path | str, target: Path | str) -> None:
        nonlocal interrupted
        real_replace(source, target)
        if (
            not interrupted
            and Path(target) == path
            and ".staging-" in Path(source).name
        ):
            interrupted = True
            raise SystemExit("injected checkpoint interruption")

    monkeypatch.setattr(publication.os, "replace", interrupt_after_checkpoint_install)
    monkeypatch.setattr(run_store.os, "replace", interrupt_after_checkpoint_install)
    with pytest.raises(SystemExit, match="checkpoint interruption"):
        save_checkpoint(changed, path)
    monkeypatch.setattr(publication.os, "replace", real_replace)
    monkeypatch.setattr(run_store.os, "replace", real_replace)

    restored = load_checkpoint(path)

    assert restored["round"] == 0
    assert not list(tmp_path.glob(".wxdf-publication-*.journal"))
