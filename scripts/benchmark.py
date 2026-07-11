#!/usr/bin/env python3
"""Benchmark harness: run wx-doc-format conversion on 6 documents × 2+ modes.

In-process approach — imports main.py functions directly to avoid
subprocess sandbox restrictions.

Usage::

    cd scripts
    .venv/bin/python3 benchmark.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

# Ensure scripts/ is on sys.path for local imports.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# ── Config ───────────────────────────────────────────────────────────────

SKILL_VERSION = "0.8.5"

MODES = ["off", "abc"]

# Paths (relative to project root — the parent of scripts/).
DEFAULT_INPUT_DIR = "../input"
DEFAULT_OUTPUT_DIR = "../wx_output/benchmark"
DEFAULT_TEMPLATE = "../assets/技术文件格式及书写要求.docx"
DEFAULT_RESULTS = "../wx_output/benchmark_results.json"
TIMEOUT_SECONDS = 300

TEMPLATE_FILENAME = "技术文件格式及书写要求.docx"


# ── Helpers ──────────────────────────────────────────────────────────────


def _script_dir() -> Path:
    return _HERE


def _project_root() -> Path:
    return _HERE.parent


def discover_docs(input_dir: Path) -> list[Path]:
    """Recursively scan *input_dir* for ``.docx`` files.

    Excludes the template file.
    """
    docs: list[Path] = []
    for p in input_dir.rglob("*.docx"):
        if p.name == TEMPLATE_FILENAME:
            continue
        docs.append(p)
    return sorted(docs)


# ── In-process conversion ────────────────────────────────────────────────


def _run_single_conversion(
    input_path: Path,
    output_path: Path,
    template_path: Path,
    mode: str,
    report_path: Path,
    timeout: int = 300,
) -> dict:
    """Run the main conversion pipeline via subprocess for reliable timeout."""
    import subprocess

    cmd = [
        sys.executable, "-m", "main",
        "--input", str(input_path),
        "--output", str(output_path),
        "--template", str(template_path),
        "--report", str(report_path),
        "--llm-enhance", mode,
    ]

    try:
        subprocess.run(cmd, cwd=str(_HERE), timeout=timeout,
                       capture_output=True, check=True)
    except subprocess.TimeoutExpired:
        return {"_benchmark_error": f"Timeout after {timeout}s"}
    except subprocess.CalledProcessError as e:
        return {"_benchmark_error": f"Exit {e.returncode}: {e.stderr.decode()[-200:]}"}

    if report_path.exists():
        return json.loads(report_path.read_text(encoding="utf-8"))
    return {}


def collect_metrics(
    report: dict,
    wall_time_sec: float,
    output_path: Path,
) -> dict:
    """Extract standardised metrics from a conversion report."""
    # Helper: safely get length of a list field.
    _len = lambda v: len(v) if v else 0
    if not report or report.get("_benchmark_error"):
        return {
            "status": "error",
            "message": report.get("_benchmark_error", "Empty report"),
            "wall_time_sec": round(wall_time_sec, 3),
            "output_size_kb": (
                round(output_path.stat().st_size / 1024, 1)
                if output_path.exists() else 0.0
            ),
        }

    metrics: dict = {
        "status": "ok",
        "wall_time_sec": round(wall_time_sec, 3),
    }

    # ── Output size ──
    if output_path.exists():
        metrics["output_size_kb"] = round(output_path.stat().st_size / 1024, 1)

    # ── Compliance ──
    tf = report.get("template_finalizer", {})
    style_audit = tf.get("style_audit", {})
    audit = report.get("audit", {})

    compliance = {
        "unexpected_styles": style_audit.get("unexpected_styles", []),
        "heading_hierarchy_warnings": len(audit.get("heading_hierarchy_warnings", [])),
        "ordered_list_nums_without_restart": len(audit.get("ordered_list_nums_without_restart", [])),
        "markdown_residue": len(audit.get("markdown_residue", [])),
        "table_paragraphs_not_table_body": len(audit.get("table_paragraphs_not_table_body", [])),
        "code_sample_table_alignment_issues": len(audit.get("code_sample_table_alignment_issues", [])),
        "risk_warnings_count": len(report.get("risk_warnings", [])),
    }
    metrics["compliance"] = compliance

    # ── Source document model summary ──
    source_summary = report.get("source_document_model_summary", {})
    source_block_counts = source_summary.get("block_counts", {})
    metrics["source"] = {
        "blocks": source_summary.get("block_count", 0),
        "heading": source_block_counts.get("heading", 0),
        "body": source_block_counts.get("body", 0),
        "table": source_block_counts.get("table", 0),
    }

    # ── Normalized document model summary ──
    norm_summary = report.get("document_model_summary", {})
    norm_block_counts = norm_summary.get("block_counts", {})
    metrics["normalized"] = {
        "blocks": norm_summary.get("block_count", 0),
        "heading": norm_block_counts.get("heading", 0),
        "body": norm_block_counts.get("body", 0),
        "table": norm_block_counts.get("table", 0),
    }

    # ── Diff ──
    diff = report.get("document_model_diff", {})
    metrics["diff"] = {
        "block_count_delta": diff.get("block_count_delta", 0),
    }

    # ── Suspicion score (recompute) ──
    from llm_enhancer import compute_suspicion_score

    parse_report_signals = {
        "suspect_visual_headings": _len(report.get("suspect_visual_headings")),
        "ambiguous_short_paragraphs": _len(report.get("ambiguous_short_paragraphs")),
        "inferred_headings": _len(report.get("inferred_headings")),
        "inferred_lists": _len(report.get("inferred_lists")),
        "unstyled_paragraphs": _len(report.get("unstyled_paragraphs")),
    }
    suspicion_report = {
        "parse_report": parse_report_signals,
        "source_document_model_summary": source_summary,
    }
    metrics["suspicion_score"] = round(compute_suspicion_score(suspicion_report), 4)
    metrics["triggered_auto"] = metrics["suspicion_score"] >= 0.15

    # ── LLM enhancement ──
    llm_enh = report.get("llm_enhancer", {})
    applied = llm_enh.get("applied", [])
    skipped = llm_enh.get("skipped", [])
    errors = llm_enh.get("errors", [])
    phase_summaries = llm_enh.get("phase_summaries", [])

    # Per-phase counts from phase_summaries (per-phase, no diff needed).
    phase_a_applied = 0
    phase_a_skipped = 0
    phase_b_applied = 0
    phase_c_applied = 0

    for ps in phase_summaries:
        ph = ps.get("phase", "")
        cur_applied = ps.get("applied_count", 0)
        cur_skipped = ps.get("skipped_count", 0)
        if ph == "A":
            phase_a_applied = cur_applied
            phase_a_skipped = cur_skipped
        elif ph == "B":
            phase_b_applied = cur_applied
        elif ph == "C":
            phase_c_applied = cur_applied

    phase_a_rate = llm_enh.get("phase_a_applied_rate", 0.0)

    reason_counter: Counter[str] = Counter()
    for d in applied:
        reason = d.get("reason", "")
        if reason:
            reason_counter[reason] += 1
    top_reasons = dict(reason_counter.most_common(5))

    phase_metrics_list = llm_enh.get("phase_metrics", [])

    llm_enhancement = {
        "phase_a_applied": phase_a_applied,
        "phase_a_skipped": phase_a_skipped,
        "phase_b_applied": phase_b_applied,
        "phase_c_applied": phase_c_applied,
        "llm_errors": len(errors),
        "phase_a_applied_rate": phase_a_rate,
        "llm_call_count": len(llm_enh.get("prompts", [])),
        "top_reasons": top_reasons,
        "phase_metrics": phase_metrics_list,
    }
    metrics["llm_enhancement"] = llm_enhancement

    return metrics


# ── Main ─────────────────────────────────────────────────────────────────


def run_benchmark(
    input_dir: Path,
    output_dir: Path,
    template: Path,
    results_path: Path,
) -> list[dict]:
    """Discover documents, run conversions in-process, collect metrics."""
    docs = discover_docs(input_dir)

    if not docs:
        print("WARNING: No .docx files found in input directory.", file=sys.stderr)
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []

    print(f"Found {len(docs)} document(s), running modes: {', '.join(MODES)}.\n")

    for doc_idx, doc in enumerate(docs, start=1):
        doc_name = doc.stem

        # Determine category from path.
        category = doc.parent.name
        try:
            relative = doc.relative_to(input_dir)
            parts = relative.parts
            if len(parts) >= 2:
                category = parts[0]
        except ValueError:
            pass

        for mode in MODES:
            output = output_dir / f"{doc_name}_{mode}.docx"
            report_file = output_dir / f"{doc_name}_{mode}.json"

            print(f"[{doc_idx}/{len(docs)}] {doc_name}  mode={mode}", end="  ")
            sys.stdout.flush()

            start = time.perf_counter()

            # Run conversion in-process.
            report = _run_single_conversion(doc, output, template, mode, report_file)
            wall = time.perf_counter() - start

            metrics = collect_metrics(report, wall, output)

            # Attach metadata.
            metrics.update({
                "document": doc_name,
                "category": category,
                "mode": mode,
                "input_size_kb": round(doc.stat().st_size / 1024, 1),
            })

            results.append(metrics)

            s = metrics.get("status", "?")
            wt = metrics.get("wall_time_sec", "?")
            print(f"status={s}  wall={wt}s")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run wx-doc-format benchmark: 6 docs × up to 3 modes."
    )
    parser.add_argument("--input-dir", type=Path, default=None,
                        help="Directory to scan for .docx input files (default: input/)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Output directory (default: ../wx_output/benchmark)")
    parser.add_argument("--template", type=Path, default=None,
                        help="Template DOCX path (default: assets/技术文件格式及书写要求.docx)")
    parser.add_argument("--results", type=Path, default=None,
                        help="Results JSON path (default: ../wx_output/benchmark_results.json)")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS,
                        help="Per-conversion timeout in seconds (ignored in in-process mode)")
    args = parser.parse_args()

    root = _project_root()

    input_dir = args.input_dir or (root / DEFAULT_INPUT_DIR)
    output_dir = args.output_dir or (root / DEFAULT_OUTPUT_DIR)
    template = args.template or (root / DEFAULT_TEMPLATE)
    results_path = args.results or (root / DEFAULT_RESULTS)

    # Validate paths.
    if not input_dir.is_dir():
        print(f"ERROR: Input directory not found: {input_dir}", file=sys.stderr)
        sys.exit(1)
    if not template.is_file():
        print(f"ERROR: Template not found: {template}", file=sys.stderr)
        sys.exit(1)

    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    print(f"wx-doc-format Benchmark v{SKILL_VERSION}")
    print(f"  Python:   {py_version}")
    print(f"  Input:    {input_dir}")
    print(f"  Output:   {output_dir}")
    print(f"  Template: {template}")
    print(f"  Results:  {results_path}")
    print(f"  Modes:    {', '.join(MODES)}")
    print()

    results = run_benchmark(input_dir, output_dir, template, results_path)

    # ── Write unified results ──
    benchmark_meta = {
        "skill_version": SKILL_VERSION,
        "python_version": py_version,
        "template": str(template),
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "timeout_sec": args.timeout,
    }

    output = {
        "benchmark_meta": benchmark_meta,
        "results": results,
    }

    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n{'='*60}")
    print(f"Results written to {results_path}")
    print(f"Total conversions: {len(results)}")
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    error_count = sum(1 for r in results if r.get("status") not in ("ok",))
    print(f"  OK:     {ok_count}")
    print(f"  Issues: {error_count}")

    # Summary table.
    print(f"\n{'─'*60}")
    print(f"{'Document':<30} {'Mode':<6} {'Status':<8} {'Wall(s)':<8} {'Styles':<8}")
    print(f"{'─'*60}")
    for r in results:
        doc_short = r.get("document", "?")
        mode = r.get("mode", "?")
        status = r.get("status", "?")
        wall = r.get("wall_time_sec", "?")
        wall_s = f"{wall:.1f}" if isinstance(wall, (int, float)) else "?"
        styles = r.get("compliance", {}).get("unexpected_styles", [])
        styles_s = str(len(styles)) if isinstance(styles, list) else "?"
        print(f"{doc_short:<30} {mode:<6} {status:<8} {wall_s:<8} {styles_s:<8}")
    print(f"{'─'*60}")


if __name__ == "__main__":
    main()
