"""The appendix range lands where the source puts it, and its title leads the TOC.

Rulings and evidence of 2026-09-16 (docs/execution-v9):

- the appendix container's create index was a SourceFacts root position used as an
  AST root position.  ``build_source_ast`` materialises extra image nodes, so the
  container landed a few slots early and the blocks that precede the range were
  pushed behind the whole appendix;
- the template's ``附录标题`` style carries outline level 1, so the template's
  ``TOC \\o "1-3"`` field put the appendix title at TOC level 2, while the
  template's and the source's cached tables of contents both show it at level 1.
  The ruling is option A: the delivered appendix title carries a direct outline
  level 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from docx import Document
from docx.shared import Cm


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel
from wxdf.run_result import ConversionRequest


TEMPLATE = ROOT / "assets" / "wx_template.docx"
REAL_DOCUMENT = Path("/Users/harris/Documents/zero项目/input/技术文件格式及书写要求.docx")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PRECEDING_TEXTS = ("这一段必须留在附录之前。", "正文的一章设置。")


def _appendix_source(tmp_path: Path, *, with_image: bool) -> Path:
    """Build a WX-styled DOCX whose body precedes an appendix range.

    The document starts from the template so the appendix styles exist, and its
    body is cleared first, which is how the other suites build a DOCX source.
    """

    from docx.oxml.ns import qn as _qn

    document = Document(str(TEMPLATE))
    body = document.element.body
    for child in list(body):
        if child.tag != _qn("w:sectPr"):
            body.remove(child)
    document.add_paragraph("其他说明", style="heading 2")
    document.add_paragraph(PRECEDING_TEXTS[0])
    if with_image:
        from PIL import Image

        image = tmp_path / "figure.png"
        Image.new("RGB", (40, 30), (20, 80, 140)).save(image)
        paragraph = document.add_paragraph()
        paragraph.add_run().add_picture(str(image), width=Cm(2))
    document.add_paragraph(PRECEDING_TEXTS[1])
    document.add_paragraph("")
    document.add_paragraph("（资料性）\n附录示例", style="附录标题")
    document.add_paragraph("章一级标题的格式说明", style="附录一级标题")
    document.add_paragraph("条二级标题的格式说明", style="附录二级标题")
    document.add_paragraph("条三级标题的格式说明", style="附录三级标题")
    document.add_paragraph("附录正文。")
    source = tmp_path / f"appendix-{'image' if with_image else 'plain'}.docx"
    document.save(str(source))
    return source


def _convert(tmp_path: Path, source: Path) -> tuple[Path, dict]:
    normalized_path = tmp_path / f"{source.stem}-normalized.json"
    output = tmp_path / f"{source.stem}-out.docx"
    request = ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off", "normalized_ast_path": normalized_path},
    )
    ConversionKernel().convert(request)
    return output, json.loads(normalized_path.read_text(encoding="utf-8"))


def _block_text(document_ast: dict, node_id: str) -> str:
    block = document_ast["blocks"].get(node_id) or {}
    return "".join(
        item.get("text", "")
        for item in block.get("inline_content", [])
        if isinstance(item, dict)
    )


def _root_and_container(normalized: dict) -> tuple[list[str], str]:
    document = normalized["source_ast"]["document"]
    root = list(document["children"]["root"])
    container_id = next(
        node_id
        for node_id, node in document["containers"].items()
        if node.get("kind") == "appendix"
    )
    return root, container_id


def _direct_outline_level(paragraph) -> str | None:
    properties = paragraph._p.find(f"{W}pPr")
    if properties is None:
        return None
    element = properties.find(f"{W}outlineLvl")
    return element.get(f"{W}val") if element is not None else None


# ---------------------------------------------------------------------------
# T01: the container lands where the range starts
# ---------------------------------------------------------------------------

def test_the_appendix_container_follows_every_block_that_precedes_it(tmp_path: Path) -> None:
    """An image before the range must not pull the container in front of the range.

    Nothing may follow the container either: every block the range does not own
    keeps its place before it, including an empty paragraph that carries no text
    of its own.
    """

    _output, normalized = _convert(tmp_path, _appendix_source(tmp_path, with_image=True))

    document = normalized["source_ast"]["document"]
    root, container_id = _root_and_container(normalized)
    preceding = [
        node_id
        for node_id in root
        if _block_text(document, node_id) in PRECEDING_TEXTS
    ]

    assert len(preceding) == len(PRECEDING_TEXTS), root
    assert root.index(container_id) > max(root.index(node_id) for node_id in preceding), root
    assert root[-1] == container_id, root
    roles_by_text = {
        _block_text(document, node_id): document["blocks"][node_id].get("role")
        for node_id in root[: root.index(container_id)]
    }
    assert roles_by_text["其他说明"] == "heading"
    assert roles_by_text[PRECEDING_TEXTS[0]] == "body"
    assert roles_by_text[PRECEDING_TEXTS[1]] == "body"
    assert document["containers"][container_id]["title_ref"] in document["children"][container_id]


def test_the_conversion_reports_clean_audits(tmp_path: Path) -> None:
    """The placement change keeps every audit green, including the appendix one."""

    output = tmp_path / "plain-out.docx"
    request = ConversionRequest(
        input_path=_appendix_source(tmp_path, with_image=True),
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    )
    result = ConversionKernel().convert(request)

    # The fixture's appendix headings make the outline normalizer report the
    # divergence it resolved, which stays non-blocking (decision 8).
    assert result.status in {"completed", "completed_with_warnings"}, result.diagnostics
    for name in ("model_audit", "saved_output_audit", "appendix_preservation_audit"):
        audit = result.report.get(name)
        assert audit and audit.get("passed") is True, (name, audit)
    assert result.report["saved_output_audit"]["unexpected_styles"] == []
    assert (result.report.get("risk_warnings") or []) == []
    assert output.is_file()


def test_the_container_position_does_not_drift_with_extra_ast_nodes(tmp_path: Path) -> None:
    """The range ends the document with and without the extra image node."""

    _plain_output, plain = _convert(tmp_path, _appendix_source(tmp_path, with_image=False))
    _image_output, with_image = _convert(tmp_path, _appendix_source(tmp_path, with_image=True))

    trailing = []
    for normalized in (plain, with_image):
        document = normalized["source_ast"]["document"]
        root, container_id = _root_and_container(normalized)
        trailing.append(
            [
                _block_text(document, node_id)
                for node_id in root[root.index(container_id) + 1 :]
            ]
        )

    assert trailing == [[], []]


# ---------------------------------------------------------------------------
# T02: the appendix title carries its own outline level
# ---------------------------------------------------------------------------

def test_the_appendix_title_records_its_outline_level(tmp_path: Path) -> None:
    _output, normalized = _convert(tmp_path, _appendix_source(tmp_path, with_image=False))

    document = normalized["source_ast"]["document"]
    paragraphs = normalized["presentation"]["paragraphs"]
    title_id = next(
        node_id
        for node_id, block in document["blocks"].items()
        if block.get("appendix_role") == "title"
    )
    level_one_id = next(
        node_id
        for node_id, block in document["blocks"].items()
        if block.get("appendix_role") == "heading"
        and block.get("appendix_relative_level") == 1
    )

    assert paragraphs[title_id] == {"outline_level": 0}
    assert level_one_id not in paragraphs


def test_the_saved_appendix_title_holds_direct_outline_level_zero(tmp_path: Path) -> None:
    output, _normalized = _convert(tmp_path, _appendix_source(tmp_path, with_image=False))

    document = Document(str(output))
    appendix_styles = ("附录标题", "附录一级标题", "附录二级标题", "附录三级标题")
    levels = {
        paragraph.style.name: _direct_outline_level(paragraph)
        for paragraph in document.paragraphs
        if paragraph.style.name in appendix_styles
    }

    assert levels == {
        "附录标题": "0",
        "附录一级标题": None,
        "附录二级标题": None,
        "附录三级标题": None,
    }
    title = next(paragraph for paragraph in document.paragraphs if paragraph.style.name == "附录标题")
    children = [child.tag.rsplit("}", 1)[-1] for child in title._p.find(f"{W}pPr")]
    if "rPr" in children:
        assert children.index("outlineLvl") < children.index("rPr")


# ---------------------------------------------------------------------------
# The reported document
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_DOCUMENT.is_file(), reason="the reported document is not on this machine")
def test_the_reported_document_keeps_its_pre_appendix_paragraphs(tmp_path: Path) -> None:
    output, _normalized = _convert(tmp_path, REAL_DOCUMENT)

    texts = [paragraph.text.strip() for paragraph in Document(str(output)).paragraphs]
    body_index = next(index for index, text in enumerate(texts) if "此模板还设置了页码" in text)
    appendix_index = next(index for index, text in enumerate(texts) if "附录示例" in text)

    assert body_index < appendix_index, texts[body_index - 2 : appendix_index + 2]


@pytest.mark.skipif(not REAL_DOCUMENT.is_file(), reason="the reported document is not on this machine")
def test_the_reported_document_appendix_title_holds_its_outline_level(tmp_path: Path) -> None:
    output, _normalized = _convert(tmp_path, REAL_DOCUMENT)

    titles = [
        paragraph
        for paragraph in Document(str(output)).paragraphs
        if paragraph.style.name == "附录标题"
    ]

    assert titles, "the reported document has an appendix title"
    assert [_direct_outline_level(paragraph) for paragraph in titles] == ["0"]


# ---------------------------------------------------------------------------
# The range audit owns the appendix's whole subtree, not only its direct children
# ---------------------------------------------------------------------------

def _owned_node(**fields: object) -> dict:
    return {"generated_by": "test", "generation_basis": ["test"], **fields}


def _range_ast(*, extra_root: bool) -> tuple[dict, dict]:
    """Return a minimal appendix AST whose range contains a nested list block.

    ``list1`` is a direct child of the appendix and ``tbl1`` is declared as a
    root of the range but sits inside the list item, which is the shape list
    materialization produces when a list item absorbs its following content.
    """

    children = {"root": ["apx"], "apx": ["title", "list1"], "list1": ["item1"], "item1": ["tbl1"]}
    blocks = {
        "title": _owned_node(
            block_type="paragraph", role="body", inline_content=[{"kind": "text", "text": "附录A"}],
            appendix_id="A", appendix_role="title", semantic_scope_id="semantic:appendix:A",
        ),
        "tbl1": _owned_node(block_type="table", table_type="data", appendix_id="A", semantic_scope_id="semantic:appendix:A"),
    }
    root_node_ids = ["title", "list1", "tbl1"]
    if extra_root:
        children = {**children, "root": ["apx", "orphan"]}
        blocks["orphan"] = _owned_node(
            block_type="paragraph", role="body", inline_content=[{"kind": "text", "text": "正文"}],
            appendix_id="A", semantic_scope_id="semantic:appendix:A",
        )
        root_node_ids.append("orphan")
    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "children": children,
            "containers": {
                "root": {"kind": "document"},
                "apx": _owned_node(
                    kind="appendix", appendix_id="A", semantic_scope_id="semantic:appendix:A",
                    title_ref="title", page_break_before=True, source_classification="unknown",
                ),
                "list1": _owned_node(
                    kind="list", list_kind="ordered", source_start=1, source_sequence_id="s1",
                    appendix_id="A", semantic_scope_id="semantic:appendix:A",
                ),
                "item1": _owned_node(
                    kind="list_item", source_marker="a)", source_ordinal=1, source_sequence_id="s1",
                    appendix_id="A", semantic_scope_id="semantic:appendix:A",
                ),
            },
            "blocks": blocks,
            "associations": [],
            "resources": {},
        },
    }
    semantics = {"ranges": [{"appendix_id": "A", "root_node_ids": root_node_ids, "heading_decisions": []}]}
    return ast, semantics


def test_the_range_audit_accepts_a_declared_block_nested_inside_the_appendix() -> None:
    """A block the AST folds into a list item still belongs to the appendix."""

    from wxdf.semantics.appendices import audit_native_appendix

    ast, semantics = _range_ast(extra_root=False)

    outcome = audit_native_appendix(ast, semantics)

    assert outcome["passed"] is True, outcome["issues"]


def test_the_range_audit_still_rejects_a_declared_block_outside_the_appendix() -> None:
    """A declared root that ended up outside the range is a real leak."""

    from wxdf.semantics.appendices import audit_native_appendix

    ast, semantics = _range_ast(extra_root=True)

    outcome = audit_native_appendix(ast, semantics)

    codes = {issue["code"] for issue in outcome["issues"]}
    assert "appendix_range_mismatch" in codes, outcome["issues"]
