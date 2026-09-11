from __future__ import annotations

import sys
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.parsers import parse
from wxdf.semantics.source_projection import project_source_facts
from wxdf.semantics.source_projection_bridge import apply_source_projection
from model_normalization import normalize_document_model_simple


def test_source_projection_replaces_legacy_content_and_formula_list_bug(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("表1 数据示例", style="Caption")
    appendix = document.add_paragraph()
    appendix.add_run("（资料性）").add_break()
    appendix.add_run("附录示例")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].text = "第一段"
    table.cell(0, 0).add_paragraph("第二段")
    document.add_paragraph("   (3)")
    document.save(source)

    facts = parse(source)
    projection = project_source_facts(facts, {"regions": []})
    legacy_model = {
        "schema_version": "1.0",
        "document": {
            "source": {"path": str(source), "type": "docx"},
            "template": {"name": "WX", "version": "test"},
            "numbering_policy": {"headings": "auto", "lists_restart_scope": "section"},
            "blocks": [
                {"id": "b1", "block_type": "caption", "text": "旧题注", "source": {"source_position": 0}},
                {"id": "b2", "block_type": "appendix", "title": "旧附录", "classification": None, "source": {"source_position": 1}},
                {
                    "id": "b3",
                    "block_type": "table",
                    "table_type": "data",
                    "header_rows": 0,
                    "rows": [[{"text": "旧单元格", "cell_role": "body"}]],
                    "source": {"source_position": 2},
                },
                {
                    "id": "b4",
                    "block_type": "list_item",
                    "text": "",
                    "level": 0,
                    "list_type": "decimal_paren",
                    "source": {"source_position": 3},
                },
            ],
        },
    }

    applied = apply_source_projection(legacy_model, projection)

    blocks = applied.model["document"]["blocks"]
    assert blocks[0]["text"] == "数据示例"
    assert blocks[1]["title"] == "附录示例"
    assert blocks[1]["classification"] == "资料性"
    assert blocks[2]["rows"][0][0]["text"] == "第一段\n第二段"
    assert blocks[3]["block_type"] == "body"
    assert blocks[3]["role"] == "formula"
    assert blocks[3]["text"] == "(3)"
    assert blocks[3]["inline_runs"] == [{"text": "(3)"}]
    normalized = normalize_document_model_simple(applied.model, {})
    normalized_formula = next(block for block in normalized["document"]["blocks"] if block["id"] == "b4")
    assert normalized_formula["block_type"] == "body"
    assert normalized_formula["role"] == "formula"
    assert applied.evidence == {
        "paragraph_candidate_count": 3,
        "paragraph_consumed_count": 3,
        "table_candidate_count": 1,
        "table_consumed_count": 1,
        "formula_retyped_count": 1,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }


def test_source_projection_matches_markdown_paragraphs_by_line_location(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("第一段\n\n第二段\n", encoding="utf-8")
    projection = project_source_facts(parse(source), {"regions": []})
    legacy_model = {
        "schema_version": "1.0",
        "document": {
            "source": {"path": str(source), "type": "markdown"},
            "template": {"name": "WX", "version": "test"},
            "numbering_policy": {"headings": "auto", "lists_restart_scope": "section"},
            "blocks": [
                {"id": "b1", "block_type": "body", "text": "x", "source": {"line_start": 1}},
                {"id": "b2", "block_type": "body", "text": "y", "source": {"line_start": 3}},
            ],
        },
    }

    applied = apply_source_projection(legacy_model, projection)

    assert [block["text"] for block in applied.model["document"]["blocks"]] == ["第一段", "第二段"]
    assert applied.evidence["unmatched_count"] == 0
    assert applied.evidence["ambiguous_count"] == 0


def test_source_projection_preserves_markdown_inline_marks_and_links(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("**粗体**与[链接](https://example.com)\n", encoding="utf-8")
    projection = project_source_facts(parse(source), {"regions": []})
    legacy_model = {
        "schema_version": "1.0",
        "document": {
            "source": {"path": str(source), "type": "markdown"},
            "template": {"name": "WX", "version": "test"},
            "numbering_policy": {"headings": "auto", "lists_restart_scope": "section"},
            "blocks": [
                {"id": "b1", "block_type": "body", "text": "stale", "inline_runs": [], "source": {"line_start": 1}},
            ],
        },
    }

    applied = apply_source_projection(legacy_model, projection)

    assert applied.model["document"]["blocks"][0]["inline_runs"] == [
        {"text": "粗体", "marks": ["strong"]},
        {"text": "与"},
        {"text": "链接", "href": "https://example.com"},
    ]
