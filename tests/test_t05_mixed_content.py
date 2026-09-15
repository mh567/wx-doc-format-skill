"""T05 end-to-end tests for list-item mixed content and content ownership.

These tests pin the behaviour the T05 ticket demands on top of T04:

  * a list item may own heterogeneous content blocks
    (paragraph / table / image / quote / code / formula / note), and the
    ownership is recorded on the materialized ``list_item`` node as
    ``content_blocks`` / ``child_lists``;
  * an implicit DOCX list run keeps a tail continuation: a body paragraph that
    follows the last item is attributed back to that item through
    ``owner_item_id`` instead of staying an orphan body paragraph;
  * a continuation paragraph that follows a CHILD list is attributed back to the
    correct (level-0) list item, and a non-paragraph block that follows the run
    hangs under that item in the AST;
  * the ``list_item_map`` seam (source item id -> owning native list container)
    keeps its shape so the appendix / table / caption / structure modules read
    the same ownership.

Fixture naming follows the T01 convention ``t05_<scenario>_<variant>``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from zipfile import ZIP_DEFLATED, ZipFile


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.source_ast import build_source_ast, materialize_list_semantics


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": WORD_NS}


# ---------------------------------------------------------------------------
# Fixture builders
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


def _build_numbered_docx(path: Path, spec: list[tuple[str, int | None, int]]) -> None:
    """Write a real DOCX whose paragraphs carry a direct ``w:numPr``."""

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


def _created_lists(candidates: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    lists: dict[str, Mapping[str, Any]] = {}
    for candidate in candidates:
        proposal = candidate.get("proposal") or {}
        value = proposal.get("value") if isinstance(proposal.get("value"), Mapping) else {}
        node = value.get("node") if isinstance(value, Mapping) else None
        if proposal.get("operation") == "create_node" and isinstance(node, Mapping) and node.get("kind") == "list":
            lists[str(value.get("node_id"))] = value
    return lists


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
    return {
        child_id: parent_id
        for parent_id, child_ids in children.items()
        for child_id in child_ids
    }


# ---------------------------------------------------------------------------
# Red light: materialized content ownership for a DOCX-derived list item
# ---------------------------------------------------------------------------

def test_t05_docx_item_owns_table_image_quote_code_content() -> None:
    """A DOCX-derived list item owns paragraph / table / image / quote / code.

    The item's ``content_blocks`` must list every block the list semantics
    attributed to it, including the materialized image node, the table, the
    quote container and the code unit, so the AST records
    "this item owns mixed content".
    """

    units = {
        "i1": _paragraph("i1", 0, "第一项"),
        "cell": _paragraph("cell", 1, "单元格"),
        "qtext": _paragraph("qtext", 2, "引用内容"),
        "code": {"kind": "code_block", "source_ref": _ref(3), "raw_text": "code"},
        "imgp": {
            "kind": "paragraph",
            "source_ref": _ref(4),
            "inline_content": [{"kind": "image", "resource_id": "r1", "alt_text": "图"}],
        },
    }
    containers = {
        "root": {"kind": "document"},
        "table": {"kind": "table"},
        "row": {"kind": "table_row", "row": 0},
        "tcell": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1, "cell_role": "body"},
        "quote": {"kind": "quote"},
    }
    children = {
        "root": ["i1", "table", "quote", "code", "imgp"],
        "table": ["row"],
        "row": ["tcell"],
        "tcell": ["cell"],
        "quote": ["qtext"],
    }
    evidence = {
        "i1": _numbered_evidence(1, 1),
        "cell": {"marker_kind": None, "explicit_depth": 0},
        "qtext": {"marker_kind": None, "explicit_depth": 0},
        "code": {"marker_kind": None, "explicit_depth": 0},
        "imgp": {"marker_kind": None, "explicit_depth": 0},
    }
    facts = _facts(containers, children, units, evidence)
    facts["resources"] = {
        "r1": {
            "kind": "image",
            "source_ref": _ref(4),
            "content_sha256": "sha256:" + "c" * 64,
            "media_type": "image/png",
        }
    }

    _result, _materialized, candidates, item_map = _materialized_ast(facts)
    items = _created_items(candidates)
    item_node = items[item_map["i1"]]

    content_blocks = list(item_node.get("content_blocks") or [])
    # The ownership record is in source order: the table / quote / code blocks
    # keep their interleaved source slots instead of being concatenated after
    # every paragraph, and the materialized image stays next to its paragraph.
    assert content_blocks == ["i1", "table", "quote", "code", "imgp", "imgp-image"], content_blocks
    assert item_node.get("child_lists") == []

    moves = _move_targets(candidates)
    owner = item_map["i1"]
    for node_id in ("table", "quote", "code", "imgp", "imgp-image"):
        assert moves.get(node_id) == owner, (node_id, moves)


def test_t05_implicit_run_owns_tail_blocks() -> None:
    """An implicit DOCX run attributes its tail blocks to the correct item.

    ``[item num1][item num1][body]`` must record the trailing body paragraph as
    a continuation owned by the second item, and the item node must list it in
    ``content_blocks``.
    """

    units = {
        "i1": _paragraph("i1", 0, "第一项"),
        "i2": _paragraph("i2", 1, "第二项"),
        "tail": _paragraph("tail", 2, "列表后的续接正文"),
    }
    children = {"root": ["i1", "i2", "tail"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "i1": _numbered_evidence(1, 1),
        "i2": _numbered_evidence(1, 2),
        "tail": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
    }
    facts = _facts(containers, children, units, evidence)

    result, _materialized, candidates, item_map = _materialized_ast(facts)

    continuations = result["continuations"]
    owners = {entry["node_id"]: entry["owner_item_id"] for entry in continuations}
    assert owners.get("tail") == "i2", owners
    assert result["status"] == "completed", result["diagnostics"]

    items = _created_items(candidates)
    item_node = items[item_map["i2"]]
    assert "tail" in list(item_node.get("content_blocks") or []), item_node


def test_t05_continuation_after_child_list_belongs_to_parent_item() -> None:
    """A continuation paragraph after a CHILD list belongs to the parent item.

    ``[parent num1][child num2][continuation]`` nests the child under the parent
    (T04) and the continuation paragraph that follows the child list is owned by
    the parent item, not by the last child item and not by the document root.
    """

    units = {
        "parent": _paragraph("parent", 0, "父项"),
        "child": _paragraph("child", 1, "子项"),
        "cont": _paragraph("cont", 2, "子列表后的续接正文"),
    }
    children = {"root": ["parent", "child", "cont"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "parent": _numbered_evidence(1, 1),
        "child": _numbered_evidence(2, 1, fmt="lower_letter"),
        "cont": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
    }
    facts = _facts(containers, children, units, evidence)

    result, _materialized, candidates, item_map = _materialized_ast(facts)

    by_id = {item["item_id"]: item for item in result["items"]}
    assert by_id["child"]["parent_item_id"] == "parent", by_id["child"]

    owners = {entry["node_id"]: entry["owner_item_id"] for entry in result["continuations"]}
    assert owners.get("cont") == "parent", owners

    moves = _move_targets(candidates)
    assert moves.get("cont") == item_map["parent"], moves


def test_t05_tail_table_hangs_under_the_item(tmp_path: Path) -> None:
    """A table that follows an implicit list run hangs under the preceding item.

    The real DOCX conversion ``[item][item][table]`` must materialize the table
    as a child of the last list item instead of a document-root sibling.
    """

    source = tmp_path / "tail-table.docx"
    _build_numbered_docx(source, [("第一项", 1, 0), ("第二项", 1, 0)])

    from docx import Document as _Document

    document = _Document(str(source))
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "列A"
    table.cell(0, 1).text = "列B"
    document.save(str(source))

    normalized, _output, result = _convert_docx(tmp_path, source, "tail-table")
    assert result.status == "completed", result.diagnostics

    children = normalized["source_ast"]["document"]["children"]
    blocks = normalized["source_ast"]["document"]["blocks"]
    containers = normalized["source_ast"]["document"]["containers"]
    parents = _parent_index(children)
    table_ids = [node_id for node_id, node in blocks.items() if node.get("block_type") == "table"]
    assert table_ids, blocks
    owners = []
    for table_id in table_ids:
        owner = parents.get(table_id)
        while owner in parents and containers.get(owner, {}).get("kind") != "list_item":
            owner = parents[owner]
        owners.append(containers.get(owner, {}).get("kind"))
    assert owners == ["list_item"], (owners, parents)


def test_t05_body_between_items_keeps_two_implicit_groups() -> None:
    """``[item][body][item]`` keeps the T04 split into two implicit groups.

    T05 attributes the interleaved body to the first item as a continuation, but
    the second item still opens a fresh implicit run, so the two-run segmentation
    (and the constraint report it drives) is preserved.
    """

    units = {
        "u0": _paragraph("u0", 0, "第一项"),
        "body": _paragraph("body", 1, "中间正文段落。"),
        "u1": _paragraph("u1", 2, "第二项"),
    }
    children = {"root": ["u0", "body", "u1"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "u0": _numbered_evidence(1, 1),
        "body": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
        "u1": _numbered_evidence(1, 2),
    }
    facts = _facts(containers, children, units, evidence)

    result = analyze_lists(facts)

    assert len(result["lists"]) == 2, result["lists"]
    by_id = {item["item_id"]: item for item in result["items"]}
    assert by_id["u1"]["parent_item_id"] is None, by_id["u1"]
    owners = {entry["node_id"]: entry["owner_item_id"] for entry in result["continuations"]}
    assert owners.get("body") == "u0", owners


def test_t05_list_item_map_seam_keeps_its_shape_and_consumers() -> None:
    """``list_item_map`` stays "source item id -> owning native list container".

    The appendix module reads this capability, so its contract may not change.
    Every source list item maps to a native list container id, and the mapping is
    exactly the one recorded on the materialized AST.
    """

    from wxdf.semantics.appendices import prepare_native_appendix_semantics

    units = {
        "i1": _paragraph("i1", 0, "第一项"),
        "i2": _paragraph("i2", 1, "第二项"),
        "tail": _paragraph("tail", 2, "续接正文"),
    }
    children = {"root": ["i1", "i2", "tail"]}
    containers = {"root": {"kind": "document"}}
    evidence = {
        "i1": _numbered_evidence(1, 1),
        "i2": _numbered_evidence(1, 2),
        "tail": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
    }
    facts = _facts(containers, children, units, evidence)

    _result, materialized, candidates, item_map = _materialized_ast(facts)
    items = _created_items(candidates)

    assert set(item_map) == {"i1", "i2"}
    assert all(list_id in items for list_id in item_map.values()), item_map
    # The appendix consumer still reads the same mapping.
    semantics = {"ranges": [{"root_node_ids": ["i1"]}]}
    prepared = prepare_native_appendix_semantics(facts, materialized, semantics, item_map)
    assert prepared["ranges"][0]["root_node_ids"] == [item_map["i1"]], prepared["ranges"][0]


# ---------------------------------------------------------------------------
# Source order is a general invariant (fix round): the ownership record, the
# materialized AST children and the rendered body order agree with the source
# order, and a generated caption stays directly above its table even when the
# table is moved under a list item in the same transaction.
# ---------------------------------------------------------------------------

def _commit(facts: Mapping[str, Any], *, with_captions: bool = False):
    """Resolve and commit the materialized AST the same way the chain does."""

    from wxdf.resolver import resolve
    from wxdf.transaction import apply_stage_batch, make_snapshot

    result = analyze_lists(facts)
    ast = build_source_ast(facts)
    _materialized, list_candidates, item_map = materialize_list_semantics(ast, facts, result)
    candidates = list(list_candidates)
    if with_captions:
        from wxdf.semantics.captions import collect_caption_candidates

        candidates.extend(collect_caption_candidates(ast, facts))
    decision_set = resolve(candidates, {"revision": ast["revision"]})
    committed = apply_stage_batch(
        make_snapshot(ast), decision_set["decisions"], expected_revision=ast["revision"]
    )
    return result, committed.snapshot.ast, item_map


def _node_label(document: Mapping[str, Any], node_id: str) -> str:
    """Return a short label for a rendered/materialized node."""

    block = document["blocks"].get(node_id)
    if block is None:
        return str(node_id)
    if block.get("block_type") == "table":
        return "tbl"
    if block.get("block_type") == "image":
        return "image"
    if block.get("block_type") == "paragraph":
        if block.get("role") == "caption":
            return "caption"
        return "".join(
            str(item.get("text") or "")
            for item in block.get("inline_content") or []
            if isinstance(item, Mapping) and item.get("kind") == "text"
        )
    return str(block.get("block_type"))


def _table_tail_facts(tail: list[tuple[str, str]]) -> dict:
    """Build facts for ``[item num1][item num1]`` followed by ``tail``.

    ``tail`` entries are ``("paragraph", text)``, ``("table", table_id)`` or
    ``("image", paragraph_id)``.
    """

    units: dict[str, dict] = {
        "i1": _paragraph("i1", 0, "第一项"),
        "i2": _paragraph("i2", 1, "第二项"),
        "cell": _paragraph("cell", 90, "单元格"),
    }
    containers: dict[str, dict] = {
        "root": {"kind": "document"},
        "table": {"kind": "table"},
        "row": {"kind": "table_row", "row": 0},
        "tcell": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1, "cell_role": "body"},
    }
    children: dict[str, list[str]] = {
        "root": ["i1", "i2"],
        "table": ["row"],
        "row": ["tcell"],
        "tcell": ["cell"],
    }
    evidence: dict[str, dict] = {
        "i1": _numbered_evidence(1, 1),
        "i2": _numbered_evidence(1, 2),
        "cell": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None},
    }
    resources: dict[str, dict] = {}
    index = 2
    for kind, value in tail:
        if kind == "paragraph":
            node_id = f"p{index}"
            units[node_id] = _paragraph(node_id, index, value)
            evidence[node_id] = {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None}
            children["root"].append(node_id)
        elif kind == "table":
            children["root"].append(value)
        elif kind == "image":
            node_id = value
            units[node_id] = {
                "kind": "paragraph",
                "source_ref": _ref(index),
                "inline_content": [{"kind": "image", "resource_id": "r1", "alt_text": "图"}],
            }
            evidence[node_id] = {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None}
            children["root"].append(node_id)
            resources["r1"] = {
                "kind": "image",
                "source_ref": _ref(index),
                "content_sha256": "sha256:" + "c" * 64,
                "media_type": "image/png",
            }
        index += 1
    facts = _facts(containers, children, units, evidence)
    facts["resources"] = resources
    return facts


def test_t05_interleaved_tail_keeps_source_order_in_committed_ast() -> None:
    """A tail ``[paragraph, table, paragraph, image]`` keeps source order.

    The ownership record and the committed AST children must both equal the
    source order, so the table is not pulled to the front and an image stays next
    to the paragraph it was materialized from.
    """

    facts = _table_tail_facts([
        ("paragraph", "续接正文甲"),
        ("table", "table"),
        ("paragraph", "续接正文乙"),
        ("image", "imgp"),
    ])

    _result, committed, item_map = _commit(facts)
    document = committed["document"]
    item = item_map["i2"]

    labels = [_node_label(document, node_id) for node_id in document["children"][item]]
    assert labels == ["第二项", "续接正文甲", "tbl", "续接正文乙", "", "image"], labels
    content_labels = [_node_label(document, node_id) for node_id in document["containers"][item]["content_blocks"]]
    assert content_labels == ["第二项", "续接正文甲", "tbl", "续接正文乙", "", "image"], content_labels
    assert document["children"][item] == document["containers"][item]["content_blocks"], document["children"][item]


def test_t05_tail_starting_with_table_keeps_source_order() -> None:
    """A tail that starts with a table keeps the table in its source slot."""

    facts = _table_tail_facts([
        ("table", "table"),
        ("paragraph", "尾部正文"),
    ])

    _result, committed, item_map = _commit(facts)
    document = committed["document"]
    item = item_map["i2"]

    labels = [_node_label(document, node_id) for node_id in document["children"][item]]
    assert labels == ["第二项", "tbl", "尾部正文"], labels
    assert document["children"][item] == document["containers"][item]["content_blocks"]


def test_t05_generated_caption_commits_above_a_table_owned_by_the_item() -> None:
    """A generated caption resolves directly above its table after the move."""

    facts = _table_tail_facts([("table", "table"), ("paragraph", "尾部正文")])

    _result, committed, item_map = _commit(facts, with_captions=True)
    document = committed["document"]
    item = item_map["i2"]
    caption_ids = [
        node_id for node_id, block in document["blocks"].items() if block.get("role") == "caption"
    ]
    assert len(caption_ids) == 1, caption_ids
    siblings = document["children"][item]
    assert siblings.index(caption_ids[0]) + 1 == siblings.index("table"), siblings
    # The caption is inside the item that owns the table, not a document sibling.
    assert caption_ids[0] in siblings, siblings
    assert document["blocks"][caption_ids[0]]["target_id"] == "table"


def test_t05_generated_caption_commits_above_a_preexisting_root_table() -> None:
    """The caption stays above a table whose parent already existed (root)."""

    units = {"cell": _paragraph("cell", 0, "单元格")}
    containers = {
        "root": {"kind": "document"},
        "table": {"kind": "table"},
        "row": {"kind": "table_row", "row": 0},
        "tcell": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1, "cell_role": "body"},
    }
    children = {"root": ["table"], "table": ["row"], "row": ["tcell"], "tcell": ["cell"]}
    evidence = {"cell": {"marker_kind": None, "explicit_depth": 0, "numbering_definition_ref": None}}
    facts = _facts(containers, children, units, evidence)

    _result, committed, _item_map = _commit(facts, with_captions=True)
    document = committed["document"]
    root_children = document["children"]["root"]
    caption_ids = [node_id for node_id, block in document["blocks"].items() if block.get("role") == "caption"]
    assert len(caption_ids) == 1, caption_ids
    assert root_children.index(caption_ids[0]) + 1 == root_children.index("table"), root_children


def _build_numbered_tail_docx(path: Path, tail: list[tuple[str, str]]) -> None:
    """Write a real DOCX ``[num][num]`` followed by ``tail`` (paragraph/table)."""

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
    for kind, value in tail:
        if kind == "paragraph":
            document.add_paragraph(value)
        elif kind == "table":
            table = document.add_table(rows=2, cols=2)
            for row in range(2):
                for col in range(2):
                    table.cell(row, col).text = f"单元格{row}{col}"
    document.save(str(path))


def _rendered_body_sequence(path: Path) -> list[tuple[str, str]]:
    """Return (kind, text) for every body paragraph and table, in order."""

    from xml.etree import ElementTree

    with ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{{{WORD_NS}}}body")
    sequence: list[tuple[str, str]] = []
    for child in body:
        if child.tag == f"{{{WORD_NS}}}p":
            num_pr = child.find(f"{{{WORD_NS}}}pPr/{{{WORD_NS}}}numPr")
            text = "".join(node.text or "" for node in child.iter(f"{{{WORD_NS}}}t"))
            sequence.append(("num" if num_pr is not None else "p", text))
        elif child.tag == f"{{{WORD_NS}}}tbl":
            sequence.append(("tbl", ""))
    return sequence


def test_t05_docx_item_tail_renders_in_source_order_with_caption_above_table(
    tmp_path: Path,
) -> None:
    """End-to-end: a DOCX list item owning an interleaved tail renders in order.

    The rendered body order must equal the source order and the generated caption
    must sit directly above the table, not after it.
    """

    source = tmp_path / "interleaved-tail.docx"
    _build_numbered_tail_docx(source, [
        ("paragraph", "第二项后的续接正文。"),
        ("paragraph", "表格前的说明。"),
        ("table", ""),
        ("paragraph", "尾部正文。"),
    ])

    normalized, output, result = _convert_docx(tmp_path, source, "interleaved-tail")
    assert result.status == "completed", result.diagnostics

    document = normalized["source_ast"]["document"]
    labels = _rendered_body_sequence(output)
    meaningful = [(kind, text.strip()) for kind, text in labels if text.strip() or kind == "tbl"]
    assert meaningful == [
        ("num", "第一项"),
        ("num", "第二项"),
        ("p", "第二项后的续接正文。"),
        ("p", "表格前的说明。"),
        ("p", "表 1"),
        ("tbl", ""),
        ("p", "尾部正文。"),
    ], meaningful

    # The AST children of the owning item match the source order; the caption
    # sits between the preceding paragraph and the table.
    parents = _parent_index(document["children"])
    table_ids = [node_id for node_id, node in document["blocks"].items() if node.get("block_type") == "table"]
    assert len(table_ids) == 1, table_ids
    owner = parents[table_ids[0]]
    assert document["containers"][owner]["kind"] == "list_item"
    owner_labels = [_node_label(document, node_id) for node_id in document["children"][owner]]
    assert owner_labels == [
        "第二项",
        "第二项后的续接正文。",
        "表格前的说明。",
        "caption",
        "tbl",
        "尾部正文。",
    ], owner_labels
    content_labels = [_node_label(document, node_id) for node_id in document["containers"][owner]["content_blocks"]]
    assert content_labels == [
        "第二项",
        "第二项后的续接正文。",
        "表格前的说明。",
        "tbl",
        "尾部正文。",
    ], content_labels


def test_t05_anchored_create_follows_a_table_created_in_the_same_batch() -> None:
    """An anchored caption lands above a table node created in the same batch."""

    from wxdf.transaction import apply_stage_batch, make_snapshot

    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {},
            "containers": {"root": {"kind": "document"}},
            "children": {"root": []},
            "associations": [],
            "resources": {},
        },
    }
    table_node = {
        "block_type": "table",
        "table_type": "data",
        "generated_by": "test",
        "generation_basis": ["table"],
    }
    caption_node = {
        "block_type": "paragraph",
        "role": "caption",
        "caption_type": "table",
        "target_id": "table",
        "inline_content": [],
        "generated_by": "test",
        "generation_basis": ["table"],
    }
    decisions = [
        {
            "operation": "create_node",
            "target_node_ids": ["table"],
            "target_source_refs": [],
            "value": {"node_id": "table", "parent_id": "root", "index": 0, "node": table_node},
        },
        {
            "operation": "create_node",
            "target_node_ids": ["caption"],
            "target_source_refs": [],
            "value": {
                "node_id": "caption",
                "parent_id": "root",
                "index": 0,
                "anchor_node_id": "table",
                "anchor_position": "before",
                "node": caption_node,
            },
        },
    ]
    result = apply_stage_batch(make_snapshot(ast), decisions, expected_revision=1)
    children = result.snapshot.ast["document"]["children"]["root"]
    assert children == ["caption", "table"], children


def test_t05_docx_tail_starting_with_table_renders_in_source_order(tmp_path: Path) -> None:
    """End-to-end: a tail that starts with a table keeps the table first."""

    source = tmp_path / "table-first-tail.docx"
    _build_numbered_tail_docx(source, [
        ("table", ""),
        ("paragraph", "尾部正文。"),
    ])

    normalized, output, result = _convert_docx(tmp_path, source, "table-first-tail")
    assert result.status == "completed", result.diagnostics

    document = normalized["source_ast"]["document"]
    meaningful = [
        (kind, text.strip())
        for kind, text in _rendered_body_sequence(output)
        if text.strip() or kind == "tbl"
    ]
    assert meaningful == [
        ("num", "第一项"),
        ("num", "第二项"),
        ("p", "表 1"),
        ("tbl", ""),
        ("p", "尾部正文。"),
    ], meaningful


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(__import__("pytest").main([__file__, "-q"]))


def _anchored_ast(node_id: str, anchor_id: str, order: list[str]) -> dict:
    return {
        "document": {
            "blocks": {
                anchor_id: {"id": anchor_id, "block_type": "table"},
                node_id: {"id": node_id, "block_type": "paragraph"},
            },
            "containers": {"root": {"kind": "document"}},
            "children": {"root": list(order)},
        }
    }


def test_t05_anchored_create_applied_predicate_matches_a_before_anchor() -> None:
    """``revision_applied`` must accept a committed "before" anchored create.

    A node anchored before a sibling is inserted AT the anchor index, so the
    anchor shifts one slot later.  Reading the anchor index without that shift
    reports a correctly committed caption as not applied, which turns a ledger
    replay into a duplicate-node failure.
    """

    from wxdf.transaction import revision_applied

    ast = _anchored_ast("cap", "tbl", ["cap", "tbl"])
    revision = {
        "revision_id": "rev-anchor-before",
        "decision": {
            "operation": "create_node",
            "target_node_ids": ["cap"],
            "value": {
                "node_id": "cap",
                "node": {"id": "cap", "block_type": "paragraph"},
                "anchor_node_id": "tbl",
                "anchor_position": "before",
            },
        },
    }

    assert revision_applied(ast, revision) is True


def test_t05_anchored_create_applied_predicate_matches_an_after_anchor() -> None:
    """The "after" anchor keeps its original offset."""

    from wxdf.transaction import revision_applied

    ast = _anchored_ast("cap", "fig", ["fig", "cap"])
    revision = {
        "revision_id": "rev-anchor-after",
        "decision": {
            "operation": "create_node",
            "target_node_ids": ["cap"],
            "value": {
                "node_id": "cap",
                "node": {"id": "cap", "block_type": "paragraph"},
                "anchor_node_id": "fig",
                "anchor_position": "after",
            },
        },
    }

    assert revision_applied(ast, revision) is True
