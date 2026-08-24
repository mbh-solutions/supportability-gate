"""Compose one independently visible result per Supportability Standard."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from supportability_gate import clause_inventory, focused_review, standard_block_ownership

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


@dataclass(frozen=True)
class ValidatedCodexBinding:
    """Named fields from one validated focused-review entry."""

    request_id: int | None
    requested_at: datetime | None
    artifact: tuple[str, int] | None
    completed_at: datetime | None


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _string_list(value: object, code: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StandardResultsError(code)
    if len(value) != len(set(value)):
        raise StandardResultsError(code)
    return value


def _require_owner(block: str, expected: int) -> None:
    owners = standard_block_ownership.owners(block)
    if len(owners) != 1:
        kind = "UNKNOWN" if not owners else "MULTIPLE"
        raise StandardResultsError(f"{kind}_STANDARD_BLOCK_OWNER:{block}")
    if owners.pop() != expected:
        raise StandardResultsError(f"STANDARD_BLOCK_SOURCE_MISMATCH:{block}")


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


def _standard_blocks(value: object, policy_blocks: list[str]) -> dict[int, list[str]]:
    if not isinstance(value, list) or len(value) != 8:
        raise StandardResultsError("MALFORMED_STANDARD_BLOCK_BINDING")
    blocks: dict[int, list[str]] = {}
    for standard, row in enumerate(value, start=1):
        if not isinstance(row, dict) or set(row) != {"blocks", "standard"}:
            raise StandardResultsError("MALFORMED_STANDARD_BLOCK_BINDING")
        if row.get("standard") != standard:
            raise StandardResultsError("MALFORMED_STANDARD_BLOCK_BINDING")
        blocks[standard] = _string_list(row.get("blocks"), "MALFORMED_STANDARD_BLOCK_BINDING")
        for block in blocks[standard]:
            _require_owner(block, standard)
    flattened = [block for standard in range(1, 9) for block in blocks[standard]]
    duplicates = sorted({block for block in flattened if flattened.count(block) > 1})
    if duplicates:
        raise StandardResultsError(f"MULTIPLE_STANDARD_BLOCK_OWNER:{duplicates[0]}")
    missing = sorted(set(policy_blocks) - set(flattened))
    if missing:
        raise StandardResultsError(f"UNKNOWN_STANDARD_BLOCK_OWNER:{missing[0]}")
    extra = sorted(set(flattened) - set(policy_blocks))
    if extra:
        raise StandardResultsError(f"STANDARD_BLOCK_SOURCE_MISMATCH:{extra[0]}")
    return blocks


def _validate_complexity(
    value: dict[str, Any], identity: RunIdentity
) -> tuple[dict[int, list[str]], list[dict[str, Any]], tuple[str, ...]]:
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
    if technical and value.get("standard_blocks") == []:
        return {standard: [] for standard in range(1, 9)}, functions, codes
    return _standard_blocks(value.get("standard_blocks"), blocks), functions, codes


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
        decision = item.get("decision")
        if decision not in {"PASS", "PASS_PROGRESSIVE", "DELETED", "BLOCK"}:
            raise StandardResultsError("MALFORMED_COMPLEXITY_RESULT")
        if decision != "BLOCK":
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
    standard_blocks, functions, technical = _validate_complexity(complexity, identity)
    if technical:
        return DeterministicState(
            standard_blocks,
            {standard: True for standard in range(1, 9)},
            None,
            technical,
        )
    quality_artifact = _validate_quality(complexity, quality_provenance, identity)
    characterization_blocks = _validate_characterization(characterization, identity)
    refactor_blocks = _validate_refactor(refactor, characterization, identity)
    architecture = _nested_blocks(
        complexity.get("architecture"), "MALFORMED_ARCHITECTURE_RESULT", 3
    )
    modularity = _nested_blocks(complexity.get("modularity"), "MALFORMED_MODULARITY_RESULT", 4)
    blocks = {standard: list(standard_blocks[standard]) for standard in range(1, 9)}
    blocks[1].extend(_function_blocks(functions))
    blocks[5].extend(characterization_blocks)
    blocks[6].extend(refactor_blocks)
    shared = list(technical)
    if not architecture.issubset(set(blocks[3])) or not modularity.issubset(set(blocks[4])):
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
    evidence: tuple[focused_review.FocusedReviewEvidence, ...],
) -> dict[str, dict[str, object]]:
    by_focus = {item.focus: item for item in evidence}
    if len(by_focus) != len(evidence) or any(
        focus not in focused_review.FOCUSES for focus in by_focus
    ):
        raise StandardResultsError("MALFORMED_CODEX_REVIEW_BINDING")
    artifacts = [item.completion for item in evidence if item.completion]
    identities = [(item.kind, item.artifact_id) for item in artifacts]
    if len(identities) != len(set(identities)):
        raise StandardResultsError("REUSED_FOCUSED_CODEX_REVIEW_EVIDENCE")
    rows: dict[str, dict[str, object]] = {}
    for focus in focused_review.FOCUSES:
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
    if prefix == "MISSING_FOCUSED_CODEX_REVIEW_REQUEST":
        return {
            int(focus): [
                f"{prefix if rows[focus]['request_id'] is None else 'FOCUSED_CODEX_REVIEW_PENDING'}_{focus}"
            ]
            for focus in focused_review.FOCUSES
            if rows[focus]["completion"] is None
        }
    return {
        int(focus): [f"{prefix}_{focus}"]
        for focus in focused_review.FOCUSES
        if rows[focus]["completion"] is None
    }


def _is_prefix(sequence: tuple[bool, ...]) -> bool:
    return sequence == tuple(sorted(sequence, reverse=True))


def _codex_payload(
    evidence: tuple[focused_review.FocusedReviewEvidence, ...],
    error_code: str | None,
) -> tuple[dict[str, dict[str, object]], dict[int, list[str]], tuple[str, ...]]:
    try:
        rows = _codex_rows(evidence)
    except StandardResultsError as binding_error:
        return {}, {}, (binding_error.code,)
    sequences = (
        tuple(rows[focus]["request_id"] is not None for focus in focused_review.FOCUSES),
        tuple(rows[focus]["completion"] is not None for focus in focused_review.FOCUSES),
    )
    if any(not _is_prefix(sequence) for sequence in sequences):
        return {}, {}, ("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_EVIDENCE",)
    blocks: dict[int, list[str]] = {standard: [] for standard in range(1, 9)}
    if error_code is None and all(rows[focus]["completion"] for focus in focused_review.FOCUSES):
        return rows, blocks, ()
    code = error_code or "MALFORMED_CODEX_REVIEW_BINDING"
    prefixes = ("FOCUSED_CODEX_REVIEW_PENDING", "MISSING_FOCUSED_CODEX_REVIEW_REQUEST")
    prefix = next((item for item in prefixes if code.startswith(f"{item}_")), None)
    if prefix and (focused_blocks := _codex_blocks(rows, prefix)):
        blocks.update(focused_blocks)
        return rows, blocks, ()
    return rows, blocks, (code,)


def _empty_codex() -> dict[str, dict[str, object]]:
    return {
        focus: {"completion": None, "focus": focus, "request_id": None, "requested_at": None}
        for focus in focused_review.FOCUSES
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
    codex_evidence: tuple[focused_review.FocusedReviewEvidence, ...],
    codex_error: str | None = None,
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
    validate_payload(payload, identity)
    return payload


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY") from error
    if parsed.tzinfo is None:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    return parsed


def _completion_identity(
    completion: object, request_id: int | None, requested_time: datetime | None
) -> tuple[tuple[str, int] | None, datetime | None]:
    if completion is None:
        return None, None
    if (
        not isinstance(completion, dict)
        or set(completion) != {"completed_at", "id", "kind"}
        or type(completion.get("id")) is not int
        or completion["id"] < 1
        or completion.get("kind") not in {"comment", "reaction", "review"}
        or not isinstance(completion.get("completed_at"), str)
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if request_id is None or requested_time is None:
        raise StandardResultsError("MISSING_STANDARD_CODEX_BINDING")
    completed_time = _time(completion["completed_at"])
    if completed_time < requested_time:
        raise StandardResultsError("STANDARD_CODEX_BINDING_MISMATCH")
    return (completion["kind"], completion["id"]), completed_time


def _validate_codex(
    value: object, focus: str, result: str, blocks: list[str]
) -> ValidatedCodexBinding:
    expected = {"completion", "focus", "request_id", "requested_at"}
    if not isinstance(value, dict) or set(value) != expected or value.get("focus") != focus:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    request_id, requested_at = value.get("request_id"), value.get("requested_at")
    if request_id is not None and (type(request_id) is not int or request_id < 1):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if (request_id is None) != (requested_at is None):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    requested_time = _time(requested_at) if requested_at is not None else None
    artifact, completed_time = _completion_identity(
        value.get("completion"), request_id, requested_time
    )
    codex_blocks = {
        f"FOCUSED_CODEX_REVIEW_PENDING_{focus}",
        f"MISSING_FOCUSED_CODEX_REVIEW_REQUEST_{focus}",
    } & set(blocks)
    if result == "PASS" and artifact is None:
        raise StandardResultsError("MISSING_STANDARD_CODEX_BINDING")
    if result == "BLOCK" and artifact is None and not codex_blocks:
        raise StandardResultsError("MISSING_STANDARD_CODEX_BINDING")
    if artifact is not None and codex_blocks:
        raise StandardResultsError("STANDARD_CODEX_BINDING_MISMATCH")
    return ValidatedCodexBinding(request_id, requested_time, artifact, completed_time)


def _validate_entry(value: object, standard: int) -> ValidatedCodexBinding:
    expected = {
        "applicable",
        "blocks",
        "check_context",
        "codex_review",
        "evidence_sources",
        "result",
        "standard",
        "technical_errors",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    result = value.get("result")
    if (
        value.get("standard") != standard
        or value.get("check_context") != CHECK_CONTEXTS[standard - 1]
        or type(value.get("applicable")) is not bool
        or result not in RESULTS
        or value.get("evidence_sources") != list(EVIDENCE_SOURCES[standard - 1])
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    blocks = _string_list(value.get("blocks"), "MALFORMED_STANDARD_RESULT_ENTRY")
    technical = _string_list(value.get("technical_errors"), "MALFORMED_STANDARD_RESULT_ENTRY")
    if blocks != sorted(blocks) or technical != sorted(technical):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    codex_families = {
        f"FOCUSED_CODEX_REVIEW_PENDING_{standard}",
        f"MISSING_FOCUSED_CODEX_REVIEW_REQUEST_{standard}",
    }
    if result == "PASS" and (blocks or technical):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if result == "BLOCK" and (not blocks or technical):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if result == "TECHNICAL_FAILURE" and not technical:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if any("FOCUSED_CODEX_REVIEW" in block and block not in codex_families for block in blocks):
        raise StandardResultsError("STANDARD_CODEX_BINDING_MISMATCH")
    for block in set(blocks) - codex_families:
        _require_owner(block, standard)
    return _validate_codex(value.get("codex_review"), str(standard), str(result), blocks)


def _validate_quality_artifact(value: object, technical: bool) -> None:
    if value is None and technical:
        return
    if (
        not isinstance(value, dict)
        or set(value) != {"capture_sha256", "digest", "id"}
        or type(value.get("id")) is not int
        or value["id"] < 1
        or not isinstance(value.get("digest"), str)
        or SHA64.fullmatch(value["digest"]) is None
        or not isinstance(value.get("capture_sha256"), str)
        or SHA64.fullmatch(value["capture_sha256"]) is None
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_ARTIFACT")


def _validate_codex_sequence(
    rows: list[ValidatedCodexBinding],
) -> None:
    request_ids = [item.request_id for item in rows if item.request_id is not None]
    artifacts = [item.artifact for item in rows if item.artifact is not None]
    if len(request_ids) != len(set(request_ids)) or len(artifacts) != len(set(artifacts)):
        raise StandardResultsError("REUSED_FOCUSED_CODEX_REVIEW_EVIDENCE")
    sequences = (
        tuple(item.request_id is not None for item in rows),
        tuple(item.artifact is not None for item in rows),
    )
    if any(not _is_prefix(sequence) for sequence in sequences):
        raise StandardResultsError("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_EVIDENCE")
    for current, following in zip(rows, rows[1:], strict=False):
        if following.requested_at is not None and (
            current.completed_at is None
            or (current.requested_at is not None and current.requested_at >= following.requested_at)
            or (current.completed_at is not None and current.completed_at >= following.requested_at)
        ):
            raise StandardResultsError("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_EVIDENCE")


def validate_payload(value: object, identity: RunIdentity | None = None) -> dict[str, Any]:
    """Validate ordering, identities, bindings, and every lane result."""
    expected = {
        "base_sha",
        "entries",
        "head_sha",
        "quality_artifact",
        "repository",
        "repository_id",
        "run_attempt",
        "run_id",
        "schema_version",
        "standard_sha256",
        "workflow_sha",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS")
    repository_id = value.get("repository_id")
    run_id = value.get("run_id")
    run_attempt = value.get("run_attempt")
    actual = RunIdentity(
        str(value.get("repository")),
        repository_id if type(repository_id) is int else 0,
        str(value.get("base_sha")),
        str(value.get("head_sha")),
        str(value.get("workflow_sha")),
        run_id if type(run_id) is int else 0,
        run_attempt if type(run_attempt) is int else 0,
    )
    _validate_identity(actual)
    if identity is not None and actual != identity:
        raise StandardResultsError("STANDARD_RESULTS_BINDING_MISMATCH")
    entries = value.get("entries")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("standard_sha256") != clause_inventory.STANDARD_SHA256
        or not isinstance(entries, list)
        or len(entries) != 8
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS")
    codex = [_validate_entry(entry, standard) for standard, entry in enumerate(entries, start=1)]
    _validate_codex_sequence(codex)
    technical_rows = [entry.get("result") == "TECHNICAL_FAILURE" for entry in entries]
    if any(technical_rows) != all(technical_rows):
        raise StandardResultsError("INCONSISTENT_SHARED_TECHNICAL_FAILURE")
    technical = all(technical_rows)
    if technical and len({tuple(entry["technical_errors"]) for entry in entries}) != 1:
        raise StandardResultsError("INCONSISTENT_SHARED_TECHNICAL_FAILURE")
    _validate_quality_artifact(value.get("quality_artifact"), technical)
    return value
