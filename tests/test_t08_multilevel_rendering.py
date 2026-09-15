"""T08 end-to-end tests for real multi-level WX list rendering.

These tests pin the T08 acceptance: under the product default policy
(``wx-default-v2``) a genuine multi-level ordered list must render REAL
multi-level numbering.  The first level keeps the first-level letter style and
the deeper levels keep the second-level style, and the emitted ``w:ilvl`` follows
the true semantic level through a numbering instance that actually defines those
levels, instead of pinning every ordered item to ``w:ilvl=0``.

Fixture naming follows the T01 convention ``t08_<scenario>_<variant>``.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.run_result import ConversionRequest

from modular_support import simulate_saved_numbering
from test_modular_normalization import _ast, _profile


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": WORD_NS}


# ---------------------------------------------------------------------------
# Fixtures: conversion + readers
# ---------------------------------------------------------------------------

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


def _convert_docx(tmp_path: Path, spec: list, tag: str):
    import json

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
    source = tmp_path / f"{tag}.docx"
    document.save(str(source))

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


def _direct_numbering(path: Path) -> list[tuple[str, str, str]]:
    """Return ``(text, numId, ilvl)`` for every paragraph with a direct numPr."""

    rows: list[tuple[str, str, str]] = []
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


def _paragraph_styles(path: Path) -> dict[str, str]:
    """Return ``{paragraph text: style name}`` for the saved document."""

    styles: dict[str, str] = {}
    with ZipFile(path) as archive:
        document = ElementTree.fromstring(archive.read("word/document.xml"))
        styles_root = ElementTree.fromstring(archive.read("word/styles.xml"))
    style_names = {}
    for style in styles_root.findall("w:style", _NS):
        style_id = style.get(f"{{{WORD_NS}}}styleId")
        name = style.find("w:name", _NS)
        if style_id and name is not None:
            style_names[style_id] = name.get(f"{{{WORD_NS}}}val") or ""
    for paragraph in document.iter(f"{{{WORD_NS}}}p"):
        style = paragraph.find("w:pPr/w:pStyle", _NS)
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t"))
        style_id = style.get(f"{{{WORD_NS}}}val") if style is not None else None
        styles[text] = style_names.get(style_id, "Normal")
    return styles


def _numbering_levels(path: Path) -> dict[int, set[int]]:
    """Return ``{numId: {defined ilvl, ...}}`` for the saved numbering part."""

    with ZipFile(path) as archive:
        numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
    abstract_levels: dict[str, set[int]] = {}
    for abstract in numbering.findall("w:abstractNum", _NS):
        abstract_id = abstract.get(f"{{{WORD_NS}}}abstractNumId")
        levels = {
            int(level.get(f"{{{WORD_NS}}}ilvl", "0"))
            for level in abstract.findall("w:lvl", _NS)
        }
        abstract_levels[str(abstract_id)] = levels
    result: dict[int, set[int]] = {}
    for num in numbering.findall("w:num", _NS):
        reference = num.find("w:abstractNumId", _NS)
        levels = set(abstract_levels.get(reference.get(f"{{{WORD_NS}}}val"), set())) if reference is not None else set()
        for override in num.findall("w:lvlOverride", _NS):
            levels.add(int(override.get(f"{{{WORD_NS}}}ilvl", "0")))
        result[int(num.get(f"{{{WORD_NS}}}numId"))] = levels
    return result


def _visible_markers(path: Path) -> dict[str, str]:
    """Return ``{paragraph text: simulated visible marker}`` for the saved document."""

    return {
        row["text"]: row["marker"]
        for row in simulate_saved_numbering(path)
        if row.get("marker") is not None and row.get("text")
    }


def _heading_numbering_instances(path: Path = TEMPLATE) -> tuple[set[str], set[str]]:
    """Return ``({numId bound to a heading style}, {its abstractNumId})``.

    The heading instances are read from the styles part, which is where the
    template binds heading 1..6 to ``numId 1`` (ilvl 0..5) and heading 7..9 to
    ``numId 2``.  A list item that shares one of these instances shares the
    heading counters, which is the defect T08's fix round removes.
    """

    with ZipFile(path) as archive:
        styles = ElementTree.fromstring(archive.read("word/styles.xml"))
        numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
    abstract_by_num = {
        num.get(f"{{{WORD_NS}}}numId"): num.find("w:abstractNumId", _NS).get(f"{{{WORD_NS}}}val")
        for num in numbering.findall("w:num", _NS)
        if num.find("w:abstractNumId", _NS) is not None
    }
    instances: set[str] = set()
    abstracts: set[str] = set()
    for style in styles.findall("w:style", _NS):
        name = style.find("w:name", _NS)
        name_value = (name.get(f"{{{WORD_NS}}}val") or "") if name is not None else ""
        if not name_value.casefold().startswith("heading"):
            continue
        num_id = style.find("w:pPr/w:numPr/w:numId", _NS)
        if num_id is None:
            continue
        instances.add(num_id.get(f"{{{WORD_NS}}}val"))
        abstract = abstract_by_num.get(num_id.get(f"{{{WORD_NS}}}val"))
        if abstract is not None:
            abstracts.add(abstract)
    return instances, abstracts


def _abstract_of(path: Path, num_id: str) -> str:
    """Return the ``abstractNumId`` a saved ``w:num`` references."""

    with ZipFile(path) as archive:
        numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
    for num in numbering.findall("w:num", _NS):
        if num.get(f"{{{WORD_NS}}}numId") == str(num_id):
            return num.find("w:abstractNumId", _NS).get(f"{{{WORD_NS}}}val")
    raise AssertionError(f"numId {num_id!r} is not defined in the saved numbering part")


def _list_depths(normalized: dict) -> dict[str, int]:
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
        if not isinstance(block, dict) or block.get("block_type") != "paragraph":
            continue
        text = "".join(
            str(item.get("text") or "")
            for item in block.get("inline_content", [])
            if isinstance(item, dict) and item.get("kind") == "text"
        )
        node_depth = depth(node_id)
        if text and node_depth > 0:
            depths[text] = node_depth
    return depths


def _three_level_ordered_ast() -> dict:
    ast = copy.deepcopy(_ast())
    ast["document"]["containers"]["l2"]["list_kind"] = "ordered"
    ast["document"]["containers"]["i2"]["source_marker"] = "1."
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
        "source_ref": ast["document"]["blocks"]["p1"]["source_ref"],
    }
    ast["document"]["children"]["i2"] = ["p2", "l3"]
    ast["document"]["children"]["l3"] = ["i4"]
    ast["document"]["children"]["i4"] = ["p4"]
    return ast


def _normalize_with_bundle(ast: dict, strategy="wx-default-v2") -> dict:
    from wxdf.normalization import normalize
    from wxdf.template import load_template_bundle

    bundle = load_template_bundle(TEMPLATE)
    return normalize(ast, bundle, strategy=strategy)


# ---------------------------------------------------------------------------
# Work item 3: real multi-level ordered output end to end
# ---------------------------------------------------------------------------

def test_t08_two_level_ordered_renders_real_multi_level(tmp_path: Path) -> None:
    """A two-level ordered list emits level 0 for parents and level 1 for children."""

    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 两级列表\n\n1. 一级一\n   1. 二级一\n   2. 二级二\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics

    depths = _list_depths(normalized)
    assert depths.get("一级一") == 1, depths
    assert depths.get("二级一") == 2, depths
    assert depths.get("二级二") == 2, depths
    assert depths.get("一级二") == 1, depths

    rows = _direct_numbering(output)
    assert [row[0] for row in rows] == ["一级一", "二级一", "二级二", "一级二"], rows
    assert [row[2] for row in rows] == ["0", "1", "1", "0"], rows
    num_ids = [row[1] for row in rows]
    # T08 FIX round: level 0 and the deeper levels share ONE list-only instance.
    # The pre-fix binding used the template's heading instance for the child and
    # the template's letter instance for the parent, so the list numbering
    # tracked the heading counters.
    assert set(num_ids) == {num_ids[0]}, rows
    heading_instances, heading_abstracts = _heading_numbering_instances()
    assert num_ids[0] not in heading_instances, (num_ids, heading_instances)
    assert _abstract_of(output, num_ids[0]) not in heading_abstracts, (num_ids, heading_abstracts)

    levels = _numbering_levels(output)
    assert {0, 1} <= levels[int(num_ids[0])], (num_ids, levels)

    styles = _paragraph_styles(output)
    assert styles["一级一"] == "1.1一级列项-编号", styles
    assert styles["一级二"] == "1.1一级列项-编号", styles
    assert styles["二级一"] == "2.1二级列项-有编号", styles
    assert styles["二级二"] == "2.1二级列项-有编号", styles

    # The simulated visible numbering is the real multi-level output: the parent
    # keeps the first-level letter form and the children carry that parent's
    # letter with their own level-1 counter.
    markers = _visible_markers(output)
    assert [markers[text] for text in ("一级一", "二级一", "二级二", "一级二")] == ["a)", "a.1", "a.2", "b)"], markers

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]

    clamps = [
        item
        for item in result.report["native_module_evidence"]["native-auditor"]["downstream"]["render_diagnostics"]
        if item.get("code") == "list_render_level_clamped"
    ]
    assert clamps == [], clamps


def test_t08_three_level_ordered_renders_real_multi_level(tmp_path: Path) -> None:
    """A three-level ordered list emits level 0, 1 and 2 through a multi-level instance."""

    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 三级列表\n\n1. 一级\n   1. 二级\n      1. 三级\n   2. 二级二\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics

    rows = _direct_numbering(output)
    assert [row[0] for row in rows] == ["一级", "二级", "三级", "二级二", "一级二"], rows
    assert [row[2] for row in rows] == ["0", "1", "2", "1", "0"], rows

    levels = _numbering_levels(output)
    deeper_ids = {row[1] for row in rows if row[2] != "0"}
    assert deeper_ids, rows
    for num_id in deeper_ids:
        assert {1, 2} <= levels[int(num_id)], (num_id, levels[num_id])

    heading_instances, heading_abstracts = _heading_numbering_instances()
    assert not ({row[1] for row in rows} & heading_instances), (rows, heading_instances)
    assert _abstract_of(output, rows[0][1]) not in heading_abstracts, rows

    markers = _visible_markers(output)
    assert [markers[text] for text in ("一级", "二级", "三级", "二级二", "一级二")] == [
        "a)", "a.1", "a.1.1", "a.2", "b)",
    ], markers

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]


def test_t08_second_level_does_not_collapse_to_zero(tmp_path: Path) -> None:
    """The pre-fix symptom (every ordered item at numId 3 ilvl 0) is gone."""

    _normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n1. 一级一\n   1. 二级一\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics
    rows = _direct_numbering(output)
    assert [(row[1], row[2]) for row in rows] != [("3", "0"), ("3", "0"), ("3", "0")], rows
    assert rows[1][2] == "1", rows


# ---------------------------------------------------------------------------
# Work item 1: the ListPolicy owns the semantic -> render projection
# ---------------------------------------------------------------------------

def test_t08_default_policy_preserves_true_level_and_second_style_projection() -> None:
    normalized = _normalize_with_bundle(_three_level_ordered_ast(), strategy="wx-default-v2")
    nodes = normalized["presentation"]["nodes"]

    assert nodes["i1"]["target_level"] == 0, nodes["i1"]
    assert nodes["i1"]["style_role"] == "list_letter", nodes["i1"]
    assert nodes["i2"]["target_level"] == 1, nodes["i2"]
    assert nodes["i2"]["style_role"] == "list_decimal", nodes["i2"]
    assert nodes["i4"]["target_level"] == 2, nodes["i4"]
    assert nodes["i4"]["style_role"] == "list_decimal", nodes["i4"]
    assert nodes["i3"]["target_level"] == 0, nodes["i3"]

    # The policy is a first-class object with a stable, documented interface.
    from wxdf.normalization import list_policy

    policy = list_policy("wx-default-v2")
    assert policy.project("ordered", 0).render_level == 0
    assert policy.project("ordered", 0).style_role == "list_letter"
    assert policy.project("ordered", 3).render_level == 3
    assert policy.project("ordered", 3).style_role == "list_decimal"
    assert policy.project("unordered", 0).style_role == "list_dash"
    assert policy.project("unordered", 4).style_role == "list_bullet"


def _numbering_num_to_abstract(path: Path) -> dict[int, int]:
    with ZipFile(path) as archive:
        numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
    result: dict[int, int] = {}
    for num in numbering.findall("w:num", _NS):
        reference = num.find("w:abstractNumId", _NS)
        if reference is None:
            continue
        result[int(num.get(f"{{{WORD_NS}}}numId"))] = int(reference.get(f"{{{WORD_NS}}}val"))
    return result


def test_t08_default_policy_binds_ordered_roles_to_one_list_only_instance() -> None:
    from template_profile import load_template_profile

    template_ids = load_template_profile(TEMPLATE)["numbering_ids"]
    # The template's own ordered roles bind single-level instances, and its only
    # multi-level ordered instance is the heading outline (heading 1 to 6 are
    # bound to it in the styles part).
    assert template_ids["list_decimal"] == 8, template_ids
    assert template_ids["list_letter"] == 3, template_ids
    levels = _numbering_levels(TEMPLATE)
    assert levels[8] == {0}, levels[8]

    normalized = _normalize_with_bundle(_three_level_ordered_ast(), strategy="wx-default-v2")
    effective = normalized["presentation"]["profile"]["numbering_ids"]
    synthesized = normalized["presentation"]["profile"]["synthesized_numbering"]
    # The policy binds every ordered role to ONE list-only instance that defines
    # the deeper levels, so the emitted w:ilvl can be a real level AND level 0
    # shares the instance with the deeper levels.
    assert effective["list_decimal"] != template_ids["list_decimal"], effective
    assert effective["list_letter"] == effective["list_decimal"], effective
    assert [item["num_id"] for item in synthesized] == [effective["list_decimal"]], synthesized
    # The abstract it clones defines the deeper levels, its level 0 comes from
    # the template's own first-level letter definition, and neither is a heading
    # definition.
    template_levels = _numbering_levels(TEMPLATE)
    abstract_by_num = _numbering_num_to_abstract(TEMPLATE)
    heading_instances, heading_abstracts = _heading_numbering_instances()
    assert str(effective["list_decimal"]) not in heading_instances, (effective, heading_instances)
    # The deeper levels are cloned from the template's multi-level outline, which
    # is also what the heading styles number with, so the clone is what keeps the
    # counters apart.
    deeper_num_id = next(
        num_id
        for num_id, abstract_id in abstract_by_num.items()
        if abstract_id == synthesized[0]["deeper_abstract_num_id"]
    )
    assert deeper_num_id in {int(item) for item in heading_instances}, (deeper_num_id, heading_instances)
    assert len(template_levels[deeper_num_id]) > 1, template_levels[deeper_num_id]
    assert str(synthesized[0]["abstract_num_id"]) not in heading_abstracts, (synthesized, heading_abstracts)
    assert synthesized[0]["level_zero_abstract_num_id"] == abstract_by_num[template_ids["list_letter"]], synthesized
    # The template's own letter definition is the one the level-0 source comes
    # from, so the level-0 format stays the first-level letter form.
    assert template_ids["list_letter_abstract"] == synthesized[0]["level_zero_abstract_num_id"], synthesized
    # The abstract companion stays consistent with the bound instance.
    assert effective["list_decimal_abstract"] == synthesized[0]["abstract_num_id"], effective
    assert effective["list_letter_abstract"] == synthesized[0]["abstract_num_id"], effective
    # ``legacy`` keeps the template's own bindings: it never synthesizes.
    legacy = _normalize_with_bundle(_three_level_ordered_ast(), strategy="legacy")
    assert legacy["presentation"]["profile"]["numbering_ids"]["list_letter"] == template_ids["list_letter"]
    assert legacy["presentation"]["profile"]["synthesized_numbering"] == []


def test_t08_list_only_instance_is_synthesized_in_the_produced_part(tmp_path: Path) -> None:
    """The list instance is written into the document, and the template stays untouched."""

    import hashlib

    template_before = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 一章\n\n1. 一级一\n   1. 二级一\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics
    assert hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() == template_before

    synthesized = normalized["presentation"]["profile"]["synthesized_numbering"]
    assert len(synthesized) == 1, synthesized
    spec = synthesized[0]
    num_id = str(spec["num_id"])
    with ZipFile(output) as archive:
        numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
        names = archive.namelist()
    abstracts = {
        abstract.get(f"{{{WORD_NS}}}abstractNumId"): abstract
        for abstract in numbering.findall("w:abstractNum", _NS)
    }
    assert "word/numbering.xml" in names
    assert str(spec["abstract_num_id"]) in abstracts
    # The definition is list-only: it carries no paragraph style link, so no
    # heading (or list) style borrows the instance.
    abstract = abstracts[str(spec["abstract_num_id"])]
    assert abstract.findall(".//w:pStyle", _NS) == []
    # Level 0 is the template's first-level letter definition and the deeper
    # levels are the template's outline levels.
    level_zero = abstract.find("w:lvl[@w:ilvl='0']", _NS)
    assert level_zero.find("w:numFmt", _NS).get(f"{{{WORD_NS}}}val") == "lowerLetter"
    assert level_zero.find("w:lvlText", _NS).get(f"{{{WORD_NS}}}val") == "%1)"
    level_one = abstract.find("w:lvl[@w:ilvl='1']", _NS)
    assert level_one.find("w:numFmt", _NS).get(f"{{{WORD_NS}}}val") == "decimal"
    assert level_one.find("w:lvlText", _NS).get(f"{{{WORD_NS}}}val") == "%1.%2 "
    # Level 0 never restarts; every deeper level restarts when the level above it
    # is used, which is what restarts a parent's children.
    assert level_zero.find("w:lvlRestart", _NS).get(f"{{{WORD_NS}}}val") == "0"
    assert level_one.find("w:lvlRestart", _NS).get(f"{{{WORD_NS}}}val") == "1"
    # The new instance is a real ``w:num`` entry that the list items reference.
    references = {
        num.get(f"{{{WORD_NS}}}numId"): num.find("w:abstractNumId", _NS).get(f"{{{WORD_NS}}}val")
        for num in numbering.findall("w:num", _NS)
    }
    assert references[num_id] == str(spec["abstract_num_id"]), references
    assert {row[1] for row in _direct_numbering(output)} == {num_id}


def test_t08_nested_list_does_not_renumber_a_following_heading(tmp_path: Path) -> None:
    """A following heading keeps the heading sequence the list must not touch."""

    _normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 一章\n\n### 一节\n\n1. 一级一\n   1. 二级一\n2. 一级二\n\n### 二节\n",
    )
    assert result.status == "completed", result.diagnostics

    rows = {row["text"]: row for row in simulate_saved_numbering(output) if row["text"]}
    heading_instances, _abstracts = _heading_numbering_instances()
    # The headings keep the heading instance; the list items never use it.
    assert rows["一节"]["num_id"] in heading_instances, rows["一节"]
    assert rows["二节"]["num_id"] in heading_instances, rows["二节"]
    assert rows["二级一"]["num_id"] not in heading_instances, rows["二级一"]
    # The list child shares the list's own instance, so it advances no heading
    # counter: the following second-level heading is 1.2, not 1.3.
    assert rows["一节"]["marker"] == "1.1", rows["一节"]
    assert rows["二级一"]["marker"] == "a.1", rows["二级一"]
    assert rows["二节"]["marker"] == "1.2", rows["二节"]


def test_t08_children_restart_under_each_top_level_item(tmp_path: Path) -> None:
    """Two top-level items each restart their own children."""

    _normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 一章\n\n1. 一级一\n   1. 二级一\n2. 一级二\n   1. 二级二\n",
    )
    assert result.status == "completed", result.diagnostics

    markers = _visible_markers(output)
    assert markers["一级一"] == "a)", markers
    assert markers["二级一"] == "a.1", markers
    assert markers["一级二"] == "b)", markers
    # The second top-level item restarts its children instead of continuing the
    # first parent's child counter (the pre-fix symptom was 1.2 here).
    assert markers["二级二"] == "b.1", markers


def test_t08_normalization_consumes_the_stored_semantic_level() -> None:
    """The projection consumes the stored level; the container walk is a fallback."""

    from wxdf.normalization import normalize
    from wxdf.template import load_template_bundle

    bundle = load_template_bundle(TEMPLATE)
    ast = _three_level_ordered_ast()

    walked = normalize(ast, bundle, strategy="wx-default-v2")
    assert walked["presentation"]["nodes"]["i4"]["target_level"] == 2, walked["presentation"]["nodes"]["i4"]

    # A stored semantic level that disagrees with the container tree wins: the
    # normalizer reads the list semantics layer's level instead of re-deriving
    # the depth from the tree, and projects that level with the run policy.
    stored = normalize(
        ast,
        bundle,
        strategy="wx-default-v2",
        context={"list_container_levels": [{"list_id": "l3", "semantic_level": 4}]},
    )
    node = stored["presentation"]["nodes"]["i4"]
    assert node["target_level"] == 4, node
    assert node["style_role"] == "list_decimal", node


def test_t08_list_policy_is_swappable_for_a_third_template_level() -> None:
    """A policy with a third role projects one level deeper without touching lists.py."""

    from wxdf.normalization import ListPolicy, normalize

    policy = ListPolicy(
        name="wx-three-level",
        ordered_roles=("list_letter", "list_decimal", "list_roman"),
        unordered_roles=("list_dash", "list_bullet"),
    )
    profile = {
        "resolved_styles": {
            "list_letter": "1.1一级列项-编号",
            "list_dash": "1.2一级列项-无编号",
            "list_decimal": "2.1二级列项-有编号",
            "list_bullet": "2.2二级列项-无编号",
            "list_roman": "3.1三级列项-编号",
        },
        "numbering_ids": {"list_letter": 3, "list_decimal": 2, "list_roman": 1},
    }
    normalized = normalize(_three_level_ordered_ast(), profile, strategy=policy)
    nodes = normalized["presentation"]["nodes"]

    assert nodes["i1"]["style_role"] == "list_letter", nodes["i1"]
    assert nodes["i2"]["style_role"] == "list_decimal", nodes["i2"]
    assert nodes["i4"]["target_level"] == 2 and nodes["i4"]["style_role"] == "list_roman", nodes["i4"]


def test_t08_rejects_an_unknown_policy_name() -> None:
    from wxdf.normalization import NormalizationError, list_policy, normalize

    with pytest.raises(NormalizationError, match="policy"):
        list_policy("not-a-policy")
    with pytest.raises(NormalizationError):
        normalize(_ast(), _profile(), strategy="not-a-policy")


# ---------------------------------------------------------------------------
# T07 protection regression: an ordinary document stays completed
# ---------------------------------------------------------------------------

def test_t08_protected_content_stays_body_text_after_the_policy_change(tmp_path: Path) -> None:
    _normalized, _output, result = _convert_docx(
        tmp_path,
        [
            ("6.3 标准化注册时序流程", 1, 0),
            ("6.4 会话建立流程", 1, 0),
            ("终端用户负责发起认证请求", None, 0),
            ("192.168.1.20", None, 0),
            ("10.0.0.5", None, 0),
        ],
        "protected",
    )
    assert result.status == "completed", result.diagnostics
    semantics = result.report["list_semantics"]
    assert semantics["item_count"] == 0, semantics
    assert (result.report.get("risk_warnings") or []) == [], result.report.get("risk_warnings")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
