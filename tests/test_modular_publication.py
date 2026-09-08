from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.publication import PublicationError, publish_bundle, recover_publication


def test_publication_writes_manifest_and_commit_marker(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    output = tmp_path / "candidate.docx"
    source.write_text("{}", encoding="utf-8")
    output.write_bytes(b"docx")

    result = publish_bundle({"source_ast": source, "docx": output}, tmp_path / "published", run_id="run-1")

    assert result["status"] == "completed"
    manifest = json.loads((tmp_path / "published" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "run-1"
    assert (tmp_path / "published" / "COMMITTED").exists()
    assert manifest["files"]["source_ast"]["sha256"]


def test_publication_failure_does_not_report_completed_and_recovery_is_explicit(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    destination = tmp_path / "published"

    with pytest.raises(PublicationError, match="missing artifact"):
        publish_bundle({"source_ast": source, "docx": tmp_path / "missing.docx"}, destination, run_id="run-2")
    assert not (destination / "COMMITTED").exists()
    assert recover_publication(destination)["status"] == "incomplete"
