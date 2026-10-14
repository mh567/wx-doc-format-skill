"""A manually numbered heading keeps its heading and loses the marker.

Ruling of 2026-09-16 (option A, docs/execution-v7): a paragraph that already
carries a heading style or a Markdown heading marker keeps its heading role, and
the manual number in its text is removed, so the delivered heading text carries
no manual number (AGENTS.md invariant) while an explicit heading outweighs a
text heuristic (decision 10).

The marker vocabulary has one owner, ``text_utils.strip_heading_marker_prefix``,
and ``lists.strip_visible_list_marker`` stays the list-side entry point that
consumes its own visible marker.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from docx import Document

from wxdf.kernel import ConversionKernel
from wxdf.model import inline_text_projection
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.structure import collect_structure_candidates


TEMPLATE = ROOT / "assets" / "wx_template.docx"


def _candidates(tmp_path: Path, spec: list[tuple[str, str | None]]) -> tuple[dict, list[dict]]:
    """Return the SourceFacts and the protected structure candidates of a DOCX."""

    document = Document()
    for text, style in spec:
        paragraph = document.add_paragraph(text)
        if style:
            paragraph.style = document.styles[style]
    source = tmp_path / "heading-marker.docx"
    document.save(str(source))
    facts = parse(source)
    return facts, collect_structure_candidates(facts, None, protect_semantics=True)


def _text_by_node(facts: dict) -> dict[str, str]:
    return {
        str(node_id): inline_text_projection(list(unit.get("inline_content") or []))
        for node_id, unit in facts["units"].items()
    }


def _role_candidates(candidates: list[dict]) -> list[dict]:
    return [
        candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "update_role"
    ]


def _content_candidates(candidates: list[dict]) -> list[dict]:
    return [
        candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "update_node_content"
    ]


def _by_expected_text(facts: dict, candidates: list[dict]) -> dict[str, dict]:
    """Index the role candidates by the source text of the node they target."""

    text_by_node = _text_by_node(facts)
    return {
        text_by_node.get(str(candidate["target_node_ids"][0]), ""): candidate
        for candidate in _role_candidates(candidates)
    }


def _convert_docx(tmp_path: Path, spec: list[tuple[str, str | None]]) -> tuple[Path, object]:
    document = Document()
    for text, style in spec:
        paragraph = document.add_paragraph(text)
        if style:
            paragraph.style = document.styles[style]
    source = tmp_path / "input.docx"
    document.save(str(source))
    output = tmp_path / "output.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    return output, ConversionKernel().convert(request)


def _convert_markdown(tmp_path: Path, text: str) -> tuple[Path, object]:
    source = tmp_path / "input.md"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "output.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    return output, ConversionKernel().convert(request)


def _paragraph_rows(path: Path) -> list[tuple[str, str]]:
    """Return ``(style name, text)`` for every paragraph of a saved DOCX."""

    document = Document(str(path))
    return [(paragraph.style.name or "", paragraph.text) for paragraph in document.paragraphs]


# ---------------------------------------------------------------------------
# Candidate level
# ---------------------------------------------------------------------------

def test_a_marked_heading_keeps_the_style_role_and_level(tmp_path: Path) -> None:
    facts, candidates = _candidates(
        tmp_path,
        [
            ("第一章 概述", "Heading 1"),
            ("1.2 需求分析", "Heading 2"),
            ("第一节 背景", "Heading 2"),
        ],
    )

    roles = _by_expected_text(facts, candidates)

    assert {
        text: (candidate["proposal"]["value"].get("role"), candidate["proposal"]["value"].get("level"))
        for text, candidate in roles.items()
    } == {
        "第一章 概述": ("heading", 1),
        "1.2 需求分析": ("heading", 2),
        "第一节 背景": ("heading", 2),
    }


def test_the_marker_is_removed_from_the_graded_heading_text(tmp_path: Path) -> None:
    facts, candidates = _candidates(
        tmp_path,
        [("第一章 概述", "Heading 1"), ("1.2 需求分析", "Heading 2")],
    )

    written = {
        str(candidate["target_node_ids"][0]): inline_text_projection(
            candidate["proposal"]["value"]["inline_content"]
        )
        for candidate in _content_candidates(candidates)
    }
    text_by_node = _text_by_node(facts)

    assert {text_by_node[node_id]: text for node_id, text in written.items()} == {
        "第一章 概述": "概述",
        "1.2 需求分析": "需求分析",
    }


def test_a_plain_decimal_prefix_is_removed_from_a_heading(tmp_path: Path) -> None:
    facts, candidates = _candidates(tmp_path, [("1 项目概述", "Heading 1")])

    written = {
        str(candidate["target_node_ids"][0]): inline_text_projection(
            candidate["proposal"]["value"]["inline_content"]
        )
        for candidate in _content_candidates(candidates)
    }
    text_by_node = _text_by_node(facts)

    assert [text for text in written.values()] == ["项目概述"]
    assert text_by_node[str(next(iter(written)))] == "1 项目概述"


def test_a_body_paragraph_with_a_marker_stays_protected_body(tmp_path: Path) -> None:
    facts, candidates = _candidates(tmp_path, [("1.2 需求分析", None)])

    roles = _role_candidates(candidates)
    text_by_node = _text_by_node(facts)

    assert [candidate["proposal"]["value"]["role"] for candidate in roles] == ["body"]
    assert roles[0]["positive_evidence"][0]["source"] == "protected_content"
    assert _content_candidates(candidates) == []
    assert text_by_node[str(roles[0]["target_node_ids"][0])] == "1.2 需求分析"


def test_a_three_segment_marker_still_keeps_its_heading(tmp_path: Path) -> None:
    facts, candidates = _candidates(tmp_path, [("1.2.3 详细设计", "Heading 3")])

    text_by_node = _text_by_node(facts)
    roles = _role_candidates(candidates)

    assert roles[0]["proposal"]["value"] == {"role": "heading", "level": 3}
    assert text_by_node[str(roles[0]["target_node_ids"][0])] == "1.2.3 详细设计"


def test_a_numbered_note_under_a_heading_style_stays_a_note(tmp_path: Path) -> None:
    facts, candidates = _candidates(tmp_path, [("注2 说明", "Heading 1")])

    roles = _role_candidates(candidates)

    assert [candidate["proposal"]["value"]["role"] for candidate in roles] == ["numbered_note"]


def test_a_marker_only_heading_keeps_its_text_and_records_the_decision(tmp_path: Path) -> None:
    facts, candidates = _candidates(
        tmp_path,
        [("第一章", "Heading 1"), ("1.2 ", "Heading 1")],
    )

    text_by_node = _text_by_node(facts)
    roles = _role_candidates(candidates)
    records = [
        item
        for candidate in roles
        for item in candidate["positive_evidence"]
        if item.get("code") == "heading_marker_only_text"
    ]

    assert len(records) == 2, candidates
    assert _content_candidates(candidates) == []
    assert sorted(text_by_node[str(candidate["target_node_ids"][0])] for candidate in roles) == [
        "1.2 ",
        "第一章",
    ]


def test_a_marker_that_spans_an_object_keeps_the_source_text(tmp_path: Path) -> None:
    """A marker the walk cannot consume is reported instead of applied in part.

    A literal marker is always text, so this shape needs an inline object whose
    cached text carries part of the marker.  Removing what the walk can reach
    would leave the heading reading ``1求分析``, so the source text stays and the
    record names the shape.
    """

    from wxdf.semantics.structure import _marker_content_candidate

    unit = {
        "inline_content": [
            {"kind": "field", "instruction": "REF x", "cached_result": "1"},
            {"kind": "text", "text": ".2 需求分析", "marks": []},
        ]
    }

    content, record = _marker_content_candidate("u-marker", unit, "1.2 需求分析")

    assert content is None
    assert record is not None and record["code"] == "heading_marker_not_consumed"
    assert record["severity"] == "info"


def test_the_cleanup_keeps_the_marks_inside_the_heading(tmp_path: Path) -> None:
    """Removing the marker consumes characters, not the runs that carry marks."""

    from wxdf.semantics.structure import _marker_content_candidate

    unit = {
        "inline_content": [
            {"kind": "text", "text": "1.2 ", "marks": []},
            {"kind": "text", "text": "需求分析", "marks": ["bold"]},
        ]
    }

    content, record = _marker_content_candidate("u-marker", unit, "1.2 需求分析")

    assert record is None
    assert content["proposal"]["value"]["inline_content"] == [
        {"kind": "text", "text": "需求分析", "marks": ["bold"]}
    ]


def test_the_marker_reason_rides_on_the_heading_candidate_as_evidence(tmp_path: Path) -> None:
    facts, candidates = _candidates(tmp_path, [("第一章 概述", "Heading 1")])

    roles = _role_candidates(candidates)
    sources = [item.get("source") or item.get("reason") for item in roles[0]["positive_evidence"]]

    assert "docx_style" in sources
    assert "manual_heading_marker" in sources


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_a_marked_heading_renders_as_a_heading_end_to_end(tmp_path: Path) -> None:
    output, result = _convert_docx(
        tmp_path,
        [("第一章 概述", "Heading 1"), ("正文内容。", None), ("1.2 需求分析", "Heading 2")],
    )

    assert result.status == "completed", result.diagnostics
    assert result.report["model_audit"]["passed"] is True
    assert result.report["saved_output_audit"]["passed"] is True
    assert result.report["saved_output_audit"]["unexpected_styles"] == []
    # The DOCX declares no title, so the front matter title is the only warning.
    assert [warning.get("type") for warning in (result.report.get("risk_warnings") or [])] == [
        "document_title_generated"
    ]

    document = Document(str(output))
    styled = [
        (paragraph.style.name, paragraph.text)
        for paragraph in document.paragraphs
        if paragraph.style.name in {"Heading 1", "Heading 2"}
    ]

    assert styled == [("Heading 1", "概述"), ("Heading 2", "需求分析")]


def test_a_marked_markdown_h1_becomes_the_clean_document_title(tmp_path: Path) -> None:
    output, result = _convert_markdown(tmp_path, "# 1.2 需求分析\n\n正文内容。\n")

    assert result.status == "completed", result.diagnostics
    assert (result.report.get("risk_warnings") or []) == []

    rows = _paragraph_rows(output)

    assert ("文档标题", "需求分析") in rows, rows
    assert [text for _style, text in rows if text == "input"] == [], rows
