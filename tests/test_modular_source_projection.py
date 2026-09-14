from __future__ import annotations

import sys
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.model import inline_text_projection
from wxdf.parsers import parse
from wxdf.semantics.parser_equivalence import compare_parser_equivalence
from wxdf.semantics.source_ast import build_source_ast
from wxdf.semantics.source_projection import project_source_facts


def _all_source_regions() -> dict:
    return {"regions": []}


def test_source_projection_canonicalizes_authored_semantics_and_table_cells(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("表1 数据示例", style="Caption")
    appendix = document.add_paragraph()
    appendix.add_run("（资料性）").add_break()
    appendix.add_run("附录示例")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].text = "第一段"
    table.cell(0, 0).add_paragraph("第二段")
    document.save(source)

    projection = project_source_facts(parse(source), _all_source_regions())

    assert projection["text_inventory"] == ["数据示例", "附录示例", "第一段\n第二段"]
    assert projection["captions"] == [
        {"caption_type": "table", "label": "表", "text": "数据示例", "source_position": 0}
    ]
    assert projection["appendices"] == [
        {"appendix_id": "A", "classification": "资料性", "title": "附录示例", "source_position": 1}
    ]
    assert projection["table_shapes"] == [[1]]


def test_source_projection_preserves_standalone_appendix_section_heading(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("附录", style="Heading 3")
    document.add_paragraph("附录的格式说明")
    document.save(source)

    projection = project_source_facts(parse(source), _all_source_regions())

    assert projection["text_inventory"] == ["附录", "附录的格式说明"]
    assert projection["appendices"] == []


def test_parser_equivalence_compares_canonical_semantics_instead_of_storage_shape(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("表1 数据示例", style="Caption")
    appendix = document.add_paragraph()
    appendix.add_run("（资料性）").add_break()
    appendix.add_run("附录示例")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].text = "第一段"
    table.cell(0, 0).add_paragraph("第二段")
    document.save(source)
    facts = parse(source)
    legacy_ast = build_source_ast(facts)

    root_children = legacy_ast["document"]["children"]["root"]
    caption_id, appendix_id, table_id = root_children
    legacy_ast["document"]["blocks"][caption_id]["inline_content"] = [
        {"kind": "text", "text": "数据示例", "marks": []}
    ]
    legacy_ast["document"]["blocks"][appendix_id]["inline_content"] = [
        {"kind": "text", "text": "附录示例", "marks": []}
    ]
    row_id = legacy_ast["document"]["children"][table_id][0]
    cell_id = legacy_ast["document"]["children"][row_id][0]
    first_id, second_id = legacy_ast["document"]["children"][cell_id]
    legacy_ast["document"]["blocks"][first_id]["inline_content"] = [
        {"kind": "text", "text": "第一段\n第二段", "marks": []}
    ]
    legacy_ast["document"]["children"][cell_id] = [first_id]
    del legacy_ast["document"]["blocks"][second_id]

    audit = compare_parser_equivalence(facts, _all_source_regions(), legacy_ast)

    assert audit["status"] == "equivalent"
    assert audit["issues"] == []
    assert audit["domains"]["text"] == {
        "status": "equivalent",
        "native_count": 3,
        "legacy_count": 3,
    }


def test_parser_text_inventory_status_uses_the_same_multiset_contract_as_issues(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("第一段\n\n第二段\n", encoding="utf-8")
    facts = parse(source)
    legacy_ast = build_source_ast(facts)
    legacy_ast["document"]["children"]["root"].reverse()

    audit = compare_parser_equivalence(facts, _all_source_regions(), legacy_ast)

    assert audit["status"] == "equivalent"
    assert audit["domains"]["text"]["status"] == "equivalent"
    assert audit["issues"] == []


def test_parser_equivalence_approves_legacy_empty_list_created_from_formula_number(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("(3)")
    document.save(source)
    facts = parse(source)
    legacy_ast = build_source_ast(facts)
    paragraph_id = legacy_ast["document"]["children"]["root"][0]
    source_position = facts["evidence"][paragraph_id]["source_position"]
    assert inline_text_projection(legacy_ast["document"]["blocks"][paragraph_id]["inline_content"]) == "(3)"

    legacy_ast["document"]["blocks"][paragraph_id]["inline_content"] = []
    legacy_ast["document"]["containers"].update({
        "legacy-list": {
            "kind": "list",
            "list_kind": "ordered",
            "source_start": 1,
            "source_sequence_id": "legacy-sequence",
        },
        "legacy-item": {
            "kind": "list_item",
            "source_marker": "3.",
            "source_ordinal": 3,
            "source_sequence_id": "legacy-sequence",
        },
    })
    legacy_ast["document"]["children"].update({
        "root": ["legacy-list"],
        "legacy-list": ["legacy-item"],
        "legacy-item": [paragraph_id],
    })
    legacy_ast["document"]["blocks"][paragraph_id]["source_ref"]["span"] = {
        "start": source_position,
        "end": source_position,
        "unit": "unicode_code_point",
    }
    legacy_ast["document"]["blocks"][paragraph_id]["source_ref"].pop("node_path", None)

    audit = compare_parser_equivalence(facts, _all_source_regions(), legacy_ast)

    assert audit["status"] == "equivalent"
    assert audit["passed"] is True
    assert audit["domains"]["lists"]["native_count"] == 0
    assert audit["domains"]["lists"]["legacy_count"] == 0
    assert audit["approved_differences"] == [
        {
            "type": "legacy_formula_number_empty_list_item",
            "source_position": source_position,
            "source_text": "(3)",
            "legacy_item_id": "legacy-item",
        }
    ]
