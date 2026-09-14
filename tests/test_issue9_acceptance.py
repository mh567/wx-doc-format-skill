from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.decision_ledger import LedgerError, accept_decisions, project_ledger, replay, source_binding
from wxdf.auditing import check_model, check_saved_output
from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.migration import legacy_view
from wxdf.normalization import normalize
from wxdf.parsers import parse
from wxdf.rendering import render
from wxdf.resolver import resolve
from wxdf.checkpoint_store import CheckpointError, load_checkpoint, save_checkpoint
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.source_ast import build_source_ast, materialize_list_semantics
from wxdf.review import accept_candidate
from wxdf.transaction import make_snapshot
from wxdf.validation import validate_ast, validate_checkpoint
from wxdf.contracts import ContractError
from wxdf.decision_ledger import _replay_snapshot

from test_modular_contracts import _valid_checkpoint


SOURCE_DIGEST = "sha256:" + "a" * 64


def _source_ref() -> dict:
    return {
        "source_id": "source",
        "part": "document.md",
        "content_sha256": SOURCE_DIGEST,
        "span": {"start": 0, "end": 1, "unit": "unicode_code_point"},
    }


def _facts(digest: str = SOURCE_DIGEST) -> dict:
    return {"source": {"content_sha256": digest}}


def _source_facts() -> dict:
    return {
        "facts_schema_version": "1.0",
        "source": {"source_id": "source", "source_type": "markdown", "content_sha256": SOURCE_DIGEST},
        "parser": {"id": "issue9", "version": "1.0", "dialect": "fixture", "config_sha256": "sha256:" + "b" * 64},
        "units": {
            "p0": {
                "kind": "paragraph",
                "role": "body",
                "source_ref": _source_ref(),
                "inline_content": [{"kind": "text", "text": "标题内容", "marks": []}],
            }
        },
        "containers": {"root": {"kind": "document"}},
        "children": {"root": ["p0"]},
        "evidence": {"p0": {"marker_kind": "ordered", "marker_text": "1.", "ordinal": 1, "explicit_depth": 0}},
        "resources": {},
        "diagnostics": [],
        "extensions": {},
    }


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


def _heading_ast() -> dict:
    return {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "p0": {
                    "block_type": "paragraph",
                    "role": "heading",
                    "level": 1,
                    "inline_content": [{"kind": "text", "text": "标题内容", "marks": []}],
                    "source_ref": _source_ref(),
                }
            },
            "containers": {"root": {"kind": "document"}},
            "children": {"root": ["p0"], "p0": []},
            "associations": [],
            "resources": {},
        },
    }


def _decision(operation: str, value: dict, *, target_node_ids: list[str], scope: str, source_ref: dict | None = None) -> dict:
    ref = copy.deepcopy(source_ref or _source_ref())
    return {
        "operation": operation,
        "target_source_refs": [ref],
        "target_node_ids": target_node_ids,
        "property_scope": [scope],
        "value": value,
        "preconditions": [{"kind": "source_sha256", "value": ref["content_sha256"]}],
        "evidence": [{"kind": "user_override", "reason": "issue9_reclassification"}],
    }


def _reclassification_decisions() -> list[dict]:
    return [
        _decision(
            "create_node",
            {
                "node_id": "list0",
                "parent_id": "root",
                "index": 0,
                "node": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "reclass"},
            },
            target_node_ids=["list0"],
            scope="children",
        ),
        _decision(
            "create_node",
            {
                "node_id": "item0",
                "parent_id": "list0",
                "index": 0,
                "node": {
                    "kind": "list_item",
                    "legacy_id": "p0",
                    "source_marker": "1.",
                    "source_ordinal": 1,
                    "source_sequence_id": "reclass",
                },
            },
            target_node_ids=["item0"],
            scope="children",
        ),
        _decision(
            "move_node",
            {"new_parent_id": "item0", "index": 0},
            target_node_ids=["p0"],
            scope="children",
        ),
        _decision(
            "update_role",
            {"role": "body"},
            target_node_ids=["p0"],
            scope="role",
        ),
    ]


def _production_reclassification() -> tuple[dict, dict, list[dict]]:
    facts = _source_facts()
    source_ast = build_source_ast(facts)
    source_ast["document"]["blocks"]["p0"].update({"role": "heading", "level": 1})
    list_result = analyze_lists(facts)
    _, candidates, _ = materialize_list_semantics(source_ast, facts, list_result)
    decision_set = resolve(candidates, {"revision": source_ast["revision"]})
    assert decision_set["unresolved"] == []
    operations = [decision["operation"] for decision in decision_set["decisions"]]
    assert operations == ["create_node", "create_node", "move_node"]
    decisions = list(decision_set["decisions"])
    decisions.append(
        _decision(
            "update_role",
            {"role": "body"},
            target_node_ids=["p0"],
            scope="role",
        )
    )
    return facts, source_ast, decisions


def _parsed_heading_reclassification(source: Path) -> tuple[dict, dict, str, list[dict]]:
    facts = parse(source)
    source_ast = build_source_ast(facts)
    heading_id = next(node_id for node_id, node in source_ast["document"]["blocks"].items() if node.get("role") == "heading")
    heading_ref = source_ast["document"]["blocks"][heading_id]["source_ref"]
    decisions = [
        _decision(
            "create_node",
            {"node_id": "list0", "parent_id": "root", "index": 0, "node": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "replay"}},
            target_node_ids=["list0"],
            scope="children",
            source_ref=heading_ref,
        ),
        _decision(
            "create_node",
            {"node_id": "item0", "parent_id": "list0", "index": 0, "node": {"kind": "list_item", "source_marker": "1.", "source_ordinal": 1, "source_sequence_id": "replay"}},
            target_node_ids=["item0"],
            scope="children",
            source_ref=heading_ref,
        ),
        _decision(
            "move_node",
            {"new_parent_id": "item0", "index": 0},
            target_node_ids=[heading_id],
            scope="children",
            source_ref=heading_ref,
        ),
        _decision(
            "update_role",
            {"role": "body"},
            target_node_ids=[heading_id],
            scope="role",
            source_ref=heading_ref,
        ),
    ]
    return facts, source_ast, heading_id, decisions


def test_production_list_candidates_feed_the_reclassification_decision_set() -> None:
    _, _, decisions = _production_reclassification()

    assert [decision["operation"] for decision in decisions] == ["create_node", "create_node", "move_node", "update_role"]
    assert {decision["target_node_ids"][0] for decision in decisions[:2]} == {"implicit-list-p0-0", "native-list-item-p0"}


def test_native_kernel_replays_valid_ledger_before_normalization_and_publish(tmp_path: Path) -> None:
    source = tmp_path / "heading.md"
    source.write_text("# 标题内容\n", encoding="utf-8")
    facts, baseline, heading_id, decisions = _parsed_heading_reclassification(source)
    ledger = _ledger(decisions)
    ledger["source_binding"] = source_binding(facts)
    request = ConversionRequest(
        input_path=source,
        output_path=tmp_path / "replayed.docx",
        template_path=SCRIPTS.parent / "assets" / "wx_template.docx",
        pipeline="modular",
        list_policy="wx-default-v2",
        options={
            "profile": "native_v2",
            "decision_ledger": ledger,
            "source_ast_path": tmp_path / "replayed-source.json",
            "normalized_ast_path": tmp_path / "replayed-normalized.json",
            "report_path": tmp_path / "replayed-report.json",
        },
    )

    result = ConversionKernel().convert(request)

    assert result.status == "completed", result.diagnostics
    replay_report = result.report["decision_ledger_replay"]
    assert replay_report["enabled"] is True
    assert replay_report["active_revision_count"] == 4
    assert all(decision["candidate_ids"] for decision in result.report["decision_set"]["decisions"])
    assert all(decision["candidate_id"] for decision in result.report["decision_set"]["decisions"])
    revisions_by_decision = {
        f"ledger-{revision['revision_id']}": revision
        for revision in ledger["revisions"]
    }
    assert all(
        decision["transaction_id"] == revisions_by_decision[decision["decision_id"]]["transaction_id"]
        for decision in result.report["decision_set"]["decisions"]
    )
    replayed = json.loads((tmp_path / "replayed-source.json").read_text(encoding="utf-8"))
    assert replayed["document"]["children"]["root"] == ["list0"]
    assert replayed["document"]["children"]["list0"] == ["item0"]
    assert replayed["document"]["children"]["item0"] == [heading_id]
    assert replayed["document"]["blocks"][heading_id]["role"] == "body"

    invalidated_ledger = copy.deepcopy(ledger)
    for revision in invalidated_ledger["revisions"]:
        for ref in revision["target_source_refs"]:
            ref["content_sha256"] = "sha256:" + "c" * 64
        for condition in revision["preconditions"]:
            if condition.get("kind") == "source_sha256":
                condition["value"] = "sha256:" + "c" * 64
    blocked_request = ConversionRequest(
        input_path=source,
        output_path=tmp_path / "blocked.docx",
        template_path=SCRIPTS.parent / "assets" / "wx_template.docx",
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"profile": "native_v2", "decision_ledger": invalidated_ledger},
    )

    blocked = ConversionKernel().convert(blocked_request)

    assert blocked.status == "failed"
    assert blocked.diagnostics[0]["code"] == "replay_blocked"
    assert blocked.report["decision_ledger_replay"]["error_code"] == "replay_blocked"


def _ledger(decisions: list[dict] | None = None) -> dict:
    return accept_decisions(
        {"schema_version": "2.0", "revision": 1, "revisions": []},
        decisions or _reclassification_decisions(),
    )


def test_heading_to_list_reclassification_survives_full_recompute_and_file_restore(tmp_path: Path) -> None:
    source = tmp_path / "heading.md"
    source.write_text("# 标题内容\n", encoding="utf-8")
    facts, baseline, heading_id, decisions = _parsed_heading_reclassification(source)
    ledger = _ledger(decisions)

    projected = project_ledger(facts, ledger)
    first = replay(facts, ledger, baseline)
    repeated = replay(facts, ledger, first)
    checkpoint = _valid_checkpoint()
    checkpoint.update({
        "status": "created",
        "resume_stage": None,
        "source_facts": facts,
        "decision_ledger": ledger,
        "source_ast": baseline,
    })
    checkpoint["resource_summary"]["source_sha256"] = facts["source"]["content_sha256"]
    checkpoint["resource_summary"]["fingerprints"] = {
        "source": facts["source"]["content_sha256"],
        "template": "sha256:" + "c" * 64,
        "resources": "sha256:" + "d" * 64,
        "configuration": "sha256:" + "e" * 64,
        "parser": "issue9@1.0",
        "protocol": "1.0",
    }
    checkpoint_path = tmp_path / "checkpoint.json"
    save_checkpoint(checkpoint, checkpoint_path)
    stored_checkpoint = load_checkpoint(checkpoint_path)
    assert stored_checkpoint["applied_transactions"] == []
    changed_facts = copy.deepcopy(facts)
    changed_facts["source"]["content_sha256"] = "sha256:" + "b" * 64
    with pytest.raises(CheckpointError) as changed_error:
        load_checkpoint(checkpoint_path, current_source_facts=changed_facts)
    assert changed_error.value.code == "source_changed"
    persisted = load_checkpoint(checkpoint_path)
    assert {revision["status"] for revision in persisted["decision_ledger"]["revisions"]} == {"invalidated"}
    save_checkpoint(checkpoint, checkpoint_path)
    same_digest_facts = copy.deepcopy(facts)
    same_digest_facts["parser"]["version"] = "9.9"
    with pytest.raises(CheckpointError) as parser_error:
        load_checkpoint(checkpoint_path, current_source_facts=same_digest_facts)
    assert parser_error.value.code == "source_changed"
    save_checkpoint(checkpoint, checkpoint_path)
    recovered_source_path = tmp_path / "recovered-source.json"
    recovered_normalized_path = tmp_path / "recovered-normalized.json"
    recovered_report_path = tmp_path / "recovered-report.json"
    recovered_output_path = tmp_path / "recovered.docx"
    child_code = (
        "import json, sys; "
        "from wxdf.kernel import ConversionKernel, ConversionRequest; "
        "from wxdf.parsers import parse; "
        "from wxdf.checkpoint_store import load_checkpoint; "
        "facts=parse(sys.argv[2]); "
        "checkpoint=load_checkpoint(sys.argv[1], expected_fingerprints={'source': facts['source']['content_sha256']}, expected_contracts={'source_facts':'1.0','ast':'2.0','patch':'2.0','checkpoint':'2.0'}, current_source_facts=facts); "
        "assert all(item['status']=='active' for item in checkpoint['decision_ledger_projection']['revisions']); "
        "request=ConversionRequest(input_path=sys.argv[2], output_path=sys.argv[4], template_path=sys.argv[3], pipeline='modular', list_policy='wx-default-v2', options={'profile':'native_v2','decision_ledger':checkpoint['decision_ledger'],'source_ast_path':sys.argv[5],'normalized_ast_path':sys.argv[6],'report_path':sys.argv[7]}); "
        "print(json.dumps(ConversionKernel().convert(request).to_dict(), ensure_ascii=False, sort_keys=True))"
    )
    child_env = os.environ.copy()
    child_env["PYTHONPATH"] = str(SCRIPTS)
    restored_process = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(checkpoint_path),
            str(source),
            str(SCRIPTS.parent / "assets" / "wx_template.docx"),
            str(recovered_output_path),
            str(recovered_source_path),
            str(recovered_normalized_path),
            str(recovered_report_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=child_env,
    )
    restored_result = json.loads(restored_process.stdout)
    restored = json.loads(recovered_source_path.read_text(encoding="utf-8"))
    restored_checkpoint = load_checkpoint(
        checkpoint_path,
        expected_fingerprints={"source": facts["source"]["content_sha256"]},
        current_source_facts=facts,
    )

    validate_ast(first)
    assert all(revision["status"] == "active" for revision in projected["revisions"])
    assert repeated == first
    assert first == restored
    assert restored_checkpoint["source_ast"] == first
    assert restored_checkpoint["normalized_ast"] is None
    assert restored_checkpoint["source_facts"] == facts
    assert restored_checkpoint["resource_summary"]["fingerprints"]["source"] == facts["source"]["content_sha256"]
    assert restored_checkpoint["applied_transactions"]
    assert set(restored_checkpoint["transaction_content_digests"]) == {
        item["transaction_id"] for item in restored_checkpoint["applied_transactions"]
    }
    assert restored_result["status"] == "completed"
    assert restored_result["report"]["model_audit"]["passed"] is True
    assert restored_result["report"]["saved_output_audit"]["passed"] is True
    list_id = next(node_id for node_id, node in first["document"]["containers"].items() if node.get("kind") == "list")
    item_id = next(node_id for node_id, node in first["document"]["containers"].items() if node.get("kind") == "list_item")
    assert first["document"]["children"]["root"] == [list_id]
    assert first["document"]["children"][list_id] == [item_id]
    assert first["document"]["children"][item_id] == [heading_id]
    assert first["document"]["blocks"][heading_id]["role"] == "body"

    legacy = legacy_view(first)
    legacy_item = next(record for record in legacy["blocks"].values() if record["block_type"] == "list_item")
    assert legacy_item["text"] == "标题内容"

    normalized = normalize(first, _profile(), strategy="wx-default-v2")
    output = tmp_path / "reclassified.docx"
    render_result = render(normalized, output)
    assert check_model(normalized)["passed"] is True
    assert check_saved_output(output, normalized, render_result["render_map"])["passed"] is True


def test_source_change_invalidates_reclassification_and_blocks_restore() -> None:
    ledger = _ledger()
    baseline = copy.deepcopy(ledger)

    projected = project_ledger(_facts("sha256:" + "b" * 64), ledger)

    assert ledger == baseline
    assert {revision["status"] for revision in projected["revisions"]} == {"invalidated"}
    assert {diagnostic["code"] for diagnostic in projected["diagnostics"]} == {"revision_precondition_failed"}
    with pytest.raises(LedgerError, match="invalidated"):
        replay(_facts("sha256:" + "b" * 64), ledger, _heading_ast())


def test_all_ledger_operations_are_idempotent_across_ast_replay() -> None:
    cases = [
        ("update_role", {"role": "note"}, "p0", "role", _heading_ast()),
        ("update_node_semantics", {"semantic_role": "body", "adapter_id": "issue9"}, "p0", "semantics", _heading_ast()),
        ("update_association", {"kind": "source_relation", "source_id": "p0", "target_id": "root"}, "p0", "association", _heading_ast()),
        ("update_overlay", {"protected": True}, "p0", "overlay", _heading_ast()),
        ("set_presentation_override", {"target_level": 1}, "p0", "presentation", _heading_ast()),
        ("delete_node", {}, "p0", "children", _heading_ast()),
    ]

    for operation, value, target_id, scope, baseline in cases:
        ledger = _ledger([_decision(operation, value, target_node_ids=[target_id], scope=scope)])
        first = replay(_facts(), ledger, baseline)
        assert replay(_facts(), ledger, first) == first, operation


def test_accepting_duplicate_revisions_is_idempotent_for_all_operations() -> None:
    cases = [
        ("update_role", {"role": "note"}, "p0", "role"),
        ("update_node_semantics", {"semantic_role": "body"}, "p0", "semantics"),
        ("update_association", {"kind": "source_relation", "source_id": "p0", "target_id": "root"}, "p0", "association"),
        ("update_overlay", {"protected": True}, "p0", "overlay"),
        ("set_presentation_override", {"target_level": 1}, "p0", "presentation"),
        ("delete_node", {}, "p0", "children"),
    ]

    for operation, value, target_id, scope in cases:
        decision = _decision(operation, value, target_node_ids=[target_id], scope=scope)
        ledger = _ledger([decision])
        repeated = accept_decisions(ledger, [decision])
        assert repeated == ledger, operation


def test_same_transaction_with_different_revision_content_is_rejected() -> None:
    ledger = _ledger([_decision("update_role", {"role": "note"}, target_node_ids=["p0"], scope="role")])
    conflicting = copy.deepcopy(ledger["revisions"][0])
    conflicting["revision_id"] = "r-conflict"
    conflicting["decision"]["value"] = {"role": "heading", "level": 1}

    with pytest.raises(LedgerError) as caught:
        accept_decisions(ledger, [conflicting])
    assert caught.value.code == "transaction_conflict"

    first = _decision("update_role", {"role": "note"}, target_node_ids=["p0"], scope="role")
    second = _decision("update_role", {"role": "heading", "level": 1}, target_node_ids=["p0"], scope="role")
    first["transaction_id"] = "tx-batch"
    second["transaction_id"] = "tx-batch"
    with pytest.raises(LedgerError) as batch_caught:
        accept_decisions({"schema_version": "2.0", "revision": 1, "revisions": []}, [first, second])
    assert batch_caught.value.code == "transaction_conflict"

    batch = accept_decisions(
        {"schema_version": "2.0", "revision": 1, "revisions": []},
        {"transaction_id": "tx-batch", "decisions": [first, second]},
    )
    assert len(batch["revisions"]) == 2
    assert len({revision["transaction_content_sha256"] for revision in batch["revisions"]}) == 1
    assert accept_decisions(batch, {"transaction_id": "tx-batch", "decisions": [first, second]}) == batch


def test_revision_without_source_digest_is_invalidated_on_projection() -> None:
    decision = _decision("update_role", {"role": "note"}, target_node_ids=["p0"], scope="role")
    decision["target_source_refs"] = []
    decision["preconditions"] = []
    ledger = _ledger([decision])
    assert ledger["revisions"][0]["status"] == "invalidated"
    projected = project_ledger(_facts(), ledger)

    assert projected["revisions"][0]["status"] == "invalidated"
    assert projected["diagnostics"] == []


def test_overlapping_property_scopes_are_conflicted() -> None:
    first = _decision("update_role", {"role": "note"}, target_node_ids=["p0"], scope="role")
    second = _decision("update_role", {"role": "heading", "level": 1}, target_node_ids=["p0"], scope="level")
    second["property_scope"] = ["role", "level"]
    projected = project_ledger(_facts(), _ledger([first, second]))

    assert {revision["status"] for revision in projected["revisions"]} == {"conflicted"}
    assert any(diagnostic["code"] == "revision_conflict" for diagnostic in projected["diagnostics"])


def test_checkpoint_recovery_preserves_overlay_target_fields(tmp_path: Path) -> None:
    source = tmp_path / "heading.md"
    source.write_text("# 标题内容\n", encoding="utf-8")
    facts, baseline, heading_id, _ = _parsed_heading_reclassification(source)
    overlay = _decision(
        "update_overlay",
        {"kind": "caption_text", "target_node_id": heading_id, "text": "标题题注"},
        target_node_ids=[heading_id],
        scope="overlay",
        source_ref=baseline["document"]["blocks"][heading_id]["source_ref"],
    )
    checkpoint = _valid_checkpoint()
    checkpoint.update({
        "status": "created",
        "resume_stage": None,
        "source_facts": facts,
        "decision_ledger": _ledger([overlay]),
        "source_ast": baseline,
    })
    checkpoint["resource_summary"]["source_sha256"] = facts["source"]["content_sha256"]
    checkpoint["resource_summary"]["fingerprints"] = {"source": facts["source"]["content_sha256"]}
    path = tmp_path / "overlay-checkpoint.json"
    save_checkpoint(checkpoint, path)

    restored = load_checkpoint(path, current_source_facts=facts)

    assert restored["content_overlay"]
    assert restored["content_overlay"][0]["target_node_id"] == heading_id
    assert restored["content_overlay"][0]["node_id"] == heading_id


def test_multi_target_overlay_replay_requires_every_target_to_be_present() -> None:
    baseline = _heading_ast()
    baseline["document"]["blocks"]["p1"] = copy.deepcopy(baseline["document"]["blocks"]["p0"])
    baseline["document"]["blocks"]["p1"]["source_ref"] = _source_ref()
    baseline["document"]["children"]["root"] = ["p0", "p1"]
    baseline["document"]["children"]["p1"] = []
    validate_ast(baseline)
    decision = _decision("update_overlay", {"protected": True}, target_node_ids=["p0", "p1"], scope="overlay")
    ledger = _ledger([decision])

    snapshot = _replay_snapshot(_facts(), ledger, make_snapshot(baseline))
    assert set(snapshot.overlays) == {"p0", "p1"}
    snapshot.overlays.pop("p1")
    recovered = _replay_snapshot(_facts(), ledger, snapshot)

    assert set(recovered.overlays) == {"p0", "p1"}


def test_move_replay_checks_order_and_semantic_metadata() -> None:
    baseline = _heading_ast()
    baseline["document"]["blocks"]["p1"] = copy.deepcopy(baseline["document"]["blocks"]["p0"])
    baseline["document"]["blocks"]["p1"]["source_ref"] = _source_ref()
    baseline["document"]["children"]["root"] = ["p0", "p1"]
    baseline["document"]["children"]["p1"] = []
    validate_ast(baseline)
    decision = _decision(
        "move_node",
        {"new_parent_id": "root", "index": 1, "semantic_metadata": {"semantic_scope_id": "scope:new"}},
        target_node_ids=["p0"],
        scope="children",
    )
    ledger = _ledger([decision])
    first = replay(_facts(), ledger, baseline)
    first["document"]["children"]["root"] = ["p0", "p1"]
    first["document"]["blocks"]["p0"]["semantic_scope_id"] = "scope:old"
    validate_ast(first)
    corrected = replay(_facts(), ledger, first)

    assert corrected["document"]["children"]["root"] == ["p1", "p0"]
    assert corrected["document"]["blocks"]["p0"]["semantic_scope_id"] == "scope:new"


def test_rejected_candidate_preserves_ledger_ast_and_model_summary() -> None:
    baseline_ast = _heading_ast()
    baseline_normalized = normalize(baseline_ast, _profile(), strategy="wx-default-v2")
    baseline_bundle = {
        "source_ast": baseline_ast,
        "normalized_ast": baseline_normalized,
        "decision_ledger": _ledger(),
        "model_summary": check_model(baseline_normalized),
        "round": 0,
    }
    changed_ast = copy.deepcopy(baseline_ast)
    changed_ast["document"]["blocks"]["p0"]["inline_content"][0]["text"] = "内容已变更"
    changed_normalized = normalize(changed_ast, _profile(), strategy="wx-default-v2")
    candidate_bundle = {
        **baseline_bundle,
        "source_ast": changed_ast,
        "normalized_ast": changed_normalized,
        "model_summary": check_model(changed_normalized),
    }

    decision, accepted = accept_candidate(
        baseline_bundle,
        candidate_bundle,
        {"baseline": [], "candidate": []},
        decisions=[{"target_node_ids": ["p0"]}],
    )

    assert decision.accepted is False
    assert decision.reason == "content_changed"
    assert accepted["source_ast"] == baseline_bundle["source_ast"]
    assert accepted["decision_ledger"] == baseline_bundle["decision_ledger"]
    assert accepted["model_summary"] == baseline_bundle["model_summary"]


def test_duplicate_created_node_ids_conflict_even_when_property_scopes_differ() -> None:
    decisions = _reclassification_decisions()
    decisions[1]["value"]["node"]["source_marker"] = "2."
    decisions[1]["property_scope"] = ["source_marker"]
    decisions[1]["value"]["node_id"] = "list0"
    decisions[1]["target_node_ids"] = ["list0"]
    ledger = accept_decisions({"schema_version": "2.0", "revision": 1, "revisions": []}, decisions[:2])

    projected = project_ledger(_facts(), ledger)

    assert {revision["status"] for revision in projected["revisions"]} == {"conflicted"}
    assert projected["diagnostics"][0]["code"] == "revision_conflict"


def test_duplicate_identical_created_node_ids_are_deduplicated_before_replay() -> None:
    decisions = _reclassification_decisions()[:1]
    ledger = accept_decisions(
        {"schema_version": "2.0", "revision": 1, "revisions": []},
        decisions + copy.deepcopy(decisions),
    )

    projected = project_ledger(_facts(), ledger)
    replayed = replay(_facts(), ledger, _heading_ast())

    assert len(projected["revisions"]) == 1
    assert projected["revisions"][0]["status"] == "active"
    assert replayed["document"]["children"]["root"][0] == "list0"


def test_created_node_id_already_in_source_ast_blocks_replay() -> None:
    existing = _heading_ast()
    existing["document"]["containers"]["list0"] = {
        "kind": "list",
        "list_kind": "ordered",
        "source_start": 1,
        "source_sequence_id": "existing",
    }
    existing["document"]["children"]["root"].append("list0")
    existing["document"]["children"]["list0"] = []
    validate_ast(existing)

    with pytest.raises(LedgerError, match="conflicted") as caught:
        replay(_facts(), _ledger(_reclassification_decisions()[:1]), existing)
    assert caught.value.code == "replay_blocked"


def test_cyclic_created_node_dependencies_are_conflicted_during_projection() -> None:
    decisions = [
        _decision(
            "create_node",
            {"node_id": "list-a", "parent_id": "list-b", "node": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "cycle"}},
            target_node_ids=["list-a"],
            scope="children",
        ),
        _decision(
            "create_node",
            {"node_id": "list-b", "parent_id": "list-a", "node": {"kind": "list", "list_kind": "ordered", "source_start": 1, "source_sequence_id": "cycle"}},
            target_node_ids=["list-b"],
            scope="children",
        ),
    ]
    ledger = _ledger(decisions)

    projected = project_ledger(_facts(), ledger, existing_node_ids={"root"})

    assert {revision["status"] for revision in projected["revisions"]} == {"conflicted"}
    assert {diagnostic["code"] for diagnostic in projected["diagnostics"]} == {"revision_conflict"}


def test_malformed_create_node_revision_is_rejected_as_structured_ledger_error() -> None:
    base = {"schema_version": "2.0", "revision": 1, "revisions": []}
    malformed_values = [
        "invalid",
        {},
        {"node_id": "list0", "parent_id": "root"},
        {"node_id": "list0", "parent_id": "root", "node": {}},
        {"node_id": "list0", "parent_id": "root", "node": {"kind": "bogus"}},
        {"node_id": "list0", "parent_id": "root", "node": {"block_type": True}},
        {"node_id": "list0", "parent_id": "root", "node": {"kind": "list", "list_kind": "ordered"}},
    ]

    for value in malformed_values:
        malformed = _decision("create_node", value, target_node_ids=["list0"], scope="children")
        with pytest.raises(LedgerError, match="create_node") as caught:
            accept_decisions(base, [malformed])
        assert caught.value.code == "invalid_decision_value"


def test_non_create_revision_requires_a_target_node() -> None:
    malformed = _decision("update_role", {"role": "body"}, target_node_ids=[], scope="role")

    with pytest.raises(LedgerError) as caught:
        accept_decisions({"schema_version": "2.0", "revision": 1, "revisions": []}, [malformed])
    assert caught.value.code == "invalid_target"


def test_checkpoint_validation_rejects_malformed_v2_create_revision() -> None:
    checkpoint = _valid_checkpoint()
    checkpoint["source_facts"] = _source_facts()
    checkpoint["source_ast"] = _heading_ast()
    checkpoint["decision_ledger"] = _ledger()
    checkpoint["decision_ledger"]["revisions"][0]["decision"]["value"] = "invalid"
    checkpoint["decision_ledger"]["schema_version"] = "2.0"

    with pytest.raises(ContractError) as caught:
        validate_checkpoint(checkpoint)
    assert caught.value.code == "invalid_revision"


def test_checkpoint_validation_rejects_transaction_digest_mismatch() -> None:
    checkpoint = _valid_checkpoint()
    checkpoint["applied_transactions"] = [{"transaction_id": "tx", "content_sha256": "sha256:" + "a" * 64}]
    checkpoint["transaction_content_digests"] = {"tx": "sha256:" + "b" * 64}

    with pytest.raises(ContractError) as caught:
        validate_checkpoint(checkpoint)
    assert caught.value.code == "transaction_state_mismatch"
