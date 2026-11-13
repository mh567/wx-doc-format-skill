"""Install only the current interpreter's wheels from the offline bundle.

The target machine provides a standard CPython interpreter and nothing else.
Wheels are expanded into a private directory that is added to sys.path, so no
venv, pip or ensurepip is involved.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

try:
    import fcntl
except ImportError:  # Windows has no fcntl; installs there run without a lock.
    fcntl = None

STATE_FILE = ".wxdf-state.json"
MANIFEST = "manifest.json"
BUNDLE = "wheels.tar.xz"


def _wheel_name(tag: str, name: str) -> str:
    if not tag or tag.startswith(".") or "/" in tag or "\\" in tag:
        raise ValueError("invalid wheel tag: " + tag)
    if not name.endswith(".whl") or name.startswith(".") or "/" in name or "\\" in name:
        raise ValueError("invalid wheel filename: " + name)
    return "{}/{}".format(tag, name)


def _detect_platform(system: str, machine: str) -> str:
    machine = (machine or "").lower()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    if system.startswith("linux") and machine in {"arm64", "aarch64"}:
        return "linux-arm64"
    raise ValueError("unsupported platform: {} {}".format(system, machine))


def _platform_id() -> str:
    return _detect_platform(sys.platform, platform.machine())


def _plan(wheelhouse: Path, tag: str) -> tuple[dict[str, str], str]:
    manifest = json.loads((wheelhouse / MANIFEST).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("invalid wheelhouse manifest")
    declared_platform = manifest.get("platform")
    if not isinstance(declared_platform, str):
        raise ValueError("invalid wheelhouse manifest")
    current_platform = _platform_id()
    if declared_platform != current_platform:
        raise ValueError(
            "the offline package targets {} but this system is {}".format(
                declared_platform, current_platform,
            )
        )
    environments = manifest.get("environments")
    if not isinstance(environments, dict) or tag not in environments:
        raise ValueError("the bundled wheelhouse does not cover Python " + tag)
    digests = {}
    records = manifest.get("wheels")
    if not isinstance(records, list):
        raise ValueError("invalid wheelhouse manifest")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("invalid wheel record")
        relative = _wheel_name(record["python_tag"], record["wheel"])
        if relative in digests:
            raise ValueError("duplicate wheel record: " + relative)
        digests[relative] = record["sha256"]
    selection = environments[tag]
    if not isinstance(selection, list):
        raise ValueError("invalid environment selection")
    plan = {}
    for relative in selection:
        if relative in plan:
            raise ValueError("duplicate wheel selection: " + relative)
        if relative not in digests:
            raise ValueError("selection names an unknown wheel: " + relative)
        plan[relative] = digests[relative]
    digest = hashlib.sha256(
        json.dumps(sorted(plan.items()), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan, digest


def _bundle_digest(wheelhouse: Path) -> str:
    digest = hashlib.sha256()
    with (wheelhouse / BUNDLE).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("unsafe wheel member: " + name)
    return path


def _expand_wheel(payload: bytes, destination: Path, top_level: set) -> None:
    with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
        for info in wheel.infolist():
            relative = _member_path(info.filename)
            target = destination.joinpath(*relative.parts)
            if info.is_dir() or info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with wheel.open(info) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            top_level.add(relative.parts[0])


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> list[dict[str, object]]:
    records = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or path.is_symlink() or relative == STATE_FILE:
            continue
        records.append({
            "path": relative,
            "size": path.stat().st_size,
            "sha256": _file_digest(path),
        })
    return records


def _cached(destination: Path, tag: str, digest: str, selection: str) -> bool:
    try:
        state = json.loads((destination / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(state, dict):
        return False
    if (state.get("python_tag") != tag or state.get("bundle_sha256") != digest
            or state.get("selection_sha256") != selection):
        return False
    top_level = state.get("top_level")
    if not isinstance(top_level, list) or not top_level:
        return False
    records = state.get("files")
    if not isinstance(records, list) or not records:
        return False
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            return False
        relative = record.get("path")
        size = record.get("size")
        expected = record.get("sha256")
        if not isinstance(relative, str) or not isinstance(size, int) or not isinstance(expected, str):
            return False
        try:
            path = _member_path(relative)
        except ValueError:
            return False
        if relative in seen:
            return False
        seen.add(relative)
        target = destination.joinpath(*path.parts)
        try:
            if (target.is_symlink() or not target.is_file()
                    or target.stat().st_size != size
                    or _file_digest(target) != expected):
                return False
        except OSError:
            return False
    return True


@contextmanager
def _install_lock(destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / (destination.name + ".lock")
    with lock.open("a+") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def install(wheelhouse: Path, destination: Path, tag: str) -> bool:
    """Expand the selected wheels into ``destination``; return True when changed."""
    with _install_lock(destination):
        plan, selection = _plan(wheelhouse, tag)
        digest = _bundle_digest(wheelhouse)
        if _cached(destination, tag, digest, selection):
            return False
        with tempfile.TemporaryDirectory(prefix="wxdf-deps-", dir=str(destination.parent)) as scratch:
            staging = Path(scratch) / "deps"
            staging.mkdir()
            top_level = set()
            found = set()
            with tarfile.open(wheelhouse / BUNDLE, "r|xz") as bundle:
                for member in bundle:
                    if member.name not in plan:
                        continue
                    if member.name in found or not member.isfile():
                        raise ValueError("invalid or duplicate bundle member: " + member.name)
                    stream = bundle.extractfile(member)
                    if stream is None:
                        raise ValueError("missing wheel payload: " + member.name)
                    payload = stream.read()
                    if hashlib.sha256(payload).hexdigest() != plan[member.name]:
                        raise ValueError("wheel checksum mismatch: " + member.name)
                    _expand_wheel(payload, staging, top_level)
                    found.add(member.name)
            if found != set(plan):
                raise ValueError("offline bundle is missing required wheels")
            (staging / STATE_FILE).write_text(json.dumps({
                "python_tag": tag,
                "bundle_sha256": digest,
                "selection_sha256": selection,
                "top_level": sorted(top_level),
                "files": _inventory(staging),
            }) + "\n", encoding="utf-8")
            backup = destination.with_name(destination.name + ".old")
            if backup.exists():
                shutil.rmtree(backup)
            if destination.exists():
                os.replace(destination, backup)
            try:
                os.replace(staging, destination)
            except OSError:
                if backup.exists():
                    os.replace(backup, destination)
                raise
            if backup.exists():
                shutil.rmtree(backup)
    return True


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        print("usage: offline_install.py <wheelhouse> <destination>", file=sys.stderr)
        return 2
    wheelhouse, destination = (Path(value) for value in arguments)
    tag = "cp{}{}".format(*sys.version_info[:2])
    try:
        install(wheelhouse, destination, tag)
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, zipfile.BadZipFile) as error:
        print("WXDF-E-VENDOR-UNAVAILABLE: {}".format(error), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
