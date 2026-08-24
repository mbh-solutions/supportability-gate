"""Own the stable mapping from policy-block vocabulary to one Standard."""

from __future__ import annotations

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
    "architecture.dependency_direction": 3,
    "architecture.reviewed_paths": 3,
    "behavior.intended_behavior": 5,
    "behavior.proof": 5,
    "characterization.captured_behavior": 5,
    "characterization.proof": 5,
    "human_review.cohesion": 4,
    "human_review.intended_behavior": 5,
    "human_review.naming": 1,
    "human_review.reviewability": 1,
    "incremental_refactor.completed_step": 6,
    "incremental_refactor.target": 6,
    "responsibility_boundary.does_not_own": 4,
    "responsibility_boundary.owns": 4,
    "responsibility_boundary.path": 4,
    "review_handoff.remaining_risks": 8,
    "review_handoff.summary": 8,
    "separation_of_concerns.after": 2,
    "separation_of_concerns.before": 2,
}


def review_block_standard(block: str) -> int | None:
    """Return the Standard for one producer-generated review block."""
    prefixes = (
        "MISSING_REVIEW_EVIDENCE:",
        "MALFORMED_REVIEW_EVIDENCE:",
        "INSUFFICIENT_REVIEW_EVIDENCE:",
    )
    prefix = next((item for item in prefixes if block.startswith(item)), None)
    if prefix is None:
        return None
    location = block.removeprefix(prefix)
    if location == "module_boundaries.path" or location.startswith("module_boundaries["):
        return 4
    if location == "module_boundaries":
        return 4
    return REVIEW_FIELD_OWNERS.get(location)


def owners(block: str) -> set[int]:
    """Return every declared Standard owner for one policy block."""
    matches = {
        standard
        for standard, families in enumerate(BLOCK_FAMILIES, start=1)
        if any(
            block.startswith(family) if family.endswith(":") else block == family
            for family in families
        )
    }
    if review_owner := review_block_standard(block):
        matches.add(review_owner)
    return matches
