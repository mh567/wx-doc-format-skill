"""Versioned data types and error definitions for modular pipeline contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence, TypeAlias, TypedDict


SOURCE_FACTS_SCHEMA_VERSION = "1.0"
AST_SCHEMA_VERSION = "2.0"
PATCH_SCHEMA_VERSION = "2.0"
CHECKPOINT_SCHEMA_VERSION = "2.0"

STAGES = (
    "source",
    "scope",
    "role",
    "relation",
    "normalize",
    "render",
    "audit",
    "publish",
)
SOURCE_TYPES = ("markdown", "docx", "text", "unknown")
AST_BLOCK_TYPES = ("paragraph", "image", "table", "opaque")
AST_CONTAINER_KINDS = (
    "document",
    "section",
    "appendix",
    "quote",
    "list",
    "list_item",
    "table_row",
    "table_cell",
)
AST_ROLES = ("title", "body", "heading", "caption", "note", "numbered_note", "unknown")
TABLE_TYPES = ("data", "code_sample", "callout", "layout", "unknown")
LIST_KINDS = ("ordered", "unordered")
CAPTION_TYPES = ("figure", "table")
REVISION_STATUSES = ("active", "superseded", "invalidated", "conflicted")
CHECKPOINT_STATUSES = (
    "created",
    "running",
    "waiting_external",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
)
PATCH_OPERATIONS = (
    "create_node",
    "update_node_semantics",
    "update_role",
    "move_node",
    "update_association",
    "delete_node",
    "update_overlay",
    "set_presentation_override",
)
SAFE_PROPERTY_SCOPES = (
    "semantics",
    "role",
    "level",
    "table_type",
    "caption_eligible",
    "layout_contract",
    "caption_type",
    "eligible",
    "region_kind",
    "semantic_scope",
    "protected",
    "reason",
    "protected_role",
    "target_id",
    "list_kind",
    "source_marker",
    "source_ordinal",
    "association",
    "children",
    "overlay",
    "presentation",
)


def overlay_property_scope(value: Mapping[str, Any]) -> list[str]:
    """Return the exact semantic fields written by an overlay decision."""

    scopes = sorted(str(key) for key in value)
    unsupported = [scope for scope in scopes if scope not in SAFE_PROPERTY_SCOPES]
    if unsupported:
        raise ValueError(f"unsupported overlay property {unsupported[0]!r}")
    return scopes


@dataclass(frozen=True)
class CapabilityContract:
    """Version and payload shape for one runtime capability."""

    name: str
    version: str
    value_kind: str = "any"


_CAPABILITY_CONTRACT_DEFINITIONS = (
    ("source_facts", "1.0", "mapping"),
    ("source_facts_summary", "1.0", "mapping"),
    ("source_facts_digest", "1.0", "string"),
    ("source_regions", "1.0", "mapping"),
    ("source_region_candidates", "2.0", "sequence"),
    ("source_region", "2.0", "mapping"),
    ("document_structure_candidates", "2.0", "sequence"),
    ("document_structure", "2.0", "mapping"),
    ("protected_content_candidates", "2.0", "sequence"),
    ("protected_content", "2.0", "mapping"),
    ("source_ast", AST_SCHEMA_VERSION, "mapping"),
    ("list_semantics_result", "2.0", "mapping"),
    ("list_semantics_candidates", "2.0", "sequence"),
    ("list_item_map", "2.0", "mapping"),
    ("list_semantics", "2.0", "mapping"),
    ("table_semantics_result", "2.0", "sequence"),
    ("table_semantics_candidates", "2.0", "sequence"),
    ("table_semantics", "2.0", "mapping"),
    ("caption_semantics_result", "2.0", "mapping"),
    ("caption_semantics_candidates", "2.0", "sequence"),
    ("caption_semantics", "2.0", "mapping"),
    ("appendix_semantics_result", "2.0", "mapping"),
    ("appendix_semantics_candidates", "2.0", "sequence"),
    ("appendix_semantics", "2.0", "mapping"),
    ("candidate_set", PATCH_SCHEMA_VERSION, "sequence"),
    ("review_candidates", PATCH_SCHEMA_VERSION, "sequence"),
    ("enhancement_result", "2.0", "mapping"),
    ("ledger_view", "2.0", "optional_mapping"),
    ("ledger_replay", "2.0", "optional_mapping"),
    ("decision_set", PATCH_SCHEMA_VERSION, "mapping"),
    ("transaction_commit", "2.0", "object"),
    ("source_ast_revision", AST_SCHEMA_VERSION, "integer"),
    ("source_ast_hierarchy", AST_SCHEMA_VERSION, "mapping"),
    ("normalized", "1.0", "mapping"),
    ("template_bundle", "1.0", "object"),
    ("resources", "1.0", "mapping"),
    ("render_plan", "1.0", "mapping"),
    ("render_result", "1.0", "mapping"),
    ("model_audit", "1.0", "mapping"),
    ("saved_output_audit", "1.0", "mapping"),
    ("appendix_audit", "1.0", "mapping"),
    ("audit_findings", "1.0", "sequence"),
    ("source_facts_integrity", "1.0", "mapping"),
    ("review_packet", "2.0", "mapping"),
    ("review_result", "2.0", "mapping"),
    ("acceptance", "1.0", "mapping"),
    ("published_output", "1.0", "optional_string"),
)

CAPABILITY_CONTRACTS = {
    name: CapabilityContract(name, version, value_kind)
    for name, version, value_kind in _CAPABILITY_CONTRACT_DEFINITIONS
}


def capability_contract(name: str) -> CapabilityContract:
    """Return a declared contract, using an unconstrained 1.0 fallback."""

    return CAPABILITY_CONTRACTS.get(
        str(name),
        CapabilityContract(str(name), "1.0", "any"),
    )


def validate_capability_payload(name: str, value: Any) -> None:
    """Validate the top-level shape carried by a runtime capability."""

    contract = capability_contract(name)
    kind = contract.value_kind
    valid = (
        kind == "any"
        or kind == "object"
        or kind == "mapping" and isinstance(value, Mapping)
        or kind == "optional_mapping" and (value is None or isinstance(value, Mapping))
        or kind == "sequence" and isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
        or kind == "string" and isinstance(value, str)
        or kind == "optional_string" and (value is None or isinstance(value, str))
        or kind == "integer" and isinstance(value, int) and not isinstance(value, bool)
    )
    if not valid:
        raise ContractError(
            "invalid_capability_payload",
            str(name),
            f"expected {kind}, got {type(value).__name__}",
            contract=f"Capability {contract.name} {contract.version}",
        )

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
JSONObject: TypeAlias = dict[str, JSONValue]


class ContractError(ValueError):
    """Structured contract failure with a stable code and JSON path."""

    def __init__(self, code: str, path: str, message: str, *, contract: str | None = None) -> None:
        self.code = code
        self.path = path
        self.contract = contract
        prefix = f"{contract}: " if contract else ""
        super().__init__(f"{prefix}{code} at {path}: {message}")


@dataclass(frozen=True)
class SourceRefData:
    source_id: str
    part: str
    content_sha256: str
    span: Mapping[str, Any] | None = None
    node_path: Sequence[str | int] | None = None


@dataclass(frozen=True)
class CandidateData:
    candidate_id: str
    module_id: str
    module_version: str
    target_source_refs: Sequence[Mapping[str, Any]]
    target_node_ids: Sequence[str]
    proposal: Mapping[str, Any]
    positive_evidence: Sequence[Mapping[str, Any]]
    negative_evidence: Sequence[Mapping[str, Any]]
    scope: Mapping[str, Any]
    constraints: Sequence[Mapping[str, Any]]
    rationale: str
    confidence: float | None = None


class Decision(TypedDict, total=False):
    decision_id: str
    candidate_id: str
    operation: str
    target_node_ids: list[str]
    expected_revision: int
    property_scope: list[str]
    value: dict[str, Any]
    preconditions: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    invalidates: list[str]


class Revision(TypedDict, total=False):
    revision_id: str
    parent_revision: int | None
    transaction_id: str
    target_source_refs: list[dict[str, Any]]
    target_node_ids: list[str]
    property_scope: list[str]
    decision: dict[str, Any]
    preconditions: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    origin: dict[str, Any]
    resolver_version: str
    status: str
    supersedes: list[str]


class SourceFacts(TypedDict, total=False):
    facts_schema_version: str
    source: dict[str, Any]
    parser: dict[str, Any]
    units: dict[str, dict[str, Any]]
    containers: dict[str, dict[str, Any]]
    children: dict[str, list[str]]
    evidence: dict[str, dict[str, Any]]
    resources: dict[str, dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    extensions: dict[str, Any]


class SourceAST(TypedDict, total=False):
    schema_version: str
    revision: int
    document: dict[str, Any]


class Patch(TypedDict, total=False):
    patch_schema_version: str
    patch_id: str
    base_revision: int
    phase: str
    candidates: list[dict[str, Any]]
    decisions: list[Decision]


class RunCheckpoint(TypedDict, total=False):
    checkpoint_schema_version: str
    run_id: str
    status: str
    resume_stage: str | None
    source_facts: dict[str, Any]
    decision_ledger: dict[str, Any]
    source_ast: dict[str, Any]
    normalized_ast: dict[str, Any] | None
    content_overlay: list[dict[str, Any]]
    presentation_override: list[dict[str, Any]]
    target_map: dict[str, Any]
    resource_summary: dict[str, Any]
    contracts: dict[str, str]
    modules: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    responses: list[dict[str, Any]]
    applied_transactions: list[dict[str, Any]]
    transaction_content_digests: dict[str, str]
    budget: dict[str, Any]
    round: int
    candidate_baseline_id: str
