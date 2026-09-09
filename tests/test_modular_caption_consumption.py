from __future__ import annotations

import sys
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from caption_placement import normalize_caption_placement
from wxdf.parsers import parse
from wxdf.semantics.captions import collect_source_caption_semantics
from wxdf.semantics.caption_bridge import apply_caption_semantics
from wxdf.semantics.tables import collect_source_table_semantics


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
