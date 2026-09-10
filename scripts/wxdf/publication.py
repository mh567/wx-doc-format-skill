"""Versioned multi-artifact publication with explicit completion markers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Mapping


class PublicationError(RuntimeError):
    """Raised when a required publication artifact is missing or invalid."""


_JOURNAL_SCHEMA_VERSION = "1.0"
_JOURNAL_PATTERN = ".wxdf-publication-*.journal"


def validate_acceptance(acceptance: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return an accepted record before any publication path is mutated."""

    if not isinstance(acceptance, Mapping) or acceptance.get("accepted") is not True:
        raise PublicationError("publication requires an accepted candidate record")
    return dict(acceptance)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _journal_paths(targets: list[Path], transaction_id: str) -> list[Path]:
    parents = {target.parent.resolve() for target in targets}
    return [
        parent / f".wxdf-publication-{transaction_id}.journal"
        for parent in sorted(parents, key=str)
    ]


def _write_json_atomically(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_journal_copies(journal: Mapping[str, Any]) -> None:
    for raw_path in journal["journal_paths"]:
        _write_json_atomically(Path(raw_path), journal)


def _cleanup_transaction(journal: Mapping[str, Any]) -> None:
    for entry in journal.get("entries", []):
        for field in ("staged", "backup"):
            raw_path = entry.get(field)
            if isinstance(raw_path, str):
                _remove_path(Path(raw_path))
    for raw_path in journal.get("journal_paths", []):
        if isinstance(raw_path, str):
            Path(raw_path).unlink(missing_ok=True)


def _recover_journal(journal_path: Path) -> str:
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if journal.get("schema_version") != _JOURNAL_SCHEMA_VERSION:
            raise ValueError("unsupported publication journal version")
        entries = journal.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError("publication journal has no entries")
        state = journal.get("state")
        if state not in {"prepared", "committed"}:
            raise ValueError("publication journal state is invalid")
        journal_identity = {
            "schema_version": journal.get("schema_version"),
            "transaction_id": journal.get("transaction_id"),
            "entries": entries,
            "journal_paths": journal.get("journal_paths"),
        }
        for raw_path in journal.get("journal_paths", []):
            copy_path = Path(raw_path)
            if not copy_path.is_file():
                continue
            journal_copy = json.loads(copy_path.read_text(encoding="utf-8"))
            copy_identity = {
                "schema_version": journal_copy.get("schema_version"),
                "transaction_id": journal_copy.get("transaction_id"),
                "entries": journal_copy.get("entries"),
                "journal_paths": journal_copy.get("journal_paths"),
            }
            if copy_identity != journal_identity:
                raise ValueError("publication journal copies disagree")
            copy_state = journal_copy.get("state")
            if copy_state not in {"prepared", "committed"}:
                raise ValueError("publication journal copy state is invalid")
            if copy_state == "committed":
                state = "committed"
        if state == "committed":
            _cleanup_transaction(journal)
            return "completed"
        for entry in reversed(entries):
            target = Path(entry["target"])
            staged = Path(entry["staged"])
            backup = Path(entry["backup"])
            had_original = entry.get("had_original") is True
            if backup.exists():
                if target.exists():
                    _remove_path(target)
                os.replace(backup, target)
            elif had_original:
                if not target.exists() or not staged.exists():
                    raise ValueError(
                        f"original publication target cannot be recovered: {target}"
                    )
            elif target.exists() and not staged.exists():
                _remove_path(target)
        _cleanup_transaction(journal)
        return "recovered"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PublicationError(f"publication recovery failed: {exc}") from exc


def recover_pending_publications(path: Path | str) -> dict[str, Any]:
    """Recover interrupted publication transactions adjacent to one target."""

    target = Path(path)
    statuses: list[str] = []
    for journal_path in sorted(target.parent.glob(_JOURNAL_PATTERN)):
        if journal_path.exists():
            statuses.append(_recover_journal(journal_path))
    if "recovered" in statuses:
        return {"status": "recovered", "target": str(target)}
    if statuses:
        return {"status": "completed", "target": str(target)}
    return {"status": "clean", "target": str(target)}


def _commit_staged_paths(
    staged: Mapping[Path, Path],
    transaction_id: str,
    *,
    crash_at: str | None = None,
) -> None:
    targets = list(staged)
    entries = [
        {
            "target": str(target.resolve()),
            "staged": str(staged[target].resolve()),
            "backup": str(
                target.with_name(f".{target.name}.rollback-{transaction_id}").resolve()
            ),
            "had_original": target.exists(),
        }
        for target in targets
    ]
    journal_paths = _journal_paths(targets, transaction_id)
    journal: dict[str, Any] = {
        "schema_version": _JOURNAL_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "state": "prepared",
        "entries": entries,
        "journal_paths": [str(path) for path in journal_paths],
    }
    try:
        _write_journal_copies(journal)
    except Exception as exc:
        for journal_path in journal_paths:
            journal_path.unlink(missing_ok=True)
        raise PublicationError(f"publication journal write failed: {exc}") from exc
    try:
        if crash_at == "before_commit":
            raise PublicationError("injected failure before commit")
        for entry in entries:
            target = Path(entry["target"])
            source = Path(entry["staged"])
            backup = Path(entry["backup"])
            if entry["had_original"]:
                os.replace(target, backup)
            os.replace(source, target)
        journal["state"] = "committed"
        _write_journal_copies(journal)
    except Exception as exc:
        try:
            _recover_journal(journal_paths[0])
        except PublicationError as rollback_exc:
            raise PublicationError(
                f"atomic publication failed: {exc}; rollback failed: {rollback_exc}"
            ) from exc
        if isinstance(exc, PublicationError):
            raise
        raise PublicationError(f"atomic publication failed: {exc}") from exc
    _cleanup_transaction(journal)
    if crash_at == "after_commit":
        raise PublicationError("injected failure after commit")


def publish_files_atomically(
    payloads: Mapping[Path | str, bytes],
    *,
    crash_at: str | None = None,
) -> None:
    """Replace formal files through one crash recoverable generation commit."""

    targets = [Path(value) for value in payloads]
    if not targets:
        raise PublicationError("publication requires at least one target")
    if len(targets) != len(set(targets)):
        raise PublicationError("publication targets must be unique")
    for target in targets:
        recover_pending_publications(target)
    transaction_id = uuid.uuid4().hex
    staged: dict[Path, Path] = {}
    try:
        for target, payload in ((Path(path), value) for path, value in payloads.items()):
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.staging-{transaction_id}")
            temporary.write_bytes(payload)
            staged[target] = temporary
        _commit_staged_paths(staged, transaction_id, crash_at=crash_at)
    except Exception:
        for temporary in staged.values():
            if temporary.exists():
                _remove_path(temporary)
        raise


def publish_bundle(
    artifacts: Mapping[str, Path | str],
    destination: Path | str,
    *,
    run_id: str | None = None,
    required: tuple[str, ...] | None = None,
    acceptance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    acceptance_record = validate_acceptance(acceptance)
    destination = Path(destination)
    recover_pending_publications(destination)
    run_id = run_id or uuid.uuid4().hex
    required = required or tuple(artifacts)
    missing = [name for name in required if name not in artifacts or not Path(artifacts[name]).is_file()]
    if missing:
        raise PublicationError(f"missing artifact: {missing[0]}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    transaction_id = uuid.uuid4().hex
    staging = destination.parent / f".{destination.name}.staging-{transaction_id}"
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": "staging",
        "acceptance": acceptance_record,
        "files": {},
    }
    try:
        staging.mkdir(parents=True, exist_ok=False)
        for name in required:
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise PublicationError(f"invalid artifact name: {name}")
            source = Path(artifacts[name])
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            manifest["files"][name] = {
                "path": name,
                "size": target.stat().st_size,
                "sha256": _sha256(target),
            }
        manifest["status"] = "completed"
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (staging / "COMMITTED").write_text(run_id, encoding="utf-8")
        _commit_staged_paths({destination: staging}, transaction_id)
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, PublicationError):
            raise
        raise PublicationError(f"publication failed: {exc}") from exc
    return {"status": "completed", "run_id": run_id, "destination": str(destination), "manifest": manifest}


def recover_publication(destination: Path | str) -> dict[str, Any]:
    destination = Path(destination)
    pending = recover_pending_publications(destination)
    if destination.is_file():
        return {"status": pending["status"], "destination": str(destination)}
    marker = destination / "COMMITTED"
    manifest_path = destination / "manifest.json"
    if not marker.exists() or not manifest_path.exists():
        return {"status": "incomplete", "destination": str(destination)}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "incomplete", "destination": str(destination)}
    for item in manifest.get("files", {}).values():
        path = destination / item["path"]
        if not path.is_file() or _sha256(path) != item.get("sha256"):
            return {"status": "incomplete", "destination": str(destination), "reason": "artifact_mismatch"}
    return {"status": "completed", "destination": str(destination), "manifest": manifest}
