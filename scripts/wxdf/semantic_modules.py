"""Native source interpretation modules and semantic candidate builders."""
from __future__ import annotations

from collections.abc import MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from .contracts import validate_capability_payload
from .parsers import digest_source_facts, parse, summarize_source_facts
from .registry import ModuleDescriptor, PlanError, RegisteredModule
from .semantics.appendices import collect_native_appendix_candidates, collect_source_appendix_semantics, prepare_native_appendix_semantics, recognize_appendix_semantics
from .semantics.captions import collect_caption_candidates, collect_source_caption_semantics, recognize_caption_semantics
from .semantics.lists import analyze_lists, recognize_list_semantics
from .semantics.paragraphs import collect_paragraph_candidates
from .semantics.protected import collect_protected_candidates
from .semantics.regions import resolve_source_regions
from .semantics.source_ast import materialize_list_semantics
from .semantics.structure import collect_structure_candidates
from .semantics.tables import collect_source_table_semantics, collect_table_candidates, recognize_table_semantics

class _ObservedMapping(MutableMapping[str, Any]):
    def __init__(self, values: Mapping[str, Any], observe: Callable[[str], None]) -> None: self.values, self.observe = dict(values), observe
    def __getitem__(self, key: str) -> Any: self.observe(str(key)); return self.values[key]
    def __setitem__(self, key: str, value: Any) -> None: self.observe(str(key)); self.values[key] = value
    def __delitem__(self, key: str) -> None: self.observe(str(key)); del self.values[key]
    def __iter__(self) -> Iterator[str]: return iter(self.values)
    def __len__(self) -> int: return len(self.values)
    def get(self, key: str, default: Any = None) -> Any: self.observe(str(key)); return self.values.get(key, default)
    def copy_values(self) -> dict[str, Any]: return dict(self.values)

class _RequestProxy:
    def __init__(self, request: Any, options: MutableMapping[str, Any]) -> None: self.request, self.options = request, options
    def __getattr__(self, name: str) -> Any: return getattr(self.request, name)

@dataclass
class ModuleExecutionContext:
    request: Any
    artifacts: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    _active: dict[str, Any] | None = field(default=None, repr=False)
    _reads: list[str] = field(default_factory=list, repr=False)
    _writes: list[str] = field(default_factory=list, repr=False)
    _service_reads: list[str] = field(default_factory=list, repr=False)
    _option_reads: list[str] = field(default_factory=list, repr=False)
    def __post_init__(self) -> None:
        raw = self.request.options.copy_values() if isinstance(self.request, _RequestProxy) and isinstance(self.request.options, _ObservedMapping) else getattr(self.request, "options", {}) or {}
        self.request = _RequestProxy(self.request, _ObservedMapping(raw, self._option_reads.append))
        self.services = _ObservedMapping(self.services, self._service_reads.append)  # type: ignore[assignment]
    def begin_module(self, descriptor: ModuleDescriptor, position: int) -> None: self._active = {"module_id": descriptor.module_id, "position": position}; self.reset_observation()
    def reset_observation(self) -> None: self._reads.clear(); self._writes.clear(); self._service_reads.clear(); self._option_reads.clear()
    def end_module(self, status: str, error: BaseException | None = None) -> dict[str, Any]:
        active = self._active or {}; module_id = str(active.get("module_id") or "")
        record = {**self.evidence.get(module_id, {}), "order": int(active.get("position") or 0), "produced": list(self._writes), "consumed": list(self._reads), "status": status}
        if self._service_reads: record["services"] = list(self._service_reads)
        if self._option_reads: record["options"] = list(self._option_reads)
        if error is not None: record["error"] = {"type": type(error).__name__, "message": str(error)}
        self.evidence[module_id] = record; self._active = None; self.reset_observation(); return record
    def provide(self, capability: str, value: Any) -> None:
        if capability in self.artifacts: raise PlanError(f"runtime capability {capability} already provided")
        try: validate_capability_payload(capability, value)
        except Exception as exc: raise PlanError(f"invalid capability payload for {capability}: {exc}") from exc
        self.artifacts[capability] = value
        if self._active is not None and capability not in self._writes: self._writes.append(capability)
    def require(self, capability: str) -> Any:
        if capability not in self.artifacts: raise PlanError(f"runtime capability {capability} is unavailable")
        try: validate_capability_payload(capability, self.artifacts[capability])
        except Exception as exc: raise PlanError(f"invalid capability payload for {capability}: {exc}") from exc
        if self._active is not None and capability not in self._reads: self._reads.append(capability)
        return self.artifacts[capability]
    def stage(self, name: str) -> Any: return _metrics_stage(self, name)

@contextmanager
def _metrics_stage(context: ModuleExecutionContext, name: str) -> Iterator[None]:
    metrics = context.services.get("metrics")
    if metrics is None: yield; return
    with metrics.stage(name): yield

def _registered(module_id: str, stages: tuple[str, ...], requires: tuple[str, ...], provides: tuple[str, ...], execute: Callable[[ModuleExecutionContext], Any], **kwargs: Any) -> RegisteredModule:
    return RegisteredModule(ModuleDescriptor(module_id, "1.0", stages, requires, provides, **kwargs), execute)

def source_facts_module() -> RegisteredModule:
    def execute(c: ModuleExecutionContext) -> dict[str, Any]:
        with c.stage("source"):
            restored = c.request.options.get("restored_source_facts")
            parse_source = compatibility_hook("parse", parse)
            facts = json.loads(json.dumps(restored, ensure_ascii=False)) if isinstance(restored, Mapping) else parse_source(c.request.input_path, {"source_store": c.services.get("store")})
            metrics = c.services.get("metrics"); metrics.record_source_opened() if metrics is not None else None
            summary, digest = summarize_source_facts(dict(facts)), digest_source_facts(dict(facts))
        c.provide("source_facts", facts); c.evidence["source-facts-parser"] = summary
        c.provide("source_facts_summary", summary); c.provide("source_facts_digest", digest)
        return summary
    return _registered("source-facts-parser", ("source",), (), ("source_facts", "source_facts_summary", "source_facts_digest"), execute, runtime_services=("metrics", "store"), option_keys=("restored_source_facts",))

def source_region_module() -> RegisteredModule:
    def execute(c: ModuleExecutionContext) -> dict[str, Any]:
        result = resolve_source_regions(c.request.input_path, c.require("source_facts")); c.provide("source_regions", result["regions"]); c.provide("source_region_candidates", result["candidates"]); c.provide("source_region", result); c.evidence["source-region"] = result["evidence"]; return result["evidence"]
    return _registered("source-region", ("scope",), ("source_facts",), ("source_regions", "source_region_candidates", "source_region"), execute)

def document_structure_module() -> RegisteredModule:
    def execute(c: ModuleExecutionContext) -> dict[str, Any]:
        items = collect_structure_candidates(c.require("source_facts"), c.require("source_regions"), protect_semantics=True); evidence = {"candidate_count": len(items), "title_candidate_count": sum(x.get("proposal", {}).get("value", {}).get("role") == "title" for x in items), "heading_candidate_count": sum(x.get("proposal", {}).get("value", {}).get("role") == "heading" for x in items), "body_candidate_count": sum(x.get("proposal", {}).get("value", {}).get("role") == "body" for x in items)}; c.provide("document_structure_candidates", items); c.provide("document_structure", evidence); c.evidence["document-structure"] = evidence; return evidence
    return _registered("document-structure", ("role",), ("source_facts", "source_regions"), ("document_structure_candidates", "document_structure"), execute)

def protected_content_module() -> RegisteredModule:
    def execute(c: ModuleExecutionContext) -> dict[str, Any]:
        items = collect_protected_candidates(c.require("source_facts")); evidence = {"candidate_count": len(items), "note_count": sum(x.get("proposal", {}).get("value", {}).get("reason") == "note" for x in items), "opaque_count": sum(str(x.get("proposal", {}).get("value", {}).get("reason") or "").startswith("opaque_") for x in items)}; c.provide("protected_content_candidates", items); c.provide("protected_content", evidence); c.evidence["protected-content"] = evidence; return evidence
    return _registered("protected-content", ("role",), ("source_facts",), ("protected_content_candidates", "protected_content"), execute)

def list_semantics_module() -> RegisteredModule:
    def execute(c: ModuleExecutionContext) -> dict[str, Any]:
        recognition = recognize_list_semantics(c.require("source_facts"), c.require("source_ast")); result, candidates, item_map = recognition["result"], recognition["candidates"], recognition["item_map"]
        evidence = {"status": result["status"], "list_count": len(result["lists"]), "item_count": len(result["items"]), "continuation_count": len(result["continuations"]), "diagnostic_count": len(result["diagnostics"])}; c.provide("list_semantics_result", result)
        c.provide("list_semantics_candidates", candidates); c.provide("list_item_map", item_map)
        c.provide("list_semantics", evidence); c.evidence["list-semantics"] = evidence; return evidence
    return _registered("list-semantics", ("relation",), ("source_facts", "source_ast"), ("list_semantics_result", "list_semantics_candidates", "list_item_map", "list_semantics"), execute)

def table_semantics_module() -> RegisteredModule:
    def execute(c: ModuleExecutionContext) -> dict[str, Any]:
        recognition = recognize_table_semantics(c.require("source_facts"), c.require("source_ast")); result, candidates = recognition["result"], recognition["candidates"]
        c.provide("table_semantics_result", result); c.provide("table_semantics_candidates", candidates); evidence = {"candidate_count": len(candidates), "source_count": len(result)}
        c.provide("table_semantics", evidence); c.evidence["table-semantics"] = evidence; return evidence
    return _registered("table-semantics", ("relation",), ("source_facts", "source_ast"), ("table_semantics_result", "table_semantics_candidates", "table_semantics"), execute)

def caption_semantics_module() -> RegisteredModule:
    def execute(c: ModuleExecutionContext) -> dict[str, Any]:
        recognition = recognize_caption_semantics(c.require("source_facts"), c.require("source_ast"), c.require("table_semantics_result")); result, candidates = recognition["result"], recognition["candidates"]; captions = result["captions"]
        c.provide("caption_semantics_result", result); c.provide("caption_semantics_candidates", candidates)
        generated = sum(1 for x in candidates if isinstance(x.get("proposal"), Mapping) and x["proposal"].get("operation") == "create_node" and isinstance(x["proposal"].get("value"), Mapping) and isinstance(x["proposal"]["value"].get("node"), Mapping) and x["proposal"]["value"]["node"].get("role") == "caption")
        evidence = {"caption_count": len(captions), "table_caption_count": sum(x.get("caption_type") == "table" for x in captions), "figure_caption_count": sum(x.get("caption_type") == "figure" for x in captions), "associated_count": sum(x.get("status") == "associated" for x in captions), "issue_count": len(result["issues"]), "generated_caption_count": generated}; c.provide("caption_semantics", evidence); c.evidence["caption-semantics"] = evidence; return evidence
    return _registered("caption-semantics", ("relation",), ("source_facts", "source_ast", "table_semantics_result"), ("caption_semantics_result", "caption_semantics_candidates", "caption_semantics"), execute)

def appendix_semantics_module() -> RegisteredModule:
    def execute(c: ModuleExecutionContext) -> dict[str, Any]:
        recognition = recognize_appendix_semantics(c.require("source_facts"), c.require("source_ast"), c.require("list_item_map")); result, candidates = recognition["result"], recognition["candidates"]
        evidence = {"candidate_count": len(result["candidates"]), "accepted_count": sum(bool(x.get("accepted")) for x in result["candidates"]), "rejected_count": sum(not bool(x.get("accepted")) for x in result["candidates"]), "range_count": len(result["ranges"]), "heading_count": sum(len(x.get("heading_decisions") or []) for x in result["ranges"]), "issue_count": len(result["issues"])}; c.provide("appendix_semantics_candidates", candidates); c.provide("appendix_semantics_result", result); c.provide("appendix_semantics", result); c.evidence["appendix-semantics"] = evidence; return evidence
    return _registered("appendix-semantics", ("relation",), ("source_facts", "source_ast", "list_item_map"), ("appendix_semantics_result", "appendix_semantics_candidates", "appendix_semantics"), execute)

def native_semantic_modules() -> tuple[RegisteredModule, ...]: return (source_facts_module(), source_region_module(), document_structure_module(), protected_content_module(), list_semantics_module(), table_semantics_module(), caption_semantics_module(), appendix_semantics_module())
def collect_native_candidates(*, source_region_candidates: Iterable[Mapping[str, Any]], document_structure_candidates: Iterable[Mapping[str, Any]], protected_content_candidates: Iterable[Mapping[str, Any]], list_semantics_candidates: Iterable[Mapping[str, Any]], appendix_semantics_candidates: Iterable[Mapping[str, Any]], table_semantics_candidates: Iterable[Mapping[str, Any]], caption_semantics_candidates: Iterable[Mapping[str, Any]], paragraph_semantics_candidates: Iterable[Mapping[str, Any]] = ()) -> list[Mapping[str, Any]]: return [*source_region_candidates, *document_structure_candidates, *protected_content_candidates, *list_semantics_candidates, *appendix_semantics_candidates, *table_semantics_candidates, *caption_semantics_candidates, *paragraph_semantics_candidates]
def _compat(name: str, fallback: Any) -> Any:
    return getattr(sys.modules.get("wxdf.builtin_modules"), name, fallback)


def compatibility_hook(name: str, fallback: Any) -> Any:
    """Resolve an explicitly supported development compatibility hook."""

    return _compat(name, fallback)


def build_native_candidate_baseline(source_path: Path | str, source_facts: Mapping[str, Any], source_ast: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    regions = _compat("resolve_source_regions", resolve_source_regions)(source_path, source_facts)
    lists = _compat("analyze_lists", analyze_lists)(source_facts)
    _model, list_candidates, _native_item_map = _compat("materialize_list_semantics", materialize_list_semantics)(source_ast, source_facts, lists)
    item_map = {
        str(item.get("item_id")): str(item.get("list_id"))
        for item in lists.get("items", [])
        if item.get("item_id") and item.get("list_id")
    }
    appendix = _compat("prepare_native_appendix_semantics", prepare_native_appendix_semantics)(
        source_facts,
        source_ast,
        _compat("collect_source_appendix_semantics", collect_source_appendix_semantics)(source_facts),
        item_map,
    )
    caption_builder = _compat("collect_caption_candidates", collect_caption_candidates)
    try:
        import inspect

        caption_parameters = inspect.signature(caption_builder).parameters
    except (TypeError, ValueError):
        caption_parameters = None
    accepts_facts = caption_parameters is None or len(caption_parameters) >= 2
    accepts_semantics = caption_parameters is None or "caption_semantics" in caption_parameters
    caption_semantics = (
        _compat("collect_source_caption_semantics", collect_source_caption_semantics)(
            source_facts,
            _compat("collect_source_table_semantics", collect_source_table_semantics)(source_facts),
        )
        if accepts_semantics
        else None
    )
    if accepts_semantics:
        caption_candidates = caption_builder(source_ast, source_facts, caption_semantics=caption_semantics)
    elif accepts_facts:
        caption_candidates = caption_builder(source_ast, source_facts)
    else:
        caption_candidates = caption_builder(source_ast)
    groups: dict[str, Any] = {
        "source_region_candidates": regions["candidates"],
        "document_structure_candidates": _compat("collect_structure_candidates", collect_structure_candidates)(source_facts, regions["regions"], protect_semantics=True),
        "protected_content_candidates": _compat("collect_protected_candidates", collect_protected_candidates)(source_facts),
        "list_semantics_candidates": list_candidates,
        "appendix_semantics_candidates": _compat("collect_native_appendix_candidates", collect_native_appendix_candidates)(source_facts, source_ast, appendix, item_map),
        "table_semantics_candidates": _compat("collect_table_candidates", collect_table_candidates)(source_ast),
        "caption_semantics_candidates": caption_candidates,
    }
    if source_facts.get("facts_schema_version"):
        groups["paragraph_semantics_candidates"] = _compat("collect_paragraph_candidates", collect_paragraph_candidates)(source_facts, source_ast, caption_semantics_result=caption_semantics)
    return _compat("collect_native_candidates", collect_native_candidates)(**groups)

__all__ = ["ModuleExecutionContext", "appendix_semantics_module", "build_native_candidate_baseline", "caption_semantics_module", "collect_native_candidates", "compatibility_hook", "document_structure_module", "list_semantics_module", "native_semantic_modules", "protected_content_module", "source_facts_module", "source_region_module", "table_semantics_module"]
