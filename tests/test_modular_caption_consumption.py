from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from caption_placement import normalize_caption_placement
from wxdf.auditing import check_saved_output
from wxdf.model import inline_text_projection
from wxdf.normalization import normalize
from wxdf.parsers import parse
from wxdf.semantics.captions import collect_caption_candidates, collect_source_caption_semantics
from wxdf.semantics.caption_bridge import apply_caption_semantics
from wxdf.semantics.source_ast import build_source_ast
from wxdf.semantics.tables import collect_source_table_semantics
from wxdf.resolver import resolve
from wxdf.rendering import render
from wxdf.source_store import SourceStore
from wxdf.template import load_template_bundle
from wxdf.transaction import apply_stage_batch, make_snapshot


def _caption_materialized_ast(facts: dict) -> dict:
    baseline = build_source_ast(facts)
    candidates = collect_caption_candidates(baseline, facts)
    decision_set = resolve(candidates, {"revision": baseline["revision"]})
    assert decision_set["unresolved"] == []
    return apply_stage_batch(
        make_snapshot(baseline),
        decision_set["decisions"],
        expected_revision=baseline["revision"],
    ).snapshot.ast


def test_source_caption_semantics_associate_non_adjacent_figure_and_appendix_table(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    image = tmp_path / "pixel.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db10000000049454e44ae426082"
        )
    )
    document = Document()
    document.add_picture(str(image))
    document.add_paragraph("")
    document.add_paragraph("")
    document.add_paragraph("图1 架构图")
    document.add_paragraph("表A.1 附录数据")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "名称"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "A"
    table.cell(1, 1).text = "示例"
    document.save(source)

    facts = parse(source)
    semantics = collect_source_caption_semantics(
        facts,
        collect_source_table_semantics(facts),
    )

    figure, table_caption = semantics["captions"]
    assert figure["caption_type"] == "figure"
    assert figure["target_location"] == {"kind": "source_position", "value": 0}
    assert figure["source_placement"] == "after"
    assert figure["status"] == "associated"
    assert table_caption["caption_type"] == "table"
    assert table_caption["raw_number"] == "A.1"
    assert table_caption["target_location"] == {"kind": "source_position", "value": 5}
    assert table_caption["source_placement"] == "before"
    assert semantics["issues"] == []


def test_source_caption_semantics_associate_unlabeled_figure_title(tmp_path: Path) -> None:
    source = tmp_path / "unlabeled-figure-caption.docx"
    image = tmp_path / "pixel.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db10000000049454e44ae426082"
        )
    )
    document = Document()
    document.add_picture(str(image))
    title = document.add_paragraph("助手配置用例图")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.save(source)

    facts = parse(source)
    semantics = collect_source_caption_semantics(
        facts,
        collect_source_table_semantics(facts),
    )

    assert semantics["issues"] == []
    assert semantics["captions"] == [
        {
            "node_id": semantics["captions"][0]["node_id"],
            "location": {"kind": "source_position", "value": 1},
            "caption_type": "figure",
            "label": "图",
            "raw_number": None,
            "text": "助手配置用例图",
            "target_node_id": semantics["captions"][0]["target_node_id"],
            "target_location": {"kind": "source_position", "value": 0},
            "required_placement": "after",
            "source_placement": "after",
            "status": "associated",
        }
    ]


def test_source_caption_semantics_do_not_reclassify_numbered_figure_list_content(tmp_path: Path) -> None:
    source = tmp_path / "numbered-figure-content.docx"
    image = tmp_path / "pixel.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db10000000049454e44ae426082"
        )
    )
    document = Document()
    image_only = document.add_paragraph()
    image_only.style = "List Number"
    image_only.add_run().add_picture(str(image))
    text_image = document.add_paragraph("文字图")
    text_image.style = "List Number"
    text_image.add_run().add_picture(str(image))
    document.save(source)

    facts = parse(source)
    semantics = collect_source_caption_semantics(
        facts,
        collect_source_table_semantics(facts),
    )

    assert semantics["captions"] == []
    assert semantics["issues"] == []


def test_source_caption_semantics_associate_unlabeled_table_title(tmp_path: Path) -> None:
    source = tmp_path / "unlabeled-table-caption.docx"
    document = Document()
    document.add_paragraph("配置表 ai_config")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "id"
    table.cell(1, 1).text = "标识"
    document.save(source)

    facts = parse(source)
    semantics = collect_source_caption_semantics(
        facts,
        collect_source_table_semantics(facts),
    )

    assert semantics["issues"] == []
    assert len(semantics["captions"]) == 1
    caption = semantics["captions"][0]
    assert caption["caption_type"] == "table"
    assert caption["label"] == "表"
    assert caption["text"] == "配置表 ai_config"
    assert caption["target_location"] == {"kind": "source_position", "value": 1}
    assert caption["source_placement"] == "before"
    assert caption["status"] == "associated"


def test_source_caption_semantics_does_not_use_center_alignment_alone(tmp_path: Path) -> None:
    source = tmp_path / "centered-table-body.docx"
    document = Document()
    paragraph = document.add_paragraph("说明")
    paragraph.alignment = 1
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "值"
    document.save(source)

    facts = parse(source)
    semantics = collect_source_caption_semantics(
        facts,
        collect_source_table_semantics(facts),
    )

    assert semantics["captions"] == []
    assert semantics["issues"] == []


def test_caption_bridge_and_normalizer_consume_explicit_non_adjacent_target() -> None:
    model = {
        "schema_version": "1.0",
        "document": {
            "blocks": [
                {"id": "image", "block_type": "image", "source": {"source_position": 0}},
                {"id": "body", "block_type": "body", "text": "", "source": {"source_position": 1}},
                {
                    "id": "caption",
                    "block_type": "caption",
                    "caption_type": "figure",
                    "text": "旧文本",
                    "numbering": {"mode": "auto", "label": "图", "raw_number": None},
                    "source": {"source_position": 3},
                },
            ]
        },
    }
    semantics = {
        "captions": [
            {
                "node_id": "caption-source",
                "location": {"kind": "source_position", "value": 3},
                "caption_type": "figure",
                "label": "图",
                "raw_number": "1",
                "text": "架构图",
                "target_location": {"kind": "source_position", "value": 0},
                "required_placement": "after",
                "source_placement": "after",
                "status": "associated",
            }
        ],
        "issues": [],
    }

    applied = apply_caption_semantics(model, semantics)
    normalized = normalize_caption_placement(applied.model)

    caption = next(block for block in normalized["document"]["blocks"] if block["id"] == "caption")
    assert caption["text"] == "架构图"
    assert caption["numbering"]["raw_number"] == "1"
    assert caption["association"]["object_id"] == "image"
    assert normalized["caption_placement"]["passed"] is True
    assert applied.evidence == {
        "candidate_count": 1,
        "consumed_count": 1,
        "associated_count": 1,
        "unmatched_count": 0,
        "ambiguous_count": 0,
    }


def test_mixed_figure_caption_is_split_into_standalone_image_and_text_caption(tmp_path: Path) -> None:
    source = tmp_path / "mixed-caption.docx"
    image = tmp_path / "pixel.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db10000000049454e44ae426082"
        )
    )
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run().add_picture(str(image))
    paragraph.add_run("图3-1 架构图")
    document.save(source)

    store = SourceStore()
    store.open_source(source)
    facts = parse(source, {"source_store": store})
    ast = _caption_materialized_ast(facts)
    children = ast["document"]["children"]["root"]
    caption_id = next(
        node_id for node_id in children
        if ast["document"]["blocks"].get(node_id, {}).get("role") == "caption"
    )
    image_id = next(
        node_id for node_id in children
        if ast["document"]["blocks"].get(node_id, {}).get("block_type") == "image"
    )
    caption = ast["document"]["blocks"][caption_id]
    image_block = ast["document"]["blocks"][image_id]

    assert children == [image_id, caption_id]
    assert caption["target_id"] == image_id
    assert all(item.get("kind") != "image" for item in caption["inline_content"])
    assert image_block["materialization_mode"] == "standalone"

    template_bundle = load_template_bundle(SCRIPTS.parent / "assets" / "wx_template.docx")
    normalized = normalize(ast, template_bundle.profile, strategy="legacy")
    output = tmp_path / "mixed-caption-output.docx"
    resource = next(iter(facts["resources"].values()))
    payload = store.resolve(resource["source_ref"])
    result = render(
        normalized,
        output,
        template_bundle=template_bundle,
        resources={image_block["resource_id"]: payload},
    )
    findings = check_saved_output(output, normalized, result["render_map"])

    assert not any(
        issue["code"] in {"image_count_changed", "image_resource_mismatch"}
        for issue in findings["issues"]
    ), findings
    assert len(result["render_map"][image_id]["image_resources"]) == 1


def test_parenthesized_mixed_figure_caption_is_associated(tmp_path: Path) -> None:
    source = tmp_path / "parenthesized-mixed-caption.docx"
    image = tmp_path / "pixel.png"
    image.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8cfc000000301010018dd8db10000000049454e44ae426082"
        )
    )
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run().add_picture(str(image))
    paragraph.add_run("（图3-1 功能简述图）")
    document.save(source)

    store = SourceStore()
    store.open_source(source)
    facts = parse(source, {"source_store": store})
    semantics = collect_source_caption_semantics(
        facts,
        collect_source_table_semantics(facts),
    )

    assert semantics["issues"] == []
    assert len(semantics["captions"]) == 1
    caption = semantics["captions"][0]
    assert caption["caption_type"] == "figure"
    assert caption["raw_number"] == "3-1"
    assert caption["text"] == "功能简述图"
    assert caption["status"] == "associated"

    ast = _caption_materialized_ast(facts)
    ast_caption_id = next(
        node_id
        for node_id in ast["document"]["children"]["root"]
        if ast["document"]["blocks"].get(node_id, {}).get("role") == "caption"
    )
    ast_caption = ast["document"]["blocks"][ast_caption_id]
    assert inline_text_projection(ast_caption["inline_content"]) == "功能简述图"

    template_bundle = load_template_bundle(SCRIPTS.parent / "assets" / "wx_template.docx")
    normalized = normalize(ast, template_bundle.profile, strategy="legacy")
    output = tmp_path / "parenthesized-mixed-caption-output.docx"
    resource = next(iter(facts["resources"].values()))
    image_id = next(
        node_id
        for node_id in ast["document"]["children"]["root"]
        if ast["document"]["blocks"].get(node_id, {}).get("block_type") == "image"
    )
    image_block = ast["document"]["blocks"][image_id]
    render_result = render(
        normalized,
        output,
        template_bundle=template_bundle,
        resources={image_block["resource_id"]: store.resolve(resource["source_ref"])},
    )
    rendered_caption_texts = [
        paragraph.text
        for paragraph in Document(output).paragraphs
        if "功能简述图" in paragraph.text
    ]
    assert rendered_caption_texts == ["图  功能简述图"]
    assert render_result["render_map"][ast_caption_id]["text"] == "图 1 功能简述图"
