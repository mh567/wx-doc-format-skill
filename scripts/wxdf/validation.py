"""Pure structural validators for the modular pipeline contracts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from .contracts import (
    AST_BLOCK_TYPES,
    AST_CONTAINER_KINDS,
    AST_ROLES,
    AST_SCHEMA_VERSION,
    CAPTION_TYPES,
    CHECKPOINT_SCHEMA_VERSION,
    CHECKPOINT_STATUSES,
    ContractError,
    LIST_KINDS,
    PATCH_OPERATIONS,
    PATCH_SCHEMA_VERSION,
    SAFE_PROPERTY_SCOPES,
    SOURCE_FACTS_SCHEMA_VERSION,
    SOURCE_TYPES,
    STAGES,
    TABLE_TYPES,
)


_HASH_PREFIX = "sha256:"
_SOURCE_REF_REQUIRED = ("source_id", "part", "content_sha256")
_INLINE_KINDS = {"text", "break", "tab", "image", "link", "field", "bookmark", "opaque_inline"}
_ASSOCIATION_KINDS = {"caption_target", "sequence_continuation", "source_relation"}
_LIST_EVIDENCE_UNITS = {
    "unicode_code_point",
    "tab_width",
    "twips",
    "half_points",
}
_SOURCE_EMBEDDINGS = {"drawingml", "vml"}


def _fail(code: str, path: str, message: str, contract: str) -> None:
    raise ContractError(code, path, message, contract=contract)


def _require(mapping: Mapping[str, Any], key: str, path: str, contract: str) -> Any:
    if key not in mapping:
        _fail("missing_required", f"{path}.{key}", "required field is missing", contract)
    return mapping[key]


def _require_fields(mapping: Mapping[str, Any], fields: Sequence[str], path: str, contract: str) -> None:
    for field in fields:
        _require(mapping, field, path, contract)


def _type(value: Any, expected: type | tuple[type, ...], path: str, contract: str, code: str = "invalid_type") -> None:
    rejects_bool = expected is int or isinstance(expected, tuple) and int in expected
    if not isinstance(value, expected) or isinstance(value, bool) and rejects_bool:
        names = expected.__name__ if isinstance(expected, type) else "/".join(item.__name__ for item in expected)
        _fail(code, path, f"expected {names}", contract)


def _non_empty_string(value: Any, path: str, contract: str) -> None:
    _type(value, str, path, contract)
    if not value.strip():
        _fail("empty_string", path, "value must not be empty", contract)


def _non_negative_int(value: Any, path: str, contract: str) -> None:
    _type(value, int, path, contract)
    if value < 0:
        _fail("negative_integer", path, "value must be non-negative", contract)


def _positive_int(value: Any, path: str, contract: str) -> None:
    _type(value, int, path, contract)
    if value < 1:
        _fail("non_positive_integer", path, "value must be positive", contract)


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _HASH_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256(value: Any, path: str, contract: str) -> None:
    _non_empty_string(value, path, contract)
    if not value.startswith(_HASH_PREFIX) or len(value) != len(_HASH_PREFIX) + 64:
        _fail("invalid_sha256", path, "value must use sha256:<64 hex characters>", contract)
    try:
        int(value[len(_HASH_PREFIX):], 16)
    except ValueError:
        _fail("invalid_sha256", path, "value must use hexadecimal digest characters", contract)


def _list(value: Any, path: str, contract: str) -> list[Any]:
    _type(value, list, path, contract)
    return value


def _mapping(value: Any, path: str, contract: str) -> Mapping[str, Any]:
    _type(value, dict, path, contract)
    return value


def _validate_source_ref(ref: Any, path: str, contract: str) -> None:
    ref = _mapping(ref, path, contract)
    _require_fields(ref, _SOURCE_REF_REQUIRED, path, contract)
    for field in _SOURCE_REF_REQUIRED[:2]:
        _non_empty_string(ref[field], f"{path}.{field}", contract)
    _sha256(ref["content_sha256"], f"{path}.content_sha256", contract)
    has_span = "span" in ref
    has_node_path = "node_path" in ref
    if has_span == has_node_path:
        _fail("invalid_source_location", path, "source_ref requires exactly one span or node_path", contract)
    if has_span:
        span = _mapping(ref["span"], f"{path}.span", contract)
        _require_fields(span, ("start", "end", "unit"), f"{path}.span", contract)
        _non_negative_int(span["start"], f"{path}.span.start", contract)
        _non_negative_int(span["end"], f"{path}.span.end", contract)
        if span["end"] < span["start"]:
            _fail("invalid_span", f"{path}.span", "end must be greater than or equal to start", contract)
        if span["unit"] != "unicode_code_point":
            _fail("invalid_offset_unit", f"{path}.span.unit", "source spans use Unicode code points", contract)
    else:
        node_path = _list(ref["node_path"], f"{path}.node_path", contract)
        if not node_path:
            _fail("empty_source_location", f"{path}.node_path", "node_path must not be empty", contract)
        for index, component in enumerate(node_path):
            if not isinstance(component, (str, int)) or isinstance(component, bool):
                _fail("invalid_node_path", f"{path}.node_path[{index}]", "path components must be strings or integers", contract)


def _validate_image_fields(value: Mapping[str, Any], path: str, contract: str) -> None:
    if "materialization_mode" in value and value["materialization_mode"] not in {"inline_reference", "standalone"}:
        _fail("invalid_materialization_mode", f"{path}.materialization_mode", "image materialization mode is unsupported", contract)
    if "source_embedding" in value:
        if value["source_embedding"] not in _SOURCE_EMBEDDINGS:
            _fail("invalid_source_embedding", f"{path}.source_embedding", "source embedding is unsupported", contract)
    if "display_size" in value:
        display_size = _mapping(value["display_size"], f"{path}.display_size", contract)
        _require_fields(display_size, ("width_emu", "height_emu"), f"{path}.display_size", contract)
        _positive_int(display_size["width_emu"], f"{path}.display_size.width_emu", contract)
        _positive_int(display_size["height_emu"], f"{path}.display_size.height_emu", contract)


def _validate_inline_content(value: Any, path: str, contract: str, resources: Mapping[str, Any] | None, *, in_link: bool = False) -> str:
    items = _list(value, path, contract)
    visible: list[str] = []
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        item = _mapping(item, item_path, contract)
        kind = _require(item, "kind", item_path, contract)
        _non_empty_string(kind, f"{item_path}.kind", contract)
        if kind not in _INLINE_KINDS:
            _fail("unknown_inline_kind", f"{item_path}.kind", f"unsupported inline kind {kind!r}", contract)
        if kind == "text":
            text = _require(item, "text", item_path, contract)
            _type(text, str, f"{item_path}.text", contract)
            marks = item.get("marks", [])
            _list(marks, f"{item_path}.marks", contract)
            for mark_index, mark in enumerate(marks):
                if mark not in {"strong", "emphasis", "code"}:
                    _fail("unknown_mark", f"{item_path}.marks[{mark_index}]", "mark is not supported", contract)
            visible.append(text)
        elif kind == "break":
            visible.append("\n")
        elif kind == "tab":
            visible.append("\t")
        elif kind == "image":
            resource_id = _require(item, "resource_id", item_path, contract)
            _non_empty_string(resource_id, f"{item_path}.resource_id", contract)
            if resources is not None and resource_id not in resources:
                _fail("missing_resource", f"{item_path}.resource_id", "resource_id is not declared", contract)
            _type(_require(item, "alt_text", item_path, contract), str, f"{item_path}.alt_text", contract)
            _validate_image_fields(item, item_path, contract)
        elif kind == "link":
            if in_link:
                _fail("nested_link", item_path, "link nodes cannot contain links", contract)
            target = _require(item, "target", item_path, contract)
            _non_empty_string(target, f"{item_path}.target", contract)
            children = _require(item, "children", item_path, contract)
            visible.append(_validate_inline_content(children, f"{item_path}.children", contract, resources, in_link=True))
        elif kind in {"field", "bookmark", "opaque_inline"}:
            _require(item, "source_ref", item_path, contract)
            _validate_source_ref(item["source_ref"], f"{item_path}.source_ref", contract)
            if kind == "opaque_inline":
                _non_empty_string(_require(item, "diagnostic", item_path, contract), f"{item_path}.diagnostic", contract)
    return "".join(visible)


def validate_source_facts(facts: Mapping[str, Any]) -> None:
    contract = "SourceFacts 1.0"
    facts = _mapping(facts, "$", contract)
    _require_fields(
        facts,
        ("facts_schema_version", "source", "parser", "units", "containers", "children", "evidence", "resources", "diagnostics", "extensions"),
        "$",
        contract,
    )
    if facts["facts_schema_version"] != SOURCE_FACTS_SCHEMA_VERSION:
        _fail("unsupported_version", "facts_schema_version", "unsupported SourceFacts version", contract)

    source = _mapping(facts["source"], "source", contract)
    _require_fields(source, ("source_id", "source_type", "content_sha256"), "source", contract)
    _non_empty_string(source["source_id"], "source.source_id", contract)
    if source["source_type"] not in SOURCE_TYPES:
        _fail("unknown_source_type", "source.source_type", "source type is not supported", contract)
    _sha256(source["content_sha256"], "source.content_sha256", contract)

    parser = _mapping(facts["parser"], "parser", contract)
    _require_fields(parser, ("id", "version", "dialect", "config_sha256"), "parser", contract)
    for field in ("id", "version", "dialect"):
        _non_empty_string(parser[field], f"parser.{field}", contract)
    _sha256(parser["config_sha256"], "parser.config_sha256", contract)

    units = _mapping(facts["units"], "units", contract)
    containers = _mapping(facts["containers"], "containers", contract)
    resources = _mapping(facts["resources"], "resources", contract)
    children = _mapping(facts["children"], "children", contract)
    evidence = _mapping(facts["evidence"], "evidence", contract)
    if "root" not in containers or containers["root"].get("kind") != "document":
        _fail("missing_source_root", "containers.root", "SourceFacts requires a document root", contract)

    node_ids = set(units) | set(containers)
    if len(node_ids) != len(units) + len(containers):
        _fail("duplicate_id", "units", "unit and container IDs must be globally unique", contract)
    for unit_id, unit in units.items():
        unit_path = f"units.{unit_id}"
        unit = _mapping(unit, unit_path, contract)
        _require(unit, "kind", unit_path, contract)
        if "source_ref" not in unit:
            _fail("missing_source_ref", f"{unit_path}.source_ref", "source units require source_ref", contract)
        _non_empty_string(unit["kind"], f"{unit_path}.kind", contract)
        _validate_source_ref(unit["source_ref"], f"{unit_path}.source_ref", contract)
        if "inline_content" in unit:
            _validate_inline_content(unit["inline_content"], f"{unit_path}.inline_content", contract, resources)
    for container_id, container in containers.items():
        container_path = f"containers.{container_id}"
        container = _mapping(container, container_path, contract)
        kind = _require(container, "kind", container_path, contract)
        if kind not in {"document", "section", "quote", "list", "list_item", "table", "table_row", "table_cell"}:
            _fail("unknown_container_kind", f"{container_path}.kind", "unsupported source container kind", contract)

    _validate_children_graph(children, node_ids, "children", contract)
    for target_id, target_evidence in evidence.items():
        if target_id not in node_ids:
            _fail("unknown_evidence_target", f"evidence.{target_id}", "evidence target is not declared", contract)
        target_evidence = _mapping(target_evidence, f"evidence.{target_id}", contract)
        if "indent_unit" in target_evidence:
            unit = target_evidence["indent_unit"]
            if unit is not None and unit not in _LIST_EVIDENCE_UNITS:
                _fail("invalid_indent_unit", f"evidence.{target_id}.indent_unit", "indent unit is unsupported", contract)
            if source["source_type"] == "markdown" and unit not in {None, "unicode_code_point", "tab_width"}:
                _fail("invalid_indent_unit", f"evidence.{target_id}.indent_unit", "Markdown facts require text-based indentation units", contract)
    for index, diagnostic in enumerate(_list(facts["diagnostics"], "diagnostics", contract)):
        diagnostic = _mapping(diagnostic, f"diagnostics[{index}]", contract)
        _require_fields(diagnostic, ("code", "severity", "message"), f"diagnostics[{index}]", contract)
        for field in ("code", "severity", "message"):
            _non_empty_string(diagnostic[field], f"diagnostics[{index}].{field}", contract)
    for resource_id, resource in resources.items():
        resource_path = f"resources.{resource_id}"
        resource = _mapping(resource, resource_path, contract)
        _require_fields(resource, ("kind", "source_ref", "content_sha256"), resource_path, contract)
        _non_empty_string(resource["kind"], f"{resource_path}.kind", contract)
        _validate_source_ref(resource["source_ref"], f"{resource_path}.source_ref", contract)
        _sha256(resource["content_sha256"], f"{resource_path}.content_sha256", contract)
        if "media_type" in resource:
            media_type = resource["media_type"]
            _non_empty_string(media_type, f"{resource_path}.media_type", contract)
            if not media_type.startswith("image/"):
                _fail("invalid_media_type", f"{resource_path}.media_type", "image resources require an image media type", contract)


def _validate_children_graph(children: Mapping[str, Any], node_ids: set[str], path: str, contract: str) -> dict[str, list[str]]:
    parents: dict[str, str] = {}
    parent_conflicts: list[tuple[str, str]] = []
    normalized: dict[str, list[str]] = {}
    for parent_id, raw_children in children.items():
        if parent_id not in node_ids:
            _fail("unknown_parent", f"{path}.{parent_id}", "children key is not a declared node", contract)
        values = _list(raw_children, f"{path}.{parent_id}", contract)
        normalized[parent_id] = []
        for index, child_id in enumerate(values):
            child_path = f"{path}.{parent_id}[{index}]"
            _non_empty_string(child_id, child_path, contract)
            if child_id not in node_ids:
                _fail("unknown_child", child_path, "child ID is not declared", contract)
            previous = parents.get(child_id)
            if previous is not None and previous != parent_id:
                parent_conflicts.append((child_path, previous))
            else:
                parents[child_id] = parent_id
            normalized[parent_id].append(child_id)

    root = "root"
    if root not in node_ids:
        _fail("missing_root", root, "graph requires root", contract)
    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        if state.get(node_id) == 1:
            _fail("cycle", path, "children graph contains a cycle", contract)
        if state.get(node_id) == 2:
            return
        state[node_id] = 1
        for child_id in normalized.get(node_id, []):
            visit(child_id)
        state[node_id] = 2

    for node_id in node_ids:
        if state.get(node_id) is None:
            visit(node_id)
    if parent_conflicts:
        conflict_path, previous_parent = parent_conflicts[0]
        _fail("multiple_parents", conflict_path, f"node already belongs to {previous_parent}", contract)
    if root in parents:
        _fail("root_has_parent", root, "root cannot have a structural parent", contract)
    reachable: set[str] = set()
    state = {}

    def mark_reachable(node_id: str) -> None:
        if node_id in reachable:
            return
        reachable.add(node_id)
        for child_id in normalized.get(node_id, []):
            mark_reachable(child_id)

    mark_reachable(root)
    for node_id in node_ids - {root}:
        if node_id not in parents:
            _fail("unreachable_node", node_id, "every non-root node requires one structural parent", contract)
    visit(root)
    if len(reachable) != len(node_ids):
        missing = sorted(node_ids - reachable)[0]
        _fail("unreachable_node", missing, "node is outside the root graph", contract)
    return normalized


def _node_kind(node_id: str, blocks: Mapping[str, Any], containers: Mapping[str, Any]) -> str:
    if node_id in blocks:
        return blocks[node_id].get("block_type", "")
    return containers[node_id].get("kind", "")


def _validate_ast_children(document: Mapping[str, Any], children: Mapping[str, list[str]], contract: str) -> None:
    blocks = document["blocks"]
    containers = document["containers"]
    for parent_id, child_ids in children.items():
        parent_kind = _node_kind(parent_id, blocks, containers)
        for index, child_id in enumerate(child_ids):
            child_kind = _node_kind(child_id, blocks, containers)
            child_path = f"document.children.{parent_id}[{index}]"
            allowed = True
            if parent_kind == "list" and child_kind != "list_item":
                allowed = False
            elif parent_kind == "table" and child_kind != "table_row":
                allowed = False
            elif parent_kind == "table_row" and child_kind != "table_cell":
                allowed = False
            elif parent_kind == "table_cell" and child_kind in {"table_row", "table_cell"}:
                allowed = False
            elif parent_kind == "list_item" and child_kind == "list_item":
                allowed = False
            if not allowed:
                _fail("invalid_child_type", child_path, f"{parent_kind} cannot contain {child_kind}", contract)


def _validate_node_origin(node: Mapping[str, Any], path: str, contract: str) -> None:
    if "source_ref" in node:
        _validate_source_ref(node["source_ref"], f"{path}.source_ref", contract)
        return
    _non_empty_string(_require(node, "generated_by", path, contract), f"{path}.generated_by", contract)
    basis = _require(node, "generation_basis", path, contract)
    _list(basis, f"{path}.generation_basis", contract)
    if not basis:
        _fail("empty_generation_basis", f"{path}.generation_basis", "generated nodes require evidence", contract)


def _validate_associations(document: Mapping[str, Any], contract: str) -> None:
    blocks = document["blocks"]
    containers = document["containers"]
    node_ids = set(blocks) | set(containers)
    associations = _list(document["associations"], "document.associations", contract)
    seen: set[tuple[str, str, str]] = set()
    continuation_edges: dict[str, str] = {}
    for index, association in enumerate(associations):
        path = f"document.associations[{index}]"
        association = _mapping(association, path, contract)
        _require_fields(association, ("kind", "source_id", "target_id"), path, contract)
        kind = association["kind"]
        if kind not in _ASSOCIATION_KINDS:
            _fail("unknown_association_kind", f"{path}.kind", "association kind is unsupported", contract)
        for field in ("source_id", "target_id"):
            if association[field] not in node_ids:
                _fail("unknown_association_target", f"{path}.{field}", "association target is not declared", contract)
        key = (kind, association["source_id"], association["target_id"])
        if key in seen:
            _fail("duplicate_association", path, "association is repeated", contract)
        seen.add(key)
        if kind == "caption_target":
            source = blocks.get(association["source_id"])
            target = blocks.get(association["target_id"])
            if not source or source.get("role") != "caption":
                _fail("invalid_caption_source", f"{path}.source_id", "caption_target source must be a caption block", contract)
            if not target or target.get("block_type") not in {"table", "image"}:
                _fail("invalid_caption_target", f"{path}.target_id", "caption target must be table or image", contract)
        if kind == "sequence_continuation":
            previous = continuation_edges.get(association["source_id"])
            if previous is not None and previous != association["target_id"]:
                _fail("multiple_sequence_targets", f"{path}.source_id", "sequence continuation has multiple targets", contract)
            continuation_edges[association["source_id"]] = association["target_id"]
    for source_id in continuation_edges:
        current = source_id
        visited: set[str] = set()
        while current in continuation_edges:
            if current in visited:
                _fail("sequence_cycle", "document.associations", "sequence continuation contains a cycle", contract)
            visited.add(current)
            current = continuation_edges[current]


def _validate_table_spans(document: Mapping[str, Any], children: Mapping[str, list[str]], contract: str) -> None:
    blocks = document["blocks"]
    containers = document["containers"]
    for table_id, table in blocks.items():
        if table.get("block_type") != "table":
            continue
        rows = children.get(table_id, [])
        occupied: dict[tuple[int, int], str] = {}
        for row_index, row_id in enumerate(rows):
            cells = children.get(row_id, [])
            for cell_id in cells:
                cell = containers[cell_id]
                if cell.get("row") != row_index:
                    _fail("invalid_table_row", f"document.containers.{cell_id}.row", "cell row must match its table row", contract)
                row_span = cell.get("row_span")
                col_span = cell.get("col_span")
                _positive_int(row_span, f"document.containers.{cell_id}.row_span", contract)
                _positive_int(col_span, f"document.containers.{cell_id}.col_span", contract)
                for row in range(cell["row"], cell["row"] + row_span):
                    for col in range(cell["col"], cell["col"] + col_span):
                        key = (row, col)
                        if row >= len(rows) or key in occupied:
                            _fail("table_span_overlap", f"document.containers.{cell_id}", "table cell spans overlap or exceed the table grid", contract)
                        occupied[key] = cell_id


def validate_ast(ast: Mapping[str, Any]) -> None:
    contract = "AST 2.0"
    ast = _mapping(ast, "$", contract)
    _require_fields(ast, ("schema_version", "revision", "document"), "$", contract)
    if ast["schema_version"] != AST_SCHEMA_VERSION:
        _fail("unsupported_version", "schema_version", "unsupported AST version", contract)
    _non_negative_int(ast["revision"], "revision", contract)
    document = _mapping(ast["document"], "document", contract)
    _require_fields(document, ("root_id", "blocks", "containers", "children", "associations", "resources"), "document", contract)
    blocks = _mapping(document["blocks"], "document.blocks", contract)
    containers = _mapping(document["containers"], "document.containers", contract)
    children = _mapping(document["children"], "document.children", contract)
    resources = _mapping(document["resources"], "document.resources", contract)
    root_id = document["root_id"]
    if root_id != "root":
        _fail("invalid_root_id", "document.root_id", "AST root_id must be root", contract)
    if root_id not in containers or containers[root_id].get("kind") != "document":
        _fail("missing_root", "document.containers.root", "AST requires a document root container", contract)
    overlap = set(blocks) & set(containers)
    if overlap:
        _fail("duplicate_id", "document", f"node ID is used by both block and container: {sorted(overlap)[0]}", contract)
    node_ids = set(blocks) | set(containers)
    if len(node_ids) != len(blocks) + len(containers):
        _fail("duplicate_id", "document", "node IDs must be unique", contract)
    normalized_children = _validate_children_graph(children, node_ids, "document.children", contract)
    _validate_ast_children(document, normalized_children, contract)

    for container_id, container in containers.items():
        path = f"document.containers.{container_id}"
        container = _mapping(container, path, contract)
        kind = _require(container, "kind", path, contract)
        if kind not in AST_CONTAINER_KINDS:
            _fail("unknown_container_kind", f"{path}.kind", "unsupported AST container kind", contract)
        if kind == "list":
            _require_fields(container, ("list_kind", "source_start", "source_sequence_id"), path, contract)
            if container["list_kind"] not in LIST_KINDS:
                _fail("unknown_list_kind", f"{path}.list_kind", "list kind is unsupported", contract)
            _positive_int(container["source_start"], f"{path}.source_start", contract)
            _non_empty_string(container["source_sequence_id"], f"{path}.source_sequence_id", contract)
        elif kind == "list_item":
            _require_fields(container, ("source_marker", "source_ordinal", "source_sequence_id"), path, contract)
            _non_empty_string(container["source_marker"], f"{path}.source_marker", contract)
            _positive_int(container["source_ordinal"], f"{path}.source_ordinal", contract)
            _non_empty_string(container["source_sequence_id"], f"{path}.source_sequence_id", contract)
        elif kind == "appendix":
            _require_fields(container, ("source_classification", "title_ref"), path, contract)
            if container["source_classification"] not in {"normative", "informative", "unknown"}:
                _fail("unknown_appendix_classification", f"{path}.source_classification", "appendix classification is unsupported", contract)
            if container["title_ref"] not in blocks:
                _fail("unknown_title_ref", f"{path}.title_ref", "appendix title_ref must reference a block", contract)
        elif kind == "table_cell":
            _require_fields(container, ("row", "col", "row_span", "col_span"), path, contract)
            _non_negative_int(container["row"], f"{path}.row", contract)
            _non_negative_int(container["col"], f"{path}.col", contract)
            _positive_int(container["row_span"], f"{path}.row_span", contract)
            _positive_int(container["col_span"], f"{path}.col_span", contract)
            if container.get("cell_role", "body") not in {"header", "body"}:
                _fail("unknown_table_cell_role", f"{path}.cell_role", "table cell role is unsupported", contract)
        if kind != "document" and "source_ref" in container:
            _validate_source_ref(container["source_ref"], f"{path}.source_ref", contract)

    for block_id, block in blocks.items():
        path = f"document.blocks.{block_id}"
        block = _mapping(block, path, contract)
        block_type = _require(block, "block_type", path, contract)
        if block_type not in AST_BLOCK_TYPES:
            _fail("unknown_block_type", f"{path}.block_type", "unsupported AST block type", contract)
        _validate_node_origin(block, path, contract)
        if block_type == "paragraph":
            role = _require(block, "role", path, contract)
            if role not in AST_ROLES:
                _fail("unknown_role", f"{path}.role", "paragraph role is unsupported", contract)
            inline = _require(block, "inline_content", path, contract)
            projection = _validate_inline_content(inline, f"{path}.inline_content", contract, resources)
            if "text" in block and block["text"] != projection:
                _fail("text_projection_mismatch", f"{path}.text", "text must equal inline_content projection", contract)
            if role == "heading":
                if "level" not in block:
                    _fail("missing_heading_level", f"{path}.level", "heading requires source semantic level", contract)
                if block["level"] not in {1, 2, 3, 4, 5, 6}:
                    _fail("invalid_heading_level", f"{path}.level", "heading level must be between 1 and 6", contract)
            if role == "caption":
                _require_fields(block, ("caption_type", "target_id"), path, contract)
                if block["caption_type"] not in CAPTION_TYPES:
                    _fail("unknown_caption_type", f"{path}.caption_type", "caption type is unsupported", contract)
                if block["target_id"] not in blocks:
                    _fail("unknown_caption_target", f"{path}.target_id", "caption target is not declared", contract)
                target_type = blocks[block["target_id"]].get("block_type")
                expected_type = "table" if block["caption_type"] == "table" else "image"
                if target_type != expected_type:
                    _fail("invalid_caption_target", f"{path}.target_id", "caption type does not match target block", contract)
        elif block_type == "image":
            resource_id = _require(block, "resource_id", path, contract)
            if resource_id not in resources:
                _fail("missing_resource", f"{path}.resource_id", "image resource is not declared", contract)
            _non_empty_string(_require(block, "alt_text", path, contract), f"{path}.alt_text", contract)
            _validate_image_fields(block, path, contract)
        elif block_type == "table":
            table_type = _require(block, "table_type", path, contract)
            if "header_rows" in block:
                _non_negative_int(block["header_rows"], f"{path}.header_rows", contract)
            if table_type not in TABLE_TYPES:
                _fail("unknown_table_type", f"{path}.table_type", "table type is unsupported", contract)
        elif block_type == "opaque":
            diagnostics = _require(block, "diagnostic_ids", path, contract)
            if not _list(diagnostics, f"{path}.diagnostic_ids", contract):
                _fail("missing_opaque_diagnostic", f"{path}.diagnostic_ids", "opaque nodes require diagnostics", contract)

    for resource_id, resource in resources.items():
        path = f"document.resources.{resource_id}"
        resource = _mapping(resource, path, contract)
        _require_fields(resource, ("kind", "content_sha256", "source_ref"), path, contract)
        _non_empty_string(resource["kind"], f"{path}.kind", contract)
        _sha256(resource["content_sha256"], f"{path}.content_sha256", contract)
        _validate_source_ref(resource["source_ref"], f"{path}.source_ref", contract)
        if "media_type" in resource:
            media_type = resource["media_type"]
            _non_empty_string(media_type, f"{path}.media_type", contract)
            if not media_type.startswith("image/"):
                _fail("invalid_media_type", f"{path}.media_type", "image resources require an image media type", contract)
    _validate_associations(document, contract)
    _validate_table_spans(document, normalized_children, contract)


def _validate_evidence_list(value: Any, path: str, contract: str) -> None:
    for index, evidence in enumerate(_list(value, path, contract)):
        evidence_path = f"{path}[{index}]"
        evidence = _mapping(evidence, evidence_path, contract)
        _non_empty_string(_require(evidence, "kind", evidence_path, contract), f"{evidence_path}.kind", contract)


def validate_patch(patch: Mapping[str, Any]) -> None:
    contract = "Patch 2.0"
    patch = _mapping(patch, "$", contract)
    _require_fields(patch, ("patch_schema_version", "patch_id", "base_revision", "phase", "candidates", "decisions"), "$", contract)
    if patch["patch_schema_version"] != PATCH_SCHEMA_VERSION:
        _fail("unsupported_version", "patch_schema_version", "unsupported Patch version", contract)
    _non_empty_string(patch["patch_id"], "patch_id", contract)
    _non_negative_int(patch["base_revision"], "base_revision", contract)
    if patch["phase"] not in STAGES:
        _fail("unknown_phase", "phase", "patch phase is unsupported", contract)
    candidate_ids: set[str] = set()
    for index, candidate in enumerate(_list(patch["candidates"], "candidates", contract)):
        path = f"candidates[{index}]"
        candidate = _mapping(candidate, path, contract)
        _require_fields(candidate, ("candidate_id", "module_id", "module_version", "target_source_refs", "target_node_ids", "proposal", "positive_evidence", "negative_evidence", "scope", "constraints", "rationale"), path, contract)
        candidate_id = _require(candidate, "candidate_id", path, contract)
        _non_empty_string(candidate_id, f"{path}.candidate_id", contract)
        if candidate_id in candidate_ids:
            _fail("duplicate_candidate_id", f"{path}.candidate_id", "candidate ID is repeated", contract)
        candidate_ids.add(candidate_id)
        for field in ("module_id", "module_version", "rationale"):
            _non_empty_string(candidate[field], f"{path}.{field}", contract)
        source_refs = _list(candidate["target_source_refs"], f"{path}.target_source_refs", contract)
        if not source_refs:
            _fail("empty_target", f"{path}.target_source_refs", "candidate requires a target source reference", contract)
        for ref_index, ref in enumerate(source_refs):
            _validate_source_ref(ref, f"{path}.target_source_refs[{ref_index}]", contract)
        target_ids = _list(candidate["target_node_ids"], f"{path}.target_node_ids", contract)
        if not target_ids:
            _fail("empty_target", f"{path}.target_node_ids", "candidate requires a target node", contract)
        for target_index, target_id in enumerate(target_ids):
            _non_empty_string(target_id, f"{path}.target_node_ids[{target_index}]", contract)
        _mapping(candidate["proposal"], f"{path}.proposal", contract)
        _validate_evidence_list(candidate["positive_evidence"], f"{path}.positive_evidence", contract)
        _validate_evidence_list(candidate["negative_evidence"], f"{path}.negative_evidence", contract)
        scope = _mapping(candidate["scope"], f"{path}.scope", contract)
        stages = _list(_require(scope, "stages", f"{path}.scope", contract), f"{path}.scope.stages", contract)
        for stage_index, stage in enumerate(stages):
            if stage not in STAGES:
                _fail("unknown_phase", f"{path}.scope.stages[{stage_index}]", "candidate stage is unsupported", contract)
        for constraint_index, constraint in enumerate(_list(candidate["constraints"], f"{path}.constraints", contract)):
            _mapping(constraint, f"{path}.constraints[{constraint_index}]", contract)
        if "confidence" in candidate:
            confidence = candidate["confidence"]
            _type(confidence, (float, int), f"{path}.confidence", contract)
            if not 0 <= confidence <= 1:
                _fail("invalid_confidence", f"{path}.confidence", "confidence must be between 0 and 1", contract)

    decision_ids: set[str] = set()
    for index, decision in enumerate(_list(patch["decisions"], "decisions", contract)):
        path = f"decisions[{index}]"
        decision = _mapping(decision, path, contract)
        _require_fields(decision, ("decision_id", "candidate_id", "operation", "target_node_ids", "expected_revision", "property_scope", "value", "preconditions", "evidence", "invalidates"), path, contract)
        decision_id = decision["decision_id"]
        _non_empty_string(decision_id, f"{path}.decision_id", contract)
        if decision_id in decision_ids:
            _fail("duplicate_decision_id", f"{path}.decision_id", "decision ID is repeated", contract)
        decision_ids.add(decision_id)
        if decision["candidate_id"] not in candidate_ids:
            _fail("unknown_candidate", f"{path}.candidate_id", "decision references an unknown candidate", contract)
        operation = decision["operation"]
        if operation not in PATCH_OPERATIONS:
            _fail("unsupported_operation", f"{path}.operation", "operation is outside the semantic patch contract", contract)
        target_ids = _list(decision["target_node_ids"], f"{path}.target_node_ids", contract)
        if operation != "create_node" and not target_ids:
            _fail("empty_target", f"{path}.target_node_ids", "decision requires a target node", contract)
        for target_index, target_id in enumerate(target_ids):
            _non_empty_string(target_id, f"{path}.target_node_ids[{target_index}]", contract)
        _non_negative_int(decision["expected_revision"], f"{path}.expected_revision", contract)
        scopes = _list(decision["property_scope"], f"{path}.property_scope", contract)
        if not scopes:
            _fail("empty_property_scope", f"{path}.property_scope", "decision requires a property scope", contract)
        for scope_index, scope in enumerate(scopes):
            if scope not in SAFE_PROPERTY_SCOPES:
                _fail("forbidden_property_scope", f"{path}.property_scope[{scope_index}]", "property is outside the semantic contract", contract)
        _mapping(decision["value"], f"{path}.value", contract)
        if operation == "update_node_content" and not isinstance(decision["value"].get("inline_content"), list):
            _fail("missing_inline_content", f"{path}.value.inline_content", "content update requires an inline content list", contract)
        if operation == "update_node_content":
            if scopes != ["inline_content"] or set(decision["value"]) != {"inline_content"}:
                _fail("invalid_content_scope", f"{path}.property_scope", "content update owns only inline_content", contract)
            _validate_inline_content(decision["value"]["inline_content"], f"{path}.value.inline_content", contract, None)
        if operation == "update_node_semantics" and "materialization_mode" in decision["value"]:
            _validate_image_fields(decision["value"], f"{path}.value", contract)
        for precondition_index, precondition in enumerate(_list(decision["preconditions"], f"{path}.preconditions", contract)):
            _mapping(precondition, f"{path}.preconditions[{precondition_index}]", contract)
        _validate_evidence_list(decision["evidence"], f"{path}.evidence", contract)
        for stage_index, stage in enumerate(_list(decision["invalidates"], f"{path}.invalidates", contract)):
            if stage not in STAGES:
                _fail("unknown_phase", f"{path}.invalidates[{stage_index}]", "invalidated stage is unsupported", contract)


def _validate_ledger(ledger: Any, path: str, contract: str) -> None:
    ledger = _mapping(ledger, path, contract)
    _require_fields(ledger, ("schema_version", "revision", "revisions"), path, contract)
    _non_empty_string(ledger["schema_version"], f"{path}.schema_version", contract)
    _non_negative_int(ledger["revision"], f"{path}.revision", contract)
    revisions = _list(ledger["revisions"], f"{path}.revisions", contract)
    seen: set[str] = set()
    for index, revision in enumerate(revisions):
        revision_path = f"{path}.revisions[{index}]"
        revision = _mapping(revision, revision_path, contract)
        _require_fields(revision, ("revision_id", "transaction_id", "target_source_refs", "target_node_ids", "property_scope", "decision", "preconditions", "evidence", "origin", "resolver_version", "status", "supersedes"), revision_path, contract)
        revision_id = revision["revision_id"]
        _non_empty_string(revision_id, f"{revision_path}.revision_id", contract)
        if revision_id in seen:
            _fail("duplicate_revision_id", f"{revision_path}.revision_id", "revision ID is repeated", contract)
        seen.add(revision_id)
        _non_empty_string(revision["transaction_id"], f"{revision_path}.transaction_id", contract)
        _non_empty_string(revision["resolver_version"], f"{revision_path}.resolver_version", contract)
        if revision["status"] not in {"active", "superseded", "invalidated", "conflicted"}:
            _fail("unknown_revision_status", f"{revision_path}.status", "revision status is unsupported", contract)
        if "parent_revision" in revision and revision["parent_revision"] is not None:
            _non_negative_int(revision["parent_revision"], f"{revision_path}.parent_revision", contract)
        for ref_index, ref in enumerate(_list(revision["target_source_refs"], f"{revision_path}.target_source_refs", contract)):
            _validate_source_ref(ref, f"{revision_path}.target_source_refs[{ref_index}]", contract)
        for field in ("target_node_ids", "property_scope", "supersedes"):
            _list(revision[field], f"{revision_path}.{field}", contract)
        _mapping(revision["decision"], f"{revision_path}.decision", contract)
        _mapping(revision["origin"], f"{revision_path}.origin", contract)


def validate_checkpoint(checkpoint: Mapping[str, Any]) -> None:
    contract = "Checkpoint 2.0"
    checkpoint = _mapping(checkpoint, "$", contract)
    fields = (
        "checkpoint_schema_version",
        "run_id",
        "status",
        "resume_stage",
        "source_facts",
        "decision_ledger",
        "source_ast",
        "normalized_ast",
        "content_overlay",
        "presentation_override",
        "target_map",
        "resource_summary",
        "contracts",
        "modules",
        "requests",
        "responses",
        "applied_transactions",
        "transaction_content_digests",
        "budget",
        "round",
        "candidate_baseline_id",
    )
    for field in fields:
        if field not in checkpoint:
            _fail("missing_checkpoint_field", field, "checkpoint field is required even when empty", contract)
    if checkpoint["checkpoint_schema_version"] != CHECKPOINT_SCHEMA_VERSION:
        _fail("unsupported_version", "checkpoint_schema_version", "unsupported checkpoint version", contract)
    digest_version = checkpoint.get("transaction_digest_version")
    if digest_version is not None and digest_version not in {"1.0", "2.0"}:
        _fail(
            "unsupported_version",
            "transaction_digest_version",
            "unsupported transaction digest version",
            contract,
        )
    _non_empty_string(checkpoint["run_id"], "run_id", contract)
    status = checkpoint["status"]
    if status not in CHECKPOINT_STATUSES:
        _fail("unknown_checkpoint_status", "status", "checkpoint status is unsupported", contract)
    resume_stage = checkpoint["resume_stage"]
    if status == "waiting_external":
        if resume_stage not in STAGES:
            _fail("invalid_resume_state", "resume_stage", "waiting_external requires a valid resume stage", contract)
    elif resume_stage is not None:
        _fail("invalid_resume_state", "resume_stage", "only waiting_external checkpoints may name a resume stage", contract)
    validate_source_facts(checkpoint["source_facts"])
    _validate_ledger(checkpoint["decision_ledger"], "decision_ledger", contract)
    if checkpoint["decision_ledger"].get("schema_version") == "2.0":
        from .decision_ledger import LedgerError, validate_revisions

        try:
            validate_revisions(checkpoint["source_facts"], checkpoint["decision_ledger"])
        except LedgerError as exc:
            _fail("invalid_revision", "decision_ledger", str(exc), contract)
    validate_ast(checkpoint["source_ast"])
    if checkpoint["normalized_ast"] is not None:
        normalized = _mapping(checkpoint["normalized_ast"], "normalized_ast", contract)
        if normalized.get("schema_version") == AST_SCHEMA_VERSION and "document" in normalized:
            validate_ast(normalized)
        else:
            fields = (
                "schema_version",
                "source_revision",
                "source_ast",
                "overlay",
                "presentation_override",
                "presentation",
                "generated_nodes",
                "template_preflight",
                "diagnostics",
                "semantic_digest",
                "presentation_digest",
                "context",
            )
            _require_fields(normalized, fields, "normalized_ast", contract)
            if normalized["schema_version"] != "1.0":
                _fail("unsupported_version", "normalized_ast.schema_version", "unsupported normalized snapshot version", contract)
            _non_negative_int(normalized["source_revision"], "normalized_ast.source_revision", contract)
            validate_ast(normalized["source_ast"])
            if normalized["source_ast"]["revision"] != normalized["source_revision"]:
                _fail("revision_mismatch", "normalized_ast.source_revision", "normalized source revision does not match its Source AST", contract)
            for field in ("overlay", "presentation_override", "presentation", "template_preflight", "context"):
                _mapping(normalized[field], f"normalized_ast.{field}", contract)
            for field in ("generated_nodes", "diagnostics"):
                _list(normalized[field], f"normalized_ast.{field}", contract)
            _sha256(normalized["semantic_digest"], "normalized_ast.semantic_digest", contract)
            _sha256(normalized["presentation_digest"], "normalized_ast.presentation_digest", contract)
            expected_semantic = _canonical_digest({
                "source_revision": normalized["source_revision"],
                "source_ast": normalized["source_ast"],
                "overlay": normalized["overlay"],
            })
            if normalized["semantic_digest"] != expected_semantic:
                _fail("digest_mismatch", "normalized_ast.semantic_digest", "normalized semantic digest does not match its snapshot", contract)
            expected_presentation = _canonical_digest(normalized["presentation"])
            if normalized["presentation_digest"] != expected_presentation:
                _fail("digest_mismatch", "normalized_ast.presentation_digest", "normalized presentation digest does not match its snapshot", contract)
    for field in ("content_overlay", "presentation_override", "modules", "requests"):
        _list(checkpoint[field], field, contract)
    responses = _list(checkpoint["responses"], "responses", contract)
    applied_transactions = _list(checkpoint["applied_transactions"], "applied_transactions", contract)
    applied_digests: dict[str, str] = {}
    for index, transaction in enumerate(applied_transactions):
        transaction_path = f"applied_transactions[{index}]"
        transaction = _mapping(transaction, transaction_path, contract)
        _require_fields(transaction, ("transaction_id", "content_sha256"), transaction_path, contract)
        _non_empty_string(transaction["transaction_id"], f"{transaction_path}.transaction_id", contract)
        _sha256(transaction["content_sha256"], f"{transaction_path}.content_sha256", contract)
        if transaction["transaction_id"] in applied_digests:
            _fail("transaction_state_mismatch", transaction_path, "transaction ID is repeated", contract)
        applied_digests[transaction["transaction_id"]] = transaction["content_sha256"]
    digests = _mapping(checkpoint["transaction_content_digests"], "transaction_content_digests", contract)
    for transaction_id, digest in digests.items():
        _non_empty_string(transaction_id, "transaction_content_digests key", contract)
        _sha256(digest, f"transaction_content_digests.{transaction_id}", contract)
    if applied_digests != dict(digests):
        _fail("transaction_state_mismatch", "applied_transactions", "transaction IDs and content digests must match exactly", contract)
    configuration = checkpoint.get("configuration")
    native_file_checkpoint = (
        checkpoint.get("profile") == "wx_full_rules-native-v2"
        or isinstance(configuration, Mapping)
        and configuration.get("modular_profile") == "native-v2"
    )
    if native_file_checkpoint and status in {"waiting_external", "completed", "completed_with_warnings"}:
        bindings = _mapping(
            checkpoint.get("response_transaction_bindings"),
            "response_transaction_bindings",
            contract,
        )
        if status == "waiting_external" and bindings:
            _fail(
                "response_transaction_binding_mismatch",
                "response_transaction_bindings",
                "waiting checkpoint cannot carry response transaction bindings",
                contract,
            )
        bound_request_ids: list[str] = []
        for index, response in enumerate(responses):
            response_path = f"responses[{index}]"
            response = _mapping(response, response_path, contract)
            applied = response.get("applied")
            apply_started = response.get("apply_started", False)
            transaction_id = response.get("transaction_id")
            transaction_digest = response.get("transaction_content_sha256")
            if status == "waiting_external":
                if applied is not False or apply_started is not False:
                    _fail(
                        "response_application_state_mismatch",
                        response_path,
                        "waiting response must remain unapplied",
                        contract,
                    )
                if transaction_id is not None or transaction_digest is not None:
                    _fail(
                        "response_application_state_mismatch",
                        response_path,
                        "waiting response cannot carry transaction application metadata",
                        contract,
                    )
                continue
            if applied is not True or response.get("apply_started") is not False:
                _fail(
                    "response_application_state_mismatch",
                    response_path,
                    "completed response must be applied and have no application in progress",
                    contract,
                )
            if (
                not isinstance(transaction_id, str)
                or not transaction_id
                or not isinstance(transaction_digest, str)
                or applied_digests.get(transaction_id) != transaction_digest
            ):
                _fail(
                    "response_application_state_mismatch",
                    response_path,
                    "completed response transaction binding does not match checkpoint transactions",
                    contract,
                )
            request_id = response.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                _fail(
                    "response_transaction_binding_mismatch",
                    response_path,
                    "completed response requires a request identity",
                    contract,
                )
            bound_request_ids.append(request_id)
            binding_path = f"response_transaction_bindings.{request_id}"
            binding = _mapping(bindings.get(request_id), binding_path, contract)
            binding_content = {
                "request_id": request_id,
                "response_sha256": response.get("response_sha256"),
                "transaction_id": transaction_id,
                "transaction_content_sha256": transaction_digest,
            }
            if any(binding.get(field) != value for field, value in binding_content.items()):
                _fail(
                    "response_transaction_binding_mismatch",
                    binding_path,
                    "saved response transaction proof does not match the response",
                    contract,
                )
            if binding.get("binding_sha256") != _canonical_digest(binding_content):
                _fail(
                    "response_transaction_binding_mismatch",
                    binding_path,
                    "saved response transaction proof digest is invalid",
                    contract,
                )
        if status != "waiting_external" and (
            len(bound_request_ids) != len(set(bound_request_ids))
            or set(bindings) != set(bound_request_ids)
        ):
            _fail(
                "response_transaction_binding_mismatch",
                "response_transaction_bindings",
                "completed response transaction proofs do not match response identities",
                contract,
            )
    target_map = _mapping(checkpoint["target_map"], "target_map", contract)
    _require_fields(target_map, ("revision", "entries", "view_sha256"), "target_map", contract)
    _non_negative_int(target_map["revision"], "target_map.revision", contract)
    _mapping(target_map["entries"], "target_map.entries", contract)
    _sha256(target_map["view_sha256"], "target_map.view_sha256", contract)
    resource_summary = _mapping(checkpoint["resource_summary"], "resource_summary", contract)
    _require_fields(resource_summary, ("resources", "source_sha256"), "resource_summary", contract)
    _list(resource_summary["resources"], "resource_summary.resources", contract)
    _sha256(resource_summary["source_sha256"], "resource_summary.source_sha256", contract)
    versions = _mapping(checkpoint["contracts"], "contracts", contract)
    for field, expected in (("source_facts", SOURCE_FACTS_SCHEMA_VERSION), ("ast", AST_SCHEMA_VERSION), ("patch", PATCH_SCHEMA_VERSION), ("checkpoint", CHECKPOINT_SCHEMA_VERSION)):
        if versions.get(field) != expected:
            _fail("contract_version_mismatch", f"contracts.{field}", f"expected contract version {expected}", contract)
    for index, module in enumerate(checkpoint["modules"]):
        module_path = f"modules[{index}]"
        module = _mapping(module, module_path, contract)
        _require_fields(module, ("id", "version"), module_path, contract)
        _non_empty_string(module["id"], f"{module_path}.id", contract)
        _non_empty_string(module["version"], f"{module_path}.version", contract)
    budget = _mapping(checkpoint["budget"], "budget", contract)
    _require_fields(budget, ("limit", "consumed", "waiting_seconds"), "budget", contract)
    _non_negative_int(budget["limit"], "budget.limit", contract)
    _non_negative_int(budget["consumed"], "budget.consumed", contract)
    if budget["consumed"] > budget["limit"]:
        _fail("budget_exceeded", "budget.consumed", "consumed budget exceeds limit", contract)
    _type(budget["waiting_seconds"], (float, int), "budget.waiting_seconds", contract)
    if budget["waiting_seconds"] < 0:
        _fail("negative_duration", "budget.waiting_seconds", "waiting time must be non-negative", contract)
    _non_negative_int(checkpoint["round"], "round", contract)
    _non_empty_string(checkpoint["candidate_baseline_id"], "candidate_baseline_id", contract)
