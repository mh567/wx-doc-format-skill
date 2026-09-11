from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

from modular_support import (
    BASELINE_MANIFEST_PATH,
    build_docx_fixture,
    load_baseline_manifest,
    run_native_markdown_conversion,
    sha256,
)


W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def test_baseline_manifest_is_hand_authored_and_pins_inputs() -> None:
    manifest = load_baseline_manifest()

    assert BASELINE_MANIFEST_PATH.is_file()
    assert manifest["schema_version"] == "modular-v3-baseline-1"
    assert manifest["inputs"]["markdown"] == "input/test_baseline.md"
    assert manifest["inputs"]["template"] == "assets/wx_template.docx"
    assert sha256(Path(manifest["inputs"]["markdown"])) == manifest["inputs"]["input_sha256"]
    assert sha256(Path(manifest["inputs"]["template"])) == manifest["inputs"]["template_sha256"]
    assert manifest["expected"]["markdown_blocks"]
    assert manifest["expected"]["docx_blocks"]
    assert manifest["local_only_inputs"] == []


def test_docx_fixture_contains_real_numbering_ooxml(tmp_path: Path) -> None:
    manifest = load_baseline_manifest()
    fixture = build_docx_fixture(tmp_path)

    with ZipFile(fixture) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))

    paragraphs = document.findall(f".//{W_NS}p")
    paragraph_texts = [
        "".join(node.text or "" for node in paragraph.findall(f".//{W_NS}t"))
        for paragraph in paragraphs
    ]
    numbered = [paragraph for paragraph in paragraphs if paragraph.find(f"{W_NS}pPr/{W_NS}numPr") is not None]

    expected = manifest["expected"]["docx_blocks"]
    assert paragraph_texts == [block["text"] for block in expected]
    assert len(numbered) == sum(block["numbered"] for block in expected)
    assert all(paragraph.find(f"{W_NS}pPr/{W_NS}numPr/{W_NS}numId") is not None for paragraph in numbered)


def test_native_markdown_baseline_conversion_is_reproducible(tmp_path: Path) -> None:
    result = run_native_markdown_conversion(tmp_path)

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.output.is_file()
    assert result.report.is_file()
    assert result.source_ast.is_file()
    assert result.normalized_ast.is_file()

    with ZipFile(result.output) as archive:
        assert archive.testzip() is None

    report = json.loads(result.report.read_text(encoding="utf-8"))
    source_ast = json.loads(result.source_ast.read_text(encoding="utf-8"))
    normalized_ast = json.loads(result.normalized_ast.read_text(encoding="utf-8"))

    assert report["pipeline"] == "modular"
    assert report["profile"] == "wx-default-v2"
    assert report["model_audit"]["passed"] is True
    assert report["saved_output_audit"]["passed"] is True
    assert source_ast["document"]["blocks"]
    assert normalized_ast["source_ast"]["document"]["blocks"]
