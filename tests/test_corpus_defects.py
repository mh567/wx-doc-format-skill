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
    output audit compares like with like.
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
