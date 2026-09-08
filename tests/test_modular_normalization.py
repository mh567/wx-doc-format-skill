from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.normalization import NormalizationError, normalize
from wxdf.render_plan import build_render_plan
import wxdf.template as template_module
from wxdf.template import TemplateCapabilityError, preflight_template
from wxdf.validation import validate_ast


def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


def _paragraph(node_id: str, index: int, text: str) -> dict:
    return {
        "block_type": "paragraph",
        "role": "body",
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
        "source_ref": _ref(index),
    }


def _ast() -> dict:
    ast = {
        "schema_version": "2.0",
        "revision": 3,
        "document": {
            "root_id": "root",
            "blocks": {
                "p1": _paragraph("p1", 1, "父项"),
                "p2": _paragraph("p2", 2, "子项"),
                "p3": _paragraph("p3", 3, "第二项"),
            },
            "containers": {
                "root": {"kind": "document"},
                "l1": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "s1"},
                "i1": {"kind": "list_item", "source_marker": "3.", "source_ordinal": 3, "source_sequence_id": "s1"},
                "l2": {"kind": "list", "list_kind": "unordered", "source_start": 1, "source_sequence_id": "s2"},
                "i2": {"kind": "list_item", "source_marker": "•", "source_ordinal": 1, "source_sequence_id": "s2"},
                "i3": {"kind": "list_item", "source_marker": "7.", "source_ordinal": 7, "source_sequence_id": "s1"},
            },
            "children": {"root": ["l1"], "l1": ["i1", "i3"], "i1": ["p1", "l2"], "l2": ["i2"], "i2": ["p2"], "i3": ["p3"]},
            "associations": [],
            "resources": {},
        },
    }
    validate_ast(ast)
    return ast


def _profile() -> dict:
    return {
        "resolved_styles": {
            "list_letter": "1.1一级列项-编号",
            "list_dash": "1.2一级列项-无编号",
            "list_decimal": "2.1二级列项-有编号",
            "list_bullet": "2.2二级列项-无编号",
        },
        "numbering_ids": {"list_letter": 11, "list_decimal": 12},
    }


def test_normalization_preserves_source_semantics_and_separates_strategy_presentation() -> None:
    ast = _ast()
    baseline = copy.deepcopy(ast)

    legacy = normalize(ast, _profile(), strategy="legacy")
    modern = normalize(ast, _profile(), strategy="wx-default-v2")

    assert ast == baseline
    assert legacy["source_revision"] == 3
    assert legacy["semantic_digest"] == modern["semantic_digest"]
    assert legacy["presentation_digest"] != modern["presentation_digest"]
    assert legacy["presentation"]["nodes"]["i1"]["target_level"] == 0
    assert legacy["presentation"]["nodes"]["i2"]["target_level"] == 1
    assert modern["presentation"]["nodes"]["i1"]["marker_kind"] == "ordered"
    assert modern["presentation"]["nodes"]["i1"]["target_ordinal"] == 1
    assert modern["presentation"]["nodes"]["i2"]["target_ordinal"] is None
    assert modern["presentation"]["nodes"]["i3"]["target_ordinal"] == 2


def test_marker_anchor_uses_first_paragraph_and_declares_generated_anchor_when_needed() -> None:
    ast = _ast()
    ast["document"]["children"]["i3"] = []
    ast["document"]["blocks"].pop("p3")

    result = normalize(ast, _profile(), strategy="wx-default-v2")

    assert result["presentation"]["nodes"]["i1"]["marker_anchor"] == "p1"
    assert result["presentation"]["nodes"]["i3"]["marker_anchor"].startswith("generated-anchor-i3")
    assert len(result["generated_nodes"]) == 1


def test_template_preflight_requires_explicit_capabilities() -> None:
    assert preflight_template(_profile(), required_roles=["list_letter", "list_dash"])["ok"] is True

    with pytest.raises(TemplateCapabilityError, match="list_dash"):
        preflight_template({"resolved_styles": {}}, required_roles=["list_dash"])


def test_real_template_bundle_exposes_complete_presentation_contract() -> None:
    bundle = template_module.load_template_bundle(SCRIPTS.parent / "assets" / "wx_template.docx")

    assert bundle.schema_version == "1.0"
    assert bundle.profile["resolved_styles"]["title"] == "文档标题"
    assert bundle.profile["table_style"]["name"] == "Table Grid"
    assert bundle.presentation_contract["toc"] == {
        "mode": "template_fragment",
        "fragment_count": 2,
        "max_level": 4,
        "update_fields_on_open": True,
    }
    assert bundle.presentation_contract["appendix"]["page_break_before"] is True
    assert len(bundle.toc_fragment_xml) == 2


def test_render_plan_is_derived_from_normalized_view() -> None:
    normalized = normalize(_ast(), _profile(), strategy="wx-default-v2")

    plan = build_render_plan(normalized)

    assert plan["source_revision"] == 3
    assert [item["node_id"] for item in plan["operations"] if item["kind"] == "list_item"] == ["i1", "i2", "i3"]
    assert plan["operations"][0]["kind"] == "list_item"


def test_unknown_strategy_is_rejected_before_projection() -> None:
    with pytest.raises(NormalizationError, match="strategy"):
        normalize(_ast(), _profile(), strategy="future")
