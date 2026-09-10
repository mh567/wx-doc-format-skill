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


def validate_acceptance(acceptance: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return an accepted record before any publication path is mutated."""

    if not isinstance(acceptance, Mapping) or acceptance.get("accepted") is not True:
        raise PublicationError("publication requires an accepted candidate record")
    return dict(acceptance)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_files_atomically(payloads: Mapping[Path | str, bytes]) -> None:
    """Replace a set of formal files as one rollback protected commit."""

    targets = [Path(value) for value in payloads]
    if len(targets) != len(set(targets)):
        raise PublicationError("publication targets must be unique")
    commit_id = uuid.uuid4().hex
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    installed: set[Path] = set()
    try:
        for target, payload in ((Path(path), value) for path, value in payloads.items()):
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.staging-{commit_id}")
            temporary.write_bytes(payload)
            staged[target] = temporary
        for target in targets:
            if target.exists():
                backup = target.with_name(f".{target.name}.rollback-{commit_id}")
                os.replace(target, backup)
                backups[target] = backup
            os.replace(staged[target], target)
            installed.add(target)
    except Exception as exc:
        rollback_errors: list[str] = []
        for target in reversed(targets):
            try:
                if target in installed and target.exists():
                    target.unlink()
                backup = backups.get(target)
                if backup is not None and backup.exists():
                    os.replace(backup, target)
            except Exception as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        detail = f"; rollback failed for {', '.join(rollback_errors)}" if rollback_errors else ""
        raise PublicationError(f"atomic publication failed: {exc}{detail}") from exc
    finally:
        for temporary in staged.values():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    for backup in backups.values():
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass


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
    run_id = run_id or uuid.uuid4().hex
    required = required or tuple(artifacts)
    missing = [name for name in required if name not in artifacts or not Path(artifacts[name]).is_file()]
    if missing:
        raise PublicationError(f"missing artifact: {missing[0]}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    commit_id = uuid.uuid4().hex
    staging = destination.parent / f".{destination.name}.staging-{commit_id}"
    backup = destination.parent / f".{destination.name}.rollback-{commit_id}"
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
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except Exception:
            if backup.exists():
                os.replace(backup, destination)
            raise
    except Exception as exc:
        if isinstance(exc, PublicationError):
            raise
        raise PublicationError(f"publication failed: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    return {"status": "completed", "run_id": run_id, "destination": str(destination), "manifest": manifest}


def recover_publication(destination: Path | str) -> dict[str, Any]:
    destination = Path(destination)
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
