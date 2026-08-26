"""Map every emitted policy-block family to its deterministic Standard owner."""

from __future__ import annotations

import re

BLOCK_FAMILIES = (
    ("FUNCTION_COMPLEXITY:",),
    (),
    (
        "ARCHITECTURE_GATE_NOT_EXECUTED",
        "ARCHITECTURE_PRODUCTION_COVERAGE:",
        "DEPENDENCY_INVERSION:",
        "FORBIDDEN_DOMAIN_DEPENDENCY:",
        "IMPORT_CYCLE:",
    ),
    (
        "INVALID_NEW_LOCATION_JUSTIFICATION:",
        "MISSING_NEW_LOCATION_JUSTIFICATION:",
        "NEW_LOCATION_GATE_COVERAGE:",
        "NEW_MODULE_OWNER_NOT_PREEXISTING:",
        "PARALLEL_PACKAGE:",
        "UNRESOLVED_MODULE_OWNER:",
        "VAGUE_PRODUCTION_LOCATION:",
    ),
    (
        "BASE_CAPTURE_DIGEST_MISMATCH",
        "CHANGED_CHARACTERIZATION_DEFINITION:",
        "CHANGED_GOLDEN_OUTPUT:",
        "CHARACTERIZATION_DEFINITION_MISMATCH:",
        "CHARACTERIZATION_DRIVER_IDENTITY_MISMATCH:",
        "CHARACTERIZATION_EXECUTION_FAILED:",
        "CHARACTERIZATION_FINGERPRINT_MISMATCH",
        "CHARACTERIZATION_REPLAY_DRIFT:",
        "GOLDEN_ARTIFACT_IDENTITY_MISMATCH:",
        "GOLDEN_BEHAVIOR_MISMATCH:",
        "HEAD_CAPTURE_DIGEST_MISMATCH",
        "HEAD_ONLY_CHARACTERIZATION_CLAIM",
        "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:",
        "INCOMPLETE_CHARACTERIZATION_EVIDENCE",
        "INVALID_ARTIFACT_IDENTITY",
        "MISSING_BASELINE",
        "MISSING_CHARACTERIZATION_COVERAGE:",
        "REMOVED_CHARACTERIZATION_SCENARIO:",
        "STALE_BASELINE_ARTIFACT",
        "STALE_POST_CHANGE_ARTIFACT",
        "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE",
    ),
    (
        "AUTHORIZATION_REPOSITORY_MISMATCH",
        "BROAD_AUTHORIZATION_REQUIRED",
        "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
        "INVALID_STRANGLER_SEQUENCE",
        "MALFORMED_OWNER_AUTHORIZATION",
        "MISSING_BOUNDED_PRODUCTION_TARGET",
        "MISSING_OWNER_AUTHORIZATION",
        "MISSING_RUNNABILITY_COVERAGE",
        "NON_RUNNABLE_LOGICAL_STEP",
        "STALE_OWNER_AUTHORIZATION",
        "STALE_RUNNABILITY_EVIDENCE",
        "UNAUTHENTICATED_OWNER_AUTHORIZATION",
        "UNAUTHENTICATED_RUNNABILITY_EVIDENCE",
        "UNFOCUSED_DIFF_SCOPE",
        "UNVERIFIABLE_BOUNDED_TARGET",
    ),
    (
        "CANDIDATE_CONTRACT_CHANGE",
        "CHANGED_FILE_GATE_COVERAGE:",
        "DECLARED_TOOL_NOT_EXECUTED:",
        "GATE_SCOPE_NARROWING",
        "HIGH_RISK_FILE_GATE_COVERAGE:",
        "MAXIMUM_EXCEEDS_APPROVED_THRESHOLD",
        "MISSING_QUALITY_COMMAND:",
        "MISSING_REQUIRED_ADAPTER:",
        "PRODUCTION_PATH_MOVED_OUTSIDE_SCOPE:",
        "PROFILE_SOURCE_MISMATCH:",
        "QUALITY_CHANGED_FILE_COVERAGE:",
        "QUALITY_CHANGED_FILE_NOT_ATTESTED:",
        "QUALITY_COMMAND_VECTOR_MISMATCH:",
        "QUALITY_COVERAGE_MAPPING_MISMATCH",
        "QUALITY_EXCLUSION_ADDED:",
        "QUALITY_GATE_FAILED:",
        "QUALITY_HIGH_RISK_FILE_COVERAGE:",
        "QUALITY_HIGH_RISK_FILE_NOT_ATTESTED:",
        "QUALITY_PRODUCTION_MANIFEST_MISMATCH",
        "QUALITY_PROOF_KIND_MISMATCH:",
        "QUALITY_SCOPE_NARROWING",
        "QUALITY_THRESHOLD_MISMATCH",
        "QUALITY_THRESHOLD_WEAKENING",
        "THRESHOLD_WEAKENING",
        "UNAPPROVED_ADAPTER:",
        "UNAPPROVED_QUALITY_COMMAND:",
        "UNTESTED_AREA:",
    ),
    (),
)

REVIEW_FIELD_OWNERS = {
    "architecture.dependency_direction": frozenset({3}),
    "architecture.reviewed_paths": frozenset({3}),
    "behavior.intended_behavior": frozenset({5}),
    "behavior.proof": frozenset({5}),
    "characterization.captured_behavior": frozenset({5}),
    "characterization.proof": frozenset({5}),
    "human_review.cohesion": frozenset({4}),
    "human_review.intended_behavior": frozenset({5}),
    "human_review.naming": frozenset({1}),
    "human_review.reviewability": frozenset({1}),
    "incremental_refactor.completed_step": frozenset({6}),
    "incremental_refactor.target": frozenset({6}),
    "responsibility_boundary.does_not_own": frozenset({4}),
    "responsibility_boundary.owns": frozenset({4}),
    "responsibility_boundary.path": frozenset({4}),
    "review_handoff.remaining_risks": frozenset({8}),
    "review_handoff.summary": frozenset({8}),
    "separation_of_concerns.after": frozenset({2}),
    "separation_of_concerns.before": frozenset({2}),
    "separation_of_concerns.boundaries": frozenset({2}),
}

_REVIEW_PREFIXES = (
    "MISSING_REVIEW_EVIDENCE:",
    "MALFORMED_REVIEW_EVIDENCE:",
    "INSUFFICIENT_REVIEW_EVIDENCE:",
)
REVIEW_STANDARDS = frozenset({1, 2, 3, 4, 5, 6, 8})
_REVIEW_SECTIONS = frozenset(field.partition(".")[0] for field in REVIEW_FIELD_OWNERS)
_REVIEW_ROOT_FIELDS = frozenset({"schema_version", *_REVIEW_SECTIONS})
_MODULE_BOUNDARY_FIELDS = frozenset({"basis", "justification", "owner_path", "path"})
_MODULE_BOUNDARY_LOCATION = re.compile(r"module_boundaries\[(0|[1-9][0-9]*)\](?:\.(.*))?\Z")
_SEPARATION_BOUNDARY_FIELDS = frozenset({"after", "before", "kind", "path", "symbol"})
_SEPARATION_BOUNDARY_LOCATION = re.compile(
    r"separation_of_concerns\.boundaries\[(0|[1-9][0-9]*)\](?:\.(.*))?\Z"
)
_QUALITY_TECHNICAL_PREFIXES = (
    "DUPLICATE_QUALITY_",
    "MALFORMED_QUALITY_",
    "MISSING_QUALITY_",
    "QUALITY_",
    "RELATIVE_QUALITY_",
    "SELF_DECLARED_QUALITY_",
    "UNAPPROVED_QUALITY_",
    "UNAUTHENTICATED_QUALITY_",
    "UNTRUSTED_QUALITY_",
)
ALL_STANDARDS = frozenset(range(1, 9))
COMPLEXITY_TECHNICAL_STANDARDS = frozenset({1, 2, 3, 4, 7, 8})
_EXACT_TECHNICAL_DEPENDENCIES = {
    "CHARACTERIZATION_RESULT_BINDING_MISMATCH": (
        "characterization-result",
        frozenset({5, 6}),
    ),
    "COMPLEXITY_RESULT_BINDING_MISMATCH": ("complexity-result", ALL_STANDARDS),
    "GATE_INSTALL_FAILURE": ("gate-install", ALL_STANDARDS),
    "MALFORMED_CHARACTERIZATION_RESULT": (
        "characterization-result",
        frozenset({5, 6}),
    ),
    "MALFORMED_COMPLEXITY_RESULT": ("complexity-result", ALL_STANDARDS),
    "MALFORMED_QUALITY_PROVENANCE": (
        "quality-profile:artifact-binding",
        frozenset({7}),
    ),
    "MALFORMED_EXTERNAL_QUALITY_ARTIFACT": (
        "quality-profile:artifact-binding",
        frozenset({7}),
    ),
    "MALFORMED_QUALITY_RESULT_BINDING": (
        "quality-profile:artifact-binding",
        frozenset({7}),
    ),
    "MALFORMED_REFACTOR_RESULT": ("refactor-policy-result", frozenset({6})),
    "MISSING_CHARACTERIZATION_RESULT": (
        "characterization-result",
        frozenset({5, 6}),
    ),
    "MISSING_COMPLEXITY_RESULT": ("complexity-result", ALL_STANDARDS),
    "MISSING_QUALITY_PROVENANCE": (
        "quality-profile:artifact-binding",
        frozenset({7}),
    ),
    "MISSING_EXTERNAL_QUALITY_ARTIFACT": (
        "quality-profile:artifact-binding",
        frozenset({7}),
    ),
    "MISSING_REFACTOR_RESULT": ("refactor-policy-result", frozenset({6})),
    "QUALITY_RESULT_BINDING_MISMATCH": (
        "quality-profile:artifact-binding",
        frozenset({7}),
    ),
    "QUALITY_ARTIFACT_IDENTITY_MISMATCH": (
        "quality-profile:artifact-binding",
        frozenset({7}),
    ),
    "REFACTOR_RESULT_BINDING_MISMATCH": (
        "refactor-policy-result",
        frozenset({6}),
    ),
}
_SOURCE_BLOCK_ALLOWED = {
    "characterization-result:policy-blocks": frozenset({5}),
    "complexity-result:policy-blocks": ALL_STANDARDS,
    "refactor-policy-result:policy-blocks": frozenset({6}),
}
_SOURCE_BLOCK_DEPENDENTS = {
    "characterization-result:policy-blocks": frozenset({5, 6}),
    "complexity-result:policy-blocks": ALL_STANDARDS,
    "refactor-policy-result:policy-blocks": frozenset({6}),
}


def _matches(block: str, family: str) -> bool:
    return block.startswith(family) if family.endswith(":") else block == family


def _review_defect(block: str) -> tuple[str, str] | None:
    prefix = next((item for item in _REVIEW_PREFIXES if block.startswith(item)), None)
    return (prefix.partition("_")[0], block.removeprefix(prefix)) if prefix else None


def _emitted_review_location(kind: str, location: str) -> bool:
    if location == "document":
        return kind in {"MISSING", "MALFORMED"}
    if location == "schema_version":
        return kind == "MALFORMED"
    if location.startswith("review_evidence."):
        root = location.removeprefix("review_evidence.")
        if kind == "MISSING":
            return root in _REVIEW_ROOT_FIELDS
        return kind == "MALFORMED" and root not in {
            *_REVIEW_ROOT_FIELDS,
            "module_boundaries",
        }
    if location == "module_boundaries":
        return kind == "MALFORMED"
    if location == "module_boundaries.path":
        return kind == "MALFORMED"
    if match := (
        _MODULE_BOUNDARY_LOCATION.fullmatch(location)
        or _SEPARATION_BOUNDARY_LOCATION.fullmatch(location)
    ):
        field = match.group(2)
        fields = (
            _MODULE_BOUNDARY_FIELDS
            if location.startswith("module_boundaries")
            else _SEPARATION_BOUNDARY_FIELDS
        )
        return kind == "MALFORMED" if field not in fields else True
    section, separator, _ = location.partition(".")
    if section not in _REVIEW_SECTIONS:
        return False
    if not separator:
        return kind == "MALFORMED"
    return location in REVIEW_FIELD_OWNERS or (
        kind == "MALFORMED" and not location.startswith("separation_of_concerns.boundaries")
    )


def review_owners(block: str) -> frozenset[int]:
    """Return exact owners for one structured-review defect."""
    defect = _review_defect(block)
    if defect is None or not _emitted_review_location(*defect):
        return frozenset()
    kind, location = defect
    if location in {"document", "schema_version"}:
        return REVIEW_STANDARDS
    if location.startswith("review_evidence."):
        if kind == "MALFORMED":
            return REVIEW_STANDARDS
        location = location.removeprefix("review_evidence.")
    if location == "module_boundaries" or location.startswith("module_boundaries["):
        return frozenset({4})
    if location == "module_boundaries.path":
        return frozenset({4})
    if exact := REVIEW_FIELD_OWNERS.get(location):
        return exact
    section = location.partition(".")[0]
    return frozenset(
        owner
        for field, owners_for_field in REVIEW_FIELD_OWNERS.items()
        if field.startswith(f"{section}.")
        for owner in owners_for_field
    )


def owners(block: str) -> frozenset[int]:
    """Return every declared Standard owner for one emitted policy block."""
    matches = {
        standard
        for standard, families in enumerate(BLOCK_FAMILIES, start=1)
        if any(_matches(block, family) for family in families)
    }
    matches.update(review_owners(block))
    return frozenset(matches)


def shared_dependency(block: str) -> tuple[str, frozenset[int]] | None:
    """Name an intentional multi-Standard structured-review dependency."""
    block_owners = review_owners(block)
    if len(block_owners) < 2:
        return None
    defect = _review_defect(block)
    assert defect is not None
    kind, location = defect
    document = location in {"document", "schema_version"} or (
        kind == "MALFORMED" and location.startswith("review_evidence.")
    )
    location = location.removeprefix("review_evidence.")
    dependency = (
        "structured-review-document"
        if document
        else f"structured-review-section:{location.partition('.')[0]}"
    )
    return dependency, block_owners


def technical_owners(code: str) -> frozenset[int]:
    """Return one lane for technical codes with stable, unique ownership."""
    raw = code.removeprefix("COMPLEXITY_RESULT:")
    if raw in {
        "COMPLEXITY_AMBIGUOUS_FUNCTION_IDENTITY",
        "COMPLEXITY_EMPTY_FUNCTION_DELTA",
        "COMPLEXITY_MISSING_AST_SPAN",
        "COMPLEXITY_SOURCE_ENCODING",
        "COMPLEXITY_SOURCE_UNAVAILABLE",
        "COMPLEXITY_SYNTAX_ERROR",
        "INCOMPLETE_REMAINING_GAP",
        "MCCABE_GRAPH_MISMATCH",
        "MISSING_FUNCTION_BODY",
        "PROFILE_NODE_MISMATCH",
    } or raw.startswith(("C901_", "RUFF_")):
        return frozenset({1})
    if raw.startswith("ARCHITECTURE_"):
        return frozenset({3, 4})
    if raw == "SEPARATION_BOUNDARY_DERIVATION_FAILURE":
        return frozenset({2})
    if raw == "REVIEW_EVIDENCE_UNAVAILABLE":
        return REVIEW_STANDARDS
    if raw == "INVALID_WORKFLOW_SHA" or raw.startswith(_QUALITY_TECHNICAL_PREFIXES):
        return frozenset({4, 7})
    return frozenset()


def expected_technical_dependency(code: str, dependency: str) -> tuple[str, frozenset[int]] | None:
    """Derive the only valid dependency and affected lanes for one technical code."""
    if exact := _EXACT_TECHNICAL_DEPENDENCIES.get(code):
        return exact
    if code.startswith("COMPLEXITY_RESULT:"):
        return (
            "complexity-result:technical-errors",
            technical_owners(code) or COMPLEXITY_TECHNICAL_STANDARDS,
        )
    if code.startswith("UNKNOWN_STANDARD_BLOCK_OWNER:"):
        block = code.removeprefix("UNKNOWN_STANDARD_BLOCK_OWNER:")
        return ("standard-block-ownership", ALL_STANDARDS) if not owners(block) else None
    if code.startswith("AMBIGUOUS_STANDARD_BLOCK_OWNER:"):
        block = code.removeprefix("AMBIGUOUS_STANDARD_BLOCK_OWNER:")
        return (
            ("standard-block-ownership", ALL_STANDARDS)
            if len(owners(block)) > 1 and shared_dependency(block) is None
            else None
        )
    if code.startswith("STANDARD_BLOCK_SOURCE_MISMATCH:"):
        block = code.removeprefix("STANDARD_BLOCK_SOURCE_MISMATCH:")
        allowed = _SOURCE_BLOCK_ALLOWED.get(dependency)
        block_owners = owners(block)
        if allowed and block_owners and not block_owners.issubset(allowed):
            return dependency, _SOURCE_BLOCK_DEPENDENTS[dependency] | block_owners
    return None
