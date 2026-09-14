"""Conversion lifecycle data types for the native-v2 pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConversionRequest:
    input_path: Path
    output_path: Path
    template_path: Path | None = None
    pipeline: str = "modular"
    list_policy: str = "wx-default-v2"
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_path", Path(self.input_path))
        object.__setattr__(self, "output_path", Path(self.output_path))
        if self.template_path is not None:
            object.__setattr__(self, "template_path", Path(self.template_path))
        if self.pipeline != "modular":
            raise ValueError(f"unsupported pipeline {self.pipeline!r}")
        if self.list_policy != "wx-default-v2":
            raise ValueError(f"unsupported list policy {self.list_policy!r}")


@dataclass
class RunResult:
    status: str
    exit_code: int
    report: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    # Internal lifecycle control; never serialize into reports or public results.
    terminal_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "report": self.report,
            "artifacts": self.artifacts,
            "diagnostics": self.diagnostics,
            "metrics": self.metrics,
        }

    def as_report(self) -> dict[str, Any]:
        payload = dict(self.report)
        payload.update({
            "status": self.status,
            "exit_code": self.exit_code,
            "artifacts": dict(self.artifacts),
            "diagnostics": list(self.diagnostics),
            "metrics": dict(self.metrics),
        })
        return payload
