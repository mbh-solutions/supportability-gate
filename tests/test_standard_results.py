from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from supportability_gate import (
    clause_inventory,
    cli,
    codex_review,
    function_changes,
    git_changes,
    reporting,
    review_evidence,
    standard_block_ownership,
    standard_results,
    standard_results_enforcer,
    standard_results_producer,
)

IDENTITY = standard_results.RunIdentity(
    "example/repository",
    123,
    "b" * 40,
    "a" * 40,
    "f" * 40,
    456,
    1,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _evidence() -> tuple[codex_review.FocusedReviewEvidence, ...]:
    start = datetime(2026, 8, 11, 12, tzinfo=UTC)
    return tuple(
        codex_review.FocusedReviewEvidence(
            focus,
            20 + int(focus),
            start + timedelta(minutes=(int(focus) - 1) * 2),
            codex_review.CompletionArtifact(
                "reaction",
                100 * int(focus) + 1,
                start + timedelta(minutes=(int(focus) - 1) * 2 + 1),
            ),
        )
        for focus in codex_review.FOCUSES
    )


def _inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    complexity: dict[str, Any] = {
        "architecture": {"blocks": []},
        "base_sha": IDENTITY.base_sha,
        "changed_files": [{"path": "src/sample.py"}],
        "functions": [],
        "head_sha": IDENTITY.head_sha,
        "modularity": {"blocks": []},
        "overall_result": "PASS",
        "policy_blocks": [],
        "quality_profile": {
            "base_sha": IDENTITY.base_sha,
            "head_sha": IDENTITY.head_sha,
            "repository_remote": f"github.com/{IDENTITY.repository}",
            "workflow_sha": IDENTITY.workflow_sha,
        },
        "repository_remote": f"github.com/{IDENTITY.repository}",
        "schema_version": "1.0",
        "standard_blocks": [{"blocks": [], "standard": standard} for standard in range(1, 9)],
        "standard_sha256": clause_inventory.STANDARD_SHA256,
        "technical_errors": [],
    }
    characterization: dict[str, Any] = {
        "base_sha": IDENTITY.base_sha,
        "head_sha": IDENTITY.head_sha,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": f"github.com/{IDENTITY.repository}",
        "schema_version": "characterization-result.v1",
        "workflow_sha": IDENTITY.workflow_sha,
    }
    refactor: dict[str, Any] = {
        "applicable": False,
        "base_sha": IDENTITY.base_sha,
        "characterization_sha256": hashlib.sha256(_canonical(characterization)).hexdigest(),
        "head_sha": IDENTITY.head_sha,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": IDENTITY.repository,
        "schema_version": "refactor-policy-result.v1",
    }
    quality = {
        "artifact_digest": "d" * 64,
        "artifact_id": "789",
        "capture_sha256": "c" * 64,
        "job": "quality-profile",
        "repository": IDENTITY.repository,
        "repository_id": str(IDENTITY.repository_id),
        "run_attempt": str(IDENTITY.run_attempt),
        "run_id": str(IDENTITY.run_id),
    }
    return complexity, characterization, refactor, quality


def _compose(
    inputs: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    *,
    codex_error: str | None = None,
) -> dict[str, object]:
    return standard_results.compose_results(*inputs, IDENTITY, _evidence(), codex_error)


def _poison(
    standard: int,
    inputs: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    complexity, characterization, refactor, _ = inputs

    def add_policy_block(block: str) -> None:
        complexity["policy_blocks"].append(block)
        complexity["standard_blocks"][standard - 1]["blocks"].append(block)

    if standard == 1:
        complexity["functions"] = [
            {"decision": "BLOCK", "head": {"qualified_name": "sample.complex"}}
        ]
    elif standard == 2:
        add_policy_block("MISSING_REVIEW_EVIDENCE:separation_of_concerns.after")
    elif standard == 3:
        complexity["architecture"]["blocks"].append("IMPORT_CYCLE:src/a.py:1:src.b")
        add_policy_block("IMPORT_CYCLE:src/a.py:1:src.b")
    elif standard == 4:
        complexity["modularity"]["blocks"].append("MISSING_NEW_LOCATION_JUSTIFICATION:src/new.py")
        add_policy_block("MISSING_NEW_LOCATION_JUSTIFICATION:src/new.py")
    elif standard == 5:
        characterization["policy_blocks"] = ["MISSING_BASELINE"]
        characterization["overall_result"] = "BLOCK"
    elif standard == 6:
        refactor["applicable"] = True
        refactor["policy_blocks"] = ["MISSING_OWNER_AUTHORIZATION"]
        refactor["overall_result"] = "BLOCK"
    elif standard == 7:
        add_policy_block("QUALITY_GATE_FAILED:python.pytest.v1")
    else:
        add_policy_block("MISSING_REVIEW_EVIDENCE:review_handoff.summary")
    if complexity["policy_blocks"] or complexity["functions"]:
        complexity["overall_result"] = "BLOCK"
    refactor["characterization_sha256"] = hashlib.sha256(_canonical(characterization)).hexdigest()


@pytest.mark.parametrize("standard", range(1, 9))
def test_one_isolated_poison_blocks_only_its_standard(standard: int) -> None:
    inputs = _inputs()
    _poison(standard, inputs)

    payload = _compose(inputs)

    assert [entry["result"] for entry in payload["entries"]] == [
        "BLOCK" if item == standard else "PASS" for item in range(1, 9)
    ]


def test_eight_simultaneous_poisons_emit_eight_blocks() -> None:
    inputs = _inputs()
    for standard in range(1, 9):
        _poison(standard, inputs)

    payload = _compose(inputs)

    assert [entry["result"] for entry in payload["entries"]] == ["BLOCK"] * 8
    assert all(len(entry["blocks"]) == 1 for entry in payload["entries"])


@pytest.mark.parametrize(
    ("decision", "metric"),
    [("PASS", "head"), ("PASS_PROGRESSIVE", "head"), ("DELETED", "base")],
)
def test_valid_non_blocking_function_decisions_pass(decision: str, metric: str) -> None:
    inputs = _inputs()
    inputs[0]["functions"] = [{"decision": decision, metric: {"qualified_name": "sample.changed"}}]

    payload = _compose(inputs)

    assert [entry["result"] for entry in payload["entries"]] == ["PASS"] * 8


def test_genuine_analyzer_technical_payload_preserves_error() -> None:
    repository_identity = git_changes.RepositoryIdentity(
        f"github.com/{IDENTITY.repository}",
        IDENTITY.base_sha,
        "c" * 40,
        IDENTITY.head_sha,
        "d" * 40,
        "git version 2",
    )
    complexity = reporting.result_payload(
        cli._technical_result(
            repository_identity,
            ".supportability.toml",
            [],
            [],
            None,
            None,
            (),
            function_changes.PythonSourceError("SYNTAX_ERROR", "invalid syntax"),
        )
    )
    _, characterization, refactor, quality = _inputs()

    payload = _compose((complexity, characterization, refactor, quality))

    assert payload["quality_artifact"] is None
    assert [entry["result"] for entry in payload["entries"]] == ["TECHNICAL_FAILURE"] * 8
    assert {tuple(entry["technical_errors"]) for entry in payload["entries"]} == {
        ("COMPLEXITY_RESULT:SYNTAX_ERROR",)
    }


@pytest.mark.parametrize(
    ("changed_files", "refactor_applicable", "expected"),
    [
        ([], False, [False] * 8),
        ([{"path": "src/sample.py"}], True, [True] * 8),
    ],
)
def test_applicability_tracks_changed_files_and_refactor_policy(
    changed_files: list[dict[str, str]], refactor_applicable: bool, expected: list[bool]
) -> None:
    inputs = _inputs()
    inputs[0]["changed_files"] = changed_files
    inputs[2]["applicable"] = refactor_applicable

    payload = _compose(inputs)

    assert [entry["applicable"] for entry in payload["entries"]] == expected


def test_unknown_or_multiply_owned_blocks_make_all_results_technical() -> None:
    unknown = _inputs()
    unknown[0]["policy_blocks"] = ["UNKNOWN_POLICY_BLOCK"]
    unknown[0]["overall_result"] = "BLOCK"

    unknown_payload = _compose(unknown)

    assert [entry["result"] for entry in unknown_payload["entries"]] == ["TECHNICAL_FAILURE"] * 8
    multiple = _inputs()
    block = "QUALITY_GATE_FAILED:python.pytest.v1"
    multiple[0]["policy_blocks"] = [block]
    multiple[0]["standard_blocks"][2]["blocks"] = [block]
    multiple[0]["standard_blocks"][6]["blocks"] = [block]
    multiple[0]["overall_result"] = "BLOCK"

    multiple_payload = _compose(multiple)

    assert [entry["result"] for entry in multiple_payload["entries"]] == ["TECHNICAL_FAILURE"] * 8


def test_shared_binding_or_codex_trust_corruption_makes_all_results_technical() -> None:
    binding = _inputs()
    binding[3]["run_id"] = "999"

    binding_payload = _compose(binding)
    codex_payload = _compose(
        _inputs(),
        codex_error="GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE",
    )

    assert [entry["result"] for entry in binding_payload["entries"]] == ["TECHNICAL_FAILURE"] * 8
    assert [entry["result"] for entry in codex_payload["entries"]] == ["TECHNICAL_FAILURE"] * 8


def test_non_prefix_completion_snapshot_becomes_shared_technical() -> None:
    evidence = list(_evidence())
    second = evidence[1]
    evidence[1] = codex_review.FocusedReviewEvidence(
        second.focus,
        second.request_id,
        second.requested_at,
        None,
    )

    payload = standard_results.compose_results(
        *_inputs(),
        IDENTITY,
        tuple(evidence),
        "FOCUSED_CODEX_REVIEW_PENDING_2",
    )

    assert [entry["result"] for entry in payload["entries"]] == ["TECHNICAL_FAILURE"] * 8
    assert {tuple(entry["technical_errors"]) for entry in payload["entries"]} == {
        ("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_EVIDENCE",)
    }


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "duplicate",
        "unknown",
        "identity",
        "artifact",
        "quality",
        "partial_technical",
        "request_gap",
        "pending_gap",
        "completion_before_request",
        "next_request_at_completion",
        "wrong_owner",
    ],
)
def test_malformed_entries_and_incorrect_bindings_fail_closed(case: str) -> None:
    payload = copy.deepcopy(_compose(_inputs()))
    entries = payload["entries"]
    if case == "missing":
        entries.pop()
    elif case == "duplicate":
        entries[-1] = copy.deepcopy(entries[0])
    elif case == "unknown":
        entries[0]["standard"] = 9
    elif case == "identity":
        payload["head_sha"] = "e" * 40
    elif case == "artifact":
        entries[1]["codex_review"]["completion"]["id"] = entries[0]["codex_review"]["completion"][
            "id"
        ]
    elif case == "quality":
        payload["quality_artifact"]["id"] = 0
    elif case == "partial_technical":
        entries[0]["result"] = "TECHNICAL_FAILURE"
        entries[0]["technical_errors"] = ["SHARED_TRUST_FAILURE"]
    elif case == "request_gap":
        entries[1]["codex_review"] = {
            "completion": None,
            "focus": "2",
            "request_id": None,
            "requested_at": None,
        }
        entries[1]["blocks"] = ["MISSING_FOCUSED_CODEX_REVIEW_REQUEST_2"]
        entries[1]["result"] = "BLOCK"
    elif case == "pending_gap":
        entries[1]["codex_review"]["completion"] = None
        entries[1]["blocks"] = ["FOCUSED_CODEX_REVIEW_PENDING_2"]
        entries[1]["result"] = "BLOCK"
    elif case == "completion_before_request":
        entries[0]["codex_review"]["completion"]["completed_at"] = "2026-08-11T11:59:00+00:00"
    elif case == "next_request_at_completion":
        entries[1]["codex_review"]["requested_at"] = entries[0]["codex_review"]["completion"][
            "completed_at"
        ]
    else:
        entries[1]["blocks"] = ["QUALITY_GATE_FAILED:python.pytest.v1"]
        entries[1]["result"] = "BLOCK"

    with pytest.raises(standard_results.StandardResultsError):
        standard_results.validate_payload(payload, IDENTITY)


@pytest.mark.parametrize("standard", range(1, 9))
def test_enforcer_returns_pass_block_or_technical(tmp_path: Path, standard: int) -> None:
    common = [
        "--repository",
        IDENTITY.repository,
        "--repository-id",
        str(IDENTITY.repository_id),
        "--base-sha",
        IDENTITY.base_sha,
        "--head-sha",
        IDENTITY.head_sha,
        "--workflow-sha",
        IDENTITY.workflow_sha,
        "--run-id",
        str(IDENTITY.run_id),
        "--run-attempt",
        str(IDENTITY.run_attempt),
        "--input",
        str(tmp_path / "standard-results.json"),
        "--standard",
        str(standard),
    ]
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(_compose(_inputs())), encoding="utf-8")
    assert standard_results_enforcer.main(common) == 0
    blocked = _inputs()
    _poison(standard, blocked)
    path.write_text(json.dumps(_compose(blocked)), encoding="utf-8")
    assert standard_results_enforcer.main(common) == 1
    path.write_text("{}", encoding="utf-8")
    assert standard_results_enforcer.main(common) == 2


def test_structured_review_collects_every_applicable_field_defect() -> None:
    _, blocks = review_evidence.evaluate_review_evidence(
        b'schema_version = "1.0"\n\n[behavior]\nintended_behavior = ""\nproof = 1\n'
    )

    expected = {
        "INSUFFICIENT_REVIEW_EVIDENCE:behavior.intended_behavior": 5,
        "MALFORMED_REVIEW_EVIDENCE:behavior.proof": 5,
        "MISSING_REVIEW_EVIDENCE:architecture.dependency_direction": 3,
        "MISSING_REVIEW_EVIDENCE:architecture.reviewed_paths": 3,
        "MISSING_REVIEW_EVIDENCE:characterization.captured_behavior": 5,
        "MISSING_REVIEW_EVIDENCE:characterization.proof": 5,
        "MISSING_REVIEW_EVIDENCE:human_review.cohesion": 4,
        "MISSING_REVIEW_EVIDENCE:human_review.intended_behavior": 5,
        "MISSING_REVIEW_EVIDENCE:human_review.naming": 1,
        "MISSING_REVIEW_EVIDENCE:human_review.reviewability": 1,
        "MISSING_REVIEW_EVIDENCE:incremental_refactor.completed_step": 6,
        "MISSING_REVIEW_EVIDENCE:incremental_refactor.target": 6,
        "MISSING_REVIEW_EVIDENCE:responsibility_boundary.does_not_own": 4,
        "MISSING_REVIEW_EVIDENCE:responsibility_boundary.owns": 4,
        "MISSING_REVIEW_EVIDENCE:responsibility_boundary.path": 4,
        "MISSING_REVIEW_EVIDENCE:review_handoff.remaining_risks": 8,
        "MISSING_REVIEW_EVIDENCE:review_handoff.summary": 8,
        "MISSING_REVIEW_EVIDENCE:separation_of_concerns.after": 2,
        "MISSING_REVIEW_EVIDENCE:separation_of_concerns.before": 2,
    }
    assert set(blocks) == set(expected)
    assert {block: review_evidence.block_standard(block) for block in blocks} == expected


@pytest.mark.parametrize(
    "block",
    [
        "MALFORMED_REVIEW_EVIDENCE:module_boundaries",
        "MALFORMED_REVIEW_EVIDENCE:module_boundaries.path",
        "MISSING_REVIEW_EVIDENCE:module_boundaries[0].basis",
    ],
)
def test_module_boundary_review_defects_belong_to_standard_four(block: str) -> None:
    assert review_evidence.block_standard(block) == 4


def test_every_non_review_block_family_has_one_exact_standard_owner() -> None:
    expected = (
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
    assert standard_block_ownership.BLOCK_FAMILIES == expected
    for standard, families in enumerate(expected, start=1):
        for family in families:
            block = f"{family}example" if family.endswith(":") else family
            assert standard_block_ownership.owners(block) == {standard}


def _producer_arguments(tmp_path: Path) -> tuple[list[str], Path]:
    paths = []
    for name, value in zip(
        ("complexity", "characterization", "refactor", "quality"), _inputs(), strict=True
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(path)
    output = tmp_path / "standard-results.json"
    return [
        "--repository",
        IDENTITY.repository,
        "--repository-id",
        str(IDENTITY.repository_id),
        "--base-sha",
        IDENTITY.base_sha,
        "--head-sha",
        IDENTITY.head_sha,
        "--workflow-sha",
        IDENTITY.workflow_sha,
        "--run-id",
        str(IDENTITY.run_id),
        "--run-attempt",
        str(IDENTITY.run_attempt),
        "--pull-number",
        "7",
        "--complexity-result",
        str(paths[0]),
        "--characterization-result",
        str(paths[1]),
        "--refactor-result",
        str(paths[2]),
        "--quality-provenance",
        str(paths[3]),
        "--output",
        str(output),
    ], output


def test_producer_preserves_partial_snapshot_when_next_request_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, output = _producer_arguments(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    complete = _evidence()
    snapshot = (
        complete[0],
        codex_review.FocusedReviewEvidence(
            complete[1].focus,
            complete[1].request_id,
            complete[1].requested_at,
            None,
        ),
    )

    def pending(*args: object) -> tuple[codex_review.FocusedReviewEvidence, ...]:
        raise codex_review.CodexReviewError("FOCUSED_CODEX_REVIEW_PENDING_2")

    monkeypatch.setattr(
        standard_results_producer.codex_review, "require_focused_completion", pending
    )
    monkeypatch.setattr(
        standard_results_producer.codex_review,
        "focused_completion_snapshot",
        lambda *args: ("MISSING_FOCUSED_CODEX_REVIEW_REQUEST_3", snapshot),
    )

    assert standard_results_producer.main(arguments) == 0
    payload = json.loads(output.read_bytes())
    assert [entry["result"] for entry in payload["entries"]] == ["PASS", *("BLOCK",) * 7]
    assert [entry["blocks"] for entry in payload["entries"]][1:] == [
        ["FOCUSED_CODEX_REVIEW_PENDING_2"],
        *[[f"MISSING_FOCUSED_CODEX_REVIEW_REQUEST_{standard}"] for standard in range(3, 9)],
    ]
    assert [entry["codex_review"]["request_id"] for entry in payload["entries"]] == [
        21,
        22,
        *([None] * 6),
    ]
    assert [entry["codex_review"]["requested_at"] for entry in payload["entries"]] == [
        complete[0].requested_at.isoformat(),
        complete[1].requested_at.isoformat(),
        *([None] * 6),
    ]
    assert [entry["codex_review"]["completion"] is not None for entry in payload["entries"]] == [
        True,
        *([False] * 7),
    ]


def test_producer_preserves_completed_prefix_when_next_request_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, output = _producer_arguments(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    def missing(*args: object) -> tuple[codex_review.FocusedReviewEvidence, ...]:
        raise codex_review.CodexReviewError("MISSING_FOCUSED_CODEX_REVIEW_REQUEST_8")

    monkeypatch.setattr(
        standard_results_producer.codex_review, "require_focused_completion", missing
    )
    monkeypatch.setattr(
        standard_results_producer.codex_review,
        "focused_completion_snapshot",
        lambda *args: ("MISSING_FOCUSED_CODEX_REVIEW_REQUEST_8", _evidence()[:7]),
    )

    assert standard_results_producer.main(arguments) == 0
    payload = json.loads(output.read_bytes())
    assert [entry["result"] for entry in payload["entries"]] == [*("PASS",) * 7, "BLOCK"]
    assert payload["entries"][7]["blocks"] == ["MISSING_FOCUSED_CODEX_REVIEW_REQUEST_8"]


def test_producer_uses_refreshed_complete_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, output = _producer_arguments(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    def pending(*args: object) -> tuple[codex_review.FocusedReviewEvidence, ...]:
        raise codex_review.CodexReviewError("FOCUSED_CODEX_REVIEW_PENDING_8")

    monkeypatch.setattr(
        standard_results_producer.codex_review, "require_focused_completion", pending
    )
    monkeypatch.setattr(
        standard_results_producer.codex_review,
        "focused_completion_snapshot",
        lambda *args: (None, _evidence()),
    )

    assert standard_results_producer.main(arguments) == 0
    payload = json.loads(output.read_bytes())
    assert [entry["result"] for entry in payload["entries"]] == ["PASS"] * 8


def test_producer_makes_snapshot_failure_shared_and_technical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, output = _producer_arguments(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "token")

    def failure(*args: object) -> tuple[codex_review.FocusedReviewEvidence, ...]:
        raise codex_review.CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")

    monkeypatch.setattr(
        standard_results_producer.codex_review, "require_focused_completion", failure
    )
    monkeypatch.setattr(
        standard_results_producer.codex_review, "focused_completion_snapshot", failure
    )

    assert standard_results_producer.main(arguments) == 0
    payload = json.loads(output.read_bytes())
    assert [entry["result"] for entry in payload["entries"]] == ["TECHNICAL_FAILURE"] * 8
    assert {tuple(entry["technical_errors"]) for entry in payload["entries"]} == {
        ("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE",)
    }


@pytest.mark.parametrize(
    ("option", "code"),
    [
        ("--complexity-result", "MISSING_COMPLEXITY_RESULT"),
        ("--characterization-result", "MISSING_CHARACTERIZATION_RESULT"),
        ("--refactor-result", "MISSING_REFACTOR_RESULT"),
        ("--quality-provenance", "MISSING_QUALITY_PROVENANCE"),
    ],
)
@pytest.mark.parametrize("defect", ["missing", "malformed"])
def test_producer_emits_exact_shared_failure_for_every_upstream_input(
    tmp_path: Path, option: str, code: str, defect: str
) -> None:
    arguments, output = _producer_arguments(tmp_path)
    path = Path(arguments[arguments.index(option) + 1])
    if defect == "missing":
        path.unlink()
    else:
        path.write_text("{", encoding="utf-8")

    assert standard_results_producer.main(arguments) == 0
    payload = json.loads(output.read_bytes())
    assert [entry["result"] for entry in payload["entries"]] == ["TECHNICAL_FAILURE"] * 8
    assert {tuple(entry["technical_errors"]) for entry in payload["entries"]} == {
        tuple(sorted(("MALFORMED_COMPLEXITY_RESULT", code)))
    }


def test_workflow_wires_each_matrix_lane_to_the_exact_artifact_and_enforcer() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/organization-required.yml"
    ).read_text(encoding="utf-8")

    assert "fail-fast: false" in workflow
    assert re.search(r"(?m)^  standard-results:\n(?:.|\n)*?^    if: always\(\)$", workflow)
    assert not re.search(r"(?m)^\s+name: Supportability Gate\s*$", workflow)
    assert not re.search(r"(?m)^  supportability-gate:\s*$", workflow)
    observer_job = workflow.split("\n  observe-codex-review:\n", 1)[1].split(
        "\n  characterize-base:\n", 1
    )[0]
    evidence_job, job = workflow.split("\n  supportability-evidence:\n", 1)[1].split(
        "\n  standard-results:\n", 1
    )
    assert codex_review.FOCUSED_POLL_ATTEMPTS == 240
    assert observer_job.count("timeout-minutes: 70") == 1
    assert evidence_job.count("timeout-minutes: 30") == 1
    assert evidence_job.count("repository: mbh-solutions/supportability-gate") == 1
    assert evidence_job.count("ref: ${{ github.workflow_sha }}") == 1
    assert evidence_job.count("path: gate") == 1
    assert evidence_job.count("PYTHONPATH: ${{ github.workspace }}/gate/src") == 1
    assert "target/src" not in evidence_job
    assert "artifact-id: ${{ steps.upload_evidence.outputs.artifact-id }}" in evidence_job
    assert "python -P -m supportability_gate.standard_results_producer \\" in evidence_job
    assert '--output "$RUNNER_TEMP/evidence/standard-results.json"' in evidence_job
    assert "id: upload_evidence" in evidence_job
    assert "path: ${{ runner.temp }}/evidence" in evidence_job
    assert "if-no-files-found: error" in evidence_job
    rows = re.findall(r"(?m)^          - standard: ([1-8])\n            context: (.+)$", job)
    assert rows == [
        (str(standard), context)
        for standard, context in enumerate(standard_results.CHECK_CONTEXTS, start=1)
    ]
    assert "needs: supportability-evidence" in job
    assert job.count("repository: mbh-solutions/supportability-gate") == 1
    assert job.count("ref: ${{ github.workflow_sha }}") == 1
    assert job.count("path: gate") == 1
    assert job.count("PYTHONPATH: ${{ github.workspace }}/gate/src") == 1
    assert "target/src" not in job
    assert "artifact-ids: ${{ needs.supportability-evidence.outputs.artifact-id }}" in job
    assert "STANDARD: ${{ matrix.standard }}" in job
    enforcer_step = job.split("\n      - name: Enforce one Standard result\n", 1)[1]
    assert enforcer_step.startswith("        if: always()\n")
    assert "continue-on-error:" not in enforcer_step
    assert "python -P -m supportability_gate.standard_results_enforcer \\" in job
    assert '--input "$RUNNER_TEMP/evidence/standard-results.json" \\' in job
    assert '--standard "$STANDARD"' in job
