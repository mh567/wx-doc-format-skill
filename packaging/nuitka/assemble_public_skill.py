from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

NUITKA_DIR = Path(__file__).resolve().parent
if str(NUITKA_DIR) not in sys.path:
    sys.path.insert(0, str(NUITKA_DIR))

from build_distribution import (
    MODULAR_CONTRACT_VERSIONS,
    RUNTIME_COMPILATION_REPORT_NAME,
    capture_source_identity,
    mode_manifest,
    release_module_names,
    validate_source_identity,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = Path(__file__).with_name("public-repo")
SUPPORTED_ARTIFACTS = {
    "macos-arm64": ".tar.gz",
    "kylin-v10-arm64": ".tar.gz",
}
GENERATED_MARKER = ".wxdf-public-skill-generated"
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_name(version: str, platform_id: str) -> str:
    return f"wx-doc-format-skill-{version}-{platform_id}{SUPPORTED_ARTIFACTS[platform_id]}"


def discover_artifacts(artifacts_dir: Path, version: str) -> dict[str, Path]:
    return {
        platform_id: artifacts_dir / artifact_name(version, platform_id)
        for platform_id in SUPPORTED_ARTIFACTS
        if (artifacts_dir / artifact_name(version, platform_id)).is_file()
    }


def verify_artifact(path: Path, version: str, platform_id: str) -> dict:
    package = f"wx-doc-format-skill-{version}-{platform_id}"
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = {}
            names = set()
            for member in archive.getmembers():
                parts = PurePosixPath(member.name).parts
                if (
                    not parts or parts[0] != package or ".." in parts
                    or any(character in member.name for character in ("\n", "\r", "\x00"))
                    or member.name in names or not (member.isfile() or member.isdir())
                ):
                    raise ValueError("unsafe or duplicate archive member")
                names.add(member.name)
                if member.isfile():
                    relative = "/".join(parts[1:])
                    if PurePosixPath(relative).suffix.lower() in {".py", ".pyc", ".pyo"} or parts[-1] == RUNTIME_COMPILATION_REPORT_NAME:
                        raise ValueError("private build payload is forbidden")
                    members[relative] = member

            def read(name: str) -> bytes:
                member = members[name]
                if member.size > 5 * 1024 * 1024:
                    raise ValueError(f"metadata exceeds size limit: {name}")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"missing {name}")
                with stream:
                    return stream.read()

            if read("VERSION").decode("utf-8").strip() != version:
                raise ValueError("VERSION mismatch")
            manifest = json.loads(read("manifest.json"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be an object")
            if manifest.get("version") != version or manifest.get("platform") != platform_id:
                raise ValueError("manifest version or platform mismatch")
            if read("runtime/VERSION").decode("utf-8").strip() != version:
                raise ValueError("runtime VERSION mismatch")
            identity = validate_source_identity(manifest.get("source_identity"), version=version)
            modular = manifest.get("modular")
            if not isinstance(modular, dict) or modular.get("source_identity") != identity:
                raise ValueError("SOURCE_IDENTITY_MISMATCH: package and runtime identities differ")
            if json.loads(read("runtime/wxdf-module-manifest.json")) != modular:
                raise ValueError("runtime module manifest differs from package summary")
            if (
                modular.get("source_modules") != list(release_module_names())
                or modular.get("runtime_modules") != modular["source_modules"]
                or modular.get("module_set_status") != "matched"
                or modular.get("runtime_evidence") != "compilation_report"
                or modular.get("open_modes") != mode_manifest()
                or modular.get("contract_versions") != MODULAR_CONTRACT_VERSIONS
                or any(modular.get(field) != [] for field in ("forbidden_runtime_modules", "missing_runtime_modules", "unexpected_runtime_modules"))
            ):
                raise ValueError("runtime module or capability contract mismatch")
            compilation = modular.get("compilation_report")
            if not isinstance(compilation, dict) or compilation.get("name") != RUNTIME_COMPILATION_REPORT_NAME or not re.fullmatch(r"[0-9a-f]{64}", str(compilation.get("sha256"))):
                raise ValueError("compilation evidence digest is invalid")
            entries = manifest.get("files")
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise ValueError("manifest file list is invalid")
            expected = {entry["path"]: entry for entry in entries}
            if len(expected) != len(entries) or set(members) != set(expected) | {"manifest.json", "SHA256SUMS.txt"}:
                raise ValueError("archive file set differs from manifest")
            sums = {}
            for line in read("SHA256SUMS.txt").decode("utf-8").splitlines():
                if len(line) < 67 or line[64:66] != "  " or not re.fullmatch(r"[0-9a-f]{64}", line[:64]) or line[66:] in sums:
                    raise ValueError("checksum file is invalid")
                sums[line[66:]] = line[:64]
            if set(sums) != set(expected) | {"manifest.json"}:
                raise ValueError("checksum file set differs from manifest")
            for name, expected_hash in sums.items():
                member = members[name]
                digest = hashlib.sha256()
                with archive.extractfile(member) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected_hash:
                    raise ValueError(f"checksum mismatch: {name}")
                if name in expected and (expected[name].get("sha256") != expected_hash or expected[name].get("size") != member.size):
                    raise ValueError(f"manifest content mismatch: {name}")
            return manifest
    except (tarfile.TarError, KeyError, OSError, ValueError, UnicodeError, RuntimeError, TypeError) as exc:
        raise ValueError(f"ARTIFACT_INVALID: {path.name}: {exc}") from exc


def assemble_public_skill(
    artifacts_dir: Path,
    output_dir: Path,
    required_platforms: tuple[str, ...] = (),
    macos_min_version: str | None = None,
    expected_source_identity: dict | None = None,
) -> Path:
    version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError("VERSION is empty")
    unknown = sorted(set(required_platforms) - set(SUPPORTED_ARTIFACTS))
    if unknown:
        raise ValueError(f"Unsupported required platforms: {', '.join(unknown)}")
    artifacts = discover_artifacts(artifacts_dir, version)
    missing = sorted(set(required_platforms) - set(artifacts))
    if missing:
        raise FileNotFoundError(f"Required release artifacts are missing: {', '.join(missing)}")
    if not artifacts:
        raise FileNotFoundError("No supported release artifacts were found")
    if macos_min_version and not re.fullmatch(r"\d+(?:\.\d+){0,2}", macos_min_version):
        raise ValueError("macOS minimum version must contain one to three numeric components")
    if "macos-arm64" in required_platforms and not macos_min_version:
        raise ValueError("macOS minimum version is required for an accepted macos-arm64 release")

    if output_dir.exists() and not (output_dir / GENERATED_MARKER).is_file():
        raise ValueError(f"Output directory is not a generated public Skill: {output_dir}")
    artifact_manifests = {
        target: verify_artifact(path, version, target)
        for target, path in artifacts.items()
    }
    source_keys = ("version", "commit", "tree")
    identities = {
        tuple(manifest["source_identity"][key] for key in source_keys)
        for manifest in artifact_manifests.values()
    }
    if len(identities) != 1:
        raise ValueError("SOURCE_IDENTITY_MISMATCH: release platforms must use the same source commit and tree")
    source_identity = dict(zip(source_keys, next(iter(identities))))
    if expected_source_identity is not None:
        expected = validate_source_identity(expected_source_identity, version=version)
        if source_identity != {key: expected[key] for key in source_keys}:
            raise ValueError("SOURCE_IDENTITY_MISMATCH: artifacts must match the frozen public Skill checkout")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / GENERATED_MARKER).write_text("generated\n", encoding="ascii")
    shutil.copy2(PROJECT_ROOT / "SKILL.md", output_dir / "SKILL.md")
    shutil.copy2(TEMPLATE_ROOT / "README.md", output_dir / "README.md")
    shutil.copy2(PROJECT_ROOT / "VERSION", output_dir / "VERSION")
    shutil.copytree(PROJECT_ROOT / "assets", output_dir / "assets")
    shutil.copytree(TEMPLATE_ROOT / "scripts", output_dir / "scripts")
    for script in (output_dir / "scripts").glob("*.sh"):
        script.chmod(0o755)

    metadata_dir = output_dir / "artifacts"
    metadata_dir.mkdir()
    sorted_artifacts = sorted(artifacts.items())
    platforms = {}
    for platform_id in sorted(SUPPORTED_ARTIFACTS):
        path = artifacts.get(platform_id)
        if platform_id == "macos-arm64":
            status = "accepted" if path is not None and macos_min_version else "unavailable"
        else:
            status = "candidate" if path is not None else "unavailable"
        entry = {"status": status}
        if path is not None:
            entry.update({
                "name": path.name,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            })
        platforms[platform_id] = entry
    sums = "".join(
        f"{sha256_file(path)}  {path.name}\n"
        for _, path in sorted_artifacts
    )
    (metadata_dir / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    (metadata_dir / "AVAILABLE_PLATFORMS.txt").write_text(
        "".join(
            f"{platform_id}\n"
            for platform_id, entry in platforms.items()
            if entry["status"] in {"accepted", "candidate"}
        ),
        encoding="utf-8",
    )
    (metadata_dir / "MACOS_MIN_VERSION.txt").write_text(
        f"{macos_min_version or 'unknown'}\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "version": version,
        "source_identity": source_identity,
        "builds": [{"platform": target, "source_identity": item["source_identity"]} for target, item in sorted(artifact_manifests.items())],
        "platforms": platforms,
        "available_platforms": [
            platform_id
            for platform_id, entry in platforms.items()
            if entry["status"] in {"accepted", "candidate"}
        ],
        "unavailable_platforms": [
            platform_id
            for platform_id, entry in platforms.items()
            if entry["status"] == "unavailable"
        ],
        "macos_min_version": macos_min_version,
        "public_report_summary": PUBLIC_REPORT_SUMMARY_CONTRACT,
        "artifacts": [
            {
                "platform": platform_id,
                "name": path.name,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
            for platform_id, path in sorted_artifacts
        ],
    }
    (metadata_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assemble the thin public Skill repository.")
    parser.add_argument("--artifacts-dir", type=Path, default=PROJECT_ROOT / "dist" / "local")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "dist" / "public-skill")
    parser.add_argument(
        "--require-platform",
        action="append",
        default=[],
        choices=tuple(SUPPORTED_ARTIFACTS),
        help="Require an artifact for this platform. May be repeated.",
    )
    parser.add_argument(
        "--macos-min-version",
        help="Minimum macOS version recorded for the macOS ARM64 runtime",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> Path:
    args = parse_args(argv)
    output = assemble_public_skill(
        args.artifacts_dir.resolve(),
        args.output_dir.resolve(),
        tuple(args.require_platform),
        args.macos_min_version,
        capture_source_identity(PROJECT_ROOT),
    )
    print(output)
    return output


if __name__ == "__main__":
    main()
