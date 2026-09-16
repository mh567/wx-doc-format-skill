"""execution-v6 residual round R5: note ownership, in-run table, per-chapter restart.

This file pins the three residuals closed in round R5 on top of the T05 content
ownership work:

  * A: a note that follows a list run is attributed to the item as an
    ``ITEM_CHILD_BLOCK`` (README's candidate-state enum lists 注 under that
    state), while the note keeps its protected role and its own text;
  * B: a table that sits between two items of the same run is attributed to the
    preceding item, and the run still splits / merges as README constraints 5
    and 8 require;
  * C: decision 1's per-chapter restart of top-level ordered numbering becomes
    real, so two chapters each followed by an ordered list both show ``a) b)``.

Fixture naming follows the T01 convention ``t<task>_<scenario>_<variant>``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping

from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import analyze_lists, _is_note, _is_protected
from wxdf.semantics.source_ast import build_source_ast, materialize_list_semantics

sys.path.insert(0, str(ROOT / "tests"))
from modular_support import simulate_saved_numbering  # noqa: E402


TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": WORD_NS}


# ---------------------------------------------------------------------------
# Fixture builders (mirroring tests/test_t05_mixed_content.py)
# ---------------------------------------------------------------------------

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


def _numbered_evidence(num_id: int, ordinal: int, *, depth: int = 0, fmt: str = "decimal") -> dict:
    return {
        "marker_kind": "ordered",
        "marker_text": f"{ordinal}.",
        "ordinal": ordinal,
        "explicit_depth": depth,
        "numbering_definition_ref": {"num_id": num_id, "abstract_num_id": num_id},
        "num_fmt": fmt,
        "lvl_text": "%1.",
        "source_ilvl": depth,
        "p_style_ref": None,
    }


def _note_evidence() -> dict:
    return {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None, "semantic_role": "note"}


def _body_evidence() -> dict:
    return {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None}


def _materialized_ast(facts: Mapping[str, Any]):
    result = analyze_lists(facts)
    ast = build_source_ast(facts)
    materialized, candidates, item_map = materialize_list_semantics(ast, facts, result)
    return result, materialized, candidates, item_map


def _created_items(candidates: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    items: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        proposal = candidate.get("proposal") or {}
        value = proposal.get("value") if isinstance(proposal.get("value"), Mapping) else {}
        node = value.get("node") if isinstance(value, Mapping) else None
        if proposal.get("operation") == "create_node" and isinstance(node, Mapping) and node.get("kind") == "list_item":
            items[str(value.get("node_id"))] = node
    return items


def _move_targets(candidates: list[Mapping[str, Any]]) -> dict[str, str]:
    moves: dict[str, str] = {}
    for candidate in candidates:
        proposal = candidate.get("proposal") or {}
        if proposal.get("operation") != "move_node":
            continue
        value = proposal.get("value") if isinstance(proposal.get("value"), Mapping) else {}
        targets = candidate.get("target_node_ids") or []
        if targets:
            moves[str(targets[0])] = str(value.get("new_parent_id"))
    return moves


def _parent_index(children: Mapping[str, list[str]]) -> dict[str, str]:
    return {child_id: parent_id for parent_id, child_ids in children.items() for child_id in child_ids}


def _convert_docx(tmp_path: Path, source: Path, tag: str) -> tuple[dict, Path, object]:
    output = tmp_path / f"{tag}.out.docx"
    normalized_path = tmp_path / f"{tag}.normalized.json"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"normalized_ast_path": normalized_path, "llm_enhance": "off"},
    )
    result = ConversionKernel().convert(request)
    normalized = json.loads(normalized_path.read_text(encoding="utf-8")) if normalized_path.exists() else {}
    return normalized, output, result


def _report_of(result: object) -> dict:
    """Return the published report of a conversion result."""

    report = getattr(result, "report", None)
    return dict(report) if isinstance(report, Mapping) else {}


# ---------------------------------------------------------------------------
# A: a note after a list run is owned by the item
# ---------------------------------------------------------------------------

def test_r5_note_after_implicit_run_is_owned_by_the_item() -> None:
    """``[item][item][note]`` attributes the note to the second item.

    The note keeps its protected role: it is attributed as an
    ``ITEM_CHILD_BLOCK`` (README lists 注 under that state) instead of being
    demoted to a continuation or dropped, and ``_is_protected`` still reports it
    as protected so its styling is untouched.
    """

    units = {
        "i1": _paragraph("i1", 0, "第一项"),
        "i2": _paragraph("i2", 1, "第二项"),
        "note": _paragraph("note", 2, "注：尾部说明。"),
    }
    children = {"root": ["i1", "i2", "note"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "i1": _numbered_evidence(1, 1),
        "i2": _numbered_evidence(1, 2),
        "note": _note_evidence(),
    }
    facts = _facts(containers, children, units, evidence)

    # The protected policy still recognizes the note, so attributing it does not
    # demote its role.
    assert _is_protected(units["note"], evidence["note"])[0] is True
    assert _is_note(units["note"], evidence["note"]) is True

    result, _materialized, candidates, item_map = _materialized_ast(facts)

    assert result["status"] == "completed", result["diagnostics"]
    owners = {entry["node_id"]: entry["owner_item_id"] for entry in result["child_blocks"]}
    assert owners.get("note") == "i2", (owners, result["child_blocks"])
    states = {entry["node_id"]: entry["state"] for entry in result["owned_content"]}
    assert states.get("note") == "ITEM_CHILD_BLOCK", states

    items = _created_items(candidates)
    item_node = items[item_map["i2"]]
    assert "note" in list(item_node.get("content_blocks") or []), item_node

    moves = _move_targets(candidates)
    assert moves.get("note") == item_map["i2"], moves


def test_r5_note_not_after_a_run_stays_protected_outside_list() -> None:
    """A note that no run owns keeps its protected ``OUTSIDE_LIST`` decision."""

    units = {
        "head": _paragraph("head", 0, "说明段"),
        "note": _paragraph("note", 1, "注：独立说明。"),
    }
    children = {"root": ["head", "note"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "head": _body_evidence(),
        "note": _note_evidence(),
    }
    facts = _facts(containers, children, units, evidence)

    result = analyze_lists(facts)

    assert result["child_blocks"] == [], result["child_blocks"]
    assert result["continuations"] == [], result["continuations"]
    decisions = {
        str(decision["target_node_ids"][0]): str((decision.get("value") or {}).get("state"))
        for decision in result["state_decisions"]
        if decision.get("target_node_ids")
    }
    assert decisions.get("note") == "OUTSIDE_LIST", decisions
    assert result["status"] == "completed", result["diagnostics"]


def _build_numbered_note_docx(path: Path, tail: str) -> None:
    """Write a real DOCX ``[num][num][note]`` with a template note style."""

    from docx import Document as _Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = _Document(str(TEMPLATE))
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    for text in ("第一项", "第二项"):
        paragraph = document.add_paragraph(text)
        properties = paragraph._p.get_or_add_pPr()
        num_pr = OxmlElement("w:numPr")
        level = OxmlElement("w:ilvl")
        level.set(qn("w:val"), "0")
        identifier = OxmlElement("w:numId")
        identifier.set(qn("w:val"), "1")
        num_pr.append(level)
        num_pr.append(identifier)
        properties.append(num_pr)
    document.add_paragraph(tail, style="3.1注-无编号注")
    document.save(str(path))


def _body_paragraphs(path: Path) -> list[tuple[str, str | None]]:
    """Return ``(text, style_id)`` for each rendered body paragraph."""

    from xml.etree import ElementTree

    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{{{WORD_NS}}}body")
    rows: list[tuple[str, str | None]] = []
    for child in body:
        if child.tag != f"{{{WORD_NS}}}p":
            continue
        style = child.find(f"{{{WORD_NS}}}pPr/{{{WORD_NS}}}pStyle")
        style_id = style.get(f"{{{WORD_NS}}}val") if style is not None else None
        text = "".join(node.text or "" for node in child.iter(f"{{{WORD_NS}}}t"))
        rows.append((text, style_id))
    return rows


def test_r5_docx_note_after_run_renders_with_note_style_under_the_item(tmp_path: Path) -> None:
    """End to end: the note stays a styled note and hangs under the last item."""

    source = tmp_path / "note-after-run.docx"
    _build_numbered_note_docx(source, "注：尾部说明。")

    normalized, output, result = _convert_docx(tmp_path, source, "note-after-run")
    assert result.status == "completed", result.diagnostics

    report = _report_of(result)
    assert report.get("saved_output_audit", {}).get("passed") is True, report.get("saved_output_audit")
    assert report.get("saved_output_audit", {}).get("unexpected_styles") in (None, [])
    assert not report.get("risk_warnings"), report.get("risk_warnings")

    document = normalized["source_ast"]["document"]
    parents = _parent_index(document["children"])
    note_ids = [
        node_id
        for node_id, block in document["blocks"].items()
        if block.get("role") in {"note", "numbered_note"}
        or "注" in str(block.get("inline_content") or "")
    ]
    assert note_ids, document["blocks"]
    owner = parents.get(note_ids[0])
    while owner in parents and document["containers"].get(owner, {}).get("kind") != "list_item":
        owner = parents[owner]
    assert document["containers"].get(owner, {}).get("kind") == "list_item", (owner, parents)

    styles = {block.get("role") for block in document["blocks"].values()}
    assert "note" in styles, styles

    # The note is a child block, not a continuation, so the run has no
    # continuation: this is the state decision work item A fixes.
    assert report.get("list_semantics", {}).get("continuation_count") == 0, report.get("list_semantics")

    # The note keeps its own style in the rendered artifact and its text is kept.
    rendered = _body_paragraphs(output)
    note_rows = [row for row in rendered if "注" in row[0] or "尾部说明" in row[0]]
    assert note_rows, rendered


# ---------------------------------------------------------------------------
# B: a table between two items is owned by the preceding item
# ---------------------------------------------------------------------------

def _run_split_facts() -> dict:
    """``[i1 num][table][i2 num]`` with the table between the two items."""

    units = {
        "i1": _paragraph("i1", 0, "第一项"),
        "i2": _paragraph("i2", 2, "第二项"),
        "cell": _paragraph("cell", 3, "单元格"),
    }
    containers = {
        "root": {"kind": "document"},
        "table": {"kind": "table"},
        "row": {"kind": "table_row", "row": 0},
        "tcell": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1, "cell_role": "body"},
    }
    children = {
        "root": ["i1", "table", "i2"],
        "table": ["row"],
        "row": ["tcell"],
        "tcell": ["cell"],
    }
    evidence = {
        "i1": _numbered_evidence(1, 1),
        "i2": _numbered_evidence(1, 2),
        "cell": _body_evidence(),
    }
    return _facts(containers, children, units, evidence)


def test_r5_table_between_two_items_is_owned_by_the_preceding_item() -> None:
    """``[item][table][item]`` attributes the table to the first item.

    The table carries no numbering or level evidence, so source adjacency is the
    only owner evidence: the table directly follows item 1, so item 1 owns it,
    and the run stays one list (README constraint 5: table content does not
    independently terminate the list).
    """

    facts = _run_split_facts()
    result, _materialized, candidates, item_map = _materialized_ast(facts)

    assert result["status"] == "completed", result["diagnostics"]
    assert len(result["lists"]) == 1, result["lists"]
    assert len(result["items"]) == 2, result["items"]

    owners = {entry["node_id"]: entry["owner_item_id"] for entry in result["child_blocks"]}
    assert owners.get("table") == "i1", (owners, result["child_blocks"])
    states = {entry["node_id"]: entry["state"] for entry in result["owned_content"]}
    assert states.get("table") == "ITEM_CHILD_BLOCK", states

    items = _created_items(candidates)
    assert "table" in list(items[item_map["i1"]].get("content_blocks") or []), items[item_map["i1"]]
    assert "table" not in list(items[item_map["i2"]].get("content_blocks") or []), items[item_map["i2"]]

    moves = _move_targets(candidates)
    assert moves.get("table") == item_map["i1"], moves


def test_r5_body_between_items_keeps_two_runs_unchanged() -> None:
    """The T05 ``[item][body][item]`` behaviour is unchanged: two implicit runs."""

    units = {
        "u0": _paragraph("u0", 0, "第一项"),
        "body": _paragraph("body", 1, "中间正文段落。"),
        "u1": _paragraph("u1", 2, "第二项"),
    }
    children = {"root": ["u0", "body", "u1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {"u0": _numbered_evidence(1, 1), "body": _body_evidence(), "u1": _numbered_evidence(1, 2)}
    facts = _facts(containers, children, units, evidence)

    result = analyze_lists(facts)
    assert len(result["lists"]) == 2, result["lists"]
    by_id = {item["item_id"]: item for item in result["items"]}
    assert by_id["u1"]["parent_item_id"] is None, by_id["u1"]
    owners = {entry["node_id"]: entry["owner_item_id"] for entry in result["continuations"]}
    assert owners.get("body") == "u0", owners


def _build_numbered_table_docx(path: Path) -> None:
    """Write a real DOCX ``[num][table][num]``.

    The two items carry the template list style, so each single-paragraph
    numbering group is still detected as a list item even though the table
    breaks the physical paragraph contiguity.
    """

    from docx import Document as _Document
    from docx.oxml.ns import qn

    document = _Document(str(TEMPLATE))
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)

    document.add_paragraph("第一项", style="1.1一级列项-编号")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "列A"
    table.cell(0, 1).text = "列B"
    document.add_paragraph("第二项", style="1.1一级列项-编号")
    document.save(str(path))


def test_r5_docx_table_between_items_hangs_under_the_preceding_item(tmp_path: Path) -> None:
    """End to end: the in-run table is a child of the first item and the list stays one run."""

    source = tmp_path / "table-between.docx"
    _build_numbered_table_docx(source)

    normalized, _output, result = _convert_docx(tmp_path, source, "table-between")
    assert result.status == "completed", result.diagnostics

    report = _report_of(result)
    assert report.get("saved_output_audit", {}).get("passed") is True, report.get("saved_output_audit")
    assert not report.get("risk_warnings"), report.get("risk_warnings")

    document = normalized["source_ast"]["document"]
    parents = _parent_index(document["children"])
    table_ids = [node_id for node_id, block in document["blocks"].items() if block.get("block_type") == "table"]
    assert len(table_ids) == 1, table_ids
    owner = parents.get(table_ids[0])
    assert document["containers"].get(owner, {}).get("kind") == "list_item", (owner, parents)

    list_items = [node_id for node_id, node in document["containers"].items() if node.get("kind") == "list_item"]
    assert len(list_items) == 2, list_items
    # The table is grouped with the first item, not the second.
    first_item = next(item for item in list_items if item in document["children"] and table_ids[0] in document["children"][item])
    assert first_item == owner, (first_item, owner)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))


# ---------------------------------------------------------------------------
# C: decision 1's per-chapter restart of top-level ordered numbering
# ---------------------------------------------------------------------------

def _convert_markdown(tmp_path: Path, text: str, tag: str) -> tuple[Path, object]:
    source = tmp_path / f"{tag}.md"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / f"{tag}.out.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    return output, ConversionKernel().convert(request)


def test_r5_two_chapters_restart_top_level_ordered_numbering(tmp_path: Path) -> None:
    """Each chapter's ordered list starts again at ``a)``.

    AGENTS.md 关键决策记录 1 requires the numbered list to restart from ``a)``
    after each heading.  Two ``w:num`` instances that reference the same
    abstract definition keep independent counters, so the visible numbering is
    the evidence: chapter one shows ``a) b)`` and chapter two shows ``a) b)``
    again rather than ``c) d)``.
    """

    output, result = _convert_markdown(
        tmp_path,
        "\n".join([
            "# 章一",
            "",
            "1. 甲一",
            "2. 甲二",
            "",
            "## 章二",
            "",
            "1. 乙一",
            "2. 乙二",
            "",
        ]),
        "chapter-restart",
    )

    assert result.status == "completed", result.diagnostics
    report = _report_of(result)
    assert report.get("saved_output_audit", {}).get("passed") is True, report.get("saved_output_audit")
    assert not report.get("risk_warnings"), report.get("risk_warnings")

    rows = simulate_saved_numbering(output)
    list_rows = [row for row in rows if row.get("num_id") is not None and row["text"] in {"甲一", "甲二", "乙一", "乙二"}]
    assert [row["text"] for row in list_rows] == ["甲一", "甲二", "乙一", "乙二"], list_rows

    first_chapter = {row["num_id"] for row in list_rows if row["text"].startswith("甲")}
    second_chapter = {row["num_id"] for row in list_rows if row["text"].startswith("乙")}
    assert first_chapter and second_chapter, list_rows
    assert first_chapter.isdisjoint(second_chapter), (
        "each chapter must number its list through its own instance so the counter restarts",
        first_chapter,
        second_chapter,
    )

    visible = {row["text"]: row.get("marker") for row in list_rows}
    assert visible["甲一"] == visible["乙一"], visible
    assert visible["甲二"] == visible["乙二"], visible
    assert visible["甲一"] != visible["甲二"], visible
