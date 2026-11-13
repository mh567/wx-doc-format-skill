"""Build offline-deliverable Skill zips with a local wheelhouse."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON_REQUIREMENTS = (
    "python-docx==1.1.2",
    "python-docx==1.2.0",
    "markdown-it-py==3.0.0",
    "markdown-it-py==4.2.0",
    "mdurl==0.1.2",
    "typing_extensions==4.13.2",
    "typing_extensions==4.16.0",
)
NATIVE_REQUIREMENTS = ("lxml==6.1.1",)
PYTHON_VERSIONS = tuple(f"3.{minor}" for minor in range(8, 15))
PLATFORMS = {
    "macos-arm64": {
        "pip_platform": "macosx_11_0_arm64",
        "python_versions": PYTHON_VERSIONS,
    },
    "linux-arm64": {
        "pip_platform": "manylinux_2_28_aarch64",
        "python_versions": PYTHON_VERSIONS,
    },
}
OFFLINE_MARKER = "OFFLINE_PACKAGE"
PACKAGE_ROOT_FILES = {"SKILL.md", "README.md", "VERSION", "requirements.txt"}
PACKAGE_PREFIXES = ("assets/", "scripts/", "references/")
PACKAGE_EXCLUDED = {"scripts/build_offline_package.py"}
ARCHIVE_ROOT = "wx-doc-format"
MAX_ARCHIVE_BYTES = 14_000_000


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
    for relative in sorted(set(tracked_files()) | {"scripts/offline_install.py"}):
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


def resolve_environments(records: list[dict], versions: tuple[str, ...]) -> dict[str, list[str]]:
    # pip is a build-time tool only; resolve markers before delivery.
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename

    requirements = [
        Requirement(line) for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    environments = {}
    for version in versions:
        tag = "cp" + version.replace(".", "")
        environment = {"python_version": version, "python_full_version": version + ".0"}
        pins = {
            canonicalize_name(requirement.name): requirement.specifier
            for requirement in requirements
            if requirement.marker is None or requirement.marker.evaluate(environment)
        }
        selected = {}
        for record in records:
            if record["python_tag"] not in {"common", tag}:
                continue
            name, wheel_version, _, _ = parse_wheel_filename(record["wheel"])
            if name in pins and wheel_version not in pins[name]:
                continue
            if name in selected:
                raise ValueError(f"ambiguous offline dependency for {tag}: {name}")
            selected[name] = f"{record['python_tag']}/{record['wheel']}"
        if not pins.keys() <= selected.keys():
            raise ValueError(f"missing offline dependencies for {tag}: {pins.keys() - selected.keys()}")
        environments[tag] = sorted(selected.values())
    return environments


def build_wheelhouse(
    destination: Path, platform_id: str, *, macos_cp38_wheel: Path | None = None,
) -> None:
    spec = PLATFORMS[platform_id]
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix="wxdf-wheels-") as scratch:
        wheels = Path(scratch)
        common = wheels / "common"
        for requirement in COMMON_REQUIREMENTS:
            # Download pins separately so pip can retain multiple package versions.
            pip_download((requirement,), common)
        tags = ["common"]
        for version in spec["python_versions"]:
            tag = f"cp{version.replace('.', '')}"
            tags.append(tag)
            platform = spec["pip_platform"]
            if platform_id == "macos-arm64" and version == "3.8":
                if macos_cp38_wheel is None:
                    raise ValueError("macOS ARM64 Python 3.8 requires --macos-cp38-wheel")
                name = macos_cp38_wheel.name
                if not (name.startswith("lxml-5.4.0-cp38-cp38-macosx_")
                        and name.endswith("_arm64.whl")):
                    raise ValueError("expected a native macOS ARM64 lxml 5.4.0 cp38 wheel")
                with zipfile.ZipFile(macos_cp38_wheel) as wheel:
                    if wheel.testzip() is not None:
                        raise ValueError("invalid macOS cp38 wheel")
                native = wheels / tag
                native.mkdir()
                shutil.copy2(macos_cp38_wheel, native / name)
                continue
            pip_download(
                ("lxml==5.4.0",) if version == "3.8" else NATIVE_REQUIREMENTS,
                wheels / tag,
                platform=platform,
                python_version=version,
                abi=tag,
            )
        # Stored wheel members let LZMA share repeated bytes across Python ABIs.
        with tarfile.open(destination / "wheels.tar.xz", "w:xz", preset=9) as bundle:
            for tag in tags:
                for wheel in sorted((wheels / tag).glob("*.whl")):
                    stored = io.BytesIO()
                    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(
                        stored, "w", compression=zipfile.ZIP_STORED
                    ) as target:
                        for info in source.infolist():
                            content = source.read(info.filename)
                            info.compress_type = zipfile.ZIP_STORED
                            target.writestr(info, content)
                    payload = stored.getvalue()
                    record = {
                        "python_tag": tag, "wheel": wheel.name,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                    records.append(record)
                    member = tarfile.TarInfo(f"{tag}/{wheel.name}")
                    member.size = len(payload)
                    bundle.addfile(member, io.BytesIO(payload))
    (destination / "manifest.json").write_text(
        json.dumps({
            "platform": platform_id,
            "python_versions": list(spec["python_versions"]),
            "environments": resolve_environments(records, spec["python_versions"]),
            "wheels": records,
        }, ensure_ascii=False, indent=2) + "\n",
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


def build(
    platform_id: str, output_dir: Path, *, macos_cp38_wheel: Path | None = None,
) -> Path:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = output_dir / f".staging-{platform_id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    stage_source(staging)
    build_wheelhouse(staging / "wheelhouse", platform_id, macos_cp38_wheel=macos_cp38_wheel)
    (staging / OFFLINE_MARKER).write_text(
        f"version={version}\nplatform={platform_id}\n", encoding="utf-8"
    )
    archive = output_dir / f"{ARCHIVE_ROOT}-{version}-{platform_id}.zip"
    archive.unlink(missing_ok=True)
    write_archive(staging, archive, ARCHIVE_ROOT)
    shutil.rmtree(staging)
    if archive.stat().st_size >= MAX_ARCHIVE_BYTES:
        size = archive.stat().st_size
        archive.unlink()
        raise ValueError(f"offline package must be below {MAX_ARCHIVE_BYTES} bytes: {size}")
    return archive


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build offline Skill zips.")
    parser.add_argument("--platform", action="append", choices=tuple(PLATFORMS), default=None)
    parser.add_argument("--macos-cp38-wheel", type=Path, help="Locally built lxml cp38 macOS ARM64 wheel.")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    platforms = args.platform or list(PLATFORMS)
    for platform_id in platforms:
        archive = build(
            platform_id, args.output_dir.resolve(), macos_cp38_wheel=args.macos_cp38_wheel,
        )
        print(f"{archive} ({archive.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
