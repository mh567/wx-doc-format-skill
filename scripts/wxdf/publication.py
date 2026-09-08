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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publish_bundle(
    artifacts: Mapping[str, Path | str],
    destination: Path | str,
    *,
    run_id: str | None = None,
    required: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    destination = Path(destination)
    run_id = run_id or uuid.uuid4().hex
    required = required or tuple(artifacts)
    missing = [name for name in required if name not in artifacts or not Path(artifacts[name]).is_file()]
    if missing:
        raise PublicationError(f"missing artifact: {missing[0]}")
    staging = destination / ".staging" / run_id
    staging.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"schema_version": "1.0", "run_id": run_id, "status": "staging", "files": {}}
    for name in required:
        source = Path(artifacts[name])
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest["files"][name] = {"path": name, "size": target.stat().st_size, "sha256": _sha256(target)}
    manifest_path = staging / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["status"] = "completed"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    destination.mkdir(parents=True, exist_ok=True)
    for name in required:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging / name, target)
    os.replace(manifest_path, destination / "manifest.json")
    (destination / "COMMITTED").write_text(run_id, encoding="utf-8")
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
