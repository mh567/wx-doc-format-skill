"""Compatibility wrapper — delegates to llm_enhancer.

Retained for backward compatibility.  All public functions are now
implemented in ``llm_enhancer.py``; this module re-exports them so
existing callers (e.g. ``main.py``) continue to work unchanged.

When ``llm_enhancer`` is unavailable (e.g. in environments without the
new module), the original implementations are used as a fallback.
"""

from __future__ import annotations

from typing import Any

# Try the new module first, fall back to the legacy implementations.
try:
    from llm_enhancer import build_role_overrides_from_docx as _new_build_role_overrides
except ImportError:
    _new_build_role_overrides = None


# ── Public re-exports ──────────────────────────────────────────────────

if _new_build_role_overrides is not None:
    build_role_overrides_from_docx = _new_build_role_overrides
else:
    # Legacy implementation (kept in-line; identical to the old code).
    import json

    def build_role_overrides_from_docx(src_doc, strict_normalize: bool, *, llm_call=None) -> dict[int, str]:
        """Stage 2 enhancement for DOCX source — legacy fallback."""
        if llm_call is None:
            return {}

        from docx_pipeline import infer_docx_role
        from text_utils import paragraph_has_graphics

        from docx.oxml.ns import qn as _qn
        from docx.text.paragraph import Paragraph
        from docx.table import Table as _T

        sections = []
        current_sec = {"heading_text": "", "blocks": []}
        had_heading = False
        para_index = 0

        for child in src_doc.element.body.iterchildren():
            tag = child.tag
            if tag.endswith("}w:sectPr"):
                continue
            if tag.endswith("}w:tbl"):
                current_sec["blocks"].append(("", "table", child, para_index))
                para_index += 1
                continue
            if tag.endswith("}w:p"):
                para = Paragraph(child, src_doc)
                text = para.text.strip()
                has_gfx = paragraph_has_graphics(para)
                if has_gfx and not text:
                    current_sec["blocks"].append(("", "image", para, para_index))
                    para_index += 1
                    continue
                if not text and not has_gfx:
                    continue

                inferred_text, style, role = infer_docx_role(para, strict_normalize, {})
                if has_gfx:
                    current_sec["blocks"].append((inferred_text, "image", para, para_index))
                    para_index += 1
                    continue
                if role == "heading":
                    if had_heading and current_sec["blocks"]:
                        sections.append(current_sec)
                    current_sec = {"heading_text": inferred_text, "blocks": []}
                    had_heading = True
                    continue
                current_sec["blocks"].append((inferred_text, role, para, para_index))
                para_index += 1

        if had_heading:
            sections.append(current_sec)

        overrides: dict[int, str] = {}
        for sec in sections:
            paras = [{"text": t, "role": r} for t, r, _, _ in sec["blocks"] if r not in ("table", "image")]
            if len(paras) <= 1:
                continue
            lines = [
                f"章节标题: \"{sec['heading_text']}\"",
                "请重新判断每个段落的语义角色，特别关注：",
                "1. 短文本(<=25字)的 body 段落是否实际属于列表项",
                "2. 列表项编号是否连续，缺失编号的 body 段落是否需要补充为列表",
                "",
                "段落列表:",
            ]
            for idx, p in enumerate(paras):
                lines.append(f"  [{idx}] role={p['role']} len={len(p.get('text','') or '')} | {(p.get('text') or '')[:200]}")
            lines.extend([
                "",
                "返回 JSON 数组，每个元素包含:",
                "  {\"index\": 序号, \"role\": \"heading|body|list\", \"list_marker\": null}",
                "规则: - 不改已有的 heading - 短文本(<=25字)如果描述功能点/模块，应归为 list",
                "  - 标记为 list 的 body 段落 list_marker 为 null（继承列表编号）",
                "  - 不新增或删除段落，只修改 role",
                "",
                "只返回 JSON 数组，不要额外文字。",
            ])
            try:
                raw = llm_call("\n".join(lines))
            except Exception:
                continue
            if not raw:
                continue
            start = raw.find("[")
            end = raw.rfind("]")
            if start < 0 or end <= start:
                continue
            try:
                corrections = json.loads(raw[start:end + 1])
            except Exception:
                continue
            if not isinstance(corrections, list):
                continue
            for corr in corrections:
                idx = corr.get("index")
                new_role = corr.get("role")
                if idx is None or new_role is None:
                    continue
                if 0 <= idx < len(paras):
                    old_role = paras[idx].get("role", "body")
                    if old_role in ("body", "list") and new_role in ("body", "list"):
                        overrides[idx] = new_role

        return overrides


# Legacy functions retained for old callers (if any).

def enhance_with_llm(heading_text, heading_level, paragraphs, *, llm_call=None):
    """Legacy — delegates to build_role_overrides_from_docx if needed.

    This function was the old per-section API.  It is preserved for
    backward compatibility but no longer called by main.py.  The new
    ``llm_enhancer.enhance_document_model`` should be used instead.
    """
    return paragraphs
