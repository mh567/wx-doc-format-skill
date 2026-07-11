#!/usr/bin/env python3
"""Standalone benchmark runner — self-contained, no subprocess needed."""
import json, os, sys, time
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ── Config ───────────────────────────────────────────────────────────────
INPUT_BASE = HERE.parent.parent / "input"  # zero项目/input/
OUTPUT_DIR = HERE.parent.parent / "wx_output" / "benchmark"
TEMPLATE = HERE.parent / "assets" / "技术文件格式及书写要求.docx"
RESULTS = HERE.parent.parent / "wx_output" / "benchmark_results.json"
MODES = ["off", "auto", "abc"]

DOCS = [
    (INPUT_BASE / "接口类" / "接口控制文件2.docx", "接口类"),
    (INPUT_BASE / "接口类" / "接口文档0708.docx", "接口类"),
    (INPUT_BASE / "接口类" / "身份接口策略下发部分.docx", "接口类"),
    (INPUT_BASE / "方案类" / "IAM全球部署高可用.docx", "方案类"),
    (INPUT_BASE / "方案类" / "生物特征认证系统集成方案.docx", "方案类"),
    (INPUT_BASE / "方案类" / "身份对象模型.docx", "方案类"),
]

from llm_enhancer import compute_suspicion_score

def collect_metrics(report, wall_time, output_path):
    if not report or report.get("_benchmark_error"):
        return {
            "status": "error",
            "wall_time_sec": round(wall_time, 3),
            "output_size_kb": (
                round(output_path.stat().st_size / 1024, 1)
                if output_path.exists() else 0.0
            ),
        }
    metrics = {"status": "ok", "wall_time_sec": round(wall_time, 3)}
    if output_path.exists():
        metrics["output_size_kb"] = round(output_path.stat().st_size / 1024, 1)
    tf = report.get("template_finalizer", {})
    style_audit = tf.get("style_audit", {})
    audit = report.get("audit", {})
    metrics["compliance"] = {
        "unexpected_styles": style_audit.get("unexpected_styles", []),
        "heading_hierarchy_warnings": len(audit.get("heading_hierarchy_warnings", [])),
        "ordered_list_nums_without_restart": len(audit.get("ordered_list_nums_without_restart", [])),
        "markdown_residue": len(audit.get("markdown_residue", [])),
        "table_paragraphs_not_table_body": len(audit.get("table_paragraphs_not_table_body", [])),
        "code_sample_table_alignment_issues": len(audit.get("code_sample_table_alignment_issues", [])),
        "risk_warnings_count": len(report.get("risk_warnings", [])),
    }
    src = report.get("source_document_model_summary", {})
    src_counts = src.get("block_counts", {})
    metrics["source"] = {"blocks": src.get("block_count", 0), "heading": src_counts.get("heading", 0), "body": src_counts.get("body", 0), "table": src_counts.get("table", 0)}
    norm = report.get("document_model_summary", {})
    norm_counts = norm.get("block_counts", {})
    metrics["normalized"] = {"blocks": norm.get("block_count", 0), "heading": norm_counts.get("heading", 0), "body": norm_counts.get("body", 0), "table": norm_counts.get("table", 0)}
    diff = report.get("document_model_diff", {})
    metrics["diff"] = {"block_count_delta": diff.get("block_count_delta", 0)}
    parse_signals = {
        "suspect_visual_headings": report.get("suspect_visual_headings", 0),
        "ambiguous_short_paragraphs": report.get("ambiguous_short_paragraphs", 0),
        "inferred_headings": report.get("inferred_headings", 0),
        "inferred_lists": report.get("inferred_lists", 0),
        "unstyled_paragraphs": report.get("unstyled_paragraphs", 0),
    }
    suspicion_report = {"parse_report": parse_signals, "source_document_model_summary": src}
    metrics["suspicion_score"] = round(compute_suspicion_score(suspicion_report), 4)
    metrics["triggered_auto"] = metrics["suspicion_score"] >= 0.15
    enh = report.get("llm_enhancer", {})
    applied = enh.get("applied", [])
    skipped = enh.get("skipped", [])
    errors = enh.get("errors", [])
    phase_summaries = enh.get("phase_summaries", [])
    pa, pas, pb, pc = 0, 0, 0, 0
    for ps in phase_summaries:
        ph = ps.get("phase")
        ca = ps.get("applied_count", 0)
        cs = ps.get("skipped_count", 0)
        if ph == "A":
            pa = ca
            pas = cs
        elif ph == "B":
            pb = ca
        elif ph == "C":
            pc = ca
    rate = enh.get("phase_a_applied_rate", 0.0)
    reasons = Counter(d.get("reason", "") for d in applied if d.get("reason"))
    phase_metrics_list = enh.get("phase_metrics", [])

    metrics["llm_enhancement"] = {
        "phase_a_applied": pa, "phase_a_skipped": pas,
        "phase_b_applied": pb, "phase_c_applied": pc,
        "llm_errors": len(errors), "phase_a_applied_rate": rate,
        "llm_call_count": len(enh.get("prompts", [])),
        "top_reasons": dict(reasons.most_common(5)),
        "phase_metrics": phase_metrics_list,
    }
    return metrics

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_results = []
    print(f"Found {len(DOCS)} documents, modes: {MODES}")
    for doc_path, cat in DOCS:
        if not doc_path.exists():
            print(f"  MISSING: {doc_path}")
            continue
        doc_name = doc_path.stem
        isize = round(doc_path.stat().st_size / 1024, 1)
        for mode in MODES:
            out = OUTPUT_DIR / f"{doc_name}_{mode}.docx"
            rpt = OUTPUT_DIR / f"{doc_name}_{mode}.json"
            print(f"  [{doc_name}] mode={mode}", end=" ")
            sys.stdout.flush()
            import importlib, main as mm
            importlib.reload(mm)
            saved_argv = list(sys.argv)
            saved_cwd = Path.cwd()
            sys.argv = ["main.py", "--input", str(doc_path), "--output", str(out), "--template", str(TEMPLATE), "--report", str(rpt), "--llm-enhance", mode]
            os.chdir(str(HERE))
            start = time.perf_counter()
            try:
                mm.main()
            except SystemExit:
                pass
            wall = time.perf_counter() - start
            sys.argv = saved_argv
            os.chdir(str(saved_cwd))
            report = json.loads(rpt.read_text(encoding="utf-8")) if rpt.exists() else {}
            metrics = collect_metrics(report, wall, out)
            metrics.update({"document": doc_name, "category": cat, "mode": mode, "input_size_kb": isize})
            all_results.append(metrics)
            print(f"status={metrics.get('status','?')} wall={metrics.get('wall_time_sec','?'):.1f}s")
    output = {
        "benchmark_meta": {
            "skill_version": "0.8.5",
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "template": str(TEMPLATE), "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "modes_run": MODES,
        },
        "results": all_results,
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in all_results if r.get("status")=="ok")
    print(f"\nDone: {len(all_results)} total, {ok} ok, results at {RESULTS}")

if __name__ == "__main__":
    main()
