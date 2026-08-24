from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = Path(__file__).with_name("public-repo")
SUPPORTED_ARTIFACTS = {
    "macos-arm64": ".tar.gz",
    "kylin-v10-arm64": ".tar.gz",
    "windows-x86_64": ".zip",
}
GENERATED_MARKER = ".wxdf-public-skill-generated"


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

    if output_dir.exists() and not (output_dir / GENERATED_MARKER).is_file():
        raise ValueError(f"Output directory is not a generated public Skill: {output_dir}")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / GENERATED_MARKER).write_text("generated\n", encoding="ascii")
    for name in ("SKILL.md", "README.md"):
        shutil.copy2(TEMPLATE_ROOT / name, output_dir / name)
    shutil.copy2(PROJECT_ROOT / "VERSION", output_dir / "VERSION")
    shutil.copytree(PROJECT_ROOT / "assets", output_dir / "assets")
    shutil.copytree(TEMPLATE_ROOT / "scripts", output_dir / "scripts")
    for script in (output_dir / "scripts").glob("*.sh"):
        script.chmod(0o755)

    metadata_dir = output_dir / "artifacts"
    metadata_dir.mkdir()
    sorted_artifacts = sorted(artifacts.items())
    sums = "".join(
        f"{sha256_file(path)}  {path.name}\n"
        for _, path in sorted_artifacts
    )
    (metadata_dir / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    (metadata_dir / "AVAILABLE_PLATFORMS.txt").write_text(
        "".join(f"{platform_id}\n" for platform_id, _ in sorted_artifacts),
        encoding="utf-8",
    )
    (metadata_dir / "MACOS_MIN_VERSION.txt").write_text(
        f"{macos_min_version or 'unknown'}\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "version": version,
        "available_platforms": [platform_id for platform_id, _ in sorted_artifacts],
        "unavailable_platforms": [
            platform_id for platform_id in SUPPORTED_ARTIFACTS if platform_id not in artifacts
        ],
        "macos_min_version": macos_min_version,
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
    )
    print(output)
    return output


if __name__ == "__main__":
    main()
