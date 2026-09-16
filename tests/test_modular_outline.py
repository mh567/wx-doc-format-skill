from __future__ import annotations

import copy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.auditing import check_model
from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.normalization import normalize
from wxdf.render_plan import build_render_plan
from wxdf.rendering import _style_for_block
from wxdf.validation import validate_ast


TEMPLATE = ROOT / "assets" / "wx_template.docx"


def _ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


def _heading(node_id: str, index: int, level: int, text: str, **extra) -> dict:
    return {
        "block_type": "paragraph",
        "role": "heading",
        "level": level,
        "inline_content": [{"kind": "text", "text": text, "marks": []}],
        "source_ref": _ref(index),
        **extra,
    }


def _ast(blocks: dict) -> dict:
    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": blocks,
            "containers": {"root": {"kind": "document"}},
            "children": {"root": list(blocks)},
            "associations": [],
            "resources": {},
        },
    }
    validate_ast(ast)
    return ast


def _profile() -> dict:
    return {"resolved_styles": {}, "numbering_ids": {}}


def _normalized(blocks: dict) -> dict:
    return normalize(_ast(blocks), _profile(), strategy="wx-default-v2")


def _outline(normalized: dict) -> dict:
    return normalized["presentation"]["outline"]


def test_source_level_jump_renders_at_the_level_the_outline_allows() -> None:
    normalized = _normalized({
        "h1": _heading("h1", 1, 1, "第一章"),
        "h2": _heading("h2", 2, 2, "第一节"),
        "h5": _heading("h5", 3, 5, "正文的一章设置。"),
    })

    assert _outline(normalized)["h5"] == {"outline_level": 3, "source_level": 5}
    # The source semantic level is preserved, so the parser equivalence contract
    # and the decision ledger keep comparing the declared level.
    assert normalized["source_ast"]["document"]["blocks"]["h5"]["level"] == 5
    assert normalized["source_ast"]["document"]["blocks"]["h2"]["level"] == 2
    assert "h2" not in _outline(normalized)


def test_derived_outline_level_drives_the_render_plan_and_the_heading_style() -> None:
    normalized = _normalized({
        "h1": _heading("h1", 1, 1, "第一章"),
        "h5": _heading("h5", 2, 5, "跳级标题"),
    })

    plan = build_render_plan(normalized)
    operation = next(item for item in plan["operations"] if item["node_id"] == "h5")

    assert operation["presentation"] == {"outline_level": 2, "source_level": 5}
    styles = {f"heading_{level}": f"Heading {level}" for level in range(1, 7)}
    block = normalized["source_ast"]["document"]["blocks"]["h5"]
    assert _style_for_block(block, styles, operation["presentation"]) == "Heading 2"
    assert _style_for_block(block, styles) == "Heading 5"


def test_model_audit_passes_and_reports_the_normalized_divergence() -> None:
    normalized = _normalized({
        "h1": _heading("h1", 1, 1, "第一章"),
        "h2": _heading("h2", 2, 2, "第一节"),
        "h5": _heading("h5", 3, 5, "跳级标题"),
    })

    audit = check_model(normalized)

    assert audit["passed"] is True
    assert audit["issues"] == [{
        "code": "source_heading_level_normalized",
        "node_id": "h5",
        "severity": "high",
        "expected_level": 3,
        "actual_level": 5,
    }]


def test_model_audit_still_fails_a_rendered_outline_violation() -> None:
    # A presentation record that breaks the outline is the contract violation the
    # audit keeps blocking on, whatever the source declared.
    normalized = _normalized({
        "h1": _heading("h1", 1, 1, "第一章"),
        "h2": _heading("h2", 2, 2, "第一节"),
    })
    normalized["presentation"]["outline"] = {"h2": {"outline_level": 5, "source_level": 2}}

    audit = check_model(normalized)

    assert audit["passed"] is False
    assert {
        "code": "heading_level_jump",
        "node_id": "h2",
        "severity": "high",
        "expected_level": 2,
        "actual_level": 5,
    } in audit["issues"]


def test_appendix_headings_are_skipped_without_moving_the_cursor() -> None:
    normalized = _normalized({
        "h1": _heading("h1", 1, 1, "第一章"),
        "a1": _heading("a1", 2, 3, "附录二级标题", appendix_role="heading", appendix_id="A"),
        "h3": _heading("h3", 3, 3, "条标题"),
    })

    # The appendix heading is owned by appendix semantics, so the third heading
    # derives its level from the first chapter heading instead.
    assert "a1" not in _outline(normalized)
    assert _outline(normalized)["h3"] == {"outline_level": 2, "source_level": 3}
    assert check_model(normalized)["passed"] is True


def test_first_heading_is_pulled_to_the_top_level() -> None:
    normalized = _normalized({"h3": _heading("h3", 1, 3, "条标题")})

    assert _outline(normalized)["h3"] == {"outline_level": 1, "source_level": 3}


def test_one_correction_cascades_into_the_following_headings() -> None:
    normalized = _normalized({
        "h5": _heading("h5", 1, 5, "一级"),
        "h3": _heading("h3", 2, 3, "二级"),
        "h4": _heading("h4", 3, 4, "三级"),
    })

    assert _outline(normalized) == {
        "h5": {"outline_level": 1, "source_level": 5},
        "h3": {"outline_level": 2, "source_level": 3},
        "h4": {"outline_level": 3, "source_level": 4},
    }


def test_a_well_formed_outline_keeps_its_presentation_empty() -> None:
    normalized = _normalized({
        "h1": _heading("h1", 1, 1, "第一章"),
        "h2": _heading("h2", 2, 2, "第一节"),
        "h3": _heading("h3", 3, 3, "条标题"),
    })

    assert _outline(normalized) == {}
    assert check_model(normalized)["issues"] == []


def test_the_derivation_leaves_the_source_model_untouched() -> None:
    # Parser equivalence, the decision ledger and migration all compare the
    # declared source level, so deriving the rendered level must not rewrite it.
    ast = _ast({
        "h1": _heading("h1", 1, 1, "第一章"),
        "h5": _heading("h5", 2, 5, "跳级标题"),
        "padding": {
            "block_type": "paragraph",
            "role": "body",
            "inline_content": [{"kind": "text", "text": "正文。", "marks": []}],
            "source_ref": _ref(3),
        },
    })
    baseline = copy.deepcopy(ast)

    normalized = normalize(ast, _profile(), strategy="wx-default-v2")

    assert ast == baseline
    assert normalized["source_ast"] == baseline
    assert normalized["presentation"]["outline"] == {"h5": {"outline_level": 2, "source_level": 5}}


def test_cli_diagnostic_echo_prefers_a_finding_that_still_describes_the_document() -> None:
    from main import _reported_diagnostic_code

    advisory = {"code": "source_heading_level_normalized", "severity": "high", "node_id": "h5"}
    blocking = {"code": "render_plan_list_mismatch", "severity": "warning"}

    assert _reported_diagnostic_code([advisory, blocking]) == "render_plan_list_mismatch"
    assert _reported_diagnostic_code([advisory, {"code": "missing_marker_anchor", "severity": "critical"}]) == "missing_marker_anchor"
    assert _reported_diagnostic_code([advisory]) == "source_heading_level_normalized"
    assert _reported_diagnostic_code([]) == "MODULAR_CONVERSION_FAILED"


def test_pure_conversion_delivers_a_jumped_outline_with_an_advisory_finding(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n### 跳级标题\n\n正文。\n", encoding="utf-8")
    request = ConversionRequest(
        input_path=source,
        output_path=tmp_path / "formal.docx",
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={
            "profile": "native_v2",
            "llm_enhance": "off",
            "source_ast_path": tmp_path / "source-ast.json",
        },
    )

    result = ConversionKernel().convert(request)

    assert result.status == "completed_with_warnings", result.diagnostics
    assert result.report["model_audit"]["passed"] is True
    issues = result.report["model_audit"]["issues"]
    assert [(issue["code"], issue["expected_level"], issue["actual_level"]) for issue in issues] == [
        ("source_heading_level_normalized", 1, 2),
    ]
    assert result.report["heading_outline"]["normalized_count"] == 1
    assert [entry["outline_level"] for entry in result.report["heading_outline"]["entries"]] == [1]
    assert result.report["native_review"]["status"] == "not_requested"
    from docx import Document

    output = Path(result.report["artifacts"]["docx"])
    assert output.is_file()
    styles = {paragraph.text: paragraph.style.name for paragraph in Document(output).paragraphs}
    assert styles["跳级标题"] == "Heading 1"


def test_review_packet_offers_the_rendered_level_as_the_repair(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n### 跳级标题\n\n正文。\n", encoding="utf-8")
    targets: list[dict] = []

    def adapter(payload):
        packet = payload.get("review_packet") or payload.get("context", {}).get("review_packet")
        if not packet:
            return {
                "request_id": payload["request_id"],
                "response_id": "outline-" + payload["request_id"],
                "candidate_set_version": "2.0",
                "base_revision": payload["model_revision"],
                "view_sha256": payload["view_sha256"],
                "candidates": [],
            }
        target = packet["targets"][0]
        targets.append(target)
        return {
            "patch_schema_version": "2.0",
            "patch_id": "outline-review",
            "base_revision": packet["baseline_revision"],
            "phase": "relation",
            "candidates": [{
                "candidate_id": "outline-review-candidate",
                "module_id": "document_review",
                "module_version": "2.0",
                "target_source_refs": [target["source_ref"]],
                "target_node_ids": [target["block_id"]],
                "proposal": {
                    "operation": "update_role",
                    "property_scope": ["role", "level"],
                    "value": {"role": "heading", "level": target["evidence"]["expected_level"]},
                },
                "positive_evidence": [{"kind": "post_audit_review"}],
                "negative_evidence": [],
                "scope": {"stages": ["role", "normalize", "render", "audit"]},
                "constraints": [],
                "rationale": "align the source model with the rendered outline",
                "confidence": 0.95,
            }],
            "decisions": [],
        }

    request = ConversionRequest(
        input_path=source,
        output_path=tmp_path / "formal.docx",
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={
            "profile": "native_v2",
            "llm_enhance": "force-c",
            "enhancement_call": adapter,
            "enhancement_budget": 1,
        },
    )

    result = ConversionKernel().convert(request)

    assert [target["audit_id"].split(":")[1] for target in targets] == ["source_heading_level_normalized"]
    assert targets[0]["allowed_values"] == {"role": ("heading",), "level": (1,)}
    assert targets[0]["evidence"] == {
        "code": "source_heading_level_normalized",
        "node_id": targets[0]["evidence"]["node_id"],
        "severity": "high",
        "expected_level": 1,
        "actual_level": 2,
    }
    assert result.status == "completed", result.diagnostics
    assert result.report["native_review"]["status"] == "accepted"
    assert result.report["model_audit"]["issues"] == []
    assert result.report["heading_outline"]["normalized_count"] == 0
