"""Assemble the offline interpreted package and its archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
NUITKA_DIR = HERE.parent / "nuitka"
if str(NUITKA_DIR) not in sys.path:
    sys.path.append(str(NUITKA_DIR))

from build_distribution import MODULAR_CONTRACT_VERSIONS, mode_manifest, release_module_names

import build_payload

PLATFORM_IDS = ("macos-arm64", "kylin-v10-arm64")
SOURCE_SUFFIX = "interpreted"
PUBLIC_LAUNCHER_DIR = HERE.parent / "nuitka" / "public-repo" / "scripts"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def native_platform_id() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine == "arm64":
        return "macos-arm64"
    if system == "linux" and machine in {"aarch64", "arm64"}:
        return "kylin-v10-arm64"
    raise RuntimeError(f"WXDF-E-PLATFORM-UNSUPPORTED: {system}/{machine}")


def capture_identity(project_root: Path, *, allow_dirty: bool) -> dict:
    def git(*arguments: str) -> str:
        return subprocess.check_output(["git", "-C", str(project_root), *arguments], text=True).strip()

    if not allow_dirty and git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("SOURCE_DIRTY: candidate builds require a clean committed source tree")
    return {
        "version": (project_root / "VERSION").read_text(encoding="utf-8").strip(),
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "toolchain": {
            "python": platform.python_version(),
            "runtime": SOURCE_SUFFIX,
            "system": platform.system(),
            "machine": platform.machine(),
            "build_image": os.environ.get("WXDF_LINUX_BUILD_IMAGE", "native"),
        },
    }


def vendor_tags(vendor_root: Path) -> list[str]:
    tags = sorted(path.name for path in vendor_root.iterdir() if path.is_dir() and path.name.startswith("cp"))
    if not tags:
        raise FileNotFoundError(f"no vendor directories found under {vendor_root}")
    return tags


def python_range(tags: list[str]) -> str:
    def version(tag: str) -> str:
        digits = tag.removeprefix("cp")
        return f"{digits[0]}.{digits[1:]}"

    ordered = sorted(tags)
    return version(ordered[0]) if len(ordered) == 1 else f"{version(ordered[0])}-{version(ordered[-1])}"


def payload_modules(index: list[dict]) -> list[str]:
    return sorted(
        item["name"] for item in index if item["name"] == "wxdf" or item["name"].startswith("wxdf.")
    )


def _vendor_entry(path: Path, kind: str) -> dict:
    vendor_json = path / "vendor.json"
    wheels = json.loads(vendor_json.read_text(encoding="utf-8"))["wheels"] if vendor_json.is_file() else []
    return {"python_tag": path.name, "kind": kind, "wheels": wheels}


def compose_package(
    package_dir: Path,
    *,
    platform_id: str,
    status: str,
    identity: dict,
    payload_dir: Path,
    payload_index: list[dict],
    vendor_root: Path,
    readme: Path,
    runtime_python: Path | None,
) -> dict:
    version = identity["version"]
    package_dir.mkdir(parents=True)
    shutil.copy2(PROJECT_ROOT / "SKILL.md", package_dir / "SKILL.md")
    shutil.copy2(readme, package_dir / "README.md")
    (package_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    shutil.copytree(PROJECT_ROOT / "assets", package_dir / "assets")

    scripts_dir = package_dir / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(HERE / "run.sh", scripts_dir / "run.sh")
    shutil.copy2(HERE / "install.sh", scripts_dir / "install.sh")
    shutil.copy2(PUBLIC_LAUNCHER_DIR / "validate-manifest.awk", scripts_dir / "validate-manifest.awk")
    for script in scripts_dir.iterdir():
        if script.suffix in {".sh", ".awk"}:
            script.chmod(0o755)

    lib_dir = package_dir / "lib"
    lib_dir.mkdir()
    shutil.copy2(HERE / "boot.py", lib_dir / "boot.py")
    shutil.copy2(payload_dir / "payload.bin", lib_dir / "payload.bin")
    payload_summary = json.loads((payload_dir / "payload.json").read_text(encoding="utf-8"))
    shutil.copy2(payload_dir / "payload.json", lib_dir / "payload.json")

    vendor_summary = []
    common = vendor_root / "common"
    if not common.is_dir():
        raise FileNotFoundError(f"shared vendor directory is missing: {common}")
    shutil.copytree(common, package_dir / "vendor" / "common")
    vendor_summary.append(_vendor_entry(common, "common"))
    tags = vendor_tags(vendor_root)
    for tag in tags:
        shutil.copytree(vendor_root / tag, package_dir / "vendor" / tag)
        vendor_summary.append(_vendor_entry(vendor_root / tag, "native"))

    runtime_block = {
        "kind": "interpreted",
        "entry": "lib/boot.py",
        "python_tags": tags,
        "python_requires": python_range(tags),
        "payload": payload_summary,
        "vendor": vendor_summary,
    }
    if runtime_python is not None:
        shutil.copytree(runtime_python, package_dir / "runtime" / "python")
        runtime_block["bundled_python"] = "runtime/python/bin/python3"

    source_modules = list(release_module_names())
    runtime_modules = payload_modules(payload_index)
    if runtime_modules != source_modules:
        raise RuntimeError(
            "MODULE_SURFACE_MISMATCH: payload modules differ from the release module surface"
        )
    manifest = {
        "schema_version": 1,
        "product": "wx-doc-format-skill",
        "version": version,
        "source_identity": identity,
        "platform": platform_id,
        "platforms": {platform_id: {"status": status}},
        "compatibility_status": SOURCE_SUFFIX,
        "offline": True,
        "runtime": runtime_block,
        "modular": {
            "package": "wxdf",
            "source_modules": source_modules,
            "runtime_modules": runtime_modules,
            "module_set_status": "matched",
            "runtime_evidence": "payload_manifest",
            "open_modes": mode_manifest(),
            "contract_versions": MODULAR_CONTRACT_VERSIONS,
            "forbidden_runtime_modules": [],
            "missing_runtime_modules": [],
            "unexpected_runtime_modules": [],
            "payload_manifest": {
                "name": "payload.json",
                "sha256": sha256_file(payload_dir / "payload.json"),
            },
        },
        "public_report_summary": {
            "schema_version": "1.0",
            "field": "public_summary",
            "required_fields": [
                "status",
                "audits",
                "unexpected_styles_count",
                "manual_review_required",
                "diagnostic_codes",
            ],
        },
    }
    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    write_sums(package_dir)
    return manifest


def write_sums(package_dir: Path) -> None:
    records = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS.txt":
            continue
        records.append(f"{sha256_file(path)}  {path.relative_to(package_dir).as_posix()}\n")
    (package_dir / "SHA256SUMS.txt").write_text("".join(records), encoding="utf-8")


def write_archive(package_dir: Path) -> Path:
    archive = package_dir.parent / f"{package_dir.name}.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(package_dir, arcname=package_dir.name)
    return archive


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble the offline interpreted Skill package.")
    parser.add_argument("--platform", choices=PLATFORM_IDS, default=None)
    parser.add_argument("--status", choices=("accepted", "candidate"), default=None)
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist" / "local")
    parser.add_argument("--vendor-root", type=Path, required=True, help="Directory holding cpXXX vendor trees.")
    parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT / "scripts")
    parser.add_argument("--readme", type=Path, default=HERE / "public-repo" / "README.md")
    parser.add_argument("--runtime-python", type=Path, default=None)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--no-archive", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    platform_id = args.platform or native_platform_id()
    status = args.status or ("accepted" if platform_id == "macos-arm64" else "candidate")
    identity = capture_identity(PROJECT_ROOT, allow_dirty=args.allow_dirty)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    package_name = f"wx-doc-format-skill-{identity['version']}-{platform_id}"
    package_dir = output_dir / package_name
    if package_dir.exists():
        shutil.rmtree(package_dir)

    with tempfile.TemporaryDirectory(prefix="wxdf-payload-") as temporary:
        payload_dir = Path(temporary)
        blob, summary, index = build_payload.build_payload(args.source_root.resolve())
        build_payload.write_payload(payload_dir, blob, summary, [], None)
        compose_package(
            package_dir,
            platform_id=platform_id,
            status=status,
            identity=identity,
            payload_dir=payload_dir,
            payload_index=index,
            vendor_root=args.vendor_root.resolve(),
            readme=args.readme.resolve(),
            runtime_python=args.runtime_python.resolve() if args.runtime_python else None,
        )

    print(f"package: {package_dir}")
    if not args.no_archive:
        archive = write_archive(package_dir)
        print(f"archive: {archive}")
        print(f"archive sha256: {sha256_file(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
