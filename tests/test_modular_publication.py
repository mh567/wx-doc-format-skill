from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.publication import PublicationError, publish_bundle, recover_publication


ACCEPTED = {
    "status": "accepted",
    "reason": "target_audit_improved",
    "accepted": True,
    "semantic_changed": True,
    "presentation_changed": False,
}


def test_publication_writes_manifest_and_commit_marker(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    output = tmp_path / "candidate.docx"
    source.write_text("{}", encoding="utf-8")
    output.write_bytes(b"docx")

    result = publish_bundle(
        {"source_ast": source, "docx": output},
        tmp_path / "published",
        run_id="run-1",
        acceptance=ACCEPTED,
    )

    assert result["status"] == "completed"
    manifest = json.loads((tmp_path / "published" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-1"
    assert (tmp_path / "published" / "COMMITTED").exists()
    assert manifest["files"]["source_ast"]["sha256"]
    assert manifest["acceptance"] == ACCEPTED


def test_publication_failure_does_not_report_completed_and_recovery_is_explicit(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    destination = tmp_path / "published"

    with pytest.raises(PublicationError, match="missing artifact"):
        publish_bundle(
            {"source_ast": source, "docx": tmp_path / "missing.docx"},
            destination,
            run_id="run-2",
            acceptance=ACCEPTED,
        )
    assert not (destination / "COMMITTED").exists()
    assert recover_publication(destination)["status"] == "incomplete"


@pytest.mark.parametrize("acceptance", [None, {}, {"accepted": False, "status": "rejected"}])
def test_publication_rejects_missing_or_unaccepted_record_before_destination_mutation(
    tmp_path: Path,
    acceptance: dict | None,
) -> None:
    artifact = tmp_path / "candidate.docx"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "formal"
    destination.mkdir()
    existing = destination / "docx"
    existing.write_bytes(b"formal")

    with pytest.raises(PublicationError, match="accepted candidate"):
        publish_bundle(
            {"docx": artifact},
            destination,
            run_id="run-rejected",
            acceptance=acceptance,
        )

    assert existing.read_bytes() == b"formal"
    assert not (destination / ".staging").exists()
