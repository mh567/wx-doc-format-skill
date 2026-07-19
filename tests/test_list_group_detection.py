from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from list_group_detection import annotate_semantic_list_groups
from llm_enhancer import _build_phase_a_prompt, compute_suspicion_score, validate_patch
from model_normalization import normalize_document_model_simple
from review_loop import collect_audit_findings


def _body(block_id: str, text: str, position: int, *, style: str = "Normal") -> dict:
    return {
        "id": block_id,
        "block_type": "body",
        "text": text,
        "source": {
            "source_position": position,
            "style": style,
            "layout": {"left_twips": 0, "right_twips": 0, "first_line_twips": 0},
        },
    }


def _model(blocks: list[dict]) -> dict:
    return {"schema_version": "1.0", "document": {"blocks": blocks}}


def test_strong_parallel_group_defaults_to_ordered_in_normal_mode() -> None:
    model = _model([
        _body("a1", "系统参与主体如下：", 0),
        _body("b1", "终端用户：负责发起认证请求；", 1),
        _body("b2", "零信任客户端：负责采集指纹；", 2),
        _body("b3", "IAM系统：负责认证策略判定；", 3),
        _body("b4", "认证系统：负责执行模板比对。", 4),
    ])
    report: dict = {}

    annotate_semantic_list_groups(model, report)
    normalized = normalize_document_model_simple(model, {"parse_report": report})
    items = normalized["document"]["blocks"][1:]
    group = report["semantic_list_groups"][0]

    assert group["status"] == "auto"
    assert group["family"] == "ordered"
    assert group["family_source"] == "semantic_default_ordered"
    assert all(item["block_type"] == "list_item" for item in items)
    assert all(item["list_type"] == "lower_letter_paren" for item in items)
    assert [item["restart"] for item in items] == [True, False, False, False]


def test_intro_typo_and_one_irregular_item_are_tolerated() -> None:
    model = _model([
        _body("a1", "涉及的主要参与主体如下，", 0),
        _body("b1", "终端用户：负责发起认证请求；", 1),
        _body("b2", "零信任客户端负责采集指纹并展示结果；", 2),
        _body("b3", "IAM系统：负责认证策略判定；", 3),
        _body("b4", "认证系统：负责执行模板比对。", 4),
    ])
    report: dict = {}

    annotate_semantic_list_groups(model, report)

    assert len(report["semantic_list_groups"]) == 1
    assert report["semantic_list_groups"][0]["status"] == "auto"
    assert set(report["semantic_list_groups"][0]["item_block_ids"]) == {"b1", "b2", "b3", "b4"}


def test_weak_group_is_reported_without_normal_mode_conversion() -> None:
    model = _model([
        _body("a1", "相关内容如下：", 0),
        _body("b1", "第一段描述了系统当前的运行情况。", 1),
        _body("b2", "第二段继续说明系统当前的配置情况。", 2),
        _body("b3", "第三段补充系统后续的演进方向。", 3),
    ])
    report: dict = {}

    annotate_semantic_list_groups(model, report)
    normalized = normalize_document_model_simple(model, {"parse_report": report})

    assert report["semantic_list_groups"][0]["status"] == "review"
    assert all(block["block_type"] == "body" for block in normalized["document"]["blocks"])


def test_headings_are_hard_group_boundaries() -> None:
    model = _model([
        _body("a1", "角色说明如下：", 0),
        _body("b1", "终端用户：负责发起请求；", 1),
        {"id": "h1", "block_type": "heading", "level": 2, "text": "另一部分", "source": {}},
        _body("b2", "客户端：负责展示结果；", 3),
        _body("b3", "IAM：负责策略判定；", 4),
        _body("b4", "认证系统：负责模板比对。", 5),
    ])
    report: dict = {}

    annotate_semantic_list_groups(model, report)

    assert report["semantic_list_groups"] == []


def test_semantic_default_family_can_be_changed_by_llm() -> None:
    group = {
        "group_id": "g1",
        "status": "review",
        "family": "ordered",
        "family_source": "semantic_default_ordered",
        "item_block_ids": ["b1"],
    }
    model = _model([{
        **_body("b1", "认证能力说明", 1),
        "source": {"list_group_candidate": group},
    }])
    patch = {
        "schema_version": "1.0",
        "phase": "A",
        "decisions": [{
            "block_id": "b1",
            "operation": "retype",
            "from": {"block_type": "body"},
            "to": {"block_type": "list_item", "level": 0, "list_type": "dash"},
            "confidence": 0.9,
        }],
    }

    assert validate_patch(patch, model, frozenset({"retype"})) == []


def test_high_confidence_source_family_cannot_be_overridden() -> None:
    group = {
        "group_id": "g1",
        "status": "review",
        "family": "unordered",
        "family_source": "source_unordered",
        "item_block_ids": ["b1"],
    }
    model = _model([{
        **_body("b1", "认证能力说明", 1),
        "source": {"list_group_candidate": group},
    }])
    patch = {
        "schema_version": "1.0",
        "phase": "A",
        "decisions": [{
            "block_id": "b1",
            "operation": "retype",
            "from": {"block_type": "body"},
            "to": {"block_type": "list_item", "level": 0, "list_type": "lower_letter_paren"},
            "confidence": 0.9,
        }],
    }

    errors = validate_patch(patch, model, frozenset({"retype"}))

    assert any("family evidence" in error["message"] for error in errors)


def test_llm_cannot_retype_only_part_of_a_semantic_group() -> None:
    group = {
        "group_id": "g1",
        "status": "review",
        "family": "ordered",
        "family_source": "semantic_default_ordered",
        "item_block_ids": ["b1", "b2", "b3"],
    }
    blocks = []
    for position in range(1, 4):
        block = _body(f"b{position}", f"第{position}项", position)
        block["source"]["list_group_candidate"] = group
        blocks.append(block)
    model = _model(blocks)
    patch = {
        "schema_version": "1.0",
        "phase": "A",
        "decisions": [{
            "block_id": "b1",
            "operation": "retype",
            "from": {"block_type": "body"},
            "to": {"block_type": "list_item", "level": 0, "list_type": "lower_letter_paren"},
            "confidence": 0.9,
        }],
    }

    errors = validate_patch(patch, model, frozenset({"retype"}))

    assert any("atomically" in error["message"] for error in errors)


def test_review_groups_become_repairable_audit_findings() -> None:
    group = {
        "group_id": "g1",
        "status": "review",
        "family": "ordered",
        "family_source": "semantic_default_ordered",
        "item_block_ids": ["b1", "b2", "b3"],
        "group_confidence": 0.74,
    }
    model = _model([
        {**_body("b1", "第一项说明。", 1), "source": {"list_group_candidate": group}},
        {**_body("b2", "第二项说明。", 2), "source": {"list_group_candidate": group}},
        {**_body("b3", "第三项说明。", 3), "source": {"list_group_candidate": group}},
    ])
    report = {"parse_report": {"semantic_list_groups": [group]}}

    findings = collect_audit_findings(report, model)

    group_findings = [item for item in findings if item["audit_id"].startswith("semantic_list_group:")]
    assert len(group_findings) == 3
    assert all(item["repairable"] for item in group_findings)
    assert all(item["allowed_ops"] == ["retype"] for item in group_findings)


def test_enhanced_mode_receives_group_evidence_and_suspicion_signal() -> None:
    model = _model([
        _body("a1", "相关内容如下：", 0),
        _body("b1", "第一段描述了当前运行情况。", 1),
        _body("b2", "第二段继续说明配置情况。", 2),
        _body("b3", "第三段补充后续演进方向。", 3),
    ])
    parse_report: dict = {}
    annotate_semantic_list_groups(model, parse_report)
    report = {
        "parse_report": parse_report,
        "source_document_model_summary": {
            "block_count": 4,
            "block_counts": {"body": 4},
        },
    }

    prompt = _build_phase_a_prompt(model)

    assert "semantic_list_group_1" in prompt
    assert "familySource=semantic_default_ordered" in prompt
    assert compute_suspicion_score(report) >= 0.15
