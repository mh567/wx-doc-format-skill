"""Small fixed-stage metrics collector with no document-content access."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator


@dataclass
class MetricsCollector:
    stage_seconds: dict[str, float] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    stage_order: list[str] = field(default_factory=list)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if name not in self.stage_seconds:
            self.stage_order.append(name)
        started = perf_counter()
        try:
            yield
        finally:
            self.stage_seconds[name] = self.stage_seconds.get(name, 0.0) + (perf_counter() - started)

    def increment(self, name: str, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("metric increments must be non-negative")
        self.counters[name] = self.counters.get(name, 0) + amount

    def record_source_opened(self) -> None:
        self.increment("source_open_count")

    def record_render(self) -> None:
        self.increment("render_count")

    def record_full_model_copy(self) -> None:
        self.increment("full_model_copy_count")

    def snapshot(self) -> dict:
        return {
            "stage_seconds": dict(self.stage_seconds),
            "counters": dict(self.counters),
            "stage_order": list(self.stage_order),
        }


def percentile(values: list[float], ratio: float) -> float:
    """Return a linear-interpolated percentile for a non-empty sample set."""

    if not values:
        raise ValueError("percentile requires at least one sample")
    if not 0.0 <= ratio <= 1.0:
        raise ValueError("percentile ratio must be between 0 and 1")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def summarize_samples(samples: list[dict]) -> dict:
    """Summarise benchmark samples while retaining stage and counter evidence."""

    wall = [float(sample["wall_time_seconds"]) for sample in samples]
    peak = [float(sample["peak_memory_bytes"]) for sample in samples]
    counters = [sample.get("metrics", {}).get("counters", {}) for sample in samples]
    counter_names = sorted({name for item in counters for name in item})
    return {
        "sample_count": len(samples),
        "wall_time_seconds": {"p50": percentile(wall, 0.50), "p95": percentile(wall, 0.95), "min": min(wall), "max": max(wall)},
        "peak_memory_bytes": {"p50": percentile(peak, 0.50), "p95": percentile(peak, 0.95), "max": max(peak)},
        "counter_p50": {name: percentile([float(item.get(name, 0)) for item in counters], 0.50) for name in counter_names},
        "counter_max": {name: max(int(item.get(name, 0)) for item in counters) for name in counter_names},
    }
