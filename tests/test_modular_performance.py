from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.kernel import ConversionKernel, ConversionRequest
from wxdf.metrics import percentile, summarize_samples


def test_percentile_and_sample_summary_are_deterministic() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)
    summary = summarize_samples([
        {"wall_time_seconds": 1.0, "peak_memory_bytes": 10, "metrics": {"counters": {"full_model_copy_count": 1}}},
        {"wall_time_seconds": 2.0, "peak_memory_bytes": 20, "metrics": {"counters": {"full_model_copy_count": 1}}},
    ])
    assert summary["sample_count"] == 2
    assert summary["counter_max"]["full_model_copy_count"] == 1


def test_native_conversion_exposes_stage_metrics_without_document_content(tmp_path: Path) -> None:
    source = tmp_path / "metrics.md"
    source.write_text("# Metrics fixture\n\nPrivate fixture content.\n", encoding="utf-8")
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source,
        output_path=tmp_path / "metrics.docx",
        template_path=ROOT / "assets" / "wx_template.docx",
    ))

    assert result.status == "completed", result.diagnostics
    assert result.metrics["stage_order"]
    assert all(value >= 0 for value in result.metrics["stage_seconds"].values())
    assert result.metrics["counters"]["source_open_count"] == 1
    assert result.metrics["counters"]["render_count"] == 1
    assert "Private fixture content" not in str(result.metrics)
