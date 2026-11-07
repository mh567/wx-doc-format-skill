"""WXDF interpreted runtime loader.

Runs the encrypted payload shipped next to this file.  The key below is
obfuscation material: anyone who can execute the package can recover it, so
this defends the payload at rest, not at runtime.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.util
import struct
import sys
import zlib
from pathlib import Path

MAGIC = b"WXDFPAY1"
FORMAT_VERSION = 1
ENTRY = struct.Struct("<8sB12sII32s")

_ROOT = Path(__file__).resolve().parent.parent
_PAYLOAD = Path(__file__).resolve().with_name("payload.bin")

_KEY_HEX = (
    "c7c37e2d0ae1d8b8",
    "3b4aebb6bb5f734d",
    "d44e4422b149d32e",
    "91447fee8c7e290a",
)
_KEY_PAD = b"wx-doc-format-payload-v1"


class PayloadError(RuntimeError):
    """Raised when the shipped payload is missing or inconsistent."""


def key() -> bytes:
    raw = bytes.fromhex("".join(_KEY_HEX))
    pad = hashlib.sha256(_KEY_PAD).digest()
    return bytes(left ^ right for left, right in zip(raw, pad))


def stream(key_bytes: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key_bytes + nonce + struct.pack("<I", counter)).digest()
        counter += 1
    return bytes(out[:length])


def slot(module_name: str) -> bytes:
    return hashlib.sha256(module_name.encode("utf-8")).digest()[:8]


def _read_index() -> tuple[bytes, dict[bytes, tuple[int, bytes, int, int, bytes]]]:
    try:
        blob = _PAYLOAD.read_bytes()
    except OSError as exc:
        raise PayloadError(f"payload.bin is not readable: {exc}") from exc
    if not blob.startswith(MAGIC):
        raise PayloadError("payload.bin has an unsupported header")
    try:
        version, count = struct.unpack_from("<IH", blob, len(MAGIC))
    except struct.error as exc:
        raise PayloadError("payload.bin is truncated") from exc
    if version != FORMAT_VERSION:
        raise PayloadError(f"payload.bin format {version} is not supported")
    offset = len(MAGIC) + 6
    entries = {}
    for _ in range(count):
        if offset + ENTRY.size > len(blob):
            raise PayloadError("payload.bin is truncated")
        digest_slot, flags, nonce, start, length, digest = ENTRY.unpack_from(blob, offset)
        offset += ENTRY.size
        if start + length > len(blob):
            raise PayloadError("payload.bin has an out-of-range module")
        entries[digest_slot] = (flags, nonce, start, length, digest)
    return blob, entries


_state: tuple[bytes, dict[bytes, tuple[int, bytes, int, int, bytes]]] | None = None


def _index() -> tuple[bytes, dict[bytes, tuple[int, bytes, int, int, bytes]]]:
    global _state
    if _state is None:
        _state = _read_index()
    return _state


def _entry(module_name: str) -> tuple[int, bytes, int, int, bytes]:
    entry = _index()[1].get(slot(module_name))
    if entry is None:
        raise PayloadError(f"module {module_name} is not present in payload.bin")
    return entry


def _source(module_name: str) -> tuple[bytes, bool]:
    blob, _ = _index()
    flags, nonce, start, length, digest = _entry(module_name)
    cipher = blob[start:start + length]
    plain = bytes(left ^ right for left, right in zip(cipher, stream(key(), nonce, length)))
    if hashlib.sha256(plain).digest() != digest:
        raise PayloadError(f"module {module_name} failed its integrity check")
    try:
        return zlib.decompress(plain), bool(flags & 1)
    except zlib.error as exc:
        raise PayloadError(f"module {module_name} could not be decompressed") from exc


def _virtual_path(module_name: str, is_package: bool) -> Path:
    parts = module_name.split(".")
    base = _ROOT / "scripts"
    directory = base.joinpath(*parts[:-1])
    if is_package:
        return directory / parts[-1] / "__init__.py"
    return directory / (parts[-1] + ".py")


class _Loader(importlib.abc.Loader):
    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        name = module.__spec__.name
        source, is_package = _source(name)
        module.__file__ = str(_virtual_path(name, is_package))
        module.__package__ = name if is_package else name.rpartition(".")[0]
        code = compile(source, f"<wxdf:{name}>", "exec")
        exec(code, module.__dict__)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        entry = _index()[1].get(slot(fullname))
        if entry is None:
            return None
        return importlib.util.spec_from_loader(fullname, _Loader(), is_package=bool(entry[0] & 1))


def install() -> None:
    sys.dont_write_bytecode = True
    tag = f"cp{sys.version_info[0]}{sys.version_info[1]}"
    for name in ("common", tag):
        path = _ROOT / "vendor" / name
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    if not any(isinstance(finder, _Finder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Finder())


def main(argv: list[str] | None = None) -> None:
    try:
        install()
        import main as entry
        entry.main(sys.argv[1:] if argv is None else argv)
    except PayloadError as exc:
        print(f"WXDF-E-PAYLOAD: {exc}", file=sys.stderr)
        raise SystemExit(4) from exc


if __name__ == "__main__":
    main()
