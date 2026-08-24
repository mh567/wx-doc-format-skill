"""Markdown Source Document recognition at the DocumentModel seam."""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from appendix_semantics import parse_appendix_title
from document_model import (
    append_block, appendix_block, body_block, caption_block, heading_block,
    image_block, list_item_block, new_document_model, separator_block,
    source_record, table_block,
)
from list_group_detection import annotate_semantic_list_groups
from text_utils import (
    caption_parts, clean_note_prefix, heading_level_from_text,
    is_appendix_title, is_formula_text, strip_heading_marker,
)
from unordered_lists import annotate_unordered_candidates


@dataclass(frozen=True)
class MarkdownSource:
    path: Path
    text: str

    @classmethod
    def from_input(cls, src: Any) -> "MarkdownSource":
        if isinstance(src, Path):
            return cls(src, src.read_text(encoding="utf-8"))
        if hasattr(src, "read"):
            text = src.read()
            if isinstance(text, bytes):
                text = text.decode("utf-8")
            return cls(Path("inline.md"), str(text))
        if isinstance(src, str):
            candidate = Path(src)
            if "\n" not in src:
                try:
                    if candidate.exists():
                        return cls(candidate, candidate.read_text(encoding="utf-8"))
                except OSError:
                    pass
            return cls(Path("inline.md"), src)
        return cls(Path("inline.md"), str(src))


@dataclass(frozen=True)
class RecognitionDiagnostic:
    code: str
    severity: str = "warning"
    line_start: int | None = None
    token_type: str | None = None
    details: dict[str, Any] | None = None


class RecognitionContractError(RuntimeError):
    """Raised when the recognized source AST violates a required contract."""


@dataclass(frozen=True)
class RecognitionResult:
    document_model: dict
    source_audit: dict
    parse_observations: dict
    diagnostics: tuple[RecognitionDiagnostic, ...]
    title_decision: dict
    preservation_contract: dict
    source_lists: dict
    tables_processed: int


def _markdown_parser():
    try:
        from markdown_it import MarkdownIt
    except ImportError as error:
        raise RuntimeError(
            "Markdown input requires markdown-it-py. Install the project dependencies first."
        ) from error
    return MarkdownIt("commonmark", {"html": False}).enable("table")


def _source_text(lines: list[str], token: Any) -> str:
    if token.map is None:
        return ""
    start, end = token.map
    return "\n".join(lines[start:end]).strip()


def _asset_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _local_image_path(source_path: Path, target: str) -> Path | None:
    parsed = urlsplit(target)
    if parsed.scheme and parsed.scheme.casefold() != "file":
        return None
    if parsed.scheme.casefold() == "file" and parsed.netloc not in {"", "localhost"}:
        return None
    path = Path(unquote(parsed.path))
    if not path.is_absolute():
        path = source_path.parent / path
    return path.resolve()


def _source_audit() -> dict[str, Any]:
    return {
        "adapter": "markdown-it-py", "title_count": 0, "heading_count": 0,
        "list_items": 0, "table_count": 0, "table_rows": 0,
        "table_header_rows": 0, "fence_blocks": 0, "blockquotes": 0,
        "inline_code": 0, "task_list_items": 0, "token_inventory": {},
        "unsupported_tokens": [],
    }


def canonical_semantic_sequence(model: dict | None) -> list[tuple[Any, ...]]:
    """Project Markdown-origin semantic blocks into the shared audit sequence."""
    blocks = (model or {}).get("document", {}).get("blocks", [])
    sequence: list[tuple[Any, ...]] = []
    for block in blocks:
        if block.get("source", {}).get("semantic_origin") != "markdown_token":
            continue
        block_type = block.get("block_type")
        if block_type == "table":
            rows = [
                [(cell.get("text", ""), cell.get("cell_role", ""), cell.get("inline_runs")) for cell in row]
                for row in block.get("rows", [])
            ]
            sequence.append(("table", block.get("role"), block.get("table_type"), block.get("header_rows"), rows))
        elif block_type == "list_item":
            numbering = block.get("source", {}).get("numbering", {})
            family = numbering.get("family") or ("unordered" if block.get("list_type") in {"dash", "bullet_dot"} else "ordered")
            sequence.append((
                "list_item", block.get("role"), block.get("text"), block.get("level"), family,
                block.get("restart"), block.get("parent_list_item_id"),
                block.get("source", {}).get("task_state"), block.get("source", {}).get("container_quote_depth"),
                block.get("inline_runs"),
            ))
        elif block_type in {"heading", "body"}:
            sequence.append((
                block_type, block.get("role"), block.get("level"), block.get("text"),
                block.get("source", {}).get("container_quote_depth"), block.get("inline_runs"),
            ))
        elif block_type == "image":
            sequence.append((
                "image", block.get("role"), block.get("asset_id"), block.get("alt_text"),
                block.get("source", {}).get("asset_path"), block.get("source", {}).get("asset_sha256"),
            ))
        elif block_type == "caption":
            sequence.append(("caption", block.get("role"), block.get("caption_type"), block.get("label"), block.get("raw_number"), block.get("text")))
        elif block_type == "appendix":
            sequence.append((
                "appendix", block.get("role"), block.get("appendix_id"), block.get("classification"),
                tuple(block.get("title_lines") or []), block.get("title"),
            ))
        elif block_type == "separator":
            sequence.append(("separator", block.get("role")))
    return copy.deepcopy(sequence)


class RecognitionBuilder:
    """Build the ordered source AST from a fixed Markdown token stream."""

    def __init__(self, source: MarkdownSource, *, template_version: str) -> None:
        self.source = source
        self.parser = _markdown_parser()
        self.tokens = self.parser.parse(source.text)
        self.lines = source.text.splitlines()
        self.model = new_document_model(source.path, "markdown", template_version)
        self.source_audit = _source_audit()
        self.parse_report: dict[str, Any] = {
            "ambiguous_short_paragraphs": [], "inferred_headings": [],
            "inferred_lists": [], "unstyled_paragraphs": 0,
        }
        self.block_index = 1
        self.title_seen = False
        self.semantic_content_seen = False
        self.heading_level: int | None = None
        self.quote_depth = 0
        self.list_stack: list[dict[str, Any]] = []
        self.list_item_stack: list[dict[str, Any]] = []
        self.table: dict[str, Any] | None = None
        self.appendix_count = 0
        self.current_row: list[dict[str, Any]] | None = None
        self.current_cell_role: str | None = None
        self.tables_processed = 0
        self.title_decision: dict[str, Any] = {}
        self._diagnostic_keys: set[tuple[str, int | None, str | None, str]] = set()
        self.dispatch = {
            "heading_open": self._on_heading_open,
            "heading_close": self._on_heading_close,
            "blockquote_open": self._on_blockquote_open,
            "blockquote_close": self._on_blockquote_close,
            "ordered_list_open": self._on_list_open,
            "bullet_list_open": self._on_list_open,
            "ordered_list_close": self._on_list_close,
            "bullet_list_close": self._on_list_close,
            "list_item_open": self._on_list_item_open,
            "list_item_close": self._on_list_item_close,
            "table_open": self._on_table_open,
            "table_close": self._on_table_close,
            "thead_open": self._on_thead_open,
            "thead_close": self._on_thead_close,
            "tr_open": self._on_row_open,
            "tr_close": self._on_row_close,
            "th_open": self._on_header_cell_open,
            "td_open": self._on_body_cell_open,
            "th_close": self._on_cell_close,
            "td_close": self._on_cell_close,
            "fence": self._on_fence,
            "code_block": self._on_code_block,
            "inline": self._on_inline,
            "hr": self._on_separator,
            "html_block": self._on_html_block,
            "paragraph_open": self._on_noop,
            "paragraph_close": self._on_noop,
            "tbody_open": self._on_noop,
            "tbody_close": self._on_noop,
        }

    def build(self) -> RecognitionResult:
        for token in self.tokens:
            token_type = token.type
            inventory = self.source_audit["token_inventory"]
            inventory[token_type] = inventory.get(token_type, 0) + 1
            self.dispatch.get(token_type, self._on_unhandled)(token)
        self._ensure_title()
        annotate_unordered_candidates(self.model, self.parse_report)
        annotate_semantic_list_groups(self.model, self.parse_report)
        self._validate_recognition_contract()
        source_lists = {
            "detected": self.source_audit["list_items"], "ambiguous": 0,
            "ignored": 0, "source": "markdown-it-py",
        }
        diagnostics = self._typed_diagnostics()
        contract = {
            "schema_version": "1.0",
            "dialect": "commonmark+table",
            "source_sha256": hashlib.sha256(self.source.text.encode("utf-8")).hexdigest(),
            "token_inventory": dict(self.source_audit["token_inventory"]),
            "semantic_block_count": len(self.model["document"]["blocks"]),
            "semantic_sequence": canonical_semantic_sequence(self.model),
            "diagnostic_codes": [item.code for item in diagnostics],
        }
        return RecognitionResult(
            document_model=self.model, source_audit=self.source_audit,
            parse_observations=self.parse_report, diagnostics=diagnostics,
            title_decision=self.title_decision, preservation_contract=contract,
            source_lists=source_lists, tables_processed=self.tables_processed,
        )

    def _next_id(self) -> str:
        value = f"b{self.block_index:04d}"
        self.block_index += 1
        return value

    @staticmethod
    def _line_number(token: Any) -> int | None:
        token_map = getattr(token, "map", None)
        return token_map[0] + 1 if token_map is not None else None

    def _record_diagnostic(
        self,
        code: str,
        token: Any | None = None,
        *,
        token_type: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        line_start = self._line_number(token) if token is not None else None
        resolved_type = token_type or (getattr(token, "type", None) if token is not None else None)
        detail_key = repr(sorted((details or {}).items()))
        key = (code, line_start, resolved_type, detail_key)
        if key in self._diagnostic_keys:
            return
        self._diagnostic_keys.add(key)
        legacy = {"type": code, "line_start": line_start}
        if resolved_type:
            legacy["token_type"] = resolved_type
        if details:
            legacy.update(details)
        self.source_audit["unsupported_tokens"].append(legacy)

    def _typed_diagnostics(self) -> tuple[RecognitionDiagnostic, ...]:
        diagnostics = [
            RecognitionDiagnostic(
                code=str(item.get("type") or "unknown_block_token"),
                line_start=item.get("line_start"),
                token_type=item.get("token_type"),
                details={key: value for key, value in item.items() if key not in {"type", "line_start", "token_type"}} or None,
            )
            for item in self.source_audit["unsupported_tokens"]
        ]
        if self.title_decision.get("code") == "markdown_title_generated":
            diagnostics.append(RecognitionDiagnostic(
                code="markdown_title_generated",
                details={"title": self.title_decision["title"], "source": "filename"},
            ))
        return tuple(diagnostics)

    def _validate_recognition_contract(self) -> None:
        blocks = self.model["document"]["blocks"]
        titles = [block for block in blocks if block.get("role") == "title"]
        if len(titles) != 1:
            raise RecognitionContractError(f"expected one title, found {len(titles)}")
        block_ids = [block.get("id") for block in blocks]
        if any(not block_id for block_id in block_ids) or len(set(block_ids)) != len(block_ids):
            raise RecognitionContractError("block ids must be present and unique")
        earlier_list_items: dict[str, dict[str, Any]] = {}
        for block in blocks:
            inline_runs = block.get("inline_runs")
            if inline_runs is not None and "".join(str(run.get("text") or "") for run in inline_runs) != str(block.get("text") or ""):
                raise RecognitionContractError(f"inline text mismatch for {block['id']}")
            if block.get("block_type") == "table":
                for row in block.get("rows", []):
                    for cell in row:
                        inline_runs = cell.get("inline_runs")
                        if inline_runs is not None and "".join(str(run.get("text") or "") for run in inline_runs) != str(cell.get("text") or ""):
                            raise RecognitionContractError(f"table inline text mismatch for {block['id']}")
            if block.get("block_type") == "separator" and block.get("text"):
                raise RecognitionContractError(f"separator {block['id']} contains text")
            if block.get("block_type") == "list_item":
                parent_id = block.get("parent_list_item_id")
                if parent_id:
                    parent = earlier_list_items.get(parent_id)
                    if parent is None or int(parent.get("level", 0)) >= int(block.get("level", 0)):
                        raise RecognitionContractError(f"invalid list parent for {block['id']}")
                earlier_list_items[str(block["id"])] = block

    def _inline_runs(self, token: Any) -> tuple[str, list[dict[str, Any]]]:
        inline_tokens = token.children or self.parser.parseInline(token.content)[0].children or []
        runs: list[dict[str, Any]] = []
        marks: list[str] = []
        hrefs: list[str] = []
        for inline in inline_tokens:
            if inline.type in {"text", "html_inline"}:
                run = {"text": inline.content}
                if marks:
                    run["marks"] = list(marks)
                if hrefs:
                    run["href"] = hrefs[-1]
                runs.append(run)
            elif inline.type == "strong_open":
                marks.append("strong")
            elif inline.type == "strong_close" and marks:
                marks.pop()
            elif inline.type == "em_open":
                marks.append("emphasis")
            elif inline.type == "em_close" and marks:
                marks.pop()
            elif inline.type == "code_inline":
                run = {"text": inline.content, "marks": ["code"]}
                if hrefs:
                    run["href"] = hrefs[-1]
                runs.append(run)
            elif inline.type == "softbreak":
                runs.append({"text": " "})
            elif inline.type == "hardbreak":
                runs.append({"text": "\n"})
            elif inline.type == "link_open":
                hrefs.append(str(inline.attrGet("href") or ""))
            elif inline.type == "link_close" and hrefs:
                href = hrefs.pop()
                if href:
                    runs.append({"text": f" ({href})"})
            elif inline.type == "image":
                target = str(inline.attrGet("src") or "")
                runs.append({"text": f"图片：{inline.content}" + (f" ({target})" if target else "")})
            else:
                fallback = str(getattr(inline, "content", "") or "")
                self._record_diagnostic(
                    "unknown_inline_token", token,
                    token_type=str(getattr(inline, "type", "")) or None,
                )
                if fallback:
                    run = {"text": fallback}
                    if marks:
                        run["marks"] = list(marks)
                    if hrefs:
                        run["href"] = hrefs[-1]
                    runs.append(run)
        runs = [run for run in runs if run.get("text")]
        return "".join(run["text"] for run in runs).strip(), runs

    def _standalone_image(self, token: Any) -> tuple[str, str] | None:
        children = token.children or self.parser.parseInline(token.content)[0].children or []
        meaningful = [child for child in children if child.type not in {"softbreak", "hardbreak"}]
        if len(meaningful) != 1 or meaningful[0].type != "image":
            return None
        image = meaningful[0]
        return image.content.strip(), str(image.attrGet("src") or "").strip()

    def _body_or_semantic_block(self, block_id: str, text: str, *, source: dict[str, Any]) -> dict:
        role = "note" if text.startswith(("备注：", "编写提示：")) else "body"
        if text.startswith(("注1：", "注2：", "注3：", "注4：", "注5：")):
            role = "numbered_note"
        if is_formula_text(text):
            role = "formula"
        if is_appendix_title(text):
            return appendix_block(block_id, text, source=source_record(**source, role="appendix_title"))
        caption_type, label, raw_number, caption_text = caption_parts(text)
        if caption_type != "unknown":
            return caption_block(block_id, caption_text, caption_type, label=label, raw_number=raw_number, source=source)
        inferred_level = heading_level_from_text(text)
        if inferred_level is not None:
            return heading_block(block_id, strip_heading_marker(text), inferred_level, source=source_record(**source, inferred=True))
        return body_block(block_id, clean_note_prefix(text), role=role if role != "body" else None, source=source)

    def _append_text_block(self, token: Any, text: str, *, role: str | None = None) -> None:
        text = text.strip()
        if not text:
            return
        source = source_record(
            raw_text=_source_text(self.lines, token) or text,
            format="markdown_quote" if self.quote_depth else "markdown_paragraph",
            line_start=self._line_number(token), role=role, semantic_origin="markdown_token",
            container_quote_depth=self.quote_depth or None,
        )
        if role in {"quote", "code_block"}:
            append_block(self.model, body_block(self._next_id(), text, role=role, source=source))
            self.semantic_content_seen = True
            return
        block = self._body_or_semantic_block(self._next_id(), text, source=source)
        _, inline_runs = self._inline_runs(token)
        if (
            inline_runs
            and "".join(run["text"] for run in inline_runs) == block.get("text")
            and block.get("block_type") in {"body", "heading"}
        ):
            block["inline_runs"] = inline_runs
        if block.get("block_type") == "appendix":
            self.appendix_count += 1
            title_data = parse_appendix_title(text, self.appendix_count)
            block.update({
                "appendix_id": title_data["appendix_id"], "title": title_data["title"],
                "title_lines": title_data["title_lines"],
                "soft_break_count": max(0, len(title_data["title_lines"]) - 1),
            })
            if title_data.get("classification"):
                block["classification"] = title_data["classification"]
        if block.get("block_type") == "heading":
            self.source_audit["heading_count"] += 1
            self.parse_report["inferred_headings"].append({"text": text, "level": block["level"], "source": "md-text"})
        elif block.get("block_type") == "body":
            self.parse_report["unstyled_paragraphs"] += 1
            if len(text) <= 30 and text[-1:] not in ("。", "；", ";", "，", ","):
                self.parse_report["ambiguous_short_paragraphs"].append(text)
        append_block(self.model, block)
        self.semantic_content_seen = True

    def _append_list_item(self, item: dict[str, Any], text: str, inline_token: Any | None = None) -> None:
        if not text:
            return
        existing = item.get("block")
        if existing is not None:
            combined_text = f"{existing['text']} {text}".strip()
            existing_runs = existing.get("inline_runs")
            _, added_runs = self._inline_runs(inline_token) if inline_token is not None else ("", [])
            if existing_runs is not None:
                candidate_runs = [*existing_runs, {"text": " "}, *added_runs]
                if added_runs and "".join(run["text"] for run in candidate_runs) == combined_text:
                    existing["inline_runs"] = candidate_runs
                else:
                    existing.pop("inline_runs", None)
            existing["text"] = combined_text
            return
        list_info = item["list"]
        list_info["items"] += 1
        level = int(list_info["level"])
        ordered = bool(list_info["ordered"])
        source_type = "decimal_paren" if ordered else ("dash" if list_info["marker"] == "-" else "bullet_dot")
        task_match = re.match(r"^\[(?P<state>[ xX])\]\s*", text)
        task_state = task_match.group("state").casefold() if task_match else None
        if task_match:
            self.source_audit["task_list_items"] += 1
            self.source_audit["unsupported_tokens"].append({"type": "task_list_state", "line_start": self._line_number(item["token"])})
        block = list_item_block(
            self._next_id(), text, level, "decimal_paren" if ordered else source_type,
            restart=list_info["items"] == 1,
            source=source_record(
                raw_text=_source_text(self.lines, item["token"]) or text,
                format="markdown_list_item", line_start=self._line_number(item["token"]),
                semantic_origin="markdown_token", container_quote_depth=self.quote_depth or None,
                task_state=task_state,
                numbering={"status": "detected", "ilvl": level, "restart": list_info["items"] == 1,
                           "source_list_type": source_type, "list_type": "decimal_paren" if ordered else source_type,
                           "family": "ordered" if ordered else "unordered", "marker": list_info["marker"]},
            ),
        )
        _, inline_runs = self._inline_runs(inline_token) if inline_token is not None else ("", [])
        if "".join(run["text"] for run in inline_runs) == text:
            block["inline_runs"] = inline_runs
        append_block(self.model, block)
        self.semantic_content_seen = True
        if level > 0 and len(self.list_item_stack) > 1:
            parent = self.list_item_stack[-2].get("block")
            if parent is not None:
                block["parent_list_item_id"] = parent.get("id")
        item["block"] = block
        self.source_audit["list_items"] += 1
        self.parse_report["inferred_lists"].append({"text": text, "source": "markdown-token"})

    def _on_heading_open(self, token: Any) -> None:
        self.heading_level = int(token.tag[1:])

    def _on_heading_close(self, token: Any) -> None:
        self.heading_level = None

    def _on_blockquote_open(self, token: Any) -> None:
        self.quote_depth += 1
        self.source_audit["blockquotes"] += 1

    def _on_blockquote_close(self, token: Any) -> None:
        self.quote_depth = max(0, self.quote_depth - 1)

    def _on_list_open(self, token: Any) -> None:
        self.list_stack.append({"level": len(self.list_stack), "ordered": token.type == "ordered_list_open",
                                "marker": token.markup, "items": 0, "quote_depth": self.quote_depth})

    def _on_list_close(self, token: Any) -> None:
        if self.list_stack:
            self.list_stack.pop()

    def _on_list_item_open(self, token: Any) -> None:
        self.list_item_stack.append({"block": None, "list": self.list_stack[-1] if self.list_stack else None, "token": token})

    def _on_list_item_close(self, token: Any) -> None:
        if self.list_item_stack:
            self.list_item_stack.pop()

    def _on_table_open(self, token: Any) -> None:
        self.table = {"rows": [], "header_rows": 0, "token": token}

    def _on_table_close(self, token: Any) -> None:
        if self.table is not None:
            rows = self.table["rows"]
            header_rows = int(self.table["header_rows"])
            append_block(self.model, table_block(
                self._next_id(), "data", rows, header_rows=header_rows,
                source=source_record(format="markdown_table", raw_text=_source_text(self.lines, self.table["token"]),
                                     line_start=self._line_number(self.table["token"]), semantic_origin="markdown_token",
                                     container_quote_depth=self.quote_depth or None),
            ))
            self.tables_processed += 1
            self.source_audit["table_count"] += 1
            self.source_audit["table_rows"] += len(rows)
            self.source_audit["table_header_rows"] += header_rows
            self.semantic_content_seen = True
        self.table = None

    def _on_thead_open(self, token: Any) -> None:
        if self.table is not None:
            self.table["in_header"] = True

    def _on_thead_close(self, token: Any) -> None:
        if self.table is not None:
            self.table["in_header"] = False

    def _on_row_open(self, token: Any) -> None:
        if self.table is not None:
            self.current_row = []

    def _on_row_close(self, token: Any) -> None:
        if self.table is not None and self.current_row is not None:
            self.table["rows"].append(self.current_row)
            if self.table.get("in_header"):
                self.table["header_rows"] += 1
            self.current_row = None

    def _on_header_cell_open(self, token: Any) -> None:
        if self.table is not None:
            self.current_cell_role = "header"

    def _on_body_cell_open(self, token: Any) -> None:
        if self.table is not None:
            self.current_cell_role = "body"

    def _on_cell_close(self, token: Any) -> None:
        self.current_cell_role = None

    def _on_fence(self, token: Any) -> None:
        self._append_code_block(token, "markdown_fence", token.info.strip() or None)

    def _on_code_block(self, token: Any) -> None:
        self._append_code_block(token, "markdown_indented_code", None)

    def _append_code_block(self, token: Any, source_format: str, language: str | None) -> None:
        append_block(self.model, body_block(
            self._next_id(), token.content, role="code_block",
            source=source_record(raw_text=_source_text(self.lines, token), format=source_format, language=language,
                                 line_start=self._line_number(token), semantic_origin="markdown_token",
                                 container_quote_depth=self.quote_depth or None),
        ))
        self.source_audit["fence_blocks"] += 1
        self.semantic_content_seen = True

    def _on_inline(self, token: Any) -> None:
        if self.table is not None and self.current_row is not None and self.current_cell_role is not None:
            cell_text, inline_runs = self._inline_runs(token)
            self.current_row.append({"text": cell_text, "cell_role": self.current_cell_role, "inline_runs": inline_runs})
            self.source_audit["inline_code"] += sum(child.type == "code_inline" for child in token.children or [])
            return
        text, inline_runs = self._inline_runs(token)
        self.source_audit["inline_code"] += sum(child.type == "code_inline" for child in token.children or [])
        if any(child.type == "text" and re.search(r"<[^>]+>", child.content) for child in token.children or []):
            text = re.sub(r"<[^>]+>", "", text).strip()
            self.source_audit["unsupported_tokens"].append({"type": "html_inline", "line_start": self._line_number(token)})
        if self.heading_level is not None:
            self._append_heading(token, text, inline_runs)
        elif self.list_item_stack and self.list_item_stack[-1].get("list") is not None:
            item = self.list_item_stack[-1]
            if self.quote_depth and not item["list"].get("quote_depth"):
                self._append_text_block(token, text, role="quote")
            else:
                self._append_list_item(item, text, token)
        else:
            self._append_inline_content(token, text)

    def _append_heading(self, token: Any, text: str, inline_runs: list[dict[str, Any]]) -> None:
        if self.list_item_stack and self.list_item_stack[-1].get("list") is not None:
            self._append_list_item(self.list_item_stack[-1], text, token)
            self.source_audit["unsupported_tokens"].append({"type": "list_item_heading", "line_start": self._line_number(token)})
            return
        if self.quote_depth:
            self._append_text_block(token, text, role="quote")
            self.source_audit["unsupported_tokens"].append({"type": "quoted_heading", "line_start": self._line_number(token)})
            return
        source = source_record(raw_text=_source_text(self.lines, token) or text, format="markdown_heading",
                               md_level=self.heading_level, line_start=self._line_number(token), semantic_origin="markdown_token")
        if self.heading_level == 1 and not self.title_seen and not self.semantic_content_seen:
            block = heading_block(self._next_id(), strip_heading_marker(text), 0, role="title", source=source)
            self.title_seen = True
            self.source_audit["title_count"] += 1
        else:
            block = heading_block(self._next_id(), strip_heading_marker(text), max(1, min(self.heading_level - 1, 5)), source=source)
            self.source_audit["heading_count"] += 1
        if "".join(run["text"] for run in inline_runs) == block["text"]:
            block["inline_runs"] = inline_runs
        append_block(self.model, block)
        self.semantic_content_seen = True

    def _append_inline_content(self, token: Any, text: str) -> None:
        standalone_image = self._standalone_image(token)
        if standalone_image is None:
            for child in token.children or []:
                target = str(child.attrGet("src") or "")
                if child.type == "image" and re.match(r"^[a-z][a-z0-9+.-]*://", target, re.IGNORECASE):
                    self.source_audit["unsupported_tokens"].append({"type": "mixed_remote_image", "line_start": self._line_number(token), "target": target})
            self._append_text_block(token, text, role="quote" if self.quote_depth else None)
            return
        alt_text, target = standalone_image
        asset_path = _local_image_path(self.source.path, target)
        if asset_path is None:
            self._append_text_block(token, f"图片：{alt_text} ({target})")
            self.source_audit["unsupported_tokens"].append({"type": "remote_image", "line_start": self._line_number(token), "target": target})
            return
        asset_sha256 = _asset_sha256(asset_path)
        if asset_sha256 is None:
            self.source_audit["unsupported_tokens"].append({"type": "missing_local_image", "line_start": self._line_number(token), "target": target})
        append_block(self.model, image_block(
            self._next_id(), alt_text=alt_text,
            source=source_record(raw_text=_source_text(self.lines, token), format="markdown_image", asset_path=str(asset_path),
                                 asset_sha256=asset_sha256, line_start=self._line_number(token), semantic_origin="markdown_token"),
        ))
        self.semantic_content_seen = True

    def _on_separator(self, token: Any) -> None:
        append_block(self.model, separator_block(
            self._next_id(), source=source_record(format="markdown_hr", line_start=self._line_number(token), semantic_origin="markdown_token"),
        ))
        self.semantic_content_seen = True

    def _on_html_block(self, token: Any) -> None:
        readable = re.sub(r"<[^>]+>", "", token.content).strip()
        if readable:
            self._append_text_block(token, readable)
        self.source_audit["unsupported_tokens"].append({"type": "html_block", "line_start": self._line_number(token)})

    def _on_noop(self, token: Any) -> None:
        return None

    def _on_unhandled(self, token: Any) -> None:
        self._record_diagnostic("unknown_block_token", token, token_type=str(getattr(token, "type", "")) or None)
        text = str(getattr(token, "content", "") or "").strip()
        if text:
            self._append_text_block(token, text)

    def _ensure_title(self) -> None:
        if self.title_seen:
            self.title_decision = {"source": "leading_h1"}
            return
        title = self.source.path.stem or "文档"
        self.model["document"]["blocks"].insert(0, heading_block(
            "b0000", title, 0, role="title",
            source=source_record(format="generated_markdown_title", semantic_origin="markdown_token"),
        ))
        self.source_audit["title_count"] += 1
        self.title_decision = {"code": "markdown_title_generated", "source": "filename", "leading_h1_absent": True, "title": title}


def recognize_markdown(source: MarkdownSource, *, template_version: str) -> RecognitionResult:
    """Recognize one Source Document without DOCX, rendering, or report mutation."""
    return RecognitionBuilder(source, template_version=template_version).build()
