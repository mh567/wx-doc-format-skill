"""Assemble the thin public Skill tree from interpreted packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import sys
import tarfile
import unicodedata
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
NUITKA_DIR = HERE.parent / "nuitka"
PUBLIC_TEMPLATE = NUITKA_DIR / "public-repo"

SUPPORTED_ARTIFACTS = {
    "macos-arm64": ".tar.gz",
    "kylin-v10-arm64": ".tar.gz",
}
GENERATED_MARKER = ".wxdf-public-skill-generated"
FIRST_PARTY_PYTHON = {"lib/boot.py"}
BUNDLED_PYTHON_PREFIXES = ("vendor/", "runtime/python/")
PAYLOAD_MAGIC = b"WXDFPAY1"
PAYLOAD_VERSION = 1
PAYLOAD_ENTRY = struct.Struct("<8sB12sII32s")


def check_payload(data: bytes, declared: dict) -> None:
    if not data.startswith(PAYLOAD_MAGIC):
        raise ValueError("payload.bin has an unsupported header")
    version, count = struct.unpack_from("<IH", data, len(PAYLOAD_MAGIC))
    if version != PAYLOAD_VERSION:
        raise ValueError("payload.bin format version is unsupported")
    if declared.get("module_count") != count:
        raise ValueError("payload module count differs from the declaration")
    if not re.fullmatch(r"[0-9a-f]{64}", str(declared.get("module_digest"))):
        raise ValueError("payload module digest is invalid")
    header_end = len(PAYLOAD_MAGIC) + 6 + count * PAYLOAD_ENTRY.size
    if header_end > len(data):
        raise ValueError("payload.bin is truncated")
    ranges = []
    offset = len(PAYLOAD_MAGIC) + 6
    for _ in range(count):
        _, _, _, start, length, _ = PAYLOAD_ENTRY.unpack_from(data, offset)
        offset += PAYLOAD_ENTRY.size
        if start < header_end or start + length > len(data):
            raise ValueError("payload.bin has an out-of-range module")
        ranges.append((start, start + length))
    ranges.sort()
    for (_, end), (next_start, _) in zip(ranges, ranges[1:]):
        if next_start < end:
            raise ValueError("payload modules overlap")


def python_range(tags: list[str]) -> str:
    def version(tag: str) -> str:
        digits = tag.removeprefix("cp")
        return f"{digits[0]}.{digits[1:]}"

    ordered = sorted(tags)
    return version(ordered[0]) if len(ordered) == 1 else f"{version(ordered[0])}-{version(ordered[-1])}"


PUBLIC_REPORT_SUMMARY_CONTRACT = {
    "schema_version": "1.0",
    "field": "public_summary",
    "required_fields": [
        "status",
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

    def collision_key(member_path: PurePosixPath) -> str:
        value = member_path.as_posix()
        return unicodedata.normalize("NFD", value.casefold()) if platform_id == "macos-arm64" else value

    try:
        with tarfile.open(path, "r:gz") as archive:
            members = {}
            names = set()
            path_records = {}
            for member in archive.getmembers():
                member_path = PurePosixPath(member.name)
                parts = list(member_path.parts)
                normalized = collision_key(member_path)
                if (
                    member_path.is_absolute()
                    or not parts
                    or parts[0] != package
                    or any(part in {".", ".."} for part in parts)
                    or "//" in member.name
                    or any(character in member.name for character in ("\n", "\r", "\x00"))
                    or normalized in names
                    or not (member.isfile() or member.isdir())
                    or (member.name.endswith("/") and not member.isdir())
                ):
                    raise ValueError("unsafe or duplicate archive member")
                required_owner = 0o500 if member.isdir() else 0o400
                if member.mode & required_owner != required_owner:
                    raise ValueError(f"archive member lacks required owner permissions {required_owner:o}: {member.name}")
                for current_path in (member_path, *member_path.parents):
                    if current_path == PurePosixPath("."):
                        continue
                    key = collision_key(current_path)
                    original = current_path.as_posix()
                    directory = member.isdir() or current_path != member_path
                    previous = path_records.get(key)
                    if previous is not None:
                        if previous[0] != original:
                            raise ValueError(f"archive paths alias the same target: {previous[0]} and {original}")
                        if previous[1] != directory:
                            raise ValueError("archive file conflicts with a directory or descendant")
                    path_records.setdefault(key, (original, directory))
                names.add(normalized)
                if member.isfile():
                    relative = member_path.relative_to(package).as_posix()
                    suffix = PurePosixPath(relative).suffix.lower()
                    if suffix in {".pyc", ".pyo"} and not relative.startswith(BUNDLED_PYTHON_PREFIXES):
                        raise ValueError("bytecode is forbidden in the package")
                    if suffix == ".py" and relative not in FIRST_PARTY_PYTHON and not relative.startswith(BUNDLED_PYTHON_PREFIXES):
                        raise ValueError(f"Python source outside the bundled runtime is forbidden: {relative}")
                    members[relative] = member

            for required in ("scripts/run.sh", "scripts/install.sh"):
                member = members.get(required)
                if member is None or not member.isfile() or not member.mode & 0o100:
                    raise ValueError(f"required executable is missing or not owner-executable: {required}")
            for required in ("lib/boot.py", "lib/payload.bin", "lib/payload.json"):
                member = members.get(required)
                if member is None or not member.isfile():
                    raise ValueError(f"required runtime file is missing: {required}")

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
            declared = manifest.get("platforms")
            if not isinstance(declared, dict) or not isinstance(declared.get(platform_id), dict):
                raise ValueError("manifest does not declare this platform")
            if declared[platform_id].get("status") not in {"accepted", "candidate"}:
                raise ValueError("manifest platform status is invalid")
            identity = manifest.get("source_identity")
            for field in ("version", "commit", "tree"):
                if not isinstance(identity, dict) or not isinstance(identity.get(field), str) or not identity[field]:
                    raise ValueError(f"source identity is missing {field}")
            if identity["version"] != version:
                raise ValueError("source identity version mismatch")

            runtime = manifest.get("runtime")
            if not isinstance(runtime, dict) or runtime.get("kind") != "interpreted":
                raise ValueError("runtime kind must be interpreted")
            payload = runtime.get("payload")
            if not isinstance(payload, dict) or payload.get("payload") != "payload.bin":
                raise ValueError("payload metadata is invalid")
            payload_bytes = read("lib/payload.bin")
            if hashlib.sha256(payload_bytes).hexdigest() != payload.get("payload_sha256"):
                raise ValueError("payload digest mismatch")
            if len(payload_bytes) != payload.get("payload_size"):
                raise ValueError("payload size mismatch")
            check_payload(payload_bytes, payload)

            tags = runtime.get("python_tags")
            if (
                not isinstance(tags, list)
                or not tags
                or any(not re.fullmatch(r"cp3\d\d", str(tag)) for tag in tags)
                or len(set(tags)) != len(tags)
            ):
                raise ValueError("runtime python tags are invalid")
            if runtime.get("python_requires") != python_range(tags):
                raise ValueError("runtime python requirement differs from the vendored tags")
            if "vendor/common/vendor.json" not in members:
                raise ValueError("shared vendor tree is missing")
            for tag in tags:
                if f"vendor/{tag}/vendor.json" not in members:
                    raise ValueError(f"vendor tree for {tag} is missing")
            bundled = runtime.get("bundled_python")
            if bundled is not None:
                entry = members.get(str(bundled))
                if entry is None or not entry.isfile() or not entry.mode & 0o100:
                    raise ValueError("bundled interpreter is missing or not executable")

            modular = manifest.get("modular")
            if not isinstance(modular, dict):
                raise ValueError("modular contract is missing")
            if modular.get("source_modules") != modular.get("runtime_modules"):
                raise ValueError("runtime module surface differs from the source surface")
            if modular.get("module_set_status") != "matched":
                raise ValueError("runtime module set is not matched")
            if modular.get("runtime_evidence") != "payload_manifest":
                raise ValueError("runtime evidence must be the payload manifest")
            payload_manifest = modular.get("payload_manifest")
            if not isinstance(payload_manifest, dict) or payload_manifest.get("name") != "payload.json":
                raise ValueError("payload manifest declaration is invalid")
            if hashlib.sha256(read("lib/payload.json")).hexdigest() != payload_manifest.get("sha256"):
                raise ValueError("payload manifest digest mismatch")

            entries = manifest.get("files")
            if entries is not None:
                raise ValueError("interpreted packages must not declare a file list")
            sums = {}
            for line in read("SHA256SUMS.txt").decode("utf-8").splitlines():
                if len(line) < 67 or line[64:66] != "  " or not re.fullmatch(r"[0-9a-f]{64}", line[:64]) or line[66:] in sums:
                    raise ValueError("checksum file is invalid")
                sums[line[66:]] = line[:64]
            if set(sums) != set(members) - {"SHA256SUMS.txt"}:
                raise ValueError("checksum file set differs from the archive")
            for name, expected_hash in sums.items():
                member = members[name]
                digest = hashlib.sha256()
                with archive.extractfile(member) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected_hash:
                    raise ValueError(f"checksum mismatch: {name}")
            return manifest
    except (tarfile.TarError, KeyError, OSError, ValueError, UnicodeError, RuntimeError, TypeError) as exc:
        raise ValueError(f"ARTIFACT_INVALID: {path.name}: {exc}") from exc


def assemble_public_skill(
    artifacts_dir: Path,
    output_dir: Path,
    required_platforms: tuple[str, ...] = (),
    macos_min_version: str | None = None,
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

    manifests = {target: verify_artifact(path, version, target) for target, path in artifacts.items()}
    identities = {
        tuple(manifest["source_identity"][key] for key in ("version", "commit", "tree"))
        for manifest in manifests.values()
    }
    if len(identities) != 1:
        raise ValueError("SOURCE_IDENTITY_MISMATCH: release platforms must use the same source commit and tree")
    source_identity = dict(zip(("version", "commit", "tree"), next(iter(identities))))

    if output_dir.exists():
        if not (output_dir / GENERATED_MARKER).is_file():
            raise ValueError(f"Output directory is not a generated public Skill: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / GENERATED_MARKER).write_text("generated\n", encoding="ascii")
    shutil.copy2(PROJECT_ROOT / "SKILL.md", output_dir / "SKILL.md")
    shutil.copy2(HERE / "public-repo" / "README.md", output_dir / "README.md")
    shutil.copy2(PROJECT_ROOT / "VERSION", output_dir / "VERSION")
    shutil.copytree(PROJECT_ROOT / "assets", output_dir / "assets")
    shutil.copytree(PUBLIC_TEMPLATE / "scripts", output_dir / "scripts")
    for script in (output_dir / "scripts").glob("*.sh"):
        script.chmod(0o755)

    metadata_dir = output_dir / "artifacts"
    metadata_dir.mkdir()
    sorted_artifacts = sorted(artifacts.items())
    platforms = {}
    for platform_id in sorted(SUPPORTED_ARTIFACTS):
        path = artifacts.get(platform_id)
        if path is None:
            platforms[platform_id] = {"status": "unavailable"}
            continue
        status = manifests[platform_id]["platforms"][platform_id]["status"]
        if status == "accepted" and platform_id == "macos-arm64" and not macos_min_version:
            raise ValueError("accepted macos-arm64 artifacts require --macos-min-version")
        platforms[platform_id] = {
            "status": status,
            "name": path.name,
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
    for platform_id in required_platforms:
        if platforms[platform_id]["status"] not in {"accepted", "candidate"}:
            raise ValueError(f"REQUIRED_PLATFORM_UNAVAILABLE: {platform_id}")
    (metadata_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for _, path in sorted_artifacts),
        encoding="utf-8",
    )
    (metadata_dir / "AVAILABLE_PLATFORMS.txt").write_text(
        "".join(f"{pid}\n" for pid, entry in platforms.items() if entry["status"] in {"accepted", "candidate"}),
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
        "builds": [
            {"platform": target, "source_identity": item["source_identity"]}
            for target, item in sorted(manifests.items())
        ],
        "platforms": platforms,
        "available_platforms": [pid for pid, entry in platforms.items() if entry["status"] in {"accepted", "candidate"}],
        "unavailable_platforms": [pid for pid, entry in platforms.items() if entry["status"] == "unavailable"],
        "macos_min_version": macos_min_version,
        "runtime_kind": "interpreted",
        "public_report_summary": PUBLIC_REPORT_SUMMARY_CONTRACT,
        "artifacts": [
            {"platform": pid, "name": path.name, "sha256": sha256_file(path), "size": path.stat().st_size}
            for pid, path in sorted_artifacts
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
    parser.add_argument("--require-platform", action="append", default=[], choices=tuple(SUPPORTED_ARTIFACTS))
    parser.add_argument("--macos-min-version")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = assemble_public_skill(
        args.artifacts_dir.resolve(),
        args.output_dir.resolve(),
        tuple(args.require_platform),
        args.macos_min_version,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
