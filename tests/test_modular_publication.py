from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import UUID

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.publication import (
    PublicationError,
    publish_bundle,
    publish_files_atomically,
    recover_pending_publications,
    recover_publication,
)


ACCEPTED = {
    "status": "accepted",
    "reason": "target_audit_improved",
    "accepted": True,
    "semantic_changed": True,
    "presentation_changed": False,
}
MISSING = object()


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


@pytest.mark.parametrize("reserved_name", [
    "manifest.json", "COMMITTED", "Manifest.JSON", "committed",
    "MANIFEST.JSON/item", "committed/item", "nested/CoMmItTeD",
    "COMMITTED.", "COMMITTED ", "manifest.json ", "Manifest.JSON. ",
    "COMMITTED./item", "manifest.json /item", "nested/CoMmItTeD. ",
])
def test_publication_rejects_reserved_bundle_artifact_names(
    tmp_path: Path,
    reserved_name: str,
) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"

    with pytest.raises(PublicationError, match="reserved artifact name"):
        publish_bundle(
            {reserved_name: artifact},
            destination,
            run_id="run-reserved",
            acceptance=ACCEPTED,
        )

    assert not destination.exists()


@pytest.mark.parametrize("names", [
    ("item", "./item"),
    ("item", "ITEM"),
    ("caf\N{LATIN SMALL LETTER E WITH ACUTE}", "cafe\N{COMBINING ACUTE ACCENT}"),
    ("item", "ITEM/child"),
    ("item", "item."),
    ("item", "item "),
    ("item.", "ITEM/child"),
    ("item", "ITEM. /child"),
    ("nested/item", "NESTED. /item. "),
    ("caf\N{LATIN SMALL LETTER E WITH ACUTE}", "CAFE\N{COMBINING ACUTE ACCENT}. "),
    ("/absolute",),
    ("../outside",),
    ("nested/../item",),
    ("",),
    ("item//child",),
    ("item/",),
    (".",),
    ("nested/./item",),
    ("C:/absolute",),
    ("nested\\item",),
    ("...",),
    (" . ",),
    ("nested/ . /item",),
])
def test_bundle_rejects_noncanonical_or_colliding_logical_names_before_mutation(
    tmp_path: Path,
    names: tuple[str, ...],
) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"
    destination.mkdir()
    original = destination / "original"
    original.write_bytes(b"old-bundle")

    with pytest.raises(PublicationError, match="artifact name"):
        publish_bundle({name: artifact for name in names}, destination, acceptance=ACCEPTED)

    assert original.read_bytes() == b"old-bundle"
    assert sorted(path.name for path in destination.iterdir()) == ["original"]
    assert not list(tmp_path.glob(".published.staging-*"))
    assert not list(tmp_path.glob(".wxdf-publication-*.journal"))


def test_bundle_preserves_canonical_nested_names(tmp_path: Path) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"

    published = publish_bundle(
        {"nested/item": artifact, "nested/other": artifact},
        destination,
        acceptance=ACCEPTED,
    )

    assert set(published["manifest"]["files"]) == {"nested/item", "nested/other"}
    assert recover_publication(destination)["status"] == "completed"


@pytest.mark.parametrize("names", [
    ("COMMITTED.",),
    ("manifest.json ",),
    ("nested/COMMITTED. /item",),
    ("item", "item."),
    ("item", "item "),
    ("item", "ITEM. /child"),
])
def test_bundle_recovery_rejects_win32_control_and_logical_aliases(
    tmp_path: Path,
    names: tuple[str, ...],
) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"
    publish_bundle({"payload": artifact}, destination, acceptance=ACCEPTED)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["files"]["payload"]
    manifest["files"] = {}
    for name in names:
        manifest["files"][name] = dict(record, path=name)
        target = destination / name
        # Some volumes cannot materialize a file and its aliased parent.
        # The manifest still contains the conflicting records to reject.
        if target.parent.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"candidate")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert recover_publication(destination)["status"] == "incomplete"


def test_publication_recovery_requires_marker_to_match_manifest_run_id(tmp_path: Path) -> None:
    artifact = tmp_path / "candidate.docx"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"
    publish_bundle(
        {"docx": artifact},
        destination,
        run_id="run-bound",
        acceptance=ACCEPTED,
    )
    (destination / "COMMITTED").write_text("different-run", encoding="utf-8")

    recovered = recover_publication(destination)

    assert recovered["status"] == "incomplete"
    assert recovered["reason"] == "marker_mismatch"


@pytest.mark.parametrize("run_id", ["", " ", "\t\n", "\u3000", None, 1, True, {}, [], MISSING])
def test_bundle_recovery_requires_nonempty_run_identity(
    tmp_path: Path,
    run_id: object,
) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"
    publish_bundle({"item": artifact}, destination, run_id="valid-run", acceptance=ACCEPTED)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if run_id is MISSING:
        manifest.pop("run_id")
    else:
        manifest["run_id"] = run_id
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    marker_text = run_id if isinstance(run_id, str) else ""
    (destination / "COMMITTED").write_text(marker_text, encoding="utf-8")

    assert recover_publication(destination)["status"] == "incomplete"
    assert (destination / "item").read_bytes() == b"candidate"


@pytest.mark.parametrize("run_id", [
    "a14aa28e-64d9-4413-a8dd-bf7937516ea9",
    "a14aa28e64d94413a8ddbf7937516ea9",
])
def test_bundle_recovery_accepts_bound_uuid_run_identity(tmp_path: Path, run_id: str) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"
    publish_bundle({"item": artifact}, destination, run_id=run_id, acceptance=ACCEPTED)

    recovered = recover_publication(destination)

    assert recovered["status"] == "completed"
    assert recovered["manifest"]["run_id"] == run_id
    assert (destination / "COMMITTED").read_text(encoding="utf-8") == run_id


@pytest.mark.parametrize("run_id", [None, "", " ", "\t\n", "\u3000"])
def test_bundle_publication_generates_uuid_for_blank_run_identity(
    tmp_path: Path,
    run_id: str | None,
) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"

    published = publish_bundle({"item": artifact}, destination, run_id=run_id, acceptance=ACCEPTED)

    assert UUID(published["run_id"]).version == 4
    assert published["manifest"]["run_id"] == published["run_id"]
    assert (destination / "COMMITTED").read_text(encoding="utf-8") == published["run_id"]
    assert recover_publication(destination)["status"] == "completed"


@pytest.mark.parametrize("field,value", [
    ("schema_version", MISSING),
    ("schema_version", None),
    ("schema_version", "99.0"),
    ("schema_version", 1.0),
    ("schema_version", {}),
    ("schema_version", []),
    ("status", MISSING),
    ("status", None),
    ("status", "staging"),
    ("status", True),
    ("status", {}),
    ("acceptance", MISSING),
    ("acceptance", None),
    ("acceptance", []),
    ("acceptance", True),
    ("acceptance", {}),
    ("acceptance", {"accepted": False}),
    ("acceptance", {"accepted": 1}),
    ("acceptance", {"accepted": "true"}),
    ("size", MISSING),
    ("size", None),
    ("size", -1),
    ("size", 2),
    ("size", True),
    ("size", 1.0),
    ("size", "1"),
    ("size", {}),
    ("size", []),
])
def test_bundle_recovery_requires_strict_completion_proof(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"x")
    destination = tmp_path / "published"
    publish_bundle({"item": artifact}, destination, run_id="proof-run", acceptance=ACCEPTED)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["files"]["item"] if field == "size" else manifest
    if value is MISSING:
        record.pop(field)
    else:
        record[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert recover_publication(destination)["status"] == "incomplete"
    assert (destination / "item").read_bytes() == b"x"


@pytest.mark.parametrize("payload", [b"", b"x"])
def test_bundle_completion_proof_accepts_exact_nonnegative_integer_sizes(
    tmp_path: Path,
    payload: bytes,
) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(payload)
    destination = tmp_path / "published"
    publish_bundle({"item": artifact}, destination, acceptance=ACCEPTED)

    recovered = recover_publication(destination)

    assert recovered["status"] == "completed"
    assert recovered["manifest"]["files"]["item"]["size"] == len(payload)


@pytest.mark.parametrize("tampering", [
    "absolute", "traversal", "different_name", "dot_alias",
    "symlink_file", "symlink_parent", "symlink_inside",
    "logical_alias", "logical_case_collision", "logical_reserved",
    "manifest_symlink", "marker_symlink", "files_list", "entry_list",
])
def test_bundle_recovery_rejects_untrusted_manifest_paths_without_reading_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampering: str,
) -> None:
    artifact = tmp_path / "candidate.bin"
    artifact.write_bytes(b"candidate")
    destination = tmp_path / "published"
    publish_bundle({"nested/item": artifact}, destination, run_id="bound-run", acceptance=ACCEPTED)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["files"]["nested/item"]
    if tampering == "absolute":
        record["path"] = str(artifact)
    elif tampering == "traversal":
        record["path"] = "../candidate.bin"
    elif tampering == "different_name":
        (destination / "different").write_bytes(b"candidate")
        record["path"] = "different"
    elif tampering == "dot_alias":
        record["path"] = "nested/./item"
    elif tampering in {"symlink_file", "symlink_inside"}:
        item_path = destination / "nested" / "item"
        item_path.unlink()
        if tampering == "symlink_inside":
            artifact = destination / "different"
            artifact.write_bytes(b"candidate")
        item_path.symlink_to(artifact)
    elif tampering == "symlink_parent":
        (destination / "nested" / "item").unlink()
        (destination / "nested").rmdir()
        external = tmp_path / "external"
        external.mkdir()
        (external / "item").write_bytes(b"candidate")
        (destination / "nested").symlink_to(external, target_is_directory=True)
    elif tampering == "logical_alias":
        manifest["files"]["./nested/item"] = dict(record, path="./nested/item")
    elif tampering == "logical_case_collision":
        manifest["files"]["NESTED/ITEM"] = dict(record, path="NESTED/ITEM")
    elif tampering == "logical_reserved":
        manifest["files"] = {"COMMITTED/child": record}
    elif tampering == "files_list":
        manifest["files"] = []
    elif tampering == "entry_list":
        manifest["files"]["nested/item"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if tampering in {"manifest_symlink", "marker_symlink"}:
        controlled = manifest_path if tampering == "manifest_symlink" else destination / "COMMITTED"
        external = tmp_path / "external-control"
        external.write_bytes(controlled.read_bytes())
        controlled.unlink()
        controlled.symlink_to(external)
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text
    outside_reads: list[Path] = []

    def tracked_read_bytes(path):
        try:
            path.resolve().relative_to(destination)
        except ValueError:
            outside_reads.append(path)
        return real_read_bytes(path)

    def tracked_read_text(path, *args, **kwargs):
        try:
            path.resolve().relative_to(destination)
        except ValueError:
            outside_reads.append(path)
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    monkeypatch.setattr(Path, "read_text", tracked_read_text)

    recovered = recover_publication(destination)

    assert recovered["status"] == "incomplete"
    assert outside_reads == []


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


def test_publication_replace_failure_restores_every_existing_bundle_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import publication

    source_ast = tmp_path / "candidate-source.json"
    docx = tmp_path / "candidate.docx"
    source_ast.write_bytes(b"new-source")
    docx.write_bytes(b"new-docx")
    destination = tmp_path / "published"
    destination.mkdir()
    (destination / "source_ast").write_bytes(b"old-source")
    (destination / "docx").write_bytes(b"old-docx")
    (destination / "manifest.json").write_bytes(b"old-manifest")
    (destination / "COMMITTED").write_bytes(b"old-marker")
    real_replace = publication.os.replace
    failed = False

    def fail_commit(source: Path | str, target: Path | str) -> None:
        nonlocal failed
        target_path = Path(target)
        source_path = Path(source)
        if not failed and (
            target_path == destination
            or target_path == destination / "docx" and ".staging" in str(source_path)
        ):
            failed = True
            raise OSError("injected bundle replace failure")
        real_replace(source, target)

    monkeypatch.setattr(publication.os, "replace", fail_commit)

    with pytest.raises(PublicationError, match="injected bundle replace failure"):
        publish_bundle(
            {"source_ast": source_ast, "docx": docx},
            destination,
            run_id="run-atomic",
            acceptance=ACCEPTED,
        )

    assert (destination / "source_ast").read_bytes() == b"old-source"
    assert (destination / "docx").read_bytes() == b"old-docx"
    assert (destination / "manifest.json").read_bytes() == b"old-manifest"
    assert (destination / "COMMITTED").read_bytes() == b"old-marker"


def test_formal_file_commit_recovers_old_generation_after_process_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import publication

    formal = tmp_path / "formal.docx"
    report = tmp_path / "report.json"
    formal.write_bytes(b"old-formal")
    report.write_bytes(b"old-report")
    real_replace = publication.os.replace
    interrupted = False

    def interrupt_after_first_install(source: Path | str, target: Path | str) -> None:
        nonlocal interrupted
        real_replace(source, target)
        if (
            not interrupted
            and Path(target) == formal
            and ".staging-" in Path(source).name
        ):
            interrupted = True
            raise SystemExit("injected formal publication interruption")

    monkeypatch.setattr(publication.os, "replace", interrupt_after_first_install)
    with pytest.raises(SystemExit, match="formal publication interruption"):
        publish_files_atomically({formal: b"new-formal", report: b"new-report"})
    monkeypatch.setattr(publication.os, "replace", real_replace)

    recovered = recover_pending_publications([formal, report])

    assert recovered["status"] == "recovered"
    assert formal.read_bytes() == b"old-formal"
    assert report.read_bytes() == b"old-report"
    assert not list(tmp_path.glob(".wxdf-publication-*.journal"))


@pytest.mark.parametrize("alias_kind", ["relative", "dotdot", "symlink", "symlink_parent"])
@pytest.mark.parametrize("failure_boundary", ["replace", "rollback"])
@pytest.mark.parametrize("with_untrusted_journal", [False, True])
def test_canonical_duplicate_targets_fail_before_recovery_or_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alias_kind: str,
    failure_boundary: str,
    with_untrusted_journal: bool,
) -> None:
    from wxdf import publication

    monkeypatch.chdir(tmp_path)
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"old-formal")
    if alias_kind == "relative":
        alias = Path("formal.docx")
    elif alias_kind == "dotdot":
        (tmp_path / "nested").mkdir()
        alias = tmp_path / "nested" / ".." / "formal.docx"
    elif alias_kind == "symlink":
        alias = tmp_path / "alias.docx"
        alias.symlink_to(formal)
    else:
        directory_alias = tmp_path / "alias-dir"
        directory_alias.symlink_to(tmp_path, target_is_directory=True)
        alias = directory_alias / "formal.docx"
    poison = tmp_path / (".wxdf-publication-" + "e" * 32 + ".journal")
    if with_untrusted_journal:
        poison.write_text("untrusted journal", encoding="utf-8")
    replace_attempts: list[tuple[Path, Path]] = []
    real_replace = publication.os.replace

    def fail_replace(source, target):
        replace_attempts.append((Path(source), Path(target)))
        if failure_boundary == "replace" or ".rollback-" in Path(source).name:
            raise OSError(f"injected {failure_boundary} failure")
        real_replace(source, target)

    monkeypatch.setattr(publication.os, "replace", fail_replace)

    with pytest.raises(PublicationError, match="targets must be unique"):
        publish_files_atomically({formal: b"new-first", alias: b"new-alias"})

    assert formal.read_bytes() == b"old-formal"
    if with_untrusted_journal:
        assert poison.read_text(encoding="utf-8") == "untrusted journal"
    else:
        assert not list(tmp_path.glob(".wxdf-publication-*.journal"))
    assert replace_attempts == []
    assert not list(tmp_path.glob(".*.staging-*"))


@pytest.mark.parametrize("failure_boundary", ["replace", "rollback"])
@pytest.mark.parametrize("with_untrusted_journal", [False, True])
def test_samefile_targets_fail_before_staging_recovery_or_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_boundary: str,
    with_untrusted_journal: bool,
) -> None:
    from wxdf import publication

    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"old-formal")
    alias = tmp_path / "FORMAL.DOCX"
    # Exercise the actual case alias on insensitive volumes and the same
    # physical-file boundary through a hard link on sensitive volumes.
    if not alias.exists():
        os.link(formal, alias)
    if str(formal.resolve()) == str(alias.resolve()):
        alias = tmp_path / "linked.docx"
        os.link(formal, alias)
    assert formal.samefile(alias)
    assert str(formal.resolve()) != str(alias.resolve())
    poison = tmp_path / (".wxdf-publication-" + "e" * 32 + ".journal")
    if with_untrusted_journal:
        poison.write_text("untrusted journal", encoding="utf-8")
    replace_attempts: list[tuple[Path, Path]] = []
    real_replace = publication.os.replace

    def fail_replace(source, target):
        source, target = Path(source), Path(target)
        replace_attempts.append((source, target))
        if ".staging-" in source.name and target == alias:
            raise OSError("injected replace failure")
        if failure_boundary == "rollback" and ".rollback-" in source.name:
            raise OSError("injected rollback failure")
        real_replace(source, target)

    monkeypatch.setattr(publication.os, "replace", fail_replace)

    with pytest.raises(PublicationError, match="targets must be unique"):
        publish_files_atomically({formal: b"new-first", alias: b"new-alias"})

    assert formal.read_bytes() == alias.read_bytes() == b"old-formal"
    assert replace_attempts == []
    assert not list(tmp_path.glob(".*.staging-*"))
    assert not list(tmp_path.glob(".*.rollback-*"))
    journals = list(tmp_path.glob(".wxdf-publication-*.journal"))
    assert journals == ([poison] if with_untrusted_journal else [])
    if with_untrusted_journal:
        assert poison.read_text(encoding="utf-8") == "untrusted journal"


@pytest.mark.parametrize("layout", ["flat", "missing_parent", "existing_parent_alias"])
@pytest.mark.parametrize("names", [
    ("formal.docx", "FORMAL.DOCX"),
    ("stra\N{LATIN SMALL LETTER SHARP S}e.docx", "strasse.docx"),
    ("first.docx", "second.docx"),
])
def test_missing_generation_targets_follow_actual_filesystem_identity(
    tmp_path: Path,
    layout: str,
    names: tuple[str, str],
) -> None:
    probe = tmp_path / names[0]
    probe.write_bytes(b"identity-probe")
    alias_probe = tmp_path / names[1]
    aliases = alias_probe.exists() and probe.samefile(alias_probe)
    probe.unlink()
    if layout == "flat":
        formal, alias = (tmp_path / name for name in names)
    elif layout == "missing_parent":
        formal, alias = (tmp_path / name / "item" for name in names)
    else:
        parent = tmp_path / names[0]
        parent.mkdir()
        formal, alias = (tmp_path / name / "item" for name in names)

    if aliases:
        with pytest.raises(PublicationError, match="targets must be unique"):
            publish_files_atomically({str(formal): b"new-first", str(alias): b"new-alias"})
        assert not formal.exists()
        assert not alias.exists()
    else:
        publish_files_atomically({str(formal): b"new-first", str(alias): b"new-alias"})
        assert formal.read_bytes() == b"new-first"
        assert alias.read_bytes() == b"new-alias"
        assert not formal.samefile(alias)
    assert not list(tmp_path.rglob(".*.staging-*"))
    assert not list(tmp_path.rglob(".*.rollback-*"))
    assert not list(tmp_path.rglob(".wxdf-publication-*.journal"))
    assert not list(tmp_path.rglob(".wxdf-identity-*"))


def test_recovery_rejects_duplicate_journal_targets_before_mutation(tmp_path: Path) -> None:
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"old-formal")
    transaction_id = "f" * 32
    backup = tmp_path / f".{formal.name}.rollback-{transaction_id}"
    backup.write_bytes(b"untrusted-backup")
    journal_path = tmp_path / f".wxdf-publication-{transaction_id}.journal"
    entry = {
        "target": str(formal),
        "staged": str(tmp_path / f".{formal.name}.staging-{transaction_id}"),
        "backup": str(backup),
        "had_original": True,
    }
    journal_path.write_text(json.dumps({
        "schema_version": "1.0",
        "transaction_id": transaction_id,
        "state": "prepared",
        "entries": [entry, entry],
        "journal_paths": [str(journal_path)],
    }), encoding="utf-8")

    with pytest.raises(PublicationError, match="targets must be unique"):
        recover_pending_publications(formal)

    assert formal.read_bytes() == b"old-formal"
    assert backup.read_bytes() == b"untrusted-backup"
    assert journal_path.exists()


def test_recovery_rejects_samefile_journal_targets_before_mutation(tmp_path: Path) -> None:
    formal = tmp_path / "formal.docx"
    formal.write_bytes(b"old-formal")
    alias = tmp_path / "FORMAL.DOCX"
    if not alias.exists():
        os.link(formal, alias)
    if str(formal.resolve()) == str(alias.resolve()):
        alias = tmp_path / "linked.docx"
        os.link(formal, alias)
    assert formal.samefile(alias)
    transaction_id = "c" * 32
    entries = []
    for target in (formal, alias):
        backup = tmp_path / f".{target.name}.rollback-{transaction_id}"
        backup.write_bytes(b"untrusted-backup")
        entries.append({
            "target": str(target),
            "staged": str(tmp_path / f".{target.name}.staging-{transaction_id}"),
            "backup": str(backup),
            "had_original": True,
        })
    journal_path = tmp_path / f".wxdf-publication-{transaction_id}.journal"
    journal_bytes = json.dumps({
        "schema_version": "1.0",
        "transaction_id": transaction_id,
        "state": "prepared",
        "entries": entries,
        "journal_paths": [str(journal_path)],
    }).encode("utf-8")
    journal_path.write_bytes(journal_bytes)

    with pytest.raises(PublicationError, match="targets must be unique"):
        recover_pending_publications([formal, alias])

    assert formal.read_bytes() == alias.read_bytes() == b"old-formal"
    assert journal_path.read_bytes() == journal_bytes
    assert all(Path(entry["backup"]).read_bytes() == b"untrusted-backup" for entry in entries)


@pytest.mark.parametrize("symlink_field", ["staged", "backup"])
def test_recovery_rejects_generation_payload_symlinks_outside_trusted_root(
    tmp_path: Path,
    symlink_field: str,
) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    formal = trusted / "formal.docx"
    formal.write_bytes(b"old-formal")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside-data")
    transaction_id = "a" * 32
    staged = trusted / f".{formal.name}.staging-{transaction_id}"
    backup = trusted / f".{formal.name}.rollback-{transaction_id}"
    paths = {"staged": staged, "backup": backup}
    paths[symlink_field].symlink_to(outside)
    journal_path = trusted / f".wxdf-publication-{transaction_id}.journal"
    journal_path.write_text(json.dumps({
        "schema_version": "1.0",
        "transaction_id": transaction_id,
        "state": "prepared",
        "entries": [{
            "target": str(formal),
            "staged": str(staged),
            "backup": str(backup),
            "had_original": True,
        }],
        "journal_paths": [str(journal_path)],
    }), encoding="utf-8")

    with pytest.raises(PublicationError, match="trusted publication root"):
        recover_pending_publications(formal)

    assert formal.read_bytes() == b"old-formal"
    assert not formal.is_symlink()
    assert outside.read_bytes() == b"outside-data"
    assert paths[symlink_field].is_symlink()


def test_cross_directory_recovery_observes_committed_generation_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import publication

    first_dir = tmp_path / "a"
    second_dir = tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()
    formal = first_dir / "formal.docx"
    report = second_dir / "report.json"
    formal.write_bytes(b"old-formal")
    report.write_bytes(b"old-report")
    real_replace = publication.os.replace
    interrupted = False

    def interrupt_after_first_committed_marker(source: Path | str, target: Path | str) -> None:
        nonlocal interrupted
        real_replace(source, target)
        target_path = Path(target)
        if (
            not interrupted
            and target_path.name.startswith(".wxdf-publication-")
            and target_path.suffix == ".journal"
            and json.loads(target_path.read_text(encoding="utf-8"))["state"] == "committed"
        ):
            interrupted = True
            raise SystemExit("injected committed marker interruption")

    monkeypatch.setattr(publication.os, "replace", interrupt_after_first_committed_marker)
    with pytest.raises(SystemExit, match="committed marker interruption"):
        publish_files_atomically({formal: b"new-formal", report: b"new-report"})
    monkeypatch.setattr(publication.os, "replace", real_replace)

    recovered = recover_pending_publications([formal, report])

    assert recovered["status"] == "completed"
    assert formal.read_bytes() == b"new-formal"
    assert report.read_bytes() == b"new-report"
    assert not list(first_dir.glob(".wxdf-publication-*.journal"))
    assert not list(second_dir.glob(".wxdf-publication-*.journal"))


def test_publication_recovery_rejects_poisoned_paths_outside_trusted_root(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    formal = trusted / "formal.docx"
    formal.write_bytes(b"old-formal")
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"safe")
    poison_backup = tmp_path / "poison-backup"
    poison_backup.write_bytes(b"poison")
    transaction_id = "a" * 32
    journal_path = trusted / f".wxdf-publication-{transaction_id}.journal"
    journal_path.write_text(
        json.dumps({
            "schema_version": "1.0",
            "transaction_id": transaction_id,
            "state": "prepared",
            "entries": [{
                "target": str(victim.resolve()),
                "staged": str((tmp_path / "poison-staged").resolve()),
                "backup": str(poison_backup.resolve()),
                "had_original": True,
            }],
            "journal_paths": [str(journal_path.resolve())],
        }),
        encoding="utf-8",
    )

    with pytest.raises(PublicationError, match="trusted publication root"):
        recover_publication(formal)

    assert victim.read_bytes() == b"safe"
    assert poison_backup.read_bytes() == b"poison"


def test_publication_recovery_rejects_untrusted_target_in_same_root(
    tmp_path: Path,
) -> None:
    formal = tmp_path / "formal.docx"
    victim = tmp_path / "unrelated.docx"
    victim.write_bytes(b"do-not-touch")
    transaction_id = "d" * 32
    journal_path = tmp_path / f".wxdf-publication-{transaction_id}.journal"
    journal_path.write_text(
        json.dumps({
            "schema_version": "1.0",
            "transaction_id": transaction_id,
            "state": "prepared",
            "entries": [{
                "target": str(victim.resolve()),
                "staged": str((tmp_path / f".{victim.name}.staging-{transaction_id}").resolve()),
                "backup": str((tmp_path / f".{victim.name}.rollback-{transaction_id}").resolve()),
                "had_original": False,
            }],
            "journal_paths": [str(journal_path.resolve())],
        }),
        encoding="utf-8",
    )

    with pytest.raises(PublicationError, match="trusted publication target"):
        recover_publication(formal)

    assert victim.read_bytes() == b"do-not-touch"


def test_publication_recovery_removes_only_target_shaped_orphan_staging(
    tmp_path: Path,
) -> None:
    target = tmp_path / "formal.docx"
    target.write_bytes(b"formal")
    transaction_id = "b" * 32
    orphan = tmp_path / f".{target.name}.staging-{transaction_id}"
    unrelated = tmp_path / f".other.docx.staging-{transaction_id}"
    orphan.write_bytes(b"orphan")
    unrelated.write_bytes(b"unrelated")

    recovered = recover_pending_publications(target)

    assert recovered["status"] == "clean"
    assert not orphan.exists()
    assert unrelated.read_bytes() == b"unrelated"


def test_bundle_commit_recovers_old_generation_after_process_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wxdf import publication

    first = tmp_path / "first.docx"
    first.write_bytes(b"first")
    destination = tmp_path / "published"
    publish_bundle({"docx": first}, destination, run_id="old-run", acceptance=ACCEPTED)
    second = tmp_path / "second.docx"
    second.write_bytes(b"second")
    real_replace = publication.os.replace
    interrupted = False

    def interrupt_after_bundle_install(source: Path | str, target: Path | str) -> None:
        nonlocal interrupted
        real_replace(source, target)
        if (
            not interrupted
            and Path(target) == destination
            and ".staging-" in Path(source).name
        ):
            interrupted = True
            raise SystemExit("injected bundle publication interruption")

    monkeypatch.setattr(publication.os, "replace", interrupt_after_bundle_install)
    with pytest.raises(SystemExit, match="bundle publication interruption"):
        publish_bundle({"docx": second}, destination, run_id="new-run", acceptance=ACCEPTED)
    monkeypatch.setattr(publication.os, "replace", real_replace)

    recovered = recover_publication(destination)

    assert recovered["status"] == "completed"
    assert recovered["manifest"]["run_id"] == "old-run"
    assert (destination / "docx").read_bytes() == b"first"
    assert not list(tmp_path.glob(".wxdf-publication-*.journal"))
