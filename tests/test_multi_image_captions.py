from __future__ import annotations

import copy
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Cm
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from template_profile import load_template_profile
from wxdf.auditing import check_model, check_saved_output
from wxdf.format_contract import audit_document_format
from wxdf.kernel import ConversionKernel
from wxdf.page_contract import audit_page_contract
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.captions import (
    collect_caption_candidates,
    collect_source_caption_semantics,
)
from wxdf.semantics.source_ast import build_source_ast
from wxdf.semantics.tables import collect_source_table_semantics

TEMPLATE = ROOT / "assets" / "wx_template.docx"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _make_image(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (48, 32)) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def _convert(tmp_path: Path, source: Path, monkeypatch) -> tuple[Path, object, dict, dict]:
    import wxdf.rendering as rendering_module

    captured: dict[str, object] = {}
    original = rendering_module.render

    def capture_render(normalized, output_path, **kwargs):
        outcome = original(normalized, output_path, **kwargs)
        captured["render_map"] = outcome["render_map"]
        captured["normalized"] = normalized
        return outcome

    monkeypatch.setattr(rendering_module, "render", capture_render)
    output = tmp_path / f"{source.stem}-out.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    result = ConversionKernel().convert(request)
    return output, result, captured["normalized"], captured["render_map"]


def _body_flow(docx_path: Path) -> list[tuple[str, str]]:
    """Return ordered ('image', sha_or_part) and ('caption'|'paragraph', text) items."""

    with ZipFile(docx_path) as archive:
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        style_names = {
            style.get(f"{W}styleId"): (
                style.find("w:name", {"w": W[1:-1]}).get(f"{W}val")
                if style.find("w:name", {"w": W[1:-1]}) is not None
                else ""
            )
            for style in styles.findall("w:style", {"w": W[1:-1]})
        }
        rels = ElementTree.fromstring(archive.read("word/_rels/document.xml.rels"))
        rel_targets = {
            rel.get("Id"): rel.get("Target")
            for rel in rels
            if (rel.get("Type") or "").endswith("/image")
        }
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{W}body")
    items: list[tuple[str, str]] = []
    for child in list(body):
        if child.tag == f"{W}p":
            blips = child.findall(f".//{DRAWING_NS}blip")
            text = "".join(node.text or "" for node in child.iter(f"{W}t"))
            pstyle = child.find(f"{W}pPr/{W}pStyle")
            style_id = pstyle.get(f"{W}val") if pstyle is not None else None
            style_name = style_names.get(style_id, style_id or "Normal")
            if blips and not text.strip():
                for blip in blips:
                    rid = blip.get(f"{REL_NS}embed")
                    items.append(("image", str(rel_targets.get(rid) or rid)))
            elif text.strip():
                kind = "caption" if style_name.casefold() == "caption" else "paragraph"
                items.append((kind, text))
        elif child.tag == f"{W}tbl":
            items.append(("table", ""))
    return items


def _image_paragraph_properties(docx_path: Path) -> list[tuple[list[str], list[str], dict[str, str]]]:
    doc = Document(docx_path)
    props: list[tuple[list[str], list[str], dict[str, str]]] = []
    for p in doc.paragraphs:
        if any(el.tag.rsplit("}", 1)[-1] == "blip" for el in p._p.iter()):
            ppr = p._p.find(qn("w:pPr"))
            tags = [el.tag.rsplit("}", 1)[-1] for el in ppr] if ppr is not None else []
            jcs = [el.get(qn("w:val")) for el in ppr.iter(qn("w:jc"))] if ppr is not None else []
            ind = ppr.find(qn("w:ind")) if ppr is not None else None
            ind_attrs = {k.rsplit("}", 1)[-1]: v for k, v in ind.attrib.items()} if ind is not None else {}
            props.append((tags, jcs, ind_attrs))
    return props


def test_two_images_in_same_paragraph_without_captions_get_individual_captions(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (10, 80, 160))
    img_b = _make_image(tmp_path / "b.png", (160, 80, 10))
    doc = Document()
    doc.add_heading("第一章", level=1)
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img_a), width=Cm(3))
    para.add_run().add_picture(str(img_b), width=Cm(3))
    source = tmp_path / "same-para-two-images.docx"
    doc.save(source)

    output, result, normalized, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["model_audit"]["passed"] is True, result.report["model_audit"]["issues"]
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]

    flow = _body_flow(output)
    assert [(kind, val if kind == "caption" else "") for kind, val in flow[2:]] == [
        ("image", ""),
        ("caption", "图 1-1 "),
        ("image", ""),
        ("caption", "图 1-2 "),
    ], flow

    for tags, jcs, ind_attrs in _image_paragraph_properties(output):
        assert tags == ["jc", "ind"]
        assert jcs == ["center"]
        assert ind_attrs == {"firstLine": "0", "firstLineChars": "0", "left": "0", "leftChars": "0"}


def test_two_images_with_soft_break_split_into_standalone_blocks_without_audit_block(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (20, 100, 180))
    img_b = _make_image(tmp_path / "b.png", (180, 100, 20))
    doc = Document()
    doc.add_heading("第一章", level=1)
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img_a), width=Cm(3))
    para.add_run().add_break()
    para.add_run().add_picture(str(img_b), width=Cm(3))
    source = tmp_path / "same-para-soft-break.docx"
    doc.save(source)

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]

    flow = _body_flow(output)
    assert [(kind, val if kind == "caption" else "") for kind, val in flow[2:]] == [
        ("image", ""),
        ("caption", "图 1-1 "),
        ("image", ""),
        ("caption", "图 1-2 "),
    ], flow


def test_two_paragraphs_followed_by_grouped_authored_captions_pair_in_source_order(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (30, 120, 190))
    img_b = _make_image(tmp_path / "b.png", (190, 120, 30))
    doc = Document()
    doc.add_heading("第一章", level=1)
    doc.add_paragraph().add_run().add_picture(str(img_a), width=Cm(3))
    doc.add_paragraph().add_run().add_picture(str(img_b), width=Cm(3))
    cap1 = doc.add_paragraph("图1 架构示意", style="Caption")
    bold_run = cap1.add_run("（新）")
    bold_run.bold = True
    doc.add_paragraph("图2 部署流程", style="Caption")
    source = tmp_path / "two-paras-grouped-captions.docx"
    doc.save(source)

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["model_audit"]["passed"] is True, result.report["model_audit"]["issues"]
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]

    flow = _body_flow(output)
    assert [(kind, val if kind == "caption" else "") for kind, val in flow[2:]] == [
        ("image", ""),
        ("caption", "图 1-1 架构示意（新）"),
        ("image", ""),
        ("caption", "图 1-2 部署流程"),
    ], flow


def test_same_paragraph_two_images_followed_by_grouped_authored_captions_pair_and_interleave(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (40, 130, 200))
    img_b = _make_image(tmp_path / "b.png", (200, 130, 40))
    doc = Document()
    doc.add_heading("第一章", level=1)
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img_a), width=Cm(3))
    para.add_run().add_break()
    para.add_run().add_picture(str(img_b), width=Cm(3))
    doc.add_paragraph("图1 界面甲")
    doc.add_paragraph("图2 界面乙")
    source = tmp_path / "same-para-grouped-captions.docx"
    doc.save(source)

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]

    flow = _body_flow(output)
    assert [(kind, val if kind == "caption" else "") for kind, val in flow[2:]] == [
        ("image", ""),
        ("caption", "图 1-1 界面甲"),
        ("image", ""),
        ("caption", "图 1-2 界面乙"),
    ], flow


def test_reused_image_file_in_same_paragraph_produces_two_independent_instances(
    tmp_path: Path, monkeypatch
) -> None:
    img = _make_image(tmp_path / "shared.png", (50, 140, 210))
    doc = Document()
    doc.add_heading("第一章", level=1)
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img), width=Cm(3))
    para.add_run().add_picture(str(img), width=Cm(3))
    source = tmp_path / "reused-image.docx"
    doc.save(source)

    output, result, normalized, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]
    image_nodes = [
        block
        for block in normalized["source_ast"]["document"]["blocks"].values()
        if block.get("block_type") == "image" and block.get("materialization_mode") == "standalone"
    ]
    assert len(image_nodes) == 2
    assert image_nodes[0]["generation_basis"] != image_nodes[1]["generation_basis"]
    flow = _body_flow(output)
    assert [(kind, val if kind == "caption" else "") for kind, val in flow[2:]] == [
        ("image", ""),
        ("caption", "图 1-1 "),
        ("image", ""),
        ("caption", "图 1-2 "),
    ], flow


def test_inline_icon_table_images_and_protected_multi_image_paragraphs_stay_unsplit(
    tmp_path: Path, monkeypatch
) -> None:
    icon = _make_image(tmp_path / "icon.png", (80, 80, 80), size=(16, 16))
    cell_img = _make_image(tmp_path / "cell.png", (90, 160, 90))
    break_img_a = _make_image(tmp_path / "ba.png", (120, 40, 40))
    break_img_b = _make_image(tmp_path / "bb.png", (40, 40, 120))

    doc = Document()
    doc.add_heading("第一章", level=1)
    p_icon = doc.add_paragraph("点击")
    p_icon.add_run().add_picture(str(icon), width=Cm(0.5))
    p_icon.add_run("按钮。")

    table = doc.add_table(rows=1, cols=1)
    cell_p = table.cell(0, 0).paragraphs[0]
    cell_p.add_run().add_picture(str(cell_img), width=Cm(1.5))
    cell_p.add_run().add_picture(str(cell_img), width=Cm(1.5))

    p_page = doc.add_paragraph()
    p_page.add_run().add_picture(str(break_img_a), width=Cm(2))
    p_page.add_run().add_break(WD_BREAK.PAGE)
    p_page.add_run().add_picture(str(break_img_b), width=Cm(2))

    p_sub = doc.add_paragraph("(a)")
    p_sub.add_run().add_picture(str(break_img_a), width=Cm(2))
    p_sub.add_run(" (b)")
    p_sub.add_run().add_picture(str(break_img_b), width=Cm(2))

    p_bm = doc.add_paragraph()
    bm_start = OxmlElement("w:bookmarkStart")
    bm_start.set(qn("w:id"), "9")
    bm_start.set(qn("w:name"), "fig_group")
    p_bm._p.append(bm_start)
    p_bm.add_run().add_picture(str(break_img_a), width=Cm(2))
    p_bm.add_run().add_picture(str(break_img_b), width=Cm(2))
    bm_end = OxmlElement("w:bookmarkEnd")
    bm_end.set(qn("w:id"), "9")
    p_bm._p.append(bm_end)

    source = tmp_path / "boundaries.docx"
    doc.save(source)

    facts = parse(source)
    ast = build_source_ast(facts)
    standalone_images = [
        node_id
        for node_id, block in ast["document"]["blocks"].items()
        if block.get("block_type") == "image" and block.get("materialization_mode") == "standalone"
    ]
    assert standalone_images == [], standalone_images

    output, result, normalized, _ = _convert(tmp_path, source, monkeypatch)
    assert result.exit_code == 0, result.diagnostics
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]
    # Neither the inline icon nor the table-cell images get caption overlays or captions,
    # and the protected multi-image paragraphs remain unsplit in the saved document.
    overlay = normalized["overlay"]
    blocks = normalized["source_ast"]["document"]["blocks"]
    captioned_bases = {
        str((blocks[node_id].get("generation_basis") or [""])[0])
        for node_id, vals in overlay.items()
        if vals.get("caption_type") == "figure" and vals.get("eligible") is True
    }
    icon_block_id = next(
        bid for bid, blk in blocks.items()
        if blk.get("block_type") == "paragraph"
        and "点击" in "".join(i.get("text", "") for i in blk.get("inline_content", []))
    )
    assert icon_block_id not in captioned_bases
    out_doc = Document(output)
    assert any(
        len(p._p.findall(f".//{DRAWING_NS}blip")) == 2
        for p in out_doc.paragraphs
        if "点击" not in p.text
    )


def test_composite_figure_with_single_shared_caption_preserves_caption_and_records_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (60, 90, 150))
    img_b = _make_image(tmp_path / "b.png", (150, 90, 60))
    doc = Document()
    doc.add_heading("第一章", level=1)
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img_a), width=Cm(3))
    para.add_run().add_picture(str(img_b), width=Cm(3))
    doc.add_paragraph("图1 登录与注册界面", style="Caption")
    source = tmp_path / "composite-shared-caption.docx"
    doc.save(source)

    facts = parse(source)
    semantics = collect_source_caption_semantics(facts, collect_source_table_semantics(facts))
    assert any(item.get("status") == "ambiguous" for item in semantics["captions"]), semantics
    assert any(issue.get("code") == "caption_attribution_ambiguous" for issue in semantics["issues"]), semantics

    output, result, normalized, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["model_audit"]["passed"] is True, result.report["model_audit"]["issues"]
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]
    assert any(item.get("code") == "caption_attribution_ambiguous" for item in result.diagnostics), result.diagnostics

    flow = _body_flow(output)
    assert flow[2:] == [
        ("image", flow[2][1]),
        ("image", flow[3][1]),
        ("caption", "图 1-1 登录与注册界面"),
    ], flow
    tpl = Document(TEMPLATE)
    out_doc = Document(output)
    assert audit_document_format(out_doc, load_template_profile(TEMPLATE), tpl)["issues"] == []
    assert audit_page_contract(out_doc, tpl)["issues"] == []
    assert not any(
        block.get("role") == "caption" and block.get("generated_by") == "caption_semantics.sequence_caption"
        for block in normalized["source_ast"]["document"]["blocks"].values()
    )


def test_conflicting_numbered_grouped_captions_preserve_sources_and_record_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (70, 110, 170))
    img_b = _make_image(tmp_path / "b.png", (170, 110, 70))
    doc = Document()
    doc.add_heading("第一章", level=1)
    doc.add_paragraph().add_run().add_picture(str(img_a), width=Cm(3))
    doc.add_paragraph().add_run().add_picture(str(img_b), width=Cm(3))
    doc.add_paragraph("图2 第二张图")
    doc.add_paragraph("图1 第一张图")
    source = tmp_path / "conflicting-numbers.docx"
    doc.save(source)

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert any(item.get("code") == "caption_attribution_ambiguous" for item in result.diagnostics), result.diagnostics
    flow = _body_flow(output)
    assert [kind for kind, _ in flow[2:]] == ["image", "image", "caption", "caption"]
    assert [val for kind, val in flow[2:] if kind == "caption"] == ["图 1-1 第二张图", "图 1-2 第一张图"]
    tpl = Document(TEMPLATE)
    out_doc = Document(output)
    assert audit_document_format(out_doc, load_template_profile(TEMPLATE), tpl)["issues"] == []
    assert audit_page_contract(out_doc, tpl)["issues"] == []


def test_appendix_multi_image_paragraph_stays_in_appendix_and_passes_audits(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (10, 150, 90))
    img_b = _make_image(tmp_path / "b.png", (90, 150, 10))
    doc = Document(str(TEMPLATE))
    body = doc.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    doc.add_paragraph("第一章", style="Heading 1")
    doc.add_paragraph("正文。")
    doc.add_paragraph("（资料性）\n附录示例", style="附录标题")
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img_a), width=Cm(2))
    para.add_run().add_picture(str(img_b), width=Cm(2))
    doc.add_paragraph("图A.1 甲图")
    doc.add_paragraph("图A.2 乙图")
    source = tmp_path / "appendix-multi-image.docx"
    doc.save(source)

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["appendix_preservation_audit"]["passed"] is True, result.report["appendix_preservation_audit"]["issues"]
    assert result.report["model_audit"]["passed"] is True, result.report["model_audit"]["issues"]
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]
    flow = _body_flow(output)
    assert any(val == "图A.1 甲图" for _, val in flow)
    assert any(val == "图A.2 乙图" for _, val in flow)


def test_audits_reject_unbound_figure_and_reordered_saved_image_caption_sequence(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (15, 75, 195))
    img_b = _make_image(tmp_path / "b.png", (195, 75, 15))
    doc = Document()
    doc.add_heading("第一章", level=1)
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img_a), width=Cm(3))
    para.add_run().add_picture(str(img_b), width=Cm(3))
    doc.add_paragraph("图1 图甲")
    doc.add_paragraph("图2 图乙")
    source = tmp_path / "audit-negative.docx"
    doc.save(source)

    output, result, normalized, render_map = _convert(tmp_path, source, monkeypatch)
    assert result.exit_code == 0, result.diagnostics

    # 1. Model audit rejects an eligible figure whose caption binding is missing.
    broken_model = copy.deepcopy(normalized)
    blocks = broken_model["source_ast"]["document"]["blocks"]
    first_caption_id = next(bid for bid, blk in blocks.items() if blk.get("role") == "caption")
    blocks[first_caption_id]["role"] = "body"
    blocks[first_caption_id].pop("caption_type", None)
    blocks[first_caption_id].pop("target_id", None)
    model_audit = check_model(broken_model)
    assert model_audit["passed"] is False
    assert any(issue["code"] == "figure_caption_binding_invalid" for issue in model_audit["issues"])

    # 2. Saved output audit rejects swapping image B and caption A in the saved DOCX.
    tampered = tmp_path / "swapped-order.docx"
    saved_doc = Document(output)
    body = saved_doc.element.body
    body_paragraphs = [el for el in list(body) if el.tag == qn("w:p")]
    # Find caption A and the second image paragraph and swap them in body.
    cap_a = next(el for el in body_paragraphs if "图甲" in "".join(t.text or "" for t in el.iter(qn("w:t"))))
    img_b_p = [el for el in body_paragraphs if any(n.tag.rsplit("}", 1)[-1] == "blip" for n in el.iter())][1]
    idx_cap = list(body).index(cap_a)
    idx_img = list(body).index(img_b_p)
    assert idx_cap < idx_img
    body.remove(img_b_p)
    body.insert(idx_cap, img_b_p)
    saved_doc.save(tampered)

    saved_audit = check_saved_output(tampered, normalized, render_map)
    assert saved_audit["passed"] is False
    assert any(
        issue["code"] in {"figure_caption_order_changed", "paragraph_order_changed"}
        for issue in saved_audit["issues"]
    ), saved_audit["issues"]


def test_list_trailing_multi_image_and_normal_captions_resolve_without_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (11, 88, 177))
    img_b = _make_image(tmp_path / "b.png", (177, 88, 11))
    doc = Document()
    doc.add_heading("第一章", level=1)
    doc.add_paragraph("a) 第一步操作")
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img_a), width=Cm(2.5))
    para.add_run().add_picture(str(img_b), width=Cm(2.5))
    doc.add_paragraph("图1 步骤图甲")
    doc.add_paragraph("图2 步骤图乙")
    doc.add_paragraph("b) 第二步操作")
    source = tmp_path / "list-trailing-captions.docx"
    doc.save(source)

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["model_audit"]["passed"] is True, result.report["model_audit"]["issues"]
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]
    flow = _body_flow(output)
    assert [(kind, val if kind == "caption" else "") for kind, val in flow[2:]] == [
        ("paragraph", ""),
        ("image", ""),
        ("caption", "图 1-1 步骤图甲"),
        ("image", ""),
        ("caption", "图 1-2 步骤图乙"),
        ("paragraph", ""),
    ], flow


def test_unsplit_protected_multi_image_with_single_caption_generates_no_extra_caption(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (25, 135, 95))
    img_b = _make_image(tmp_path / "b.png", (95, 135, 25))
    doc = Document()
    doc.add_heading("第一章", level=1)
    p_bm = doc.add_paragraph()
    bm_start = OxmlElement("w:bookmarkStart")
    bm_start.set(qn("w:id"), "12")
    bm_start.set(qn("w:name"), "protected_pair")
    p_bm._p.append(bm_start)
    p_bm.add_run().add_picture(str(img_a), width=Cm(2))
    p_bm.add_run().add_picture(str(img_b), width=Cm(2))
    bm_end = OxmlElement("w:bookmarkEnd")
    bm_end.set(qn("w:id"), "12")
    p_bm._p.append(bm_end)
    doc.add_paragraph("图1 书签双图")
    source = tmp_path / "bookmark-single-caption.docx"
    doc.save(source)

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["model_audit"]["passed"] is True, result.report["model_audit"]["issues"]
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]
    captions = [
        p.text for p in Document(output).paragraphs
        if p.style and p.style.name == "Caption"
    ]
    assert captions == ["图 1-1 书签双图"], captions


def test_link_only_paragraph_and_parenthesized_label_caption_pass_saved_flow_audit(
    tmp_path: Path, monkeypatch
) -> None:
    img_a = _make_image(tmp_path / "a.png", (45, 95, 195))
    doc = Document()
    doc.add_heading("第一章", level=1)
    link_p = doc.add_paragraph()
    hyperlink = OxmlElement("w:hyperlink")
    rid = link_p.part.relate_to("https://example.com/docs", RT.HYPERLINK, is_external=True)
    hyperlink.set(qn("r:id"), rid)
    link_run = OxmlElement("w:r")
    link_text = OxmlElement("w:t")
    link_text.text = "参考文档"
    link_run.append(link_text)
    hyperlink.append(link_run)
    link_p._p.append(hyperlink)

    doc.add_paragraph().add_run().add_picture(str(img_a), width=Cm(3))
    doc.add_paragraph("（图1）")
    source = tmp_path / "link-and-paren-caption.docx"
    doc.save(source)

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)

    assert result.exit_code == 0, result.diagnostics
    assert result.report["saved_output_audit"]["passed"] is True, result.report["saved_output_audit"]["issues"]
    flow = _body_flow(output)
    assert [(kind, val if kind in {"paragraph", "caption"} else "") for kind, val in flow[2:]] == [
        ("paragraph", "参考文档"),
        ("image", ""),
        ("caption", "图 1-1 "),
    ], flow


def test_protected_heading_or_list_styled_multi_image_paragraph_stays_unsplit(
    tmp_path: Path, monkeypatch
) -> None:
    from docx.enum.style import WD_STYLE_TYPE

    img_a = _make_image(tmp_path / "a.png", (80, 40, 140))
    img_b = _make_image(tmp_path / "b.png", (140, 40, 80))
    doc = Document(str(TEMPLATE))
    body = doc.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    cn_heading = doc.styles.add_style("标题 1", WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("文档标题", style="文档标题")
    h_para = doc.add_paragraph(style=cn_heading)
    h_para.add_run().add_picture(str(img_a), width=Cm(2))
    h_para.add_run().add_picture(str(img_b), width=Cm(2))
    source = tmp_path / "styled-heading-images.docx"
    doc.save(source)

    facts = parse(source)
    ast = build_source_ast(facts)
    standalone = [
        nid for nid, blk in ast["document"]["blocks"].items()
        if blk.get("block_type") == "image" and blk.get("materialization_mode") == "standalone"
    ]
    assert standalone == []

    output, result, _, _ = _convert(tmp_path, source, monkeypatch)
    assert result.exit_code == 0, result.diagnostics
    heading_paragraphs = [
        p for p in Document(output).paragraphs
        if p.style and p.style.name == "Heading 1"
    ]
    assert len(heading_paragraphs) == 1
    assert len(heading_paragraphs[0]._p.findall(f".//{DRAWING_NS}blip")) == 2


def test_appendix_move_revisions_satisfy_revision_applied_with_grouped_captions(
    tmp_path: Path, monkeypatch
) -> None:
    from wxdf.transaction import revision_applied

    img_a = _make_image(tmp_path / "a.png", (10, 150, 90))
    img_b = _make_image(tmp_path / "b.png", (90, 150, 10))
    doc = Document(str(TEMPLATE))
    body = doc.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    doc.add_paragraph("第一章", style="Heading 1")
    doc.add_paragraph("（资料性）\n附录示例", style="附录标题")
    para = doc.add_paragraph()
    para.add_run().add_picture(str(img_a), width=Cm(2))
    para.add_run().add_picture(str(img_b), width=Cm(2))
    doc.add_paragraph("图A.1 甲图")
    doc.add_paragraph("图A.2 乙图")
    source = tmp_path / "appendix-replay.docx"
    doc.save(source)

    _, result, normalized, _ = _convert(tmp_path, source, monkeypatch)
    assert result.exit_code == 0, result.diagnostics
    for decision in result.report["decision_set"]["decisions"]:
        if decision.get("operation") == "move_node":
            revision = {"decision": decision, "target_node_ids": decision["target_node_ids"]}
            assert revision_applied(normalized["source_ast"], revision), decision["candidate_id"]
