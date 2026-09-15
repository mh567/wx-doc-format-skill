"""T04 end-to-end tests for multi-level nesting and parent arbitration.

These tests pin the behaviour the T04 ticket demands:

  * a sub-list that starts a NEW ``numId`` at ``ilvl=0`` is recognised as a
    CHILD of the preceding parent item, not as a sibling;
  * multi-level ordered / unordered / cross nesting and nesting with body
    paragraphs interleaved converts with the correct list tree;
  * constraints 3 (deep items need a parent) and 4 (stepwise level increase)
    are validated for explicit list containers too, not only implicit runs;
  * a child that appears before its parent is degraded and reported
    explicitly instead of being attached to the nearest parent;
  * the saved-DOCX readback compares the parent chain, so a saved document
    whose parent chain drifted from the AST list tree is reported;
  * candidate states are arbitrated on evidence class, with no bespoke
    ``score`` field that neither participates nor is documented.

Fixture naming follows the T01 convention ``t04_<scenario>_<variant>``.
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, ZipFile

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.source_ast import build_source_ast, materialize_list_semantics

from test_modular_normalization import _ast, _profile


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": WORD_NS}


def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


def _paragraph(unit_id: str, index: int, text: str, *, style: str | None = None) -> dict:
    unit = {
        "kind": "paragraph",
        "source_ref": _ref(index),
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
    }
    if style is not None:
        unit["style"] = style
    return unit


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


# ---------------------------------------------------------------------------
# Fixtures: builders and readers
# ---------------------------------------------------------------------------

def _build_numbered_docx(path: Path, spec: list[tuple[str, int | None, int]]) -> None:
    """Write a real DOCX whose paragraphs carry a direct ``w:numPr``.

    ``spec`` is a list of ``(text, num_id, ilvl)``; ``num_id`` ``None`` writes a
    plain body paragraph.
    """

    from docx import Document as _Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    document = _Document(str(TEMPLATE))
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    for text, num_id, ilvl in spec:
        paragraph = document.add_paragraph(text)
        if num_id is None:
            continue
        properties = paragraph._p.get_or_add_pPr()
        num_pr = OxmlElement("w:numPr")
        level = OxmlElement("w:ilvl")
        level.set(qn("w:val"), str(ilvl))
        identifier = OxmlElement("w:numId")
        identifier.set(qn("w:val"), str(num_id))
        num_pr.append(level)
        num_pr.append(identifier)
        properties.append(num_pr)
    document.save(str(path))


def _convert_docx(tmp_path: Path, source: Path, tag: str) -> tuple[dict, Path, object]:
    import json

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


def _convert_markdown(tmp_path: Path, text: str) -> tuple[dict, Path, object]:
    import json

    source = tmp_path / "input.md"
    source.write_text(text, encoding="utf-8")
    output = tmp_path / "output.docx"
    normalized_path = tmp_path / "normalized.json"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"normalized_ast_path": normalized_path, "llm_enhance": "off"},
    )
    result = ConversionKernel().convert(request)
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    return normalized, output, result


def _direct_numbering(path: Path) -> list[tuple[str, str | None, str | None]]:
    """Return (text, numId, ilvl) for every paragraph carrying a direct numPr."""

    from xml.etree import ElementTree

    rows: list[tuple[str, str | None, str | None]] = []
    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    for paragraph in root.iter(f"{{{WORD_NS}}}p"):
        num_pr = paragraph.find("w:pPr/w:numPr", _NS)
        if num_pr is None:
            continue
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t"))
        num_id = num_pr.find("w:numId", _NS)
        ilvl = num_pr.find("w:ilvl", _NS)
        rows.append((
            text,
            num_id.get(f"{{{WORD_NS}}}val") if num_id is not None else None,
            ilvl.get(f"{{{WORD_NS}}}val") if ilvl is not None else None,
        ))
    return rows


def _block_text(block: Mapping[str, Any]) -> str:
    return "".join(
        str(item.get("text") or "")
        for item in block.get("inline_content", [])
        if isinstance(item, Mapping) and item.get("kind") == "text"
    )


def _list_depths(normalized: Mapping[str, Any]) -> dict[str, int]:
    """Return ``{paragraph text: list-container nesting depth}``.

    The depth of a paragraph is the number of ``list`` containers among its
    ancestors, which is exactly the semantic nesting the tree must express.
    """

    document = normalized.get("source_ast", {}).get("document", {})
    blocks = document.get("blocks", {})
    children = document.get("children", {})
    containers = document.get("containers", {})
    parents = {
        child_id: parent_id
        for parent_id, child_ids in children.items()
        for child_id in child_ids
    }

    def depth(node_id: str) -> int:
        count = 0
        current = node_id
        while current in parents:
            current = parents[current]
            if containers.get(current, {}).get("kind") == "list":
                count += 1
        return count

    depths: dict[str, int] = {}
    for node_id, block in blocks.items():
        if not isinstance(block, Mapping) or block.get("block_type") != "paragraph":
            continue
        text = _block_text(block)
        node_depth = depth(node_id)
        if text and node_depth > 0:
            depths[text] = node_depth
    return depths


def _rewrite_document_xml(path: Path, transform) -> None:
    from xml.etree import ElementTree

    temporary = path.with_suffix(".tmp.docx")
    with ZipFile(path) as archive, ZipFile(temporary, "w", ZIP_DEFLATED) as rewritten:
        for item in archive.infolist():
            data = archive.read(item.filename)
            if item.filename == "word/document.xml":
                root = ElementTree.fromstring(data)
                transform(root)
                data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
            rewritten.writestr(item, data)
    os.replace(temporary, path)


def _level_aware_profile() -> dict:
    # ``list_decimal`` points at numbering instance 2 of the real template,
    # whose abstract definition carries nine levels, so level 1 is expressible.
    return {
        "resolved_styles": dict(_profile()["resolved_styles"]),
        "numbering_ids": {"list_letter": 3, "list_decimal": 2, "list_dash": 5, "list_bullet": 4},
    }


def _two_level_ordered_ast() -> dict:
    ast = copy.deepcopy(_ast())
    ast["document"]["containers"]["l2"]["list_kind"] = "ordered"
    ast["document"]["containers"]["i2"]["source_marker"] = "1."
    return ast


def _three_level_ordered_ast() -> dict:
    ast = _two_level_ordered_ast()
    ast["document"]["containers"]["l3"] = {
        "kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s3",
    }
    ast["document"]["containers"]["i4"] = {
        "kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "s3",
    }
    ast["document"]["blocks"]["p4"] = {
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [{"kind": "text", "text": "三级项", "marks": []}],
        "source_ref": _ref(4),
    }
    ast["document"]["children"]["i2"] = ["p2", "l3"]
    ast["document"]["children"]["l3"] = ["i4"]
    ast["document"]["children"]["i4"] = ["p4"]
    return ast


# ---------------------------------------------------------------------------
# Work item 2: a new numId at ilvl=0 starts a nested sub-list
# ---------------------------------------------------------------------------

def test_t04_sublist_new_numid_starts_a_child_not_a_sibling() -> None:
    """A deeper numId-at-ilvl=0 run is a nested sub-list, not a sibling run."""

    units = {
        "p1": _paragraph("p1", 0, "父项"),
        "c1": _paragraph("c1", 1, "子项一"),
        "c2": _paragraph("c2", 2, "子项二"),
        "g1": _paragraph("g1", 3, "孙项一"),
        "g2": _paragraph("g2", 4, "孙项二"),
    }
    children = {"root": ["p1", "c1", "c2", "g1", "g2"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "p1": _numbered_evidence(1, 1),
        "c1": _numbered_evidence(2, 1, fmt="lower_letter"),
        "c2": _numbered_evidence(2, 2, fmt="lower_letter"),
        "g1": _numbered_evidence(3, 1, fmt="lower_roman"),
        "g2": _numbered_evidence(3, 2, fmt="lower_roman"),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    by_id = {item["item_id"]: item for item in result["items"]}

    assert by_id["c1"]["parent_item_id"] == "p1", by_id["c1"]
    assert by_id["c2"]["parent_item_id"] == "p1", by_id["c2"]
    assert by_id["c1"]["semantic_level"] == 1, by_id["c1"]
    assert by_id["g1"]["parent_item_id"] == "c2", by_id["g1"]
    assert by_id["g2"]["parent_item_id"] == "c2", by_id["g2"]
    assert by_id["g1"]["semantic_level"] == 2, by_id["g1"]


# ---------------------------------------------------------------------------
# Work item 4: multi-level end-to-end, tree + numbering
# ---------------------------------------------------------------------------

def test_t04_sublist_new_numid_end_to_end(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    source = tmp_path / "sublist.docx"
    _build_numbered_docx(source, [
        ("父项一", 1, 0),
        ("父项二", 1, 0),
        ("子项一", 2, 0),
        ("子项二", 2, 0),
    ])

    normalized, output, result = _convert_docx(tmp_path, source, "sublist")
    assert result.status == "completed", result.diagnostics

    depths = _list_depths(normalized)
    assert depths["父项一"] == 1, depths
    assert depths["父项二"] == 1, depths
    assert depths["子项一"] == 2, depths
    assert depths["子项二"] == 2, depths

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]


def test_t04_three_level_nesting_end_to_end(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    source = tmp_path / "three-level.docx"
    _build_numbered_docx(source, [
        ("父项一", 1, 0),
        ("父项二", 1, 0),
        ("子项一", 2, 0),
        ("子项二", 2, 0),
        ("孙项一", 3, 0),
        ("孙项二", 3, 0),
    ])

    normalized, output, result = _convert_docx(tmp_path, source, "three-level")
    assert result.status == "completed", result.diagnostics

    depths = _list_depths(normalized)
    assert depths["父项一"] == 1, depths
    assert depths["父项二"] == 1, depths
    assert depths["子项一"] == 2, depths
    assert depths["子项二"] == 2, depths
    assert depths["孙项一"] == 3, depths
    assert depths["孙项二"] == 3, depths

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]


def test_t04_markdown_multi_level_nesting_end_to_end(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 标题\n\n"
        "1. 一级 A\n"
        "   1. 二级 A1\n"
        "      1. 三级 A11\n"
        "   2. 二级 A2\n"
        "2. 一级 B\n\n"
        "对照正文段落。\n\n"
        "- 无序一\n"
        "  1. 有序子项\n"
        "  - 无序二\n",
    )
    assert result.status == "completed", result.diagnostics

    depths = _list_depths(normalized)
    assert depths["一级 A"] == 1, depths
    assert depths["二级 A1"] == 2, depths
    assert depths["三级 A11"] == 3, depths
    assert depths["二级 A2"] == 2, depths
    assert depths["一级 B"] == 1, depths
    assert depths["无序一"] == 1, depths
    assert depths["有序子项"] == 2, depths
    assert depths["无序二"] == 2, depths
    assert "对照正文段落。" not in depths, depths

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]


# ---------------------------------------------------------------------------
# Constraint coverage gap: explicit list containers are validated too
# ---------------------------------------------------------------------------

def _constraint_codes(result: dict) -> list[tuple[int, str]]:
    return [(int(item["constraint"]), str(item["code"])) for item in result["state_constraint_findings"]]


def test_t04_explicit_nested_list_without_parent_item_is_reported() -> None:
    """A nested list that does not hang from a list_item breaks constraint 3.

    ``root -> listA -> listB -> itemB`` gives ``listB`` a structural level of 1
    but no level-0 parent item, so the item is a deep item without a parent and
    must be reported even though it lives in an explicit container.
    """

    units = {"pB": _paragraph("pB", 0, "子项")}
    containers = {
        "root": {"kind": "document"},
        "listA": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "sA"},
        "listB": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "sB"},
        "itemB": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "sB"},
    }
    children = {"root": ["listA"], "listA": ["listB"], "listB": ["itemB"], "itemB": ["pB"]}
    evidence = {
        "itemB": _numbered_evidence(2, 1),
        "pB": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
    }

    result = analyze_lists(_facts(containers, children, units, evidence))

    assert (3, "list_constraint_violation") in _constraint_codes(result), _constraint_codes(result)
    finding = next(item for item in result["state_constraint_findings"] if item["constraint"] == 3)
    assert finding["node_id"] == "itemB"


# ---------------------------------------------------------------------------
# Work item 3: a child before its parent is degraded and reported
# ---------------------------------------------------------------------------

def test_t04_child_before_parent_is_reported() -> None:
    """``[level-1 child][level-0 parent]`` reports the missing parent ordering.

    The child has no preceding parent, so it is degraded to the local list
    root; the parent that appears afterwards is reported as late instead of the
    child being silently attached to the nearest later item.
    """

    units = {
        "child": _paragraph("child", 0, "子项"),
        "parent": _paragraph("parent", 1, "父项"),
    }
    children = {"root": ["child", "parent"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "child": _numbered_evidence(1, 1, depth=1, fmt="lower_letter"),
        "parent": _numbered_evidence(1, 1),
    }

    result = analyze_lists(_facts(containers, children, units, evidence))
    codes = [item["code"] for item in result["diagnostics"]]

    assert "late_list_parent" in codes, codes
    late = next(item for item in result["diagnostics"] if item["code"] == "late_list_parent")
    assert late["node_id"] == "child"
    assert late["declared_level"] == 1
    # The child is degraded instead of being attached to the nearest item.
    by_id = {item["item_id"]: item for item in result["items"]}
    assert by_id["child"]["parent_item_id"] is None, by_id["child"]


# ---------------------------------------------------------------------------
# Tree-level readback: the parent chain is compared
# ---------------------------------------------------------------------------

def test_t04_readback_detects_wrong_parent_chain(tmp_path: Path) -> None:
    from wxdf.auditing import audit_list_readback
    from wxdf.normalization import normalize
    from wxdf.rendering import render

    normalized = normalize(_two_level_ordered_ast(), _level_aware_profile(), strategy="legacy")
    assert normalized["presentation"]["nodes"]["i2"]["target_level"] == 1
    output = tmp_path / "parent-chain.docx"
    render(normalized, output, template_path=TEMPLATE)
    assert audit_list_readback(output, normalized)["passed"] is True

    def flatten_child(root):
        for ilvl in root.iter(f"{{{WORD_NS}}}ilvl"):
            ilvl.set(f"{{{WORD_NS}}}val", "0")

    _rewrite_document_xml(output, flatten_child)
    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is False
    codes = [issue["code"] for issue in comparison["issues"]]
    assert "list_parent_chain_mismatch" in codes, codes


# ---------------------------------------------------------------------------
# Candidate score: arbitrated on evidence class, no inert score field
# ---------------------------------------------------------------------------

def test_t04_state_candidates_are_arbitrated_on_evidence_class() -> None:
    from wxdf.semantics.lists import _arbitrate_state_candidates, _state_candidate

    units = {"i1": _paragraph("i1", 0, "第一项")}
    children = {"root": ["i1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {"i1": _numbered_evidence(1, 1)}

    result = analyze_lists(_facts(containers, children, units, evidence))

    # No candidate carries a bespoke, inert ``score`` field.
    assert not any("score" in candidate for candidate in result["state_candidates"]), (
        "candidate states must not carry an unconsumed score field"
    )

    # Arbitration follows the evidence class the resolver already ranks on: a
    # structural candidate outranks a contextual alternative.
    decisions, unresolved = _arbitrate_state_candidates([
        _state_candidate("i1", "OUTSIDE_LIST", positive_evidence=[{"kind": "contextual"}]),
        _state_candidate("i1", "LIST_ITEM", positive_evidence=[{"kind": "structural"}]),
    ])
    assert unresolved == [], unresolved
    assert (decisions[0].get("value") or {}).get("state") == "LIST_ITEM", decisions


def test_t04_three_level_ordered_numbering_is_level_aware(tmp_path: Path) -> None:
    """A three-level ordered tree renders real per-level ``w:ilvl`` values.

    The product default policy pins ordered items to level 0 (T08 owns that), so
    this pins the capability the pipeline has once the policy lets the render
    levels through: the saved ``w:ilvl`` follows the tree and the readback parent
    chain matches the AST.
    """

    from wxdf.auditing import audit_list_readback
    from wxdf.normalization import normalize
    from wxdf.rendering import render

    normalized = normalize(_three_level_ordered_ast(), _level_aware_profile(), strategy="legacy")
    levels = {node_id: node["target_level"] for node_id, node in normalized["presentation"]["nodes"].items()}
    assert levels == {"i1": 0, "i2": 1, "i3": 0, "i4": 2}, levels

    output = tmp_path / "three-level-ordered.docx"
    render(normalized, output, template_path=TEMPLATE)

    rows = _direct_numbering(output)
    assert [ilvl for _, _, ilvl in rows] == ["0", "1", "2", "0"], rows
    assert [num_id for _, num_id, _ in rows] == ["3", "2", "2", "3"], rows

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]
    assert comparison["expected_parent_chain"] == [None, 0, 1, None], comparison["expected_parent_chain"]


def test_t04_source_ast_materializes_child_list_under_parent_item() -> None:
    """The materialized AST nests the sub-list container under the parent item."""

    units = {
        "p1": _paragraph("p1", 0, "父项"),
        "c1": _paragraph("c1", 1, "子项"),
    }
    children = {"root": ["p1", "c1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "p1": _numbered_evidence(1, 1),
        "c1": _numbered_evidence(2, 1, fmt="lower_letter"),
    }
    facts = _facts(containers, children, units, evidence)
    result = analyze_lists(facts)
    ast = build_source_ast(facts)

    _materialized, candidates, item_map = materialize_list_semantics(ast, facts, result)

    def create_nodes(kind: str) -> list[dict]:
        return [
            candidate["proposal"]["value"]
            for candidate in candidates
            if candidate.get("proposal", {}).get("operation") == "create_node"
            and isinstance(candidate["proposal"].get("value"), Mapping)
            and isinstance(candidate["proposal"]["value"].get("node"), Mapping)
            and candidate["proposal"]["value"]["node"].get("kind") == kind
        ]

    parent_item = item_map["p1"]
    child_lists = [value for value in create_nodes("list") if value.get("parent_id") == parent_item]
    assert len(child_lists) == 1, create_nodes("list")
    child_list_id = child_lists[0]["node_id"]
    child_items = [value for value in create_nodes("list_item") if value.get("parent_id") == child_list_id]
    assert [value["node_id"] for value in child_items] == [item_map["c1"]], child_items


def test_t04_multi_level_unordered_nesting_end_to_end(tmp_path: Path) -> None:
    """Bullets nested three deep keep one child per level.

    T04 work item 4 requires multi-level UNORDERED nesting as well as ordered
    and cross nesting.  Each bullet is its own item, so the tree must be one
    single-child list per level rather than a flat sibling run.
    """

    normalized, output, result = _convert_markdown(
        tmp_path,
        "\n".join([
            "- 一级一",
            "  - 二级一",
            "    - 三级一",
            "- 一级二",
            "",
        ]),
    )

    assert result.status == "completed", result.diagnostics
    assert normalized["source_ast"]["document"]["resources"] is not None
    depths = _list_depths(normalized)
    assert depths == {"一级一": 1, "二级一": 2, "三级一": 3, "一级二": 1}, depths
    assert [row[0] for row in _direct_numbering(output)] == [
        "一级一",
        "二级一",
        "三级一",
        "一级二",
    ]


def test_t04_created_container_preserves_source_order_for_three_or_more_items(tmp_path: Path) -> None:
    """A created list container must keep source order for three or more items.

    Structural children created in one batch are inserted into an empty child
    list, so they have to be applied in ascending index order.  Descending
    order anchors correctly against pre-existing siblings but scrambles a newly
    created container once it holds three or more items.  This pins the
    ordering so a future revert cannot reintroduce the scramble silently.
    """

    source = tmp_path / "flat-four.docx"
    _build_numbered_docx(source, [
        ("第一项", 1, 0),
        ("第二项", 1, 0),
        ("第三项", 1, 0),
        ("第四项", 1, 0),
    ])

    _, output, result = _convert_docx(tmp_path, source, "flat-four")

    assert result.status == "completed", result.diagnostics
    assert [row[0] for row in _direct_numbering(output)] == [
        "第一项",
        "第二项",
        "第三项",
        "第四项",
    ]
    saved_texts = [text for text in _saved_paragraph_texts(output) if text in {
        "第一项",
        "第二项",
        "第三项",
        "第四项",
    }]
    assert saved_texts == ["第一项", "第二项", "第三项", "第四项"]


def _saved_paragraph_texts(path: Path) -> list[str]:
    """Return every paragraph text of the saved document in document order."""

    from xml.etree import ElementTree

    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    return [
        "".join(node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t"))
        for paragraph in root.iter(f"{{{WORD_NS}}}p")
    ]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
