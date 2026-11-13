from __future__ import annotations

import json
import os
import sys
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_offline_package as package
import offline_install


IMPORT_NAMES = {"python-docx": "docx", "markdown-it-py": "markdown_it"}


def _fake_download(requirements, destination, **kwargs):
    destination.mkdir(parents=True, exist_ok=True)
    project, version = requirements[0].split("==")
    name = project.replace("-", "_")
    module = IMPORT_NAMES.get(project, name)
    wheel = destination / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr(f"{module}/__init__.py", f"# {module}\n")
        handle.writestr(f"{name}-{version}.dist-info/METADATA", requirements[0])
    return [wheel]


def _host_platform():
    try:
        return offline_install._platform_id()
    except ValueError:
        pytest.skip("offline packages only target macOS ARM64 and Linux ARM64")


def _build_host_wheelhouse(destination, platform_id, tmp_path):
    if platform_id == "macos-arm64":
        native = tmp_path / "lxml-5.4.0-cp38-cp38-macosx_11_0_arm64.whl"
        with zipfile.ZipFile(native, "w") as handle:
            handle.writestr("lxml/__init__.py", "# lxml\n")
        package.build_wheelhouse(
            destination, platform_id, macos_cp38_wheel=native,
        )
    else:
        package.build_wheelhouse(destination, platform_id)


@pytest.fixture
def wheelhouse(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "pip_download", _fake_download)
    destination = tmp_path / "wheelhouse"
    _build_host_wheelhouse(destination, _host_platform(), tmp_path)
    return destination


def selected_modules(manifest, tag):
    modules = set()
    for name in manifest["environments"][tag]:
        distribution = name.split("/", 1)[1].split("-", 1)[0]
        project = distribution.replace("_", "-")
        modules.add(IMPORT_NAMES.get(project, distribution))
    return modules


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("darwin", "arm64", "macos-arm64"),
        ("linux", "aarch64", "linux-arm64"),
        ("linux", "arm64", "linux-arm64"),
    ],
)
def test_detects_supported_platforms(system, machine, expected):
    assert offline_install._detect_platform(system, machine) == expected


def test_rejects_unsupported_platform():
    with pytest.raises(ValueError, match="unsupported platform"):
        offline_install._detect_platform("win32", "AMD64")


def test_rejects_package_for_another_platform(wheelhouse, tmp_path, monkeypatch):
    monkeypatch.setattr(offline_install, "_platform_id", lambda: "other-arm64")
    destination = tmp_path / "deps"
    with pytest.raises(ValueError, match="targets"):
        offline_install.install(wheelhouse, destination, "cp38")
    assert not destination.exists()


def test_main_reports_platform_mismatch(wheelhouse, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(offline_install, "_platform_id", lambda: "other-arm64")
    code = offline_install.main([str(wheelhouse), str(tmp_path / "deps")])
    assert code == 3
    assert "WXDF-E-VENDOR-UNAVAILABLE" in capsys.readouterr().err


def test_accepts_package_for_current_platform(wheelhouse, tmp_path):
    destination = tmp_path / "deps"
    manifest = json.loads((wheelhouse / "manifest.json").read_text())
    assert manifest["platform"] == offline_install._platform_id()
    assert offline_install.install(wheelhouse, destination, "cp38") is True


def test_wheelhouse_contains_only_manifest_and_bundle(wheelhouse):
    assert {path.name for path in wheelhouse.iterdir()} == {"manifest.json", "wheels.tar.xz"}


def test_package_preserves_skill_reference_documents(tmp_path, monkeypatch):
    source = tmp_path / "source"
    reference = source / "references/ai-conversion.md"
    reference.parent.mkdir(parents=True)
    reference.write_text("AI response protocol", encoding="utf-8")
    installer = source / "scripts/offline_install.py"
    installer.parent.mkdir()
    installer.write_text("", encoding="utf-8")
    monkeypatch.setattr(package, "ROOT", source)
    monkeypatch.setattr(package, "tracked_files", lambda: ["references/ai-conversion.md"])
    destination = tmp_path / "skill"

    package.stage_source(destination)

    assert (destination / "references/ai-conversion.md").read_bytes() == reference.read_bytes()


@pytest.mark.parametrize("platform_id", package.PLATFORMS)
def test_package_covers_all_python_versions(tmp_path, monkeypatch, platform_id):
    def download(requirements, destination, **kwargs):
        destination.mkdir(parents=True, exist_ok=True)
        project, version = requirements[0].split("==")
        path = destination / f"{project.replace('-', '_')}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as handle:
            handle.writestr("fixture", requirements[0])
        return [path]

    monkeypatch.setattr(package, "pip_download", download)
    monkeypatch.setattr(package, "stage_source", lambda destination: None)
    native = tmp_path / "lxml-5.4.0-cp38-cp38-macosx_11_0_arm64.whl"
    with zipfile.ZipFile(native, "w") as handle:
        handle.writestr("fixture", "lxml==5.4.0")
    archive = package.build(platform_id, tmp_path, macos_cp38_wheel=native)
    assert archive.name.endswith(f"-{platform_id}.zip")
    assert archive.stat().st_size < 14_000_000
    with zipfile.ZipFile(archive) as handle:
        assert handle.testzip() is None
        manifest = json.loads(handle.read("wx-doc-format/wheelhouse/manifest.json"))
        assert manifest["platform"] == platform_id
        assert manifest["python_versions"] == [f"3.{minor}" for minor in range(8, 15)]
        assert {record["python_tag"] for record in manifest["wheels"]} == {
            "common", *(f"cp3{minor}" for minor in range(8, 15)),
        }
        assert set(manifest["environments"]) == {f"cp3{minor}" for minor in range(8, 15)}
        for tag, selected in manifest["environments"].items():
            assert len(selected) == 5
            expected = (
                ("python_docx-1.1.2", "markdown_it_py-3.0.0", "typing_extensions-4.13.2")
                if tag == "cp38" else
                ("python_docx-1.2.0", "markdown_it_py-3.0.0", "typing_extensions-4.16.0")
                if tag == "cp39" else
                ("python_docx-1.2.0", "markdown_it_py-4.2.0", "typing_extensions-4.16.0")
            )
            for pin in expected:
                assert any(Path(name).name.startswith(pin + "-") for name in selected)
        with tarfile.open(fileobj=io.BytesIO(handle.read(
            "wx-doc-format/wheelhouse/wheels.tar.xz"
        )), mode="r:xz") as bundle:
            for record in manifest["wheels"]:
                path = f"{record['python_tag']}/{record['wheel']}"
                payload = bundle.extractfile(path).read()
                assert package.hashlib.sha256(payload).hexdigest() == record["sha256"]
                with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
                    assert wheel.testzip() is None
                    assert all(i.compress_type == zipfile.ZIP_STORED for i in wheel.infolist())


@pytest.mark.parametrize("minor", range(8, 15))
def test_installs_only_matching_wheels(wheelhouse, tmp_path, minor):
    tag = f"cp3{minor}"
    destination = tmp_path / "deps"
    assert offline_install.install(wheelhouse, destination, tag) is True
    manifest = json.loads((wheelhouse / "manifest.json").read_text())
    expected = selected_modules(manifest, tag)
    installed = {
        path.name for path in destination.iterdir()
        if path.is_dir() and not path.name.endswith(".dist-info")
    }
    assert installed == expected
    state = json.loads((destination / offline_install.STATE_FILE).read_text())
    assert state["python_tag"] == tag
    assert expected <= set(state["top_level"])
    for module in expected:
        assert (destination / module / "__init__.py").is_file()


def test_reuses_verified_cache(wheelhouse, tmp_path):
    destination = tmp_path / "deps"
    assert offline_install.install(wheelhouse, destination, "cp38") is True
    sentinel = destination / "sentinel"
    sentinel.write_text("cached")
    assert offline_install.install(wheelhouse, destination, "cp38") is False
    assert sentinel.exists()
    state = json.loads((destination / offline_install.STATE_FILE).read_text())
    state["bundle_sha256"] = "0" * 64
    (destination / offline_install.STATE_FILE).write_text(json.dumps(state))
    assert offline_install.install(wheelhouse, destination, "cp38") is True
    assert not sentinel.exists()


@pytest.mark.parametrize("damage", ("missing", "tampered", "truncated", "legacy"))
def test_cache_requires_complete_file_inventory(wheelhouse, tmp_path, damage):
    destination = tmp_path / "deps"
    assert offline_install.install(wheelhouse, destination, "cp38") is True
    sentinel = destination / "sentinel"
    sentinel.write_text("cached")
    state_path = destination / offline_install.STATE_FILE
    state = json.loads(state_path.read_text())
    if damage == "legacy":
        state.pop("files")
        state_path.write_text(json.dumps(state))
        target = destination / "docx" / "__init__.py"
    else:
        entry = next(item for item in state["files"] if item["size"] > 0)
        target = destination / entry["path"]
        if damage == "missing":
            target.unlink()
        elif damage == "tampered":
            target.write_bytes(b"tampered")
        else:
            target.write_bytes(target.read_bytes()[:1])
    assert offline_install.install(wheelhouse, destination, "cp38") is True
    assert not sentinel.exists()
    assert target.is_file()
    restored = json.loads(state_path.read_text())
    assert restored["files"]
    for entry in restored["files"]:
        path = destination / entry["path"]
        assert path.is_file()
        assert path.stat().st_size == entry["size"]
        assert package.sha256_file(path) == entry["sha256"]


def test_concurrent_first_install_succeeds(wheelhouse, tmp_path):
    destination = tmp_path / "deps"
    command = [
        sys.executable, "-B", str(ROOT / "scripts" / "offline_install.py"),
        str(wheelhouse), str(destination),
    ]
    processes = [subprocess.Popen(command, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = [process.communicate() for process in processes]
    assert [process.returncode for process in processes] == [0, 0], results
    state = json.loads((destination / offline_install.STATE_FILE).read_text())
    assert state["files"]
    for entry in state["files"]:
        path = destination / entry["path"]
        assert path.is_file()
        assert path.stat().st_size == entry["size"]
        assert package.sha256_file(path) == entry["sha256"]


@pytest.mark.skipif(os.name != "posix", reason="requires fcntl")
def test_install_lock_is_released(wheelhouse, tmp_path):
    import fcntl

    destination = tmp_path / "deps"
    assert offline_install.install(wheelhouse, destination, "cp38") is True
    lock_file = destination.parent / (destination.name + ".lock")
    with lock_file.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    assert offline_install.install(wheelhouse, destination, "cp38") is False


def test_install_never_calls_installers():
    assert not hasattr(offline_install, "subprocess")
    assert "subprocess" not in (ROOT / "scripts" / "offline_install.py").read_text()


def test_rejects_wheel_member_escape(tmp_path, monkeypatch):
    def download(requirements, destination, **kwargs):
        destination.mkdir(parents=True, exist_ok=True)
        project, version = requirements[0].split("==")
        path = destination / f"{project.replace('-', '_')}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as handle:
            handle.writestr("../../escape.txt", "escape")
        return [path]

    monkeypatch.setattr(package, "pip_download", download)
    monkeypatch.setattr(offline_install, "_platform_id", lambda: "linux-arm64")
    wheelhouse = tmp_path / "wheelhouse"
    package.build_wheelhouse(wheelhouse, "linux-arm64")
    with pytest.raises(ValueError, match="unsafe wheel member"):
        offline_install.install(wheelhouse, tmp_path / "deps", "cp38")
    assert not (tmp_path / "deps").exists()
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.parametrize("problem", ("checksum", "missing", "path", "duplicate", "unsupported"))
def test_rejects_invalid_bundle(wheelhouse, tmp_path, problem):
    path = wheelhouse / "manifest.json"
    manifest = json.loads(path.read_text())
    if problem == "checksum":
        manifest["wheels"][0]["sha256"] = "0" * 64
    elif problem == "missing":
        manifest["wheels"][0]["wheel"] = "missing.whl"
    elif problem == "path":
        manifest["wheels"][0]["wheel"] = "../escape.whl"
    elif problem == "duplicate":
        manifest["wheels"].append(manifest["wheels"][0])
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        offline_install.install(
            wheelhouse, tmp_path / "deps", "cp315" if problem == "unsupported" else "cp38"
        )
    assert not (tmp_path / "escape.whl").exists()
    assert not (tmp_path / "deps").exists()


@pytest.mark.parametrize("problem", ("missing", "ambiguous"))
def test_build_rejects_unresolved_environment(wheelhouse, problem):
    records = json.loads((wheelhouse / "manifest.json").read_text())["wheels"]
    if problem == "missing":
        records = [record for record in records if record["python_tag"] != "cp38"]
    else:
        records.append(next(record for record in records if record["python_tag"] == "cp38"))
    with pytest.raises(ValueError, match="offline dependenc"):
        package.resolve_environments(records, ("3.8",))


@pytest.mark.parametrize("kind", ("symlink", "duplicate"))
def test_rejects_unsafe_tar_members(wheelhouse, tmp_path, kind):
    path = wheelhouse / "wheels.tar.xz"
    with tarfile.open(path, "r:xz") as bundle:
        contents = [(member, bundle.extractfile(member).read()) for member in bundle]
    with tarfile.open(path, "w:xz") as bundle:
        for index, (member, payload) in enumerate(contents):
            if index == 0 and kind == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = "/tmp/escape.whl"
                member.size = 0
                bundle.addfile(member)
            else:
                bundle.addfile(member, io.BytesIO(payload))
                if index == 0 and kind == "duplicate":
                    bundle.addfile(member, io.BytesIO(payload))
    with pytest.raises(ValueError, match="invalid or duplicate bundle member"):
        offline_install.install(wheelhouse, tmp_path / "deps", "cp38")


def test_corrupt_bundle_is_rejected(wheelhouse, tmp_path):
    (wheelhouse / "wheels.tar.xz").write_bytes(b"not an xz archive")
    with pytest.raises(tarfile.TarError):
        offline_install.install(wheelhouse, tmp_path / "deps", "cp38")


def test_macos_bundle_requires_native_cp38_wheel(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "pip_download", lambda *args, **kwargs: [])
    with pytest.raises(ValueError, match="requires --macos-cp38-wheel"):
        package.build_wheelhouse(tmp_path, "macos-arm64")
    with pytest.raises(ValueError, match="expected a native macOS ARM64"):
        package.build_wheelhouse(
            tmp_path, "macos-arm64", macos_cp38_wheel=tmp_path / "lxml-x86_64.whl",
        )


def test_oversize_package_fails_and_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(package, "stage_source", lambda destination: None)
    monkeypatch.setattr(package, "build_wheelhouse", lambda *args, **kwargs: None)
    monkeypatch.setattr(package, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="offline package must be below"):
        package.build("macos-arm64", tmp_path)
    assert not list(tmp_path.glob("*.zip"))


def test_installed_dependencies_are_importable(wheelhouse, tmp_path):
    destination = tmp_path / "deps"
    offline_install.install(wheelhouse, destination, "cp38")
    environment = dict(os.environ, PYTHONPATH=str(destination))
    subprocess.run(
        [sys.executable, "-B", "-c", "import docx, lxml, markdown_it"],
        env=environment,
        check=True,
    )


def test_manifest_change_invalidates_cache(wheelhouse, tmp_path):
    destination = tmp_path / "deps"
    assert offline_install.install(wheelhouse, destination, "cp38") is True
    sentinel = destination / "sentinel"
    sentinel.write_text("cached")
    path = wheelhouse / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["environments"]["cp38"].append(
        "common/markdown_it_py-4.2.0-py3-none-any.whl"
    )
    path.write_text(json.dumps(manifest))
    assert offline_install.install(wheelhouse, destination, "cp38") is True
    assert not sentinel.exists()


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")
def test_setup_offline_does_not_create_venv(wheelhouse, tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    (root / "wheelhouse").symlink_to(wheelhouse, target_is_directory=True)
    (root / "OFFLINE_PACKAGE").write_text("version=0\n")
    environment = dict(os.environ, WXDF_PYTHON=sys.executable)
    completed = subprocess.run(
        ["sh", str(root / "scripts" / "setup.sh"), "--offline"],
        env=environment, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert (root / ".deps" / offline_install.STATE_FILE).is_file()
    assert not (root / ".venv").exists()


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")
def test_setup_offline_without_bundle_does_not_fall_back_to_pip(tmp_path):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    (root / "wheelhouse").mkdir()
    (root / "wheelhouse" / "manifest.json").write_text("{}\n")
    (root / "OFFLINE_PACKAGE").write_text("version=0\n")
    environment = dict(os.environ, WXDF_PYTHON=sys.executable)
    completed = subprocess.run(
        ["sh", str(root / "scripts" / "setup.sh"), "--offline"],
        env=environment, capture_output=True, text=True,
    )
    assert completed.returncode == 3
    assert "WXDF-E-VENDOR-UNAVAILABLE" in completed.stderr
    assert not (root / ".venv").exists()
