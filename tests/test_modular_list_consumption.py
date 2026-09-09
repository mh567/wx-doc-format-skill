from __future__ import annotations

import sys
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_normalization import normalize_document_model_simple
from wxdf.parsers import parse
from wxdf.semantics.list_bridge import apply_list_semantics


def test_docx_source_facts_only_marks_detected_numbering_as_list(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("忽略候选")
    document.add_paragraph("有效列项")
    document.save(source)

    def fake_analysis(document, report):
        report["source_lists"] = {"detected": 1, "ignored": 1}
        return {
            0: {
                "status": "ignored",
                "num_fmt": "decimal",
                "lvl_text": "%1.",
                "start": 1,
                "ilvl": 0,
                "num_id": 1,
                "abstract_num_id": 1,
                "source_list_type": "decimal_paren",
                "list_type": "lower_letter_paren",
                "evidence": ["protected_role"],
            },
            1: {
                "status": "detected",
                "num_fmt": "lowerLetter",
                "lvl_text": "%1)",
                "start": 1,
                "ilvl": 0,
                "num_id": 2,
                "abstract_num_id": 2,
                "source_list_type": "lower_letter_paren",
                "list_type": "lower_letter_paren",
                "evidence": ["valid_numbering_definition"],
            },
        }

    monkeypatch.setattr("list_detector.analyze_docx_lists", fake_analysis)
    facts = parse(source)
    by_position = {
        evidence["source_position"]: evidence
        for evidence in facts["evidence"].values()
        if "source_position" in evidence
    }

    assert by_position[0]["numbering_status"] == "ignored"
    assert "marker_kind" not in by_position[0]
    assert by_position[1]["numbering_status"] == "detected"
    assert by_position[1]["marker_kind"] == "ordered"
    assert by_position[1]["source_list_type"] == "lower_letter_paren"


def test_list_bridge_flattens_ordered_items_and_restarts_after_heading() -> None:
    model = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {"id": "h1", "block_type": "heading", "role": "heading", "level": 1, "text": "第一章", "source": {"source_position": 0}},
                {"id": "a", "block_type": "body", "text": "第一项", "source": {"source_position": 1, "numbering": {"ilvl": 2}}},
                {"id": "b", "block_type": "body", "text": "第二项", "source": {"source_position": 2, "numbering": {"ilvl": 3}}},
                {"id": "h2", "block_type": "heading", "role": "heading", "level": 1, "text": "第二章", "source": {"source_position": 3}},
                {"id": "c", "block_type": "body", "text": "第三项", "source": {"source_position": 4, "numbering": {"ilvl": 1}}},
            ]
        },
    }
    facts = {
        "units": {
            "u-a": {"source_ref": {"source_position": 1}},
            "u-b": {"source_ref": {"source_position": 2}},
            "u-c": {"source_ref": {"source_position": 4}},
        },
        "containers": {},
        "children": {},
        "evidence": {
            "u-a": {"source_position": 1},
            "u-b": {"source_position": 2},
            "u-c": {"source_position": 4},
        },
    }
    semantics = {
        "items": [
            {"item_id": "u-a", "list_id": "l1", "list_kind": "ordered", "parent_item_id": None},
            {"item_id": "u-b", "list_id": "l1", "list_kind": "ordered", "parent_item_id": "u-a"},
            {"item_id": "u-c", "list_id": "l2", "list_kind": "ordered", "parent_item_id": None},
        ],
        "continuations": [],
    }

    applied = apply_list_semantics(model, facts, semantics)
    normalized = normalize_document_model_simple(applied.model, {})
    items = [block for block in normalized["document"]["blocks"] if block["block_type"] == "list_item"]

    assert [item["level"] for item in items] == [0, 0, 0]
    assert [item["list_type"] for item in items] == ["lower_letter_paren"] * 3
    assert [item["restart"] for item in items] == [True, False, True]
    assert items[0]["source"]["numbering"]["ilvl"] == 2
    assert items[0]["native_list_decision"]["level"] == 0
    assert applied.evidence == {
        "item_candidate_count": 3,
        "item_consumed_count": 3,
        "continuation_candidate_count": 0,
        "continuation_consumed_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
