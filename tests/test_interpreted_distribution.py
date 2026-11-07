from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INTERPRETED = PROJECT_ROOT / "packaging" / "interpreted"
if str(INTERPRETED) not in sys.path:
    sys.path.insert(0, str(INTERPRETED))

import assemble_public_skill
import boot
import build_package
import build_payload

IDENTITY = {
    "version": "9.9.9",
    "commit": "a" * 40,
    "tree": "b" * 40,
    "toolchain": {
        "python": "3.13.0",
        "runtime": "interpreted",
        "system": "Darwin",
        "machine": "arm64",
        "build_image": "native",
    },
}


def _vendor(tmp_path: Path) -> Path:
    root = tmp_path / "vendor-root"
    common = root / "common"
    common.mkdir(parents=True)
    (common / "vendor.json").write_text('{"schema_version": 1, "wheels": []}\n', encoding="utf-8")
    (common / "placeholder.txt").write_text("common\n", encoding="utf-8")
    native = root / "cp313"
    native.mkdir()
    (native / "vendor.json").write_text('{"schema_version": 1, "wheels": []}\n', encoding="utf-8")
    (native / "placeholder.txt").write_text("native\n", encoding="utf-8")
    return root


def _package(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "scripts"
    (source_root / "wxdf").mkdir(parents=True)
    (source_root / "main.py").write_text(
        "def main(argv=None):\n    import wxdf.core\n    print(wxdf.core.VALUE)\n",
        encoding="utf-8",
    )
    (source_root / "wxdf" / "__init__.py").write_text("", encoding="utf-8")
    (source_root / "wxdf" / "core.py").write_text(
        "from .helper import VALUE\n",
        encoding="utf-8",
    )
    (source_root / "wxdf" / "helper.py").write_text("VALUE = 'payload-ok'\n", encoding="utf-8")

    payload_dir = tmp_path / "payload"
    blob, summary, index = build_payload.build_payload(source_root)
    build_payload.write_payload(payload_dir, blob, summary, [], None)

    package_dir = tmp_path / "out" / "wx-doc-format-skill-9.9.9-macos-arm64"
    surface = build_package.release_module_names
    build_package.release_module_names = lambda: ["wxdf", "wxdf.core", "wxdf.helper"]
    try:
        build_package.compose_package(
            package_dir,
            platform_id="macos-arm64",
            status="accepted",
            identity=IDENTITY,
            payload_dir=payload_dir,
            payload_index=index,
            vendor_root=_vendor(tmp_path),
            readme=INTERPRETED / "public-repo" / "README.md",
            runtime_python=None,
        )
    finally:
        build_package.release_module_names = surface
    archive = build_package.write_archive(package_dir)
    return package_dir, archive


def _rebuild(package_dir: Path) -> Path:
    build_package.write_sums(package_dir)
    return build_package.write_archive(package_dir)


def test_payload_loader_serves_modules_with_virtual_paths(tmp_path: Path) -> None:
    package_dir, _ = _package(tmp_path)
    probe = (
        "import sys;"
        f"sys.path.insert(0, {str(package_dir / 'lib')!r});"
        "import boot;"
        "boot.install();"
        "import main;"
        "import wxdf.core;"
        "main.main([]);"
        "print(main.__file__);"
        "print(wxdf.core.__file__)"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "payload-ok"
    assert lines[1].endswith("scripts/main.py")
    assert lines[2].endswith("scripts/wxdf/core.py")


def test_artifact_verification_accepts_interpreted_package(tmp_path: Path) -> None:
    _, archive = _package(tmp_path)
    manifest = assemble_public_skill.verify_artifact(archive, "9.9.9", "macos-arm64")
    assert manifest["runtime"]["kind"] == "interpreted"
    assert manifest["modular"]["runtime_evidence"] == "payload_manifest"


def test_artifact_rejects_first_party_python_outside_boot(tmp_path: Path) -> None:
    package_dir, _ = _package(tmp_path)
    (package_dir / "scripts" / "leak.py").write_text("SECRET = 1\n", encoding="utf-8")
    archive = _rebuild(package_dir)
    with pytest.raises(ValueError, match="Python source outside the bundled runtime"):
        assemble_public_skill.verify_artifact(archive, "9.9.9", "macos-arm64")


def test_artifact_rejects_bytecode(tmp_path: Path) -> None:
    package_dir, _ = _package(tmp_path)
    (package_dir / "lib" / "stale.pyc").write_bytes(b"\x00")
    archive = _rebuild(package_dir)
    with pytest.raises(ValueError, match="bytecode is forbidden"):
        assemble_public_skill.verify_artifact(archive, "9.9.9", "macos-arm64")


def test_artifact_rejects_payload_digest_mismatch(tmp_path: Path) -> None:
    package_dir, _ = _package(tmp_path)
    manifest_path = package_dir / "manifest.json"
    manifest = build_package.json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime"]["payload"]["payload_sha256"] = "0" * 64
    manifest_path.write_text(build_package.json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = _rebuild(package_dir)
    with pytest.raises(ValueError, match="payload digest mismatch"):
        assemble_public_skill.verify_artifact(archive, "9.9.9", "macos-arm64")


def test_artifact_rejects_payload_count_mismatch(tmp_path: Path) -> None:
    package_dir, _ = _package(tmp_path)
    manifest_path = package_dir / "manifest.json"
    manifest = build_package.json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime"]["payload"]["module_count"] = 999
    manifest_path.write_text(build_package.json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = _rebuild(package_dir)
    with pytest.raises(ValueError, match="module count"):
        assemble_public_skill.verify_artifact(archive, "9.9.9", "macos-arm64")


def test_vendor_directory_requires_a_python_tag(tmp_path: Path) -> None:
    empty = tmp_path / "vendor-root"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="no vendor directories"):
        build_package.vendor_tags(empty)
