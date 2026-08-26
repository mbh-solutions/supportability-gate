from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from supportability_gate import (
    clause_inventory,
    cli,
    contract,
    git_changes,
    standard_block_ownership,
    standard_results,
    standard_results_enforcer,
    standard_results_producer,
)

IDENTITY = standard_results.RunIdentity(
    "example/repository", 123, "b" * 40, "a" * 40, "f" * 40, 456, 1
)
SUCCESS_OUTCOMES = {
    "install": "success",
    "complexity": "success",
    "characterization": "success",
    "refactor": "success",
    "quality": "success",
}
EXPECTED_QUALITY_ARTIFACT = {
    "capture_sha256": "c" * 64,
    "digest": "d" * 64,
    "id": "789",
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _review_evidence() -> dict[str, object]:
    return {
        "architecture": {
            "dependency_direction": "Dependencies point toward domain policy.",
            "reviewed_paths": ["src/sample.py"],
        },
        "behavior": {"intended_behavior": "Covered behavior.", "proof": "tests/test_sample.py"},
        "characterization": {
            "captured_behavior": "Pre-change behavior is captured.",
            "proof": "tests/test_sample.py",
        },
        "human_review": {
            "cohesion": "Cohesive.",
            "intended_behavior": "Intended.",
            "naming": "Named.",
            "reviewability": "Reviewable.",
        },
        "incremental_refactor": {"completed_step": "One step.", "target": "One boundary."},
        "module_boundaries": [],
        "responsibility_boundary": {
            "does_not_own": "Presentation.",
            "owns": "Sample behavior.",
            "path": "src/sample.py",
        },
        "review_handoff": {"remaining_risks": ["None known."], "summary": "Ready."},
        "schema_version": "1.0",
        "separation_of_concerns": {
            "after": "One owner.",
            "before": "Mixed owners.",
            "boundaries": [],
        },
    }


def _changed_file(path: str, lines: list[int], *, status: str = "MODIFIED") -> dict[str, object]:
    production = path.startswith("src/")
    return {
        "base_production": production and status != "ADDED",
        "changed_head_lines": lines,
        "complexity_assessed": production,
        "head_production": production,
        "new_path": path,
        "old_path": None if status == "ADDED" else path,
        "status": status,
    }


def _complexity(
    path: str = "src/sample.py",
    lines: list[int] | None = None,
    *,
    status: str = "MODIFIED",
) -> dict[str, Any]:
    lines = lines or [1]
    return {
        "architecture": {
            "adapter": "python.ast-imports.v1",
            "blocks": [],
            "covered_paths": [path] if path.startswith("src/") else [],
            "edges": [],
            "executed": True,
            "nodes": [path] if path.startswith("src/") else [],
        },
        "base_contract_blob_sha": "1" * 40,
        "base_sha": IDENTITY.base_sha,
        "base_tree_sha": "c" * 40,
        "changed_files": [_changed_file(path, lines, status=status)],
        "commands": [
            {
                "arguments": ["diff", "--name-status", IDENTITY.base_sha, IDENTITY.head_sha],
                "exit_code": 0,
                "stderr_sha256": "0" * 64,
                "stdout_sha256": "1" * 64,
                "tool": "git",
            }
        ],
        "contract_path": ".supportability.toml",
        "contract_sha256": "2" * 64,
        "dependency_direction_explanation": "PASS: verified import graph [].",
        "functions": [],
        "gate_coverage": [
            {"adapter": "python.c901-touched.v1", "paths": ["src"]},
            {"adapter": "python.ast-imports.v1", "paths": ["src"]},
        ],
        "head_sha": IDENTITY.head_sha,
        "head_tree_sha": "d" * 40,
        "high_risk_paths": [],
        "language": "python",
        "modularity": {
            "blocks": [],
            "changed_paths": [path] if path.startswith("src/") else [],
            "coupling_edges": [],
            "coverage": [],
            "justifications": [],
            "new_paths": [],
        },
        "overall_result": "PASS",
        "policy_blocks": [],
        "production_paths": ["src"],
        "quality_profile": {
            "base_sha": IDENTITY.base_sha,
            "changed_paths": [path],
            "commands": [
                {
                    "adapter": "python.pytest.v1",
                    "arguments": ["python", "-m", "pytest", "-q"],
                    "executed": True,
                    "exit_code": 0,
                    "observed_paths": ["src/sample.py"],
                    "proof_kind": "runtime-lines",
                    "zero_statement_paths": [],
                }
            ],
            "exclusions": [],
            "head_sha": IDENTITY.head_sha,
            "high_risk_paths": [],
            "language": "python",
            "maximum_complexity": 10,
            "production_files": ["src/sample.py"],
            "production_paths": ["src"],
            "repository_remote": f"github.com/{IDENTITY.repository}",
            "schema_version": "quality-gates.v3",
            "workflow_sha": IDENTITY.workflow_sha,
        },
        "rename_bindings": [],
        "repository_remote": f"github.com/{IDENTITY.repository}",
        "review_evidence": _review_evidence(),
        "review_evidence_path": ".supportability-review.toml",
        "ruff_diagnostics": [],
        "schema_version": "1.0",
        "standard_sha256": clause_inventory.STANDARD_SHA256,
        "technical_errors": [],
        "tool_versions": {},
        "touched_qualified_functions": [],
    }


def _characterization(path: str = "src/sample.py") -> dict[str, Any]:
    return {
        "artifacts": {
            "base": {"capture_sha256": "3" * 64, "digest": "4" * 64, "id": "701"},
            "head": {"capture_sha256": "5" * 64, "digest": "6" * 64, "id": "702"},
        },
        "base_sha": IDENTITY.base_sha,
        "behavior_fingerprint": hashlib.sha256(_canonical([["sample", "e" * 64]])).hexdigest(),
        "coverage": {
            "covered_paths": [path] if path.startswith("src/") else [],
            "required_paths": [path] if path.startswith("src/") else [],
        },
        "head_sha": IDENTITY.head_sha,
        "manifest_blob_sha": "8" * 40,
        "manifest_sha256": "9" * 64,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": f"github.com/{IDENTITY.repository}",
        "scenarios": [
            {
                "base_behavior_sha256": "e" * 64,
                "command": ["python", "tests/characterization/sample.characterization.py"],
                "compatibility": "PASS",
                "covers": ["src/sample.py"],
                "golden_behavior_sha256": "e" * 64,
                "head_behavior_sha256": "e" * 64,
                "id": "sample",
                "kind": "golden",
            }
        ],
        "schema_version": "characterization-result.v1",
        "workflow_sha": IDENTITY.workflow_sha,
    }


def _refactor(characterization: dict[str, Any], path: str) -> dict[str, Any]:
    return {
        "applicable": False,
        "authorization": None,
        "authorization_comment_id": None,
        "base_sha": IDENTITY.base_sha,
        "characterization_sha256": hashlib.sha256(_canonical(characterization)).hexdigest(),
        "changed_paths": [path],
        "head_sha": IDENTITY.head_sha,
        "other_standard_clauses_waived": False,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": IDENTITY.repository,
        "schema_version": "refactor-policy-result.v1",
        "targets": [],
        "unbounded_paths": [],
    }


def _quality() -> dict[str, Any]:
    return {
        "artifact_digest": "d" * 64,
        "artifact_id": "789",
        "capture_sha256": "c" * 64,
        "commands": [
            {
                "adapter": "python.pytest.v1",
                "raw_proof_sha256": "a" * 64,
                "stderr_sha256": "b" * 64,
                "stdout_sha256": "c" * 64,
            }
        ],
        "job": "quality-profile",
        "repository": IDENTITY.repository,
        "repository_id": str(IDENTITY.repository_id),
        "run_attempt": str(IDENTITY.run_attempt),
        "run_id": str(IDENTITY.run_id),
        "runner_environment": "github-hosted",
    }


def _inputs(
    path: str = "src/sample.py",
    lines: list[int] | None = None,
    *,
    status: str = "MODIFIED",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    complexity = _complexity(path, lines, status=status)
    characterization = _characterization(path)
    return complexity, characterization, _refactor(characterization, path), _quality()


def _owned_new_location_inputs(
    path: str = "src/orders/model.py",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    inputs = _inputs(path, status="ADDED")
    complexity = inputs[0]
    claim = {
        "basis": "domain",
        "justification": "Exact source path owns one cohesive boundary.",
        "owner_path": path,
        "path": path,
    }
    adapters = sorted(item["adapter"] for item in complexity["gate_coverage"])
    profile_command = complexity["quality_profile"]["commands"][0]
    complexity["quality_profile"]["commands"] = [
        {**profile_command, "adapter": adapter, "observed_paths": [path]} for adapter in adapters
    ]
    provenance_command = inputs[3]["commands"][0]
    inputs[3]["commands"] = [{**provenance_command, "adapter": adapter} for adapter in adapters]
    complexity["review_evidence"]["module_boundaries"] = [claim]
    complexity["modularity"] = {
        "blocks": [],
        "changed_paths": [path],
        "coupling_edges": [],
        "coverage": [{"adapters": adapters, "architecture": True, "path": path}],
        "justifications": [claim],
        "new_paths": [path],
    }
    return inputs


def _metric(path: str, qualified_name: str, complexity: int) -> dict[str, object]:
    return {
        "complexity": complexity,
        "end_line": 30,
        "path": path,
        "qualified_name": qualified_name,
        "start_line": 1,
    }


def _function(
    base: dict[str, object] | None,
    head: dict[str, object] | None,
    *,
    state: str,
    decision: str,
    debt: int | None,
    next_target: int | None,
) -> dict[str, object]:
    return {
        "base": base,
        "decision": decision,
        "ending_complexity": head["complexity"] if head else None,
        "head": head,
        "next_target": next_target,
        "remaining_debt": debt,
        "remaining_gap": debt,
        "starting_complexity": base["complexity"] if base else None,
        "state": state,
    }


def _ruff(metric: dict[str, object]) -> dict[str, object]:
    return {
        "code": "C901",
        "complexity": metric["complexity"],
        "line": metric["start_line"],
        "message": f"function is too complex ({metric['complexity']} > 10)",
        "path": metric["path"],
        "qualified_name": metric["qualified_name"],
    }


def _compose(
    inputs: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
    *,
    source_errors: dict[str, str] | None = None,
    source_outcomes: dict[str, str] | None = None,
) -> dict[str, Any]:
    if source_outcomes is None:
        source_outcomes = dict(SUCCESS_OUTCOMES)
        for source, value in zip(("complexity", "characterization", "refactor"), inputs[:3]):
            source_outcomes[source] = (
                "success" if value.get("overall_result") == "PASS" else "failure"
            )
        profile = inputs[0].get("quality_profile")
        if isinstance(profile, dict) and any(
            not command["executed"] or command["exit_code"]
            for command in profile.get("commands", [])
        ):
            source_outcomes["quality"] = "failure"
    return standard_results.compose_results(
        *inputs,
        IDENTITY,
        expected_quality_artifact=EXPECTED_QUALITY_ARTIFACT,
        source_errors=source_errors,
        source_outcomes=source_outcomes,
    )


def _results(payload: dict[str, Any]) -> list[str]:
    return [entry["result"] for entry in payload["entries"]]


def _entry(payload: dict[str, Any], standard: int) -> dict[str, Any]:
    return payload["entries"][standard - 1]


def _technical_standards(payload: dict[str, Any]) -> set[int]:
    return {
        standard
        for standard, entry in enumerate(payload["entries"], start=1)
        if entry["result"] == "TECHNICAL_FAILURE"
    }


def test_clean_current_aggregate_schema_passes_without_prototype_standard_blocks() -> None:
    inputs = _inputs()
    payload = _compose(inputs)

    assert "standard_blocks" not in inputs[0]
    assert _results(payload) == ["PASS"] * 8
    assert payload["applicability_evidence"] == {
        "changed_files": inputs[0]["changed_files"],
        "classification": "FULL_PROCESS",
        "source_sha256": hashlib.sha256(_canonical(inputs[0])).hexdigest(),
        "source_validated": True,
    }
    assert standard_results.validate_payload(payload, IDENTITY) is None
    assert standard_results.review_required(payload) is True


def test_standard_results_import_does_not_require_analysis_dependencies() -> None:
    script = """
import sys

sys.path.insert(0, "src")
import supportability_gate.standard_results
"""

    completed = subprocess.run(
        [sys.executable, "-P", "-S", "-c", script],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_current_refactor_schema_has_exact_boolean_applicable() -> None:
    inputs = _inputs()

    payload = _compose(inputs)

    assert inputs[2]["applicable"] is False
    assert _entry(payload, 6)["result"] == "PASS"


def test_quality_artifact_has_exact_authenticated_identity() -> None:
    payload = _compose(_inputs())

    assert payload["quality_artifact"] == {
        "capture_sha256": "c" * 64,
        "digest": "d" * 64,
        "id": 789,
    }
    forged = copy.deepcopy(payload)
    forged["quality_artifact"]["id"] = True
    with pytest.raises(standard_results.StandardResultsError):
        standard_results.validate_payload(forged, IDENTITY)


@pytest.mark.parametrize("field", ["repository_id", "run_id", "run_attempt"])
def test_boolean_identity_numbers_are_rejected(field: str) -> None:
    identity = replace(IDENTITY, **{field: True})

    with pytest.raises(
        standard_results.StandardResultsError,
        match="MALFORMED_STANDARD_RESULTS_IDENTITY",
    ):
        standard_results.compose_results(
            *_inputs(),
            identity,
            expected_quality_artifact=EXPECTED_QUALITY_ARTIFACT,
            source_outcomes=SUCCESS_OUTCOMES,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("standard", True), ("applicable", 1)],
)
def test_boolean_or_integer_row_spoofs_are_rejected(field: str, value: object) -> None:
    payload = _compose(_inputs())
    payload["entries"][0][field] = value

    with pytest.raises(standard_results.StandardResultsError):
        standard_results.validate_payload(payload, IDENTITY)


@pytest.mark.parametrize(
    "block",
    [
        "IMPORT_CYCLE:src/a.py:1:src.b",
        "MALFORMED_TYPESCRIPT_CONFIG",
        "UNSUPPORTED_TYPESCRIPT_CONFIG",
        "UNRESOLVED_TYPESCRIPT_ALIAS:src/a.ts:1:@domain/model",
    ],
)
def test_gate_three_architecture_blocks_only_gate_three(block: str) -> None:
    inputs = _inputs()
    inputs[0]["architecture"]["blocks"] = [block]
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS", "PASS", "BLOCK", *("PASS",) * 5]
    assert _entry(payload, 3)["policy_blocks"] == [block]


def test_gate_four_vocabulary_is_exact_and_ordered() -> None:
    assert standard_block_ownership.BLOCK_FAMILIES[3] == (
        "INVALID_NEW_LOCATION_JUSTIFICATION:",
        "MISSING_NEW_LOCATION_JUSTIFICATION:",
        "NEW_LOCATION_GATE_COVERAGE:",
        "NEW_MODULE_OWNER_NOT_PREEXISTING:",
        "PARALLEL_PACKAGE:",
        "UNRESOLVED_MODULE_OWNER:",
        "VAGUE_PRODUCTION_LOCATION:",
    )


def test_added_location_cannot_hide_missing_gate_four_evidence() -> None:
    payload = _compose(_inputs("src/orders/model.py", status="ADDED"))

    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


@pytest.mark.parametrize(
    ("field", "forged"),
    [
        ("changed_paths", []),
        ("new_paths", []),
        ("justifications", []),
        (
            "coupling_edges",
            [
                {
                    "internal": True,
                    "line": 1,
                    "source": "src/orders/model.py",
                    "specifier": "orders.owner",
                    "target": "src/orders/owner.py",
                }
            ],
        ),
        ("coverage", []),
        ("blocks", ["MISSING_NEW_LOCATION_JUSTIFICATION:src/orders/model.py"]),
    ],
)
def test_gate_four_aggregate_evidence_is_bound_to_source_facts(field: str, forged: object) -> None:
    inputs = _owned_new_location_inputs()
    inputs[0]["modularity"][field] = forged
    if field == "blocks":
        inputs[0]["policy_blocks"] = forged
        inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


def test_authentic_gate_four_poison_blocks_only_gate_four() -> None:
    path = "src/orders/model.py"
    block = f"MISSING_NEW_LOCATION_JUSTIFICATION:{path}"
    inputs = _owned_new_location_inputs(path)
    inputs[0]["review_evidence"]["module_boundaries"] = []
    inputs[0]["modularity"]["justifications"] = []
    inputs[0]["modularity"]["blocks"] = [block]
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS", "PASS", "PASS", "BLOCK", *["PASS"] * 4]
    assert _entry(payload, 4)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


def test_malformed_module_boundaries_evidence_blocks_only_gate_four() -> None:
    inputs = _inputs()
    block = "MALFORMED_REVIEW_EVIDENCE:module_boundaries"
    separation = inputs[0]["review_evidence"]["separation_of_concerns"]
    inputs[0]["review_evidence"] = {"separation_of_concerns": separation}
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS", "PASS", "PASS", "BLOCK", *["PASS"] * 4]
    assert _entry(payload, 4)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


def test_boundary_evidence_poison_blocks_gate_two_only() -> None:
    inputs = _inputs()
    block = "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries"
    inputs[0]["review_evidence"] = None
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS", "BLOCK", *["PASS"] * 6]
    assert _entry(payload, 2)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


@pytest.mark.parametrize(
    "block",
    [
        "MISSING_REVIEW_EVIDENCE:separation_of_concerns.boundaries[0].after",
        "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries[0].before",
    ],
)
def test_indexed_boundary_evidence_poison_blocks_gate_two_only(block: str) -> None:
    inputs = _inputs()
    inputs[0]["review_evidence"] = None
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS", "BLOCK", *["PASS"] * 6]
    assert _entry(payload, 2)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


def test_boundary_derivation_failure_is_gate_two_technical_only() -> None:
    inputs = _inputs()
    code = "SEPARATION_BOUNDARY_DERIVATION_FAILURE"
    inputs[0]["technical_errors"] = [{"code": code, "message": "invalid source"}]
    inputs[0]["overall_result"] = "TECHNICAL_FAILURE"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS", "TECHNICAL_FAILURE", *["PASS"] * 6]
    assert _entry(payload, 2)["technical_errors"] == [f"COMPLEXITY_RESULT:{code}"]
    assert standard_block_ownership.expected_technical_dependency(
        f"COMPLEXITY_RESULT:{code}", "complexity-result:technical-errors"
    ) == ("complexity-result:technical-errors", frozenset({2}))
    assert payload["shared_failures"] == []


def test_quality_provenance_binding_mismatch_is_gate_seven_technical_only() -> None:
    inputs = _inputs()
    inputs[3]["run_id"] = "999"

    payload = _compose(inputs)

    assert _results(payload) == [*("PASS",) * 6, "TECHNICAL_FAILURE", "PASS"]
    assert "QUALITY_RESULT_BINDING_MISMATCH" in _entry(payload, 7)["technical_errors"]


@pytest.mark.parametrize(
    ("field", "poison"),
    [
        ("artifact_id", "790"),
        ("artifact_digest", "e" * 64),
        ("capture_sha256", "f" * 64),
    ],
)
def test_valid_looking_quality_artifact_poison_is_gate_seven_technical_only(
    field: str, poison: str
) -> None:
    inputs = _inputs()
    inputs[3][field] = poison

    payload = _compose(inputs)

    assert _results(payload) == [*("PASS",) * 6, "TECHNICAL_FAILURE", "PASS"]
    assert _entry(payload, 7)["technical_errors"] == ["QUALITY_ARTIFACT_IDENTITY_MISMATCH"]
    assert payload["quality_artifact"] is None


def test_simultaneous_gate_three_and_gate_seven_poisons_remain_independent() -> None:
    inputs = _inputs()
    block = "IMPORT_CYCLE:src/a.py:1:src.b"
    inputs[0]["architecture"]["blocks"] = [block]
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"
    inputs[3]["run_id"] = "999"

    payload = _compose(inputs)

    assert _results(payload) == [
        "PASS",
        "PASS",
        "BLOCK",
        "PASS",
        "PASS",
        "PASS",
        "TECHNICAL_FAILURE",
        "PASS",
    ]


def test_unmapped_aggregate_analyzer_error_stays_with_aggregate_owners() -> None:
    inputs = _inputs()
    inputs[0]["technical_errors"] = [{"code": "SYNTAX_ERROR", "message": "invalid syntax"}]
    inputs[0]["overall_result"] = "TECHNICAL_FAILURE"
    for field in ("architecture", "modularity"):
        inputs[0][field] = None

    payload = _compose(inputs)

    expected = {1, 2, 3, 4, 7, 8}
    code = "COMPLEXITY_RESULT:SYNTAX_ERROR"
    assert _technical_standards(payload) == expected
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in expected)
    assert _entry(payload, 5)["result"] == "PASS"
    assert _entry(payload, 6)["result"] == "PASS"
    assert payload["quality_artifact"] == {
        "capture_sha256": "c" * 64,
        "digest": "d" * 64,
        "id": 789,
    }
    assert payload["shared_failures"] == [
        {
            "affected_standards": sorted(expected),
            "code": code,
            "dependency": "complexity-result:technical-errors",
            "kind": "TECHNICAL_ERROR",
        }
    ]

    mixed = copy.deepcopy(inputs)
    mixed[0]["architecture"] = {"invalid": True}
    mixed_payload = _compose(mixed)
    assert _technical_standards(mixed_payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"]
        for entry in mixed_payload["entries"]
    )

    isolated = copy.deepcopy(inputs)
    isolated[0]["technical_errors"] = [
        {"code": "MCCABE_GRAPH_MISMATCH", "message": "graph mismatch"}
    ]
    isolated[0]["architecture"] = _complexity()["architecture"]
    isolated[0]["modularity"] = _complexity()["modularity"]
    isolated_payload = _compose(isolated)
    assert _technical_standards(isolated_payload) == {1}
    assert _entry(isolated_payload, 1)["technical_errors"] == [
        "COMPLEXITY_RESULT:MCCABE_GRAPH_MISMATCH"
    ]
    assert _entry(isolated_payload, 7)["result"] == "PASS"
    assert isolated_payload["quality_artifact"] == {
        "capture_sha256": "c" * 64,
        "digest": "d" * 64,
        "id": 789,
    }
    assert standard_results.validate_payload(isolated_payload, IDENTITY) is None

    for field in ("changed_files", "commands", "gate_coverage", "quality_profile"):
        forged = copy.deepcopy(isolated)
        forged[0][field] = None if field == "quality_profile" else []
        forged_payload = _compose(forged)
        assert _technical_standards(forged_payload) == set(range(1, 9))
        assert all(
            entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"]
            for entry in forged_payload["entries"]
        )

    quality = _inputs()
    quality[0]["technical_errors"] = [
        {"code": "MISSING_QUALITY_EVIDENCE", "message": "quality evidence missing"}
    ]
    quality[0]["overall_result"] = "TECHNICAL_FAILURE"
    quality[0]["modularity"] = None
    quality[0]["quality_profile"] = None
    quality_payload = _compose(quality)
    assert _technical_standards(quality_payload) == {4, 7}

    simultaneous = copy.deepcopy(quality)
    simultaneous[0]["technical_errors"].insert(
        0, {"code": "MCCABE_GRAPH_MISMATCH", "message": "graph mismatch"}
    )
    simultaneous_payload = _compose(simultaneous)
    assert _technical_standards(simultaneous_payload) == {1, 4, 7}

    syntax = _inputs()
    syntax[0]["technical_errors"] = [
        {"code": "COMPLEXITY_SYNTAX_ERROR", "message": "invalid syntax"},
        {"code": "ARCHITECTURE_SYNTAX_ERROR", "message": "invalid syntax"},
    ]
    syntax[0]["overall_result"] = "TECHNICAL_FAILURE"
    syntax[0]["architecture"] = None
    syntax[0]["modularity"] = None
    syntax_payload = _compose(syntax)
    assert _technical_standards(syntax_payload) == {1, 3, 4}
    assert _entry(syntax_payload, 7)["result"] == "PASS"
    assert _entry(syntax_payload, 8)["result"] == "PASS"


def test_wrong_source_block_affects_source_dependents_and_claimed_owner_only() -> None:
    inputs = _inputs()
    block = "IMPORT_CYCLE:src/a.py:1:src.b"
    inputs[1]["policy_blocks"] = [block]
    inputs[1]["overall_result"] = "BLOCK"
    inputs[2]["characterization_sha256"] = hashlib.sha256(_canonical(inputs[1])).hexdigest()

    payload = _compose(inputs)

    expected = {3, 5, 6}
    code = f"STANDARD_BLOCK_SOURCE_MISMATCH:{block}"
    assert _technical_standards(payload) == expected
    assert payload["shared_failures"] == [
        {
            "affected_standards": sorted(expected),
            "code": code,
            "dependency": "characterization-result:policy-blocks",
            "kind": "TECHNICAL_ERROR",
        }
    ]


def test_malformed_shared_review_document_names_only_affected_lanes() -> None:
    inputs = _inputs()
    inputs[0]["review_evidence"] = None
    inputs[0]["policy_blocks"] = ["MALFORMED_REVIEW_EVIDENCE:document"]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == [*("BLOCK",) * 6, "PASS", "BLOCK"]
    assert payload["shared_failures"] == [
        {
            "affected_standards": [1, 2, 3, 4, 5, 6, 8],
            "code": "MALFORMED_REVIEW_EVIDENCE:document",
            "dependency": "structured-review-document",
            "kind": "POLICY_BLOCK",
        }
    ]

    subset = _inputs()
    subset[0]["review_evidence"] = None
    subset[0]["policy_blocks"] = ["INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.before"]
    subset[0]["overall_result"] = "BLOCK"
    subset_payload = _compose(subset)
    assert _results(subset_payload) == ["PASS", "BLOCK", *["PASS"] * 6]
    assert _entry(subset_payload, 2)["policy_blocks"] == [
        "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.before"
    ]
    assert _technical_standards(subset_payload) == set()
    assert subset_payload["shared_failures"] == []


def test_other_lane_poison_cannot_excuse_missing_gate_two_review_evidence() -> None:
    inputs = _inputs()
    inputs[0]["review_evidence"] = None
    inputs[0]["policy_blocks"] = ["MALFORMED_REVIEW_EVIDENCE:architecture.dependency_direction"]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


def test_unknown_root_review_key_is_shared_document_defect() -> None:
    block = "MALFORMED_REVIEW_EVIDENCE:review_evidence.unexpected"
    expected = frozenset({1, 2, 3, 4, 5, 6, 8})

    assert standard_block_ownership.review_owners(block) == expected
    assert standard_block_ownership.shared_dependency(block) == (
        "structured-review-document",
        expected,
    )


@pytest.mark.parametrize(
    "code",
    ["PROFILE_NODE_MISMATCH", "MCCABE_GRAPH_MISMATCH", "MISSING_FUNCTION_BODY"],
)
def test_metric_technical_failures_belong_only_to_gate_one(code: str) -> None:
    rendered = f"COMPLEXITY_RESULT:{code}"

    assert standard_block_ownership.technical_owners(rendered) == frozenset({1})
    assert standard_block_ownership.expected_technical_dependency(rendered, "") == (
        "complexity-result:technical-errors",
        frozenset({1}),
    )


def test_review_location_kind_must_be_emittable() -> None:
    impossible = "MISSING_REVIEW_EVIDENCE:architecture.not_a_field"
    emitted = "MALFORMED_REVIEW_EVIDENCE:architecture.not_a_field"
    indexed = "MISSING_REVIEW_EVIDENCE:separation_of_concerns.boundaries[0].after"
    invalid_index = "MALFORMED_REVIEW_EVIDENCE:separation_of_concerns.boundaries[01].after"
    invalid_suffix = "MALFORMED_REVIEW_EVIDENCE:separation_of_concerns.boundaries.invalid"

    assert standard_block_ownership.review_owners(impossible) == frozenset()
    assert standard_block_ownership.shared_dependency(impossible) is None
    assert standard_block_ownership.review_owners(emitted) == frozenset({3})
    assert standard_block_ownership.review_owners(indexed) == frozenset({2})
    assert standard_block_ownership.review_owners(invalid_index) == frozenset()
    assert standard_block_ownership.review_owners(invalid_suffix) == frozenset()


def test_one_added_document_line_is_authenticated_short_task() -> None:
    inputs = _inputs("docs/release-note.md", [7], status="ADDED")
    payload = _compose(inputs)

    assert payload["short_task"] is True
    assert payload["applicability_evidence"] == {
        "changed_files": inputs[0]["changed_files"],
        "classification": "SHORT_TASK",
        "source_sha256": hashlib.sha256(_canonical(inputs[0])).hexdigest(),
        "source_validated": True,
    }
    assert _results(payload) == [
        *("NOT_APPLICABLE_SHORT_TASK",) * 6,
        "PASS",
        "NOT_APPLICABLE_SHORT_TASK",
    ]
    assert "quality-provenance.json" in _entry(payload, 7)["evidence_sources"]
    assert payload["source_outcomes"]["quality"] == "success"
    assert standard_results.review_required(payload) is False


def test_cli_captures_one_added_document_line_without_broadening_other_files(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "target"
    repository.mkdir()

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout.strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "core.autocrlf", "false")
    git("remote", "add", "origin", "https://github.com/example/repository.git")
    (repository / "notes.txt").write_text("base\n", encoding="utf-8")
    git("add", "--all")
    git("commit", "-m", "base")
    base_sha = git("rev-parse", "HEAD")

    document = repository / "docs" / "release-note.md"
    document.parent.mkdir()
    document.write_text("release\n", encoding="utf-8")
    git("add", "--all")
    git("commit", "-m", "document")
    document_sha = git("rev-parse", "HEAD")

    policy = contract.parse_contract(
        (Path(__file__).parents[1] / ".supportability.toml").read_bytes()
    )
    records: list[git_changes.CommandRecord] = []
    identity = git_changes.inspect_repository(repository, base_sha, document_sha, records)
    assessments = cli._classify_changes(
        repository,
        identity,
        policy,
        git_changes.changed_paths(repository, base_sha, document_sha, records),
        records,
    )

    assert assessments[0].changed_head_lines == (1,)
    inputs = _inputs("docs/release-note.md", [1], status="ADDED")
    inputs[0]["changed_files"] = [
        {
            "base_production": assessments[0].base_production,
            "changed_head_lines": list(assessments[0].changed_head_lines),
            "complexity_assessed": assessments[0].complexity_assessed,
            "head_production": assessments[0].head_production,
            "new_path": assessments[0].change.new_path,
            "old_path": assessments[0].change.old_path,
            "status": assessments[0].change.status,
        }
    ]
    assert _compose(inputs)["short_task"] is True

    (repository / "notes.txt").write_text("head\n", encoding="utf-8")
    git("add", "--all")
    git("commit", "-m", "broad file")
    broad_sha = git("rev-parse", "HEAD")
    records = []
    identity = git_changes.inspect_repository(repository, document_sha, broad_sha, records)
    broad = cli._classify_changes(
        repository,
        identity,
        policy,
        git_changes.changed_paths(repository, document_sha, broad_sha, records),
        records,
    )

    assert broad[0].change.new_path == "notes.txt"
    assert broad[0].changed_head_lines == ()


@pytest.mark.parametrize(
    ("source", "outcome", "code", "expected"),
    [
        ("complexity", "failure", "MALFORMED_COMPLEXITY_RESULT", set(range(1, 9))),
        ("complexity", "cancelled", "MALFORMED_COMPLEXITY_RESULT", set(range(1, 9))),
        ("complexity", "skipped", "MALFORMED_COMPLEXITY_RESULT", set(range(1, 9))),
        (
            "characterization",
            "failure",
            "MALFORMED_CHARACTERIZATION_RESULT",
            {5, 6},
        ),
        ("refactor", "failure", "MALFORMED_REFACTOR_RESULT", {6}),
        ("quality", "failure", "MALFORMED_QUALITY_PROVENANCE", {7}),
    ],
)
def test_completed_passing_source_requires_success_outcome(
    source: str,
    outcome: str,
    code: str,
    expected: set[int],
    tmp_path: Path,
) -> None:
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes[source] = outcome

    payload = _compose(_inputs(), source_outcomes=outcomes)

    assert _technical_standards(payload) == expected
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in expected)

    forged = _compose(_inputs())
    forged["source_outcomes"][source] = outcome
    path = tmp_path / f"forged-{source}-{outcome}.json"
    path.write_text(json.dumps(forged), encoding="utf-8")
    assert standard_results_enforcer.main(_enforcer_arguments(path, 1)) == 2


def test_blocked_refactor_source_requires_failure_and_remains_a_policy_block() -> None:
    inputs = _inputs()
    block = "MISSING_OWNER_AUTHORIZATION"
    inputs[2]["policy_blocks"] = [block]
    inputs[2]["overall_result"] = "BLOCK"
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["refactor"] = "failure"

    payload = _compose(inputs, source_outcomes=outcomes)

    assert _entry(payload, 6)["result"] == "BLOCK"
    assert _entry(payload, 6)["policy_blocks"] == [block]
    assert _technical_standards(payload) == set()

    mismatched = _compose(inputs, source_outcomes=SUCCESS_OUTCOMES)
    assert _entry(mismatched, 6)["technical_errors"] == ["MALFORMED_REFACTOR_RESULT"]


def test_failed_quality_capture_authenticates_its_gate_seven_block() -> None:
    inputs = _inputs()
    block = "QUALITY_GATE_FAILED:python.pytest.v1"
    inputs[0]["quality_profile"]["commands"][0]["exit_code"] = 1
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _entry(payload, 7)["result"] == "BLOCK"
    assert _entry(payload, 7)["policy_blocks"] == [block]
    assert _technical_standards(payload) == set()


def test_failed_quality_capture_without_gate_seven_block_is_malformed() -> None:
    inputs = _inputs()
    inputs[0]["quality_profile"]["commands"][0]["exit_code"] = 1
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["quality"] = "failure"

    payload = _compose(inputs, source_outcomes=outcomes)

    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


@pytest.mark.parametrize(
    ("language", "adapter", "architecture_adapter", "path"),
    [
        ("python", "python.c901-touched.v1", "python.import-linter.v1", "src/sample.py"),
        (
            "typescript",
            "typescript.c901-equivalent-touched.v1",
            "typescript.import-boundaries.v1",
            "src/sample.ts",
        ),
    ],
)
def test_complexity_policy_exit_blocks_gate_one_only(
    language: str, adapter: str, architecture_adapter: str, path: str
) -> None:
    inputs = _inputs(path)
    head = _metric(path, "too_complex", 11)
    inputs[0]["functions"] = [
        _function(None, head, state="NEW", decision="BLOCK", debt=None, next_target=None)
    ]
    inputs[0]["language"] = language
    inputs[0]["overall_result"] = "BLOCK"
    inputs[0]["ruff_diagnostics"] = [_ruff(head)] if language == "python" else []
    inputs[0]["touched_qualified_functions"] = ["too_complex"]
    inputs[0]["architecture"]["adapter"] = architecture_adapter
    inputs[0]["gate_coverage"] = [
        {"adapter": adapter, "paths": ["src"]},
        {"adapter": architecture_adapter, "paths": ["src"]},
    ]
    inputs[0]["quality_profile"]["language"] = language
    inputs[0]["review_evidence"]["architecture"]["reviewed_paths"] = [path]
    inputs[0]["review_evidence"]["responsibility_boundary"]["path"] = path
    profile_command = inputs[0]["quality_profile"]["commands"][0]
    profile_command["adapter"] = adapter
    profile_command["exit_code"] = 1
    inputs[3]["commands"][0]["adapter"] = adapter
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["complexity"] = "failure"

    payload = _compose(inputs, source_outcomes=outcomes)

    assert _results(payload) == ["BLOCK", *("PASS",) * 7]
    assert _entry(payload, 1)["policy_blocks"] == ["FUNCTION_COMPLEXITY:too_complex"]
    assert payload["shared_failures"] == []
    assert standard_results.review_required(payload) is False

    forged = copy.deepcopy(inputs)
    forged[0]["policy_blocks"] = [f"QUALITY_GATE_FAILED:{adapter}"]
    forged_payload = _compose(forged, source_outcomes=outcomes)
    assert _technical_standards(forged_payload) == set(range(1, 9))


@pytest.mark.parametrize(
    ("language", "complexity_adapter", "architecture_adapter", "path"),
    [
        ("python", "python.c901-touched.v1", "python.import-linter.v1", "src/sample.py"),
        (
            "typescript",
            "typescript.c901-equivalent-touched.v1",
            "typescript.import-boundaries.v1",
            "src/sample.ts",
        ),
    ],
)
def test_architecture_policy_exit_blocks_gate_three_only(
    language: str, complexity_adapter: str, architecture_adapter: str, path: str
) -> None:
    inputs = _inputs(path)
    block = f"ARCHITECTURE_GATE_FAILED:{architecture_adapter}"
    inputs[0]["language"] = language
    inputs[0]["overall_result"] = "BLOCK"
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["architecture"]["adapter"] = architecture_adapter
    inputs[0]["gate_coverage"] = [
        {"adapter": complexity_adapter, "paths": ["src"]},
        {"adapter": architecture_adapter, "paths": ["src"]},
    ]
    inputs[0]["quality_profile"]["language"] = language
    inputs[0]["review_evidence"]["architecture"]["reviewed_paths"] = [path]
    inputs[0]["review_evidence"]["responsibility_boundary"]["path"] = path
    inputs[0]["quality_profile"]["commands"][0]["adapter"] = architecture_adapter
    inputs[0]["quality_profile"]["commands"][0]["exit_code"] = 1
    inputs[3]["commands"][0]["adapter"] = architecture_adapter
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["complexity"] = "failure"

    payload = _compose(inputs, source_outcomes=outcomes)

    assert _results(payload) == ["PASS", "PASS", "BLOCK", *("PASS",) * 5]
    assert _entry(payload, 3)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


def test_architecture_policy_exit_without_gate_three_evidence_is_malformed() -> None:
    inputs = _inputs()
    inputs[0]["quality_profile"]["commands"][0]["adapter"] = "python.import-linter.v1"
    inputs[0]["quality_profile"]["commands"][0]["exit_code"] = 1
    inputs[3]["commands"][0]["adapter"] = "python.import-linter.v1"

    payload = _compose(inputs)

    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


def test_progressive_complexity_policy_exit_remains_a_successful_capture() -> None:
    inputs = _inputs()
    base = _metric("src/sample.py", "legacy", 14)
    head = _metric("src/sample.py", "legacy", 12)
    inputs[0]["functions"] = [
        _function(
            base,
            head,
            state="EXISTING_LEGACY",
            decision="PASS_PROGRESSIVE",
            debt=2,
            next_target=10,
        )
    ]
    inputs[0]["ruff_diagnostics"] = [_ruff(head)]
    inputs[0]["touched_qualified_functions"] = ["legacy"]
    profile_command = inputs[0]["quality_profile"]["commands"][0]
    profile_command["adapter"] = "python.c901-touched.v1"
    profile_command["exit_code"] = 1
    inputs[3]["commands"][0]["adapter"] = "python.c901-touched.v1"

    payload = _compose(inputs, source_outcomes=SUCCESS_OUTCOMES)

    assert _results(payload) == ["PASS"] * 8
    assert payload["source_outcomes"]["quality"] == "success"
    assert standard_results.review_required(payload) is True

    forged = copy.deepcopy(inputs)
    forged[0]["language"] = "typescript"
    forged[0]["ruff_diagnostics"] = []
    assert _technical_standards(_compose(forged, source_outcomes=SUCCESS_OUTCOMES)) == set(
        range(1, 9)
    )


def test_complexity_adapter_tool_failure_still_blocks_gate_seven() -> None:
    inputs = _inputs()
    block = "QUALITY_GATE_FAILED:python.c901-touched.v1"
    profile_command = inputs[0]["quality_profile"]["commands"][0]
    profile_command["adapter"] = "python.c901-touched.v1"
    profile_command["exit_code"] = 2
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"
    inputs[3]["commands"][0]["adapter"] = "python.c901-touched.v1"

    payload = _compose(inputs)

    assert _entry(payload, 7)["result"] == "BLOCK"
    assert _entry(payload, 7)["policy_blocks"] == [block]
    assert _technical_standards(payload) == set()


@pytest.mark.parametrize(
    "poison",
    [
        "base-name",
        "head-path",
        "touched-name",
        "missing-ruff",
        "duplicate-function",
        "duplicate-ruff",
    ],
)
def test_function_evidence_must_cross_bind_to_changed_paths_and_ruff(poison: str) -> None:
    inputs = _inputs()
    base = _metric("src/sample.py", "legacy", 14)
    head = _metric("src/sample.py", "legacy", 12)
    function = _function(
        base,
        head,
        state="EXISTING_LEGACY",
        decision="PASS_PROGRESSIVE",
        debt=2,
        next_target=10,
    )
    inputs[0]["functions"] = [function]
    inputs[0]["ruff_diagnostics"] = [_ruff(head)]
    inputs[0]["touched_qualified_functions"] = ["legacy"]
    if poison == "base-name":
        base["qualified_name"] = "unrelated"
    elif poison == "head-path":
        head["path"] = "src/unrelated.py"
    elif poison == "touched-name":
        inputs[0]["touched_qualified_functions"] = ["unrelated"]
    elif poison == "missing-ruff":
        inputs[0]["ruff_diagnostics"] = []
    elif poison == "duplicate-function":
        inputs[0]["functions"].append(copy.deepcopy(function))
        inputs[0]["touched_qualified_functions"].append("legacy")
    else:
        inputs[0]["ruff_diagnostics"].append(copy.deepcopy(inputs[0]["ruff_diagnostics"][0]))

    payload = _compose(inputs)

    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


def test_function_evidence_must_intersect_changed_head_lines() -> None:
    inputs = _inputs(lines=[40])
    base = _metric("src/sample.py", "untouched", 1)
    head = _metric("src/sample.py", "untouched", 1)
    inputs[0]["functions"] = [
        _function(
            base,
            head,
            state="EXISTING",
            decision="PASS",
            debt=None,
            next_target=None,
        )
    ]
    inputs[0]["touched_qualified_functions"] = ["untouched"]

    payload = _compose(inputs)

    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


@pytest.mark.parametrize("poison", ["line", "message", "surplus"])
def test_ruff_evidence_must_bind_to_touched_function(poison: str) -> None:
    inputs = _inputs()
    base = _metric("src/sample.py", "legacy", 14)
    head = _metric("src/sample.py", "legacy", 12)
    inputs[0]["functions"] = [
        _function(
            base,
            head,
            state="EXISTING_LEGACY",
            decision="PASS_PROGRESSIVE",
            debt=2,
            next_target=10,
        )
    ]
    inputs[0]["ruff_diagnostics"] = [_ruff(head)]
    inputs[0]["touched_qualified_functions"] = ["legacy"]
    if poison == "line":
        inputs[0]["ruff_diagnostics"][0]["line"] = 31
    elif poison == "message":
        inputs[0]["ruff_diagnostics"][0]["message"] = "function is too complex (11 > 10)"
    else:
        surplus = _ruff(_metric("src/sample.py", "untouched", 11))
        inputs[0]["ruff_diagnostics"].append(surplus)

    payload = _compose(inputs)

    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


def test_short_task_ignores_irrelevant_characterization_and_refactor_outcomes() -> None:
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["characterization"] = "cancelled"
    outcomes["refactor"] = "skipped"

    payload = _compose(
        _inputs("docs/release-note.md", [1], status="ADDED"),
        source_outcomes=outcomes,
    )

    assert payload["short_task"] is True
    assert _results(payload) == [
        *("NOT_APPLICABLE_SHORT_TASK",) * 6,
        "PASS",
        "NOT_APPLICABLE_SHORT_TASK",
    ]


def test_corrupt_complexity_binding_cannot_authenticate_short_task() -> None:
    inputs = _inputs("docs/release-note.md", [7], status="ADDED")
    inputs[0]["head_sha"] = "e" * 40

    payload = _compose(inputs)

    assert payload["short_task"] is False
    assert payload["applicability_evidence"]["classification"] == "FULL_PROCESS"
    assert payload["applicability_evidence"]["source_validated"] is False
    assert _technical_standards(payload) == set(range(1, 9))
    assert payload["shared_failures"] == [
        {
            "affected_standards": list(range(1, 9)),
            "code": "COMPLEXITY_RESULT_BINDING_MISMATCH",
            "dependency": "complexity-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


def test_forged_short_results_are_rejected_by_applicability_evidence() -> None:
    payload = _compose(_inputs())
    payload["short_task"] = True
    for standard, entry in enumerate(payload["entries"], start=1):
        if standard == 7:
            continue
        entry["applicable"] = False
        entry["result"] = "NOT_APPLICABLE_SHORT_TASK"

    with pytest.raises(
        standard_results.StandardResultsError,
        match="MALFORMED_STANDARD_RESULTS_APPLICABILITY",
    ):
        standard_results.validate_payload(payload, IDENTITY)


@pytest.mark.parametrize(
    ("path", "lines"),
    [
        ("README.md", [1]),
        ("README.md", [1, 2]),
        ("docs/architecture.md", [1]),
        ("docs/architecture.md", [1, 2, 3]),
        ("docs/supportability_standard.md", [1]),
        ("docs/fixed_roadmap.md", [1]),
        ("docs/product_completion_contract.md", [1]),
    ],
)
def test_uncertain_or_broad_docs_default_to_full_process(path: str, lines: list[int]) -> None:
    payload = _compose(_inputs(path, lines))

    assert payload["short_task"] is False
    assert _results(payload) == ["PASS"] * 8
    assert standard_results.review_required(payload) is True


@pytest.mark.parametrize(
    ("case", "expected", "code", "dependency"),
    [
        ("architecture", set(range(1, 9)), "MALFORMED_COMPLEXITY_RESULT", "complexity-result"),
        ("modularity", set(range(1, 9)), "MALFORMED_COMPLEXITY_RESULT", "complexity-result"),
        ("review", set(range(1, 9)), "MALFORMED_COMPLEXITY_RESULT", "complexity-result"),
        (
            "aggregate_commands",
            set(range(1, 9)),
            "MALFORMED_COMPLEXITY_RESULT",
            "complexity-result",
        ),
        ("quality_profile", set(range(1, 9)), "MALFORMED_COMPLEXITY_RESULT", "complexity-result"),
        (
            "characterization",
            {5, 6},
            "MALFORMED_CHARACTERIZATION_RESULT",
            "characterization-result",
        ),
        ("refactor", {6}, "MALFORMED_REFACTOR_RESULT", None),
        ("quality_provenance", {7}, "MALFORMED_QUALITY_RESULT_BINDING", None),
    ],
)
def test_missing_critical_source_fields_fail_closed(
    case: str,
    expected: set[int],
    code: str,
    dependency: str | None,
) -> None:
    inputs = _inputs()
    if case in {"architecture", "modularity"}:
        inputs[0].pop(case)
    elif case == "review":
        inputs[0].pop("review_evidence")
    elif case == "aggregate_commands":
        inputs[0].pop("commands")
    elif case == "quality_profile":
        inputs[0]["quality_profile"].pop("commands")
    elif case == "characterization":
        inputs[1].pop("scenarios")
    elif case == "refactor":
        inputs[2].pop("applicable")
    else:
        inputs[3].pop("commands")

    payload = _compose(inputs)

    assert _technical_standards(payload) == expected
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in expected)
    if dependency:
        assert payload["shared_failures"] == [
            {
                "affected_standards": sorted(expected),
                "code": code,
                "dependency": dependency,
                "kind": "TECHNICAL_ERROR",
            }
        ]


@pytest.mark.parametrize("case", ["unknown", "duplicate", "stale", "spoofed", "misbound"])
def test_malformed_result_artifacts_fail_closed(case: str) -> None:
    payload = copy.deepcopy(_compose(_inputs()))
    if case == "unknown":
        payload["unknown"] = True
    elif case == "duplicate":
        payload["entries"][1] = copy.deepcopy(payload["entries"][0])
    elif case == "stale":
        payload["head_sha"] = "e" * 40
    elif case == "spoofed":
        payload["entries"][0]["policy_blocks"] = ["IMPORT_CYCLE:src/a.py:1:src.b"]
        payload["entries"][0]["result"] = "BLOCK"
    else:
        payload["repository_id"] = 999

    with pytest.raises(standard_results.StandardResultsError):
        standard_results.validate_payload(payload, IDENTITY)


def test_malformed_shared_failure_set_fails_closed() -> None:
    inputs = _inputs()
    inputs[0]["review_evidence"] = None
    inputs[0]["policy_blocks"] = ["MALFORMED_REVIEW_EVIDENCE:document"]
    inputs[0]["overall_result"] = "BLOCK"
    payload = _compose(inputs)
    payload["shared_failures"][0]["affected_standards"] = [1, 2, 3]

    with pytest.raises(standard_results.StandardResultsError):
        standard_results.validate_payload(payload, IDENTITY)


def test_gate_seven_technical_error_cannot_be_moved_to_another_lane() -> None:
    payload = _quality_failure_payload()
    quality_error = _entry(payload, 7)["technical_errors"].pop()
    _entry(payload, 7)["result"] = "PASS"
    _entry(payload, 1)["technical_errors"] = [quality_error]
    _entry(payload, 1)["result"] = "TECHNICAL_FAILURE"
    payload["quality_artifact"] = _compose(_inputs())["quality_artifact"]

    with pytest.raises(
        standard_results.StandardResultsError,
        match="MALFORMED_STANDARD_TECHNICAL_OWNERSHIP",
    ):
        standard_results.validate_payload(payload, IDENTITY)


def test_copied_generic_technical_error_across_rows_is_rejected() -> None:
    payload = _compose(_inputs())
    code = "GENERIC_TECHNICAL_FAILURE"
    for entry in payload["entries"]:
        entry["technical_errors"] = [code]
        entry["result"] = "TECHNICAL_FAILURE"
    payload["shared_failures"] = [
        {
            "affected_standards": list(range(1, 9)),
            "code": code,
            "dependency": "complexity-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]

    with pytest.raises(
        standard_results.StandardResultsError,
        match="MALFORMED_STANDARD_TECHNICAL_OWNERSHIP",
    ):
        standard_results.validate_payload(payload, IDENTITY)


def test_forged_shared_dependency_is_rejected_even_when_rows_match() -> None:
    inputs = _inputs()
    block = "MALFORMED_REVIEW_EVIDENCE:document"
    inputs[0]["review_evidence"] = None
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"
    payload = _compose(inputs)
    payload["shared_failures"][0]["dependency"] = "forged-dependency"

    with pytest.raises(
        standard_results.StandardResultsError,
        match="MALFORMED_SHARED_FAILURE",
    ):
        standard_results.validate_payload(payload, IDENTITY)


def test_synchronized_shared_ownership_spoof_is_rejected() -> None:
    payload = _compose(_inputs())
    block = "IMPORT_CYCLE:src/a.py:1:src.b"
    for standard in (3, 4):
        _entry(payload, standard)["policy_blocks"] = [block]
        _entry(payload, standard)["result"] = "BLOCK"
    payload["shared_failures"] = [
        {
            "affected_standards": [3, 4],
            "code": block,
            "dependency": "forged-dependency",
            "kind": "POLICY_BLOCK",
        }
    ]

    with pytest.raises(
        standard_results.StandardResultsError,
        match="MALFORMED_SHARED_FAILURE",
    ):
        standard_results.validate_payload(payload, IDENTITY)


def _enforcer_arguments(path: Path, standard: int) -> list[str]:
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
        "--input",
        str(path),
        "--standard",
        str(standard),
    ]


def test_exact_result_artifact_identity_mismatch_is_shared_via_enforcer(tmp_path: Path) -> None:
    payload = _compose(_inputs())
    payload["head_sha"] = "e" * 40
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert {
        standard_results_enforcer.main(_enforcer_arguments(path, standard))
        for standard in range(1, 9)
    } == {2}


def _producer_arguments(tmp_path: Path) -> tuple[list[str], dict[str, Path], Path]:
    inputs = _inputs()
    sources = {
        "complexity": inputs[0],
        "characterization": inputs[1],
        "refactor": inputs[2],
        "quality": inputs[3],
    }
    paths: dict[str, Path] = {}
    for source, value in sources.items():
        path = tmp_path / f"{source}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[source] = path
    output = tmp_path / "standard-results.json"
    return (
        [
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
            "--install-outcome",
            "success",
            "--expected-quality-artifact-id",
            EXPECTED_QUALITY_ARTIFACT["id"],
            "--expected-quality-artifact-digest",
            EXPECTED_QUALITY_ARTIFACT["digest"],
            "--expected-quality-capture-sha256",
            EXPECTED_QUALITY_ARTIFACT["capture_sha256"],
            "--complexity-result",
            str(paths["complexity"]),
            "--characterization-result",
            str(paths["characterization"]),
            "--refactor-result",
            str(paths["refactor"]),
            "--quality-provenance",
            str(paths["quality"]),
            "--complexity-outcome",
            "success",
            "--characterization-outcome",
            "success",
            "--refactor-outcome",
            "success",
            "--quality-outcome",
            "success",
            "--output",
            str(output),
        ],
        paths,
        output,
    )


@pytest.mark.parametrize(
    ("source", "expected_technical", "code", "dependency"),
    [
        (
            "complexity",
            set(range(1, 9)),
            "MISSING_COMPLEXITY_RESULT",
            "complexity-result",
        ),
        (
            "characterization",
            {5, 6},
            "MISSING_CHARACTERIZATION_RESULT",
            "characterization-result",
        ),
        ("refactor", {6}, "MISSING_REFACTOR_RESULT", None),
        ("quality", {7}, "MISSING_QUALITY_PROVENANCE", None),
    ],
)
def test_producer_missing_inputs_have_independent_outcomes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    source: str,
    expected_technical: set[int],
    code: str,
    dependency: str | None,
) -> None:
    arguments, paths, output = _producer_arguments(tmp_path)
    paths[source].unlink()
    arguments[arguments.index(f"--{source}-outcome") + 1] = "failure"

    assert standard_results_producer.main(arguments) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert capsys.readouterr().out.strip() == "false"
    assert _technical_standards(payload) == expected_technical
    assert payload["source_outcomes"][source] == "failure"
    assert all(
        entry["result"] == "PASS"
        for standard, entry in enumerate(payload["entries"], start=1)
        if standard not in expected_technical
    )
    assert all(
        _entry(payload, standard)["technical_errors"] == [code] for standard in expected_technical
    )
    if dependency:
        assert payload["shared_failures"] == [
            {
                "affected_standards": sorted(expected_technical),
                "code": code,
                "dependency": dependency,
                "kind": "TECHNICAL_ERROR",
            }
        ]


def test_missing_characterization_suppresses_derived_refactor_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments, paths, output = _producer_arguments(tmp_path)
    paths["characterization"].unlink()
    paths["refactor"].unlink()
    for source in ("characterization", "refactor"):
        arguments[arguments.index(f"--{source}-outcome") + 1] = "failure"

    assert standard_results_producer.main(arguments) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert capsys.readouterr().out.strip() == "false"
    assert _technical_standards(payload) == {5, 6}
    assert payload["shared_failures"] == [
        {
            "affected_standards": [5, 6],
            "code": "MISSING_CHARACTERIZATION_RESULT",
            "dependency": "characterization-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]
    assert all(
        "MISSING_REFACTOR_RESULT" not in entry["technical_errors"] for entry in payload["entries"]
    )


def test_install_failure_suppresses_all_derived_source_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments, paths, output = _producer_arguments(tmp_path)
    for path in paths.values():
        path.unlink()
    arguments[arguments.index("--install-outcome") + 1] = "failure"
    for source in ("complexity", "characterization", "refactor", "quality"):
        arguments[arguments.index(f"--{source}-outcome") + 1] = "skipped"

    assert standard_results_producer.main(arguments) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert capsys.readouterr().out.strip() == "false"
    assert _technical_standards(payload) == set(range(1, 9))
    assert payload["shared_failures"] == [
        {
            "affected_standards": list(range(1, 9)),
            "code": "GATE_INSTALL_FAILURE",
            "dependency": "gate-install",
            "kind": "TECHNICAL_ERROR",
        }
    ]
    assert payload["quality_artifact"] is None
    assert {code for entry in payload["entries"] for code in entry["technical_errors"]} == {
        "GATE_INSTALL_FAILURE"
    }


def _duplicate_root_key(value: dict[str, Any]) -> str:
    key = next(iter(value))
    return f"{json.dumps(value)[:-1]}, {json.dumps(key)}: null}}"


@pytest.mark.parametrize(
    ("source", "expected_technical", "code"),
    [
        ("complexity", set(range(1, 9)), "MALFORMED_COMPLEXITY_RESULT"),
        ("characterization", {5, 6}, "MALFORMED_CHARACTERIZATION_RESULT"),
        ("refactor", {6}, "MALFORMED_REFACTOR_RESULT"),
        ("quality", {7}, "MALFORMED_QUALITY_PROVENANCE"),
    ],
)
def test_producer_rejects_duplicate_json_keys(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    source: str,
    expected_technical: set[int],
    code: str,
) -> None:
    arguments, paths, output = _producer_arguments(tmp_path)
    value = json.loads(paths[source].read_text(encoding="utf-8"))
    paths[source].write_text(_duplicate_root_key(value), encoding="utf-8")

    assert standard_results_producer.main(arguments) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert capsys.readouterr().out.strip() == "false"
    assert _technical_standards(payload) == expected_technical
    assert all(
        _entry(payload, standard)["technical_errors"] == [code] for standard in expected_technical
    )


def _cycle_payload() -> dict[str, Any]:
    inputs = _inputs()
    block = "IMPORT_CYCLE:src/a.py:1:src.b"
    inputs[0]["architecture"]["blocks"] = [block]
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"
    return _compose(inputs)


def _quality_failure_payload() -> dict[str, Any]:
    inputs = _inputs()
    inputs[3]["run_id"] = "999"
    return _compose(inputs)


@pytest.mark.parametrize(
    ("payload_factory", "standard", "expected"),
    [
        (lambda: _compose(_inputs()), 1, 0),
        (_cycle_payload, 3, 1),
        (_cycle_payload, 1, 0),
        (_quality_failure_payload, 7, 2),
        (_quality_failure_payload, 1, 0),
        (lambda: _compose(_inputs("docs/release-note.md", [1], status="ADDED")), 1, 0),
    ],
)
def test_enforcer_exit_codes(
    tmp_path: Path, payload_factory: Any, standard: int, expected: int
) -> None:
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload_factory()), encoding="utf-8")

    assert standard_results_enforcer.main(_enforcer_arguments(path, standard)) == expected


def test_enforcer_malformed_artifact_exits_technical(tmp_path: Path) -> None:
    path = tmp_path / "standard-results.json"
    path.write_text("{}", encoding="utf-8")

    assert standard_results_enforcer.main(_enforcer_arguments(path, 1)) == 2


def test_enforcer_duplicate_json_key_exits_technical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "standard-results.json"
    path.write_text(_duplicate_root_key(_compose(_inputs())), encoding="utf-8")

    assert standard_results_enforcer.main(_enforcer_arguments(path, 1)) == 2
    assert capsys.readouterr().out.strip() == "MALFORMED_STANDARD_RESULTS"


def _job(name: str, next_name: str | None) -> str:
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/organization-required.yml"
    ).read_text(encoding="utf-8")
    body = workflow.split(f"\n  {name}:\n", 1)[1]
    return body.split(f"\n  {next_name}:\n", 1)[0] if next_name else body


def test_workflow_wires_conditional_reviews_independent_matrix_and_final_gate() -> None:
    quality = _job("quality-profile", "deterministic-evidence")
    evidence = _job("deterministic-evidence", "observe-codex-review")
    observer = _job("observe-codex-review", "collect-codex-review")
    connector = _job("collect-codex-review", "standard-results")
    matrix = _job("standard-results", "supportability-gate")
    gate = _job("supportability-gate", None)

    assert "capture-outcome: ${{ steps.capture.outcome }}" in quality
    assert "id: capture\n        continue-on-error: true" in quality
    assert "id: upload\n        if: always()" in quality
    assert 'exit "$status"' in quality
    assert "review-required: ${{ steps.standard_results.outputs.review-required }}" in evidence
    assert "python -P -m supportability_gate.standard_results_producer" in evidence
    assert '--install-outcome "${{ steps.install.outcome }}"' in evidence
    download_quality = evidence.split("- name: Download authenticated quality evidence", 1)[
        1
    ].split("- name: Read back GitHub artifact metadata", 1)[0]
    artifact_metadata = evidence.split("- name: Read back GitHub artifact metadata", 1)[1].split(
        "- name: Verify exact behavior characterization", 1
    )[0]
    assert "continue-on-error: true" in download_quality
    assert "continue-on-error: true" in artifact_metadata
    evaluate = evidence.split("- name: Evaluate immutable pull-request commits", 1)[1].split(
        "- name: Compose eight independently enforceable results", 1
    )[0]
    assert "if: ${{ always() && steps.install.outcome == 'success' }}" in evaluate
    assert "steps.download_quality.outcome == 'success'" not in evaluate
    assert "steps.artifact_metadata.outcome == 'success'" not in evaluate
    assert (
        "--quality-outcome \"${{ needs.quality-profile.outputs.capture-outcome != 'success' "
        "&& needs.quality-profile.outputs.capture-outcome || "
        "steps.download_quality.outcome != 'success' && steps.download_quality.outcome || "
        'steps.artifact_metadata.outcome }}"' in evidence
    )
    assert (
        '--expected-quality-artifact-id "${{ needs.quality-profile.outputs.artifact-id }}"'
        in evidence
    )
    assert (
        '--expected-quality-artifact-digest "${{ needs.quality-profile.outputs.artifact-digest }}"'
        in evidence
    )
    assert (
        '--expected-quality-capture-sha256 "${{ needs.quality-profile.outputs.capture-sha256 }}"'
        in evidence
    )
    condition = "needs.deterministic-evidence.outputs.review-required == 'true'"
    assert condition in observer
    assert condition in connector
    rows = re.findall(r"(?m)^          - standard: ([1-8])\n            context: (.+)$", matrix)
    assert rows == [
        (str(standard), context)
        for standard, context in enumerate(standard_results.CHECK_CONTEXTS, start=1)
    ]
    assert "fail-fast: false" in matrix
    assert "python -P -m supportability_gate.standard_results_enforcer" in matrix
    assert "if: always()" in matrix
    assert "name: Supportability Gate" in gate
    assert "standard-results" in gate
    assert "REVIEW_REQUIRED: ${{ needs.deterministic-evidence.outputs.review-required }}" in gate
    assert "STANDARD_RESULTS_RESULT: ${{ needs.standard-results.result }}" in gate
    assert "COLLECTOR_RESULT: ${{ needs.collect-codex-review.result }}" in gate
    assert 'if [ "$OBSERVER_RESULT" != "skipped" ]' in gate
    assert '[ "$COLLECTOR_RESULT" != "skipped" ]' in gate
