"""Fidelity fixes for the first real-template conversion round.

The rules under test keep what the template cannot rebuild (formulas, fields,
bookmarks, the source numbering of an appendix caption), let evidence decide a
role (a bare note marker against a heading style, a list level whose marker is
its level text) and keep the template's own table of contents the only one that
renders.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import qn

from note_semantics import is_heading_style, source_note_role
from wxdf.normalization import _body_outline_overrides, _drop_excluded_blocks, _paragraph_presentation, normalize
from wxdf.parsers import parse
from wxdf.rendering import _write_caption
from wxdf.semantics.regions import classify_regions, resolve_source_regions
from wxdf.semantics.lists import analyze_lists
from wxdf.source_store import SourceStore
from wxdf.template import load_template_bundle


TEMPLATE = ROOT / "assets" / "wx_template.docx"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"

_MATH_XML = (
    f'<m:oMathPara xmlns:m="{M[1:-1]}" xmlns:w="{W[1:-1]}">'
    '<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></m:oMathPara>'
)
_FIELD_XML = (
    f'<w:fldSimple xmlns:w="{W[1:-1]}" w:instr="SEQ 公式 \\* ARABIC">'
    "<w:r><w:t>3</w:t></w:r></w:fldSimple>"
)


def _ref(index: int) -> dict:
    return {
        "source_id": "docx:fixture",
        "part": "word/document.xml",
        "content_sha256": "sha256:" + "b" * 64,
        "node_path": ["body", index, "w:p"],
        "parser_version": "1.1.0",
    }


def _paragraph(node_id: str, text: str, **extra) -> dict:
    return {
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
        "source_ref": _ref(0),
        **extra,
    }


def _heading(node_id: str, level: int) -> dict:
    return _paragraph(node_id, f"标题{level}", role="heading", level=level)


def _paragraph_units(facts: dict) -> list[dict]:
    return [
        unit
        for unit in facts["units"].values()
        if unit.get("kind") == "paragraph" and unit.get("inline_content")
    ]


def _kinds(unit: dict) -> list[str]:
    return [str(item.get("kind")) for item in unit.get("inline_content") or []]


def test_docx_formula_field_and_bookmark_are_retained_by_source_reference(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    paragraph = document.add_paragraph("前")
    paragraph._p.append(parse_xml(_MATH_XML))
    paragraph._p.append(parse_xml(_FIELD_XML))
    bookmark = parse_xml(f'<w:bookmarkStart xmlns:w="{W[1:-1]}" w:id="7" w:name="_Ref1"/>')
    paragraph._p.append(bookmark)
    document.save(source)

    store = SourceStore()
    store.open_source(source)
    facts = parse(source, {"source_store": store})
    units = _paragraph_units(facts)
    kinds = [kind for unit in units for kind in _kinds(unit)]

    assert "math" in kinds
    assert "field" in kinds
    assert "bookmark" in kinds
    items = {str(item.get("kind")): item for unit in units for item in unit.get("inline_content") or []}
    assert items["field"]["instruction"] == "SEQ 公式 \\* ARABIC"
    assert items["field"]["cached_result"] == "3"
    assert items["bookmark"]["name"] == "_Ref1"
    # The payload is retained under a part the store can resolve.
    resource = facts["resources"][items["math"]["resource_id"]]
    assert b"oMath" in store.resolve(dict(resource["source_ref"]))
    store.close()


def test_retained_field_projects_the_text_it_last_showed(tmp_path: Path) -> None:
    from wxdf.model import inline_text_projection

    content = [
        {"kind": "text", "text": "见图"},
        {
            "kind": "field",
            "source_ref": {
                "source_id": "docx:x",
                "part": "word/document.xml",
                "content_sha256": "sha256:" + "a" * 64,
                "node_path": ["body", 0, "w:p", 1],
            },
            "instruction": "REF _Ref1 \\h",
            "cached_result": "1",
        },
        {"kind": "text", "text": "。"},
    ]

    assert inline_text_projection(content) == "见图1。"


def test_source_toc_control_is_one_excluded_region(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("标题")
    control = parse_xml(
        f'<w:sdt xmlns:w="{W[1:-1]}"><w:sdtContent>'
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-3" </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        "<w:r><w:t>目录内容</w:t></w:r>"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        "<w:p><w:r><w:t>1 章</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
    )
    document.element.body.insert(0, control)
    document.save(source)

    facts = parse(source)
    marked = [node_id for node_id, evidence in facts["evidence"].items() if evidence.get("source_toc")]
    assert len(marked) == 2
    regions = classify_regions(facts)
    assert sum(1 for region in regions["regions"] if region.get("reason") == "source_toc_content") == 2

    # The normalized view renders the template's control only.
    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {node_id: {"block_type": "paragraph", "role": "body", "inline_content": []} for node_id in marked},
            "containers": {"root": {"kind": "document"}},
            "children": {"root": list(marked)},
            "associations": [],
            "resources": {},
        },
    }
    overlay = {node_id: {"semantic_scope": "excluded"} for node_id in marked}
    _drop_excluded_blocks(ast, overlay)
    assert ast["document"]["children"]["root"] == []
    assert ast["document"]["blocks"] == {}


def test_a_bare_note_marker_under_a_heading_style_stays_a_heading() -> None:
    assert is_heading_style("Heading 3")
    assert is_heading_style("标题2")
    assert not is_heading_style("3.1注-无编号注")

    # A heading carrying the note marker without an ordinal keeps the heading role.
    assert source_note_role("Heading 3", None, "注") is None
    assert source_note_role("Heading 3", None, "注：") is None
    assert source_note_role("Heading 3", None, "注 说明") is None
    # A note carrying an ordinal and content stays a note, whatever style it has.
    assert source_note_role("Heading 1", None, "注2 无冒号说明") == "numbered_note"
    assert source_note_role(None, None, "注1 无冒号说明") == "numbered_note"
    # The note styles still decide on their own.
    assert source_note_role("3.1注-无编号注", None, "任意文字") == "note"


def test_marker_only_unordered_levels_are_list_items(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document(TEMPLATE)
    body = document.element.body
    for element in list(body):
        if element.tag != qn("w:sectPr"):
            body.remove(element)
    for style, text in (
        ("1.2一级列项-无编号", "一级无编号列项。"),
        ("2.2二级列项-无编号", "二级无编号列项。"),
    ):
        paragraph = document.add_paragraph(text)
        paragraph.style = document.styles[style]
    document.save(source)

    facts = parse(source)
    result = analyze_lists(facts)
    items = [
        (str(item["item_id"]), item.get("list_kind"))
        for item in result["items"]
    ]

    assert [kind for _, kind in items] == ["unordered", "unordered"]
    levels = [facts["evidence"][item_id].get("marker_kind") for item_id, _ in items]
    assert levels == ["unordered", "unordered"]
    assert [facts["evidence"][item_id].get("source_list_type") for item_id, _ in items] == ["dash", "bullet_dot"]
    assert not result.get("downgrades")


def test_appendix_caption_keeps_its_source_number() -> None:
    paragraph = Document().add_paragraph()
    authored = [
        {"kind": "text", "text": "表A.1 小四黑体不加粗", "marks": []},
    ]
    block = {"role": "caption", "caption_type": "table", "appendix_id": "A", "source_number_preserved": True, "inline_content": authored}

    text = _write_caption(paragraph, block, {})

    assert text == "表A.1 小四黑体不加粗"
    assert paragraph.text == "表A.1 小四黑体不加粗"
    assert list(paragraph._p.iter(qn("w:fldSimple"))) == []


def test_body_caption_still_owns_its_sequence_field() -> None:
    paragraph = Document().add_paragraph()
    block = {"role": "caption", "caption_type": "table", "inline_content": [{"kind": "text", "text": "表1 说明", "marks": []}]}

    text = _write_caption(paragraph, block, {})

    fields = [element.get(qn("w:instr")) for element in paragraph._p.iter(qn("w:fldSimple"))]
    assert fields == ["SEQ Table \\* ARABIC"]
    assert text == "表 1 说明"


def test_caption_copies_the_anchors_it_carries() -> None:
    paragraph = Document().add_paragraph()
    source_ref = _ref(3)
    block = {
        "role": "caption",
        "caption_type": "table",
        "inline_content": [
            {"kind": "bookmark", "role": "start", "name": "_Ref1", "resource_id": "bookmark", "source_ref": source_ref},
            {"kind": "text", "text": "表1 说明", "marks": []},
        ],
    }
    payloads = {
        "bookmark": f'<w:bookmarkStart xmlns:w="{W[1:-1]}" w:id="1" w:name="_Ref1"/>'.encode("utf-8"),
    }

    _write_caption(paragraph, block, payloads)

    # The caption rebuilds its number, and copies the anchor a cross reference
    # targets so the reference still resolves after the fields refresh.
    assert [element.get(qn("w:name")) for element in paragraph._p.iter(qn("w:bookmarkStart"))] == ["_Ref1"]
    assert [element.get(qn("w:instr")) for element in paragraph._p.iter(qn("w:fldSimple"))] == ["SEQ Table \\* ARABIC"]


def test_paragraph_presentation_carries_only_the_alignment_a_style_cannot() -> None:
    ast = {
        "document": {
            "blocks": {
                "image": {"block_type": "image", "resource_id": "r"},
                "formula_number": {
                    "block_type": "paragraph",
                    "role": "body",
                    "semantic_role": "formula_number",
                    "inline_content": [{"kind": "text", "text": "（3）", "marks": []}],
                    "source_paragraph_format": {"alignment": "right"},
                },
                "centered_body": {
                    "block_type": "paragraph",
                    "role": "body",
                    "semantic_role": "paragraph",
                    "inline_content": [{"kind": "text", "text": "正文", "marks": []}],
                    "source_paragraph_format": {"alignment": "center"},
                },
                "heading": {
                    "block_type": "paragraph",
                    "role": "heading",
                    "level": 2,
                    "inline_content": [{"kind": "text", "text": "标题", "marks": []}],
                    "source_paragraph_format": {"alignment": "right"},
                },
            }
        }
    }

    presentation = _paragraph_presentation(ast)

    # Only the image paragraph and the formula number own an alignment: any other
    # alignment belongs to the template style.
    assert presentation == {
        "image": {"alignment": "center"},
        "formula_number": {"alignment": "right"},
    }


def test_table_number_column_is_the_table_own_numbering() -> None:
    from wxdf.semantics.lists import table_numbering_role
    from template_profile import load_template_profile

    facts = {
        "units": {"p1": {"kind": "paragraph"}},
        "containers": {
            "cell0": {"kind": "table_cell", "col": 0},
            "cell1": {"kind": "table_cell", "col": 1},
            "row": {"kind": "table_row"},
            "table": {"kind": "table", "table_semantics": {"table_type": "data"}},
            "sample": {"kind": "table", "table_semantics": {"table_type": "code_sample"}},
        },
        "children": {"cell0": ["p1"], "cell1": ["p1"], "row": ["cell0"], "table": ["row"]},
    }

    # A plain table's leftmost cell is its numbering column.
    assert table_numbering_role(facts, "cell0", source_list_type="decimal_paren", text="") == "table_number"
    # Another column, a cell with text, and a case table keep the list policy.
    assert table_numbering_role(facts, "cell1", source_list_type="decimal_paren", text="") is None
    assert table_numbering_role(facts, "cell0", source_list_type="decimal_paren", text="注释") is None
    assert table_numbering_role(facts, "cell0", source_list_type="lower_letter_paren", text="") is None
    facts["containers"]["table"]["table_semantics"] = {"table_type": "code_sample"}
    assert table_numbering_role(facts, "cell0", source_list_type="decimal_paren", text="") is None

    # The template publishes the plain decimal instance the column renders with.
    numbering_ids = load_template_profile(TEMPLATE)["numbering_ids"]
    assert isinstance(numbering_ids.get("table_number"), int)
    assert numbering_ids["table_number"] not in {
        numbering_ids.get("list_letter"),
        numbering_ids.get("list_dash"),
        numbering_ids.get("list_decimal"),
        numbering_ids.get("list_bullet"),
        numbering_ids.get("heading"),
    }


def test_an_item_with_its_own_empty_paragraph_generates_no_anchor() -> None:
    bundle = load_template_bundle(TEMPLATE)
    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "empty": {"block_type": "paragraph", "role": "body", "inline_content": [], "source_ref": _ref(0)},
                "text": _paragraph("text", "正文"),
            },
            "containers": {
                "root": {"kind": "document"},
                "list": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s"},
                "i1": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "s"},
                "i2": {"kind": "list_item", "source_marker": "2.", "source_ordinal": 2, "source_sequence_id": "s"},
            },
            "children": {"root": ["list"], "list": ["i1", "i2"], "i1": ["empty"], "i2": ["text"]},
            "associations": [],
            "resources": {},
        },
    }

    normalized = normalize(ast, bundle, strategy="wx-default-v2")
    nodes = normalized["presentation"]["nodes"]

    # The empty paragraph is the marker paragraph, so nothing is added to the item.
    assert nodes["i1"]["marker_anchor"] == "empty"
    assert nodes["i1"]["generated_anchor"] is False
    assert nodes["i2"]["marker_anchor"] == "text"
    assert normalized["generated_nodes"] == []


def test_normalization_prunes_excluded_regions_and_maps_the_outline(tmp_path: Path) -> None:
    bundle = load_template_bundle(TEMPLATE)
    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "dropped": _paragraph("dropped", "被替换的目录段落"),
                "h1": _heading("h1", 1),
                "h5": _heading("h5", 5),
            },
            "containers": {"root": {"kind": "document"}},
            "children": {"root": ["dropped", "h1", "h5"]},
            "associations": [],
            "resources": {},
        },
    }

    normalized = normalize(
        ast,
        bundle,
        strategy="wx-default-v2",
        overlay={"dropped": {"semantic_scope": "excluded"}},
    )

    assert normalized["source_ast"]["document"]["children"]["root"] == ["h1", "h5"]
    assert "dropped" not in normalized["source_ast"]["document"]["blocks"]
    assert normalized["presentation"]["outline"] == {"h5": {"outline_level": 2, "source_level": 5}}
