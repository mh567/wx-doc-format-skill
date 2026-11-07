"""Build the encrypted interpreted payload for the wx-doc-format skill."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import boot

PROJECT_ROOT = HERE.parents[1]


def collect_modules(source_root: Path) -> list[tuple[str, bool, Path]]:
    if source_root.name != "scripts":
        raise ValueError("source root must be named 'scripts' to keep virtual __file__ paths accurate")
    modules: list[tuple[str, bool, Path]] = []
    for path in sorted(source_root.glob("*.py")):
        modules.append((path.stem, False, path))
    package_root = source_root / "wxdf"
    if not package_root.is_dir():
        raise FileNotFoundError(f"package directory is missing: {package_root}")
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(source_root)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            modules.append((".".join(parts[:-1]), True, path))
        else:
            modules.append((".".join(parts), False, path))
    return modules


def _encrypt(plain: bytes, master: bytes, nonce: bytes) -> bytes:
    return bytes(left ^ right for left, right in zip(plain, boot.stream(master, nonce, len(plain))))


def build_payload(source_root: Path) -> tuple[bytes, dict, list[dict]]:
    modules = collect_modules(source_root)
    names = [name for name, _, _ in modules]
    if "main" not in names:
        raise ValueError("source root does not contain main.py")
    slots = [boot.slot(name) for name in names]
    if len(set(slots)) != len(slots):
        raise ValueError("payload slot collision between module names")

    master = boot.key()
    data_start = len(boot.MAGIC) + 6 + len(modules) * boot.ENTRY.size
    entries: list[tuple[bytes, int, bytes, int, int, bytes]] = []
    body = bytearray()
    index: list[dict] = []
    for (name, is_package, path), slot in zip(modules, slots):
        source = path.read_bytes()
        plain = zlib.compress(source, 9)
        nonce = os.urandom(12)
        cipher = _encrypt(plain, master, nonce)
        entries.append(
            (
                slot,
                1 if is_package else 0,
                nonce,
                data_start + len(body),
                len(cipher),
                hashlib.sha256(plain).digest(),
            )
        )
        body += cipher
        index.append(
            {
                "name": name,
                "package": is_package,
                "source_sha256": hashlib.sha256(source).hexdigest(),
                "source_size": len(source),
                "payload_offset": data_start + len(body) - len(cipher),
                "payload_size": len(cipher),
                "payload_sha256": hashlib.sha256(plain).hexdigest(),
            }
        )

    header = (
        boot.MAGIC
        + struct.pack("<IH", boot.FORMAT_VERSION, len(modules))
        + b"".join(boot.ENTRY.pack(*entry) for entry in entries)
    )
    blob = header + bytes(body)

    module_lines = "".join(
        f"{item['name']}\t{item['payload_sha256']}\n" for item in sorted(index, key=lambda item: item["name"])
    )
    summary = {
        "schema_version": 1,
        "format_version": boot.FORMAT_VERSION,
        "payload": "payload.bin",
        "payload_sha256": hashlib.sha256(blob).hexdigest(),
        "payload_size": len(blob),
        "module_count": len(modules),
        "module_digest": hashlib.sha256(module_lines.encode("utf-8")).hexdigest(),
        "entry_module": "main",
    }
    return blob, summary, index


def write_payload(output_dir: Path, blob: bytes, summary: dict, index: list[dict] | None, index_path: Path | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "payload.bin").write_bytes(blob)
    (output_dir / "payload.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if index_path is not None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the encrypted wx-doc-format payload.")
    parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT / "scripts")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=None, help="Private module index for build evidence.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    blob, summary, index = build_payload(args.source_root.resolve())
    write_payload(args.output_dir.resolve(), blob, summary, index, args.index)
    print(f"payload modules: {summary['module_count']}")
    print(f"payload bytes: {summary['payload_size']}")
    print(f"payload sha256: {summary['payload_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
