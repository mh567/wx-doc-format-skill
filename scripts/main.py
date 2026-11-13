from __future__ import annotations

import os
import sys

if os.environ.get("WXDF_DEPS"):
    sys.path.insert(0, os.environ["WXDF_DEPS"])

import argparse
from copy import deepcopy
import errno
import json
import re
import tempfile
from pathlib import Path
from zipfile import BadZipFile

from reporting import write_markdown_report
from wxdf.capabilities import normalize_mode
from wxdf.kernel import ConversionKernel, ConversionRequest


SKILL_VERSION = "0.20.4"

# The conversion identity.  It is recorded in the run configuration and the
# report so a resumed run can prove it continued the same conversion, and it is
# not selectable: there is one pipeline, one profile and one list policy.
NATIVE_PIPELINE = "modular"
NATIVE_PROFILE = "wx-default-v2"

DEFAULT_TABLE_ROW_HEIGHT_CM = 0.7
DEFAULT_TABLE_ROW_HEIGHT_RULE = "at-least"
DEFAULT_CAPTION_NUMBERING = "chapter"


class _PathIdentityError(RuntimeError):
    pass


def _resolve_path(path: Path) -> Path:
    try:
        resolved = Path(path).resolve(strict=False)
    except RuntimeError as exc:
        raise _PathIdentityError(f"cannot resolve path {path}: {exc}") from exc
    try:
        os.stat(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _PathIdentityError(f"cannot resolve path {path}: {exc}") from exc
    return resolved


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _missing_path_parts(path: Path) -> tuple[Path, tuple[str, ...]]:
    absolute = _absolute_path(path)
    parts = [absolute.name]
    parent = absolute.parent
    while not parent.exists():
        if parent == parent.parent:
            break
        parts.insert(0, parent.name)
        parent = parent.parent
    return parent, tuple(parts)


def _probe_aliases(left: Path, right: Path) -> bool:
    left_parent, left_parts = _missing_path_parts(left)
    right_parent, right_parts = _missing_path_parts(right)
    try:
        same_parent = os.path.samefile(left_parent, right_parent)
    except OSError:
        same_parent = _resolve_path(left_parent) == _resolve_path(right_parent)
    if not same_parent:
        return False
    try:
        with tempfile.TemporaryDirectory(prefix=".wxdf-identity-", dir=str(left_parent)) as directory:
            first = Path(directory).joinpath(*left_parts)
            second = Path(directory).joinpath(*right_parts)
            first.parent.mkdir(parents=True, exist_ok=True)
            second.parent.mkdir(parents=True, exist_ok=True)
            first.touch(exist_ok=False)
            try:
                second.touch(exist_ok=False)
            except FileExistsError:
                return True
            return os.path.samefile(first, second)
    except OSError:
        return _resolve_path(left) == _resolve_path(right)


def _same_path(left: Path, right: Path) -> bool:
    if _resolve_path(left) == _resolve_path(right):
        return True
    try:
        if Path(left).exists() and Path(right).exists():
            return os.path.samefile(left, right)
    except OSError:
        pass
    return _probe_aliases(Path(left), Path(right))


def _validate_distinct_paths(
    parser: argparse.ArgumentParser,
    *,
    sources: list[tuple[str, Path | None]],
    outputs: list[tuple[str, Path | None]],
) -> None:
    source_entries = [(name, Path(path)) for name, path in sources if path is not None]
    output_entries = [(name, Path(path)) for name, path in outputs if path is not None]
    try:
        for output_name, output_path in output_entries:
            for source_name, source_path in source_entries:
                if _same_path(output_path, source_path):
                    parser.error(f"{output_name} must not be the same path as {source_name}")
        for index, (first_name, first_path) in enumerate(output_entries):
            for second_name, second_path in output_entries[index + 1:]:
                if _same_path(first_path, second_path):
                    parser.error(f"{first_name} and {second_name} must be different paths")
    except _PathIdentityError as exc:
        parser.error(str(exc))


def _write_json_report(path: Path, report: dict) -> None:
    """Write a report through a same-directory temporary file and atomic replace."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


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
                shlex.split(command) if _os.name == "nt" else command,
                input=prompt,
                text=True,
                shell=_os.name != "nt",
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
    if value.get("status") == "incomplete":
        return "incomplete"
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


_DIAGNOSTIC_SEVERITY_RANK = {
    "critical": 0,
    "error": 1,
    "high": 2,
    "medium": 3,
    "warning": 4,
    "low": 5,
    "info": 6,
}


def _reported_diagnostic_code(diagnostics: list) -> str:
    """Return the code worth echoing on stderr.

    The list leads with the pipeline's own records, which are informational, so
    reporting the first entry can name a harmless record while the run actually
    failed on a later one.  Rank by severity and keep the advisory findings,
    whose defect normalization already resolved, behind the ones that still
    describe the delivered document.
    """

    from wxdf.auditing import is_advisory_audit_issue

    ranked: list[tuple[int, int, str]] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        severity = str(diagnostic.get("severity") or "")
        rank = _DIAGNOSTIC_SEVERITY_RANK.get(severity, len(_DIAGNOSTIC_SEVERITY_RANK))
        advisory = 1 if is_advisory_audit_issue(diagnostic) else 0
        ranked.append((advisory, rank, str(diagnostic.get("code") or "")))
    if not ranked:
        return "MODULAR_CONVERSION_FAILED"
    # The sort is stable, so equal ranks keep the pipeline's own order.
    ranked.sort(key=lambda item: item[:2])
    return ranked[0][2] or "MODULAR_CONVERSION_FAILED"


_DELIVERY_FINDING_MESSAGES = {
    "audit_exception": "部分自动检查未完成，请复核输出文档。",
    "inline_content_changed": "输出行内内容与源内容存在待核对差异。",
    "paragraph_order_changed": "输出段落顺序或文本存在待核对差异。",
    "inline_paragraph_missing": "行内检查未找到预期段落，请核对相关内容。",
    "image_resource_missing": "图片资源检查发现异常，请核对图片。",
    "image_count_changed": "图片数量检查发现差异，请核对图片。",
    "table_count_changed": "表格数量检查发现差异，请核对表格。",
    "unexpected_styles": "输出包含模板外样式，请复核格式。",
}


def _public_delivery_findings(findings: list) -> list[dict]:
    """Publish diagnostic identity and paragraph positions without arbitrary strings."""

    result = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", code):
            code = "audit_finding"
        finding = {"code": code, "message": _DELIVERY_FINDING_MESSAGES.get(code, "自动检查发现需复核项，请查看转换报告。")}
        for key, values in (
            ("category", {"audit", "model", "saved_output", "appendix", "document_format", "page_contract", "list_readback", "content_integrity"}),
            ("severity", {"critical", "high", "medium", "low", "warning", "info", "error"}),
            ("certainty", {"confirmed", "suspected", "reported", "unknown", "incomplete"}),
        ):
            if isinstance(item.get(key), str) and item[key] in values:
                finding[key] = item[key]
        block_id = item.get("block_id")
        if isinstance(block_id, str) and re.fullmatch(r"(?:u-[0-9a-f]{16}|b[0-9]+|(?:node|block)-[0-9]+)", block_id):
            finding["block_id"] = block_id
        location = item.get("location")
        if isinstance(location, str) and re.fullmatch(r"(?:第[0-9]+段|Paragraph [0-9]+)", location):
            finding["location"] = location
        result.append(finding)
    return result


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
    summary = {
        "schema_version": "1.0",
        "status": status,
        "audits": audits,
        "unexpected_styles_count": unexpected_styles_count,
        "manual_review_required": manual_review_required,
        "diagnostic_codes": diagnostic_codes,
    }
    for key in ("delivery_policy", "conversion_status", "audit_status", "delivery_status"):
        if key in report:
            summary[key] = _public_summary_scalar(report[key])
    if isinstance(report.get("audit_findings"), list):
        summary["audit_findings"] = _public_delivery_findings(report["audit_findings"])
    if report.get("audit_status") in {"issues", "incomplete"}:
        summary["manual_review_required"] = True
    return summary


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
    if isinstance(report.get("audit_findings"), list):
        public_report["audit_findings"] = _public_delivery_findings(report["audit_findings"])
    public_report["public_summary"] = public_report_summary(report)
    return public_report


def _native_checkpoint_configuration(args, *, output_path: Path) -> dict:
    """Freeze the effective native CLI configuration used across resume."""

    configuration = {
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
        "delivery_policy": args.delivery_policy,
        "table_row_height_cm": args.table_row_height_cm,
        "table_row_height_rule": args.table_row_height_rule,
        "caption_numbering": args.caption_numbering,
        "pipeline": NATIVE_PIPELINE,
        "list_policy": NATIVE_PROFILE,
    }
    if getattr(args, "allow_external_assets", False):
        configuration["allow_external_assets"] = True
    if getattr(args, "_legacy_delivery_configuration", False):
        configuration.pop("delivery_policy")
    return configuration


def _run_review_mode(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    from review_report import write_review_report, write_review_report_html
    from wxdf.input_reviewer import plan_review_requests, review_input_document
    from wxdf.review_enhancement import (
        ReviewEnhancement,
        ReviewProtocolError,
        load_protocol,
        verify_protocol_sources,
        write_protocol,
    )

    if args.output:
        parser.error("--review does not use --output; use --report / --report-md / --report-html")
    if args.resume and args.generate_requests:
        parser.error("--generate-requests and --resume are mutually exclusive")

    mode = normalize_mode(args.llm_enhance)
    enhancement = None
    if args.resume:
        # The run file records the input and template the requests were built
        # from, so a resume can restore them and prove the document has not
        # changed under the responses that answer it.
        try:
            run, responses = load_protocol(args.resume)
            args.input = args.input or Path(str(run["input"]["path"]))
            args.template = args.template or Path(str(run["template"]["path"]))
            verify_protocol_sources(run, args.input, args.template)
        except ReviewProtocolError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from None
        except (KeyError, TypeError) as exc:
            print(f"REVIEW_RUN_INVALID: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        mode = str(run.get("mode") or mode)
        enhancement = ReviewEnhancement(mode=mode, responses=tuple(responses))

    if not args.input:
        parser.error("--review requires --input")
    if not args.template:
        parser.error("--review requires --template")
    if args.input.suffix.lower() != ".docx":
        print(f"REVIEW_INPUT_UNSUPPORTED: 审查只接受 DOCX 输入：{args.input}", file=sys.stderr)
        raise SystemExit(2)

    _validate_distinct_paths(
        parser,
        sources=[("--input", args.input), ("--template", args.template)],
        outputs=[
            ("--report", args.report),
            ("--report-md", args.report_md),
            ("--report-html", args.report_html),
            ("--ast", args.ast),
            ("--source-ast", args.source_ast),
        ],
    )

    if mode != "off" and not (args.llm_command or args.generate_requests or args.resume):
        parser.error("AI review requires --llm-command, --generate-requests or --resume")

    if args.generate_requests is not None:
        requests = plan_review_requests(args.input, args.template, mode)
        run_path = write_protocol(
            args.generate_requests,
            requests,
            input_path=args.input,
            template_path=args.template,
            mode=mode,
        )
        print(
            f"Wrote {len(requests)} review request(s) to {run_path.parent}; "
            f"fill review_responses.jsonl, then resume {run_path}"
        )
        raise SystemExit(0)

    if enhancement is None and mode != "off":
        local_command = _llm_call_from_command(args.llm_command or "")
        if local_command is None:
            parser.error("AI review requires --llm-command")
        enhancement = ReviewEnhancement(mode=mode, call=local_command)

    # A review that cannot read its input is a caller error, not a crash: the
    # release boundary turns it into a stable code instead of an unexpected exit.
    try:
        result = review_input_document(args.input, args.template, enhancement=enhancement)
    except (OSError, ValueError, KeyError, BadZipFile) as exc:
        print(f"REVIEW_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if args.report:
        _write_json_report(args.report, result)
    if args.report_md:
        write_review_report(result, args.report_md)
    if args.report_html:
        write_review_report_html(result, args.report_html)
    score = result.get("score", 0)
    grade = result.get("grade", "unknown")
    print(f"Review complete: {score}/100 ({grade})")
    summary = result.get("summary", {})
    if summary.get("total_issues"):
        print(
            f"Found {summary['total_issues']} issues "
            f"(critical {summary.get('critical', 0)}, high {summary.get('high', 0)}, "
            f"medium {summary.get('medium', 0)}, low {summary.get('low', 0)})"
        )
    else:
        print("No issues found")
    ai_review = result.get("ai_review") or {}
    if ai_review.get("status") not in {None, "not_requested"}:
        detail = "、".join(
            f"{name}={info.get('status')}"
            for name, info in sorted((ai_review.get("capabilities") or {}).items())
        )
        print(f"AI review: {ai_review.get('status')} ({detail})")
    raise SystemExit(0)


def _ensure_utf8_console() -> None:
    """Standalone runtime starts without a locale and defaults stdout to ASCII."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").replace("-", "").lower()
        if encoding == "utf8":
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def main(argv: list[str] | None = None, *, runtime_context: dict | None = None) -> None:
    global SKILL_VERSION
    _ensure_utf8_console()
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
    parser.add_argument("--strict-normalize", action="store_true", default=True)
    parser.add_argument("--no-strict-normalize", dest="strict_normalize", action="store_false")
    parser.add_argument(
        "--delivery-policy", choices=["strict", "desktop"], default="strict",
        help="Delivery gate: strict audit acceptance, or readable Desktop output with review findings.",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--report-md", type=Path, default=None)
    parser.add_argument("--report-html", type=Path, default=None, help="HTML review report path (--review mode).")
    parser.add_argument(
        "--allow-external-assets",
        action="store_true",
        default=False,
        help="Allow Markdown images outside the source directory to be read.",
    )
    parser.add_argument("--ast", type=Path, default=None, help="Normalized document model (Step 2 output).")
    parser.add_argument("--source-ast", type=Path, default=None, help="Parsed source document model (Step 1 output).")
    parser.add_argument("--table-row-height-cm", type=float, default=DEFAULT_TABLE_ROW_HEIGHT_CM)
    parser.add_argument("--table-row-height-rule", choices=["exact", "at-least"], default=DEFAULT_TABLE_ROW_HEIGHT_RULE)
    parser.add_argument(
        "--caption-numbering",
        choices=["chapter", "sequential"],
        default=DEFAULT_CAPTION_NUMBERING,
        help="Caption numbering form (default: chapter, e.g. 图5-1; sequential renders 图1).",
    )
    parser.add_argument("--fail-on-risk", action="store_true")
    parser.add_argument(
        "--llm-enhance",
        choices=[
            "off", "auto",
            "s", "a", "b", "c", "sa", "sb", "sc", "ab", "ac", "bc",
            "sab", "sac", "sbc", "abc", "sabc",
            "d", "e", "f", "de", "df", "ef", "def",
            "force-s", "force-a", "force-b", "force-c", "force-sa", "force-sb", "force-sc",
            "force-ab", "force-ac", "force-bc", "force-sab", "force-sac", "force-sbc",
            "force-abc", "force-sabc",
            "force-d", "force-e", "force-f", "force-def", "force-sabcdef", "sabcdef",
            "toc_region_review", "list_detect", "caption_gen", "document_review",
            "citation_order_review", "glossary_review", "list_prose_review",
            "all",
        ],
        default="off",
        help=(
            "LLM enhancement level (default: off). "
            "Conversion capabilities: toc_region_review / list_detect / caption_gen / document_review. "
            "Review capabilities: citation_order_review / glossary_review / list_prose_review. "
            "all selects every registered capability."
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
    parser.add_argument(
        "--review",
        action="store_true",
        default=False,
        help=(
            "Review mode: audit the input document against template requirements "
            "and output a compliance report. Mutually exclusive with conversion."
        ),
    )
    args = parser.parse_args(cli_arguments)

    import os as _os

    SKILL_VERSION = _load_version()
    if args.version:
        print(SKILL_VERSION)
        return

    if _os.environ.get("WX_DOC_FORMAT_RELEASE") == "1" and (args.ast or args.source_ast):
        parser.error("--ast and --source-ast are unavailable in release packages")

    if args.review:
        _run_review_mode(args, parser)
        return

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

    # Normalize capability names to the compact enhancement phases.
    args.llm_enhance = normalize_mode(args.llm_enhance)

    # ── Handle --resume: load run.json and restore original CLI args ──
    native_checkpoint = False
    if args.resume:
        run_info = json.loads(args.resume.read_text(encoding="utf-8"))
        if run_info.get("protocol_version") == "1.0":
            parser.error(
                "LEGACY_CHECKPOINT_UNSUPPORTED: protocol 1.0 cannot resume; "
                "regenerate requests with the current CLI"
            )
        checkpoint_schema_version = run_info.get("checkpoint_schema_version")
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
        run_args = run_info.get("configuration", {})
        def restore_native(flag: str, *aliases: str) -> bool:
            return not explicit_options.intersection((flag, *aliases))

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
        if "delivery_policy" in run_args and restore_native("--delivery-policy"):
            args.delivery_policy = run_args["delivery_policy"]
        args._legacy_delivery_configuration = (
            "delivery_policy" not in run_args
            and restore_native("--delivery-policy")
            and args.delivery_policy == "strict"
        )
        if "table_row_height_cm" in run_args and restore_native("--table-row-height-cm"):
            args.table_row_height_cm = run_args["table_row_height_cm"]
        if "table_row_height_rule" in run_args and restore_native("--table-row-height-rule"):
            args.table_row_height_rule = run_args["table_row_height_rule"]
        if "caption_numbering" in run_args and restore_native("--caption-numbering"):
            args.caption_numbering = run_args["caption_numbering"]
        if run_args.get("allow_external_assets") and restore_native("--allow-external-assets"):
            args.allow_external_assets = True
    if args.delivery_policy not in {"strict", "desktop"}:
        parser.error("unsupported delivery policy in checkpoint")
    if _os.environ.get("WX_DOC_FORMAT_RELEASE") == "1" and (args.ast or args.source_ast):
        parser.error("--ast and --source-ast are unavailable in release packages")

    # After --resume resolution, verify required paths exist.
    if args.resume and not args.input:
        raise SystemExit("--resume: run.json does not contain 'input', provide --input on CLI")
    if args.resume and not args.output:
        raise SystemExit("--resume: run.json does not contain 'output', provide --output on CLI")

    _validate_distinct_paths(
        parser,
        sources=[("--input", args.input), ("--template", args.template)],
        outputs=[
            ("--output", args.output),
            ("--report", args.report),
            ("--report-md", args.report_md),
            ("--report-html", args.report_html),
            ("--ast", args.ast),
            ("--source-ast", args.source_ast),
        ],
    )

    modular_protocol_mode = "resume" if args.resume else "generate_requests" if args.generate_requests else None

    modular_output_path = args.output
    if modular_output_path is None and args.generate_requests is not None:
        modular_output_path = args.generate_requests / ".pending-output.docx"
    if (
        args.llm_enhance != "off"
        and not args.llm_command
        and modular_protocol_mode is None
    ):
        parser.error("enhancement requires an explicit local --llm-command")
    enhancement_call = None
    if args.llm_enhance != "off":
        local_command = _llm_call_from_command(args.llm_command or "")
        if local_command is not None:
            def enhancement_call(payload, *, _local_command=local_command):
                return _local_command(json.dumps(payload, ensure_ascii=False))
    kernel_options = {
        "delivery_policy": args.delivery_policy,
        "llm_enhance": args.llm_enhance,
        "protocol_mode": modular_protocol_mode,
        "source_ast_path": args.source_ast,
        "normalized_ast_path": args.ast,
        "report_path": args.report,
        "report_transform": report_for_output,
        "release": _os.environ.get("WX_DOC_FORMAT_RELEASE") == "1",
        "profile": "native_v2",
        "enhancement_call": enhancement_call,
        "table_row_height_cm": args.table_row_height_cm,
        "table_row_height_rule": args.table_row_height_rule,
        "caption_numbering": args.caption_numbering,
        "allow_external_assets": bool(args.allow_external_assets),
    }
    if args.generate_requests is not None:
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
    elif native_checkpoint:
        kernel_options["checkpoint_path"] = args.resume.resolve()
        kernel_options["checkpoint_configuration"] = _native_checkpoint_configuration(
            args,
            output_path=modular_output_path,
        )
    result = ConversionKernel().convert(
        ConversionRequest(
            input_path=args.input,
            output_path=modular_output_path,
            template_path=args.template,
            pipeline=NATIVE_PIPELINE,
            list_policy=NATIVE_PROFILE,
            options=kernel_options,
        )
    )
    if result.terminal_replay:
        raise SystemExit(result.exit_code)
    kernel_published_report = (
        result.status != "waiting_external"
        and args.report is not None
        and result.artifacts.get("report") == str(args.report)
    )
    output_report = report_for_output(result.as_report())
    if args.report is not None and not kernel_published_report:
        _write_json_report(args.report, output_report)
    if args.report_md is not None:
        write_markdown_report(output_report, args.report_md)
    if result.diagnostics:
        print(_reported_diagnostic_code(result.diagnostics), file=sys.stderr)
    raise SystemExit(result.exit_code)

if __name__ == "__main__":
    main()
