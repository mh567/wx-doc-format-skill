from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.semantics.lists import analyze_lists
from wxdf.parsers import parse


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
    assert result["diagnostics"][0]["code"] == "unresolved_list_parent"
    assert all(item["scope_id"] == "scope:document" for item in result["items"])


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
    assert result["diagnostics"][0]["code"] == "protected_list_like_text"
    assert result["items"][0]["generated_anchor_required"] is False


def test_real_markdown_parser_facts_use_the_same_list_consumer(tmp_path: Path) -> None:
    source = tmp_path / "lists.md"
    source.write_text("1. 父项\n   - 无序子项\n   1. 有序子项\n2. 第二项\n", encoding="utf-8")

    facts = parse(source)
    result = analyze_lists(facts)

    assert result["status"] == "completed"
    assert {item["list_kind"] for item in result["lists"]} == {"ordered", "unordered"}
    assert len(result["items"]) == 4
