from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.source_ast import build_source_ast, materialize_list_semantics
from wxdf.parsers import parse
from wxdf.model import inline_text_projection


def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


def _paragraph(unit_id: str, index: int, text: str) -> dict:
    return {
        "kind": "paragraph",
        "source_ref": _ref(index),
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
    }


def _facts(containers: dict, children: dict, units: dict, evidence: dict) -> dict:
    return {
        "facts_schema_version": "1.0",
        "source": {"source_id": "source", "source_type": "markdown", "content_sha256": "sha256:" + "a" * 64},
        "parser": {"id": "test", "version": "1.0", "dialect": "fixture", "config_sha256": "sha256:" + "b" * 64},
        "units": units,
        "containers": containers,
        "children": children,
        "evidence": evidence,
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


def test_explicit_nested_ordered_unordered_ordered_lists_share_target_scope() -> None:
    units = {key: _paragraph(key, index, text) for index, (key, text) in enumerate((
        ("p1", "父项一"), ("pu", "无序子项"), ("po", "有序子项"), ("p2", "父项二"),
    ))}
    containers = {
        "root": {"kind": "document"},
        "l1": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s1"},
        "i1": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "s1"},
        "lu": {"kind": "list", "list_kind": "unordered", "source_start": 1, "source_sequence_id": "su"},
        "iu": {"kind": "list_item", "source_marker": "•", "source_ordinal": 1, "source_sequence_id": "su"},
        "lo": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "so"},
        "io": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "so"},
        "i2": {"kind": "list_item", "source_marker": "2.", "source_ordinal": 2, "source_sequence_id": "s1"},
    }
    children = {
        "root": ["l1"], "l1": ["i1", "i2"], "i1": ["p1", "lu", "lo"], "lu": ["iu"], "iu": ["pu"], "lo": ["io"], "io": ["po"], "i2": ["p2"],
    }
    evidence = {key: {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 0} for key in ("i1", "i2")}
    evidence.update({"iu": {"marker_kind": "unordered", "marker_text": "•", "ordinal": 1, "explicit_depth": 0}, "io": {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 0}})

    result = analyze_lists(_facts(containers, children, units, evidence))

    by_id = {item["item_id"]: item for item in result["items"]}
    assert by_id["i1"]["target_ordinal"] == 1
    assert by_id["iu"]["target_ordinal"] is None
    assert by_id["io"]["target_ordinal"] == 2
    assert by_id["i2"]["target_ordinal"] == 3
    assert by_id["io"]["parent_item_id"] == "i1"
    assert result["diagnostics"] == []


def test_implicit_docx_depth_jump_is_unresolved_and_container_scope_isolated() -> None:
    units = {key: _paragraph(key, index, text) for index, (key, text) in enumerate((
        ("u1", "第一项"), ("u2", "孤立深层项"), ("u3", "第二项"),
    ))}
    children = {"root": ["u1", "u2", "u3"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "u1": {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 0, "numbering_definition_ref": {"num_id": 1}},
        "u2": {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 2, "numbering_definition_ref": {"num_id": 1}},
        "u3": {"marker_kind": "ordered", "marker_text": "2.", "ordinal": 2, "explicit_depth": 0, "numbering_definition_ref": {"num_id": 1}},
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert result["status"] == "unresolved"
    codes = [item["code"] for item in result["diagnostics"]]
    # The level jump is reported once as constraint 4; the materializer records
    # the flattening it applies as ``unresolved_list_parent``.  A single defect
    # must not also produce a constraint-3 "no parent" finding for the same node.
    assert "unresolved_list_parent" in codes
    constraint_findings = [
        item for item in result["state_constraint_findings"] if item["code"] == "list_constraint_violation"
    ]
    assert [(item["constraint"], item["node_id"]) for item in constraint_findings] == [(4, "u2")], constraint_findings
    assert all(item["scope_id"] == "scope:document" for item in result["items"])


def test_implicit_docx_orphan_depth_is_flattened_to_local_list_root() -> None:
    units = {key: _paragraph(key, index, text) for index, (key, text) in enumerate((
        ("u1", "孤立深层项一"), ("u2", "孤立深层项二"),
    ))}
    children = {"root": ["u1", "u2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "u1": {"marker_kind": "unordered", "marker_text": "", "ordinal": 1, "explicit_depth": 2, "numbering_definition_ref": {"num_id": 25}},
        "u2": {"marker_kind": "unordered", "marker_text": "", "ordinal": 2, "explicit_depth": 2, "numbering_definition_ref": {"num_id": 25}},
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    codes = [item["code"] for item in result["diagnostics"]]
    assert sorted(codes) == sorted([
        "orphan_list_depth_flattened",
        "list_constraint_violation",
        "list_constraint_violation",
    ])
    assert sorted(
        (item["constraint"], item["node_id"]) for item in result["state_constraint_findings"]
    ) == [(3, "u1"), (4, "u2")]
    orphan = next(item for item in result["diagnostics"] if item["code"] == "orphan_list_depth_flattened")
    assert orphan["severity"] == "info"
    assert all(item["parent_item_id"] is None for item in result["items"])


def test_materialize_list_semantics_normalizes_empty_docx_bullet_marker() -> None:
    facts = _facts(
        {"root": {"kind": "document"}},
        {"root": ["p1"]},
        {"p1": _paragraph("p1", 0, "项目")},
        {
            "p1": {
                "marker_text": "",
                "source_list_type": "bullet_dot",
                "ordinal": 1,
            }
        },
    )
    source_ast = build_source_ast(facts)
    list_result = {
        "lists": [{
            "list_id": "docx-bullet-list",
            "list_kind": "unordered",
            "source_start": 1,
            "source_sequence_id": "docx-bullet",
        }],
        "items": [{
            "item_id": "p1",
            "list_id": "docx-bullet-list",
            "source_marker": "",
            "source_ordinal": 1,
            "source_sequence_id": "docx-bullet",
            "list_kind": "unordered",
        }],
        "candidates": [],
        "continuations": [],
    }

    _, candidates, _ = materialize_list_semantics(source_ast, facts, list_result)

    list_item = next(
        candidate["proposal"]["value"]["node"]
        for candidate in candidates
        if candidate["candidate_id"] == "list-item-structure-p1"
    )
    assert list_item["source_marker"] == "•"


def test_materialize_list_semantics_derives_deterministic_list_markers() -> None:
    item_ids = ("keep", "dash", "letter", "decimal")
    facts = _facts(
        {"root": {"kind": "document"}},
        {"root": list(item_ids)},
        {item_id: _paragraph(item_id, index, item_id) for index, item_id in enumerate(item_ids)},
        {
            "keep": {"marker_text": "源标记", "source_list_type": "bullet_dot", "ordinal": 1},
            "dash": {"marker_text": "", "source_list_type": "dash", "ordinal": 1},
            "letter": {"marker_text": "", "source_list_type": "lower_letter_paren", "ordinal": 2},
            "decimal": {"marker_text": "", "source_list_type": "decimal_paren", "ordinal": 3},
        },
    )
    source_ast = build_source_ast(facts)
    list_result = {
        "lists": [
            {"list_id": "unordered", "list_kind": "unordered", "source_start": 1},
            {"list_id": "ordered", "list_kind": "ordered", "source_start": 1},
        ],
        "items": [
            {"item_id": "keep", "list_id": "unordered", "source_marker": "源标记", "source_ordinal": 1},
            {"item_id": "dash", "list_id": "unordered", "source_marker": "", "source_list_type": "dash", "source_ordinal": 1},
            {"item_id": "letter", "list_id": "ordered", "source_marker": "", "source_list_type": "lower_letter_paren", "source_ordinal": 2},
            {"item_id": "decimal", "list_id": "ordered", "source_marker": "", "source_list_type": "decimal_paren", "source_ordinal": 3},
        ],
        "candidates": [],
        "continuations": [],
    }

    _, candidates, _ = materialize_list_semantics(source_ast, facts, list_result)

    markers = {
        candidate["target_source_refs"][0]["span"]["start"]: candidate["proposal"]["value"]["node"]["source_marker"]
        for candidate in candidates
        if candidate["candidate_id"].startswith("list-item-structure-")
    }
    assert markers == {0: "源标记", 1: "-", 2: "b)", 3: "3)"}
    assert facts["evidence"]["dash"]["marker_text"] == ""


def test_continuation_and_protected_date_are_distinguished() -> None:
    units = {
        "p1": _paragraph("p1", 0, "第一项"),
        "p1b": _paragraph("p1b", 1, "续段"),
        "date": _paragraph("date", 2, "2026-09-08"),
    }
    containers = {
        "root": {"kind": "document"},
        "list": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s"},
        "item": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "s"},
    }
    children = {"root": ["list", "date"], "list": ["item"], "item": ["p1", "p1b"]}
    evidence = {
        "item": {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 0},
        "p1b": {"continuation_hint": True},
        "date": {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 0},
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert result["continuations"] == [{"node_id": "p1b", "owner_item_id": "item", "list_id": "list", "scope_id": "scope:document"}]
    assert result["downgrades"][0]["code"] == "protected_list_like_text"
    assert result["items"][0]["generated_anchor_required"] is False


def test_real_markdown_parser_facts_use_the_same_list_consumer(tmp_path: Path) -> None:
    source = tmp_path / "lists.md"
    source.write_text("1. 父项\n   - 无序子项\n   1. 有序子项\n2. 第二项\n", encoding="utf-8")

    facts = parse(source)
    result = analyze_lists(facts)

    assert result["status"] == "completed"
    assert {item["list_kind"] for item in result["lists"]} == {"ordered", "unordered"}
    assert len(result["items"]) == 4


def test_docx_visible_list_markers_are_removed_when_list_semantics_materialize(tmp_path: Path) -> None:
    source = tmp_path / "visible-markers.docx"
    from docx import Document

    document = Document()
    document.add_paragraph("事件运营需求", style="Heading 3")
    document.add_paragraph("1. 第一项需求")
    document.add_paragraph("2. 第二项需求")
    document.add_paragraph("4.2.1 复合编号需求")
    document.add_paragraph("a) 事件输入")
    document.add_paragraph("b) 事件研判")
    document.save(source)

    facts = parse(source)
    source_ast = build_source_ast(facts)
    source_ast_texts = {
        inline_text_projection(block.get("inline_content", []))
        for block in source_ast["document"]["blocks"].values()
        if block.get("block_type") == "paragraph"
    }
    assert "1. 第一项需求" in source_ast_texts
    list_result = analyze_lists(facts)
    materialized, candidates, _ = materialize_list_semantics(source_ast, facts, list_result)

    assert materialized["document"]["blocks"]
    cleaned_texts = {
        inline_text_projection(candidate["proposal"]["value"]["inline_content"])
        for candidate in candidates
        if candidate["proposal"]["operation"] == "update_node_content"
    }
    assert {"第一项需求", "第二项需求", "复合编号需求", "事件输入", "事件研判"} <= cleaned_texts
    assert not {"1. 第一项需求", "2. 第二项需求", "4.2.1 复合编号需求", "a) 事件输入", "b) 事件研判"} & cleaned_texts
