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
from wxdf.normalization import (
    _IMAGE_PARAGRAPH_INDENT,
    _body_outline_overrides,
    _drop_excluded_blocks,
    _paragraph_presentation,
    normalize,
)
from wxdf.parsers import parse
from wxdf.rendering import _write_caption
from wxdf.semantics.regions import classify_regions, resolve_source_regions
from wxdf.semantics.structure import _generated_title_candidate, collect_structure_candidates, generated_title
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

    # Without a template contract the WX label is the fallback identifier.
    instructions = [element.text for element in paragraph._p.iter(qn("w:instrText"))]
    assert instructions == [" SEQ 表 \\* ARABIC "]
    assert text == "表1 说明"


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
    assert [element.text for element in paragraph._p.iter(qn("w:instrText"))] == [" SEQ 表 \\* ARABIC "]


def test_paragraph_presentation_carries_only_the_alignment_a_style_cannot() -> None:
    ast = {
        "document": {
            "blocks": {
                "image": {"block_type": "image", "resource_id": "r"},
                "image_carrier": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "image", "resource_id": "r", "alt_text": ""}],
                },
                "image_with_text": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [
                        {"kind": "text", "text": "见图", "marks": []},
                        {"kind": "image", "resource_id": "r", "alt_text": ""},
                    ],
                },
                "heading_with_image": {
                    "block_type": "paragraph",
                    "role": "heading",
                    "level": 2,
                    "inline_content": [{"kind": "image", "resource_id": "r", "alt_text": ""}],
                },
                "layout_table": {"block_type": "table", "table_type": "layout"},
                "layout_row": {"kind": "table_row"},
                "layout_cell": {"kind": "table_cell"},
                "layout_cell_image": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "image", "resource_id": "r", "alt_text": ""}],
                },
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
            },
            "containers": {
                "layout_row": {"kind": "table_row"},
                "layout_cell": {"kind": "table_cell"},
            },
            "children": {
                "layout_table": ["layout_row"],
                "layout_row": ["layout_cell"],
                "layout_cell": ["layout_cell_image"],
            },
        }
    }

    presentation = _paragraph_presentation(ast)

    # Only the image node, the paragraph whose content is an inline image and the
    # formula number own a presentation: a paragraph that mixes text with an image,
    # a heading and a table cell keep the template style or the table contract,
    # and any other alignment belongs to the template style.  An image paragraph
    # also owns the zeroed indent that keeps the template ``Normal`` first-line
    # indent from pushing the centred image off centre (decision 2).
    assert presentation == {
        "image": {"alignment": "center", "indent": dict(_IMAGE_PARAGRAPH_INDENT)},
        "image_carrier": {"alignment": "center", "indent": dict(_IMAGE_PARAGRAPH_INDENT)},
        "formula_number": {"alignment": "right"},
    }


def test_an_inline_image_paragraph_is_centred_without_a_style(tmp_path: Path) -> None:
    """The paragraph that draws an inline image keeps the template's image form.

    The template's own image paragraphs carry no style and ``jc=center``, so the
    delivered document reproduces that: the paragraph that draws the image has no
    ``pStyle`` and its only paragraph property is the centred alignment.  Reading
    the alignment off the materialized image node instead would leave the
    paragraph that actually draws the image uncentred, because the renderer skips
    that node while the token stays in the paragraph.
    """

    from wxdf.rendering import render
    from wxdf.semantics.source_ast import build_source_ast

    source = tmp_path / "image.docx"
    image = tmp_path / "pixel.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db10000000049454e44ae426082"
        )
    )
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run().add_picture(str(image))
    document.save(source)

    store = SourceStore()
    store.open_source(source)
    facts = parse(source, {"source_store": store})
    ast = build_source_ast(facts)
    carrier_id = next(
        node_id
        for node_id, block in ast["document"]["blocks"].items()
        if block.get("block_type") == "paragraph"
        and any(item.get("kind") == "image" for item in block.get("inline_content", []))
    )
    assert any(
        block.get("block_type") == "image" and block.get("materialization_mode") != "standalone"
        for block in ast["document"]["blocks"].values()
    )

    template_bundle = load_template_bundle(TEMPLATE)
    normalized = normalize(ast, template_bundle.profile, strategy="legacy")
    output = tmp_path / "image-output.docx"
    result = render(
        normalized,
        output,
        template_bundle=template_bundle,
        resources={
            block["resource_id"]: store.resolve(block["source_ref"])
            for block in ast["document"]["blocks"].values()
            if block.get("block_type") == "image"
        },
    )

    rendered = Document(output)
    image_paragraph = next(
        paragraph
        for paragraph in rendered.paragraphs
        if any(element.tag.rsplit("}", 1)[-1] == "blip" for element in paragraph._p.iter())
    )
    properties = image_paragraph._p.find(qn("w:pPr"))
    assert properties is not None
    # No style and no other property: the centred alignment and the zeroed indent
    # are the whole of the image paragraph's presentation, which is decision 2's
    # contract.  A ``pStyle`` or any other paragraph property would show up in
    # this list.
    assert [element.tag.rsplit("}", 1)[-1] for element in properties] == ["jc", "ind"]
    assert [element.get(qn("w:val")) for element in properties.iter(qn("w:jc"))] == ["center"]
    indent = properties.find(qn("w:ind"))
    assert {name.rsplit("}", 1)[-1]: value for name, value in indent.attrib.items()} == {
        "firstLine": "0",
        "firstLineChars": "0",
        "left": "0",
        "leftChars": "0",
    }
    assert carrier_id in result["render_map"]
    assert len(result["render_map"][carrier_id]["image_resources"]) == 1


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


def test_markdown_soft_break_is_a_space_and_markup_is_stripped(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(
        "# 标题\n\n第一行\n第二行\n\n<b>粗体</b> 说明\n\n`<i>` 是代码，a < b 是比较\n",
        encoding="utf-8",
    )

    facts = parse(source)
    units = [unit for unit in _paragraph_units(facts)]
    texts = [
        "".join(
            str(item.get("text") or "")
            for item in unit.get("inline_content") or []
            if item.get("kind") == "text"
        )
        for unit in units
    ]

    # A soft break is a space: only a hard break becomes a line break.
    assert "第一行 第二行" in texts
    # HTML markup has no representation, so its readable text is kept without it.
    assert "粗体 说明" in texts
    assert "html_inline" in [item["code"] for item in facts["diagnostics"]]
    # A code span shows the markup on purpose, and a comparison is prose.
    assert "<i> 是代码，a < b 是比较" in texts
    assert not any(
        item.get("kind") == "break"
        for unit in units
        for item in unit.get("inline_content") or []
    )


def test_markdown_hard_break_stays_a_line_break(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n上一行\\\n下一行\n", encoding="utf-8")

    facts = parse(source)
    breaks = [
        item
        for unit in _paragraph_units(facts)
        for item in unit.get("inline_content") or []
        if item.get("kind") == "break"
    ]

    assert len(breaks) == 1


def test_a_markdown_source_without_a_leading_h1_gets_a_filename_title(tmp_path: Path) -> None:
    source = tmp_path / "无一级标题的方案.md"
    source.write_text("## 第一节\n\n正文。\n", encoding="utf-8")
    bundle = load_template_bundle(TEMPLATE)

    facts = parse(source)
    candidates = collect_structure_candidates(facts, {"regions": []})
    generated = [
        candidate
        for candidate in candidates
        if candidate.get("candidate_id") == "structure-generated-title"
    ]

    assert len(generated) == 1
    node = generated[0]["proposal"]["value"]["node"]
    assert node["role"] == "title"
    assert node["inline_content"][0]["text"] == "无一级标题的方案"
    assert node["generated_by"] == "structure_semantics.filename_title"
    assert generated_title(candidates) == "无一级标题的方案"

    # A source that already carries a leading H1 keeps its own title.
    with_h1 = tmp_path / "有标题.md"
    with_h1.write_text("# 自带标题\n\n正文。\n", encoding="utf-8")
    titles = [
        candidate
        for candidate in collect_structure_candidates(parse(with_h1), {"regions": []})
        if candidate.get("proposal", {}).get("value", {}).get("role") == "title"
    ]
    assert all(candidate["candidate_id"] != "structure-generated-title" for candidate in titles)
    assert generated_title(collect_structure_candidates(parse(with_h1), {"regions": []})) is None
    # A source with nothing but a level-2 heading still needs the document title.
    empty = tmp_path / "空文件.md"
    empty.write_text("## 只有二级标题\n", encoding="utf-8")
    assert generated_title(collect_structure_candidates(parse(empty), {"regions": []})) == "空文件"
    # A source that does not name its file yields no title rather than an invented one.
    assert _generated_title_candidate({"source": {"name": ""}}) is None
    assert bundle is not None


def test_docx_source_never_gets_a_filename_title(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("正文。")
    document.save(source)

    candidates = collect_structure_candidates(parse(source), {"regions": []})

    assert all(candidate.get("candidate_id") != "structure-generated-title" for candidate in candidates)


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
