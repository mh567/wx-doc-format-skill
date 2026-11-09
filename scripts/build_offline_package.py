"""Build offline-deliverable Skill zips with a local wheelhouse."""

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

ROOT = Path(__file__).resolve().parents[1]
COMMON_REQUIREMENTS = (
    "python-docx==1.2.0",
    "markdown-it-py==4.2.0",
    "mdurl==0.1.2",
    "typing_extensions==4.16.0",
)
NATIVE_REQUIREMENTS = ("lxml==6.1.1",)
PLATFORMS = {
    "macos-arm64": {
        "pip_platform": "macosx_11_0_arm64",
        "python_versions": ("3.12", "3.13", "3.14"),
    },
    "linux-arm64": {
        "pip_platform": "manylinux_2_28_aarch64",
        "python_versions": ("3.12", "3.13", "3.14"),
    },
}
OFFLINE_MARKER = "OFFLINE_PACKAGE"
PACKAGE_ROOT_FILES = {"SKILL.md", "README.md", "VERSION", "requirements.txt"}
PACKAGE_PREFIXES = ("assets/", "scripts/")
PACKAGE_EXCLUDED = {"scripts/build_offline_package.py"}
ARCHIVE_ROOT = "wx-doc-format"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pip_download(
    requirements: tuple[str, ...],
    destination: Path,
    *,
    platform: str | None = None,
    python_version: str | None = None,
    abi: str | None = None,
) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--only-binary=:all:",
        "--no-deps",
        "--dest",
        str(destination),
    ]
    if platform:
        command += ["--platform", platform, "--implementation", "cp"]
        if python_version:
            command += ["--python-version", python_version]
        if abi:
            command += ["--abi", abi]
    command += list(requirements)
    subprocess.run(command, check=True)
    return sorted(destination.glob("*.whl"))


def tracked_files() -> list[str]:
    output = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"])
    return [item for item in output.decode("utf-8").split("\0") if item]


def package_files() -> list[str]:
    files = []
    for relative in tracked_files():
        if relative in PACKAGE_EXCLUDED:
            continue
        if relative in PACKAGE_ROOT_FILES or relative.startswith(PACKAGE_PREFIXES):
            files.append(relative)
    return files


def stage_source(destination: Path) -> None:
    for relative in package_files():
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"tracked file is missing: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for script in destination.rglob("*.sh"):
        script.chmod(0o755)


def build_wheelhouse(destination: Path, platform_id: str) -> None:
    spec = PLATFORMS[platform_id]
    common = destination / "common"
    pip_download(COMMON_REQUIREMENTS, common)
    records = []
    for wheel in sorted(common.glob("*.whl")):
        records.append({"python_tag": "common", "wheel": wheel.name, "sha256": sha256_file(wheel)})
    for version in spec["python_versions"]:
        tag = f"cp{version.replace('.', '')}"
        native = destination / tag
        pip_download(
            NATIVE_REQUIREMENTS,
            native,
            platform=spec["pip_platform"],
            python_version=version,
            abi=tag,
        )
        for wheel in sorted(native.glob("*.whl")):
            records.append({"python_tag": tag, "wheel": wheel.name, "sha256": sha256_file(wheel)})
    (destination / "manifest.json").write_text(
        json.dumps({"platform": platform_id, "wheels": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_archive(staging: Path, archive: Path, root_name: str) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for path in sorted(staging.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(staging).as_posix()
            name = f"{root_name}/{relative}"
            info = zipfile.ZipInfo.from_file(path, name)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.suffix in {".sh"} else 0o644
            info.external_attr = mode << 16
            with path.open("rb") as stream:
                handle.writestr(info, stream.read())


def build(platform_id: str, output_dir: Path) -> Path:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir / f".staging-{platform_id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    stage_source(staging)
    build_wheelhouse(staging / "wheelhouse", platform_id)
    (staging / OFFLINE_MARKER).write_text(
        f"version={version}\nplatform={platform_id}\n", encoding="utf-8"
    )
    archive = output_dir / f"{ARCHIVE_ROOT}-{version}-{platform_id}.zip"
    archive.unlink(missing_ok=True)
    write_archive(staging, archive, ARCHIVE_ROOT)
    shutil.rmtree(staging)
    return archive


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build offline Skill zips.")
    parser.add_argument("--platform", action="append", choices=tuple(PLATFORMS), default=None)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    platforms = args.platform or list(PLATFORMS)
    for platform_id in platforms:
        archive = build(platform_id, args.output_dir.resolve())
        print(f"{archive} ({archive.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
