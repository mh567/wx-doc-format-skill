from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DOCX_IMPORT_ERROR = None
try:
    from docx import Document
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm
except Exception as exc:
    DOCX_IMPORT_ERROR = exc

from text_utils import (
    scan_non_text_objects,
    looks_like_code_sample_table,
    set_table_autofit_to_window,
)
from document_model import summarize_document_model, validate_document_model, compare_document_models
from docx_pipeline import parse_docx_to_model
from md_pipeline import parse_md_to_model
from model_normalization import normalize_document_model, summarize_source_document_model
from word_model_renderer import (
    render_document_model,
    style_from_profile,
)
from text_utils import set_template_table_properties
from docx_render import render_docx_direct
from audit import audit_document, collect_content_warnings
from reporting import new_report, add_risk_warnings, write_markdown_report
from template_finalizer import apply_template_finalizer
from template_profile import load_template_profile

from llm_enhancer import (
    enhance_document_model,
    build_role_overrides_from_docx,
    compute_suspicion_score,
    should_enhance,
)


SKILL_VERSION = "0.7.0"
DEFAULT_TABLE_ROW_HEIGHT_CM = 0.69
DEFAULT_TABLE_ROW_HEIGHT_RULE = "at-least"


def skill_version() -> str:
    return SKILL_VERSION


def _load_version() -> str:
    try:
        v = Path(__file__).resolve().parent.parent / "VERSION"
        return v.read_text().strip()
    except Exception:
        return SKILL_VERSION


def maybe_reexec_with_skill_venv() -> None:
    if DOCX_IMPORT_ERROR is not None:
        return
    import os, sys
    venv = os.environ.get("WX_DOC_FORMAT_VENV", "")
    if not venv:
        return
    # Already running inside the target venv — nothing to do.
    if sys.prefix == venv or os.path.realpath(sys.prefix) == os.path.realpath(venv):
        return
    venv_python = Path(venv) / "bin" / "python3"
    if venv_python.exists():
        me = Path(__file__).resolve()
        os.execv(str(venv_python), [str(venv_python), str(me)] + sys.argv[1:])


def clear_document_body(doc) -> None:
    from copy import deepcopy
    from docx.oxml.ns import qn
    body = doc.element.body
    sect_pr = None
    if len(body) and body[-1].tag == qn("w:sectPr"):
        sect_pr = deepcopy(body[-1])
    for child in list(body):
        body.remove(child)
    if sect_pr is not None:
        body.append(sect_pr)


def merge_template_numbering_ids(template_profile: dict | None, fallback_ids: dict) -> dict:
    if template_profile is None:
        return fallback_ids
    return template_profile.get("numbering_ids", {})


def ensure_fallback_style_setup(doc) -> dict:
    from fallback_styles import ensure_fallback_styles, ensure_auto_numbering
    ensure_fallback_styles(doc)
    return ensure_auto_numbering(doc)


def build_document_model_from_output_wrapper(doc, source_path: Path, report: dict) -> dict:
    from text_utils import build_document_model_from_output
    return build_document_model_from_output(doc, source_path, report)


def audit_document_wrapper(doc, row_height_cm: float, row_height_rule: str) -> dict:
    from text_utils import heading_level_from_style as _hls, strip_heading_marker as _shm, paragraph_num_info as _pni
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    return audit_document(
        doc, row_height_cm, row_height_rule,
        heading_level_from_style=_hls,
        paragraph_direct_num_info=_pni,
        existing_heading_number=lambda t: bool(_shm(t) != t),
        looks_like_code_sample_table=looks_like_code_sample_table,
        qn=qn,
        center_alignment=WD_ALIGN_PARAGRAPH.CENTER,
    )


def _normalize_template_table(table, row_height_cm: float, row_height_rule: str) -> None:
    set_template_table_properties(table, row_height_cm, row_height_rule)


def _resolve_llm_call(args):
    """Auto-detect LLM backend for semantic enhancement.

    Returns a callable that accepts a prompt string and returns the LLM
    response, or None if no LLM backend is available.
    """
    import os as _os, shutil, subprocess

    # 1. Anthropic API
    key = _os.environ.get("ANTHROPIC_API_KEY")
    if key:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        model = _os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")

        def _llm(prompt: str) -> str:
            msg = client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text

        return _llm

    # 2. OpenAI API
    key = _os.environ.get("OPENAI_API_KEY")
    if key:
        import openai
        client = openai.OpenAI(api_key=key, base_url=_os.environ.get("OPENAI_BASE_URL"))
        model = _os.environ.get("LLM_MODEL", "gpt-4o")

        def _llm(prompt: str) -> str:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            return resp.choices[0].message.content or ""

        return _llm

    # 3. codex CLI (fallback)
    codex = shutil.which("codex")
    if codex:
        def _llm(prompt: str) -> str:
            r = subprocess.run([codex, "exec"], input=prompt.encode(),
                               capture_output=True, timeout=60)
            out = (r.stdout or b"") + (r.stderr or b"")
            return out.decode("utf-8", errors="replace")

        return _llm

    return None


def convert_md(src: Path, doc, report: dict, row_height_cm: float, row_height_rule: str, numbering_ids: dict, args=None) -> dict:
    source_model = parse_md_to_model(src, report, skill_version=skill_version)
    summarize_source_document_model(report, source_model)

    # ── LLM Enhancement ──
    enhance_mode = getattr(args, "llm_enhance", "off") if args is not None else "off"
    llm_call = _resolve_llm_call(args) if enhance_mode != "off" else None
    llm_hint = getattr(args, "llm_hint", None) if args is not None else None

    # Phase A: block role review (before normalization)
    if should_enhance(report, "A", enhance_mode):
        source_model = enhance_document_model(
            source_model, report, phase="A",
            llm_call=llm_call, hint=llm_hint,
        )

    model = normalize_document_model(source_model, report)

    # Phase B + C: structure and table enhancement (after normalization)
    if should_enhance(report, "B", enhance_mode):
        model = enhance_document_model(
            model, report, phase="B",
            llm_call=llm_call, hint=llm_hint,
        )
    if should_enhance(report, "C", enhance_mode):
        model = enhance_document_model(
            model, report, phase="C",
            llm_call=llm_call, hint=llm_hint,
        )

    render_document_model(
        model, doc, report, row_height_cm, row_height_rule, numbering_ids,
        template_profile=report.get("template_profile"),
    )
    return {"source": source_model, "normalized": model}


def convert_docx(src: Path, dst_doc, row_height_cm: float, row_height_rule: str, strict_normalize: bool, report: dict, numbering_ids: dict, args=None) -> dict:
    from docx.oxml.ns import qn as _qn
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    from docx_pipeline import infer_docx_role

    src_doc = Document(src)
    source_model = parse_docx_to_model(
        src, src_doc, strict_normalize, 0,
        skill_version=skill_version,
        new_report=lambda: {},
        iter_blocks=lambda d: (
            Paragraph(child, d) if child.tag == _qn("w:p")
            else Table(child, d)
            for child in d.element.body.iterchildren()
            if child.tag in (_qn("w:p"), _qn("w:tbl"))
        ),
        paragraph_class=Paragraph,
        infer_docx_role=infer_docx_role,
        looks_like_code_sample_table=looks_like_code_sample_table,
        caption_pattern=None,
    )
    summarize_source_document_model(report, source_model)

    # ── LLM Enhancement ──
    enhance_mode = getattr(args, "llm_enhance", "off") if args is not None else "off"
    llm_call = _resolve_llm_call(args) if enhance_mode != "off" else None
    llm_hint = getattr(args, "llm_hint", None) if args is not None else None

    # Phase A: block role review (before normalization)
    if should_enhance(report, "A", enhance_mode):
        source_model = enhance_document_model(
            source_model, report, phase="A",
            llm_call=llm_call, hint=llm_hint,
        )

    model = normalize_document_model(source_model, report)

    # Phase B + C: structure and table enhancement (after normalization)
    if should_enhance(report, "B", enhance_mode):
        model = enhance_document_model(
            model, report, phase="B",
            llm_call=llm_call, hint=llm_hint,
        )
    if should_enhance(report, "C", enhance_mode):
        model = enhance_document_model(
            model, report, phase="C",
            llm_call=llm_call, hint=llm_hint,
        )

    # role_overrides for direct rendering path
    role_overrides = build_role_overrides_from_docx(
        src_doc, strict_normalize, llm_call=llm_call
    ) if llm_call else None

    # Build heading_level_overrides and table_type_overrides from the
    # enhanced (Phase B / C) model.  The index position tracks model
    # block order which matches source-document iteration order for
    # typical documents without empty-paragraph gaps.
    heading_level_overrides = _extract_heading_level_overrides_from_model(model, report)
    table_type_overrides = _extract_table_type_overrides_from_model(model, report)

    render_docx_direct(
        src_doc, dst_doc, report, row_height_cm, row_height_rule, numbering_ids,
        template_profile=report.get("template_profile"),
        strict_normalize=strict_normalize,
        role_overrides=role_overrides,
        heading_level_overrides=heading_level_overrides,
        table_type_overrides=table_type_overrides,
    )
    return {"source": source_model, "normalized": model}


def _extract_heading_level_overrides_from_model(model: dict, report: dict) -> dict[int, int]:
    """Build heading_level_overrides from the enhanced model.

    Maps model-block position → heading level for heading blocks that
    Phase B adjusted (adjust_level / retype-to-heading).  The model-block
    position is a best-effort match for *rerender_docx_direct*'s
    ``_para_idx``; it works when model blocks align 1:1 with source
    paragraphs/tables (the common case for well-formed documents).

    Returns an empty dict when there are no Phase B heading changes.
    """
    # Collect block IDs changed by Phase B heading operations.
    changed_heading_ids: set[str] = set()
    for dec in report.get("llm_enhancer", {}).get("applied", []):
        op = dec.get("operation", "")
        bid = dec.get("block_id", "")
        if op == "adjust_level":
            changed_heading_ids.add(bid)
        elif op == "retype":
            to_type = dec.get("to", {}).get("block_type", "")
            if to_type == "heading":
                changed_heading_ids.add(bid)

    if not changed_heading_ids:
        return {}

    overrides: dict[int, int] = {}
    idx = 0
    for block in model.get("document", {}).get("blocks", []):
        btype = block.get("block_type")
        if btype in ("heading", "body", "list_item", "caption",
                      "table", "image", "appendix", "unknown"):
            if btype == "heading" and block.get("id") in changed_heading_ids:
                overrides[idx] = block.get("level", 1)
            idx += 1

    return overrides


def _extract_table_type_overrides_from_model(model: dict, report: dict) -> dict[int, str]:
    """Build table_type_overrides from the enhanced model.

    Maps model-block position → table type string for tables that
    Phase C changed (set_table_type).  See heading_level_overrides
    for index-mapping caveats.

    Returns an empty dict when there are no Phase C table changes.
    """
    changed_table_ids: set[str] = set()
    for dec in report.get("llm_enhancer", {}).get("applied", []):
        op = dec.get("operation", "")
        if op == "set_table_type":
            bid = dec.get("block_id", "")
            if bid:
                changed_table_ids.add(bid)

    if not changed_table_ids:
        return {}

    overrides: dict[int, str] = {}
    idx = 0
    for block in model.get("document", {}).get("blocks", []):
        btype = block.get("block_type")
        if btype in ("heading", "body", "list_item", "caption",
                      "table", "image", "appendix", "unknown"):
            if btype == "table" and block.get("id") in changed_table_ids:
                overrides[idx] = block.get("table_type", "data")
            idx += 1

    return overrides


def _open_template_renamed_media(template_path):
    """Open template, renaming its media files to _tmpl suffix to avoid conflicts."""
    import zipfile, io, tempfile, os, re
    buf = io.BytesIO()
    with zipfile.ZipFile(template_path, 'r') as z:
        with zipfile.ZipFile(buf, 'w') as out:
            for item in z.infolist():
                data = z.read(item.filename)
                if item.filename.startswith('word/media/') and not item.filename.endswith('/'):
                    dot_idx = item.filename.rfind('.')
                    if dot_idx > 0:
                        new_name = item.filename[:dot_idx] + '_tmpl' + item.filename[dot_idx:]
                    else:
                        new_name = item.filename + '_tmpl'
                    out.writestr(new_name, data)
                elif item.filename == 'word/_rels/document.xml.rels':
                    rels_text = data.decode('utf-8')
                    rels_text = re.sub(
                        r'Target="media/([^"]+?)\.(png|jpg|jpeg|gif|bmp|emf|wmf)"',
                        r'Target="media/\1_tmpl.\2"',
                        rels_text
                    )
                    out.writestr(item, rels_text.encode('utf-8'))
                else:
                    out.writestr(item, data)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.docx')
    tmp.write(buf.getvalue())
    tmp.close()
    from docx import Document
    doc = Document(tmp.name)
    doc._tmpl_path = tmp.name
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert MD or DOCX to template-formatted DOCX.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--template", type=Path, default=None, help="DOCX template driving styles and numbering.")
    parser.add_argument("--strict-normalize", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--report-md", type=Path, default=None)
    parser.add_argument("--ast", type=Path, default=None, help="Normalized document model (Step 2 output).")
    parser.add_argument("--source-ast", type=Path, default=None, help="Parsed source document model (Step 1 output).")
    parser.add_argument("--table-row-height-cm", type=float, default=DEFAULT_TABLE_ROW_HEIGHT_CM)
    parser.add_argument("--table-row-height-rule", choices=["exact", "at-least"], default=DEFAULT_TABLE_ROW_HEIGHT_RULE)
    parser.add_argument("--fail-on-risk", action="store_true")
    parser.add_argument(
        "--llm-enhance",
        choices=["off", "auto", "a", "ab", "abc", "force-a", "force-ab", "force-abc"],
        default="off",
        help="LLM semantic enhancement level (default: off)"
    )
    parser.add_argument(
        "--llm-hint",
        type=str,
        default=None,
        help="Natural-language hint injected into LLM enhancement prompts"
    )
    args = parser.parse_args()

    # Backward compatibility: WX_DOC_LLM_ENHANCE env var → abc mode
    import os as _os
    if args.llm_enhance == "off" and _os.environ.get("WX_DOC_LLM_ENHANCE") == "1":
        args.llm_enhance = "abc"

    global SKILL_VERSION
    SKILL_VERSION = _load_version()

    maybe_reexec_with_skill_venv()

    if DOCX_IMPORT_ERROR is not None:
        if args.input.suffix.lower() == ".docx":
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from format_docx_ooxml import convert_docx_ooxml
            convert_docx_ooxml(args.input, args.output, args.report, args.report_md)
            print(args.output)
            return
        raise SystemExit("python-docx or lxml not available.")

    report = new_report(SKILL_VERSION)
    template_profile = None

    if args.template is not None:
        template_profile = load_template_profile(args.template)
        out_doc = _open_template_renamed_media(args.template)
        clear_document_body(out_doc)
        numbering_ids = merge_template_numbering_ids(template_profile, {})
        report["template_profile"] = {
            "path": str(args.template),
            "resolved_styles": template_profile.get("resolved_styles", {}),
            "missing_roles": template_profile.get("missing_roles", []),
            "numbering_ids": template_profile.get("numbering_ids", {}),
        }
    else:
        out_doc = Document()
        numbering_ids = ensure_fallback_style_setup(out_doc)

    report["non_text_objects"] = scan_non_text_objects(args.input)
    source_document_model = None
    normalized_document_model = None
    suffix = args.input.suffix.lower()

    if suffix in {".md", ".markdown"}:
        models = convert_md(args.input, out_doc, report, args.table_row_height_cm, args.table_row_height_rule, numbering_ids, args)
        source_document_model = models["source"]
        normalized_document_model = models["normalized"]
    elif suffix == ".docx":
        models = convert_docx(args.input, out_doc, args.table_row_height_cm, args.table_row_height_rule, args.strict_normalize, report, numbering_ids, args)
        source_document_model = models["source"]
        normalized_document_model = models["normalized"]
    else:
        raise SystemExit(f"Unsupported input type: {args.input.suffix}")

    # Apply template finalizer (format script rules as format check/fix)
    report["template_finalizer"] = apply_template_finalizer(
        out_doc, template_profile,
        args.table_row_height_cm, args.table_row_height_rule,
        row_height_rule_enum=WD_ROW_HEIGHT_RULE,
        cm=Cm,
        left_alignment=WD_ALIGN_PARAGRAPH.LEFT,
        center_alignment=WD_ALIGN_PARAGRAPH.CENTER,
        set_table_autofit_to_window=set_table_autofit_to_window,
        looks_like_code_sample_table=looks_like_code_sample_table,
    )

    rendered_model = build_document_model_from_output_wrapper(out_doc, args.input, report)
    report["rendered_document_model_summary"] = report.get("document_model_summary", {})
    report["rendered_document_model_issues"] = report.get("document_model_issues", [])

    if normalized_document_model is not None:
        report["document_model_summary"] = summarize_document_model(normalized_document_model)
        report["document_model_issues"] = validate_document_model(normalized_document_model)
    report["document_model_diff"] = compare_document_models(normalized_document_model or source_document_model, rendered_model)
    report["audit"] = audit_document_wrapper(out_doc, args.table_row_height_cm, args.table_row_height_rule)
    report["content_warnings"] = collect_content_warnings(out_doc)
    add_risk_warnings(report, args.table_row_height_rule)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(args.output)
    if hasattr(out_doc, '_tmpl_path'):
        try:
            import os
            os.unlink(out_doc._tmpl_path)
        except Exception:
            pass

    if args.ast is not None and normalized_document_model is not None:
        args.ast.parent.mkdir(parents=True, exist_ok=True)
        args.ast.write_text(json.dumps(normalized_document_model, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.source_ast is not None and source_document_model is not None:
        args.source_ast.parent.mkdir(parents=True, exist_ok=True)
        args.source_ast.write_text(json.dumps(source_document_model, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.report_md is not None:
        write_markdown_report(report, args.report_md)
    if args.fail_on_risk and report["risk_warnings"]:
        raise SystemExit("Conversion completed with risk warnings. Review the report before delivery.")
    print(args.output)


if __name__ == "__main__":
    main()
