from __future__ import annotations

import sys
from pathlib import Path

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from model_normalization import normalize_document_model_simple
from wxdf.parsers import parse
from wxdf.semantics.protected import collect_protected_candidates
from wxdf.semantics.protected_bridge import apply_protected_candidates
from wxdf.semantics.regions import classify_regions
from wxdf.semantics.source_projection import project_source_facts


def _candidate(node_id: str, reason: str, *, role: str | None = None) -> dict:
    value = {"protected": True, "reason": reason}
    if role is not None:
        value["role"] = role
    return {
        "candidate_id": f"protected-{reason}-{node_id}",
        "target_node_ids": [node_id],
        "proposal": {"value": value},
    }


def test_protected_bridge_prevents_api_label_from_becoming_heading() -> None:
    model = {
        "schema_version": "1.0",
        "document": {
            "blocks": [{
                "id": "legacy-api",
                "block_type": "heading",
                "role": "heading",
                "level": 1,
                "text": "API: /v1/items",
                "source": {"line_start": 7, "raw_text": "API: /v1/items"},
            }]
        },
    }
    facts = {
        "units": {"p-api": {"source_ref": {"line_start": 7}}},
        "evidence": {"p-api": {"line_start": 7}},
    }

    applied = apply_protected_candidates(
        model,
        facts,
        [_candidate("p-api", "api_label")],
    )
    normalized = normalize_document_model_simple(applied.model, {})
    block = normalized["document"]["blocks"][0]

    assert block["block_type"] == "body"
    assert block["source"]["protected"] is True
    assert block["source"]["protected_reason"] == "api_label"
    assert applied.evidence == {
        "candidate_count": 1,
        "applied_count": 1,
        "unchanged_count": 0,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }


def test_protected_bridge_applies_explicit_note_and_formula_roles() -> None:
    model = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {
                    "id": "legacy-note",
                    "block_type": "list_item",
                    "level": 0,
                    "list_type": "decimal_paren",
                    "text": "注1：保留说明",
                    "source": {"source_position": 3},
                },
                {
                    "id": "legacy-formula",
                    "block_type": "body",
                    "text": "E = mc2",
                    "source": {"source_position": 4},
                },
            ]
        },
    }
    facts = {
        "units": {
            "p-note": {"source_ref": {"source_position": 3}},
            "p-formula": {"source_ref": {"source_position": 4}},
        },
        "evidence": {
            "p-note": {"source_position": 3},
            "p-formula": {"source_position": 4},
        },
    }

    applied = apply_protected_candidates(
        model,
        facts,
        [
            _candidate("p-note", "note", role="numbered_note"),
            _candidate("p-formula", "formula_opaque", role="formula"),
        ],
    )

    note, formula = applied.model["document"]["blocks"]
    assert note["block_type"] == "body"
    assert note["role"] == "numbered_note"
    assert formula["role"] == "formula"
    assert applied.evidence["applied_count"] == 2
    assert applied.evidence["unmatched_count"] == 0


def test_markdown_remark_uses_one_note_contract_for_candidate_and_projection(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# 标题\n\n**备注：** 保留说明\n", encoding="utf-8")
    facts = parse(source)

    candidates = collect_protected_candidates(facts)
    projection = project_source_facts(facts, classify_regions(facts))

    note = next(item for item in candidates if item["proposal"]["value"].get("role") == "note")
    paragraph = next(item for item in projection["paragraphs"] if item["location"]["value"] == 3)
    assert note["proposal"]["value"]["reason"] == "note"
    assert paragraph["text"] == "保留说明"
    assert paragraph["semantic_kind"] == "note"


def test_table_cell_note_is_deferred_to_table_semantics(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    table = document.add_table(rows=1, cols=1)
    paragraph = table.cell(0, 0).paragraphs[0]
    paragraph.style = "Normal"
    paragraph.add_run("注1：表内说明")
    document.save(source)
    facts = parse(source)

    candidates = collect_protected_candidates(facts)

    assert all(item["proposal"]["value"].get("reason") != "note" for item in candidates)
