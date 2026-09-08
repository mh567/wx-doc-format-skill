from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = Path(__file__).with_name("release_entrypoint.py")
PUBLIC_DIR = Path(__file__).with_name("public")
SUPPORTED_PLATFORM_IDS = (
    "windows-x86_64",
    "macos-arm64",
    "kylin-v10-arm64",
)


MODULAR_CONTRACT_VERSIONS = {
    "source_facts": "1.0",
    "ast": "2.0",
    "patch": "2.0",
    "checkpoint": "2.0",
    "capability_input": "2.0",
    "normalized": "1.0",
}

MODULAR_OPEN_MODES = {
    "modular_pure": {"status": "enabled", "entrypoint": "ConversionKernel.convert", "contract": "T09"},
    "modular_online": {"status": "closed", "entrypoint": "EnhancementExecutor", "reason": "kernel candidate-acceptance integration is pending"},
    "modular_file": {"status": "closed", "entrypoint": "CheckpointStore", "reason": "modular CLI resume integration is pending"},
    "modular_review": {"status": "closed", "entrypoint": "ReviewEvaluator", "reason": "modular post-audit rerender integration is pending"},
    "legacy_online": {"status": "enabled", "entrypoint": "legacy CLI", "contract": "legacy"},
    "legacy_file": {"status": "enabled", "entrypoint": "legacy CLI", "contract": "1.0"},
    "legacy_review": {"status": "enabled", "entrypoint": "legacy CLI", "contract": "legacy"},
}


def wxdf_module_names() -> tuple[str, ...]:
    """Return the source wxdf package and every Python module below it."""

    package_root = PROJECT_ROOT / "scripts" / "wxdf"
    names = {"wxdf"}
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            names.add("wxdf." + ".".join(parts))
    return tuple(sorted(names))


def modular_module_manifest(
    *,
    runtime_modules: tuple[str, ...] | list[str] | None = None,
    runtime_evidence: str = "explicit_nuitka_inclusion",
) -> dict:
    """Describe the modular surface carried by a compiled release."""

    source_modules = list(wxdf_module_names())
    runtime_names = sorted(set(runtime_modules or source_modules))
    return {
        "package": "wxdf",
        "source_modules": source_modules,
        "runtime_modules": runtime_names,
        "module_set_status": "matched" if source_modules == runtime_names else "mismatch",
        "runtime_evidence": runtime_evidence,
        "contract_versions": dict(MODULAR_CONTRACT_VERSIONS),
        "open_modes": json.loads(json.dumps(MODULAR_OPEN_MODES)),
    }


def _report_wxdf_modules(report_path: Path) -> tuple[str, ...]:
    """Extract wxdf module names from a Nuitka compilation report."""

    if not report_path.is_file():
        return ()
    text = report_path.read_text(encoding="utf-8", errors="replace")
    candidates = set(re.findall(r"\bwxdf(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b", text))
    known = set(wxdf_module_names())
    return tuple(sorted(candidate for candidate in candidates if candidate in known))


def _write_runtime_module_manifest(dist_dir: Path, report_path: Path) -> None:
    report_modules = _report_wxdf_modules(report_path)
    evidence = "compilation_report" if report_modules else "explicit_nuitka_inclusion"
    manifest = modular_module_manifest(runtime_modules=report_modules or None, runtime_evidence=evidence)
    (dist_dir / "wxdf-module-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def platform_id(system: str | None = None, machine: str | None = None) -> str:
    system_name = (system or platform.system()).strip().lower()
    machine_name = (machine or platform.machine()).strip().lower()
    architectures = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    architecture = architectures.get(machine_name)
    if architecture is None:
        raise ValueError(f"Unsupported architecture: {machine_name}")
    systems = {
        "windows": "windows",
        "darwin": "macos",
        "linux": "kylin-v10",
    }
    target_system = systems.get(system_name)
    if target_system is None:
        raise ValueError(f"Unsupported operating system: {system_name}")
    target = f"{target_system}-{architecture}"
    if target not in SUPPORTED_PLATFORM_IDS:
        raise ValueError(f"Unsupported release target: {target}")
    return target


def executable_name(system: str | None = None) -> str:
    return "wx-doc-format.exe" if (system or platform.system()).lower() == "windows" else "wx-doc-format"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nuitka_command(build_dir: Path) -> list[str]:
    include_args = [
        "--include-package=wxdf",
        *[
            f"--include-module={name}"
            for name in wxdf_module_names()
            if name not in {"wxdf", "wxdf.semantics"}
        ],
        "--include-package=wxdf.semantics",
    ]
    return [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        *include_args,
        f"--output-dir={build_dir}",
        f"--output-filename={executable_name()}",
        f"--report={build_dir / 'compilation-report.xml'}",
        str(ENTRYPOINT),
    ]


def compile_standalone(build_dir: Path) -> Path:
    build_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    scripts_path = str(PROJECT_ROOT / "scripts")
    previous = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = scripts_path if not previous else os.pathsep.join((scripts_path, previous))
    subprocess.run(nuitka_command(build_dir), cwd=PROJECT_ROOT, env=environment, check=True)
    dist_dir = build_dir / "release_entrypoint.dist"
    if not dist_dir.is_dir():
        raise RuntimeError(f"Nuitka standalone directory was not created: {dist_dir}")
    ensure_runtime_layout(dist_dir)
    _write_runtime_module_manifest(dist_dir, build_dir / "compilation-report.xml")
    return dist_dir


def ensure_runtime_layout(runtime_dir: Path) -> None:
    """Restore package directories required by runtime-relative data lookups."""
    if (runtime_dir / "docx" / "templates").is_dir():
        (runtime_dir / "docx" / "parts").mkdir(parents=True, exist_ok=True)


def _copy_public_files(package_dir: Path) -> None:
    for name in ("SKILL.md", "README.md"):
        shutil.copy2(PUBLIC_DIR / name, package_dir / name)
    shutil.copy2(PROJECT_ROOT / "VERSION", package_dir / "VERSION")
    shutil.copytree(PROJECT_ROOT / "assets", package_dir / "assets")
    adapter_dir = package_dir / "scripts"
    adapter_dir.mkdir()
    for name in ("install.sh", "run.sh", "install.ps1", "run.ps1"):
        source = Path(__file__).with_name(name)
        target = adapter_dir / name
        shutil.copy2(source, target)
        if target.suffix == ".sh":
            target.chmod(0o755)


def python_source_files(root: Path) -> list[Path]:
    forbidden_suffixes = {".py", ".pyc", ".pyo"}
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in forbidden_suffixes)


def reject_python_sources(root: Path) -> None:
    forbidden = python_source_files(root)
    if forbidden:
        paths = ", ".join(path.relative_to(root).as_posix() for path in forbidden)
        raise ValueError(f"Python source files are forbidden in release packages: {paths}")


def reject_unsafe_release_paths(root: Path) -> None:
    unsafe = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if "\n" in path.relative_to(root).as_posix() or "\r" in path.relative_to(root).as_posix()
    ]
    if unsafe:
        raise ValueError("Release package paths must not contain newlines")


def _write_integrity_files(package_dir: Path, target: str, version: str) -> None:
    reject_unsafe_release_paths(package_dir)
    payload_files = sorted(
        path for path in package_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    )
    manifest = {
        "schema_version": 1,
        "product": "wx-doc-format-skill",
        "version": version,
        "platform": target,
        "compatibility_status": "candidate" if target.startswith("kylin-v10-") else "native",
        "offline": True,
        "modular": json.loads(
            (package_dir / "runtime" / "wxdf-module-manifest.json").read_text(encoding="utf-8")
        ) if (package_dir / "runtime" / "wxdf-module-manifest.json").is_file() else modular_module_manifest(),
        "files": [
            {
                "path": path.relative_to(package_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in payload_files
        ],
    }
    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksummed = [*payload_files, manifest_path]
    sums = "".join(
        f"{sha256_file(path)}  {path.relative_to(package_dir).as_posix()}\n"
        for path in sorted(checksummed)
    )
    (package_dir / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")


def assemble_package(dist_dir: Path, output_dir: Path, target: str | None = None) -> Path:
    if not dist_dir.is_dir():
        raise FileNotFoundError(f"Standalone directory does not exist: {dist_dir}")
    version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError("VERSION is empty")
    resolved_target = target or platform_id()
    if resolved_target not in SUPPORTED_PLATFORM_IDS:
        raise ValueError(f"Unsupported platform identifier: {resolved_target}")
    reject_python_sources(dist_dir)
    reject_unsafe_release_paths(dist_dir)
    package_dir = output_dir / f"wx-doc-format-skill-{version}-{resolved_target}"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)
    _copy_public_files(package_dir)
    runtime_dir = package_dir / "runtime"
    shutil.copytree(dist_dir, runtime_dir)
    ensure_runtime_layout(runtime_dir)
    shutil.copy2(PROJECT_ROOT / "VERSION", runtime_dir / "VERSION")
    expected_executable = runtime_dir / executable_name("windows" if resolved_target.startswith("windows-") else None)
    if not expected_executable.is_file():
        raise FileNotFoundError(f"Runtime executable does not exist: {expected_executable}")
    if expected_executable.suffix != ".exe":
        expected_executable.chmod(expected_executable.stat().st_mode | 0o111)
    reject_python_sources(package_dir)
    reject_unsafe_release_paths(package_dir)
    _write_integrity_files(package_dir, resolved_target, version)
    return package_dir


def create_archive(package_dir: Path, target: str) -> Path:
    if target.startswith("windows-"):
        archive_path = package_dir.parent / f"{package_dir.name}.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, Path(package_dir.name) / path.relative_to(package_dir))
        return archive_path
    archive_path = package_dir.parent / f"{package_dir.name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(package_dir, arcname=package_dir.name)
    return archive_path


def validate_expected_version(expected: str | None) -> str:
    version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    normalized = (expected or "").strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    if normalized and normalized != version:
        raise ValueError(f"Tag version {normalized} does not match VERSION {version}")
    return version


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an offline Nuitka standalone package.")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist")
    parser.add_argument("--build-dir", type=Path, default=PROJECT_ROOT / "build" / "nuitka")
    parser.add_argument("--dist-dir", type=Path, help="Existing Nuitka .dist directory")
    parser.add_argument(
        "--platform-id",
        dest="target",
        choices=SUPPORTED_PLATFORM_IDS,
        help="Override artifact platform identifier",
    )
    parser.add_argument("--skip-compile", action="store_true", help="Assemble an existing --dist-dir")
    parser.add_argument("--archive", action="store_true", help="Create a platform archive after assembly")
    parser.add_argument("--expected-version", default=None, help="Require VERSION to match this tag")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> Path:
    args = parse_args(argv)
    validate_expected_version(args.expected_version)
    if args.skip_compile and args.dist_dir is None:
        raise SystemExit("--skip-compile requires --dist-dir")
    dist_dir = args.dist_dir if args.skip_compile else compile_standalone(args.build_dir)
    package_dir = assemble_package(dist_dir.resolve(), args.output_dir.resolve(), args.target)
    print(package_dir)
    if args.archive:
        print(create_archive(package_dir, args.target or platform_id()))
    return package_dir


if __name__ == "__main__":
    main()
