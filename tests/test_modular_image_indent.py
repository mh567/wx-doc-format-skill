from __future__ import annotations

import sys
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.normalization import _IMAGE_PARAGRAPH_INDENT, _paragraph_presentation
from wxdf.rendering import _apply_paragraph_alignment, _apply_paragraph_indent


def test_paragraph_presentation_records_centring_and_the_zeroed_indent() -> None:
    ast = {
        "document": {
            "root_id": "root",
            "blocks": {"img": {"block_type": "image"}},
            "containers": {},
            "children": {"root": ["img"]},
        }
    }

    records = _paragraph_presentation(ast)

    # A centred image paragraph who carries no style inherits the template
    # ``Normal`` first-line indent unless the paragraph zeroes it, so the record
    # carries the zeroes the template's own picture paragraph uses.
    assert records["img"] == {"alignment": "center", "indent": dict(_IMAGE_PARAGRAPH_INDENT)}


def test_image_presentation_writes_the_zeroed_indent_next_to_the_centring() -> None:
    document = Document()
    paragraph = document.add_paragraph("")
    record = {"alignment": "center", "indent": dict(_IMAGE_PARAGRAPH_INDENT)}

    _apply_paragraph_alignment(paragraph, record)
    _apply_paragraph_indent(paragraph, record)

    ppr = paragraph._p.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr"
    )
    jc = ppr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}jc")
    ind = ppr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}ind")
    assert jc is not None and jc.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val") == "center"
    assert ind is not None
    assert {name.split("}")[-1]: value for name, value in ind.attrib.items()} == {
        "firstLine": "0",
        "firstLineChars": "0",
        "left": "0",
        "leftChars": "0",
    }


def test_paragraph_without_an_indent_record_keeps_its_style_indent() -> None:
    document = Document()
    paragraph = document.add_paragraph("")

    _apply_paragraph_alignment(paragraph, {"alignment": "center"})
    _apply_paragraph_indent(paragraph, {"alignment": "center"})

    ppr = paragraph._p.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr"
    )
    assert ppr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}ind") is None
