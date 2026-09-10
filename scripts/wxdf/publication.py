"""Versioned multi-artifact publication with explicit completion markers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping


class PublicationError(RuntimeError):
    """Raised when a required publication artifact is missing or invalid."""


_JOURNAL_SCHEMA_VERSION = "1.0"
_JOURNAL_PATTERN = ".wxdf-publication-*.journal"
_RESERVED_BUNDLE_NAMES = {"manifest.json", "committed"}
_TRANSACTION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def validate_acceptance(acceptance: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return an accepted record before any publication path is mutated."""

    if not isinstance(acceptance, Mapping) or acceptance.get("accepted") is not True:
        raise PublicationError("publication requires an accepted candidate record")
    return dict(acceptance)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle_artifact_paths(names: Iterable[str]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    identities: set[tuple[str, ...]] = set()
    for name in names:
        if not isinstance(name, str) or any(character in name for character in ("\\", ":", "\0")):
            raise PublicationError(f"invalid artifact name: {name}")
        parts = name.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise PublicationError(f"invalid artifact name: {name}")
        identity = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
        if any(part in _RESERVED_BUNDLE_NAMES for part in identity):
            raise PublicationError(f"reserved artifact name: {name}")
        if any(
            identity[:len(existing)] == existing or existing[:len(identity)] == identity
            for existing in identities
        ):
            raise PublicationError(f"colliding artifact name: {name}")
        identities.add(identity)
        paths[name] = Path(*parts)
    return paths


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


def _validate_journal_paths(
    journal: Mapping[str, Any],
    journal_path: Path,
    trusted_targets: set[Path],
) -> dict[str, Any]:
    trusted_roots = {target.parent for target in trusted_targets}
    if journal.get("schema_version") != _JOURNAL_SCHEMA_VERSION:
        raise ValueError("unsupported publication journal version")
    transaction_id = journal.get("transaction_id")
    if not isinstance(transaction_id, str) or _TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is None:
        raise ValueError("publication journal transaction ID is invalid")
    expected_name = f".wxdf-publication-{transaction_id}.journal"
    discovered = journal_path.resolve()
    if discovered.name != expected_name or discovered.parent not in trusted_roots:
        raise ValueError("publication journal is outside a trusted publication root")
    raw_entries = journal.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("publication journal has no entries")
    entries: list[dict[str, Any]] = []
    entry_roots: set[Path] = set()
    entry_targets: set[Path] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise ValueError("publication journal entry is invalid")
        target = Path(str(raw_entry.get("target") or ""))
        if not target.is_absolute() or target != target.resolve() or target not in trusted_targets:
            raise ValueError(
                "publication target is not a trusted publication target or is outside a trusted publication root"
            )
        if target in entry_targets:
            raise ValueError("publication targets must be unique")
        entry_targets.add(target)
        expected_staged = target.with_name(f".{target.name}.staging-{transaction_id}")
        expected_backup = target.with_name(f".{target.name}.rollback-{transaction_id}")
        if Path(str(raw_entry.get("staged") or "")) != expected_staged:
            raise ValueError("publication staged path does not match its transaction")
        if Path(str(raw_entry.get("backup") or "")) != expected_backup:
            raise ValueError("publication backup path does not match its transaction")
        if any(path.resolve() != path for path in (expected_staged, expected_backup)):
            raise ValueError("publication payload is outside its trusted publication root")
        entries.append({
            "target": str(target),
            "staged": str(expected_staged),
            "backup": str(expected_backup),
            "had_original": raw_entry.get("had_original") is True,
        })
        entry_roots.add(target.parent)
    expected_journal_paths = [
        root / expected_name
        for root in sorted(entry_roots, key=str)
    ]
    raw_journal_paths = journal.get("journal_paths")
    if not isinstance(raw_journal_paths, list) or [Path(str(path)) for path in raw_journal_paths] != expected_journal_paths:
        raise ValueError("publication journal copies do not match trusted roots")
    if discovered not in expected_journal_paths:
        raise ValueError("publication journal is outside a trusted publication root")
    state = journal.get("state")
    if state not in {"prepared", "committed"}:
        raise ValueError("publication journal state is invalid")
    return {
        "schema_version": _JOURNAL_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "state": state,
        "entries": entries,
        "journal_paths": [str(path) for path in expected_journal_paths],
    }


def _recover_journal(journal_path: Path, trusted_targets: set[Path]) -> str:
    try:
        journal = _validate_journal_paths(
            json.loads(journal_path.read_text(encoding="utf-8")),
            journal_path,
            trusted_targets,
        )
        entries = journal["entries"]
        state = journal.get("state")
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
            journal_copy = _validate_journal_paths(
                json.loads(copy_path.read_text(encoding="utf-8")),
                copy_path,
                trusted_targets,
            )
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


def recover_pending_publications(
    path: Path | str | Iterable[Path | str],
) -> dict[str, Any]:
    """Recover interrupted transactions only within caller-trusted roots."""

    if isinstance(path, (Path, str)):
        targets = [Path(path)]
    else:
        targets = [Path(item) for item in path]
    if not targets:
        raise PublicationError("publication recovery requires a trusted target")
    trusted_targets = {target.resolve() for target in targets}
    trusted_roots = {target.parent for target in trusted_targets}
    statuses: list[str] = []
    journal_paths = sorted({
        journal_path
        for root in trusted_roots
        for journal_path in root.glob(_JOURNAL_PATTERN)
    }, key=str)
    for journal_path in journal_paths:
        if journal_path.exists():
            statuses.append(_recover_journal(journal_path, trusted_targets))
    for target in trusted_targets:
        prefix = f".{target.name}.staging-"
        for candidate in target.parent.glob(prefix + "*"):
            transaction_id = candidate.name.removeprefix(prefix)
            if _TRANSACTION_ID_PATTERN.fullmatch(transaction_id) is not None:
                _remove_path(candidate)
    target = targets[0]
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
    targets = [target.resolve() for target in staged]
    if len(targets) != len(set(targets)):
        raise PublicationError("publication targets must be unique")
    entries = [
        {
            "target": str(target.resolve()),
            "staged": str(staged[target].resolve()),
            "backup": str(
                target.with_name(f".{target.name}.rollback-{transaction_id}").resolve()
            ),
            "had_original": target.exists(),
        }
        for target in staged
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
        journal = _validate_journal_paths(journal, journal_paths[0], set(targets))
    except ValueError as exc:
        raise PublicationError(f"invalid publication generation: {exc}") from exc
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
            _recover_journal(
                journal_paths[0],
                {target.resolve() for target in targets},
            )
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

    targets = [Path(value).resolve() for value in payloads]
    if not targets:
        raise PublicationError("publication requires at least one target")
    if len(targets) != len(set(targets)):
        raise PublicationError("publication targets must be unique")
    recover_pending_publications(targets)
    transaction_id = uuid.uuid4().hex
    staged: dict[Path, Path] = {}
    try:
        for target, payload in zip(targets, payloads.values()):
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
    destination = Path(destination).resolve()
    run_id = run_id or uuid.uuid4().hex
    required = required or tuple(artifacts)
    _bundle_artifact_paths(artifacts)
    artifact_paths = _bundle_artifact_paths(required)
    missing = [name for name in required if name not in artifacts or not Path(artifacts[name]).is_file()]
    if missing:
        raise PublicationError(f"missing artifact: {missing[0]}")
    recover_pending_publications(destination)
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
            source = Path(artifacts[name])
            target = staging / artifact_paths[name]
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
    try:
        root = destination.resolve()
        marker = root / "COMMITTED"
        manifest_path = root / "manifest.json"
        if any(
            path.resolve() != path or not path.is_file()
            for path in (marker, manifest_path)
        ):
            return {"status": "incomplete", "destination": str(destination)}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        marker_run_id = marker.read_text(encoding="utf-8")
    except (OSError, ValueError, RuntimeError):
        return {"status": "incomplete", "destination": str(destination)}
    if not isinstance(manifest, Mapping):
        return {"status": "incomplete", "destination": str(destination)}
    if marker_run_id != manifest.get("run_id"):
        return {
            "status": "incomplete",
            "destination": str(destination),
            "reason": "marker_mismatch",
        }
    try:
        files = manifest.get("files")
        if not isinstance(files, Mapping):
            raise PublicationError("invalid artifact manifest")
        artifact_paths = _bundle_artifact_paths(files)
        for name, relative in artifact_paths.items():
            item = files[name]
            if not isinstance(item, Mapping) or item.get("path") != name:
                raise PublicationError("artifact path does not match its logical name")
            path = root / relative
            if path.resolve() != path or not path.is_file():
                raise PublicationError("artifact path escapes its bundle location")
            if _sha256(path) != item.get("sha256"):
                raise PublicationError("artifact digest mismatch")
    except (PublicationError, OSError, ValueError, RuntimeError):
        return {"status": "incomplete", "destination": str(destination), "reason": "artifact_mismatch"}
    return {"status": "completed", "destination": str(destination), "manifest": manifest}
