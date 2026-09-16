from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

import pytest
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PYTHON = Path(sys.executable)
TEMPLATE = ROOT / "assets" / "wx_template.docx"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W_NS}
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9xQAAAABJRU5ErkJggg=="
)

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.auditing import check_saved_output
from wxdf.rendering import render


def _run_native(source: Path, tmp_path: Path, name: str) -> tuple[dict, dict, dict, Path]:
    output = tmp_path / f"{name}.output.docx"
    report = tmp_path / f"{name}.report.json"
    source_ast = tmp_path / f"{name}.source.json"
    normalized_ast = tmp_path / f"{name}.normalized.json"
    completed = subprocess.run(
        [
            str(PYTHON),
            str(SCRIPTS / "main.py"),
            "--input", str(source),
            "--output", str(output),
            "--template", str(TEMPLATE),
            "--pipeline", "modular",
            "--modular-profile", "native-v2",
            "--list-policy", "wx-default-v2",
            "--llm-enhance", "off",
            "--report", str(report),
            "--source-ast", str(source_ast),
            "--ast", str(normalized_ast),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return (
        json.loads(report.read_text(encoding="utf-8")),
        json.loads(source_ast.read_text(encoding="utf-8")),
        json.loads(normalized_ast.read_text(encoding="utf-8")),
        output,
    )


def _build_docx(tmp_path: Path) -> Path:
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("前")
    paragraph.add_run().add_picture(io.BytesIO(PNG_1X1))
    paragraph.add_run("中")
    relation_id = paragraph.part.relate_to("https://example.org", RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relation_id)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "链接"
    run.append(text)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)
    paragraph.add_run("后")
    paragraph.add_run().add_break()
    paragraph.add_run("换行")
    source = tmp_path / "mixed.docx"
    document.save(source)
    return source


def _inline_block(ast: dict) -> dict:
    return next(
        block
        for block in ast["document"]["blocks"].values()
        if block.get("inline_content")
        and "前" in "".join(item.get("text", "") for item in block["inline_content"])
        and "换行" in "".join(item.get("text", "") for item in block["inline_content"])
    )


def _inline_signature(inline_content: list[dict]) -> list[str]:
    return [str(item.get("kind")) for item in inline_content]


def _assert_source_inline_contract(ast: dict) -> None:
    block = _inline_block(ast)
    inline = block["inline_content"]
    assert _inline_signature(inline) == ["text", "image", "text", "link", "text", "break", "text"]
    link = next(item for item in inline if item["kind"] == "link")
    assert link["target"] == "https://example.org"
    assert [item["kind"] for item in link["children"]] == ["text"]
    assert len(ast["document"]["resources"]) == 1
    assert ast["document"]["resources"]


def _output_inline_signature(output: Path) -> tuple[list[str], set[str]]:
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        relationships = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
    hyperlink_targets = {
        relationship.get("Id"): relationship.get("Target")
        for relationship in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
        if relationship.get("Type", "").endswith("/hyperlink")
    }
    paragraph = next(
        paragraph
        for paragraph in document.findall(".//w:p", NS)
        if "前" in "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))
        and "换行" in "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))
    )
    signature: list[str] = []
    targets: set[str] = set()
    for child in paragraph:
        if child.tag == qn("w:pPr"):
            continue
        if child.tag == qn("w:hyperlink"):
            signature.append("link")
            target = hyperlink_targets.get(child.get(qn("r:id")))
            if target:
                targets.add(target)
            continue
        if child.tag != qn("w:r"):
            continue
        if child.find("w:drawing", NS) is not None:
            signature.append("image")
        elif child.find("w:br", NS) is not None or child.find("w:cr", NS) is not None:
            signature.append("break")
        elif child.find("w:t", NS) is not None:
            signature.append("text")
    return signature, targets


def _rewrite_docx_part(source: Path, target: Path, part_name: str, transform) -> None:
    with ZipFile(source) as original, ZipFile(target, "w") as rewritten:
        for info in original.infolist():
            data = original.read(info.filename)
            if info.filename == part_name:
                data = transform(data)
            rewritten.writestr(info, data)


def _convert_inline_image_to_vml(source: Path, target: Path) -> None:
    def convert(data: bytes) -> bytes:
        root = ElementTree.fromstring(data)
        drawing = root.find(".//w:drawing", NS)
        assert drawing is not None
        blip = drawing.find(".//a:blip", {**NS, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"})
        assert blip is not None
        relationship_id = blip.get(f"{{{R_NS}}}embed")
        assert relationship_id
        run = next(parent for parent in root.iter() if drawing in list(parent))
        pict = ElementTree.fromstring(
            f'<w:pict xmlns:w="{W_NS}" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:r="{R_NS}">'
            f'<v:shape id="_x0000_i1025" type="#_x0000_t75" style="width:1pt;height:1pt">'
            f'<v:imagedata r:id="{relationship_id}"/></v:shape></w:pict>'
        )
        run.remove(drawing)
        run.append(pict)
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    _rewrite_docx_part(source, target, "word/document.xml", convert)


@pytest.mark.parametrize("source_kind", ["markdown", "docx"])
def test_l14_mixed_inline_order_links_breaks_and_resources_survive_native_pipeline(tmp_path: Path, source_kind: str) -> None:
    if source_kind == "markdown":
        image = tmp_path / "pixel.png"
        image.write_bytes(PNG_1X1)
        source = tmp_path / "mixed.md"
        source.write_text("前 ![像素](pixel.png) 中 [链接](https://example.org) 后\\\n换行\n", encoding="utf-8")
    else:
        source = _build_docx(tmp_path)

    report, source_ast, normalized, output = _run_native(source, tmp_path, source_kind)

    _assert_source_inline_contract(source_ast)
    _assert_source_inline_contract(normalized["source_ast"])
    signature, targets = _output_inline_signature(output)
    assert signature == ["text", "image", "text", "link", "text", "break", "text"]
    assert targets == {"https://example.org"}
    assert report["saved_output_audit"]["passed"] is True


def test_l14_saved_output_audit_rejects_tampered_hyperlink_target(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(PNG_1X1)
    source = tmp_path / "mixed.md"
    source.write_text("前 ![像素](pixel.png) 中 [链接](https://example.org) 后\n换行\n", encoding="utf-8")
    _, _, normalized, _ = _run_native(source, tmp_path, "tamper")
    resources = {
        resource_id: PNG_1X1
        for resource_id, resource in normalized["source_ast"]["document"]["resources"].items()
        if resource.get("kind") == "image"
    }
    baseline = tmp_path / "baseline.docx"
    rendered = render(normalized, baseline, template_path=TEMPLATE, resources=resources)

    def tamper(data: bytes) -> bytes:
        root = ElementTree.fromstring(data)
        for relationship in root.findall(f"{{{PKG_REL_NS}}}Relationship"):
            if relationship.get("Type", "").endswith("/hyperlink"):
                relationship.set("Target", "https://tampered.example")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    tampered = tmp_path / "tampered.docx"
    _rewrite_docx_part(baseline, tampered, "word/_rels/document.xml.rels", tamper)
    findings = check_saved_output(tampered, normalized, rendered["render_map"])

    assert findings["passed"] is False
    assert any(issue["code"] == "inline_content_changed" for issue in findings["issues"])


def test_l14_docx_vml_inline_image_keeps_mixed_inline_semantics(tmp_path: Path) -> None:
    drawingml_source = _build_docx(tmp_path)
    source = tmp_path / "mixed-vml.docx"
    _convert_inline_image_to_vml(drawingml_source, source)

    report, source_ast, normalized, output = _run_native(source, tmp_path, "vml")

    _assert_source_inline_contract(source_ast)
    _assert_source_inline_contract(normalized["source_ast"])
    signature, targets = _output_inline_signature(output)
    assert signature == ["text", "image", "text", "link", "text", "break", "text"]
    assert targets == {"https://example.org"}
    assert report["saved_output_audit"]["passed"] is True
