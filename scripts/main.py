from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
import sys
from pathlib import Path

def _deferred_legacy_callable(module_name: str, attribute: str):
    """Resolve one stable legacy helper when its module is unavailable.

    The native v2 chain imports this module while the legacy parser, model
    normalization, finalizer and renderer are not importable.  The bindings
    below still prefer the direct import, so a normal run loads the legacy
    modules at their original position in the import order and keeps the
    legacy output contract unchanged.
    """

    def deferred(*args, **kwargs):
        from importlib import import_module

        return getattr(import_module(module_name), attribute)(*args, **kwargs)

    deferred.__name__ = attribute
    deferred.__qualname__ = attribute
    deferred.__doc__ = f"Deferred access to {module_name}.{attribute} for the legacy path."
    return deferred


DOCX_IMPORT_ERROR = None
try:
    from docx import Document
    from docx.enum.table import WD_ROW_HEIGHT_RULE
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm
except Exception as exc:
    DOCX_IMPORT_ERROR = exc

from text_utils import (
    scan_non_text_objects,
    looks_like_code_sample_table,
    set_table_autofit_to_window,
)
from document_model import summarize_document_model, validate_document_model, compare_document_models
try:
    from docx_pipeline import parse_docx_to_model
    from md_pipeline import audit_markdown_preservation, parse_md_to_model
    from model_normalization import normalize_document_model, summarize_source_document_model
except ImportError:
    parse_docx_to_model = _deferred_legacy_callable("docx_pipeline", "parse_docx_to_model")
    parse_md_to_model = _deferred_legacy_callable("md_pipeline", "parse_md_to_model")
    audit_markdown_preservation = _deferred_legacy_callable(
        "md_pipeline", "audit_markdown_preservation"
    )
    normalize_document_model = _deferred_legacy_callable(
        "model_normalization", "normalize_document_model"
    )
    summarize_source_document_model = _deferred_legacy_callable(
        "model_normalization", "summarize_source_document_model"
    )
from word_model_renderer import (
    render_document_model,
    style_from_profile,
)
try:
    from docx_render import render_docx_direct
except ImportError:
    render_docx_direct = _deferred_legacy_callable("docx_render", "render_docx_direct")
from audit import audit_document, collect_content_warnings
from reporting import new_report, add_risk_warnings, write_markdown_report
try:
    from template_finalizer import apply_template_finalizer, extract_template_toc_fragment
except ImportError:
    apply_template_finalizer = _deferred_legacy_callable(
        "template_finalizer", "apply_template_finalizer"
    )
    extract_template_toc_fragment = _deferred_legacy_callable(
        "template_finalizer", "extract_template_toc_fragment"
    )
from template_profile import load_template_profile
from note_semantics import audit_note_preservation
from appendix_semantics import audit_appendix_preservation
from toc_detector import (
    audit_toc_replacement,
    detect_toc_regions,
    finalize_toc_selection,
    selected_source_positions,
)
from list_detector import analyze_docx_lists, audit_list_preservation
from front_matter import (
    analyze_front_matter,
    audit_output_structure,
    front_matter_source_positions,
    inject_document_title,
)
from table_semantics import audit_model_table_semantics
from caption_placement import audit_model_caption_placement, audit_rendered_caption_placement
from review_loop import (
    accepted_candidate,
    build_review_packet,
    collect_audit_findings,
    unknown_pattern_packet,
)

from llm_enhancer import (
    enhance_document_model,
    compute_suspicion_score,
    normalize_mode,
    should_enhance,
)

from llm_file_protocol import (
    collect_phase_requests,
    replay_phase_responses,
    generate_run_id,
    compute_source_sha256,
    build_run_info,
    write_requests_and_run,
    read_run_info,
    read_requests,
    read_responses,
    ProtocolError,
    verify_source_snapshot,
)
from wxdf.kernel import ConversionKernel, ConversionRequest, RunResult


SKILL_VERSION = "0.7.0"
DEFAULT_TABLE_ROW_HEIGHT_CM = 0.69
DEFAULT_TABLE_ROW_HEIGHT_RULE = "at-least"


def skill_version() -> str:
    return SKILL_VERSION


def _version_resource_candidates(
    *,
    module_file: str | Path | None = None,
    executable: str | Path | None = None,
    release: bool | None = None,
) -> list[Path]:
    """Return VERSION locations for source and packaged executions."""
    import os as _os

    source_file = Path(module_file or __file__).resolve()
    is_release = (
        _os.environ.get("WX_DOC_FORMAT_RELEASE") == "1"
        if release is None
        else release
    )
    candidates = []
    if is_release:
        candidates.append(Path(executable or sys.executable).resolve().parent / "VERSION")
    candidates.append(source_file.parent.parent / "VERSION")
    return candidates


def _load_version(
    *,
    module_file: str | Path | None = None,
    executable: str | Path | None = None,
    release: bool | None = None,
) -> str:
    for version_file in _version_resource_candidates(
        module_file=module_file,
        executable=executable,
        release=release,
    ):
        try:
            value = version_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
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


def audit_document_wrapper(
    doc,
    row_height_cm: float,
    row_height_rule: str,
    *,
    template_profile: dict | None = None,
    table_roles: list[str] | None = None,
    markdown_input: bool = False,
    markdown_code_paragraphs: set[int] | None = None,
    markdown_code_texts: set[str] | None = None,
) -> dict:
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
        template_profile=template_profile,
        table_roles=table_roles,
        markdown_input=markdown_input,
        markdown_code_paragraphs=markdown_code_paragraphs,
        markdown_code_texts=markdown_code_texts,
    )


def _fresh_output_document(args, template_profile):
    if args.template is not None:
        doc = _open_template_renamed_media(args.template)
        clear_document_body(doc)
    else:
        doc = Document()
        ensure_fallback_style_setup(doc)
    return doc


def _cleanup_template_copy(doc) -> None:
    path = getattr(doc, "_tmpl_path", None)
    if not path:
        return
    try:
        import os
        os.unlink(path)
    except Exception:
        pass


def _finalize_and_audit_output(
    out_doc,
    report: dict,
    *,
    args,
    template_profile,
    normalized_model: dict | None,
    source_model: dict | None,
    models: dict,
) -> None:
    report["template_finalizer"] = apply_template_finalizer(
        out_doc, template_profile,
        args.table_row_height_cm, args.table_row_height_rule,
        row_height_rule_enum=WD_ROW_HEIGHT_RULE,
        cm=Cm,
        left_alignment=WD_ALIGN_PARAGRAPH.LEFT,
        center_alignment=WD_ALIGN_PARAGRAPH.CENTER,
        set_table_autofit_to_window=set_table_autofit_to_window,
        looks_like_code_sample_table=looks_like_code_sample_table,
        table_roles=[
            block.get("table_type", "data")
            for block in (normalized_model or {}).get("document", {}).get("blocks", [])
            if block.get("block_type") == "table"
        ],
        template_toc_fragment=getattr(out_doc, "_template_toc_fragment", None),
    )
    suffix = args.input.suffix.lower()
    report["toc_replacement_audit"] = audit_toc_replacement(
        out_doc,
        models.get("toc_context") if suffix == ".docx" else None,
    )
    report["output_structure_audit"] = audit_output_structure(out_doc, template_profile)
    report["caption_placement_audit"] = audit_rendered_caption_placement(out_doc)

    rendered_model = build_document_model_from_output_wrapper(out_doc, args.input, report)
    report["rendered_document_model_summary"] = report.get("document_model_summary", {})
    report["rendered_document_model_issues"] = report.get("document_model_issues", [])
    if normalized_model is not None:
        report["document_model_summary"] = summarize_document_model(normalized_model)
        report["document_model_issues"] = validate_document_model(normalized_model)
    report["document_model_diff"] = compare_document_models(
        normalized_model or source_model, rendered_model,
    )
    final_table_roles = [
        block.get("table_type", "data")
        for block in (normalized_model or {}).get("document", {}).get("blocks", [])
        if block.get("block_type") == "table"
    ]
    report["audit"] = audit_document_wrapper(
        out_doc,
        args.table_row_height_cm,
        args.table_row_height_rule,
        template_profile=template_profile,
        table_roles=final_table_roles,
        markdown_input=suffix in {".md", ".markdown"},
        markdown_code_paragraphs={
            index + 1
            for index, paragraph in enumerate(out_doc.paragraphs)
            if paragraph._p.get(qn("w14:paraId")) in {
                item.get("para_id")
                for item in report.get("markdown_render_semantics", {}).get("paragraphs", [])
                if item.get("role") == "code_block"
            }
        },
    )
    report["list_preservation_audit"] = audit_list_preservation(
        out_doc,
        normalized_model,
        report.get("source_lists"),
        template_profile,
    )
    report["note_preservation_audit"] = audit_note_preservation(
        out_doc, normalized_model, template_profile,
    )
    report["appendix_preservation_audit"] = audit_appendix_preservation(
        out_doc, normalized_model, template_profile,
    )
    if (source_model or {}).get("document", {}).get("source", {}).get("type") == "markdown":
        report["markdown_preservation_audit"] = audit_markdown_preservation(
            source_model, normalized_model, report, rendered_model,
        )
    report["content_warnings"] = collect_content_warnings(out_doc)
    report["risk_warnings"] = []
    add_risk_warnings(report, args.table_row_height_rule)


def _render_review_candidate(
    args,
    model: dict,
    report: dict,
    *,
    template_profile,
    numbering_ids: dict,
    models: dict,
):
    out_doc = _fresh_output_document(args, template_profile)
    suffix = args.input.suffix.lower()
    if suffix in {".md", ".markdown"}:
        render_document_model(
            model, out_doc, report,
            args.table_row_height_cm, args.table_row_height_rule, numbering_ids,
            template_profile=report.get("template_profile"),
        )
    else:
        src_doc = Document(args.input)
        render_docx_direct(
            src_doc, out_doc, report,
            args.table_row_height_cm, args.table_row_height_rule, numbering_ids,
            template_profile=report.get("template_profile"),
            strict_normalize=args.strict_normalize,
            role_overrides=_extract_role_overrides_from_model(model, report) or None,
            heading_level_overrides=_extract_heading_level_overrides_from_model(model, report),
            table_type_overrides=_extract_table_type_overrides_from_model(model, report),
            model=model,
            excluded_source_positions=models.get("excluded_source_positions", set()),
        )
    return out_doc


def _llm_call_from_command(command: str):
    """Build an LLM callable from a shell command template.

    The command receives the prompt via stdin by default.  If the command
    contains the ``{prompt_file}`` placeholder, the prompt is written to a
    temporary file and the placeholder is replaced with its quoted path.

    Returns a ``(prompt: str) -> str`` callable, or ``None`` if *command*
    is empty.
    """
    import os as _os
    import shlex
    import subprocess
    import tempfile

    command = command.strip()
    if not command:
        return None

    def _llm(prompt: str) -> str:
        if "{prompt_file}" not in command:
            r = subprocess.run(
                command,
                input=prompt,
                text=True,
                shell=True,
                capture_output=True,
                timeout=60,
            )
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or "").strip())
            return r.stdout or ""

        prompt_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".prompt.txt", delete=False) as f:
                f.write(prompt)
                prompt_path = f.name
            r = subprocess.run(
                command.replace("{prompt_file}", shlex.quote(prompt_path)),
                text=True,
                shell=True,
                capture_output=True,
                timeout=60,
            )
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout or "").strip())
            return r.stdout or ""
        finally:
            if prompt_path:
                try:
                    _os.unlink(prompt_path)
                except OSError:
                    pass

    return _llm


def _resolve_llm_call(args):
    """Auto-detect LLM backend for semantic enhancement.

    Returns a callable that accepts a prompt string and returns the LLM
    response, or None if no LLM backend is available.
    """
    import os as _os

    # 1. Explicit LLM command (--llm-command / LLM_COMMAND)
    command = getattr(args, "llm_command", None) if args is not None else None
    command = command or _os.environ.get("LLM_COMMAND")
    command_call = _llm_call_from_command(command or "")
    if command_call is not None:
        return command_call

    # Release packages are offline by default. Explicit local commands and the
    # request/response file protocol remain available, while cloud SDK
    # auto-discovery is disabled.
    if _os.environ.get("WX_DOC_FORMAT_OFFLINE") == "1":
        return None

    # 2. Anthropic API
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

    # 3. OpenAI API
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

    return None


_RELEASE_REPORT_OMIT_KEYS = {
    "review_packet",
    "unknown_pattern_packet",
    "decisions",
    "blocks",
}
_RELEASE_REPORT_OMIT_FRAGMENTS = (
    "candidate",
    "confidence",
    "decision",
    "evidence",
    "frame",
    "score",
    "stack",
    "traceback",
)
_PUBLIC_SUMMARY_AUDITS = (
    ("audit", "audit"),
    ("model_audit", "model_audit"),
    ("saved_output_audit", "saved_output_audit"),
    ("appendix_preservation_audit", "appendix_preservation_audit"),
)


def _is_full_ast(value) -> bool:
    if not isinstance(value, dict):
        return False
    document = value.get("document")
    return (
        isinstance(document, dict) and isinstance(document.get("blocks"), list)
    ) or (
        "schema_version" in value and isinstance(value.get("blocks"), list)
    )


def _release_report_key_is_sensitive(key: object) -> bool:
    normalized = str(key).strip().lower()
    if normalized in _RELEASE_REPORT_OMIT_KEYS:
        return True
    if normalized == "ast" or normalized.startswith("ast_") or normalized.endswith("_ast"):
        return True
    return any(fragment in normalized for fragment in _RELEASE_REPORT_OMIT_FRAGMENTS)


def sanitize_release_report(report: dict) -> dict:
    """Return a report copy without private diagnostic implementation data."""
    def sanitize(value):
        if _is_full_ast(value):
            return None
        if isinstance(value, dict):
            cleaned = {}
            for key, child in value.items():
                if _release_report_key_is_sensitive(key) or _is_full_ast(child):
                    continue
                cleaned[key] = sanitize(child)
            return cleaned
        if isinstance(value, list):
            return [sanitize(item) for item in value if not _is_full_ast(item)]
        return deepcopy(value)

    return sanitize(report)


def _public_summary_scalar(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip()
    if not normalized or len(normalized) > 80:
        return "unknown"
    if "/" in normalized or "\\" in normalized or "\n" in normalized or "\r" in normalized:
        return "unknown"
    return normalized


def _public_audit_status(value: object) -> str:
    if not isinstance(value, dict):
        return "not_run"
    if value.get("passed") is True:
        return "passed"
    if value.get("passed") is False:
        return "failed"
    return "not_run"


def _public_unexpected_styles_count(report: dict) -> int | None:
    for container in (
        report,
        report.get("saved_output_audit"),
        report.get("audit"),
    ):
        if not isinstance(container, dict):
            continue
        if "unexpected_styles" not in container:
            continue
        value = container["unexpected_styles"]
        if isinstance(value, list):
            return len(value)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None
    return None


def _public_diagnostic_codes(report: dict) -> list[str]:
    codes = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            code = value.get("code")
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", code):
                codes.add(code)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(report.get("diagnostics", []))
    collect(report.get("risk_warnings", []))
    for _, source_key in _PUBLIC_SUMMARY_AUDITS:
        collect(report.get(source_key, {}))
    return sorted(codes)


def public_report_summary(report: dict) -> dict:
    audits = {
        public_name: _public_audit_status(report.get(source_key))
        for public_name, source_key in _PUBLIC_SUMMARY_AUDITS
    }
    status = _public_summary_scalar(report.get("status"))
    unexpected_styles_count = _public_unexpected_styles_count(report)
    diagnostic_codes = _public_diagnostic_codes(report)
    if unexpected_styles_count is None:
        diagnostic_codes = sorted({*diagnostic_codes, "WXDF-E-AUDIT-EVIDENCE-MISSING"})
    explicit_manual_review = report.get("manual_review_required")
    manual_review_required = (
        True
        if unexpected_styles_count is None
        else (
            explicit_manual_review
            if isinstance(explicit_manual_review, bool)
            else (
                status != "completed"
                or unexpected_styles_count > 0
                or bool(diagnostic_codes)
                or "failed" in audits.values()
            )
        )
    )
    return {
        "schema_version": "1.0",
        "status": status,
        "pipeline": _public_summary_scalar(report.get("pipeline")),
        "profile": _public_summary_scalar(
            report.get("profile")
            or (report.get("execution_plan") or {}).get("profile")
        ),
        "list_policy": _public_summary_scalar(report.get("list_policy")),
        "audits": audits,
        "unexpected_styles_count": unexpected_styles_count,
        "manual_review_required": manual_review_required,
        "diagnostic_codes": diagnostic_codes,
    }


def report_for_output(report: dict, *, release: bool | None = None) -> dict:
    import os as _os

    is_release = (
        _os.environ.get("WX_DOC_FORMAT_RELEASE") == "1"
        if release is None
        else release
    )
    if not is_release:
        return report
    public_report = sanitize_release_report(report)
    public_report["public_summary"] = public_report_summary(report)
    return public_report


def _apply_native_structure_context(
    source_model: dict,
    report: dict,
    runtime_context: dict | None,
) -> dict:
    context = (runtime_context or {}).get("native_structure")
    if not isinstance(context, dict):
        return source_model
    from wxdf.semantics.structure_bridge import apply_structure_decisions

    applied = apply_structure_decisions(
        source_model,
        context.get("source_facts") or {},
        list(context.get("candidates") or []),
    )
    report["structure_decision_consumption"] = applied.evidence
    return applied.model


def _native_region_resolution(runtime_context: dict | None) -> dict | None:
    context = (runtime_context or {}).get("native_region")
    return context if isinstance(context, dict) else None


def _merge_native_region_report(report: dict, resolution: dict | None) -> None:
    for key, value in ((resolution or {}).get("report") or {}).items():
        report[key] = deepcopy(value)


def _apply_native_markdown_regions(
    source_model: dict,
    report: dict,
    runtime_context: dict | None,
) -> dict:
    resolution = _native_region_resolution(runtime_context)
    if not resolution or resolution.get("source_type") != "markdown":
        return source_model
    from wxdf.semantics.region_bridge import apply_region_decisions

    applied = apply_region_decisions(
        source_model,
        resolution.get("source_facts") or {},
        resolution.get("regions") or {},
    )
    report["source_region_decision_consumption"] = applied.evidence
    return applied.model


def _apply_native_source_projection(
    source_model: dict,
    report: dict,
    runtime_context: dict | None,
) -> dict:
    context = (runtime_context or {}).get("native_source")
    if not isinstance(context, dict):
        return source_model
    from wxdf.semantics.source_projection_bridge import apply_source_projection

    applied = apply_source_projection(
        source_model,
        context.get("projection") or {},
    )
    report["source_parser_consumption"] = applied.evidence
    return applied.model


def _apply_native_protected_context(
    source_model: dict,
    report: dict,
    runtime_context: dict | None,
) -> dict:
    context = (runtime_context or {}).get("native_protected")
    if not isinstance(context, dict):
        return source_model
    from wxdf.semantics.protected_bridge import apply_protected_candidates

    applied = apply_protected_candidates(
        source_model,
        context.get("source_facts") or {},
        list(context.get("candidates") or []),
    )
    report["protected_content_consumption"] = applied.evidence
    return applied.model


def _apply_native_list_context(
    source_model: dict,
    report: dict,
    runtime_context: dict | None,
) -> dict:
    context = (runtime_context or {}).get("native_list")
    if not isinstance(context, dict):
        return source_model
    from wxdf.semantics.list_bridge import apply_list_semantics

    applied = apply_list_semantics(
        source_model,
        context.get("source_facts") or {},
        context.get("semantics") or {},
    )
    report["list_semantics_consumption"] = applied.evidence
    return applied.model


def _apply_native_table_context(
    source_model: dict,
    report: dict,
    runtime_context: dict | None,
) -> dict:
    context = (runtime_context or {}).get("native_table")
    if not isinstance(context, dict):
        return source_model
    from wxdf.semantics.table_bridge import apply_table_semantics

    applied = apply_table_semantics(
        source_model,
        list(context.get("semantics") or []),
    )
    report["table_semantics_consumption"] = applied.evidence
    return applied.model


def _apply_native_caption_context(
    source_model: dict,
    report: dict,
    runtime_context: dict | None,
) -> dict:
    context = (runtime_context or {}).get("native_caption")
    if not isinstance(context, dict):
        return source_model
    from wxdf.semantics.caption_bridge import apply_caption_semantics

    applied = apply_caption_semantics(
        source_model,
        context.get("semantics") or {},
    )
    report["caption_semantics_consumption"] = applied.evidence
    return applied.model


def _apply_native_appendix_context(
    source_model: dict,
    report: dict,
    runtime_context: dict | None,
) -> dict:
    context = (runtime_context or {}).get("native_appendix")
    if not isinstance(context, dict):
        return source_model
    from wxdf.semantics.appendix_bridge import apply_appendix_semantics

    applied = apply_appendix_semantics(
        source_model,
        context.get("semantics") or {},
    )
    report["appendix_semantics_consumption"] = applied.evidence
    return applied.model


def convert_md(src: Path, doc, report: dict, row_height_cm: float, row_height_rule: str, numbering_ids: dict, args=None, file_protocol_ctx=None, runtime_context=None) -> dict:
    source_model = parse_md_to_model(src, report, skill_version=skill_version)
    source_model = _apply_native_markdown_regions(source_model, report, runtime_context)
    source_model = _apply_native_source_projection(source_model, report, runtime_context)
    source_model = _apply_native_protected_context(source_model, report, runtime_context)
    source_model = _apply_native_structure_context(source_model, report, runtime_context)
    source_model = _apply_native_list_context(source_model, report, runtime_context)
    source_model = _apply_native_table_context(source_model, report, runtime_context)
    source_model = _apply_native_caption_context(source_model, report, runtime_context)
    source_model = _apply_native_appendix_context(source_model, report, runtime_context)
    summarize_source_document_model(report, source_model)

    # ── LLM Enhancement ──
    enhance_mode = getattr(args, "llm_enhance", "off") if args is not None else "off"
    enhance_mode = normalize_mode(enhance_mode)
    llm_call = _resolve_llm_call(args) if enhance_mode != "off" else None
    llm_hint = getattr(args, "llm_hint", None) if args is not None else None

    # Phase A: block role review (before normalization)
    if should_enhance(report, "A", enhance_mode):
        source_model = _run_phase_enhancement(
            source_model, report, phase="A",
            llm_call=llm_call, hint=llm_hint,
            file_protocol_ctx=file_protocol_ctx,
        )

    model = normalize_document_model(source_model, report)

    # Phase B: caption text generation via LLM (after normalization)
    if should_enhance(report, "B", enhance_mode):
        model = _run_phase_enhancement(
            model, report, phase="B",
            llm_call=llm_call, hint=llm_hint,
            file_protocol_ctx=file_protocol_ctx,
        )

    report["table_semantics_audit"] = audit_model_table_semantics(model)
    report["caption_placement_model_audit"] = audit_model_caption_placement(model)

    render_document_model(
        model, doc, report, row_height_cm, row_height_rule, numbering_ids,
        template_profile=report.get("template_profile"),
    )
    return {"source": source_model, "normalized": model}


def convert_docx(src: Path, dst_doc, row_height_cm: float, row_height_rule: str, strict_normalize: bool, report: dict, numbering_ids: dict, args=None, file_protocol_ctx=None, runtime_context=None) -> dict:
    from docx.oxml.ns import qn as _qn
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    from docx_pipeline import infer_docx_role

    src_doc = Document(src)
    native_region = _native_region_resolution(runtime_context)
    if native_region and native_region.get("source_type") == "docx":
        _merge_native_region_report(report, native_region)
        toc_context = deepcopy(native_region.get("toc_context") or {})
    else:
        native_region = None
        toc_context = detect_toc_regions(src_doc, report)
    enhance_mode = normalize_mode(getattr(args, "llm_enhance", "off")) if args is not None else "off"
    llm_call = _resolve_llm_call(args) if enhance_mode != "off" else None
    llm_hint = getattr(args, "llm_hint", None) if args is not None else None

    if should_enhance(report, "S", enhance_mode):
        toc_context = _run_phase_enhancement(
            toc_context, report, phase="S",
            llm_call=llm_call, hint=llm_hint,
            file_protocol_ctx=file_protocol_ctx,
        )
    finalize_toc_selection(toc_context, report, method=(
        "llm" if any(
            item.get("operation") == "exclude_toc_region"
            for item in report.get("llm_enhancer", {}).get("applied", [])
        ) else "rules"
    ))
    if native_region and not should_enhance(report, "S", enhance_mode):
        front_matter_context = deepcopy(native_region.get("front_matter_context") or {})
        report["source_front_matter"] = deepcopy(front_matter_context)
    else:
        front_matter_context = analyze_front_matter(src_doc, toc_context, src, report)
    excluded_source_positions = (
        selected_source_positions(toc_context)
        | front_matter_source_positions(front_matter_context)
    )
    if native_region:
        report["source_region_decision_consumption"] = {
            "excluded_candidate_count": len(native_region.get("excluded_source_positions") or []),
            "removed_count": len(excluded_source_positions),
            "unmatched_count": 0,
            "ambiguous_count": 0,
        }
    numbering_context = analyze_docx_lists(
        src_doc,
        report,
        excluded_source_positions=excluded_source_positions,
    )
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
        excluded_source_positions=excluded_source_positions,
        numbering_context=numbering_context,
    )
    report["parse_report"] = source_model.get("parse_report", {})
    source_model = inject_document_title(source_model, front_matter_context)
    source_model = _apply_native_source_projection(source_model, report, runtime_context)
    source_model = _apply_native_protected_context(source_model, report, runtime_context)
    source_model = _apply_native_structure_context(source_model, report, runtime_context)
    source_model = _apply_native_list_context(source_model, report, runtime_context)
    source_model = _apply_native_table_context(source_model, report, runtime_context)
    source_model = _apply_native_caption_context(source_model, report, runtime_context)
    source_model = _apply_native_appendix_context(source_model, report, runtime_context)
    summarize_source_document_model(report, source_model)

    # ── LLM Enhancement ──
    # Phase A: block role review (before normalization)
    applied_start = len(report.get("llm_enhancer", {}).get("applied", []))
    if should_enhance(report, "A", enhance_mode):
        source_model = _run_phase_enhancement(
            source_model, report, phase="A",
            llm_call=llm_call, hint=llm_hint,
            file_protocol_ctx=file_protocol_ctx,
        )

    model = normalize_document_model(source_model, report)

    # Phase B: caption text generation via LLM (after normalization)
    if should_enhance(report, "B", enhance_mode):
        model = _run_phase_enhancement(
            model, report, phase="B",
            llm_call=llm_call, hint=llm_hint,
            file_protocol_ctx=file_protocol_ctx,
        )

    report["table_semantics_audit"] = audit_model_table_semantics(model)
    report["caption_placement_model_audit"] = audit_model_caption_placement(model)

    # role_overrides for direct rendering path — extracted from Phase A applied decisions
    phase_a_role_overrides: dict[int, str] = {}
    if should_enhance(report, "A", enhance_mode):
        phase_a_role_overrides = _extract_role_overrides_from_model(
            model, report, applied_start=applied_start,
        )
    role_overrides = phase_a_role_overrides or None

    # Build heading_level_overrides and table_type_overrides from the
    # enhanced model.  The index position tracks model block order which
    # matches source-document iteration order for typical documents
    # without empty-paragraph gaps.
    heading_level_overrides = _extract_heading_level_overrides_from_model(model, report)
    table_type_overrides = _extract_table_type_overrides_from_model(model, report)

    render_docx_direct(
        src_doc, dst_doc, report, row_height_cm, row_height_rule, numbering_ids,
        template_profile=report.get("template_profile"),
        strict_normalize=strict_normalize,
        role_overrides=role_overrides,
        heading_level_overrides=heading_level_overrides,
        table_type_overrides=table_type_overrides,
        model=model,
        excluded_source_positions=excluded_source_positions,
    )
    return {
        "source": source_model,
        "normalized": model,
        "toc_context": toc_context,
        "front_matter_context": front_matter_context,
        "excluded_source_positions": excluded_source_positions,
    }


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
    were changed via set_table_type.  See heading_level_overrides
    for index-mapping caveats.

    Returns an empty dict when there are no table-type changes.
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


def _extract_role_overrides_from_model(
    model: dict,
    report: dict,
    *,
    applied_start: int = 0,
) -> dict[int, str]:
    """从 Phase A 的 applied decisions 提取段落级别的 role_overrides。

    只提取 retype 操作中与原始类型不同的映射，返回 ``{模型块索引: 新角色}``
    用于 ``render_docx_direct`` 的段落实例化。
    """
    changed_roles: dict[str, str] = {}
    applied = report.get("llm_enhancer", {}).get("applied", [])[applied_start:]

    for dec in applied:
        if dec.get("operation") != "retype":
            continue
        to_type = dec.get("to", {}).get("block_type", "")
        from_type = dec.get("from", {}).get("block_type", "")
        if to_type in {"heading", "body", "list_item", "caption"} and to_type != from_type:
            bid = dec.get("block_id", "")
            if bid:
                changed_roles[bid] = to_type

    if not changed_roles:
        return {}

    overrides: dict[int, str] = {}
    idx = 0
    for block in model.get("document", {}).get("blocks", []):
        btype = block.get("block_type")
        if btype in {"heading", "body", "list_item", "caption",
                      "table", "image", "appendix", "unknown"}:
            bid = block.get("id")
            if bid in changed_roles:
                overrides[idx] = changed_roles[bid]
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


def _run_phase_enhancement(model, report, *, phase, llm_call, hint, file_protocol_ctx=None):
    """Run enhancement for a phase — normal, generate, or resume.

    * Normal mode: calls ``enhance_document_model`` with the real LLM callable.
    * Generate mode: collects prompts into ``file_protocol_ctx["requests"]``,
      returns model unchanged.
    * Resume mode: replays saved responses through validate + apply.
    """
    if file_protocol_ctx is None:
        return enhance_document_model(
            model, report, phase=phase, llm_call=llm_call, hint=hint,
        )

    mode = file_protocol_ctx.get("mode")

    if mode == "generate":
        reqs = collect_phase_requests(model, report, phase=phase, hint=hint)
        file_protocol_ctx.setdefault("requests", []).extend(reqs)
        return model  # no LLM call in generate mode

    if mode == "resume":
        from llm_enhancer import _resolve_legacy_phase

        request_phase = _resolve_legacy_phase(phase)
        phase_requests = [
            request for request in file_protocol_ctx.get("requests", [])
            if request.get("phase") == request_phase
        ]
        if not phase_requests:
            reqs = collect_phase_requests(model, report, phase=phase, hint=hint)
            if reqs:
                file_protocol_ctx.setdefault("requests", []).extend(reqs)
                file_protocol_ctx.setdefault("new_requests", []).extend(reqs)
            return model
        return replay_phase_responses(
            model, report, phase=phase,
            requests=file_protocol_ctx.get("requests", []),
            responses=file_protocol_ctx.get("responses", []),
            hint=hint,
        )

    return model


def _write_protocol_requests(args, requests: list[dict], *, request_stage: str) -> dict:
    run_id = generate_run_id()
    source_sha256 = compute_source_sha256(args.input)
    run_info = build_run_info(
        run_id=run_id,
        source_path=str(args.input),
        source_sha256=source_sha256,
        args={
            "input": str(args.input),
            "output": str(args.output),
            "template": str(args.template) if args.template else None,
            "report": str(args.report) if args.report else None,
            "report_md": str(args.report_md) if args.report_md else None,
            "ast": str(args.ast) if args.ast else None,
            "source_ast": str(args.source_ast) if args.source_ast else None,
            "fail_on_risk": bool(args.fail_on_risk),
            "llm_enhance": args.llm_enhance,
            "llm_hint": args.llm_hint,
            "strict_normalize": args.strict_normalize,
            "table_row_height_cm": args.table_row_height_cm,
            "table_row_height_rule": args.table_row_height_rule,
            "pipeline": args.pipeline,
            "list_policy": args.list_policy,
        },
        work_dir=str(args.generate_requests),
    )
    run_info["request_stage"] = request_stage
    write_requests_and_run(requests, run_info, args.generate_requests)
    return run_info


def _run_generate_requests(args, report, runtime_context=None) -> None:
    """Phase 1: parse source, build prompts, write files, stop."""
    suffix = args.input.suffix.lower()
    llm_hint = getattr(args, "llm_hint", None)
    all_requests: list[dict] = []
    fp_ctx: dict = {"mode": "generate", "requests": all_requests}
    enhance_mode = normalize_mode(getattr(args, "llm_enhance", "off"))
    toc_context = None
    excluded_source_positions: set[int] = set()

    # ── Parse source ─────────────────────────────────────────────────
    if suffix in {".md", ".markdown"}:
        source_model = parse_md_to_model(
            args.input, report, skill_version=skill_version,
        )
        source_model = _apply_native_markdown_regions(source_model, report, runtime_context)
    elif suffix == ".docx":
        from docx import Document as _Document
        from docx.oxml.ns import qn as _qn
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        from docx_pipeline import infer_docx_role

        src_doc = _Document(args.input)
        native_region = _native_region_resolution(runtime_context)
        if native_region and native_region.get("source_type") == "docx":
            _merge_native_region_report(report, native_region)
            toc_context = deepcopy(native_region.get("toc_context") or {})
        else:
            native_region = None
            toc_context = detect_toc_regions(src_doc, report)
        if should_enhance(report, "S", enhance_mode):
            _run_phase_enhancement(
                toc_context, report, phase="S",
                llm_call=None, hint=llm_hint,
                file_protocol_ctx=fp_ctx,
            )
        if all_requests:
            _write_protocol_requests(args, all_requests, request_stage="source")
            print(f"Generated {len(all_requests)} source-stage LLM request(s) in {args.generate_requests.resolve()}")
            print("Run with --resume <run.json> after processing llm_responses.jsonl.")
            return
        if native_region and not should_enhance(report, "S", enhance_mode):
            front_matter_context = deepcopy(native_region.get("front_matter_context") or {})
            report["source_front_matter"] = deepcopy(front_matter_context)
        else:
            front_matter_context = analyze_front_matter(
                src_doc, toc_context, args.input, report,
            )
        excluded_source_positions = (
            selected_source_positions(toc_context)
            | front_matter_source_positions(front_matter_context)
        )
        if native_region:
            report["source_region_decision_consumption"] = {
                "excluded_candidate_count": len(native_region.get("excluded_source_positions") or []),
                "removed_count": len(excluded_source_positions),
                "unmatched_count": 0,
                "ambiguous_count": 0,
            }
        numbering_context = analyze_docx_lists(
            src_doc,
            report,
            excluded_source_positions=excluded_source_positions,
        )
        source_model = parse_docx_to_model(
            args.input, src_doc, True, 0,
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
            excluded_source_positions=excluded_source_positions,
            numbering_context=numbering_context,
        )
        source_model = inject_document_title(source_model, front_matter_context)
    else:
        raise SystemExit(f"Unsupported input type: {args.input.suffix}")

    source_model = _apply_native_structure_context(source_model, report, runtime_context)
    summarize_source_document_model(report, source_model)

    # ── Collect Phase A requests ─────────────────────────────────────
    if should_enhance(report, "A", enhance_mode):
        _run_phase_enhancement(
            source_model, report, phase="A",
            llm_call=None, hint=llm_hint,
            file_protocol_ctx=fp_ctx,
        )

    # ── Normalize ────────────────────────────────────────────────────
    model = normalize_document_model(source_model, report)

    # ── Collect Phase B requests ─────────────────────────────────────
    if should_enhance(report, "B", enhance_mode):
        _run_phase_enhancement(
            model, report, phase="B",
            llm_call=None, hint=llm_hint,
            file_protocol_ctx=fp_ctx,
        )

    # ── Write requests and run.json ──────────────────────────────────
    _write_protocol_requests(args, fp_ctx["requests"], request_stage="ast")

    print(f"Generated {len(fp_ctx['requests'])} LLM request(s) in {args.generate_requests.resolve()}")
    print("Run with --resume <run.json> after processing llm_responses.jsonl.")


def _legacy_adapter_argv(args) -> list[str]:
    """Re-enter the stable CLI path with the already resolved modular arguments."""

    argv: list[str] = ["--pipeline", "legacy", "--list-policy", "legacy"]
    for flag, value in (
        ("--input", args.input),
        ("--output", args.output),
        ("--template", args.template),
        ("--report", args.report),
        ("--report-md", args.report_md),
        ("--ast", args.ast),
        ("--source-ast", args.source_ast),
        ("--llm-hint", args.llm_hint),
        ("--llm-command", args.llm_command),
        ("--generate-requests", args.generate_requests),
        ("--resume", args.resume),
    ):
        if value is not None:
            argv.extend((flag, str(value)))
    argv.extend((
        "--llm-enhance", str(args.llm_enhance),
        "--table-row-height-cm", str(args.table_row_height_cm),
        "--table-row-height-rule", str(args.table_row_height_rule),
        "--strict-normalize" if args.strict_normalize else "--no-strict-normalize",
    ))
    if args.fail_on_risk:
        argv.append("--fail-on-risk")
    return argv


def _modular_native_v2_enabled(args) -> bool:
    """Select the native modular seam from explicit mode and source contracts."""

    if args.llm_enhance != "off" and not args.llm_command and not (args.resume or args.generate_requests):
        return False
    if args.input is None:
        return False
    if args.modular_profile == "native-v2":
        return True
    return False


def _native_checkpoint_configuration(args, *, output_path: Path) -> dict:
    """Freeze the effective native CLI configuration used across resume."""

    return {
        "input": str(args.input),
        "output": str(output_path),
        "template": str(args.template) if args.template else None,
        "report": str(args.report) if args.report else None,
        "report_md": str(args.report_md) if args.report_md else None,
        "ast": str(args.ast) if args.ast else None,
        "source_ast": str(args.source_ast) if args.source_ast else None,
        "fail_on_risk": bool(args.fail_on_risk),
        "llm_enhance": args.llm_enhance,
        "llm_hint": args.llm_hint,
        "strict_normalize": args.strict_normalize,
        "table_row_height_cm": args.table_row_height_cm,
        "table_row_height_rule": args.table_row_height_rule,
        "pipeline": args.pipeline,
        "modular_profile": args.modular_profile,
        "list_policy": args.list_policy,
    }


def _run_legacy_full_adapter(args, request: ConversionRequest) -> RunResult:
    """Execute all stable capabilities as one transitional modular module."""

    try:
        main(
            _legacy_adapter_argv(args),
            runtime_context={
                "native_structure": request.options.get("_native_structure_context"),
                "native_region": request.options.get("_native_region_context"),
                "native_source": request.options.get("_native_source_context"),
                "native_protected": request.options.get("_native_protected_context"),
                "native_list": request.options.get("_native_list_context"),
                "native_table": request.options.get("_native_table_context"),
                "native_caption": request.options.get("_native_caption_context"),
                "native_appendix": request.options.get("_native_appendix_context"),
                "adapter_artifacts": request.options.get("_adapter_artifacts"),
            },
        )
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return RunResult(
            status="failed",
            exit_code=code,
            diagnostics=[{
                "code": "LEGACY_FULL_ADAPTER_FAILED",
                "message": str(exc.code or "stable conversion failed"),
            }],
        )

    artifacts = {"docx": str(request.output_path)} if request.output_path.is_file() else {}
    try:
        from wxdf.migration import migrate_legacy_model
        from wxdf.normalization import normalize as normalize_modular_ast
        from wxdf.template import load_template_bundle

        if args.source_ast is not None and args.source_ast.is_file():
            legacy_source = json.loads(args.source_ast.read_text(encoding="utf-8"))
            migrated_source = migrate_legacy_model(legacy_source).ast
            args.source_ast.write_text(
                json.dumps(migrated_source, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            artifacts["source_ast"] = str(args.source_ast)
        if args.ast is not None and args.ast.is_file():
            legacy_normalized = json.loads(args.ast.read_text(encoding="utf-8"))
            migrated_normalized = migrate_legacy_model(legacy_normalized).ast
            profile = load_template_bundle(args.template) if args.template is not None else {
                "resolved_styles": {},
                "numbering_ids": {},
            }
            normalized_ast = normalize_modular_ast(
                migrated_normalized,
                profile,
                strategy=request.list_policy,
            )
            args.ast.write_text(
                json.dumps(normalized_ast, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            artifacts["normalized_ast"] = str(args.ast)
    except Exception as exc:
        return RunResult(
            status="failed",
            exit_code=1,
            artifacts=artifacts,
            diagnostics=[{
                "code": "LEGACY_FULL_ADAPTER_AST_MIGRATION_FAILED",
                "message": str(exc),
                "type": type(exc).__name__,
            }],
        )

    report: dict = {}
    if args.report is not None and args.report.is_file():
        report = json.loads(args.report.read_text(encoding="utf-8"))
    if request.options.get("protocol_mode") in {"generate_requests", "resume"} and not request.output_path.is_file():
        return RunResult(status="waiting_external", exit_code=0, report=report)
    if not request.output_path.is_file():
        return RunResult(
            status="failed",
            exit_code=1,
            report=report,
            diagnostics=[{
                "code": "LEGACY_FULL_ADAPTER_OUTPUT_MISSING",
                "message": str(request.output_path),
            }],
        )
    return RunResult(
        status="completed",
        exit_code=0,
        report=report,
        artifacts=artifacts,
    )


def main(argv: list[str] | None = None, *, runtime_context: dict | None = None) -> None:
    global SKILL_VERSION
    cli_arguments = list(sys.argv[1:] if argv is None else argv)
    explicit_options = {
        argument.split("=", 1)[0]
        for argument in cli_arguments
        if argument.startswith("--")
    }
    parser = argparse.ArgumentParser(description="Convert MD or DOCX to template-formatted DOCX.")
    parser.add_argument("--version", action="store_true", help="Print the installed Skill version and exit.")
    parser.add_argument("--input", type=Path, help="Source file (optional when --resume is used)")
    parser.add_argument("--output", type=Path, help="Output .docx path (optional when --resume is used)")
    parser.add_argument("--template", type=Path, default=None, help="DOCX template driving styles and numbering.")
    parser.add_argument(
        "--pipeline",
        choices=["legacy", "modular"],
        default="modular",
        help="Execution pipeline (default: modular). legacy is a diagnostic pipeline only.",
    )
    parser.add_argument(
        "--modular-profile",
        choices=["legacy-full-v1", "native-v2"],
        default=None,
        help=(
            "Pipeline profile (default: native-v2). The legacy diagnostic "
            "pipeline uses legacy-full-v1."
        ),
    )
    parser.add_argument(
        "--list-policy",
        choices=["legacy", "wx-default-v2"],
        default=None,
        help=(
            "List policy (default: wx-default-v2). The legacy diagnostic "
            "pipeline uses legacy."
        ),
    )
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
        choices=[
            "off", "auto",
            "s", "a", "b", "c", "sa", "sb", "sc", "ab", "ac", "bc",
            "sab", "sac", "sbc", "abc", "sabc",
            "force-s", "force-a", "force-b", "force-c", "force-sa", "force-sb", "force-sc",
            "force-ab", "force-ac", "force-bc", "force-sab", "force-sac", "force-sbc",
            "force-abc", "force-sabc",
            "toc_region_review", "list_detect", "caption_gen", "document_review", "all",
        ],
        default="off",
        help=(
            "LLM enhancement level (default: off). "
            "New names: toc_region_review / list_detect / caption_gen / document_review / all. "
            "Compact phase combinations remain supported."
        ),
    )
    parser.add_argument(
        "--llm-hint",
        type=str,
        default=None,
        help="Natural-language hint injected into LLM enhancement prompts"
    )
    parser.add_argument(
        "--llm-command",
        type=str,
        default=None,
        help=(
            "Shell command used for LLM enhancement. The prompt is sent to stdin "
            "and stdout is used as the response. Use {prompt_file} if the command "
            "expects a prompt file path."
        ),
    )
    parser.add_argument(
        "--generate-requests",
        type=Path,
        default=None,
        help=(
            "Generate LLM request files (llm_requests.jsonl + run.json) in DIR "
            "and stop.  The agent processes the requests and writes "
            "llm_responses.jsonl, then --resume continues."
        ),
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help=(
            "Resume from a run.json file produced by --generate-requests. "
            "Reads llm_responses.jsonl, validates integrity, and continues "
            "the pipeline (enhance → validate → apply → render)."
        ),
    )
    args = parser.parse_args(cli_arguments)

    SKILL_VERSION = _load_version()
    if args.version:
        print(SKILL_VERSION)
        return

    if args.modular_profile is None:
        args.modular_profile = (
            "legacy-full-v1" if args.pipeline == "legacy" else "native-v2"
        )
    if args.list_policy is None:
        args.list_policy = "legacy" if args.pipeline == "legacy" else "wx-default-v2"

    import os as _os
    if _os.environ.get("WX_DOC_FORMAT_RELEASE") == "1" and (args.ast or args.source_ast):
        parser.error("--ast and --source-ast are unavailable in release packages")

    # ── Argument validation ─────────────────────────────────────────
    if args.generate_requests and args.resume:
        parser.error("--generate-requests and --resume are mutually exclusive")

    if args.generate_requests and not args.input:
        parser.error("--input is required when using --generate-requests")

    if not args.resume and not args.generate_requests:
        if not args.input:
            parser.error("--input is required")
        if not args.output:
            parser.error("--output is required")

    # Resume mode may pull input/output from run.json so we defer
    # the required check until after the resume handler runs.

    # Backward compatibility applies only outside the explicit native-v2 seam.
    native_v2_cli = args.pipeline == "modular" and args.modular_profile == "native-v2"
    if not native_v2_cli and args.llm_enhance == "off" and _os.environ.get("WX_DOC_LLM_ENHANCE") == "1":
        args.llm_enhance = "abc"

    # Normalize new capability names to legacy mode names
    args.llm_enhance = normalize_mode(args.llm_enhance)

    # ── Handle --resume: load run.json and restore original CLI args ──
    native_checkpoint = False
    if args.resume:
        try:
            run_info = read_run_info(args.resume)
        except ValueError:
            raw_run_info = json.loads(args.resume.read_text(encoding="utf-8"))
            checkpoint_schema_version = raw_run_info.get("checkpoint_schema_version")
            if checkpoint_schema_version != "2.0":
                if checkpoint_schema_version is not None:
                    parser.error(
                        "CHECKPOINT_SCHEMA_UNSUPPORTED: checkpoint schema "
                        f"{checkpoint_schema_version!r} cannot be resumed"
                    )
                parser.error(
                    "CHECKPOINT_FORMAT_UNSUPPORTED: run.json is not a native Checkpoint 2.0"
                )
            native_checkpoint = True
            run_info = raw_run_info
        else:
            if run_info.get("protocol_version") == "1.0":
                parser.error(
                    "LEGACY_CHECKPOINT_UNSUPPORTED: protocol 1.0 cannot resume "
                    "after the native-v2 cutover; regenerate requests with the current CLI"
                )
        run_args = run_info.get("configuration" if native_checkpoint else "args", {})
        def restore_native(flag: str, *aliases: str) -> bool:
            return not native_checkpoint or not explicit_options.intersection((flag, *aliases))

        # Only fill in input/output/template from run.json if the user
        # didn't provide them on the CLI (allows override).
        if native_checkpoint and restore_native("--input") and run_args.get("input"):
            args.input = Path(run_args["input"])
        elif not args.input and "input" in run_args:
            args.input = Path(run_args["input"])
        if native_checkpoint and restore_native("--output") and run_args.get("output"):
            args.output = Path(run_args["output"])
        elif not args.output and "output" in run_args:
            args.output = Path(run_args["output"])
        if native_checkpoint and restore_native("--template") and run_args.get("template"):
            args.template = Path(run_args["template"])
        elif not args.template and run_args.get("template"):
            args.template = Path(run_args["template"])
        if native_checkpoint and restore_native("--report") and run_args.get("report"):
            args.report = Path(run_args["report"])
        elif not args.report and run_args.get("report"):
            args.report = Path(run_args["report"])
        if native_checkpoint and restore_native("--report-md") and run_args.get("report_md"):
            args.report_md = Path(run_args["report_md"])
        elif not args.report_md and run_args.get("report_md"):
            args.report_md = Path(run_args["report_md"])
        if native_checkpoint and restore_native("--ast") and run_args.get("ast"):
            args.ast = Path(run_args["ast"])
        elif not args.ast and run_args.get("ast"):
            args.ast = Path(run_args["ast"])
        if native_checkpoint and restore_native("--source-ast") and run_args.get("source_ast"):
            args.source_ast = Path(run_args["source_ast"])
        elif not args.source_ast and run_args.get("source_ast"):
            args.source_ast = Path(run_args["source_ast"])
        if "fail_on_risk" in run_args and restore_native("--fail-on-risk"):
            args.fail_on_risk = bool(run_args["fail_on_risk"])
        if "llm_enhance" in run_args and restore_native("--llm-enhance"):
            args.llm_enhance = run_args["llm_enhance"]
        if "llm_hint" in run_args and run_args["llm_hint"] and restore_native("--llm-hint"):
            args.llm_hint = run_args["llm_hint"]
        if "strict_normalize" in run_args and restore_native("--strict-normalize", "--no-strict-normalize"):
            args.strict_normalize = run_args["strict_normalize"]
        if "table_row_height_cm" in run_args and restore_native("--table-row-height-cm"):
            args.table_row_height_cm = run_args["table_row_height_cm"]
        if "table_row_height_rule" in run_args and restore_native("--table-row-height-rule"):
            args.table_row_height_rule = run_args["table_row_height_rule"]
        if not native_checkpoint and "pipeline" in run_args:
            args.pipeline = run_args["pipeline"]
        if not native_checkpoint and "modular_profile" in run_args:
            args.modular_profile = run_args["modular_profile"]
        if not native_checkpoint and "list_policy" in run_args:
            args.list_policy = run_args["list_policy"]

    if _os.environ.get("WX_DOC_FORMAT_RELEASE") == "1" and (args.ast or args.source_ast):
        parser.error("--ast and --source-ast are unavailable in release packages")

    # After --resume resolution, verify required paths exist.
    if args.resume and not args.input:
        raise SystemExit("--resume: run.json does not contain 'input', provide --input on CLI")
    if args.resume and not args.output:
        raise SystemExit("--resume: run.json does not contain 'output', provide --output on CLI")
    if args.resume and not native_checkpoint:
        try:
            verify_source_snapshot(args.input, str(run_info.get("source_sha256") or ""))
        except ProtocolError as exc:
            raise SystemExit(f"--resume: {exc}") from exc

    if args.pipeline == "legacy":
        if args.modular_profile != "legacy-full-v1":
            parser.error(
                "legacy diagnostic pipeline requires --modular-profile legacy-full-v1"
            )
        if args.list_policy != "legacy":
            parser.error("legacy diagnostic pipeline requires --list-policy legacy")
    else:
        if args.modular_profile != "native-v2":
            parser.error("modular pipeline requires --modular-profile native-v2")
        if args.list_policy != "wx-default-v2":
            parser.error("modular pipeline requires --list-policy wx-default-v2")

    if args.pipeline == "modular" or native_checkpoint:
        modular_protocol_mode = "resume" if args.resume else "generate_requests" if args.generate_requests else None
        modular_output_path = args.output
        if modular_output_path is None and args.generate_requests is not None:
            modular_output_path = args.generate_requests / ".pending-output.docx"
        if (
            args.modular_profile == "native-v2"
            and args.llm_enhance != "off"
            and not args.llm_command
            and modular_protocol_mode is None
        ):
            parser.error("native-v2 enhancement requires an explicit local --llm-command")
        native_v2 = native_checkpoint or _modular_native_v2_enabled(args)
        enhancement_call = None
        if native_v2 and args.llm_enhance != "off":
            local_command = _llm_call_from_command(args.llm_command or "")
            if local_command is not None:
                def enhancement_call(payload, *, _local_command=local_command):
                    return _local_command(json.dumps(payload, ensure_ascii=False))
        kernel_options = {
            "llm_enhance": args.llm_enhance,
            "protocol_mode": modular_protocol_mode,
            "source_ast_path": args.source_ast,
            "normalized_ast_path": args.ast,
            "report_path": args.report,
            "report_transform": report_for_output,
            "release": _os.environ.get("WX_DOC_FORMAT_RELEASE") == "1",
            "profile": "native_v2" if native_v2 else None,
            "enhancement_call": enhancement_call,
            "table_row_height_cm": args.table_row_height_cm,
            "table_row_height_rule": args.table_row_height_rule,
        }
        if native_v2 and args.generate_requests is not None:
            request_dir = args.generate_requests.resolve()
            kernel_options.update({
                "checkpoint_path": request_dir / "run.json",
                "requests_path": request_dir / "llm_requests.jsonl",
                "responses_path": request_dir / "llm_responses.jsonl",
                "enhancement_request_sink": [],
                "checkpoint_configuration": _native_checkpoint_configuration(
                    args,
                    output_path=modular_output_path,
                ),
            })
        elif native_v2 and native_checkpoint:
            kernel_options["checkpoint_path"] = args.resume.resolve()
            kernel_options["checkpoint_configuration"] = _native_checkpoint_configuration(
                args,
                output_path=modular_output_path,
            )
        result = ConversionKernel(
            modular_runner=None if native_v2 else lambda request: _run_legacy_full_adapter(args, request),
        ).convert(
            ConversionRequest(
                input_path=args.input,
                output_path=modular_output_path,
                template_path=args.template,
                pipeline=args.pipeline,
                list_policy=args.list_policy,
                options=kernel_options,
            )
        )
        kernel_published_report = (
            native_v2
            and result.status != "waiting_external"
            and args.report is not None
            and result.artifacts.get("report") == str(args.report)
        )
        output_report = report_for_output(result.as_report())
        if args.report is not None and not kernel_published_report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(output_report, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.report_md is not None:
            write_markdown_report(output_report, args.report_md)
        if result.diagnostics:
            diagnostic = result.diagnostics[0]
            print(diagnostic.get("code", "MODULAR_CONVERSION_FAILED"), file=sys.stderr)
        raise SystemExit(result.exit_code)

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
        out_doc._template_toc_fragment = extract_template_toc_fragment(out_doc)
        clear_document_body(out_doc)
        numbering_ids = merge_template_numbering_ids(template_profile, {})
        report["template_profile"] = {
            "path": str(args.template),
            "resolved_styles": template_profile.get("resolved_styles", {}),
            "missing_roles": template_profile.get("missing_roles", []),
            "numbering_ids": template_profile.get("numbering_ids", {}),
            "table_style": template_profile.get("table_style", {}),
        }
    else:
        out_doc = Document()
        numbering_ids = ensure_fallback_style_setup(out_doc)

    report["non_text_objects"] = scan_non_text_objects(args.input)
    source_document_model = None
    normalized_document_model = None
    suffix = args.input.suffix.lower()

    # ── Phase 1: Generate LLM requests and stop ─────────────────────
    if args.generate_requests:
        _run_generate_requests(args, report, runtime_context=runtime_context)
        return

    # ── Phase 3: Resume — set up file protocol context ──────────────
    file_protocol_ctx = None
    if args.resume:
        run_info = read_run_info(args.resume)
        reqs = read_requests(Path(run_info["requests_path"]))
        resps = read_responses(Path(run_info["responses_path"]))
        file_protocol_ctx = {
            "mode": "resume",
            "requests": reqs,
            "responses": resps,
        }

    if suffix in {".md", ".markdown"}:
        models = convert_md(args.input, out_doc, report, args.table_row_height_cm, args.table_row_height_rule, numbering_ids, args, file_protocol_ctx=file_protocol_ctx, runtime_context=runtime_context)
        source_document_model = models["source"]
        normalized_document_model = models["normalized"]
    elif suffix == ".docx":
        models = convert_docx(args.input, out_doc, args.table_row_height_cm, args.table_row_height_rule, args.strict_normalize, report, numbering_ids, args, file_protocol_ctx=file_protocol_ctx, runtime_context=runtime_context)
        source_document_model = models["source"]
        normalized_document_model = models["normalized"]
    else:
        raise SystemExit(f"Unsupported input type: {args.input.suffix}")

    adapter_artifacts = (runtime_context or {}).get("adapter_artifacts")
    if isinstance(adapter_artifacts, dict) and source_document_model is not None:
        adapter_artifacts["source_document_model"] = deepcopy(source_document_model)

    if file_protocol_ctx and file_protocol_ctx.get("new_requests"):
        run_info["request_stage"] = "ast"
        request_dir = Path(run_info["requests_path"]).parent
        write_requests_and_run(
            file_protocol_ctx.get("requests", []),
            run_info,
            request_dir,
        )
        print(
            f"Generated {len(file_protocol_ctx['new_requests'])} downstream "
            f"LLM request(s) in {request_dir.resolve()}"
        )
        print("Complete the new responses, then run --resume again.")
        return

    _finalize_and_audit_output(
        out_doc, report,
        args=args,
        template_profile=template_profile,
        normalized_model=normalized_document_model,
        source_model=source_document_model,
        models=models,
    )

    # Post-audit semantic review. Every mutation is applied to a cloned AST,
    # rendered into a fresh document, and accepted only after full re-audit.
    enhance_mode = normalize_mode(getattr(args, "llm_enhance", "off"))
    report["review_packet"] = build_review_packet(report, normalized_document_model or {})
    review_state = {
        "enabled": should_enhance(report, "C", enhance_mode),
        "triggered": False,
        "status": "not_requested",
        "rounds": 0,
        "repairable_findings": report["review_packet"].get("repairable_count", 0),
    }
    report["review_loop"] = review_state

    if review_state["enabled"] and not review_state["repairable_findings"]:
        review_state["status"] = "no_repairable_findings"

    if (
        review_state["enabled"]
        and review_state["repairable_findings"]
        and normalized_document_model is not None
    ):
        review_state["triggered"] = True
        review_state["status"] = "reviewing"
        baseline_report = deepcopy(report)
        baseline_model = deepcopy(normalized_document_model)
        candidate_report = deepcopy(report)
        candidate_model = deepcopy(normalized_document_model)
        before_applied = len(candidate_report.get("llm_enhancer", {}).get("applied", []))
        llm_call = _resolve_llm_call(args) if file_protocol_ctx is None else None
        candidate_model = _run_phase_enhancement(
            candidate_model, candidate_report, phase="C",
            llm_call=llm_call, hint=getattr(args, "llm_hint", None),
            file_protocol_ctx=file_protocol_ctx,
        )

        if file_protocol_ctx and file_protocol_ctx.get("new_requests"):
            run_info["request_stage"] = "review"
            request_dir = Path(run_info["requests_path"]).parent
            write_requests_and_run(
                file_protocol_ctx.get("requests", []), run_info, request_dir,
            )
            print(
                f"Generated {len(file_protocol_ctx['new_requests'])} post-audit "
                f"LLM review request(s) in {request_dir.resolve()}"
            )
            print("Complete the new responses, then run --resume again.")
            _cleanup_template_copy(out_doc)
            return

        applied = candidate_report.get("llm_enhancer", {}).get("applied", [])[before_applied:]
        review_state["decisions"] = applied
        if applied:
            review_state["rounds"] = 1
            candidate_doc = None
            try:
                candidate_model = normalize_document_model(candidate_model, candidate_report)
                candidate_report["table_semantics_audit"] = audit_model_table_semantics(candidate_model)
                candidate_report["caption_placement_model_audit"] = audit_model_caption_placement(candidate_model)
                candidate_doc = _render_review_candidate(
                    args, candidate_model, candidate_report,
                    template_profile=template_profile,
                    numbering_ids=numbering_ids,
                    models=models,
                )
                _finalize_and_audit_output(
                    candidate_doc, candidate_report,
                    args=args,
                    template_profile=template_profile,
                    normalized_model=candidate_model,
                    source_model=source_document_model,
                    models=models,
                )
                accepted, reason, comparison = accepted_candidate(
                    baseline_report, candidate_report, baseline_model, candidate_model,
                    decisions=applied,
                )
            except Exception as exc:
                accepted = False
                reason = "candidate_pipeline_error"
                comparison = {
                    "candidate_error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                }
            review_state.update({
                "status": "accepted" if accepted else "rolled_back",
                "accepted": accepted,
                "reason": reason,
                **comparison,
            })
            if accepted and candidate_doc is not None:
                candidate_report["review_packet"] = build_review_packet(
                    candidate_report, candidate_model,
                )
                _cleanup_template_copy(out_doc)
                out_doc = candidate_doc
                report = candidate_report
                normalized_document_model = candidate_model
                report["review_loop"] = review_state
            else:
                if candidate_doc is not None:
                    _cleanup_template_copy(candidate_doc)
                report["review_loop"] = review_state
        else:
            has_backend = llm_call is not None or file_protocol_ctx is not None
            review_state["status"] = "no_changes" if has_backend else "no_backend"

    remaining_findings = collect_audit_findings(report, normalized_document_model or {})
    if remaining_findings:
        report["unknown_pattern_packet"] = unknown_pattern_packet(
            report,
            normalized_document_model or {},
            input_hash=compute_source_sha256(args.input),
        )
    if report.get("review_loop", {}).get("status") == "rolled_back":
        report.setdefault("risk_warnings", []).append({
            "type": "document_review_rolled_back",
            "message": "Post-audit semantic repair was rejected and the baseline document was restored.",
            "reason": report["review_loop"].get("reason"),
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out_doc.save(args.output)
    _cleanup_template_copy(out_doc)

    if args.ast is not None and normalized_document_model is not None:
        args.ast.parent.mkdir(parents=True, exist_ok=True)
        args.ast.write_text(json.dumps(normalized_document_model, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.source_ast is not None and source_document_model is not None:
        args.source_ast.parent.mkdir(parents=True, exist_ok=True)
        args.source_ast.write_text(json.dumps(source_document_model, ensure_ascii=False, indent=2), encoding="utf-8")
    output_report = report_for_output(report)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(output_report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.report_md is not None:
        write_markdown_report(output_report, args.report_md)
    if args.fail_on_risk and report["risk_warnings"]:
        raise SystemExit("Conversion completed with risk warnings. Review the report before delivery.")
    print(args.output)


if __name__ == "__main__":
    main()
