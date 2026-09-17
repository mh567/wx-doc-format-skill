"""Regression tests for the defects the real-input corpus exposed.

Each test pins one defect found by converting the corpus in
``/Users/harris/Documents/zero项目/input`` and the fix that made the conversion
match the source:

  * a ``w:b`` / ``w:i`` switch that OOXML explicitly turns off must not become a
    mark, because editors write the off form on runs whose style would otherwise
    supply it (11k+ runs across the corpus);
  * a numbering level that declares a visible format is a list level, so circled
    and ideographic sequences are not rejected as damaged definitions;
  * ``w:numId`` 0 detaches a paragraph from numbering, so it is not a damaged
    definition either;
  * the excluded-region pruning drops the associations and captions that point
    into the dropped subtree, so the normalized view stays a valid AST 2.0;
  * a created node joins the scope of the container it lands in, so a caption
    generated for a table inside an appendix is appendix-owned;
  * the render map reports the text a retained inline object shows, so the saved
    output audit compares like with like;
  * the cover heuristic does not treat the body ahead of the first ``Heading 1``
    as a cover, so an interface document that opens with level-2 sections keeps
    them (D7);
  * an explicitly styled heading keeps its role although its text matches the
    API-label protection, which still governs an unstyled label (D8);
  * the table format contract reaches every rendered cell paragraph, so a code
    sample is left aligned on every line and not only on its first (D9);
  * a non-``data`` table keeps the source fill that marks it as code, while a
    data table takes the template look (D10).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.digest import digest
from wxdf.kernel import ConversionKernel
from wxdf.parsers import parse
from wxdf.run_result import ConversionRequest
from wxdf.semantics.lists import analyze_lists
from wxdf.semantics.regions import resolve_source_regions
from wxdf.transaction import _apply_create_node
from wxdf.normalization import normalize
from wxdf.validation import validate_ast

from test_modular_normalization import _profile


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "wx_template.docx"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CAPTION_STYLE_ID = "12"


def _empty_template_document() -> Document:
    """Return the template document with its body content removed."""

    document = Document(str(TEMPLATE))
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    return document


def _source_ref(index: int) -> dict:
    return {
        "source_id": "source",
        "part": "word/document.xml",
        "content_sha256": "sha256:" + "a" * 64,
        "span": {"start": index, "end": index + 1, "unit": "unicode_code_point"},
    }


# ---------------------------------------------------------------------------
# D2: run switch values
# ---------------------------------------------------------------------------

def test_run_switch_value_decides_bold_and_italic(tmp_path: Path) -> None:
    """``w:val="0"`` turns a mark off instead of adding it."""

    source = tmp_path / "switches.docx"
    document = _empty_template_document()
    paragraph = document.add_paragraph()
    explicit_on = paragraph.add_run("显式加粗")
    explicit_on.bold = True
    explicit_off = paragraph.add_run("显式取消加粗")
    explicit_off.bold = False
    italic_off = paragraph.add_run("显式取消倾斜")
    italic_off.italic = False
    document.save(str(source))

    facts = parse(source)
    inline = next(
        unit["inline_content"]
        for unit in facts["units"].values()
        if unit.get("kind") == "paragraph"
        and any(item.get("text") == "显式取消加粗" for item in unit.get("inline_content") or [])
    )
    marks = {
        item.get("text"): item.get("marks")
        for item in inline
        if item.get("kind") == "text" and item.get("text")
    }

    assert marks["显式加粗"] == ["strong"]
    assert marks["显式取消加粗"] == []
    assert marks["显式取消倾斜"] == []


# ---------------------------------------------------------------------------
# D3: numbering formats
# ---------------------------------------------------------------------------

def _add_abstract(document: Document, abstract_id: int, ilvl: int, num_fmt: str, lvl_text: str) -> None:
    root = document.part.numbering_part.element
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), str(ilvl))
    for tag, value in (("w:start", "1"), ("w:numFmt", num_fmt), ("w:lvlText", lvl_text)):
        element = OxmlElement(tag)
        element.set(qn("w:val"), value)
        level.append(element)
    abstract.append(level)
    root.insert(0, abstract)


def _add_num(document: Document, num_id: int, abstract_id: int) -> None:
    root = document.part.numbering_part.element
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    reference = OxmlElement("w:abstractNumId")
    reference.set(qn("w:val"), str(abstract_id))
    num.append(reference)
    root.append(num)


def _apply_numpr(paragraph, num_id: int, ilvl: int) -> None:
    properties = paragraph._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), str(ilvl))
    identifier = OxmlElement("w:numId")
    identifier.set(qn("w:val"), str(num_id))
    num_pr.append(level)
    num_pr.append(identifier)
    properties.append(num_pr)


def test_circled_numbering_format_is_a_list_level(tmp_path: Path) -> None:
    """``decimalEnclosedCircle`` declares a marker, so its level is a list."""

    source = tmp_path / "circled.docx"
    document = Document()
    _add_abstract(document, 26, 0, "decimalEnclosedCircle", "%1")
    _add_num(document, 22, 26)
    for text in ("文件数据处理层：包含可疑文件处理", "分析结果处理"):
        paragraph = document.add_paragraph(text)
        _apply_numpr(paragraph, 22, 0)
    document.save(str(source))

    facts = parse(source)
    entries = [
        item for item in facts["evidence"].values()
        if item.get("num_fmt") == "decimalEnclosedCircle"
    ]

    assert entries, "the circled level must reach the numbering evidence"
    for entry in entries:
        assert entry["numbering_status"] == "detected"
        assert "valid_numbering_definition" in entry["numbering_evidence"]
        assert "invalid_numbering_definition" not in entry["numbering_evidence"]

    result = analyze_lists(facts)
    codes = {item["code"] for item in result["diagnostics"]}

    assert result["items"], "circled numbering must still materialize list items"
    assert not codes & {"missing_numbering_definition", "invalid_numbering_definition"}


# ---------------------------------------------------------------------------
# D4: numId 0
# ---------------------------------------------------------------------------

def test_numbering_suppressed_paragraph_is_not_a_damaged_definition(tmp_path: Path) -> None:
    """``w:numId`` 0 reports suppression and yields no downgrade diagnostic."""

    source = tmp_path / "suppressed.docx"
    document = Document()
    paragraph = document.add_paragraph("总体编制要求：")
    paragraph.style = document.styles["Normal"]
    _apply_numpr(paragraph, 0, 0)
    document.save(str(source))

    facts = parse(source)
    evidence = next(
        item for item in facts["evidence"].values() if item.get("numbering_status") is not None
    )

    assert evidence["numbering_status"] == "ignored"
    assert "numbering_suppressed" in evidence["numbering_evidence"]
    assert "invalid_numbering_definition" not in evidence["numbering_evidence"]

    result = analyze_lists(facts)
    codes = {item["code"] for item in result["diagnostics"]}

    assert not codes & {"missing_numbering_definition", "invalid_numbering_definition"}


# ---------------------------------------------------------------------------
# D5: a list deeper than the two-level model
# ---------------------------------------------------------------------------

def test_deep_orphan_list_is_reported_without_blocking_delivery(tmp_path: Path) -> None:
    """A source level the two-level model cannot express is clamped and reported.

    The defect this pins: the corpus document declared ``w:ilvl=2`` bullets with
    no level-1 parent, every audit passed, and the run still failed with no
    artifact at all.
    """

    source = tmp_path / "deep.docx"
    document = Document()
    _add_abstract(document, 40, 2, "bullet", "\uf0d8")
    _add_num(document, 25, 40)
    document.add_paragraph("工单信息由多个页面维护。")
    for text in ("新建工单信息", "保存附件文件信息"):
        paragraph = document.add_paragraph(text)
        _apply_numpr(paragraph, 25, 2)
    document.save(str(source))

    output = tmp_path / "deep.out.docx"
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))
    report = result.as_report()
    codes = {item["code"] for item in report["diagnostics"]}

    assert report["status"] in {"completed", "completed_with_warnings"}, codes
    assert output.is_file(), "the clamped document must still be delivered"
    # The clamp stays visible: the source depth is reported, not erased.
    assert "list_constraint_violation" in codes, codes
    assert "orphan_list_depth_flattened" in codes, codes


# ---------------------------------------------------------------------------
# D1: excluded-region pruning
# ---------------------------------------------------------------------------

def _region_ast() -> dict:
    """Return an AST whose excluded region owns a captioned table."""

    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "p1": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "正文", "marks": []}],
                    "source_ref": _source_ref(1),
                },
                "t1": {"block_type": "table", "table_type": "data", "source_ref": _source_ref(2)},
                "p2": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "目录单元格", "marks": []}],
                    "source_ref": _source_ref(3),
                },
                "cap1": {
                    "block_type": "paragraph",
                    "role": "caption",
                    "caption_type": "table",
                    "target_id": "t1",
                    "inline_content": [{"kind": "text", "text": "表 目录表", "marks": []}],
                    "source_ref": _source_ref(4),
                },
            },
            "containers": {
                "root": {"kind": "document"},
                "toc": {"kind": "section"},
                "row1": {"kind": "table_row"},
                "cell1": {"kind": "table_cell", "row": 0, "col": 0, "row_span": 1, "col_span": 1},
            },
            "children": {
                "root": ["toc", "cap1", "p1"],
                "toc": ["t1"],
                "t1": ["row1"],
                "row1": ["cell1"],
                "cell1": ["p2"],
            },
            "associations": [{"kind": "caption_target", "source_id": "cap1", "target_id": "t1"}],
            "resources": {},
        },
    }
    validate_ast(ast)
    return ast


def test_excluded_region_prunes_dependent_captions_and_associations() -> None:
    """Dropping a region drops what only referenced it."""

    ast = _region_ast()
    overlay = {"toc": {"semantic_scope": "excluded"}}

    normalized = normalize(ast, _profile(), strategy="wx-default-v2", overlay=overlay)
    document = normalized["source_ast"]["document"]

    validate_ast(normalized["source_ast"])
    assert "toc" not in document["containers"]
    assert "t1" not in document["blocks"]
    assert "cap1" not in document["blocks"]
    assert document["associations"] == []
    # The committed Source AST keeps the region it was handed.
    assert ast["document"]["blocks"]["cap1"]["target_id"] == "t1"


def test_normalized_digest_describes_the_snapshot_it_stores() -> None:
    """The published semantic digest is the one a checkpoint recomputes.

    A checkpoint writes this digest beside the normalized snapshot and
    recomputes it from that snapshot on resume, so a digest taken over the tree
    ``normalize`` was handed rather than the pruned tree it stores made every
    document with an excluded region unresumable.
    """

    ast = _region_ast()
    overlay = {"toc": {"semantic_scope": "excluded"}}

    normalized = normalize(ast, _profile(), strategy="wx-default-v2", overlay=overlay)
    # The digest must survive the serialisation a checkpoint performs.
    stored = json.loads(json.dumps(normalized["source_ast"]))

    assert normalized["semantic_digest"] == digest({
        "source_revision": normalized["source_revision"],
        "source_ast": stored,
        "overlay": normalized["overlay"],
    })
    # Pruning happened, so the digest cannot describe the tree handed in.
    assert normalized["semantic_digest"] != digest({
        "source_revision": ast["revision"],
        "source_ast": ast,
        "overlay": overlay,
    })


# ---------------------------------------------------------------------------
# Enhancement protocol: a document that carries its own table of contents
# ---------------------------------------------------------------------------

def _build_source_toc_docx(path: Path) -> None:
    """Write a DOCX whose own table of contents lives in a ``w:sdt`` control."""

    document = _empty_template_document()
    body = document.element.body
    control = OxmlElement("w:sdt")
    control.append(OxmlElement("w:sdtPr"))
    content = OxmlElement("w:sdtContent")
    field_paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = ' TOC \\o "1-3" \\h \\z \\u '
    run.append(instruction)
    field_paragraph.append(run)
    content.append(field_paragraph)
    for entry in ("概述\t1", "设计说明\t2"):
        paragraph = OxmlElement("w:p")
        entry_run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.set(qn("xml:space"), "preserve")
        text.text = entry
        entry_run.append(text)
        paragraph.append(entry_run)
        content.append(paragraph)
    control.append(content)
    body.insert(0, control)
    for line in ("演示文档", "概述", "本文说明系统定位与建设必要性。", "设计说明", "按总体架构和业务流程组织。"):
        document.add_paragraph(line)
    document.save(str(path))


def _answer_pending_requests(run_dir: Path) -> int:
    """Answer every request without a response, mirroring what an agent does."""

    requests = [
        json.loads(line)
        for line in (run_dir / "llm_requests.jsonl").read_text().splitlines()
        if line.strip()
    ]
    responses_path = run_dir / "llm_responses.jsonl"
    answered = {
        json.loads(line)["request_id"]
        for line in responses_path.read_text().splitlines()
        if line.strip()
    } if responses_path.exists() else set()
    written = 0
    with responses_path.open("a") as handle:
        for index, request in enumerate(requests, 1):
            if request["request_id"] in answered:
                continue
            candidates = []
            packet = request.get("review_packet") or {}
            for target in packet.get("targets") or []:
                if "update_role" not in (target.get("allowed_operations") or []):
                    continue
                values = target.get("allowed_values") or {}
                candidates.append({
                    "candidate_id": f"candidate-{target['block_id']}",
                    "module_id": request["capability"],
                    "module_version": "2.0",
                    "target_source_refs": [target["source_ref"]],
                    "target_node_ids": [target["block_id"]],
                    "proposal": {
                        "operation": "update_role",
                        "property_scope": list(target.get("property_scope") or ["role", "level"]),
                        "value": {
                            "role": (values.get("role") or ["heading"])[0],
                            "level": int((values.get("level") or [1])[0]),
                        },
                    },
                    "positive_evidence": [{"kind": "source_structure"}],
                    "negative_evidence": [],
                    "scope": {"stages": ["role", "normalize", "render", "audit"]},
                    "constraints": [],
                    "confidence": 0.95,
                    "rationale": "source structure supports this level",
                })
            handle.write(json.dumps({
                "request_id": request["request_id"],
                "response_id": f"response-{request['capability']}-{index}",
                "candidate_set_version": "2.0",
                "base_revision": int(request["model_revision"]),
                "view_sha256": request["view_sha256"],
                "candidates": candidates,
            }, ensure_ascii=False) + "\n")
            written += 1
    return written


def _run_protocol(*arguments: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "main.py"), *(str(value) for value in arguments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_enhancement_protocol_resumes_a_document_with_its_own_toc(tmp_path: Path) -> None:
    """The file protocol round trips for a document that has an excluded region.

    The source table of contents is replaced by the template's, so the
    normalized view the checkpoint stores has fewer nodes than the tree it was
    built from.  A digest taken over the tree handed in made every resume of
    such a document fail its own checkpoint validation.
    """

    source = tmp_path / "with-toc.docx"
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _build_source_toc_docx(source)
    output = tmp_path / "out.docx"
    report = tmp_path / "report.json"

    started = _run_protocol(
        "--input", source, "--output", output, "--template", TEMPLATE,
        "--report", report, "--llm-enhance", "all", "--generate-requests", run_dir,
    )
    assert started.returncode == 0, started.stderr
    assert json.loads((run_dir / "run.json").read_text())["status"] == "waiting_external"

    status = "waiting_external"
    for _round in range(4):
        if status != "waiting_external":
            break
        assert _answer_pending_requests(run_dir) > 0
        resumed = _run_protocol("--resume", run_dir / "run.json")
        assert "invalid_checkpoint" not in resumed.stderr, resumed.stderr
        status = json.loads((run_dir / "run.json").read_text())["status"]

    final = json.loads(report.read_text())
    assert final["status"] in {"completed", "completed_with_warnings"}, final.get("diagnostics")
    assert output.is_file()


# ---------------------------------------------------------------------------
# D1b: created nodes join the scope of their container
# ---------------------------------------------------------------------------

def test_created_node_inherits_container_scope_metadata() -> None:
    """A caption generated inside an appendix becomes appendix-owned."""

    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "title": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "附录", "marks": []}],
                    "source_ref": _source_ref(1),
                },
                "t1": {"block_type": "table", "table_type": "data", "source_ref": _source_ref(2)},
            },
            "containers": {
                "root": {"kind": "document"},
                "apx": {
                    "kind": "appendix",
                    "source_classification": "unknown",
                    "title_ref": "title",
                    "appendix_id": "A",
                    "semantic_scope_id": "semantic:appendix:A",
                    "source_number_preserved": True,
                },
            },
            "children": {"root": ["apx"], "apx": ["title", "t1"]},
            "associations": [],
            "resources": {},
        },
    }
    validate_ast(ast)

    _apply_create_node(ast, {
        "value": {
            "node_id": "cap1",
            "parent_id": "apx",
            "index": 1,
            "node": {
                "block_type": "paragraph",
                "role": "caption",
                "caption_type": "table",
                "target_id": "t1",
                "inline_content": [],
                "generated_by": "test",
                "generation_basis": ["t1"],
            },
        }
    })

    created = ast["document"]["blocks"]["cap1"]
    assert created["appendix_id"] == "A"
    assert created["semantic_scope_id"] == "semantic:appendix:A"
    # ``source_number_preserved`` describes the node's own content, not the
    # container's scope.  Inheriting it made a generated caption take the
    # "keep the authored number" branch and render as an empty paragraph.
    assert "source_number_preserved" not in created
    assert ast["document"]["children"]["apx"].index("cap1") == 1


def test_created_node_keeps_its_own_scope_declaration() -> None:
    """An explicit scope on the created node wins over the container's.

    A precedence guard rather than a defect regression: the created node keeps
    its own declaration instead of being overwritten by the container's.
    """

    ast = {
        "schema_version": "2.0",
        "revision": 1,
        "document": {
            "root_id": "root",
            "blocks": {
                "p1": {
                    "block_type": "paragraph",
                    "role": "body",
                    "inline_content": [{"kind": "text", "text": "正文", "marks": []}],
                    "source_ref": _source_ref(1),
                },
            },
            "containers": {"root": {"kind": "document"}, "sec": {"kind": "section"}},
            "children": {"root": ["sec"], "sec": ["p1"]},
            "associations": [],
            "resources": {},
        },
    }
    validate_ast(ast)

    _apply_create_node(ast, {
        "value": {
            "node_id": "p2",
            "parent_id": "sec",
            "index": 1,
            "node": {
                "block_type": "paragraph",
                "role": "body",
                "inline_content": [{"kind": "text", "text": "新增", "marks": []}],
                "generated_by": "test",
                "generation_basis": ["p1"],
                "appendix_id": "B",
            },
        }
    })

    assert ast["document"]["blocks"]["p2"]["appendix_id"] == "B"


# ---------------------------------------------------------------------------
# D6: a caption generated inside an appendix
# ---------------------------------------------------------------------------

def test_generated_caption_inside_an_appendix_keeps_its_label(tmp_path: Path) -> None:
    """A generated appendix caption must not render as an empty paragraph."""

    source = tmp_path / "appendix.docx"
    document = _empty_template_document()
    document.add_paragraph("演示文档")
    document.add_paragraph("附录")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "标识"
    table.cell(1, 1).text = "资源标识"
    document.save(str(source))

    output = tmp_path / "appendix.out.docx"
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))
    report = result.as_report()

    assert report["appendix_preservation_audit"]["passed"] is True, (
        report["appendix_preservation_audit"]["issues"]
    )

    with ZipFile(output) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    captions = []
    for paragraph in root.iter(f"{{{WORD_NS}}}p"):
        style = paragraph.find(f"{{{WORD_NS}}}pPr/{{{WORD_NS}}}pStyle")
        if style is None or style.get(f"{{{WORD_NS}}}val") != CAPTION_STYLE_ID:
            continue
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}t"))
        instruction = "".join(
            node.text or "" for node in paragraph.iter(f"{{{WORD_NS}}}instrText")
        )
        captions.append((text, instruction))

    assert captions, "the appendix data table must carry a caption"
    for text, instruction in captions:
        assert text.strip() or "SEQ" in instruction, (text, instruction)


# ---------------------------------------------------------------------------
# Shared end-to-end helpers
# ---------------------------------------------------------------------------

def _convert(source: Path, output: Path) -> dict:
    """Convert one synthetic source and return its report."""

    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))
    return result.as_report()


def _body_paragraphs(document: Document) -> list:
    from docx.text.paragraph import Paragraph

    return [
        Paragraph(child, document)
        for child in document.element.body.iterchildren()
        if child.tag == qn("w:p")
    ]


def _paragraph_texts(document: Document) -> list[str]:
    return [paragraph.text.strip() for paragraph in _body_paragraphs(document)]


_SHADING_SUCCESSORS = {
    qn("w:tblPr"): ("w:tblLayout", "w:tblCellMar", "w:tblLook"),
    qn("w:tcPr"): ("w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark"),
}


def _set_fill(properties, fill: str) -> None:
    """Shade an element the way OOXML orders it, so the fixture is schema-faithful."""

    parent = properties.getparent()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), fill)
    for tag in _SHADING_SUCCESSORS.get(parent.tag, ()):
        anchor = properties.find(qn(tag))
        if anchor is not None:
            anchor.addprevious(shading)
            return
    properties.append(shading)


def _code_sample_source(tmp_path: Path, *, fill: str | None = "D8D8D8") -> Path:
    """Write a document whose request example is a single-cell code table."""

    source = tmp_path / "code.docx"
    document = _empty_template_document()
    document.add_paragraph("演示文档", style="Heading 1")
    document.add_paragraph("请求示例", style="Heading 2")
    table = document.add_table(rows=1, cols=1, style="Table Grid")
    cell = table.cell(0, 0)
    lines = ["<<Header>>", "POST /api/v1/items", '{"a": 1}', "}", "]"]
    cell.paragraphs[0].text = lines[0]
    for line in lines[1:]:
        cell.add_paragraph(line)
    if fill is not None:
        _set_fill(table._tbl.tblPr, fill)
        _set_fill(cell._tc.get_or_add_tcPr(), fill)
    document.save(str(source))
    return source


# ---------------------------------------------------------------------------
# D5: render map text for retained inline objects
# ---------------------------------------------------------------------------

def test_retained_field_text_matches_the_saved_output(tmp_path: Path) -> None:
    """A copied-back sequence field must not look like changed text."""

    source = tmp_path / "field.docx"
    document = _empty_template_document()
    paragraph = document.add_paragraph()
    paragraph.add_run("图 ")
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "SEQ Figure \\* ARABIC")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.set(qn("xml:space"), "preserve")
    text.text = "1"
    run.append(text)
    field.append(run)
    paragraph._p.append(field)
    paragraph.add_run(" 演示图")
    document.save(str(source))

    output = tmp_path / "field.out.docx"
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=output,
        template_path=TEMPLATE,
        pipeline="modular",
        list_policy="wx-default-v2",
        options={"llm_enhance": "off"},
    ))
    report = result.as_report()

    assert report["saved_output_audit"]["passed"] is True, report["saved_output_audit"]["issues"]
    assert "paragraph_order_changed" not in {
        issue["code"] for issue in report["saved_output_audit"]["issues"]
    }


# ---------------------------------------------------------------------------
# D7: the cover heuristic must not swallow the document body
# ---------------------------------------------------------------------------

def test_body_ahead_of_the_first_heading_one_is_not_a_cover(tmp_path: Path) -> None:
    """A source that opens with level-2 headings keeps those sections.

    The defect this pins: the corpus document's first ``Heading 1`` sits far
    down the file, so the whole prefix before it was scored as a cover and
    excluded.  Two weak signals reached the threshold (a small content table
    counted as cover metadata, and the section title ``接口说明`` scored as a
    cover title), and the no-page-break fallback had no structural guard.
    """

    source = tmp_path / "prefix.docx"
    document = _empty_template_document()
    document.add_paragraph("策略下发", style="Heading 2")
    document.add_paragraph("接口说明", style="Heading 3")
    document.add_paragraph("外部系统通过该接口向IAM禁用用户状态")
    document.add_paragraph("请求参数说明")
    table = document.add_table(rows=2, cols=4, style="Table Grid")
    for index, value in enumerate(("协议", "URL", "方法", "参数类型")):
        table.cell(0, index).text = value
    for index, value in enumerate(("HTTPS", "<host>/api/v1", "POST", "application/json")):
        table.cell(1, index).text = value
    document.add_paragraph("TAC", style="Heading 1")
    document.add_paragraph("TAC限制应用访问", style="Heading 2")
    document.add_paragraph("正文内容")
    document.save(str(source))

    output = tmp_path / "prefix.out.docx"
    report = _convert(source, output)

    assert report["status"] in {"completed", "completed_with_warnings"}, report.get("diagnostics")
    delivered = Document(str(output))
    texts = _paragraph_texts(delivered)

    assert "策略下发" in texts, texts
    assert "请求参数说明" in texts, texts
    assert len(delivered.tables) == 1, "the body table must not be excluded"


# ---------------------------------------------------------------------------
# D8: an explicit heading style outweighs the API-label protection
# ---------------------------------------------------------------------------

def test_api_label_with_a_heading_style_keeps_its_heading_role(tmp_path: Path) -> None:
    """``接口说明`` declared as ``Heading 3`` stays a heading.

    The defect this pins: the API-label protection matched the section titles by
    exact text and forced ``role=body``, so interface documents lost the ``接口说明``
    and ``请求参数`` entries from both the body and the table of contents.
    """

    source = tmp_path / "api-label.docx"
    document = _empty_template_document()
    document.add_paragraph("TAC", style="Heading 1")
    document.add_paragraph("TAC限制应用访问", style="Heading 2")
    document.add_paragraph("接口说明", style="Heading 3")
    document.add_paragraph("外部系统可调用该接口。")
    document.add_paragraph("请求参数", style="Heading 3")
    document.add_paragraph("参数见下表。")
    document.save(str(source))

    output = tmp_path / "api-label.out.docx"
    report = _convert(source, output)

    assert report["status"] in {"completed", "completed_with_warnings"}, report.get("diagnostics")
    styles = {
        paragraph.text.strip(): paragraph.style.name
        for paragraph in _body_paragraphs(Document(str(output)))
    }

    assert styles.get("接口说明") == "Heading 3", styles
    assert styles.get("请求参数") == "Heading 3", styles


def test_api_label_without_a_heading_style_stays_body(tmp_path: Path) -> None:
    """A plain ``请求参数`` label is still not promoted to a heading.

    A precedence guard for the ruling above: only an explicit heading style wins,
    so the protection keeps governing an unstyled label.
    """

    source = tmp_path / "api-plain.docx"
    document = _empty_template_document()
    document.add_paragraph("TAC", style="Heading 1")
    document.add_paragraph("请求参数", style="Normal")
    document.add_paragraph("参数见下表。")
    document.save(str(source))

    output = tmp_path / "api-plain.out.docx"
    report = _convert(source, output)

    assert report["status"] in {"completed", "completed_with_warnings"}, report.get("diagnostics")
    styles = {
        paragraph.text.strip(): paragraph.style.name
        for paragraph in _body_paragraphs(Document(str(output)))
    }

    assert styles.get("请求参数") == "Normal", styles


# ---------------------------------------------------------------------------
# D9: every code-sample paragraph carries the left alignment
# ---------------------------------------------------------------------------

def test_code_sample_cell_paragraphs_are_all_left_aligned(tmp_path: Path) -> None:
    """The whole code block is left aligned, not only its first line.

    The defect this pins: the table contract ran before the cell content was
    rendered, so the policy alignment reached one placeholder paragraph per cell.
    Every later line inherited ``表正文``'s centered alignment, which is invisible
    for a data table but turns a request example into a centered code block.
    """

    source = _code_sample_source(tmp_path)
    output = tmp_path / "code.out.docx"
    report = _convert(source, output)

    assert report["status"] in {"completed", "completed_with_warnings"}, report.get("diagnostics")
    delivered = Document(str(output))
    code_table = next(
        table for table in delivered.tables
        if len(table.rows) == 1 and len(table.rows[0].cells) == 1
    )
    cell = code_table.rows[0].cells[0]

    assert len(cell.paragraphs) == 5
    assert [paragraph.alignment for paragraph in cell.paragraphs] == [0, 0, 0, 0, 0]


# ---------------------------------------------------------------------------
# D10: a code-sample table keeps the source fill
# ---------------------------------------------------------------------------

def test_code_sample_table_keeps_the_source_fill(tmp_path: Path) -> None:
    """The grey box the source uses to mark code survives the conversion."""

    source = _code_sample_source(tmp_path, fill="D8D8D8")
    output = tmp_path / "code-fill.out.docx"
    report = _convert(source, output)

    assert report["status"] in {"completed", "completed_with_warnings"}, report.get("diagnostics")
    delivered = Document(str(output))
    code_table = next(
        table for table in delivered.tables
        if len(table.rows) == 1 and len(table.rows[0].cells) == 1
    )
    cell = code_table.rows[0].cells[0]
    shading = cell._tc.get_or_add_tcPr().find(qn("w:shd"))

    assert shading is not None, "the code sample keeps the fill that marks it as code"
    assert shading.get(qn("w:fill")) == "D8D8D8"


def test_a_genuine_cover_is_still_detected(tmp_path: Path) -> None:
    """The cover guard must not cost a real cover page.

    The guard reads only in the no-page-break fallback, so a page break alone
    still detects a cover, and a cover whose prefix carries one subtitle or a
    single-field metadata table is still cover-like.  A page break is not needed
    for the third shape either: what separates it from the defect above is that
    the prefix holds no section heading of the body and no content table.
    """

    def build(name: str, *, subtitle: bool, page_break: bool, labelled_fields: int) -> Path:
        from docx.enum.text import WD_BREAK

        source = tmp_path / f"{name}.docx"
        document = _empty_template_document()
        document.add_paragraph("系统建设方案", style="文档标题")
        if subtitle:
            document.add_paragraph("技术方案分册", style="Heading 2")
        table = document.add_table(rows=labelled_fields, cols=2, style="Table Grid")
        for index, label in enumerate(("版本", "日期", "密级")[:labelled_fields]):
            table.cell(index, 0).text = label
            table.cell(index, 1).text = "V1.0"
        if page_break:
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        document.add_paragraph("概述", style="Heading 1")
        document.add_paragraph("正文内容。")
        document.save(str(source))
        return source

    cases = {
        # (subtitle, page break, labelled metadata fields, excluded, body start)
        "cover-page-break": (False, True, 3, {0, 1, 2}, 3),
        "cover-subtitle": (True, False, 2, {0, 1, 2}, 3),
        "cover-single-field": (False, False, 1, {0, 1}, 2),
    }

    for name, (subtitle, page_break, fields, excluded, body_start) in cases.items():
        source = build("cover-" + name, subtitle=subtitle, page_break=page_break, labelled_fields=fields)
        resolution = resolve_source_regions(source, parse(source))
        context = resolution["front_matter_context"] or {}

        assert context.get("status") == "cover_detected", (name, context)
        # The title paragraph, the subtitle and the metadata table stay out of the
        # body; the document starts at its first structural heading.
        assert set(resolution["excluded_source_positions"]) == excluded, (name, resolution)
        assert context.get("body_start_source_position") == body_start, (name, context)


def test_a_content_table_in_the_prefix_keeps_the_body(tmp_path: Path) -> None:
    """An unlabelled table ahead of the first heading is body, not cover metadata.

    The deliberate boundary of the guard above: a small table that names no
    metadata field is document content, which is what the protocol/URL table of an
    interface document is.  Treating it as cover metadata is the defect that cost
    the two IAM sections.
    """

    source = tmp_path / "prefix-table.docx"
    document = _empty_template_document()
    document.add_paragraph("系统建设方案", style="文档标题")
    table = document.add_table(rows=2, cols=4, style="Table Grid")
    for index, value in enumerate(("协议", "URL", "方法", "参数类型")):
        table.cell(0, index).text = value
    for index, value in enumerate(("HTTPS", "<host>/api/v1", "POST", "application/json")):
        table.cell(1, index).text = value
    document.add_paragraph("TAC", style="Heading 1")
    document.add_paragraph("正文内容。")
    document.save(str(source))

    output = tmp_path / "prefix-table.out.docx"
    report = _convert(source, output)

    assert report["status"] in {"completed", "completed_with_warnings"}, report.get("diagnostics")
    resolution = resolve_source_regions(source, parse(source))
    context = resolution["front_matter_context"] or {}
    assert context.get("status") != "cover_detected", context
    # Only the title paragraph leaves the body; the content table stays.
    assert set(resolution["excluded_source_positions"]) == {0}, resolution["excluded_source_positions"]
    delivered = Document(str(output))
    assert len(delivered.tables) == 1, "the prefix table must not be excluded"
    assert any(
        "协议" in cell.text for row in delivered.tables[0].rows for cell in row.cells
    )


def test_data_table_does_not_keep_a_source_fill(tmp_path: Path) -> None:
    """A data table takes the template look, so a source fill is not carried over."""

    source = tmp_path / "data-fill.docx"
    document = _empty_template_document()
    document.add_paragraph("演示文档", style="Heading 1")
    table = document.add_table(rows=2, cols=2, style="Table Grid")
    table.cell(0, 0).text = "参数"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "标识"
    table.cell(1, 1).text = "资源标识"
    _set_fill(table.cell(0, 0)._tc.get_or_add_tcPr(), "D8D8D8")
    document.save(str(source))

    output = tmp_path / "data-fill.out.docx"
    report = _convert(source, output)

    assert report["status"] in {"completed", "completed_with_warnings"}, report.get("diagnostics")
    delivered = Document(str(output))
    filled = [
        cell for table in delivered.tables
        for row in table.rows for cell in row.cells
        if cell._tc.get_or_add_tcPr().find(qn("w:shd")) is not None
    ]

    assert filled == []
