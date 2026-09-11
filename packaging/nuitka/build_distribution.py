from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
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
from xml.etree import ElementTree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = Path(__file__).with_name("release_entrypoint.py")
PUBLIC_DIR = Path(__file__).with_name("public")
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from wxdf.review import mode_manifest


SUPPORTED_PLATFORM_IDS = (
    "windows-x86_64",
    "macos-arm64",
    "kylin-v10-arm64",
)
PUBLIC_PACKAGE_PLATFORM_IDS = (
    "macos-arm64",
    "kylin-v10-arm64",
)
RUNTIME_COMPILATION_REPORT_NAME = "wxdf-compilation-report.xml"


MODULAR_CONTRACT_VERSIONS = {
    "source_facts": "1.0",
    "ast": "2.0",
    "patch": "2.0",
    "checkpoint": "2.0",
    "capability_input": "2.0",
    "normalized": "1.0",
}

MODULAR_OPEN_MODES = mode_manifest()

RETIRED_RELEASE_MODULES = frozenset({
    "wxdf.migration",
    "wxdf.semantics.compat",
    "wxdf.semantics.appendix_bridge",
    "wxdf.semantics.caption_bridge",
    "wxdf.semantics.equivalence",
    "wxdf.semantics.list_bridge",
    "wxdf.semantics.parser_equivalence",
    "wxdf.semantics.protected_bridge",
    "wxdf.semantics.region_bridge",
    "wxdf.semantics.source_projection",
    "wxdf.semantics.source_projection_bridge",
    "wxdf.semantics.structure_bridge",
    "wxdf.semantics.table_bridge",
})
RETIRED_RUNTIME_IMPORTS = frozenset({
    "docx_pipeline",
    "md_pipeline",
    "model_normalization",
    "template_finalizer",
    "docx_render",
    "word_model_renderer",
})

PUBLIC_REPORT_SUMMARY_CONTRACT = {
    "schema_version": "1.0",
    "field": "public_summary",
    "required_fields": [
        "status",
        "pipeline",
        "profile",
        "list_policy",
        "audits",
        "unexpected_styles_count",
        "manual_review_required",
        "diagnostic_codes",
    ],
}


def capture_source_identity(project_root: Path = PROJECT_ROOT) -> dict:
    """Identify the clean source tree used by this build process."""
    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(project_root), *arguments], text=True,
        ).strip()

    if git("status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError("SOURCE_DIRTY: candidate builds require a clean committed source tree")
    return {
        "version": (project_root / "VERSION").read_text(encoding="utf-8").strip(),
        "commit": git("rev-parse", "HEAD"),
        "tree": git("rev-parse", "HEAD^{tree}"),
        "toolchain": {
            "python": platform.python_version(),
            "nuitka": importlib.metadata.version("Nuitka"),
            "system": platform.system(),
            "machine": platform.machine(),
            "build_image": os.environ.get("WXDF_LINUX_BUILD_IMAGE", "native"),
        },
    }


def validate_source_identity(identity: object, *, version: str | None = None) -> dict:
    if not isinstance(identity, dict):
        raise RuntimeError("SOURCE_IDENTITY_INVALID: source identity is missing")
    for field in ("commit", "tree"):
        if not isinstance(identity.get(field), str) or not re.fullmatch(r"[0-9a-f]{40}", identity[field]):
            raise RuntimeError(f"SOURCE_IDENTITY_INVALID: invalid {field}")
    if not isinstance(identity.get("version"), str) or not identity["version"]:
        raise RuntimeError("SOURCE_IDENTITY_INVALID: version is missing")
    if version is not None and identity["version"] != version:
        raise RuntimeError("SOURCE_IDENTITY_MISMATCH: version differs from the package")
    toolchain = identity.get("toolchain")
    if not isinstance(toolchain, dict) or any(
        not isinstance(toolchain.get(field), str) or not toolchain[field]
        for field in ("python", "nuitka", "system", "machine", "build_image")
    ):
        raise RuntimeError("SOURCE_IDENTITY_INVALID: incomplete toolchain")
    return identity


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


def _forbidden_release_module(name: str) -> bool:
    return name in RETIRED_RELEASE_MODULES or name.startswith("wxdf.legacy_")


def release_module_names() -> tuple[str, ...]:
    """Return the explicit wxdf modules required by the native release chain."""

    return tuple(
        name
        for name in wxdf_module_names()
        if name not in RETIRED_RELEASE_MODULES and not _forbidden_release_module(name)
    )


def modular_module_manifest(
    *,
    runtime_modules: tuple[str, ...] | list[str] | None = None,
    runtime_evidence: str = "explicit_nuitka_inclusion",
) -> dict:
    """Describe the modular surface carried by a compiled release."""

    source_modules = list(release_module_names())
    runtime_names = sorted(set(source_modules if runtime_modules is None else runtime_modules))
    missing_runtime_modules = sorted(set(source_modules) - set(runtime_names))
    unexpected_runtime_modules = sorted(set(runtime_names) - set(source_modules))
    forbidden_runtime_modules = sorted(
        name for name in runtime_names if _forbidden_release_module(name)
    )
    return {
        "package": "wxdf",
        "source_modules": source_modules,
        "runtime_modules": runtime_names,
        "module_set_status": (
            "matched"
            if source_modules == runtime_names and not forbidden_runtime_modules
            else "mismatch"
        ),
        "missing_runtime_modules": missing_runtime_modules,
        "unexpected_runtime_modules": unexpected_runtime_modules,
        "forbidden_runtime_modules": forbidden_runtime_modules,
        "runtime_evidence": runtime_evidence,
        "contract_versions": dict(MODULAR_CONTRACT_VERSIONS),
        "open_modes": mode_manifest(),
    }


def _report_wxdf_modules(report_path: Path) -> tuple[str, ...]:
    """Extract wxdf module names from a Nuitka compilation report."""

    if not report_path.is_file():
        raise RuntimeError(f"Nuitka compilation report is missing: {report_path}")
    try:
        root = ElementTree.parse(report_path).getroot()
    except ElementTree.ParseError as exc:
        raise RuntimeError(f"Nuitka compilation report is invalid: {exc}") from exc
    if root.tag != "nuitka-compilation-report":
        raise RuntimeError(
            "Nuitka compilation report root must be nuitka-compilation-report"
        )
    if root.get("completion") != "yes":
        raise RuntimeError("Nuitka compilation report completion must be yes")
    if root.get("mode") != "standalone":
        raise RuntimeError("Nuitka compilation report mode must be standalone")
    modules = {
        str(element.get("name"))
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "module"
        and str(element.get("name") or "").startswith("wxdf")
    }
    if not modules:
        raise RuntimeError("Nuitka compilation report contains no wxdf modules")
    return tuple(sorted(modules))


def _write_runtime_module_manifest(
    dist_dir: Path, report_path: Path, *, source_identity: dict | None = None,
) -> None:
    report_modules = _report_wxdf_modules(report_path)
    stored_report = dist_dir / RUNTIME_COMPILATION_REPORT_NAME
    if report_path.resolve() != stored_report.resolve():
        shutil.copy2(report_path, stored_report)
    manifest = modular_module_manifest(
        runtime_modules=report_modules,
        runtime_evidence="compilation_report",
    )
    manifest["compilation_report"] = {
        "name": RUNTIME_COMPILATION_REPORT_NAME,
        "sha256": sha256_file(stored_report),
    }
    manifest["source_identity"] = validate_source_identity(
        source_identity if source_identity is not None else capture_source_identity(PROJECT_ROOT),
    )
    (dist_dir / "wxdf-module-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _verified_runtime_module_manifest(dist_dir: Path) -> dict:
    manifest_path = dist_dir / "wxdf-module-manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Runtime module manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Runtime module manifest is invalid: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("Runtime module manifest must be a JSON object")
    validate_source_identity(
        manifest.get("source_identity"),
        version=(PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
    )
    if manifest.get("runtime_evidence") != "compilation_report":
        raise RuntimeError("Runtime module manifest runtime_evidence must be compilation_report")
    report_identity = manifest.get("compilation_report")
    if not isinstance(report_identity, dict):
        raise RuntimeError("Runtime module manifest compilation_report must be an object")
    if report_identity.get("name") != RUNTIME_COMPILATION_REPORT_NAME:
        raise RuntimeError(
            "Runtime module manifest compilation_report name must use the fixed runtime name"
        )
    report_path = dist_dir / RUNTIME_COMPILATION_REPORT_NAME
    if not report_path.is_file():
        raise RuntimeError(f"Runtime compilation report is missing: {report_path}")
    if report_identity.get("sha256") != sha256_file(report_path):
        raise RuntimeError("Runtime compilation report sha256 does not match manifest")
    report_modules = list(_report_wxdf_modules(report_path))
    report_forbidden = [
        name for name in report_modules if _forbidden_release_module(name)
    ]
    if report_forbidden:
        raise RuntimeError(
            f"Runtime compilation report contains forbidden module: {report_forbidden[0]}"
        )
    if manifest.get("module_set_status") != "matched":
        raise RuntimeError("Runtime module manifest module_set_status must be matched")
    if manifest.get("open_modes") != mode_manifest():
        raise RuntimeError("Runtime module manifest open_modes must match authoritative native modes")
    if manifest.get("contract_versions") != MODULAR_CONTRACT_VERSIONS:
        raise RuntimeError("Runtime module manifest contract_versions must match expected versions")

    source_modules = manifest.get("source_modules")
    runtime_modules = manifest.get("runtime_modules")
    expected_modules = list(release_module_names())
    if (
        source_modules != expected_modules
        or runtime_modules != source_modules
        or report_modules != runtime_modules
    ):
        raise RuntimeError(
            "Runtime compilation report, source_modules and runtime_modules must match release module set"
        )
    for field in (
        "forbidden_runtime_modules",
        "missing_runtime_modules",
        "unexpected_runtime_modules",
    ):
        if manifest.get(field) != []:
            raise RuntimeError(f"Runtime module manifest {field} must be empty")
    forbidden = [name for name in runtime_modules if _forbidden_release_module(name)]
    if forbidden:
        raise RuntimeError(
            f"Runtime module manifest contains forbidden module: {forbidden[0]}"
        )
    return manifest


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
        *[
            f"--include-module={name}"
            for name in release_module_names()
        ],
        "--nofollow-import-to=wxdf.migration",
        "--nofollow-import-to=wxdf.legacy_*",
        *[
            f"--nofollow-import-to={name}"
            for name in sorted(RETIRED_RELEASE_MODULES - {"wxdf.migration"})
        ],
        *[
            f"--nofollow-import-to={name}"
            for name in sorted(RETIRED_RUNTIME_IMPORTS)
        ],
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
    identity = capture_source_identity(PROJECT_ROOT)
    build_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    scripts_path = str(PROJECT_ROOT / "scripts")
    previous = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = scripts_path if not previous else os.pathsep.join((scripts_path, previous))
    subprocess.run(nuitka_command(build_dir), cwd=PROJECT_ROOT, env=environment, check=True)
    if capture_source_identity(PROJECT_ROOT) != identity:
        raise RuntimeError("SOURCE_IDENTITY_MISMATCH: source changed during compilation")
    dist_dir = build_dir / "release_entrypoint.dist"
    if not dist_dir.is_dir():
        raise RuntimeError(f"Nuitka standalone directory was not created: {dist_dir}")
    ensure_runtime_layout(dist_dir)
    _write_runtime_module_manifest(dist_dir, build_dir / "compilation-report.xml", source_identity=identity)
    return dist_dir


def ensure_runtime_layout(runtime_dir: Path) -> None:
    """Restore package directories required by runtime-relative data lookups."""
    if (runtime_dir / "docx" / "templates").is_dir():
        (runtime_dir / "docx" / "parts").mkdir(parents=True, exist_ok=True)


def _copy_public_files(package_dir: Path) -> None:
    shutil.copy2(PROJECT_ROOT / "SKILL.md", package_dir / "SKILL.md")
    shutil.copy2(PUBLIC_DIR / "README.md", package_dir / "README.md")
    shutil.copy2(PROJECT_ROOT / "VERSION", package_dir / "VERSION")
    shutil.copytree(PROJECT_ROOT / "assets", package_dir / "assets")
    adapter_dir = package_dir / "scripts"
    adapter_dir.mkdir()
    for name in ("install.sh", "run.sh", "install.ps1", "run.ps1"):
        source = (
            PUBLIC_DIR / "scripts" / name
            if name == "run.sh"
            else Path(__file__).with_name(name)
        )
        target = adapter_dir / name
        shutil.copy2(source, target)
        if target.suffix == ".sh":
            target.chmod(0o755)
    shutil.copy2(
        PUBLIC_DIR / "scripts" / "validate-manifest.awk",
        adapter_dir / "validate-manifest.awk",
    )


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


def _write_integrity_files(
    package_dir: Path,
    target: str,
    version: str,
    runtime_module_manifest: dict,
) -> None:
    reject_unsafe_release_paths(package_dir)
    payload_files = sorted(
        path for path in package_dir.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.txt"}
    )
    platform_status = "candidate" if target.startswith("kylin-v10-") else "accepted"
    manifest = {
        "schema_version": 1,
        "product": "wx-doc-format-skill",
        "version": version,
        "source_identity": runtime_module_manifest["source_identity"],
        "platform": target,
        "platforms": {target: {"status": platform_status}},
        "compatibility_status": "candidate" if platform_status == "candidate" else "native",
        "offline": True,
        "public_report_summary": PUBLIC_REPORT_SUMMARY_CONTRACT,
        "modular": json.loads(json.dumps(runtime_module_manifest)),
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
    if resolved_target not in PUBLIC_PACKAGE_PLATFORM_IDS:
        raise ValueError(f"Unsupported public package target: {resolved_target}")
    runtime_module_manifest = _verified_runtime_module_manifest(dist_dir)
    reject_python_sources(dist_dir)
    reject_unsafe_release_paths(dist_dir)
    package_dir = output_dir / f"wx-doc-format-skill-{version}-{resolved_target}"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)
    _copy_public_files(package_dir)
    runtime_dir = package_dir / "runtime"
    shutil.copytree(dist_dir, runtime_dir, ignore=shutil.ignore_patterns(RUNTIME_COMPILATION_REPORT_NAME))
    ensure_runtime_layout(runtime_dir)
    shutil.copy2(PROJECT_ROOT / "VERSION", runtime_dir / "VERSION")
    expected_executable = runtime_dir / executable_name("windows" if resolved_target.startswith("windows-") else None)
    if not expected_executable.is_file():
        raise FileNotFoundError(f"Runtime executable does not exist: {expected_executable}")
    if expected_executable.suffix != ".exe":
        expected_executable.chmod(expected_executable.stat().st_mode | 0o111)
    reject_python_sources(package_dir)
    reject_unsafe_release_paths(package_dir)
    _write_integrity_files(
        package_dir,
        resolved_target,
        version,
        runtime_module_manifest,
    )
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
    parser = argparse.ArgumentParser(
        description="Compile a Nuitka standalone runtime and optionally assemble a public package."
    )
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
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Stop after compiling the standalone runtime",
    )
    parser.add_argument("--archive", action="store_true", help="Create a platform archive after assembly")
    parser.add_argument("--expected-version", default=None, help="Require VERSION to match this tag")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> Path:
    args = parse_args(argv)
    validate_expected_version(args.expected_version)
    if args.skip_compile and args.dist_dir is None:
        raise SystemExit("--skip-compile requires --dist-dir")
    if args.compile_only and args.skip_compile:
        raise SystemExit("--compile-only cannot be combined with --skip-compile")
    if args.compile_only and args.archive:
        raise SystemExit("--compile-only cannot be combined with --archive")
    identity = capture_source_identity(PROJECT_ROOT)
    dist_dir = args.dist_dir if args.skip_compile else compile_standalone(args.build_dir)
    if args.compile_only:
        runtime_dir = dist_dir.resolve()
        print(runtime_dir)
        return runtime_dir
    runtime_identity = _verified_runtime_module_manifest(dist_dir)["source_identity"]
    if any(runtime_identity[field] != identity[field] for field in ("version", "commit", "tree")):
        raise RuntimeError("SOURCE_IDENTITY_MISMATCH: existing runtime differs from the current frozen source")
    package_dir = assemble_package(dist_dir.resolve(), args.output_dir.resolve(), args.target)
    if capture_source_identity(PROJECT_ROOT) != identity:
        raise RuntimeError("SOURCE_IDENTITY_MISMATCH: source changed while assembling the package")
    print(package_dir)
    if args.archive:
        print(create_archive(package_dir, args.target or platform_id()))
    return package_dir


if __name__ == "__main__":
    main()
