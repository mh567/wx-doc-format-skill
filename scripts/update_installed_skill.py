#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen
import zipfile


DEFAULT_REPO = "mh567/wx-doc-format-skill"
DEFAULT_REF = "main"
PRESERVE_NAMES = {".git", ".venv", "__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def read_version(path: Path) -> str:
    version_path = path / "VERSION"
    if version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return "unknown"


def should_skip(path: Path) -> bool:
    return path.name in PRESERVE_NAMES or path.suffix in SKIP_SUFFIXES


def copy_tree_contents(src: Path, dest: Path) -> None:
    for item in src.iterdir():
        if should_skip(item):
            continue
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def remove_old_files(dest: Path, src: Path) -> None:
    source_names = {item.name for item in src.iterdir() if not should_skip(item)}
    for item in dest.iterdir():
        if should_skip(item) or item.name not in source_names:
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()


def backup_current(dest: Path) -> Path:
    backup_root = dest.parent / ".skill-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = backup_root / f"{dest.name}-{stamp}"
    shutil.copytree(
        dest,
        backup_dir,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc", "*.pyo"),
    )
    return backup_dir


def download_repo(repo: str, ref: str, tmp: Path) -> Path:
    url = f"https://codeload.github.com/{repo}/zip/refs/heads/{ref}"
    if ref.startswith("v") or "." in ref:
        url = f"https://codeload.github.com/{repo}/zip/refs/tags/{ref}"
    archive_path = tmp / "repo.zip"
    with urlopen(url, timeout=60) as response:
        archive_path.write_bytes(response.read())
    extract_dir = tmp / "repo"
    with zipfile.ZipFile(archive_path) as zf:
        zf.extractall(extract_dir)
    roots = [path for path in extract_dir.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"Unexpected archive layout for {repo}@{ref}")
    return roots[0]


def validate_source(src: Path) -> None:
    required = [src / "SKILL.md", src / "scripts" / "format_document.py"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("Update source is missing required files: " + ", ".join(missing))


def main() -> None:
    parser = argparse.ArgumentParser(description="Update an installed wx-doc-format skill from GitHub or a local source tree.")
    parser.add_argument("--dest", type=Path, default=Path(__file__).resolve().parents[1], help="Installed skill directory to update.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repo in owner/name form.")
    parser.add_argument("--ref", default=DEFAULT_REF, help="Branch or tag to install from.")
    parser.add_argument("--source-dir", type=Path, help="Use a local source directory instead of downloading from GitHub.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create a backup before replacing files.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and report the planned update without copying files.")
    args = parser.parse_args()

    dest = args.dest.expanduser().resolve()
    if not dest.exists():
        raise SystemExit(f"Destination does not exist: {dest}")

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        source = args.source_dir.expanduser().resolve() if args.source_dir else download_repo(args.repo, args.ref, tmp)
        validate_source(source)
        current_version = read_version(dest)
        source_version = read_version(source)
        if args.dry_run:
            print(f"Would update {dest} from {source}")
            print(f"Current version: {current_version}")
            print(f"Source version: {source_version}")
            return
        backup_dir = None if args.no_backup else backup_current(dest)
        remove_old_files(dest, source)
        copy_tree_contents(source, dest)
        print(f"Updated {dest} from {args.repo}@{args.ref}" if not args.source_dir else f"Updated {dest} from {source}")
        print(f"Version: {current_version} -> {source_version}")
        if backup_dir is not None:
            print(f"Backup: {backup_dir}")
        print("Restart Codex to pick up the updated skill.")


if __name__ == "__main__":
    main()
