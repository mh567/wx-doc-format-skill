from __future__ import annotations

import sys
from pathlib import Path

from docx import Document


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.parsers import parse
from wxdf.semantics.appendices import collect_source_appendix_semantics


def test_source_appendix_semantics_reject_nested_marker_and_build_relative_levels(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    document = Document()
    document.add_heading("附录", level=3)
    document.add_paragraph("正文继续")
    document.add_heading("附录 A", level=2)
    document.add_heading("适用范围", level=2)
    document.add_heading("详细要求", level=3)
    document.add_paragraph("附录正文")
    document.save(source)

    facts = parse(source)
    first_id = facts["children"]["root"][0]
    facts["evidence"][first_id].update({"num_fmt": "decimal", "lvl_text": "%1.%2.%3 "})
    semantics = collect_source_appendix_semantics(facts)

    assert len(semantics["candidates"]) == 2
    assert semantics["candidates"][0]["accepted"] is False
    assert semantics["candidates"][0]["negative_evidence"] == ["nested_main_numbered_heading"]
    assert semantics["candidates"][1]["accepted"] is True
    appendix = semantics["ranges"][0]
    assert appendix["appendix_id"] == "A"
    assert appendix["page_break_before"] is True
    assert [item["relative_level"] for item in appendix["heading_decisions"]] == [1, 2]
