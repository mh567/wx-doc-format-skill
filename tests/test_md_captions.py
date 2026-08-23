from __future__ import annotations

import base64
import sys
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from caption_placement import audit_rendered_caption_placement
from main import clear_document_body
from md_pipeline import parse_md_to_model
from model_normalization import normalize_document_model_simple
from word_model_renderer import render_document_model


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"


def test_markdown_table_caption_is_parsed_and_rendered_as_seq_field(tmp_path: Path) -> None:
    source = tmp_path / "caption.md"
    source.write_text(
        "# 接口清单\n\n表 1 服务接口\n\n| 名称 | 说明 |\n| --- | --- |\n| 模型服务 | 推理 |\n",
        encoding="utf-8",
    )
    report = {"tables_processed": 0, "inferred_lists": [], "repairs": []}

    source_model = parse_md_to_model(source, report, skill_version=lambda: "test")
    caption = source_model["document"]["blocks"][1]

    assert caption["block_type"] == "caption"
    assert caption["caption_type"] == "table"
    assert caption["text"] == "服务接口"
    assert caption["numbering"] == {"mode": "auto", "label": "表", "raw_number": None}

    model = normalize_document_model_simple(source_model, {})
    output = Document(TEMPLATE)
    clear_document_body(output)
    render_document_model(model, output, {}, 0.69, "at-least", {})

    output_path = tmp_path / "caption.docx"
    output.save(output_path)
    rendered = Document(output_path)
    paragraph = next(p for p in rendered.paragraphs if "服务接口" in p.text)

    assert paragraph.text == "表  服务接口"
    assert "SEQ Table" in paragraph._p.xml
    assert ">1<" in paragraph._p.xml
    assert audit_rendered_caption_placement(rendered)["passed"] is True


def test_markdown_figure_caption_is_bound_to_a_local_image(tmp_path: Path) -> None:
    image = tmp_path / "pixel.png"
    image.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nK0AAAAASUVORK5CYII="
    ))
    source = tmp_path / "caption.md"
    source.write_text(
        "# 系统架构\n\n![系统架构](pixel.png)\n\n图 1 系统架构\n",
        encoding="utf-8",
    )
    report = {"tables_processed": 0, "inferred_lists": [], "repairs": []}

    source_model = parse_md_to_model(source, report, skill_version=lambda: "test")

    assert [block["block_type"] for block in source_model["document"]["blocks"]] == [
        "heading", "image", "caption",
    ]

    model = normalize_document_model_simple(source_model, {})
    output = Document(TEMPLATE)
    clear_document_body(output)
    render_document_model(model, output, {}, 0.69, "at-least", {})

    output_path = tmp_path / "caption.docx"
    output.save(output_path)
    rendered = Document(output_path)

    assert len(rendered.inline_shapes) == 1
    assert audit_rendered_caption_placement(rendered)["passed"] is True
