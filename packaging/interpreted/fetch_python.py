"""Fetch a relocatable CPython for Linux packages.

macOS packages must rely on an interpreter already present on the target
machine, because bundling one would reintroduce native code signing.  Linux has
no such constraint, so Kylin packages carry their own interpreter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

DEFAULT_RELEASE = "20261026"
DEFAULT_VERSION = "3.13.18"
TARGET = "aarch64-unknown-linux-gnu"
RELEASE_BASE = "https://github.com/astral-sh/python-build-standalone/releases/download"
TERMINFO_PREFIX = "python/share/terminfo/"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_name(version: str, release: str) -> str:
    return f"cpython-{version}+{release}-{TARGET}-install_only_stripped.tar.gz"


def fetch(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as target:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)


def expected_digest(release: str, asset: str) -> str:
    with urllib.request.urlopen(f"{RELEASE_BASE}/{release}/SHA256SUMS", timeout=60) as response:
        text = response.read().decode("utf-8")
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == asset:
            return parts[0]
    raise RuntimeError(f"checksum for {asset} is missing from the release")


def _validate_link(member: tarfile.TarInfo) -> None:
    if posixpath.isabs(member.linkname) or ".." in member.linkname.split("/"):
        raise RuntimeError(f"unsafe archive link: {member.name} -> {member.linkname}")
    if member.issym():
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
    else:
        resolved = posixpath.normpath(member.linkname)
    if resolved.split("/")[0] != "python":
        raise RuntimeError(f"unsafe archive link: {member.name} -> {member.linkname}")


def extract(archive: Path, output: Path) -> None:
    members = []
    with tarfile.open(archive, "r:gz") as handle:
        for member in handle.getmembers():
            name = Path(member.name)
            if member.name.startswith(TERMINFO_PREFIX):
                continue
            if name.is_absolute() or ".." in name.parts or name.parts[0] != "python":
                raise RuntimeError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                _validate_link(member)
            elif not (member.isfile() or member.isdir()):
                raise RuntimeError(f"unsupported archive member: {member.name}")
            members.append(member)
        if sys.version_info >= (3, 12):
            handle.extractall(output, members=members, filter="data")
        else:
            handle.extractall(output, members=members)


PRUNE_PATHS = (
    "include",
    "lib/pkgconfig",
    "share",
)
PRUNE_VERSION_PATHS = (
    "site-packages",
    "ensurepip",
    "idlelib",
    "lib2to3",
    "pydoc_data",
    "test",
    "tkinter",
    "turtledemo",
)
PRUNE_LIB_GLOBS = (
    "libpython3*.so",
    "libtcl*",
    "libtk*",
    "tcl9*",
    "tk9*",
    "itcl*",
    "thread*",
)


def _version_dir(python_root: Path) -> Path:
    candidates = sorted(path for path in (python_root / "lib").glob("python3.*") if path.is_dir())
    if len(candidates) != 1:
        raise RuntimeError("bundled interpreter version directory is ambiguous")
    return candidates[0]


def prune(python_root: Path) -> list[str]:
    version_dir = _version_dir(python_root)
    removed = []
    bin_dir = python_root / "bin"
    entry = bin_dir / "python3"
    real = entry.resolve()
    if not real.is_file():
        raise RuntimeError("bundled interpreter target is missing")
    for path in sorted(bin_dir.iterdir()):
        if path == real:
            continue
        removed.append(path.relative_to(python_root).as_posix())
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    if real != entry:
        real.rename(entry)
    if not entry.is_file() or not os.stat(entry).st_mode & 0o100:
        raise RuntimeError("pruned interpreter is missing or not executable")
    for relative in PRUNE_PATHS:
        target = python_root / relative
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(relative)
    for name in PRUNE_VERSION_PATHS:
        target = version_dir / name
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(target.relative_to(python_root).as_posix())
    for target in sorted(version_dir.glob("config-*")):
        shutil.rmtree(target)
        removed.append(target.relative_to(python_root).as_posix())
    lib_dir = python_root / "lib"
    for pattern in PRUNE_LIB_GLOBS:
        for target in sorted(lib_dir.glob(pattern)):
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(target.relative_to(python_root).as_posix())
    return removed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a relocatable CPython for Linux packages.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release", default=DEFAULT_RELEASE)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--asset", default=None)
    parser.add_argument("--keep-full", action="store_true", help="Keep headers, pip and extra entry points.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    asset = args.asset or asset_name(args.version, args.release)
    archive = output / asset
    url = f"{RELEASE_BASE}/{args.release}/{asset}"
    fetch(url, archive)
    digest = sha256_file(archive)
    expected = expected_digest(args.release, asset)
    if digest != expected:
        raise RuntimeError(f"SHA256 mismatch for {asset}: {digest} != {expected}")

    staging = output / "extract"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    extract(archive, staging)
    python_root = staging / "python"
    if not python_root.is_dir():
        raise RuntimeError("archive does not contain a python/ directory")
    executable = python_root / "bin" / "python3"
    if not executable.is_file() or not os.stat(executable).st_mode & 0o100:
        raise RuntimeError("bundled interpreter is missing or not executable")

    target = output / "python"
    if target.exists():
        shutil.rmtree(target)
    python_root.rename(target)
    shutil.rmtree(staging)
    archive.unlink()
    pruned = [] if args.keep_full else prune(target)

    summary = {
        "schema_version": 1,
        "release": args.release,
        "version": args.version,
        "python_tag": "cp" + "".join(args.version.split(".")[:2]),
        "target": TARGET,
        "asset": asset,
        "sha256": digest,
        "entry": "python/bin/python3",
        "pruned": pruned,
    }
    (output / "python.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"python: {target}")
    print(f"python sha256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
