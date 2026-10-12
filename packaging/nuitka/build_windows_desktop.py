"""Build a private Windows desktop artifact from a verified standalone runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import runpy
import shutil
import struct
import subprocess
import sys
import zipfile


def run(*command: str, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def assert_x64(path: Path) -> None:
    with path.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError(f'Not a PE executable: {path}')
        stream.seek(0x3c)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b'PE\0\0' or struct.unpack('<H', stream.read(2))[0] != 0x8664:
            raise ValueError(f'Not AMD64: {path}')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--desktop', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Windows' or struct.calcsize('P') != 8:
        raise RuntimeError('Windows x86_64 Python is required')
    engine, desktop, output = (p.resolve() for p in (args.engine, args.desktop, args.output))
    output.mkdir(parents=True, exist_ok=True)
    version = (engine / 'VERSION').read_text().strip()
    sys.path.insert(0, str(engine / 'packaging' / 'nuitka'))
    import build_distribution as build
    dist = engine / 'build' / 'nuitka' / 'windows-desktop' / 'release_entrypoint.dist'
    module_manifest = build._verified_runtime_module_manifest(dist)
    build.reject_python_sources(dist)
    assert_x64(dist / 'wx-doc-format.exe')
    package = output / f'wx-doc-format-skill-{version}-windows-x86_64'
    package.mkdir()
    shutil.copytree(dist, package / 'runtime', ignore=shutil.ignore_patterns(build.RUNTIME_COMPILATION_REPORT_NAME))
    (package / 'assets').mkdir()
    shutil.copy2(engine / 'assets' / 'wx_template.docx', package / 'assets' / 'wx_template.docx')
    shutil.copy2(engine / 'VERSION', package / 'VERSION')
    build._write_integrity_files(package, 'windows-x86_64', version, module_manifest)
    archive = build.create_archive(package, 'windows-x86_64')
    checksums = output / 'native-SHA256SUMS.txt'
    checksums.write_text(f'{build.sha256_file(archive)}  {archive.name}\n')
    for name in ('VERSION', 'UPSTREAM_VERSION'):
        (desktop / name).write_text(version + '\n')
    (desktop / 'src' / 'wxdoc_desktop' / '_version.py').write_text(f'__version__ = "{version}"\n')
    run(sys.executable, '-m', 'pip', 'install', '-e', f'{desktop}[test,build]')
    run(sys.executable, '-m', 'pytest', str(desktop / 'tests'), cwd=desktop)
    sys.path.insert(0, str(desktop / 'packaging'))
    import prepare_native_skill
    # Private desktop payload only; public Skill platform declarations stay unchanged.
    prepare_native_skill.current_platform = lambda: 'windows-x86_64'
    os.environ['MAGIC_FORMAT_SKILL_ARCHIVE'] = str(archive)
    os.environ['MAGIC_FORMAT_SKILL_CHECKSUMS'] = str(checksums)
    os.chdir(desktop)
    runpy.run_path(str(desktop / 'packaging' / 'build.py'), run_name='__main__')
    run(sys.executable, str(desktop / 'tools' / 'smoke_package.py'), cwd=desktop)
    app = desktop / 'dist' / 'MagicFormat'
    assert_x64(app / 'MagicFormat.exe')
    assert_x64(app / 'native_skill' / 'runtime' / 'wx-doc-format.exe')
    final = output / f'wx-doc-format-{version}-windows-x86_64.zip'
    with zipfile.ZipFile(final, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                bundle.write(path, Path('MagicFormat') / path.relative_to(app))
    fresh = output / 'extracted'
    with zipfile.ZipFile(final) as bundle:
        bundle.extractall(fresh / 'dist')
    (fresh / 'tools').mkdir()
    shutil.copy2(desktop / 'tools' / 'smoke_package.py', fresh / 'tools' / 'smoke_package.py')
    shutil.copy2(desktop / 'UPSTREAM_VERSION', fresh / 'UPSTREAM_VERSION')
    run(sys.executable, str(fresh / 'tools' / 'smoke_package.py'))
    record = {'version': version, 'engine_source': module_manifest['source_identity'],
              'desktop_source': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=desktop, text=True).strip(),
              'platform': 'windows-x86_64', 'package_smoke': 'passed', 'extracted_package_smoke': 'passed',
              'sha256': build.sha256_file(final), 'archive': final.name}
    (output / 'acceptance.json').write_text(json.dumps(record, indent=2) + '\n')
    (output / 'SHA256SUMS.txt').write_text(f'{record["sha256"]}  {final.name}\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
