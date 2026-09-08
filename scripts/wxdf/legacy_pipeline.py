"""Adapter around the stable legacy Markdown and DOCX conversion functions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


class LegacyPipeline:
    def __init__(
        self,
        *,
        markdown_converter: Callable[..., Any],
        docx_converter: Callable[..., Any],
    ) -> None:
        self._markdown_converter = markdown_converter
        self._docx_converter = docx_converter

    def convert(self, source: Path, *args: Any, **kwargs: Any) -> Any:
        suffix = Path(source).suffix.lower()
        if suffix in {".md", ".markdown"}:
            return self._markdown_converter(source, *args, **kwargs)
        if suffix == ".docx":
            return self._docx_converter(source, *args, **kwargs)
        raise ValueError(f"unsupported legacy input type: {suffix}")

    def convert_md(self, source: Path, *args: Any, **kwargs: Any) -> Any:
        return self._markdown_converter(source, *args, **kwargs)

    def convert_docx(self, source: Path, *args: Any, **kwargs: Any) -> Any:
        return self._docx_converter(source, *args, **kwargs)
