from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.source_ast import build_source_ast, materialize_list_semantics


HEADING_STYLE = "heading 6"
LIST_STYLE = "1.1一级列项-编号"


def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "document.docx",
        "content_sha256": "sha256:" + "a" * 64,
        "node_path": ["body", index, "w:p"],
    }


def _paragraph(unit_id: str, index: int, text: str, *, style: str | None = None, role: str | None = None) -> dict:
    unit = {
        "kind": "paragraph",
        "source_ref": _ref(index),
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
    }
    if style is not None:
        unit["style"] = style
    if role is not None:
        unit["role"] = role
    return unit


def _clause(unit_id: str, index: int, *segments: str) -> dict:
    """A paragraph whose inline content holds soft line breaks between clauses."""
    inline: list[dict] = []
    for position, segment in enumerate(segments):
        if position:
            inline.append({"kind": "break"})
        inline.append({"kind": "text", "text": segment, "marks": []})
    return {"kind": "paragraph", "source_ref": _ref(index), "inline_content": inline}


def _facts(containers: dict, children: dict, units: dict, evidence: dict) -> dict:
    return {
        "facts_schema_version": "1.0",
        "source": {"source_id": "source", "source_type": "docx", "content_sha256": "sha256:" + "a" * 64},
        "parser": {"id": "test", "version": "1.0", "dialect": "fixture", "config_sha256": "sha256:" + "b" * 64},
        "units": units,
        "containers": containers,
        "children": children,
        "evidence": evidence,
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def _confirmed_item_evidence() -> dict:
    return {
        "style": LIST_STYLE,
        "numbering_status": "detected",
        "numbering_definition_ref": {"num_id": 5, "abstract_num_id": 4},
        "num_fmt": "decimal",
        "lvl_text": "%1)",
        "source_list_type": "decimal_chain",
        "marker_text": "a)",
        "marker_kind": "ordered",
        "ordinal": 1,
        "explicit_depth": 0,
    }


def _states(result: dict) -> dict[str, str]:
    return {
        str(decision["target_node_ids"][0]): str((decision.get("value") or {}).get("state"))
        for decision in result["state_decisions"]
        if decision.get("target_node_ids")
    }


def test_section_body_of_parallel_clauses_becomes_one_level_one_ordered_list() -> None:
    units = {
        "h1": _paragraph("h1", 0, "智能问答", style=HEADING_STYLE, role="heading"),
        "p1": _paragraph("p1", 1, "支持查询告警信息；"),
        "p2": _paragraph("p2", 2, "支持查询事件信息；"),
        "p3": _paragraph("p3", 3, "支持查询风险信息。"),
        "h2": _paragraph("h2", 4, "关键指标", style=HEADING_STYLE, role="heading"),
        "c1": _paragraph("c1", 5, "支持展示指标当前值；"),
        "c2": _paragraph("c2", 6, "支持展示指标目标值。"),
    }
    children = {"root": ["h1", "p1", "p2", "p3", "h2", "c1", "c2"]}
    evidence = {
        "h1": {"style": HEADING_STYLE},
        "h2": {"style": HEADING_STYLE},
        "c1": _confirmed_item_evidence(),
        "c2": _confirmed_item_evidence(),
    }

    result = analyze_lists(_facts({"root": {"kind": "document"}}, children, units, evidence))

    states = _states(result)
    assert states["p1"] == states["p2"] == states["p3"] == "LIST_ITEM", states
    assert result["status"] == "completed", result["diagnostics"]
    by_id = {item["item_id"]: item for item in result["items"]}
    assert by_id["p1"]["list_id"] == by_id["p2"]["list_id"] == by_id["p3"]["list_id"]
    group = next(group for group in result["lists"] if group["list_id"] == by_id["p1"]["list_id"])
    assert group["list_kind"] == "ordered"
    assert [by_id[key]["target_ordinal"] for key in ("p1", "p2", "p3")] == [1, 2, 3]
    assert all(by_id[key]["semantic_level"] == 0 for key in ("p1", "p2", "p3"))
    records = [item for item in result["downgrades"] if item["code"] == "inferred_requirement_list"]
    assert len(records) == 1, result["downgrades"]
    assert records[0]["severity"] == "info"
    assert records[0]["node_id"] == "p1"
    assert sorted(records[0]["member_node_ids"]) == ["p1", "p2", "p3"]
    # An inference is a solved decision, so it is published as a downgrade record
    # and never turns a clean document into a warning run.
    assert result["inferred_requirement_runs"] == [records[0]]
    assert "inferred_requirement_list" not in [item["code"] for item in result["diagnostics"]]


def test_clause_lead_without_a_confirmed_list_item_stays_body() -> None:
    units = {
        "h1": _paragraph("h1", 0, "智能问答", style=HEADING_STYLE, role="heading"),
        "p1": _paragraph("p1", 1, "支持查询告警信息；"),
        "p2": _paragraph("p2", 2, "支持查询事件信息；"),
        "p3": _paragraph("p3", 3, "支持查询风险信息。"),
    }
    children = {"root": ["h1", "p1", "p2", "p3"]}
    evidence = {"h1": {"style": HEADING_STYLE}}

    result = analyze_lists(_facts({"root": {"kind": "document"}}, children, units, evidence))

    states = _states(result)
    assert states["p1"] == states["p2"] == states["p3"] == "OUTSIDE_LIST", states
    assert result["items"] == []


def test_section_with_prose_around_the_clauses_stays_body() -> None:
    units = {
        "h1": _paragraph("h1", 0, "智能问答", style=HEADING_STYLE, role="heading"),
        "lead": _paragraph("lead", 1, "本节说明智能问答的能力范围。"),
        "p1": _paragraph("p1", 2, "支持查询告警信息；"),
        "p2": _paragraph("p2", 3, "支持查询事件信息；"),
        "p3": _paragraph("p3", 4, "支持查询风险信息。"),
        "h2": _paragraph("h2", 5, "关键指标", style=HEADING_STYLE, role="heading"),
        "c1": _paragraph("c1", 6, "支持展示指标当前值；"),
        "c2": _paragraph("c2", 7, "支持展示指标目标值。"),
    }
    children = {"root": ["h1", "lead", "p1", "p2", "p3", "h2", "c1", "c2"]}
    evidence = {
        "h1": {"style": HEADING_STYLE},
        "h2": {"style": HEADING_STYLE},
        "c1": _confirmed_item_evidence(),
        "c2": _confirmed_item_evidence(),
    }

    result = analyze_lists(_facts({"root": {"kind": "document"}}, children, units, evidence))

    states = _states(result)
    assert states["lead"] == states["p1"] == states["p2"] == states["p3"] == "OUTSIDE_LIST", states
    assert [item["item_id"] for item in result["items"]] == ["c1", "c2"]


def test_single_clause_section_is_not_promoted() -> None:
    units = {
        "h1": _paragraph("h1", 0, "智能问答", style=HEADING_STYLE, role="heading"),
        "p1": _paragraph("p1", 1, "支持查询告警信息。"),
        "h2": _paragraph("h2", 2, "关键指标", style=HEADING_STYLE, role="heading"),
        "c1": _paragraph("c1", 3, "支持展示指标当前值；"),
        "c2": _paragraph("c2", 4, "支持展示指标目标值。"),
    }
    children = {"root": ["h1", "p1", "h2", "c1", "c2"]}
    evidence = {
        "h1": {"style": HEADING_STYLE},
        "h2": {"style": HEADING_STYLE},
        "c1": _confirmed_item_evidence(),
        "c2": _confirmed_item_evidence(),
    }

    result = analyze_lists(_facts({"root": {"kind": "document"}}, children, units, evidence))

    assert _states(result)["p1"] == "OUTSIDE_LIST"
    assert [item["item_id"] for item in result["items"]] == ["c1", "c2"]


def test_break_separated_clauses_of_a_confirmed_item_become_separate_items() -> None:
    units = {
        "q1": _clause("q1", 0, "支持甲项；", "支持乙项；", "支持丙项。"),
    }
    children = {"root": ["q1"]}
    evidence = {"q1": _confirmed_item_evidence()}
    facts = _facts({"root": {"kind": "document"}}, children, units, evidence)

    result = analyze_lists(facts)

    assert [item["item_id"] for item in result["items"]] == ["q1", "q1#1", "q1#2"], result["items"]
    assert [item["target_ordinal"] for item in result["items"]] == [1, 2, 3]


def test_break_separated_clauses_of_an_inferred_section_become_separate_items() -> None:
    units = {
        "h1": _paragraph("h1", 0, "智能问答", style=HEADING_STYLE, role="heading"),
        "q1": _clause("q1", 1, "支持甲方查询；", "支持乙方查询；", "支持丙方查询。"),
        "h2": _paragraph("h2", 2, "关键指标", style=HEADING_STYLE, role="heading"),
        "c1": _paragraph("c1", 3, "支持展示指标当前值；"),
        "c2": _paragraph("c2", 4, "支持展示指标目标值。"),
    }
    children = {"root": ["h1", "q1", "h2", "c1", "c2"]}
    evidence = {
        "h1": {"style": HEADING_STYLE},
        "h2": {"style": HEADING_STYLE},
        "c1": _confirmed_item_evidence(),
        "c2": _confirmed_item_evidence(),
    }

    result = analyze_lists(_facts({"root": {"kind": "document"}}, children, units, evidence))

    # Items stay in document order: the merged section comes before the key-metric
    # list, and its clauses are expanded in place.
    assert [item["item_id"] for item in result["items"]] == ["q1", "q1#1", "q1#2", "c1", "c2"]


def test_split_item_materializes_generated_blocks_for_the_extra_clauses() -> None:
    units = {
        "q1": _clause("q1", 0, "支持甲项；", "支持乙项；", "支持丙项。"),
    }
    children = {"root": ["q1"]}
    evidence = {"q1": _confirmed_item_evidence()}
    facts = _facts({"root": {"kind": "document"}}, children, units, evidence)
    ast = build_source_ast(facts)

    semantic, candidates = materialize_list_semantics(ast, facts, analyze_lists(facts))[:2]

    created = {
        str(candidate["proposal"]["value"]["node_id"]): candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "create_node"
    }
    assert "q1#1" in created and "q1#2" in created
    assert created["q1#1"]["proposal"]["value"]["node"]["block_type"] == "paragraph"
    assert created["q1#1"]["proposal"]["value"]["node"]["generated_by"] == "list_semantics.parallel_clause_split"
    assert created["q1#1"]["proposal"]["value"]["node"]["inline_content"] == [
        {"kind": "text", "text": "支持乙项；", "marks": []},
    ]
    trimmed = [
        candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "update_node_content"
        and str(candidate["target_node_ids"][0]) == "q1"
    ]
    assert len(trimmed) == 1
    assert trimmed[0]["proposal"]["value"]["inline_content"] == [
        {"kind": "text", "text": "支持甲项；", "marks": []},
    ]
    assert semantic is not None


def test_break_in_a_non_list_paragraph_keeps_its_breaks() -> None:
    units = {
        "b1": _clause("b1", 0, "普通正文第一行；", "普通正文第二行。"),
    }
    children = {"root": ["b1"]}
    facts = _facts({"root": {"kind": "document"}}, children, units, {})

    result = analyze_lists(facts)

    assert result["items"] == []
    assert [item["kind"] for item in units["b1"]["inline_content"]] == ["text", "break", "text"]


def test_a_minority_confirmed_lead_does_not_drive_the_inference() -> None:
    units = {
        "h1": _paragraph("h1", 0, "平台能力中心", style=HEADING_STYLE, role="heading"),
        "x1": _paragraph("x1", 1, "平台能力中心作为公共能力支撑层提供统一能力。"),
        "x2": _paragraph("x2", 2, "平台能力中心重点承载共性能力。"),
        "h2": _paragraph("h2", 3, "支持要求", style=HEADING_STYLE, role="heading"),
        "p1": _paragraph("p1", 4, "支持甲项；"),
        "p2": _paragraph("p2", 5, "支持乙项；"),
        "p3": _paragraph("p3", 6, "支持丙项；"),
        "p4": _paragraph("p4", 7, "支持丁项；"),
        "p5": _paragraph("p5", 8, "支持戊项；"),
        "p6": _paragraph("p6", 9, "支持己项。"),
        "q1": _paragraph("q1", 10, "平台能力一；"),
        "q2": _paragraph("q2", 11, "平台能力二。"),
    }
    children = {"root": ["h1", "x1", "x2", "h2", "p1", "p2", "p3", "p4", "p5", "p6", "q1", "q2"]}
    evidence = {"h1": {"style": HEADING_STYLE}, "h2": {"style": HEADING_STYLE}}
    for key in ("p1", "p2", "p3", "p4", "p5", "p6", "q1", "q2"):
        evidence[key] = _confirmed_item_evidence()

    result = analyze_lists(_facts({"root": {"kind": "document"}}, children, units, evidence))

    states = _states(result)
    # ``支持`` is confirmed six times and ``平台`` twice, so the two prose
    # paragraphs that merely share the two leading characters of the minority
    # lead stay body text.
    assert states["x1"] == states["x2"] == "OUTSIDE_LIST", states
    assert states["p1"] == states["p6"] == "LIST_ITEM", states


def test_split_item_owns_its_generated_paragraph_without_a_marker_anchor() -> None:
    units = {
        "q1": _clause("q1", 0, "支持甲项；", "支持乙项；", "支持丙项。"),
    }
    children = {"root": ["q1"]}
    evidence = {"q1": _confirmed_item_evidence()}
    facts = _facts({"root": {"kind": "document"}}, children, units, evidence)

    result = analyze_lists(facts)
    by_id = {item["item_id"]: item for item in result["items"]}
    # Every clause owns the paragraph its item was created with, so no item
    # generates a second, empty marker paragraph (decision 10).
    assert by_id["q1"]["generated_anchor_required"] is False
    assert by_id["q1#1"]["generated_anchor_required"] is False
    assert by_id["q1#2"]["generated_anchor_required"] is False

    ast = build_source_ast(facts)
    _semantic, candidates = materialize_list_semantics(ast, facts, result)[:2]
    assert [item["candidate_id"] for item in candidates if "list-anchor" in item["candidate_id"]] == []
    created = {
        str(candidate["proposal"]["value"]["node_id"]): candidate
        for candidate in candidates
        if candidate["proposal"]["operation"] == "create_node"
    }
    # A generated item container cites the paragraph its clause was split from,
    # so a decision ledger can still bind the revision to a source.
    assert created["native-list-item-q1#1"]["target_source_refs"] == [units["q1"]["source_ref"]]
    assert created["native-list-item-q1#1"]["proposal"]["value"]["node"]["source_ref"] == units["q1"]["source_ref"]


def test_clauses_in_a_table_cell_are_not_inferred() -> None:
    units = {
        "h1": _paragraph("h1", 0, "智能问答", style=HEADING_STYLE, role="heading"),
        "h2": _paragraph("h2", 1, "关键指标", style=HEADING_STYLE, role="heading"),
        "c1": _paragraph("c1", 2, "支持展示指标当前值；"),
        "c2": _paragraph("c2", 3, "支持展示指标目标值。"),
        "k1": _paragraph("k1", 4, "支持单元格条款一；"),
        "k2": _paragraph("k2", 5, "支持单元格条款二。"),
    }
    containers = {
        "root": {"kind": "document"},
        "table": {"kind": "table", "table_type": "data"},
        "row": {"kind": "table_row", "row": 0},
        "cell": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1},
    }
    children = {
        "root": ["h1", "table", "h2", "c1", "c2"],
        "table": ["row"],
        "row": ["cell"],
        "cell": ["k1", "k2"],
    }
    evidence = {
        "h1": {"style": HEADING_STYLE},
        "h2": {"style": HEADING_STYLE},
        "c1": _confirmed_item_evidence(),
        "c2": _confirmed_item_evidence(),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    states = _states(result)
    # The table contract owns cell content, so a cell is not a section body.
    assert states["k1"] == states["k2"] == "OUTSIDE_LIST", states
    assert [item["item_id"] for item in result["items"]] == ["c1", "c2"]


def test_clauses_without_a_heading_are_not_inferred() -> None:
    units = {
        "p1": _paragraph("p1", 0, "支持甲项；"),
        "p2": _paragraph("p2", 1, "支持乙项。"),
        "h2": _paragraph("h2", 2, "关键指标", style=HEADING_STYLE, role="heading"),
        "c1": _paragraph("c1", 3, "支持展示指标当前值；"),
        "c2": _paragraph("c2", 4, "支持展示指标目标值。"),
    }
    children = {"root": ["p1", "p2", "h2", "c1", "c2"]}
    evidence = {
        "h2": {"style": HEADING_STYLE},
        "c1": _confirmed_item_evidence(),
        "c2": _confirmed_item_evidence(),
    }

    result = analyze_lists(_facts({"root": {"kind": "document"}}, children, units, evidence))

    states = _states(result)
    assert states["p1"] == states["p2"] == "OUTSIDE_LIST", states
    assert [item["item_id"] for item in result["items"]] == ["c1", "c2"]
