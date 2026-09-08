from __future__ import annotations

import copy
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from test_modular_ast import _ast
from wxdf.semantics.appendices import annotate_appendix_ranges, collect_appendix_candidates
from wxdf.semantics.captions import audit_caption_placement, build_caption_overlay, invalidate_overlay
from wxdf.semantics.tables import audit_table_semantics, collect_table_candidates
from wxdf.validation import validate_ast


def _semantic_ast() -> dict:
    ast = _ast(4)
    document = ast["document"]
    document["blocks"]["p0"].update({"block_type": "table", "table_type": "layout", "layout": {"source_width": 4200, "source_margins": {"top": 80}, "row_heights": [360], "borders": "none", "autofit": False}})
    document["blocks"]["p1"].update({"role": "caption", "caption_type": "table", "target_id": "p0"})
    document["blocks"]["p2"].update({"block_type": "image", "resource_id": "image-1", "alt_text": "图"})
    document["resources"]["image-1"] = {"kind": "image", "source_ref": {"source_id": "image", "part": "image.bin", "content_sha256": "sha256:" + "a" * 64, "span": {"start": 0, "end": 1, "unit": "unicode_code_point"}}, "content_sha256": "sha256:" + "a" * 64}
    document["blocks"]["p3"].update({"role": "heading", "level": 1})
    document["blocks"]["p3"]["inline_content"] = [{"kind": "text", "text": "附录 A（资料性）", "marks": []}]
    document["blocks"]["p4"] = {"block_type": "paragraph", "role": "heading", "level": 2, "inline_content": [{"kind": "text", "text": "A.1 说明", "marks": []}], "source_ref": copy.deepcopy(document["blocks"]["p3"]["source_ref"])}
    document["containers"]["appendix-a"] = {"kind": "appendix", "source_classification": "informative", "title_ref": "p3", "source_ref": copy.deepcopy(document["blocks"]["p3"]["source_ref"])}
    document["children"] = {"root": ["p1", "p0", "p2", "appendix-a"], "p0": [], "p1": [], "p2": [], "p3": [], "p4": [], "appendix-a": ["p3", "p4"]}
    document["associations"] = [{"kind": "caption_target", "source_id": "p1", "target_id": "p0"}]
    validate_ast(ast)
    return ast


def test_table_semantics_preserve_layout_contract_and_caption_eligibility() -> None:
    ast = _semantic_ast()
    candidates = collect_table_candidates(ast)
    layout = candidates[0]

    assert layout["proposal"]["value"]["table_type"] == "layout"
    assert layout["proposal"]["value"]["caption_eligible"] is False
    assert layout["proposal"]["value"]["layout_contract"]["source_width"] == 4200
    assert audit_table_semantics(ast)["passed"] is True


def test_caption_overlay_binds_target_digest_and_invalidates_after_target_change() -> None:
    ast = _semantic_ast()
    overlay = build_caption_overlay(ast, "p0", "表说明", capability_version="caption_gen@1.0")

    assert overlay["target_content_sha256"].startswith("sha256:")
    assert invalidate_overlay(overlay, ast) is False
    ast["document"]["blocks"]["p0"]["table_type"] = "data"
    assert invalidate_overlay(overlay, ast) is True
    placement = audit_caption_placement(ast)
    assert placement["passed"] is True


def test_appendix_ranges_keep_relative_levels_negative_evidence_and_unique_page_boundary() -> None:
    ast = _semantic_ast()
    candidates = collect_appendix_candidates(ast)
    result = annotate_appendix_ranges(ast)

    assert candidates[0]["accepted"] is True
    assert result["ranges"][0]["appendix_id"] == "A"
    assert result["ranges"][0]["page_break_before"] is True
    assert result["ranges"][0]["heading_levels"]["p4"] == 1
    assert ast["document"]["blocks"]["p4"]["role"] == "heading"
