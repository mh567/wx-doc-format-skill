"""T08 end-to-end tests for two-level WX list rendering.

These tests pin the T08 acceptance under the product default policy
(``wx-default-v2``).  ``AGENTS.md`` decision 1 recognises at most two list
levels: semantic level 0 uses the kind's first-level style (``a)`` for ordered,
the first-level unordered style for unordered) and semantic level 1 uses the
kind's second-level style from the template's own instance (``1)`` for ordered,
the second-level bullet for unordered).  A semantic level deeper than 1 clamps
to the second level and reports ``list_render_level_clamped``.  The template's
second-level instances are single-level, so a second-level item emits
``w:ilvl 0`` and the two levels are told apart by the style and the instance,
not by the level attribute.

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
# Work item 3: two-level ordered output end to end
# ---------------------------------------------------------------------------

def test_t08_two_level_ordered_renders_the_two_level_styles(tmp_path: Path) -> None:
    """A two-level ordered list renders ``a)`` then the template's ``1)`` form."""

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
    # Both levels emit w:ilvl 0: the template's second-level ordered style sits
    # on its own single-level instance, so the levels are distinguished by the
    # instance (and the style) rather than by a deeper w:ilvl.
    assert [row[2] for row in rows] == ["0", "0", "0", "0"], rows
    num_ids = [row[1] for row in rows]
    # Level 0 binds the template's first-level letter instance; level 1 binds the
    # template's own second-level decimal instance, so the visible marker is the
    # designed ``1)`` instead of an outline ``a.1``.
    assert num_ids == ["3", "8", "8", "3"], rows
    heading_instances, heading_abstracts = _heading_numbering_instances()
    assert not (set(num_ids) & heading_instances), (num_ids, heading_instances)
    assert _abstract_of(output, "3") not in heading_abstracts, heading_abstracts

    levels = _numbering_levels(output)
    assert levels[3] == {0}, levels[3]
    assert levels[8] == {0}, levels[8]

    styles = _paragraph_styles(output)
    assert styles["一级一"] == "1.1一级列项-编号", styles
    assert styles["一级二"] == "1.1一级列项-编号", styles
    assert styles["二级一"] == "2.1二级列项-有编号", styles
    assert styles["二级二"] == "2.1二级列项-有编号", styles

    # The simulated visible numbering is the designed two-level output: the
    # parent keeps the first-level letter form and the child carries the
    # second-level style's own independent ``1)``/``2)`` sequence.
    markers = _visible_markers(output)
    assert [markers[text] for text in ("一级一", "二级一", "二级二", "一级二")] == ["a)", "1)", "2)", "b)"], markers

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]

    clamps = [
        item
        for item in result.report["native_module_evidence"]["native-auditor"]["downstream"]["render_diagnostics"]
        if item.get("code") == "list_render_level_clamped"
    ]
    assert clamps == [], clamps


def test_t08_three_level_ordered_clamps_the_third_level(tmp_path: Path) -> None:
    """A three-level ordered list clamps the third level to the second."""

    from wxdf.auditing import audit_list_readback

    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 三级列表\n\n1. 一级\n   1. 二级\n      1. 三级\n   2. 二级二\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics

    rows = _direct_numbering(output)
    assert [row[0] for row in rows] == ["一级", "二级", "三级", "二级二", "一级二"], rows
    # The third semantic level is clamped to the second, so it too emits ilvl 0.
    assert [row[2] for row in rows] == ["0", "0", "0", "0", "0"], rows
    assert [row[1] for row in rows] == ["3", "8", "8", "8", "3"], rows

    levels = _numbering_levels(output)
    assert levels[8] == {0}, levels[8]

    heading_instances, heading_abstracts = _heading_numbering_instances()
    assert not ({row[1] for row in rows} & heading_instances), (rows, heading_instances)
    assert _abstract_of(output, rows[0][1]) not in heading_abstracts, rows

    markers = _visible_markers(output)
    assert [markers[text] for text in ("一级", "二级", "三级", "二级二", "一级二")] == [
        "a)", "1)", "2)", "3)", "b)",
    ], markers

    comparison = audit_list_readback(output, normalized)
    assert comparison["passed"] is True, comparison["issues"]

    # The third semantic level is deeper than the two-level model, so it clamps
    # to the second level and the clamp is observable instead of silent.
    clamps = [
        item
        for item in result.report["native_module_evidence"]["native-auditor"]["downstream"]["render_diagnostics"]
        if item.get("code") == "list_render_level_clamped"
    ]
    assert len(clamps) == 1, clamps
    assert clamps[0]["semantic_level"] == 2, clamps[0]
    assert clamps[0]["render_level"] == 1, clamps[0]
    assert clamps[0]["emitted_level"] == 0, clamps[0]
    assert clamps[0]["num_id"] == 8, clamps[0]


def test_t08_second_level_uses_its_own_ordered_instance(tmp_path: Path) -> None:
    """The pre-fix symptom (every ordered item on one letter instance) is gone."""

    _normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n1. 一级一\n   1. 二级一\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics
    rows = _direct_numbering(output)
    # The second level binds the template's second-level ordered instance instead
    # of collapsing onto the first-level letter instance.
    assert [row[1] for row in rows] == ["3", "8", "3"], rows
    assert [row[2] for row in rows] == ["0", "0", "0"], rows


# ---------------------------------------------------------------------------
# Work item 1: the ListPolicy owns the semantic -> render projection
# ---------------------------------------------------------------------------

def test_t08_default_policy_caps_at_two_levels_and_picks_second_style() -> None:
    normalized = _normalize_with_bundle(_three_level_ordered_ast(), strategy="wx-default-v2")
    nodes = normalized["presentation"]["nodes"]

    assert nodes["i1"]["target_level"] == 0, nodes["i1"]
    assert nodes["i1"]["style_role"] == "list_letter", nodes["i1"]
    assert nodes["i2"]["target_level"] == 1, nodes["i2"]
    assert nodes["i2"]["style_role"] == "list_decimal", nodes["i2"]
    assert nodes["i2"]["render_level_clamped"] is False, nodes["i2"]
    # The third semantic level is deeper than the two-level model, so it clamps
    # to render level 1 and carries the clamp flag.
    assert nodes["i4"]["semantic_level"] == 2, nodes["i4"]
    assert nodes["i4"]["target_level"] == 1, nodes["i4"]
    assert nodes["i4"]["style_role"] == "list_decimal", nodes["i4"]
    assert nodes["i4"]["render_level_clamped"] is True, nodes["i4"]
    assert nodes["i3"]["target_level"] == 0, nodes["i3"]

    # The policy is a first-class object with a stable, documented interface.
    from wxdf.normalization import list_policy

    policy = list_policy("wx-default-v2")
    assert policy.max_level == 1
    assert policy.project("ordered", 0).render_level == 0
    assert policy.project("ordered", 0).style_role == "list_letter"
    assert policy.project("ordered", 3).render_level == 1
    assert policy.project("ordered", 3).style_role == "list_decimal"
    assert policy.project("ordered", 3).clamped is True
    assert policy.project("unordered", 0).style_role == "list_dash"
    assert policy.project("unordered", 4).render_level == 1
    assert policy.project("unordered", 4).style_role == "list_bullet"
    assert policy.project("unordered", 4).clamped is True


def test_t08_default_policy_binds_roles_to_the_template_instances() -> None:
    from template_profile import load_template_profile

    template_ids = load_template_profile(TEMPLATE)["numbering_ids"]
    # The template binds each list role to the instance designed for its level:
    # the first-level letter instance (numId 3) and the second-level ordered
    # instance (numId 8, ``%1)`` decimal), which is what makes the second level
    # show ``1)``.
    assert template_ids["list_decimal"] == 8, template_ids
    assert template_ids["list_letter"] == 3, template_ids
    assert template_ids["list_bullet"] == 4, template_ids
    levels = _numbering_levels(TEMPLATE)
    assert levels[8] == {0}, levels[8]

    normalized = _normalize_with_bundle(_three_level_ordered_ast(), strategy="wx-default-v2")
    profile = normalized["presentation"]["profile"]
    effective = profile["numbering_ids"]
    # The default policy synthesizes no numbering instance: every role keeps the
    # template's own binding, so the two levels are distinguished by style and
    # instance rather than by a shared multi-level outline.
    assert "synthesized_numbering" not in profile, profile
    assert effective["list_letter"] == template_ids["list_letter"], effective
    assert effective["list_decimal"] == template_ids["list_decimal"], effective
    assert effective["list_bullet"] == template_ids["list_bullet"], effective
    heading_instances, heading_abstracts = _heading_numbering_instances()
    assert str(effective["list_decimal"]) not in heading_instances, (effective, heading_instances)
    assert str(effective["list_letter"]) not in heading_instances, (effective, heading_instances)
    assert effective["list_decimal_abstract"] == template_ids["list_decimal_abstract"], effective
    assert effective["list_letter_abstract"] == template_ids["list_letter_abstract"], effective
    assert str(effective["list_decimal_abstract"]) not in heading_abstracts, (effective, heading_abstracts)
    # ``legacy`` keeps the same template bindings and never synthesizes either.
    legacy = _normalize_with_bundle(_three_level_ordered_ast(), strategy="legacy")
    legacy_profile = legacy["presentation"]["profile"]
    assert "synthesized_numbering" not in legacy_profile, legacy_profile
    assert legacy_profile["numbering_ids"]["list_letter"] == template_ids["list_letter"]
    assert legacy_profile["numbering_ids"]["list_decimal"] == template_ids["list_decimal"]


def test_t08_second_level_binds_the_template_second_level_instance(tmp_path: Path) -> None:
    """The second level uses the template's ``%1)`` instance; the template stays untouched."""

    import hashlib

    template_before = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 一章\n\n1. 一级一\n   1. 二级一\n2. 一级二\n",
    )
    assert result.status == "completed", result.diagnostics
    assert hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() == template_before

    profile = normalized["presentation"]["profile"]
    assert "synthesized_numbering" not in profile, profile
    assert profile["numbering_ids"]["list_letter"] == 3, profile
    assert profile["numbering_ids"]["list_decimal"] == 8, profile
    assert profile["numbering_ids"]["list_letter_abstract"] == 4, profile
    assert profile["numbering_ids"]["list_decimal_abstract"] == 7, profile

    with ZipFile(output) as archive:
        numbering = ElementTree.fromstring(archive.read("word/numbering.xml"))
    abstracts = {
        abstract.get(f"{{{WORD_NS}}}abstractNumId"): abstract
        for abstract in numbering.findall("w:abstractNum", _NS)
    }
    # The second-level ordered style's own instance is a single-level decimal
    # ``%1)`` definition, which is what makes the visible marker ``1)``.
    second_level_zero = abstracts["7"].find("w:lvl[@w:ilvl='0']", _NS)
    assert second_level_zero.find("w:numFmt", _NS).get(f"{{{WORD_NS}}}val") == "decimal"
    assert second_level_zero.find("w:lvlText", _NS).get(f"{{{WORD_NS}}}val") == "%1)"
    # The first-level letter instance keeps the ``a)`` form.
    first_level_zero = abstracts["4"].find("w:lvl[@w:ilvl='0']", _NS)
    assert first_level_zero.find("w:numFmt", _NS).get(f"{{{WORD_NS}}}val") == "lowerLetter"
    assert first_level_zero.find("w:lvlText", _NS).get(f"{{{WORD_NS}}}val") == "%1)"
    # The produced document references exactly those two instances.
    assert {row[1] for row in _direct_numbering(output)} == {"3", "8"}


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
    # The list child uses the list's own second-level instance, so it advances no
    # heading counter: the following second-level heading is 1.2, not 1.3.
    assert rows["一节"]["marker"] == "1.1", rows["一节"]
    assert rows["二级一"]["marker"] == "1)", rows["二级一"]
    assert rows["二节"]["marker"] == "1.2", rows["二节"]


def test_t08_second_level_uses_the_independent_ordered_instance(tmp_path: Path) -> None:
    """Second-level items share one independent ``1)`` instance, not the outline."""

    _normalized, output, result = _convert_markdown(
        tmp_path,
        "# 文档\n\n## 一章\n\n1. 一级一\n   1. 二级一\n2. 一级二\n   1. 二级二\n",
    )
    assert result.status == "completed", result.diagnostics

    markers = _visible_markers(output)
    assert markers["一级一"] == "a)", markers
    assert markers["一级二"] == "b)", markers
    # The second level is the template's own single-level ``1)`` instance, so the
    # two children continue its sequence (1), 2)) instead of the outline
    # a.1 / b.1 the synthesized multi-level instance produced.
    assert markers["二级一"] == "1)", markers
    assert markers["二级二"] == "2)", markers


def test_t08_normalization_consumes_the_stored_semantic_level() -> None:
    """The projection consumes the stored level; the container walk is a fallback."""

    from wxdf.normalization import normalize
    from wxdf.template import load_template_bundle

    bundle = load_template_bundle(TEMPLATE)
    ast = _three_level_ordered_ast()

    walked = normalize(ast, bundle, strategy="wx-default-v2")
    walked_node = walked["presentation"]["nodes"]["i4"]
    assert walked_node["semantic_level"] == 2, walked_node
    assert walked_node["target_level"] == 1, walked_node
    assert walked_node["render_level_clamped"] is True, walked_node

    # A stored semantic level that disagrees with the container tree wins: the
    # normalizer reads the list semantics layer's level instead of re-deriving
    # the depth from the tree, and projects that level with the run policy.  Both
    # levels are deeper than the two-level model, so both clamp to level 1.
    stored = normalize(
        ast,
        bundle,
        strategy="wx-default-v2",
        context={"list_container_levels": [{"list_id": "l3", "semantic_level": 4}]},
    )
    node = stored["presentation"]["nodes"]["i4"]
    assert node["semantic_level"] == 4, node
    assert node["target_level"] == 1, node
    assert node["style_role"] == "list_decimal", node
    assert node["render_level_clamped"] is True, node


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
