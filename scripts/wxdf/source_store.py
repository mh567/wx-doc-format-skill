"""Read-only source and resource access for modular parser adapters."""

from __future__ import annotations

import hashlib
import io
import struct
import zlib
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile
from typing import Any


MAX_PART_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_ENTRIES = 10_000
_READ_CHUNK_BYTES = 1024 * 1024
_EOCD_SEARCH_BYTES = 65557
_ZIP64_EOCD_MIN_BYTES = 56


def _find_eocd(raw: bytes) -> int | None:
    eocd = raw.rfind(b"PK\x05\x06", max(0, len(raw) - _EOCD_SEARCH_BYTES))
    if eocd < 0 or eocd + 22 > len(raw):
        return None
    return eocd


def _zip64_eocd(raw: bytes, eocd: int) -> tuple[int, int, int] | None:
    locator = eocd - 20
    if locator < 0 or raw[locator : locator + 4] != b"PK\x06\x07":
        return None
    locator_offset = struct.unpack_from("<Q", raw, locator + 8)[0]
    records: list[tuple[int, tuple[int, int, int]]] = []
    seen_offsets: set[int] = set()
    for zip64_offset in (locator_offset, eocd - 76):
        if zip64_offset in seen_offsets:
            continue
        seen_offsets.add(zip64_offset)
        if zip64_offset < 0 or zip64_offset + _ZIP64_EOCD_MIN_BYTES > len(raw):
            continue
        if raw[zip64_offset : zip64_offset + 4] != b"PK\x06\x06":
            continue
        fields = struct.unpack_from("<QQQ", raw, zip64_offset + 32)
        records.append((zip64_offset, fields))
    if not records:
        return None
    if len(records) > 1 and records[0][1] != records[1][1]:
        raise SourceStoreError("DOCX has conflicting ZIP64 end of central directory records")
    zip64_offset, (entries, size_cd, _offset_cd) = records[0]
    return entries, size_cd, zip64_offset


def _declared_entry_count(raw: bytes) -> int | None:
    eocd = _find_eocd(raw)
    if eocd is None:
        return None
    zip64 = _zip64_eocd(raw, eocd)
    if zip64 is not None:
        return zip64[0]
    entries = struct.unpack_from("<H", raw, eocd + 10)[0]
    return entries


def _scan_central_directory(raw: bytes) -> int | None:
    eocd = _find_eocd(raw)
    if eocd is None:
        return None
    zip64 = _zip64_eocd(raw, eocd)
    if zip64 is not None:
        size_cd = zip64[1]
        end = zip64[2]
    else:
        size_cd = struct.unpack_from("<I", raw, eocd + 12)[0]
        end = eocd

    start = end - size_cd
    if start < 0:
        raise SourceStoreError("DOCX central directory is out of bounds")

    position = start
    count = 0
    while position < end:
        if position + 46 > end:
            raise SourceStoreError("DOCX central directory record is truncated")
        if raw[position : position + 4] != b"PK\x01\x02":
            raise SourceStoreError("DOCX central directory has an invalid record signature")
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", raw, position + 28)
        position += 46 + name_length + extra_length + comment_length
        if position > end:
            raise SourceStoreError("DOCX central directory record is truncated")
        count += 1
        if count > MAX_ENTRIES:
            raise SourceStoreError(
                f"DOCX has more than {MAX_ENTRIES} entries, exceeding entry count limit {MAX_ENTRIES}"
            )
    return count


def _read_source_bytes(path: Path) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with path.open("rb") as fp:
            while True:
                chunk = fp.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_TOTAL_BYTES:
                    raise SourceStoreError(f"source {path} exceeds total size limit {MAX_TOTAL_BYTES} bytes")
                chunks.append(chunk)
    except SourceStoreError:
        raise
    except (OSError, ValueError) as exc:
        raise SourceStoreError(f"cannot read source {path}") from exc
    return b"".join(chunks)


class SourceStoreError(RuntimeError):
    """Raised when a source reference cannot be resolved safely."""


@dataclass(frozen=True)
class SourceHandle:
    source_id: str
    source_type: str
    path: Path
    content_sha256: str
    encoding: str | None = None


class SourceStore:
    def __init__(self) -> None:
        self._handle: SourceHandle | None = None
        self._parts: dict[str, str | bytes] = {}
        self._opened_path: Path | None = None
        self.open_count = 0

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    @property
    def handle(self) -> SourceHandle:
        if self._handle is None:
            raise SourceStoreError("source is not open")
        return self._handle

    def open_source(self, source: Path | str, *, text: str | None = None) -> SourceHandle:
        path = Path(source)
        if self._handle is not None:
            if path == self._opened_path:
                return self._handle
            raise SourceStoreError("one SourceStore instance can open only one source")

        if text is not None:
            raw = text.encode("utf-8")
            source_type = "markdown" if path.suffix.lower() in {".md", ".markdown"} else "text"
        else:
            try:
                source_size = path.stat().st_size
            except (OSError, ValueError) as exc:
                raise SourceStoreError(f"cannot read source {path}") from exc
            if source_size > MAX_TOTAL_BYTES:
                raise SourceStoreError(f"source {path} exceeds total size limit {MAX_TOTAL_BYTES} bytes")
            raw = _read_source_bytes(path)
            source_type = "docx" if path.suffix.lower() == ".docx" else "markdown" if path.suffix.lower() in {".md", ".markdown"} else "text"

        digest = hashlib.sha256(raw).hexdigest()
        prefixed_digest = f"sha256:{digest}"
        source_id = f"{source_type}:{digest}"
        parts: dict[str, str | bytes] = {
            str(path): text if text is not None else raw,
            path.name: text if text is not None else raw,
        }
        if source_type == "docx":
            try:
                declared_entries = _declared_entry_count(raw)
                if declared_entries is not None and declared_entries > MAX_ENTRIES:
                    raise SourceStoreError(
                        f"DOCX has {declared_entries} entries, exceeding entry count limit {MAX_ENTRIES}"
                    )
                _scan_central_directory(raw)
                with ZipFile(io.BytesIO(raw)) as archive:
                    infos = archive.infolist()
                    # The EOCD count is a fast precheck; this fallback counts parsed central directory records.
                    if len(infos) > MAX_ENTRIES:
                        raise SourceStoreError(
                            f"DOCX has {len(infos)} entries, exceeding entry count limit {MAX_ENTRIES}"
                        )
                    declared_total = 0
                    for info in infos:
                        if info.flag_bits & 0x1:
                            raise SourceStoreError(f"DOCX part {info.filename!r} is encrypted")
                        if info.flag_bits & 0x60:
                            raise SourceStoreError(
                                f"DOCX part {info.filename!r} uses unsupported flags {info.flag_bits:#x}"
                            )
                        if info.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                            raise SourceStoreError(
                                f"DOCX part {info.filename!r} uses unsupported compression method {info.compress_type}"
                            )
                        if info.file_size > MAX_PART_BYTES:
                            raise SourceStoreError(
                                f"DOCX part {info.filename!r} exceeds part size limit {MAX_PART_BYTES} bytes"
                            )
                        declared_total += info.file_size
                        if declared_total > MAX_TOTAL_BYTES:
                            raise SourceStoreError(
                                f"DOCX part {info.filename!r} exceeds total size limit {MAX_TOTAL_BYTES} bytes"
                            )

                    actual_total = 0
                    for info in infos:
                        probe = copy(info)
                        # Read past a forged file_size so actual bytes are measured, then verify CRC below.
                        probe.file_size = max(probe.file_size, MAX_PART_BYTES + 1)
                        probe.CRC = None
                        chunks: list[bytes] = []
                        part_bytes = 0
                        running_crc = 0
                        with archive.open(probe) as fp:
                            while True:
                                chunk = fp.read(_READ_CHUNK_BYTES)
                                if not chunk:
                                    break
                                part_bytes += len(chunk)
                                actual_total += len(chunk)
                                if part_bytes > MAX_PART_BYTES:
                                    raise SourceStoreError(
                                        f"DOCX part {info.filename!r} exceeds part size limit {MAX_PART_BYTES} bytes"
                                    )
                                if actual_total > MAX_TOTAL_BYTES:
                                    raise SourceStoreError(
                                        f"DOCX part {info.filename!r} exceeds total size limit {MAX_TOTAL_BYTES} bytes"
                                    )
                                chunks.append(chunk)
                                running_crc = zlib.crc32(chunk, running_crc)
                        if running_crc != info.CRC:
                            raise BadZipFile(f"Bad CRC-32 for file {info.filename!r}")
                        parts[info.filename] = b"".join(chunks)
            except SourceStoreError:
                raise
            except (BadZipFile, zlib.error, OSError, EOFError, RuntimeError, ValueError) as exc:
                raise SourceStoreError(f"cannot read DOCX package {path}: {exc}") from exc
        elif text is None:
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                parts[str(path)] = decoded
                parts[path.name] = decoded

        self._parts = parts
        self._handle = SourceHandle(
            source_id=source_id,
            source_type=source_type,
            path=path,
            content_sha256=prefixed_digest,
            encoding="utf-8" if source_type != "docx" else None,
        )
        self._opened_path = path
        self.open_count += 1
        return self._handle

    def resolve(self, source_ref: dict[str, Any]) -> str | bytes:
        handle = self.handle
        source_id = source_ref.get("source_id")
        if source_id != handle.source_id:
            raise SourceStoreError("source_ref belongs to a different source snapshot")
        part = source_ref.get("part")
        if not isinstance(part, str) or not part:
            raise SourceStoreError("source_ref.part is required")
        try:
            return self._parts[part]
        except KeyError as exc:
            raise SourceStoreError(f"source part is unavailable: {part}") from exc

    def bind_part(self, part: str, payload: bytes) -> None:
        """Retain one verified resource snapshot without permitting replacement."""

        if not self.is_open:
            raise SourceStoreError("source is not open")
        if not isinstance(part, str) or not part:
            raise SourceStoreError("source part is required")
        value = bytes(payload)
        existing = self._parts.get(part)
        if existing is not None and existing != value:
            raise SourceStoreError(f"source part is already bound: {part}")
        self._parts[part] = value

    def close(self) -> None:
        self._handle = None
        self._opened_path = None
        self._parts.clear()
