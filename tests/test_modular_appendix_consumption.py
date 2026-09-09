from __future__ import annotations

import sys
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.parsers import parse
from wxdf.semantics.appendices import collect_source_appendix_semantics
from wxdf.semantics.appendix_bridge import apply_appendix_semantics


def test_source_appendix_semantics_reject_nested_marker_and_build_relative_levels(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_heading("附录", level=3)
    document.add_paragraph("正文继续")
    document.add_heading("附录 A", level=2)
    document.add_heading("适用范围", level=2)
    document.add_heading("详细要求", level=3)
    document.add_paragraph("附录正文")
    document.save(source)

    facts = parse(source)
    first_id = facts["children"]["root"][0]
    facts["evidence"][first_id].update({"num_fmt": "decimal", "lvl_text": "%1.%2.%3 "})
    semantics = collect_source_appendix_semantics(facts)

    assert len(semantics["candidates"]) == 2
    assert semantics["candidates"][0]["accepted"] is False
    assert semantics["candidates"][0]["negative_evidence"] == ["nested_main_numbered_heading"]
    assert semantics["candidates"][1]["accepted"] is True
    appendix = semantics["ranges"][0]
    assert appendix["appendix_id"] == "A"
    assert appendix["page_break_before"] is True
    assert [item["relative_level"] for item in appendix["heading_decisions"]] == [1, 2]


def test_appendix_bridge_applies_title_scope_headings_and_single_page_boundary() -> None:
    model = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {"id": "title", "block_type": "heading", "level": 2, "text": "附录 A", "source": {"source_position": 2}},
                {"id": "h1", "block_type": "heading", "level": 2, "text": "适用范围", "source": {"source_position": 3}},
                {"id": "h2", "block_type": "heading", "level": 3, "text": "详细要求", "source": {"source_position": 4}},
                {"id": "body", "block_type": "body", "text": "附录正文", "source": {"source_position": 5}},
            ]
        },
    }
    semantics = {
        "candidates": [],
        "ranges": [
            {
                "appendix_id": "A",
                "classification": None,
                "title": "",
                "title_lines": [""],
                "soft_break_count": 0,
                "title_location": {"kind": "source_position", "value": 2},
                "start_location": {"kind": "source_position", "value": 2},
                "end_location": {"kind": "source_position", "value": 5},
                "page_break_before": True,
                "confidence": 1.0,
                "evidence": ["explicit_appendix_title"],
                "negative_evidence": [],
                "heading_decisions": [
                    {"location": {"kind": "source_position", "value": 3}, "relative_level": 1, "text": "适用范围"},
                    {"location": {"kind": "source_position", "value": 4}, "relative_level": 2, "text": "详细要求"},
                ],
            }
        ],
        "issues": [],
    }

    applied = apply_appendix_semantics(model, semantics)
    title, h1, h2, body = applied.model["document"]["blocks"]

    assert title["block_type"] == "appendix"
    assert title["layout"] == {"page_break_before": True}
    assert title["appendix_id"] == "A"
    assert [(h1["role"], h1["level"]), (h2["role"], h2["level"])] == [
        ("appendix_heading", 1),
        ("appendix_heading", 2),
    ]
    assert all(block["context"] == {"scope": "appendix", "appendix_id": "A"} for block in (title, h1, h2, body))
    assert applied.evidence == {
        "range_candidate_count": 1,
        "range_consumed_count": 1,
        "heading_candidate_count": 2,
        "heading_consumed_count": 2,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }
