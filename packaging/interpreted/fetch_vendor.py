"""Populate a vendor directory from prebuilt wheels.

Two kinds are supported:

- ``common`` holds the pure-Python dependencies and is shared by every
  interpreter version.
- ``native`` holds the compiled extensions and must match one CPython minor
  version.

Runs natively by default.  Cross-downloading is available so one build machine
can cover several interpreter minors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIREMENTS = {
    "common": HERE / "requirements-vendor.txt",
    "native": HERE / "requirements-vendor-native.txt",
}


def wheel_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    requirements: Path,
    destination: Path,
    python: str,
    platform: str | None,
    python_version: str | None,
    abi: str | None,
    no_deps: bool,
) -> list[Path]:
    command = [
        python,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--only-binary=:all:",
        "--dest",
        str(destination),
        "-r",
        str(requirements),
    ]
    if no_deps:
        command.append("--no-deps")
    if platform:
        command += ["--platform", platform, "--implementation", "cp"]
        if python_version:
            command += ["--python-version", python_version]
        if abi:
            command += ["--abi", abi]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pip download failed:\n{result.stdout}\n{result.stderr}")
    wheels = sorted(destination.glob("*.whl"))
    if not wheels:
        raise RuntimeError("pip download produced no wheels")
    return wheels


def extract(wheels: list[Path], output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                archive.extract(member, output)
        records.append(
            {
                "wheel": wheel.name,
                "sha256": wheel_digest(wheel),
                "size": wheel.stat().st_size,
            }
        )
    return records


def _version_from_tag(tag: str) -> str:
    digits = tag.removeprefix("cp")
    return f"{digits[0]}.{digits[1:]}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Populate a vendor directory from prebuilt wheels.")
    parser.add_argument("--kind", choices=tuple(REQUIREMENTS), default="common")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--platform", default=None, help="Optional pip platform tag for cross-download.")
    parser.add_argument("--python-version", default=None)
    parser.add_argument("--abi", default=None)
    parser.add_argument("--python-tag", default=None, help="Recorded target tag, defaults to the running interpreter.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output.resolve()
    tag = args.python_tag or f"cp{sys.version_info.major}{sys.version_info.minor}"
    if args.python_version and args.python_version.startswith("cp"):
        args.python_version = args.python_version[2:]
    requirements = args.requirements or REQUIREMENTS[args.kind]
    with tempfile.TemporaryDirectory(prefix="wxdf-vendor-") as temporary:
        wheels = download(
            requirements.resolve(),
            Path(temporary),
            args.python,
            args.platform,
            args.python_version,
            args.abi,
            no_deps=args.kind == "common",
        )
        if output.exists():
            shutil.rmtree(output)
        records = extract(wheels, output)
    summary = {
        "schema_version": 1,
        "kind": args.kind,
        "python_tag": tag,
        "python_version": _version_from_tag(tag) if args.kind == "native" else None,
        "requirements": requirements.name,
        "wheels": records,
    }
    (output / "vendor.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(record["wheel"])
    print(f"vendor directory: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
