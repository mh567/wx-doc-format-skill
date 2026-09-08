from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from modular_benchmark import run_benchmark
from wxdf.metrics import percentile, summarize_samples


def test_percentile_and_sample_summary_are_deterministic() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)
    summary = summarize_samples([
        {"wall_time_seconds": 1.0, "peak_memory_bytes": 10, "metrics": {"counters": {"full_model_copy_count": 1}}},
        {"wall_time_seconds": 2.0, "peak_memory_bytes": 20, "metrics": {"counters": {"full_model_copy_count": 1}}},
    ])
    assert summary["sample_count"] == 2
    assert summary["counter_max"]["full_model_copy_count"] == 1


def test_fixed_benchmark_smoke_covers_all_sizes_and_policies() -> None:
    payload = run_benchmark(repetitions=2, warmups=1, sizes=("small",), enforce_minimum=False)

    assert payload["repetitions"] == 2
    assert set(payload["cases"]) == {"small"}
    assert payload["default_candidate"]["global_default_changed"] is False
    for case in payload["cases"].values():
        assert set(case) == {"legacy", "wx-default-v2"}
        assert all(case[policy]["sample_count"] == 2 for policy in case)
        assert all(case[policy]["wall_time_seconds"]["p95"] >= case[policy]["wall_time_seconds"]["p50"] for policy in case)
