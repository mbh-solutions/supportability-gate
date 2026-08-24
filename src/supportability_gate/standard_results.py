"""Compose one independently visible result per Supportability Standard."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from supportability_gate import clause_inventory, codex_review

SCHEMA_VERSION = "standard-results.v1"
RESULTS = frozenset({"PASS", "BLOCK", "TECHNICAL_FAILURE"})
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
CHECK_CONTEXTS = (
    "Supportability 1 - Cyclomatic Complexity",
    "Supportability 2 - Separation of Concerns",
    "Supportability 3 - Dependency Direction",
    "Supportability 4 - Domain Modularity",
    "Supportability 5 - Characterization",
    "Supportability 6 - Incremental Refactor",
    "Supportability 7 - Quality Gates",
    "Supportability 8 - Review Handoff",
)
EVIDENCE_SOURCES = (
    (
        "complexity-result.json:functions",
        "complexity-result.json:ruff_diagnostics",
        "complexity-result.json:review_evidence.human_review",
    ),
    ("complexity-result.json:review_evidence.separation_of_concerns",),
    (
        "complexity-result.json:architecture",
        "complexity-result.json:dependency_direction_explanation",
        "complexity-result.json:review_evidence.architecture",
    ),
    (
        "complexity-result.json:modularity",
        "complexity-result.json:review_evidence.responsibility_boundary",
        "complexity-result.json:review_evidence.module_boundaries",
    ),
    (
        "characterization-result.json",
        "complexity-result.json:review_evidence.behavior",
        "complexity-result.json:review_evidence.characterization",
    ),
    (
        "refactor-policy-result.json",
        "complexity-result.json:review_evidence.incremental_refactor",
    ),
    (
        "complexity-result.json:quality_profile",
        "quality-provenance.json",
        "complexity-result.json:gate_coverage",
        "complexity-result.json:changed_files",
    ),
    ("complexity-result.json:review_evidence.review_handoff",),
)
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


class StandardResultsError(ValueError):
    """One fail-closed standard-result contract error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RunIdentity:
    """Exact producer and pull-request identity."""

    repository: str
    repository_id: int
    base_sha: str
    head_sha: str
    workflow_sha: str
    run_id: int
    run_attempt: int


@dataclass(frozen=True)
class DeterministicState:
    """Blocks and applicability derived only from existing evidence."""

    blocks: dict[int, list[str]]
    applicable: dict[int, bool]
    quality_artifact: dict[str, object] | None
    technical_errors: tuple[str, ...]


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _string_list(value: object, code: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StandardResultsError(code)
    if len(value) != len(set(value)):
        raise StandardResultsError(code)
    return value


def _family_owners(block: str) -> set[int]:
    return {
        standard
        for standard, families in enumerate(BLOCK_FAMILIES, start=1)
        if any(
            block.startswith(family) if family.endswith(":") else block == family
            for family in families
        )
    }


def _require_owner(block: str, expected: int | None = None) -> int:
    owners = _family_owners(block)
    if review_owner := _review_owner(block):
        owners.add(review_owner)
    if len(owners) != 1:
        kind = "UNKNOWN" if not owners else "MULTIPLE"
        raise StandardResultsError(f"{kind}_STANDARD_BLOCK_OWNER:{block}")
    owner = owners.pop()
    if expected is not None and owner != expected:
        raise StandardResultsError(f"STANDARD_BLOCK_SOURCE_MISMATCH:{block}")
    return owner


def _validate_identity(identity: RunIdentity) -> None:
    if (
        identity.repository.count("/") != 1
        or identity.repository_id < 1
        or SHA40.fullmatch(identity.base_sha) is None
        or SHA40.fullmatch(identity.head_sha) is None
        or SHA40.fullmatch(identity.workflow_sha) is None
        or identity.run_id < 1
        or identity.run_attempt < 1
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_IDENTITY")


def _result_blocks(value: dict[str, Any], schema: str, code: str, standard: int) -> list[str]:
    blocks = _string_list(value.get("policy_blocks"), code)
    result = value.get("overall_result")
    if value.get("schema_version") != schema or result not in {"PASS", "BLOCK"}:
        raise StandardResultsError(code)
    if (result == "PASS") == bool(blocks):
        raise StandardResultsError(code)
    for block in blocks:
        _require_owner(block, standard)
    return blocks


def _validate_quality(
    complexity: dict[str, Any], provenance: dict[str, Any], identity: RunIdentity
) -> dict[str, object]:
    quality = complexity.get("quality_profile")
    if not isinstance(quality, dict):
        raise StandardResultsError("MALFORMED_QUALITY_RESULT_BINDING")
    expected_quality = (
        identity.base_sha,
        identity.head_sha,
        f"github.com/{identity.repository}",
        identity.workflow_sha,
    )
    actual_quality = (
        quality.get("base_sha"),
        quality.get("head_sha"),
        quality.get("repository_remote"),
        quality.get("workflow_sha"),
    )
    expected_provenance = (
        identity.repository,
        str(identity.repository_id),
        str(identity.run_id),
        str(identity.run_attempt),
        "quality-profile",
    )
    actual_provenance = tuple(
        provenance.get(name)
        for name in ("repository", "repository_id", "run_id", "run_attempt", "job")
    )
    if actual_quality != expected_quality or actual_provenance != expected_provenance:
        raise StandardResultsError("QUALITY_RESULT_BINDING_MISMATCH")
    artifact_id = provenance.get("artifact_id")
    digest = provenance.get("artifact_digest")
    capture = provenance.get("capture_sha256")
    if (
        not isinstance(artifact_id, str)
        or not artifact_id.isdigit()
        or not isinstance(digest, str)
        or SHA64.fullmatch(digest) is None
        or not isinstance(capture, str)
        or SHA64.fullmatch(capture) is None
    ):
        raise StandardResultsError("MALFORMED_QUALITY_RESULT_BINDING")
    return {"capture_sha256": capture, "digest": digest, "id": int(artifact_id)}


def _validate_complexity(
    value: dict[str, Any], identity: RunIdentity
) -> tuple[list[str], list[dict[str, Any]], tuple[str, ...]]:
    expected = (identity.base_sha, identity.head_sha, f"github.com/{identity.repository}")
    actual = (value.get("base_sha"), value.get("head_sha"), value.get("repository_remote"))
    technical = value.get("technical_errors")
    functions = value.get("functions")
    if (
        value.get("schema_version") != "1.0"
        or value.get("standard_sha256") != clause_inventory.STANDARD_SHA256
        or actual != expected
        or not isinstance(technical, list)
        or not isinstance(functions, list)
        or any(not isinstance(item, dict) for item in technical)
        or any(not isinstance(item, dict) for item in functions)
    ):
        raise StandardResultsError("MALFORMED_COMPLEXITY_RESULT")
    codes = tuple(
        sorted(
            {
                f"COMPLEXITY_RESULT:{item.get('code')}"
                for item in technical
                if isinstance(item.get("code"), str)
            }
        )
    )
    if technical and len(codes) != len(technical):
        raise StandardResultsError("MALFORMED_COMPLEXITY_RESULT")
    blocks = _string_list(value.get("policy_blocks"), "MALFORMED_COMPLEXITY_RESULT")
    result = value.get("overall_result")
    blocked_functions = [item for item in functions if item.get("decision") == "BLOCK"]
    expected_result = (
        "TECHNICAL_FAILURE" if technical else "BLOCK" if blocks or blocked_functions else "PASS"
    )
    if result not in RESULTS or result != expected_result:
        raise StandardResultsError("MALFORMED_COMPLEXITY_RESULT")
    return blocks, functions, codes


def _validate_characterization(value: dict[str, Any], identity: RunIdentity) -> list[str]:
    expected = (
        f"github.com/{identity.repository}",
        identity.base_sha,
        identity.head_sha,
        identity.workflow_sha,
    )
    actual = tuple(
        value.get(name) for name in ("repository", "base_sha", "head_sha", "workflow_sha")
    )
    if actual != expected:
        raise StandardResultsError("CHARACTERIZATION_RESULT_BINDING_MISMATCH")
    return _result_blocks(
        value, "characterization-result.v1", "MALFORMED_CHARACTERIZATION_RESULT", 5
    )


def _validate_refactor(
    value: dict[str, Any], characterization: dict[str, Any], identity: RunIdentity
) -> list[str]:
    expected = (identity.repository, identity.base_sha, identity.head_sha)
    actual = tuple(value.get(name) for name in ("repository", "base_sha", "head_sha"))
    expected_hash = hashlib.sha256(_canonical(characterization)).hexdigest()
    if actual != expected or value.get("characterization_sha256") != expected_hash:
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    if type(value.get("applicable")) is not bool:
        raise StandardResultsError("MALFORMED_REFACTOR_RESULT")
    return _result_blocks(value, "refactor-policy-result.v1", "MALFORMED_REFACTOR_RESULT", 6)


def _review_owner(block: str) -> int | None:
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


def _nested_blocks(value: object, code: str, standard: int) -> set[str]:
    if not isinstance(value, dict):
        raise StandardResultsError(code)
    blocks = set(_string_list(value.get("blocks"), code))
    for block in blocks:
        _require_owner(block, standard)
    return blocks


def _function_blocks(functions: list[dict[str, Any]]) -> list[str]:
    blocks: list[str] = []
    for item in functions:
        if item.get("decision") not in {"PASS", "BLOCK"}:
            raise StandardResultsError("MALFORMED_COMPLEXITY_RESULT")
        if item.get("decision") == "PASS":
            continue
        metric = item.get("head") or item.get("base")
        name = metric.get("qualified_name") if isinstance(metric, dict) else None
        if not isinstance(name, str) or not name:
            raise StandardResultsError("MALFORMED_COMPLEXITY_RESULT")
        blocks.append(f"FUNCTION_COMPLEXITY:{name}")
    return blocks


def _deterministic_state(
    complexity: dict[str, Any],
    characterization: dict[str, Any],
    refactor: dict[str, Any],
    quality_provenance: dict[str, Any],
    identity: RunIdentity,
) -> DeterministicState:
    _validate_identity(identity)
    policy_blocks, functions, technical = _validate_complexity(complexity, identity)
    quality_artifact = _validate_quality(complexity, quality_provenance, identity)
    characterization_blocks = _validate_characterization(characterization, identity)
    refactor_blocks = _validate_refactor(refactor, characterization, identity)
    architecture = _nested_blocks(
        complexity.get("architecture"), "MALFORMED_ARCHITECTURE_RESULT", 3
    )
    modularity = _nested_blocks(complexity.get("modularity"), "MALFORMED_MODULARITY_RESULT", 4)
    blocks: dict[int, list[str]] = {standard: [] for standard in range(1, 9)}
    blocks[1].extend(_function_blocks(functions))
    blocks[5].extend(characterization_blocks)
    blocks[6].extend(refactor_blocks)
    shared = list(technical)
    for block in policy_blocks:
        try:
            blocks[_require_owner(block)].append(block)
        except StandardResultsError as error:
            shared.append(error.code)
    if architecture != set(blocks[3]) or modularity != set(blocks[4]):
        shared.append("NESTED_STANDARD_RESULT_BINDING_MISMATCH")
    changed = complexity.get("changed_files")
    if not isinstance(changed, list):
        raise StandardResultsError("MALFORMED_COMPLEXITY_RESULT")
    applicable = {standard: bool(changed) for standard in range(1, 9)}
    applicable[6] = bool(refactor["applicable"])
    return DeterministicState(
        blocks,
        applicable,
        quality_artifact,
        tuple(sorted(set(shared))),
    )


def _codex_rows(
    evidence: tuple[codex_review.FocusedReviewEvidence, ...],
) -> dict[str, dict[str, object]]:
    by_focus = {item.focus: item for item in evidence}
    if len(by_focus) != len(evidence) or any(
        focus not in codex_review.FOCUSES for focus in by_focus
    ):
        raise StandardResultsError("MALFORMED_CODEX_REVIEW_BINDING")
    artifacts = [item.completion for item in evidence if item.completion]
    identities = [(item.kind, item.artifact_id) for item in artifacts]
    if len(identities) != len(set(identities)):
        raise StandardResultsError("REUSED_FOCUSED_CODEX_REVIEW_EVIDENCE")
    rows: dict[str, dict[str, object]] = {}
    for focus in codex_review.FOCUSES:
        item = by_focus.get(focus)
        completion = item.completion if item else None
        rows[focus] = {
            "completion": (
                {
                    "completed_at": completion.completed_at.isoformat(),
                    "id": completion.artifact_id,
                    "kind": completion.kind,
                }
                if completion
                else None
            ),
            "focus": focus,
            "request_id": item.request_id if item else None,
            "requested_at": item.requested_at.isoformat() if item else None,
        }
    return rows


def _codex_blocks(rows: dict[str, dict[str, object]], prefix: str) -> dict[int, list[str]]:
    return {
        int(focus): [f"{prefix}_{focus}"]
        for focus in codex_review.FOCUSES
        if rows[focus]["completion"] is None
    }


def _codex_payload(
    evidence: tuple[codex_review.FocusedReviewEvidence, ...],
    error: codex_review.CodexReviewError | None,
) -> tuple[dict[str, dict[str, object]], dict[int, list[str]], tuple[str, ...]]:
    try:
        rows = _codex_rows(evidence)
    except StandardResultsError as binding_error:
        return {}, {}, (binding_error.code,)
    blocks: dict[int, list[str]] = {standard: [] for standard in range(1, 9)}
    if error is None and all(rows[focus]["completion"] for focus in codex_review.FOCUSES):
        return rows, blocks, ()
    code = error.code if error else "MALFORMED_CODEX_REVIEW_BINDING"
    prefixes = ("FOCUSED_CODEX_REVIEW_PENDING", "MISSING_FOCUSED_CODEX_REVIEW_REQUEST")
    prefix = next((item for item in prefixes if code.startswith(f"{item}_")), None)
    if prefix and (focused_blocks := _codex_blocks(rows, prefix)):
        blocks.update(focused_blocks)
        return rows, blocks, ()
    return rows, blocks, (code,)


def _empty_codex() -> dict[str, dict[str, object]]:
    return {
        focus: {"completion": None, "focus": focus, "request_id": None, "requested_at": None}
        for focus in codex_review.FOCUSES
    }


def _payload(
    identity: RunIdentity,
    state: DeterministicState,
    codex_rows: dict[str, dict[str, object]],
    codex_blocks: dict[int, list[str]],
    codex_technical: tuple[str, ...],
) -> dict[str, object]:
    technical = tuple(sorted(set((*state.technical_errors, *codex_technical))))
    entries = []
    for standard, context in enumerate(CHECK_CONTEXTS, start=1):
        blocks = sorted(set((*state.blocks[standard], *codex_blocks.get(standard, []))))
        result = "TECHNICAL_FAILURE" if technical else "BLOCK" if blocks else "PASS"
        entries.append(
            {
                "applicable": state.applicable[standard],
                "blocks": blocks,
                "check_context": context,
                "codex_review": codex_rows.get(str(standard), _empty_codex()[str(standard)]),
                "evidence_sources": list(EVIDENCE_SOURCES[standard - 1]),
                "result": result,
                "standard": standard,
                "technical_errors": list(technical),
            }
        )
    return {
        "base_sha": identity.base_sha,
        "entries": entries,
        "head_sha": identity.head_sha,
        "quality_artifact": state.quality_artifact,
        "repository": identity.repository,
        "repository_id": identity.repository_id,
        "run_attempt": identity.run_attempt,
        "run_id": identity.run_id,
        "schema_version": SCHEMA_VERSION,
        "standard_sha256": clause_inventory.STANDARD_SHA256,
        "workflow_sha": identity.workflow_sha,
    }


def compose_results(
    complexity: dict[str, Any],
    characterization: dict[str, Any],
    refactor: dict[str, Any],
    quality_provenance: dict[str, Any],
    identity: RunIdentity,
    codex_evidence: tuple[codex_review.FocusedReviewEvidence, ...],
    codex_error: codex_review.CodexReviewError | None = None,
) -> dict[str, object]:
    """Compose exactly eight results without rerunning target analysis."""
    try:
        state = _deterministic_state(
            complexity, characterization, refactor, quality_provenance, identity
        )
    except StandardResultsError as error:
        state = DeterministicState(
            {standard: [] for standard in range(1, 9)},
            {standard: True for standard in range(1, 9)},
            None,
            (error.code,),
        )
    codex_rows, codex_blocks, codex_technical = _codex_payload(codex_evidence, codex_error)
    payload = _payload(
        identity,
        state,
        codex_rows or _empty_codex(),
        codex_blocks,
        codex_technical,
    )
    return payload
