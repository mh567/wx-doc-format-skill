"""Read-only source and resource access for modular parser adapters."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from typing import Any


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
                raw = path.read_bytes()
            except OSError as exc:
                raise SourceStoreError(f"cannot read source {path}") from exc
            source_type = "docx" if path.suffix.lower() == ".docx" else "markdown" if path.suffix.lower() in {".md", ".markdown"} else "text"

        digest = hashlib.sha256(raw).hexdigest()
        prefixed_digest = f"sha256:{digest}"
        source_id = f"{source_type}:{digest}"
        self._parts[str(path)] = text if text is not None else raw
        self._parts[path.name] = text if text is not None else raw
        if source_type == "docx":
            try:
                with ZipFile(io.BytesIO(raw)) as archive:
                    for name in archive.namelist():
                        self._parts[name] = archive.read(name)
            except BadZipFile as exc:
                raise SourceStoreError(f"source is not a valid DOCX package: {path}") from exc
        elif text is None:
            try:
                self._parts[str(path)] = raw.decode("utf-8")
                self._parts[path.name] = self._parts[str(path)]
            except UnicodeDecodeError:
                pass

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

    def close(self) -> None:
        self._handle = None
        self._opened_path = None
        self._parts.clear()
