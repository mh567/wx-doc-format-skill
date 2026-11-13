from __future__ import annotations

import struct
import sys
import tracemalloc
import zlib
from pathlib import Path
from zipfile import ZIP_BZIP2, ZIP_DEFLATED, ZIP_LZMA, BadZipFile, ZipFile, ZipInfo

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf import source_store
from wxdf.source_store import SourceStore, SourceStoreError


def _write_zip(path: Path, entries: dict[str, bytes], compression: int = ZIP_DEFLATED) -> Path:
    with ZipFile(path, "w", compression) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return path


def _document_xml() -> bytes:
    return b"<w:document>" + b"x" * 64 + b"</w:document>"


def _zero_crc(payload_bytes: int) -> int:
    crc = 0
    chunk = b"\0" * (1024 * 1024)
    remaining = payload_bytes
    while remaining:
        piece = chunk[: min(len(chunk), remaining)]
        crc = zlib.crc32(piece, crc)
        remaining -= len(piece)
    return crc


def _corrupt_deflated_payload(path: Path) -> None:
    raw = bytearray(path.read_bytes())
    header = raw.find(b"PK\x03\x04")
    assert header >= 0
    name_length, extra_length = struct.unpack_from("<HH", raw, header + 26)
    compressed_size = struct.unpack_from("<I", raw, header + 18)[0]
    method = struct.unpack_from("<H", raw, header + 8)[0]
    assert method == 8 and compressed_size > 0
    payload_offset = header + 30 + name_length + extra_length
    raw[payload_offset] ^= 0xFF
    path.write_bytes(bytes(raw))


def _write_forged_size_zip(path: Path, payloads: dict[str, int]) -> Path:
    chunk = b"\0" * (1024 * 1024)
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for name, payload_bytes in payloads.items():
            info = ZipInfo(name)
            info.compress_type = ZIP_DEFLATED
            with archive.open(info, "w") as writer:
                remaining = payload_bytes
                while remaining:
                    piece = chunk[: min(len(chunk), remaining)]
                    writer.write(piece)
                    remaining -= len(piece)

    raw = bytearray(path.read_bytes())
    central = raw.find(b"PK\x01\x02")
    assert central >= 0
    for _, payload_bytes in payloads.items():
        declared_crc = _zero_crc(payload_bytes)
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", raw, central + 28)
        local = struct.unpack_from("<I", raw, central + 42)[0]
        assert local >= 0
        struct.pack_into("<I", raw, local + 14, declared_crc)
        struct.pack_into("<I", raw, local + 22, 1)
        struct.pack_into("<I", raw, central + 16, declared_crc)
        struct.pack_into("<I", raw, central + 24, 1)
        central += 46 + name_length + extra_length + comment_length
    path.write_bytes(bytes(raw))
    return path


def _write_forged_one_byte_docx(path: Path, payload_bytes: int = 128 * 1024 * 1024) -> Path:
    return _write_forged_size_zip(path, {"word/document.xml": payload_bytes})


def _set_zip_flag(path: Path, flag: int) -> None:
    raw = bytearray(path.read_bytes())
    local = raw.find(b"PK\x03\x04")
    central = raw.find(b"PK\x01\x02")
    assert local >= 0 and central > local
    local_flags = struct.unpack_from("<H", raw, local + 6)[0]
    central_flags = struct.unpack_from("<H", raw, central + 8)[0]
    struct.pack_into("<H", raw, local + 6, local_flags | flag)
    struct.pack_into("<H", raw, central + 8, central_flags | flag)
    path.write_bytes(bytes(raw))


def _set_central_extract_version(path: Path, version: int) -> None:
    raw = bytearray(path.read_bytes())
    central = raw.find(b"PK\x01\x02")
    assert central >= 0
    struct.pack_into("<H", raw, central + 6, version)
    path.write_bytes(bytes(raw))


def _set_eocd_entry_count(path: Path, count: int) -> None:
    raw = bytearray(path.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<H", raw, eocd + 10, count)
    path.write_bytes(bytes(raw))


def _set_eocd_central_size(path: Path, size: int) -> None:
    raw = bytearray(path.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    assert eocd >= 0
    struct.pack_into("<I", raw, eocd + 12, size)
    path.write_bytes(bytes(raw))


def _truncate_first_central_record(path: Path) -> None:
    raw = bytearray(path.read_bytes())
    central = raw.find(b"PK\x01\x02")
    assert central >= 0
    struct.pack_into("<H", raw, central + 28, 0xFFFF)
    path.write_bytes(bytes(raw))


def _break_first_central_signature(path: Path) -> None:
    raw = bytearray(path.read_bytes())
    central = raw.find(b"PK\x01\x02")
    assert central >= 0
    raw[central : central + 4] = b"XXXX"
    path.write_bytes(bytes(raw))


def _insert_zip64_eocd(
    path: Path,
    *,
    nonstandard: bool,
    locator_delta: int = 0,
    entry_count: int | None = None,
    central_offset_delta: int = 0,
    tamper_last_record: bool = True,
) -> Path:
    raw = bytearray(path.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    assert eocd >= 0
    size_cd = struct.unpack_from("<I", raw, eocd + 12)[0]
    central_start = eocd - size_cd
    position = central_start
    last_start = central_start
    count = 0
    while position < eocd:
        assert raw[position : position + 4] == b"PK\x01\x02"
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", raw, position + 28)
        last_start = position
        position += 46 + name_length + extra_length + comment_length
        count += 1
    assert position == eocd
    last_length = eocd - last_start

    if nonstandard:
        if tamper_last_record:
            struct.pack_into("<HHH", raw, last_start + 28, 0, 0, last_length + 30)
            normal_size_cd = last_length + 76
        else:
            normal_size_cd = 0
        normal_entries = 1
        normal_offset_cd = central_start
    else:
        normal_entries = 0xFFFF
        normal_size_cd = 0xFFFFFFFF
        normal_offset_cd = 0xFFFFFFFF

    zip64_entry_count = count if entry_count is None else entry_count
    zip64_eocd = struct.pack(
        "<IQHHIIQQQQ",
        0x06064B50,
        44,
        45,
        45,
        0,
        0,
        zip64_entry_count,
        zip64_entry_count,
        size_cd,
        central_start + central_offset_delta,
    )
    locator = struct.pack("<IIQI", 0x07064B50, 0, eocd + locator_delta, 1)
    normal = bytearray(raw[eocd : eocd + 22])
    struct.pack_into("<HH", normal, 8, normal_entries, normal_entries)
    struct.pack_into("<I", normal, 12, normal_size_cd)
    struct.pack_into("<I", normal, 16, normal_offset_cd)
    path.write_bytes(bytes(raw[:eocd]) + zip64_eocd + locator + bytes(normal) + bytes(raw[eocd + 22 :]))
    return path


def test_docx_rejects_part_above_part_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source_store, "MAX_PART_BYTES", 1024)
    monkeypatch.setattr(source_store, "MAX_TOTAL_BYTES", 10000)
    source = _write_zip(
        tmp_path / "oversized-part.docx",
        {"word/document.xml": _document_xml(), "word/media/bomb.bin": b"y" * 4096},
    )
    store = SourceStore()

    with pytest.raises(SourceStoreError) as error:
        store.open_source(source)

    message = str(error.value)
    assert "word/media/bomb.bin" in message
    assert str(source_store.MAX_PART_BYTES) in message
    assert store._parts == {}
    assert store._handle is None


def test_docx_rejects_total_above_total_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source_store, "MAX_PART_BYTES", 1024)
    monkeypatch.setattr(source_store, "MAX_TOTAL_BYTES", 1200)
    source = _write_zip(
        tmp_path / "oversized-total.docx",
        {"word/document.xml": b"a" * 800, "word/styles.xml": b"b" * 800},
    )

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value)
    assert "word/styles.xml" in message
    assert str(source_store.MAX_TOTAL_BYTES) in message


def test_docx_rejects_unreferenced_bomb_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source_store, "MAX_PART_BYTES", 1024)
    monkeypatch.setattr(source_store, "MAX_TOTAL_BYTES", 10000)
    source = _write_zip(
        tmp_path / "unreferenced-bomb.docx",
        {"word/document.xml": _document_xml(), "_rels/unused.bin": b"z" * 4096},
    )

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value)
    assert "_rels/unused.bin" in message
    assert str(source_store.MAX_PART_BYTES) in message


def test_normal_docx_still_opens_and_resolves_document_part() -> None:
    source = Path(__file__).resolve().parents[1] / "assets" / "wx_template.docx"
    store = SourceStore()

    handle = store.open_source(source)
    payload = store.resolve({"source_id": handle.source_id, "part": "word/document.xml"})

    assert isinstance(payload, bytes)
    assert b"<w:document" in payload


def test_corrupt_docx_entry_is_wrapped_as_source_store_error(tmp_path: Path) -> None:
    source = _write_zip(tmp_path / "corrupt.docx", {"word/document.xml": _document_xml()})
    _corrupt_deflated_payload(source)
    store = SourceStore()

    with pytest.raises(SourceStoreError) as error:
        store.open_source(source)

    assert isinstance(error.value.__cause__, (zlib.error, BadZipFile))
    assert store._parts == {}
    assert store._handle is None


def test_forged_central_file_size_does_not_inflate_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_PART_BYTES", 1024 * 1024)
    source = _write_forged_one_byte_docx(tmp_path / "forged-size.docx")

    tracemalloc.start()
    try:
        with pytest.raises(SourceStoreError) as error:
            SourceStore().open_source(source)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert "word/document.xml" in str(error.value)
    assert str(source_store.MAX_PART_BYTES) in str(error.value)
    assert peak < 64 * 1024 * 1024


@pytest.mark.parametrize("compression", [ZIP_BZIP2, ZIP_LZMA])
def test_docx_rejects_unsupported_compression(
    tmp_path: Path, compression: int
) -> None:
    source = _write_zip(
        tmp_path / "unsupported-compression.docx",
        {"word/document.xml": _document_xml()},
        compression,
    )

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value)
    assert "word/document.xml" in message
    assert "compression" in message.lower()


def test_docx_rejects_encrypted_entry(tmp_path: Path) -> None:
    source = _write_zip(tmp_path / "encrypted.docx", {"word/document.xml": _document_xml()})
    _set_zip_flag(source, 0x1)

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value).lower()
    assert "word/document.xml" in message
    assert "encrypted" in message


def test_docx_rejects_unsupported_zip_flags(tmp_path: Path) -> None:
    source = _write_zip(tmp_path / "unsupported-flags.docx", {"word/document.xml": _document_xml()})
    _set_zip_flag(source, 0x60)

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value).lower()
    assert "word/document.xml" in message
    assert "unsupported flags" in message


def test_docx_rejects_excessive_entry_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_ENTRIES", 2)
    source = _write_zip(
        tmp_path / "many-entries.docx",
        {"word/document.xml": _document_xml(), "a.xml": b"a", "b.xml": b"b"},
    )

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value)
    assert str(source_store.MAX_ENTRIES) in message


def test_docx_rejects_entry_count_before_zipfile_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_ENTRIES", 10)
    entries = {"word/document.xml": _document_xml()}
    entries.update({f"part-{index}.xml": b"x" for index in range(10)})
    source = _write_zip(tmp_path / "too-many-entries.docx", entries)

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not parse the central directory")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value)
    assert str(source_store.MAX_ENTRIES) in message


def test_forged_eocd_count_still_rejected_before_zipfile_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_ENTRIES", 10)
    entries = {"word/document.xml": _document_xml()}
    entries.update({f"part-{index}.xml": b"x" for index in range(10)})
    source = _write_zip(tmp_path / "forged-eocd-count.docx", entries)
    _set_eocd_entry_count(source, 1)

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not parse the central directory")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value)
    assert str(source_store.MAX_ENTRIES) in message


def test_docx_rejects_truncated_central_record_before_zipfile_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_zip(tmp_path / "truncated-central.docx", {"word/document.xml": _document_xml()})
    _truncate_first_central_record(source)

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not parse the central directory")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value).lower()
    assert "central directory" in message
    assert "truncated" in message


def test_docx_rejects_invalid_central_signature_before_zipfile_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_zip(tmp_path / "bad-central-signature.docx", {"word/document.xml": _document_xml()})
    _break_first_central_signature(source)

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not parse the central directory")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value).lower()
    assert "central directory" in message
    assert "signature" in message


def test_docx_rejects_central_directory_out_of_bounds_before_zipfile_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_zip(tmp_path / "central-out-of-bounds.docx", {"word/document.xml": _document_xml()})
    _set_eocd_central_size(source, 0xFFFFFFF0)

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not parse the central directory")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value).lower()
    assert "central directory" in message
    assert "out of bounds" in message


def test_nonstandard_zip64_cannot_bypass_entry_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_ENTRIES", 10)
    entries = {"word/document.xml": _document_xml()}
    entries.update({f"part-{index}.xml": b"x" for index in range(9)})
    entries["l" * 200 + ".xml"] = b"z"
    source = _write_zip(tmp_path / "zip64-bypass.docx", entries)
    _insert_zip64_eocd(source, nonstandard=True)

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not parse the central directory")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    assert str(source_store.MAX_ENTRIES) in str(error.value)


def test_mispointed_zip64_locator_uses_fixed_eocd_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_ENTRIES", 10)
    entries = {"word/document.xml": _document_xml()}
    entries.update({f"part-{index}.xml": b"x" for index in range(9)})
    entries["l" * 200 + ".xml"] = b"z"
    source = _write_zip(tmp_path / "mispointed-zip64-locator.docx", entries)
    _insert_zip64_eocd(source, nonstandard=True, locator_delta=-50, central_offset_delta=-50)

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not parse the central directory")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    assert str(source_store.MAX_ENTRIES) in str(error.value)


def test_mispointed_zip64_locator_with_forged_count_is_rejected_by_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_ENTRIES", 10)
    entries = {"word/document.xml": _document_xml()}
    entries.update({f"part-{index}.xml": b"x" for index in range(9)})
    entries["l" * 200 + ".xml"] = b"z"
    source = _write_zip(tmp_path / "mispointed-zip64-forged-count.docx", entries)
    _insert_zip64_eocd(
        source,
        nonstandard=True,
        locator_delta=-50,
        entry_count=1,
        central_offset_delta=-50,
        tamper_last_record=False,
    )

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not parse the central directory")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    assert str(source_store.MAX_ENTRIES) in str(error.value)


def test_standard_zip64_still_opens_and_resolves_document_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_ENTRIES", 20)
    entries = {"word/document.xml": _document_xml()}
    entries.update({f"part-{index}.xml": b"x" for index in range(10)})
    source = _write_zip(tmp_path / "standard-zip64.docx", entries)
    _insert_zip64_eocd(source, nonstandard=False)
    store = SourceStore()

    handle = store.open_source(source)
    payload = store.resolve({"source_id": handle.source_id, "part": "word/document.xml"})

    assert payload == _document_xml()


def test_source_size_is_checked_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_zip(tmp_path / "large-source.docx", {"word/document.xml": _document_xml()})
    monkeypatch.setattr(source_store, "MAX_TOTAL_BYTES", 10)

    def fail_read(_: Path) -> bytes:
        raise AssertionError("read_bytes must not run before the size check")

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value)
    assert "size limit" in message
    assert str(source_store.MAX_TOTAL_BYTES) in message


def test_source_growth_after_stat_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "growing-source.docx"
    source.write_bytes(b"12345")
    monkeypatch.setattr(source_store, "MAX_TOTAL_BYTES", 4)

    class SmallStat:
        st_size = 1

    monkeypatch.setattr(Path, "stat", lambda _: SmallStat())
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    assert f"exceeds total size limit {source_store.MAX_TOTAL_BYTES} bytes" in str(error.value)


def test_actual_decompressed_total_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_store, "MAX_PART_BYTES", 1024 * 1024)
    monkeypatch.setattr(source_store, "MAX_TOTAL_BYTES", 1536 * 1024)
    source = _write_forged_size_zip(
        tmp_path / "forged-total.docx",
        {"word/document.xml": 1024 * 1024, "word/styles.xml": 1024 * 1024},
    )

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    message = str(error.value)
    assert "word/styles.xml" in message
    assert f"exceeds total size limit {source_store.MAX_TOTAL_BYTES} bytes" in message


def test_docx_wraps_unsupported_extract_version(tmp_path: Path) -> None:
    source = _write_zip(tmp_path / "future-version.docx", {"word/document.xml": _document_xml()})
    _set_central_extract_version(source, 99)

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    assert isinstance(error.value.__cause__, NotImplementedError)
    assert "version" in str(error.value).lower()


def test_open_source_wraps_path_value_error(tmp_path: Path) -> None:
    source = tmp_path / "bad\0name.docx"

    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    assert isinstance(error.value.__cause__, ValueError)


def test_docx_wraps_zipfile_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _write_zip(tmp_path / "bad-local-header.docx", {"word/document.xml": _document_xml()})

    def fail_zipfile(*args: object, **kwargs: object) -> None:
        raise ValueError("negative seek value")

    monkeypatch.setattr(source_store, "ZipFile", fail_zipfile)
    with pytest.raises(SourceStoreError) as error:
        SourceStore().open_source(source)

    assert isinstance(error.value.__cause__, ValueError)
    assert "negative seek value" in str(error.value)
