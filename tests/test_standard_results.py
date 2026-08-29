from __future__ import annotations

import copy
import hashlib
import json
import pkgutil
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import supportability_gate
from supportability_gate import (
    clause_inventory,
    cli,
    contract,
    git_changes,
    quality_profile,
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
    "capture_sha256": "54a168bb3fe0cb5640dfaa0e5ec96862fa3ff98b42702e9d6c71a0845c42d753",
    "digest": "d" * 64,
    "id": "789",
}
EXPECTED_CHARACTERIZATION_ARTIFACTS = {
    "base": {"capture_sha256": "3" * 64, "digest": "4" * 64, "id": "701"},
    "head": {"capture_sha256": "5" * 64, "digest": "6" * 64, "id": "702"},
}
REVIEW_BINDING = {
    "base": {"blob_sha": "7" * 40, "sha256": "7" * 64},
    "head": {"blob_sha": "8" * 40, "sha256": "8" * 64},
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
        "review_handoff": {
            "remaining_risks": ["DERIVED_FROM_AUTHENTICATED_EVIDENCE"],
            "summary": "DERIVED_FROM_AUTHENTICATED_EVIDENCE",
        },
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


def _quality_arguments(
    adapter: str,
    language: str,
    production_files: tuple[str, ...],
    test_files: tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    template = dict(quality_profile.command_templates(language))[adapter]
    scalar_values = {
        "$LINT_IMPORTS": "lint-imports",
        "$NODE": "node",
        "$NPM": "npm",
        "$OUTPUT": "C:/quality",
        "$PYTHON": "python",
        "$REPOSITORY": "C:/repo/target",
        "$TOOLS": "C:/quality/quality-tools",
    }
    list_values = {
        "$SOURCE_FILES": tuple(f"C:/repo/target/{path}" for path in production_files),
        "$TEST_FILES": tuple(f"C:/repo/target/{path}" for path in test_files),
    }
    executed: list[str] = []
    for argument in template:
        if argument in list_values:
            executed.extend(list_values[argument])
            continue
        for token, replacement in scalar_values.items():
            argument = argument.replace(token, replacement)
        executed.append(argument)
    return list(template), executed


def _profile_commands(
    language: str, production_files: tuple[str, ...], test_files: tuple[str, ...] = ()
) -> list[dict[str, object]]:
    commands: list[dict[str, object]] = []
    for adapter in quality_profile.required_adapters(language):
        template, _ = _quality_arguments(adapter, language, production_files, test_files)
        commands.append(
            {
                "adapter": adapter,
                "arguments": template,
                "executed": True,
                "exit_code": 0,
                "observed_paths": list(production_files),
                "proof_kind": quality_profile.expected_proof_kind(adapter),
                "zero_statement_paths": [],
            }
        )
    return commands


def _provenance_commands(
    language: str, production_files: tuple[str, ...], test_files: tuple[str, ...] = ()
) -> list[dict[str, object]]:
    return [
        {
            "adapter": adapter,
            "executed_arguments": _quality_arguments(
                adapter, language, production_files, test_files
            )[1],
            "raw_proof_sha256": "a" * 64,
            "stderr_sha256": "b" * 64,
            "stdout_sha256": "c" * 64,
        }
        for adapter in quality_profile.required_adapters(language)
    ]


def _set_quality_commands(
    inputs: tuple[dict[str, Any], ...],
    language: str,
    production_files: tuple[str, ...],
    test_files: tuple[str, ...] = (),
) -> None:
    production_files = tuple(sorted(production_files))
    profile = inputs[0]["quality_profile"]
    profile["language"] = language
    profile["production_files"] = list(production_files)
    profile["source_files"] = list(production_files)
    profile["asset_receipts"] = []
    profile["test_files"] = list(test_files)
    profile["commands"] = _profile_commands(language, production_files, test_files)
    inputs[3]["commands"] = _provenance_commands(language, production_files, test_files)


def _quality_command(inputs: tuple[dict[str, Any], ...], adapter: str) -> dict[str, Any]:
    return next(
        command
        for command in inputs[0]["quality_profile"]["commands"]
        if command["adapter"] == adapter
    )


def _complexity(
    path: str = "src/sample.py",
    lines: list[int] | None = None,
    *,
    status: str = "MODIFIED",
) -> dict[str, Any]:
    lines = lines or [1]
    targets = [f"{path}::module:{path}:1-1"] if path.startswith("src/") else []
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
        "responsibility_targets": targets,
        "quality_profile": {
            "base_sha": IDENTITY.base_sha,
            "changed_paths": [path],
            "commands": _profile_commands("python", ("src/sample.py",)),
            "exclusions": [],
            "head_sha": IDENTITY.head_sha,
            "high_risk_paths": [],
            "language": "python",
            "maximum_complexity": 10,
            "asset_receipts": [],
            "production_files": ["src/sample.py"],
            "production_paths": ["src"],
            "repository_remote": f"github.com/{IDENTITY.repository}",
            "schema_version": "quality-gates.v6",
            "source_files": ["src/sample.py"],
            "test_files": [],
            "workflow_sha": IDENTITY.workflow_sha,
        },
        "rename_bindings": [],
        "repository_remote": f"github.com/{IDENTITY.repository}",
        "review_evidence": _review_evidence(),
        "review_evidence_binding": copy.deepcopy(REVIEW_BINDING),
        "review_evidence_path": ".supportability-review.toml",
        "ruff_diagnostics": [],
        "schema_version": "1.0",
        "standard_sha256": clause_inventory.STANDARD_SHA256,
        "technical_errors": [],
        "tool_versions": {},
        "touched_qualified_functions": [],
        "unbounded_production_paths": [],
    }


def _characterization(path: str = "src/sample.py") -> dict[str, Any]:
    targets = [f"{path}::module:{path}:1-1"] if path.startswith("src/") else []
    return {
        "artifacts": {
            "base": {"capture_sha256": "3" * 64, "digest": "4" * 64, "id": "701"},
            "head": {"capture_sha256": "5" * 64, "digest": "6" * 64, "id": "702"},
        },
        "base_sha": IDENTITY.base_sha,
        "behavior_fingerprint": hashlib.sha256(_canonical([["sample", "e" * 64]])).hexdigest(),
        "coverage": {
            "covered_paths": [path],
            "required_paths": [path] if path.startswith("src/") else [],
        },
        "head_sha": IDENTITY.head_sha,
        "manifest_blob_sha": "8" * 40,
        "manifest_sha256": "9" * 64,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": f"github.com/{IDENTITY.repository}",
        "refactor_runnability": {
            "base_sha": IDENTITY.base_sha,
            "head_sha": IDENTITY.head_sha,
            "repository": f"github.com/{IDENTITY.repository}",
            "runnable": True,
            "schema_version": "refactor-runnability.v1",
            "targets": targets,
            "unbounded_paths": [],
            "workflow_sha": IDENTITY.workflow_sha,
        },
        "scenarios": [
            {
                "base_behavior_sha256": "e" * 64,
                "command": ["python", "tests/characterization/sample.characterization.py"],
                "compatibility": "PASS",
                "covers": [path],
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
    applicable = path.startswith("src/")
    targets = [f"{path}::module:{path}:1-1"] if applicable else []
    return {
        "applicable": applicable,
        "authorization": (
            {
                "base_sha": IDENTITY.base_sha,
                "broad": False,
                "head_sha": IDENTITY.head_sha,
                "repository": IDENTITY.repository,
                "scope": [path],
                "sequence": {"predecessor_sha": IDENTITY.base_sha, "step": 1},
                "targets": targets,
            }
            if applicable
            else None
        ),
        "authorization_comment_id": 11 if applicable else None,
        "base_sha": IDENTITY.base_sha,
        "characterization_sha256": hashlib.sha256(_canonical(characterization)).hexdigest(),
        "changed_paths": [path],
        "head_sha": IDENTITY.head_sha,
        "other_standard_clauses_waived": False,
        "overall_result": "PASS",
        "policy_blocks": [],
        "predecessor": {
            "authorization": None,
            "authorization_comment_id": None,
            "base_sha": None,
            "block": None,
            "head_sha": None,
            "merge_sha": None,
            "pull_number": None,
        },
        "repository": IDENTITY.repository,
        "schema_version": "refactor-policy-result.v1",
        "targets": targets,
        "unbounded_paths": [],
    }


def _quality() -> dict[str, Any]:
    return {
        "artifact_digest": "d" * 64,
        "artifact_id": "789",
        "capture_sha256": "54a168bb3fe0cb5640dfaa0e5ec96862fa3ff98b42702e9d6c71a0845c42d753",
        "commands": _provenance_commands("python", ("src/sample.py",)),
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


def _quality_capture(inputs: tuple[dict[str, Any], ...]) -> str:
    profile = copy.deepcopy(inputs[0]["quality_profile"])
    provenance = inputs[3]
    proof = {item["adapter"]: item for item in provenance["commands"]}
    profile["commands"] = [
        {**command, **proof[command["adapter"]]} for command in profile["commands"]
    ]
    profile.update(
        {
            name: provenance[name]
            for name in (
                "job",
                "repository",
                "repository_id",
                "run_attempt",
                "run_id",
                "runner_environment",
            )
        }
    )
    profile.update({"artifact_digest": "", "artifact_id": "", "capture_sha256": ""})
    return hashlib.sha256(
        (json.dumps(profile, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()


def _bind_quality(inputs: tuple[dict[str, Any], ...]) -> dict[str, str]:
    capture = _quality_capture(inputs)
    inputs[3]["capture_sha256"] = capture
    return {
        "capture_sha256": capture,
        "digest": inputs[3]["artifact_digest"],
        "id": inputs[3]["artifact_id"],
    }


def _step_two_refactor(inputs: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    refactor = inputs[2]
    current = refactor["authorization"]
    assert isinstance(current, dict)
    current["sequence"]["step"] = 2
    predecessor = {
        "authorization": {
            "base_sha": "c" * 40,
            "broad": current["broad"],
            "head_sha": "d" * 40,
            "repository": IDENTITY.repository,
            "scope": list(current["scope"]),
            "sequence": {"predecessor_sha": "c" * 40, "step": 1},
            "targets": list(current["targets"]),
        },
        "authorization_comment_id": 10,
        "base_sha": "c" * 40,
        "block": None,
        "head_sha": "d" * 40,
        "merge_sha": IDENTITY.base_sha,
        "pull_number": 6,
    }
    refactor["predecessor"] = predecessor
    return predecessor


def _owned_new_location_inputs(
    path: str = "src/orders/model.py",
    *,
    language: str = "python",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    inputs = _inputs(path, status="ADDED")
    complexity = inputs[0]
    adapters = {
        "python": ("python.c901-touched.v1", "python.import-linter.v1"),
        "typescript": (
            "typescript.c901-equivalent-touched.v1",
            "typescript.import-boundaries.v1",
        ),
    }[language]
    complexity["language"] = language
    complexity["architecture"]["adapter"] = adapters[1]
    complexity["gate_coverage"] = [{"adapter": adapter, "paths": ["src"]} for adapter in adapters]
    complexity["quality_profile"]["language"] = language
    complexity["quality_profile"]["production_files"] = [path]
    complexity["review_evidence"]["architecture"]["reviewed_paths"] = [path]
    complexity["review_evidence"]["responsibility_boundary"]["path"] = path
    claim = {
        "basis": "domain",
        "justification": "Exact source path owns one cohesive boundary.",
        "owner_path": path,
        "path": path,
    }
    sorted_adapters = sorted(adapters)
    _set_quality_commands(inputs, language, (path,))
    complexity["review_evidence"]["module_boundaries"] = [claim]
    complexity["modularity"] = {
        "blocks": [],
        "changed_paths": [path],
        "coupling_edges": [],
        "coverage": [{"adapters": sorted_adapters, "architecture": True, "path": path}],
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
    expected_characterization_artifacts: dict[str, Any] | None = None,
    expected_quality_artifact: dict[str, Any] | None = None,
    source_errors: dict[str, str] | None = None,
    source_outcomes: dict[str, str] | None = None,
) -> dict[str, Any]:
    if expected_quality_artifact is None:
        try:
            expected_quality_artifact = _bind_quality(inputs)
        except (KeyError, TypeError):
            expected_quality_artifact = EXPECTED_QUALITY_ARTIFACT
    if source_outcomes is None:
        source_outcomes = dict(SUCCESS_OUTCOMES)
        for source, value in zip(("complexity", "characterization", "refactor"), inputs[:3]):
            source_outcomes[source] = (
                "success" if value.get("overall_result") == "PASS" else "failure"
            )
        profile = inputs[0].get("quality_profile")
        if isinstance(profile, dict):
            failed_command = any(
                not command["executed"] or command["exit_code"]
                for command in profile.get("commands", [])
            )
            failed_asset = any(
                receipt.get("result") != "PASS"
                for receipt in profile.get("asset_receipts", [])
                if isinstance(receipt, dict)
            )
            if failed_command or failed_asset:
                source_outcomes["quality"] = "failure"
    return standard_results.compose_results(
        *inputs,
        IDENTITY,
        expected_characterization_artifacts=(
            EXPECTED_CHARACTERIZATION_ARTIFACTS
            if expected_characterization_artifacts is None
            else expected_characterization_artifacts
        ),
        expected_quality_artifact=expected_quality_artifact,
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


def test_gate_eight_exposes_exact_canonical_handoff_facts() -> None:
    inputs = _inputs()
    payload = _compose(inputs)

    handoff = payload["review_handoff"]
    assert handoff["schema_version"] == "review-handoff.v1"
    assert handoff["identity"] == {
        "base_sha": IDENTITY.base_sha,
        "characterization_artifacts": EXPECTED_CHARACTERIZATION_ARTIFACTS,
        "characterization_result_sha256": hashlib.sha256(_canonical(inputs[1])).hexdigest(),
        "complexity_result_sha256": hashlib.sha256(_canonical(inputs[0])).hexdigest(),
        "head_sha": IDENTITY.head_sha,
        "quality_artifact": {
            "capture_sha256": EXPECTED_QUALITY_ARTIFACT["capture_sha256"],
            "digest": EXPECTED_QUALITY_ARTIFACT["digest"],
            "id": 789,
        },
        "repository": IDENTITY.repository,
        "repository_id": IDENTITY.repository_id,
        "refactor_result_sha256": hashlib.sha256(_canonical(inputs[2])).hexdigest(),
        "review_evidence": REVIEW_BINDING,
        "run_attempt": IDENTITY.run_attempt,
        "run_id": IDENTITY.run_id,
        "quality_provenance_sha256": hashlib.sha256(_canonical(inputs[3])).hexdigest(),
        "workflow_sha": IDENTITY.workflow_sha,
    }
    assert handoff["changed_files"] == [
        {
            "base_production": True,
            "changed_head_lines": [1],
            "complexity_assessed": True,
            "head_production": True,
            "new_path": "src/sample.py",
            "old_path": "src/sample.py",
            "status": "MODIFIED",
        }
    ]
    assert handoff["responsibility_boundaries"] == []
    assert handoff["functions"] == []
    assert handoff["validation"]["source_outcomes"] == SUCCESS_OUTCOMES
    assert handoff["validation"]["commands"][0]["adapter"] == "python.ruff-lint.v1"
    assert handoff["validation"]["commands"][0]["executed_arguments"][:3] == [
        "python",
        "-I",
        "-m",
    ]
    assert handoff["coverage"] == {
        "candidate_contract_changed": False,
        "changed_paths": ["src/sample.py"],
        "exclusions": [],
        "gate_coverage": [
            {"adapter": "python.c901-touched.v1", "paths": ["src"]},
            {"adapter": "python.ast-imports.v1", "paths": ["src"]},
        ],
        "high_risk_paths": [],
        "maximum_complexity": 10,
        "asset_receipts": [],
        "production_files": ["src/sample.py"],
        "production_paths": ["src"],
        "source_files": ["src/sample.py"],
        "scope_state": "UNCHANGED",
        "test_files": [],
        "threshold_state": "UNCHANGED",
        "untested_paths": [],
    }
    assert handoff["risks"] == []
    assert handoff["gaps"] == []
    assert handoff["follow_up"] == []
    responsibility_facts = {
        "boundaries": handoff["responsibility_boundaries"],
        "targets": handoff["responsibility_targets"],
    }
    source_facts = {
        "change": (["complexity-result.json:changed_files"], handoff["changed_files"]),
        "coverage": (
            [
                "complexity-result.json:gate_coverage",
                "complexity-result.json:policy_blocks",
                "complexity-result.json:quality_profile",
            ],
            handoff["coverage"],
        ),
        "functions": (["complexity-result.json:functions"], handoff["functions"]),
        "identity": (
            [
                "characterization-result.json",
                "complexity-result.json",
                "quality-provenance.json",
                "refactor-policy-result.json",
                "workflow-run-identity",
            ],
            handoff["identity"],
        ),
        "responsibilities": (
            [
                "complexity-result.json:responsibility_targets",
                "complexity-result.json:review_evidence.separation_of_concerns",
            ],
            responsibility_facts,
        ),
        "review_identity": (
            ["complexity-result.json:review_evidence_binding"],
            handoff["identity"]["review_evidence"],
        ),
        "validation": (
            [
                "complexity-result.json:quality_profile.commands",
                "quality-provenance.json:commands",
            ],
            handoff["validation"]["commands"],
        ),
    }
    assert handoff["sources"] == {
        name: {
            "citations": citations,
            "sha256": hashlib.sha256(_canonical(fact)).hexdigest(),
        }
        for name, (citations, fact) in source_facts.items()
    }
    assert payload["review_handoff_sha256"] == hashlib.sha256(_canonical(handoff)).hexdigest()
    assert (
        "complexity-result.json:review_evidence_binding" in _entry(payload, 8)["evidence_sources"]
    )


def test_handoff_exposes_only_source_bound_boundary_identities() -> None:
    inputs = _inputs()
    inputs[0]["review_evidence"]["responsibility_boundary"] = {
        "does_not_own": "Fictional exclusion.",
        "owns": "Fictional ownership.",
        "path": "src/unrelated.py",
    }
    inputs[0]["review_evidence"]["separation_of_concerns"] = {
        "after": "Fictional after claim.",
        "before": "Fictional before claim.",
        "boundaries": [
            {
                "after": "Fictional boundary after.",
                "before": "Fictional boundary before.",
                "kind": "function",
                "path": "src/sample.py",
                "symbol": "calculate",
            }
        ],
    }

    handoff = _compose(inputs)["review_handoff"]

    assert handoff["responsibility_boundaries"] == [
        {"kind": "function", "path": "src/sample.py", "symbol": "calculate"}
    ]
    assert "Fictional" not in json.dumps(handoff)


@pytest.mark.parametrize(
    ("block", "field", "state"),
    [
        ("QUALITY_THRESHOLD_WEAKENING", "threshold_state", "WEAKENED"),
        ("QUALITY_THRESHOLD_MISMATCH", "threshold_state", "MISMATCH"),
        ("QUALITY_SCOPE_NARROWING", "scope_state", "NARROWED"),
    ],
)
def test_handoff_reports_authenticated_threshold_and_scope_blocks(
    block: str, field: str, state: str
) -> None:
    inputs = _inputs()
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert payload["review_handoff"]["coverage"][field] == state


def test_handoff_does_not_claim_unchanged_scope_without_quality_profile() -> None:
    inputs = _inputs()
    inputs[0]["technical_errors"] = [
        {"code": "MALFORMED_QUALITY_EVIDENCE", "message": "quality evidence missing"}
    ]
    inputs[0]["overall_result"] = "TECHNICAL_FAILURE"
    inputs[0]["quality_profile"] = None
    inputs[0]["modularity"] = None

    coverage = _compose(inputs)["review_handoff"]["coverage"]

    assert coverage["scope_state"] == "UNVERIFIED"
    assert coverage["threshold_state"] == "UNVERIFIED"


def test_handoff_does_not_use_malformed_quality_profile_as_truth() -> None:
    inputs = _inputs()
    inputs[0]["quality_profile"]["schema_version"] = "quality-gates.v3"
    inputs[0]["quality_profile"]["maximum_complexity"] = 999

    coverage = _compose(inputs)["review_handoff"]["coverage"]

    assert coverage["maximum_complexity"] is None
    assert coverage["scope_state"] == "UNVERIFIED"
    assert coverage["threshold_state"] == "UNVERIFIED"


def test_stale_review_handoff_blocks_gate_eight_only() -> None:
    inputs = _inputs()
    inputs[0]["review_evidence_binding"]["head"] = copy.deepcopy(
        inputs[0]["review_evidence_binding"]["base"]
    )

    payload = _compose(inputs)

    assert _results(payload) == [*(["PASS"] * 7), "BLOCK"]
    assert _entry(payload, 8)["policy_blocks"] == ["STALE_HANDOFF_EVIDENCE"]
    assert payload["shared_failures"] == []


def test_unbound_review_handoff_blocks_gate_eight_only() -> None:
    inputs = _inputs()
    inputs[0]["review_evidence_binding"]["head"] = None

    payload = _compose(inputs)

    assert _results(payload) == [*(["PASS"] * 7), "BLOCK"]
    assert _entry(payload, 8)["policy_blocks"] == ["UNAUTHENTICATED_HANDOFF_EVIDENCE"]
    assert payload["shared_failures"] == []


def test_missing_review_handoff_summary_blocks_gate_eight_only() -> None:
    inputs = _inputs()
    block = "MISSING_REVIEW_EVIDENCE:review_handoff.summary"
    inputs[0]["review_evidence"] = {
        "separation_of_concerns": inputs[0]["review_evidence"]["separation_of_concerns"]
    }
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == [*(["PASS"] * 7), "BLOCK"]
    assert _entry(payload, 8)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


@pytest.mark.parametrize(
    ("field", "value", "block"),
    [
        (
            "summary",
            "All validation passed.",
            "UNSUPPORTED_HANDOFF_CLAIM:review_handoff.summary",
        ),
        (
            "remaining_risks",
            ["None."],
            "UNSUPPORTED_HANDOFF_CLAIM:review_handoff.remaining_risks",
        ),
    ],
)
def test_schema_valid_ungrounded_handoff_claim_blocks_gate_eight_only(
    field: str, value: object, block: str
) -> None:
    inputs = _inputs()
    inputs[0]["review_evidence"]["review_handoff"][field] = value

    payload = _compose(inputs)

    assert _results(payload) == [*(["PASS"] * 7), "BLOCK"]
    assert _entry(payload, 8)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


def test_declared_unsupported_handoff_claim_blocks_gate_eight_only() -> None:
    inputs = _inputs()
    block = "UNSUPPORTED_HANDOFF_CLAIM:review_handoff.summary"
    inputs[0]["review_evidence"] = {
        "separation_of_concerns": inputs[0]["review_evidence"]["separation_of_concerns"]
    }
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == [*(["PASS"] * 7), "BLOCK"]
    assert _entry(payload, 8)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


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


def test_current_refactor_schema_has_authenticated_applicability() -> None:
    inputs = _inputs()

    payload = _compose(inputs)

    assert inputs[2]["applicable"] is True
    assert _entry(payload, 6)["result"] == "PASS"


def test_gate_six_vocabulary_is_exact_and_ordered() -> None:
    assert standard_block_ownership.BLOCK_FAMILIES[5] == (
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
    )


@pytest.mark.parametrize(
    "defect", ["applicable", "scope", "targets", "target_shape", "authorization"]
)
def test_refactor_result_cross_binds_authenticated_change_evidence(defect: str) -> None:
    inputs = _inputs()
    target = "src/sample.py::function:calculate:1-2"
    authorization = {
        "base_sha": IDENTITY.base_sha,
        "broad": False,
        "head_sha": IDENTITY.head_sha,
        "repository": IDENTITY.repository,
        "scope": ["src/sample.py"],
        "sequence": {"predecessor_sha": IDENTITY.base_sha, "step": 1},
        "targets": [target],
    }
    inputs[2].update(
        {
            "applicable": True,
            "authorization": authorization,
            "authorization_comment_id": 11,
            "targets": [target],
        }
    )
    if defect == "applicable":
        inputs[2]["applicable"] = False
    elif defect == "scope":
        inputs[2]["changed_paths"] = ["docs/forged.md"]
        authorization["scope"] = ["docs/forged.md"]
    elif defect == "targets":
        forged = "src/forged.py::function:calculate:1-2"
        inputs[2]["targets"] = [forged]
        authorization["targets"] = [forged]
    elif defect == "target_shape":
        forged = "src/sample.py::forged:anything"
        inputs[2]["targets"] = [forged]
        authorization["targets"] = [forged]
    else:
        authorization["head_sha"] = "f" * 40

    payload = _compose(inputs)

    expected = (
        "MALFORMED_REFACTOR_RESULT"
        if defect == "target_shape"
        else "REFACTOR_RESULT_BINDING_MISMATCH"
    )
    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [expected] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": expected,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


def test_refactor_result_rejects_same_path_forged_target_identity() -> None:
    inputs = _inputs()
    forged = "src/sample.py::function:forged:999-999"
    authorization = inputs[2]["authorization"]
    assert isinstance(authorization, dict)
    inputs[2]["targets"] = [forged]
    authorization["targets"] = [forged]

    payload = _compose(inputs)

    code = "REFACTOR_RESULT_BINDING_MISMATCH"
    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": code,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


def test_refactor_binding_preserves_deleted_old_path_identity_on_rename() -> None:
    changed = (
        {
            "base_production": True,
            "head_production": True,
            "new_path": "src/renamed.py",
            "old_path": "src/sample.py",
        },
    )
    moved = "src/renamed.py::function:retained:1-2"
    deleted = "src/sample.py::function:removed:4-5"
    targets = (moved, deleted)
    authorization = {
        "base_sha": IDENTITY.base_sha,
        "broad": True,
        "head_sha": IDENTITY.head_sha,
        "repository": IDENTITY.repository,
        "scope": ["src/renamed.py", "src/sample.py"],
        "sequence": {"predecessor_sha": IDENTITY.base_sha, "step": 1},
        "targets": list(targets),
    }
    row = {
        "applicable": True,
        "changed_paths": authorization["scope"],
        "policy_blocks": [],
        "targets": list(targets),
        "unbounded_paths": [],
    }

    assert standard_results._s02_refactor_change_paths(changed) == (
        ["src/renamed.py", "src/sample.py"],
        ["src/renamed.py"],
        ["src/renamed.py", "src/sample.py"],
    )
    standard_results._s02_refactor_binding(
        row, authorization, IDENTITY, changed, targets, (), None, None
    )
    row["targets"] = [moved]
    authorization["targets"] = [moved]
    standard_results._s02_refactor_binding(
        row, authorization, IDENTITY, changed, (moved,), (), None, None
    )


@pytest.mark.parametrize(
    ("defect", "block"),
    [
        ("repository", "AUTHORIZATION_REPOSITORY_MISMATCH"),
        ("base", "STALE_OWNER_AUTHORIZATION"),
        ("broad", "BROAD_AUTHORIZATION_REQUIRED"),
        ("sequence", "INVALID_STRANGLER_SEQUENCE"),
        ("scope", "UNFOCUSED_DIFF_SCOPE"),
        ("target", "UNVERIFIABLE_BOUNDED_TARGET"),
    ],
)
def test_authenticated_refactor_poison_remains_a_gate_six_block(defect: str, block: str) -> None:
    inputs = _inputs()
    authorization = inputs[2]["authorization"]
    assert isinstance(authorization, dict)
    if defect == "repository":
        authorization["repository"] = "example/other"
    elif defect == "base":
        authorization["base_sha"] = "f" * 40
    elif defect == "broad":
        extra = "src/sample.py::module:src/sample.py:3-3"
        targets = sorted([*inputs[2]["targets"], extra])
        authorization["targets"] = targets
        inputs[2]["targets"] = targets
        inputs[0]["changed_files"][0]["changed_head_lines"] = [1, 3]
        inputs[0]["responsibility_targets"] = targets
        inputs[1]["refactor_runnability"]["targets"] = targets
    elif defect == "sequence":
        authorization["sequence"]["predecessor_sha"] = "f" * 40
    elif defect == "scope":
        authorization["scope"] = ["docs/forged.md"]
    else:
        authorization["targets"] = ["src/forged.py::module:src/forged.py:1-1"]
    inputs[2]["policy_blocks"] = [block]
    inputs[2]["overall_result"] = "BLOCK"
    inputs[2]["characterization_sha256"] = hashlib.sha256(_canonical(inputs[1])).hexdigest()

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 5 + ["BLOCK"] + ["PASS"] * 2
    assert _entry(payload, 6)["result"] == "BLOCK"
    assert _entry(payload, 6)["policy_blocks"] == [block]
    assert _entry(payload, 6)["technical_errors"] == []
    assert payload["shared_failures"] == []


def test_exact_authenticated_step_two_predecessor_passes() -> None:
    inputs = _inputs()
    _step_two_refactor(inputs)

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 8
    assert payload["shared_failures"] == []


@pytest.mark.parametrize(
    ("defect", "expected"),
    [
        ("base_sha", "REFACTOR_RESULT_BINDING_MISMATCH"),
        ("head_sha", "REFACTOR_RESULT_BINDING_MISMATCH"),
        ("merge_sha", "REFACTOR_RESULT_BINDING_MISMATCH"),
        ("authorization_comment_id", "MALFORMED_REFACTOR_RESULT"),
        ("pull_number", "REFACTOR_RESULT_BINDING_MISMATCH"),
        ("step", "REFACTOR_RESULT_BINDING_MISMATCH"),
    ],
)
def test_forged_predecessor_evidence_is_gate_six_technical(defect: str, expected: str) -> None:
    inputs = _inputs()
    predecessor = _step_two_refactor(inputs)
    if defect == "step":
        predecessor["authorization"]["sequence"]["step"] = 999
    elif defect in {"authorization_comment_id", "pull_number"}:
        predecessor[defect] = 0
    else:
        predecessor[defect] = "f" * 40

    payload = _compose(inputs)

    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [expected] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": expected,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


@pytest.mark.parametrize("predecessor_state", ["missing", "wrong_step"])
def test_missing_or_wrong_predecessor_is_exact_gate_six_block(predecessor_state: str) -> None:
    inputs = _inputs()
    predecessor = _step_two_refactor(inputs)
    if predecessor_state == "missing":
        inputs[2]["predecessor"] = {
            "authorization": None,
            "authorization_comment_id": None,
            "base_sha": None,
            "block": None,
            "head_sha": None,
            "merge_sha": None,
            "pull_number": None,
        }
    else:
        predecessor["authorization"]["sequence"]["step"] = 7
    inputs[2]["policy_blocks"] = ["INVALID_STRANGLER_SEQUENCE"]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 5 + ["BLOCK"] + ["PASS"] * 2
    assert _entry(payload, 6)["policy_blocks"] == ["INVALID_STRANGLER_SEQUENCE"]
    assert _entry(payload, 6)["technical_errors"] == []


def test_spurious_sequence_block_is_gate_six_technical() -> None:
    inputs = _inputs()
    _step_two_refactor(inputs)
    inputs[2]["policy_blocks"] = ["INVALID_STRANGLER_SEQUENCE"]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    code = "REFACTOR_RESULT_BINDING_MISMATCH"
    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": code,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


def test_sequence_and_predecessor_lookup_blocks_are_aggregated_together() -> None:
    inputs = _inputs()
    authorization = inputs[2]["authorization"]
    assert isinstance(authorization, dict)
    authorization["sequence"]["predecessor_sha"] = "f" * 40
    inputs[2]["predecessor"]["block"] = "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"
    inputs[2]["policy_blocks"] = [
        "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
        "INVALID_STRANGLER_SEQUENCE",
    ]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 5 + ["BLOCK"] + ["PASS"] * 2
    assert _entry(payload, 6)["policy_blocks"] == inputs[2]["policy_blocks"]
    assert _entry(payload, 6)["technical_errors"] == []


def test_unsorted_refactor_blocks_are_malformed() -> None:
    inputs = _inputs()
    inputs[2]["policy_blocks"] = [
        "INVALID_STRANGLER_SEQUENCE",
        "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
    ]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    code = "MALFORMED_REFACTOR_RESULT"
    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": code,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


@pytest.mark.parametrize(
    "block",
    [
        "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
        "MALFORMED_OWNER_AUTHORIZATION",
        "MISSING_OWNER_AUTHORIZATION",
        "STALE_OWNER_AUTHORIZATION",
        "UNAUTHENTICATED_OWNER_AUTHORIZATION",
    ],
)
def test_current_authorization_failure_remains_a_gate_six_block(block: str) -> None:
    inputs = _inputs()
    inputs[2]["authorization"] = None
    inputs[2]["authorization_comment_id"] = None
    inputs[2]["policy_blocks"] = [block]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 5 + ["BLOCK"] + ["PASS"] * 2
    assert _entry(payload, 6)["policy_blocks"] == [block]
    assert _entry(payload, 6)["technical_errors"] == []
    assert payload["shared_failures"] == []


def test_current_authorization_failure_rejects_impossible_predecessor_evidence() -> None:
    inputs = _inputs()
    _step_two_refactor(inputs)
    inputs[2]["authorization"] = None
    inputs[2]["authorization_comment_id"] = None
    inputs[2]["policy_blocks"] = ["GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    code = "REFACTOR_RESULT_BINDING_MISMATCH"
    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": code,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


def test_nonapplicable_refactor_rejects_impossible_predecessor_evidence() -> None:
    predecessor = _step_two_refactor(_inputs())
    inputs = _inputs("docs/evidence.md")
    inputs[2]["predecessor"] = predecessor

    payload = _compose(inputs)

    code = "REFACTOR_RESULT_BINDING_MISMATCH"
    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": code,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


def test_unverifiable_authorized_target_remains_a_gate_six_block() -> None:
    inputs = _inputs()
    authorization = inputs[2]["authorization"]
    assert isinstance(authorization, dict)
    authorization["targets"] = ["src/sample.py::wrong"]
    inputs[2]["policy_blocks"] = ["UNVERIFIABLE_BOUNDED_TARGET"]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 5 + ["BLOCK"] + ["PASS"] * 2
    assert _entry(payload, 6)["policy_blocks"] == ["UNVERIFIABLE_BOUNDED_TARGET"]
    assert payload["shared_failures"] == []


def test_unbounded_production_path_has_exact_gate_six_blocks() -> None:
    path = "src/sample.bin"
    inputs = _inputs(path)
    target = f"{path}::module:{path}:1-1"
    authorization = inputs[2]["authorization"]
    assert isinstance(authorization, dict)
    authorization["broad"] = True
    authorization["targets"] = [target]
    inputs[0]["changed_files"][0]["complexity_assessed"] = False
    inputs[0]["modularity"]["changed_paths"] = []
    inputs[0]["responsibility_targets"] = []
    inputs[0]["unbounded_production_paths"] = [path]
    inputs[1]["refactor_runnability"]["targets"] = []
    inputs[1]["refactor_runnability"]["unbounded_paths"] = [path]
    inputs[2]["targets"] = []
    inputs[2]["unbounded_paths"] = [path]
    inputs[2]["policy_blocks"] = [
        "MISSING_BOUNDED_PRODUCTION_TARGET",
        "UNVERIFIABLE_BOUNDED_TARGET",
    ]
    inputs[2]["overall_result"] = "BLOCK"
    inputs[2]["characterization_sha256"] = hashlib.sha256(_canonical(inputs[1])).hexdigest()

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 5 + ["BLOCK"] + ["PASS"] * 2
    assert _entry(payload, 6)["policy_blocks"] == [
        "MISSING_BOUNDED_PRODUCTION_TARGET",
        "UNVERIFIABLE_BOUNDED_TARGET",
    ]
    assert _entry(payload, 6)["technical_errors"] == []
    assert payload["shared_failures"] == []


def test_refactor_cross_binding_waits_for_authenticated_complexity() -> None:
    inputs = _inputs()
    inputs[0]["head_sha"] = "f" * 40

    payload = _compose(inputs)

    assert _entry(payload, 6)["technical_errors"] == ["COMPLEXITY_RESULT_BINDING_MISMATCH"]


def test_non_applicable_refactor_rejects_owner_authorization_blocks() -> None:
    inputs = _inputs("docs/evidence.md")
    inputs[2]["policy_blocks"] = ["MISSING_OWNER_AUTHORIZATION"]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    code = "REFACTOR_RESULT_BINDING_MISMATCH"
    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": code,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


def test_non_applicable_refactor_does_not_require_runnability_extension() -> None:
    inputs = _inputs("docs/evidence.md")
    inputs[1].pop("refactor_runnability")
    inputs[2]["characterization_sha256"] = hashlib.sha256(_canonical(inputs[1])).hexdigest()

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 8
    assert payload["shared_failures"] == []


@pytest.mark.parametrize(
    ("field", "block"),
    [("scope", "UNFOCUSED_DIFF_SCOPE"), ("targets", "UNVERIFIABLE_BOUNDED_TARGET")],
)
def test_refactor_authorization_lists_are_canonical(field: str, block: str) -> None:
    inputs = _inputs()
    authorization = inputs[2]["authorization"]
    assert isinstance(authorization, dict)
    authorization[field] = [*authorization[field], *authorization[field]]
    inputs[2]["policy_blocks"] = [block]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    code = "MALFORMED_REFACTOR_RESULT"
    assert _technical_standards(payload) == {6, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (6, 8))
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": code,
            "dependency": "refactor-policy-result",
            "kind": "TECHNICAL_ERROR",
        }
    ]


@pytest.mark.parametrize(
    "block",
    [
        "MISSING_RUNNABILITY_COVERAGE",
        "NON_RUNNABLE_LOGICAL_STEP",
        "STALE_RUNNABILITY_EVIDENCE",
        "UNAUTHENTICATED_RUNNABILITY_EVIDENCE",
    ],
)
def test_refactor_runnability_blocks_remain_gate_six_blocks(block: str) -> None:
    inputs = _inputs()
    runnability = inputs[1]["refactor_runnability"]
    assert isinstance(runnability, dict)
    if block == "MISSING_RUNNABILITY_COVERAGE":
        change = inputs[0]["changed_files"][0]
        change.update(
            {
                "changed_head_lines": [],
                "head_production": False,
                "new_path": None,
                "old_path": "src/sample.py",
                "status": "DELETED",
            }
        )
        inputs[0]["modularity"]["changed_paths"] = []
        inputs[1]["coverage"] = {
            "covered_paths": ["tests/characterization/sample.py"],
            "required_paths": [],
        }
        inputs[1]["scenarios"][0]["covers"] = ["tests/characterization/sample.py"]
        runnability["runnable"] = False
    elif block == "NON_RUNNABLE_LOGICAL_STEP":
        runnability["runnable"] = False
    elif block == "STALE_RUNNABILITY_EVIDENCE":
        runnability["base_sha"] = "f" * 40
    else:
        inputs[1].pop("refactor_runnability")
    inputs[2]["policy_blocks"] = [block]
    inputs[2]["overall_result"] = "BLOCK"
    inputs[2]["characterization_sha256"] = hashlib.sha256(_canonical(inputs[1])).hexdigest()

    payload = _compose(inputs)

    assert _results(payload) == ["PASS"] * 5 + ["BLOCK"] + ["PASS"] * 2
    assert _entry(payload, 6)["policy_blocks"] == [block]
    assert _entry(payload, 6)["technical_errors"] == []
    assert payload["shared_failures"] == []


def test_quality_artifact_has_exact_authenticated_identity() -> None:
    payload = _compose(_inputs())

    assert payload["quality_artifact"] == {**EXPECTED_QUALITY_ARTIFACT, "id": 789}
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


@pytest.mark.parametrize(
    "case",
    ["invalid", "missing", "coverage", "new_owner", "parallel", "unresolved", "vague"],
)
def test_authentic_gate_four_poison_blocks_only_gate_four(case: str) -> None:
    path = "src/utils/format.py" if case == "vague" else "src/orders/model.py"
    inputs = _owned_new_location_inputs(path)
    complexity = inputs[0]
    claim = complexity["review_evidence"]["module_boundaries"][0]
    if case == "invalid":
        claim["owner_path"] = "../owner.py"
        block = f"INVALID_NEW_LOCATION_JUSTIFICATION:{path}"
    elif case == "missing":
        complexity["review_evidence"]["module_boundaries"] = []
        complexity["modularity"]["justifications"] = []
        block = f"MISSING_NEW_LOCATION_JUSTIFICATION:{path}"
    elif case == "coverage":
        complexity["architecture"]["covered_paths"] = []
        complexity["modularity"]["coverage"][0]["architecture"] = False
        block = f"NEW_LOCATION_GATE_COVERAGE:{path}"
    elif case == "new_owner":
        owner = "src/orders/owner.py"
        claim["owner_path"] = owner
        owner_claim = {
            "basis": "domain",
            "justification": "Exact source path owns one cohesive boundary.",
            "owner_path": owner,
            "path": owner,
        }
        complexity["review_evidence"]["module_boundaries"].append(owner_claim)
        complexity["modularity"]["justifications"].append(owner_claim)
        complexity["changed_files"].append(_changed_file(owner, [1], status="ADDED"))
        complexity["architecture"]["covered_paths"].append(owner)
        complexity["architecture"]["nodes"].append(owner)
        complexity["quality_profile"]["changed_paths"].append(owner)
        _set_quality_commands(inputs, "python", (path, owner))
        complexity["modularity"]["changed_paths"].append(owner)
        complexity["modularity"]["new_paths"].append(owner)
        complexity["modularity"]["coverage"].append(
            {
                "adapters": complexity["modularity"]["coverage"][0]["adapters"],
                "architecture": True,
                "path": owner,
            }
        )
        target = f"{owner}::module:{owner}:1-1"
        complexity["responsibility_targets"].append(target)
        inputs[1]["scenarios"][0]["covers"].append(owner)
        inputs[1]["coverage"]["covered_paths"].append(owner)
        inputs[1]["coverage"]["required_paths"].append(owner)
        inputs[1]["refactor_runnability"]["targets"].append(target)
        authorization = inputs[2]["authorization"]
        assert isinstance(authorization, dict)
        authorization["broad"] = True
        authorization["scope"].append(owner)
        inputs[2]["changed_paths"].append(owner)
        inputs[2]["targets"].append(target)
        _bind_characterization(inputs)
        block = f"NEW_MODULE_OWNER_NOT_PREEXISTING:{path}:{owner}"
    elif case == "parallel":
        owner = "src/inventory/model.py"
        claim["owner_path"] = owner
        complexity["architecture"]["nodes"].append(owner)
        block = f"PARALLEL_PACKAGE:{path}:{owner}"
    elif case == "unresolved":
        owner = "src/orders/missing.py"
        claim["owner_path"] = owner
        block = f"UNRESOLVED_MODULE_OWNER:{path}:{owner}"
    else:
        block = f"VAGUE_PRODUCTION_LOCATION:{path}:utils"
    complexity["modularity"]["blocks"] = [block]
    complexity["policy_blocks"] = [block]
    complexity["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _results(payload) == ["PASS", "PASS", "PASS", "BLOCK", *["PASS"] * 4]
    assert _entry(payload, 4)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


def test_typescript_owned_new_location_passes_aggregate_validation() -> None:
    payload = _compose(
        _owned_new_location_inputs("src/components/order-card.tsx", language="typescript")
    )

    assert _results(payload) == ["PASS"] * 8


def test_gate_five_vocabulary_is_exact_and_ordered() -> None:
    assert standard_block_ownership.BLOCK_FAMILIES[4] == (
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
    )


def _bind_characterization(inputs: tuple[dict[str, Any], ...]) -> None:
    inputs[2]["characterization_sha256"] = hashlib.sha256(_canonical(inputs[1])).hexdigest()


def _gate_five_poison(
    block: str,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any], list[str]]:
    inputs = _inputs()
    characterization = inputs[1]
    expected = copy.deepcopy(EXPECTED_CHARACTERIZATION_ARTIFACTS)
    blocks = [block]
    scenario = characterization["scenarios"][0]
    if block == "BASE_CAPTURE_DIGEST_MISMATCH":
        characterization["artifacts"]["base"]["capture_sha256"] = "7" * 64
    elif block == "HEAD_CAPTURE_DIGEST_MISMATCH":
        characterization["artifacts"]["head"]["capture_sha256"] = "7" * 64
    elif block == "GOLDEN_BEHAVIOR_MISMATCH:sample":
        scenario["golden_behavior_sha256"] = "d" * 64
    elif block == "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:sample":
        blocks = ["GOLDEN_BEHAVIOR_MISMATCH:sample", block]
        scenario["head_behavior_sha256"] = "d" * 64
        scenario["golden_behavior_sha256"] = "d" * 64
        scenario["compatibility"] = "BLOCK"
        characterization["behavior_fingerprint"] = hashlib.sha256(
            _canonical([["sample", "d" * 64]])
        ).hexdigest()
    elif block == "INVALID_ARTIFACT_IDENTITY":
        characterization["artifacts"]["base"]["id"] = "0"
        expected["base"]["id"] = "0"
    elif block == "MISSING_CHARACTERIZATION_COVERAGE:src/risk.py":
        inputs[0]["high_risk_paths"] = ["src/risk.py"]
        profile = inputs[0]["quality_profile"]
        profile["high_risk_paths"] = ["src/risk.py"]
        _set_quality_commands(inputs, "python", ("src/sample.py", "src/risk.py"))
        characterization["coverage"]["required_paths"].append("src/risk.py")
        characterization["coverage"]["required_paths"].sort()
    elif block in {"HEAD_ONLY_CHARACTERIZATION_CLAIM", "MISSING_BASELINE"}:
        blocks = ["HEAD_ONLY_CHARACTERIZATION_CLAIM", "MISSING_BASELINE"]
        characterization["artifacts"]["base"]["capture_sha256"] = None
        scenario["base_behavior_sha256"] = None
        scenario["compatibility"] = "BLOCK"
    elif block == "INCOMPLETE_CHARACTERIZATION_EVIDENCE":
        characterization["artifacts"]["head"]["capture_sha256"] = None
        scenario.update(
            command=None,
            compatibility="BLOCK",
            golden_behavior_sha256=None,
            head_behavior_sha256=None,
        )
        characterization["behavior_fingerprint"] = hashlib.sha256(
            _canonical([["sample", None]])
        ).hexdigest()
    characterization["policy_blocks"] = blocks
    characterization["overall_result"] = "BLOCK"
    _bind_characterization(inputs)
    return inputs, expected, blocks


@pytest.mark.parametrize(
    "block",
    [
        "BASE_CAPTURE_DIGEST_MISMATCH",
        "CHANGED_CHARACTERIZATION_DEFINITION:sample",
        "CHANGED_GOLDEN_OUTPUT:sample",
        "CHARACTERIZATION_DEFINITION_MISMATCH:sample",
        "CHARACTERIZATION_DRIVER_IDENTITY_MISMATCH:sample",
        "CHARACTERIZATION_EXECUTION_FAILED:sample",
        "CHARACTERIZATION_FINGERPRINT_MISMATCH",
        "CHARACTERIZATION_REPLAY_DRIFT:sample",
        "GOLDEN_ARTIFACT_IDENTITY_MISMATCH:sample",
        "GOLDEN_BEHAVIOR_MISMATCH:sample",
        "HEAD_CAPTURE_DIGEST_MISMATCH",
        "HEAD_ONLY_CHARACTERIZATION_CLAIM",
        "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:sample",
        "INCOMPLETE_CHARACTERIZATION_EVIDENCE",
        "INVALID_ARTIFACT_IDENTITY",
        "MISSING_BASELINE",
        "MISSING_CHARACTERIZATION_COVERAGE:src/risk.py",
        "REMOVED_CHARACTERIZATION_SCENARIO:sample",
        "STALE_BASELINE_ARTIFACT",
        "STALE_POST_CHANGE_ARTIFACT",
        "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE",
    ],
)
def test_authentic_gate_five_poison_blocks_only_gate_five(block: str) -> None:
    inputs, expected, blocks = _gate_five_poison(block)

    payload = _compose(inputs, expected_characterization_artifacts=expected)

    assert _results(payload) == ["PASS", "PASS", "PASS", "PASS", "BLOCK", *["PASS"] * 3]
    assert _entry(payload, 5)["policy_blocks"] == blocks
    assert payload["shared_failures"] == []


@pytest.mark.parametrize(
    "case",
    [
        "fingerprint",
        "required_paths",
        "covered_paths",
        "scenario_covers",
        "compatibility",
        "golden_hash",
        "base_golden_hash",
        "forged_golden_block",
        "masked_missing_captures",
        "masked_missing_command",
        "unhashable_kind",
        "unhashable_compatibility",
        "duplicate_scenario",
    ],
)
def test_characterization_aggregate_rejects_semantic_forgery(case: str) -> None:
    inputs = _inputs()
    characterization = inputs[1]
    scenario = characterization["scenarios"][0]
    if case == "fingerprint":
        characterization["behavior_fingerprint"] = "0" * 64
    elif case == "required_paths":
        characterization["coverage"]["required_paths"] = []
    elif case == "covered_paths":
        characterization["coverage"]["covered_paths"] = []
    elif case == "scenario_covers":
        scenario["covers"] = ["src/forged.py"]
    elif case == "compatibility":
        scenario["compatibility"] = "BLOCK"
    elif case == "golden_hash":
        scenario["golden_behavior_sha256"] = "d" * 64
    elif case == "base_golden_hash":
        scenario["base_behavior_sha256"] = "d" * 64
        scenario["compatibility"] = "BLOCK"
        characterization["policy_blocks"] = ["INCOMPATIBLE_POST_CHANGE_BEHAVIOR:sample"]
        characterization["overall_result"] = "BLOCK"
    elif case == "forged_golden_block":
        characterization["policy_blocks"] = ["GOLDEN_BEHAVIOR_MISMATCH:sample"]
        characterization["overall_result"] = "BLOCK"
    elif case == "masked_missing_captures":
        characterization["artifacts"]["base"]["capture_sha256"] = None
        characterization["artifacts"]["head"]["capture_sha256"] = None
        characterization["policy_blocks"] = ["CHANGED_GOLDEN_OUTPUT:sample"]
        characterization["overall_result"] = "BLOCK"
    elif case == "masked_missing_command":
        scenario["command"] = None
        characterization["policy_blocks"] = ["CHANGED_GOLDEN_OUTPUT:sample"]
        characterization["overall_result"] = "BLOCK"
    elif case == "unhashable_kind":
        scenario["kind"] = []
    elif case == "unhashable_compatibility":
        scenario["compatibility"] = {}
    else:
        characterization["scenarios"].append(copy.deepcopy(scenario))
    _bind_characterization(inputs)

    payload = _compose(inputs)

    assert _technical_standards(payload) == {5, 6, 8}
    assert all(
        _entry(payload, standard)["technical_errors"] == ["MALFORMED_CHARACTERIZATION_RESULT"]
        for standard in (5, 6, 8)
    )


@pytest.mark.parametrize("side", ["base", "head"])
@pytest.mark.parametrize("field", ["id", "digest"])
def test_characterization_artifacts_require_external_binding(side: str, field: str) -> None:
    inputs = _inputs()
    inputs[1]["artifacts"][side][field] = "999" if field == "id" else "7" * 64
    _bind_characterization(inputs)

    payload = _compose(inputs)

    assert _technical_standards(payload) == {5, 6, 8}
    assert all(
        _entry(payload, standard)["technical_errors"]
        == ["CHARACTERIZATION_RESULT_BINDING_MISMATCH"]
        for standard in (5, 6, 8)
    )


def test_high_risk_paths_require_authenticated_profile_binding() -> None:
    inputs = _inputs()
    inputs[0]["quality_profile"]["high_risk_paths"] = ["src/risk.py"]

    payload = _compose(inputs)

    assert _technical_standards(payload) == {4, 7, 8}
    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert _entry(payload, 4)["technical_errors"] == [code]
    assert _entry(payload, 7)["technical_errors"] == [code]
    assert _entry(payload, 8)["technical_errors"] == [code]


def test_gate_four_aggregate_requires_authentic_changed_path_coupling() -> None:
    path = "src/sample.py"
    owner = "src/owner.py"
    edge = {
        "internal": True,
        "line": 1,
        "source": path,
        "specifier": "owner",
        "target": owner,
    }
    inputs = _inputs()
    inputs[0]["architecture"]["nodes"].append(owner)
    inputs[0]["architecture"]["edges"] = [edge]
    _set_quality_commands(inputs, "python", (path, owner))
    inputs[0]["modularity"]["coupling_edges"] = [edge]

    assert _results(_compose(inputs)) == ["PASS"] * 8

    inputs[0]["modularity"]["coupling_edges"] = []
    payload = _compose(inputs)
    assert _technical_standards(payload) == set(range(1, 9))
    assert all(
        entry["technical_errors"] == ["MALFORMED_COMPLEXITY_RESULT"] for entry in payload["entries"]
    )


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


def test_boundary_derivation_failure_affects_only_gate_two_and_handoff() -> None:
    inputs = _inputs()
    code = "SEPARATION_BOUNDARY_DERIVATION_FAILURE"
    inputs[0]["technical_errors"] = [{"code": code, "message": "invalid source"}]
    inputs[0]["overall_result"] = "TECHNICAL_FAILURE"

    payload = _compose(inputs)

    assert _technical_standards(payload) == {2, 8}
    assert _entry(payload, 2)["technical_errors"] == [f"COMPLEXITY_RESULT:{code}"]
    assert standard_block_ownership.expected_technical_dependency(
        f"COMPLEXITY_RESULT:{code}", "complexity-result:technical-errors"
    ) == ("complexity-result:technical-errors", frozenset({2, 8}))


def test_refactor_target_derivation_failure_affects_only_gate_six_and_handoff() -> None:
    inputs = _inputs()
    code = "REFACTOR_TARGET_DERIVATION_FAILURE"
    inputs[0]["technical_errors"] = [{"code": code, "message": "target derivation failed"}]
    inputs[0]["overall_result"] = "TECHNICAL_FAILURE"
    inputs[0]["responsibility_targets"] = []
    inputs[0]["unbounded_production_paths"] = []

    payload = _compose(inputs)

    assert _technical_standards(payload) == {6, 8}
    assert _entry(payload, 6)["technical_errors"] == [f"COMPLEXITY_RESULT:{code}"]
    assert payload["shared_failures"] == [
        {
            "affected_standards": [6, 8],
            "code": f"COMPLEXITY_RESULT:{code}",
            "dependency": "complexity-result:technical-errors",
            "kind": "TECHNICAL_ERROR",
        }
    ]

    forged = copy.deepcopy(inputs)
    forged[2]["changed_paths"] = ["src/forged.py"]
    forged_payload = _compose(forged)
    assert _entry(forged_payload, 6)["technical_errors"] == [
        f"COMPLEXITY_RESULT:{code}",
        "REFACTOR_RESULT_BINDING_MISMATCH",
    ]

    unrelated = _inputs()
    unrelated[0]["technical_errors"] = [
        {"code": "MCCABE_GRAPH_MISMATCH", "message": "metric evidence failed"}
    ]
    unrelated[0]["overall_result"] = "TECHNICAL_FAILURE"
    unrelated[0]["responsibility_targets"] = []
    unrelated_payload = _compose(unrelated)
    assert _entry(unrelated_payload, 6)["technical_errors"] == ["REFACTOR_RESULT_BINDING_MISMATCH"]


@pytest.mark.parametrize(
    ("field", "poison"),
    [
        ("repository", "other/repository"),
        ("repository_id", "999"),
        ("run_id", "999"),
        ("run_attempt", "2"),
        ("job", "other-job"),
        ("runner_environment", "owner-workstation"),
    ],
)
def test_quality_provenance_binding_mismatch_is_gate_seven_technical_only(
    field: str, poison: str
) -> None:
    inputs = _inputs()
    inputs[3][field] = poison

    payload = _compose(inputs)

    assert _technical_standards(payload) == {7, 8}
    assert all(
        "QUALITY_RESULT_BINDING_MISMATCH" in _entry(payload, standard)["technical_errors"]
        for standard in (7, 8)
    )


def test_malformed_quality_profile_affects_only_its_explicit_dependents() -> None:
    inputs = _inputs()
    inputs[0]["quality_profile"]["schema_version"] = "quality-gates.v3"

    payload = _compose(inputs)

    assert _technical_standards(payload) == {4, 7, 8}
    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (4, 7, 8))


def test_malformed_quality_profile_cannot_abort_added_path_isolation() -> None:
    inputs = _owned_new_location_inputs()
    inputs[0]["quality_profile"].pop("commands")

    payload = _compose(inputs)

    assert _technical_standards(payload) == {4, 7, 8}
    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (4, 7, 8))


@pytest.mark.parametrize(
    ("field", "poison"),
    [
        ("raw_proof_sha256", "f" * 64),
        ("executed_arguments", ["python", "unsafe.py"]),
    ],
)
def test_quality_capture_rejects_valid_shaped_split_forgery_in_gate_seven_only(
    field: str, poison: object
) -> None:
    inputs = _inputs()
    trusted = _bind_quality(inputs)
    assert _results(_compose(inputs, expected_quality_artifact=trusted)) == ["PASS"] * 8
    inputs[3]["commands"][0][field] = poison

    payload = _compose(inputs, expected_quality_artifact=trusted)

    assert _technical_standards(payload) == {7, 8}
    assert all(
        _entry(payload, standard)["technical_errors"] == ["QUALITY_ARTIFACT_IDENTITY_MISMATCH"]
        for standard in (7, 8)
    )
    assert payload["quality_artifact"] is None


def test_freshly_authenticated_quality_argv_poison_is_gate_seven_technical_only() -> None:
    inputs = _inputs()
    inputs[3]["commands"][0]["executed_arguments"] = ["python", "unsafe.py"]
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    assert _technical_standards(payload) == {7, 8}
    assert all(
        _entry(payload, standard)["technical_errors"] == ["MALFORMED_QUALITY_RESULT_BINDING"]
        for standard in (7, 8)
    )
    assert payload["quality_artifact"] is None


def test_freshly_authenticated_quality_template_poison_is_quality_evidence_failure() -> None:
    inputs = _inputs()
    inputs[0]["quality_profile"]["commands"][0]["arguments"] = ["python", "unsafe.py"]
    inputs[3]["commands"][0]["executed_arguments"] = ["python", "unsafe.py"]
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    assert _technical_standards(payload) == {4, 7, 8}
    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (4, 7, 8))
    assert payload["quality_artifact"] is None


@pytest.mark.parametrize("mutation", ["unreported-missing", "reordered"])
def test_freshly_authenticated_quality_adapter_sequence_is_required(mutation: str) -> None:
    inputs = _inputs()
    if mutation == "unreported-missing":
        inputs[0]["quality_profile"]["commands"].pop()
        inputs[3]["commands"].pop()
    else:
        inputs[0]["quality_profile"]["commands"].reverse()
        inputs[3]["commands"].reverse()
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    assert _technical_standards(payload) == {4, 7, 8}
    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (4, 7, 8))
    assert payload["quality_artifact"] is None


def test_authenticated_missing_quality_adapter_block_is_gate_seven_only() -> None:
    inputs = _inputs()
    adapter = inputs[0]["quality_profile"]["commands"].pop()["adapter"]
    inputs[3]["commands"].pop()
    block = f"MISSING_QUALITY_COMMAND:{adapter}"
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    assert _results(payload) == [*("PASS",) * 6, "BLOCK", "PASS"]
    assert _entry(payload, 7)["policy_blocks"] == [block]
    assert _technical_standards(payload) == set()
    assert payload["quality_artifact"] == {**trusted, "id": int(trusted["id"])}


def test_quality_argv_requires_exact_test_manifest() -> None:
    profile = {
        "production_files": [],
        "source_files": [],
        "test_files": ["tests/test_sample.py"],
        "commands": [{"arguments": ["tool", "--root=$REPOSITORY", "$TEST_FILES"]}],
    }
    provenance = {
        "commands": [{"executed_arguments": ["tool", "--root=/repo/target"]}],
    }

    with pytest.raises(
        standard_results.StandardResultsError, match="MALFORMED_QUALITY_RESULT_BINDING"
    ):
        standard_results._s02_quality_argv(profile, provenance, "MALFORMED_QUALITY_RESULT_BINDING")


def _mixed_asset_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    inputs = _inputs()
    profile = inputs[0]["quality_profile"]
    profile["asset_receipts"] = [
        {
            "blob_sha256": "1" * 64,
            "kind": "json",
            "path": "src/config.json",
            "result": "PASS",
            "validator": "json.stdlib.v1",
        }
    ]
    profile["production_files"] = ["src/config.json", "src/sample.py"]
    profile["source_files"] = ["src/sample.py"]
    return inputs


def test_mixed_asset_profile_keeps_assets_out_of_source_argv_and_handoff() -> None:
    inputs = _mixed_asset_inputs()
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    commands = payload["review_handoff"]["validation"]["commands"]
    assert all(
        "C:/repo/target/src/config.json" not in command["executed_arguments"]
        for command in commands
    )
    assert payload["review_handoff"]["coverage"]["source_files"] == ["src/sample.py"]
    assert (
        payload["review_handoff"]["coverage"]["asset_receipts"]
        == inputs[0]["quality_profile"]["asset_receipts"]
    )
    assert _results(payload) == ["PASS"] * 8


@pytest.mark.parametrize("field", ["observed_paths", "zero_statement_paths"])
def test_standard_results_rejects_asset_command_observation(field: str) -> None:
    inputs = _mixed_asset_inputs()
    inputs[0]["quality_profile"]["commands"][0][field] = ["src/config.json"]
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert _technical_standards(payload) == {4, 7, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (4, 7, 8))


def test_forged_asset_receipt_breaks_authenticated_capture() -> None:
    inputs = _mixed_asset_inputs()
    trusted = _bind_quality(inputs)
    inputs[0]["quality_profile"]["asset_receipts"][0]["blob_sha256"] = "2" * 64

    payload = _compose(inputs, expected_quality_artifact=trusted)

    assert _technical_standards(payload) == {7, 8}
    assert all(
        _entry(payload, standard)["technical_errors"] == ["QUALITY_ARTIFACT_IDENTITY_MISMATCH"]
        for standard in (7, 8)
    )


def test_authenticated_malformed_asset_blocks_only_gate_seven() -> None:
    inputs = _mixed_asset_inputs()
    block = "MALFORMED_PRODUCTION_ASSET:src/config.json"
    inputs[0]["quality_profile"]["asset_receipts"][0]["result"] = "MALFORMED"
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    assert _results(payload) == [*(["PASS"] * 6), "BLOCK", "PASS"]
    assert _entry(payload, 7)["policy_blocks"] == [block]
    assert _technical_standards(payload) == set()


@pytest.mark.parametrize(
    "blocks",
    [
        pytest.param([], id="omitted"),
        pytest.param(["QUALITY_SCOPE_NARROWING"], id="substitution"),
        pytest.param(
            [
                "MALFORMED_PRODUCTION_ASSET:src/config.json",
                "UNSUPPORTED_PRODUCTION_ASSET:src/other.bin",
            ],
            id="extra",
        ),
    ],
)
def test_asset_failure_blocks_must_exactly_match_receipts(blocks: list[str]) -> None:
    inputs = _mixed_asset_inputs()
    inputs[0]["quality_profile"]["asset_receipts"][0]["result"] = "MALFORMED"
    inputs[0]["policy_blocks"] = blocks
    inputs[0]["overall_result"] = "BLOCK" if blocks else "PASS"
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert _technical_standards(payload) == {4, 7, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (4, 7, 8))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "wrong-kind", "asset-as-source"])
def test_asset_receipt_partition_and_identity_are_strict(mutation: str) -> None:
    inputs = _mixed_asset_inputs()
    receipts = inputs[0]["quality_profile"]["asset_receipts"]
    if mutation == "missing":
        receipts.clear()
    elif mutation == "duplicate":
        receipts.append(copy.deepcopy(receipts[0]))
    elif mutation == "wrong-kind":
        receipts[0]["kind"] = "png"
    else:
        receipts.clear()
        inputs[0]["quality_profile"]["source_files"].insert(0, "src/config.json")
    trusted = _bind_quality(inputs)

    payload = _compose(inputs, expected_quality_artifact=trusted)

    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert _technical_standards(payload) == {4, 7, 8}
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (4, 7, 8))


@pytest.mark.parametrize(
    "block",
    [
        "MALFORMED_PRODUCTION_ASSET:src/config.json",
        "UNSUPPORTED_PRODUCTION_ASSET:src/config.bin",
        "QUALITY_ASSET_RECEIPT_MISMATCH",
        "QUALITY_PRODUCTION_PARTITION_MISMATCH",
        "QUALITY_SOURCE_MANIFEST_MISMATCH",
    ],
)
def test_asset_policy_blocks_are_owned_only_by_gate_seven(block: str) -> None:
    assert standard_block_ownership.owners(block) == frozenset({7})


def test_missing_executed_argv_is_gate_seven_technical_only() -> None:
    inputs = _inputs()
    del inputs[3]["commands"][0]["executed_arguments"]

    payload = _compose(inputs)

    assert _technical_standards(payload) == {7, 8}
    assert all(
        _entry(payload, standard)["technical_errors"] == ["MALFORMED_QUALITY_RESULT_BINDING"]
        for standard in (7, 8)
    )


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
    trusted = _bind_quality(inputs)
    inputs[3][field] = poison

    payload = _compose(inputs, expected_quality_artifact=trusted)

    assert _technical_standards(payload) == {7, 8}
    assert all(
        _entry(payload, standard)["technical_errors"] == ["QUALITY_ARTIFACT_IDENTITY_MISMATCH"]
        for standard in (7, 8)
    )
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
        "TECHNICAL_FAILURE",
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
    assert payload["quality_artifact"] == {**EXPECTED_QUALITY_ARTIFACT, "id": 789}
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
    assert _technical_standards(isolated_payload) == {1, 8}
    assert _entry(isolated_payload, 1)["technical_errors"] == [
        "COMPLEXITY_RESULT:MCCABE_GRAPH_MISMATCH"
    ]
    assert _entry(isolated_payload, 7)["result"] == "PASS"
    assert isolated_payload["quality_artifact"] == {**EXPECTED_QUALITY_ARTIFACT, "id": 789}
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
    assert _technical_standards(quality_payload) == {4, 7, 8}

    simultaneous = copy.deepcopy(quality)
    simultaneous[0]["technical_errors"].insert(
        0, {"code": "MCCABE_GRAPH_MISMATCH", "message": "graph mismatch"}
    )
    simultaneous_payload = _compose(simultaneous)
    assert _technical_standards(simultaneous_payload) == {1, 4, 7, 8}

    syntax = _inputs()
    syntax[0]["technical_errors"] = [
        {"code": "COMPLEXITY_SYNTAX_ERROR", "message": "invalid syntax"},
        {"code": "ARCHITECTURE_SYNTAX_ERROR", "message": "invalid syntax"},
    ]
    syntax[0]["overall_result"] = "TECHNICAL_FAILURE"
    syntax[0]["architecture"] = None
    syntax[0]["modularity"] = None
    syntax_payload = _compose(syntax)
    assert _technical_standards(syntax_payload) == {1, 3, 4, 8}
    assert _entry(syntax_payload, 7)["result"] == "PASS"
    assert _entry(syntax_payload, 8)["result"] == "TECHNICAL_FAILURE"


def test_wrong_source_block_affects_source_dependents_and_claimed_owner_only() -> None:
    inputs = _inputs()
    block = "IMPORT_CYCLE:src/a.py:1:src.b"
    inputs[1]["policy_blocks"] = [block]
    inputs[1]["overall_result"] = "BLOCK"
    inputs[2]["characterization_sha256"] = hashlib.sha256(_canonical(inputs[1])).hexdigest()

    payload = _compose(inputs)

    expected = {3, 5, 6, 8}
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


def test_wrong_refactor_source_block_affects_handoff_dependency() -> None:
    inputs = _inputs()
    block = "IMPORT_CYCLE:src/a.py:1:src.b"
    inputs[2]["policy_blocks"] = [block]
    inputs[2]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    code = f"STANDARD_BLOCK_SOURCE_MISMATCH:{block}"
    assert _technical_standards(payload) == {3, 6, 8}
    assert payload["shared_failures"] == [
        {
            "affected_standards": [3, 6, 8],
            "code": code,
            "dependency": "refactor-policy-result:policy-blocks",
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
def test_metric_technical_failures_affect_only_gate_one_and_handoff(code: str) -> None:
    rendered = f"COMPLEXITY_RESULT:{code}"

    assert standard_block_ownership.technical_owners(rendered) == frozenset({1, 8})
    assert standard_block_ownership.expected_technical_dependency(rendered, "") == (
        "complexity-result:technical-errors",
        frozenset({1, 8}),
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


def test_short_task_quality_failure_stays_in_the_only_applicable_lane() -> None:
    inputs = _inputs("docs/release-note.md", [7], status="ADDED")
    inputs[3]["run_id"] = "999"

    payload = _compose(inputs)

    assert payload["short_task"] is True
    assert _results(payload) == [
        *("NOT_APPLICABLE_SHORT_TASK",) * 6,
        "TECHNICAL_FAILURE",
        "NOT_APPLICABLE_SHORT_TASK",
    ]
    assert "QUALITY_RESULT_BINDING_MISMATCH" in _entry(payload, 7)["technical_errors"]
    assert _entry(payload, 8)["technical_errors"] == []
    assert payload["shared_failures"] == []
    assert standard_results.validate_payload(payload, IDENTITY) is None


def test_short_task_ignores_review_blocks_owned_by_inapplicable_lanes() -> None:
    inputs = _inputs("docs/release-note.md", [1], status="ADDED")
    inputs[0]["review_evidence"] = None
    inputs[0]["policy_blocks"] = ["INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries"]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert payload["short_task"] is True
    assert payload["applicability_evidence"]["inapplicable_complexity_result"]["policy_blocks"] == [
        "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries"
    ]
    assert payload["source_outcomes"]["complexity"] == "failure"
    assert _results(payload) == [
        *("NOT_APPLICABLE_SHORT_TASK",) * 6,
        "PASS",
        "NOT_APPLICABLE_SHORT_TASK",
    ]


def test_short_task_rejects_unbound_inapplicable_block() -> None:
    payload = _compose(_inputs("docs/release-note.md", [1], status="ADDED"))
    payload["source_outcomes"]["complexity"] = "failure"
    payload["applicability_evidence"]["inapplicable_policy_blocks"] = [
        "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries"
    ]

    with pytest.raises(
        standard_results.StandardResultsError,
        match="MALFORMED_STANDARD_RESULTS_APPLICABILITY",
    ):
        standard_results.validate_payload(payload, IDENTITY)


@pytest.mark.parametrize("outcome", ["cancelled", "failure", "skipped"])
def test_short_task_requires_completed_complexity_outcome(outcome: str) -> None:
    payload = _compose(_inputs("docs/release-note.md", [1], status="ADDED"))
    payload["source_outcomes"]["complexity"] = outcome

    with pytest.raises(
        standard_results.StandardResultsError,
        match="MALFORMED_STANDARD_RESULTS_SOURCE_OUTCOMES",
    ):
        standard_results.validate_payload(payload, IDENTITY)


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
            {5, 6, 8},
        ),
        ("refactor", "failure", "MALFORMED_REFACTOR_RESULT", {6, 8}),
        ("quality", "failure", "MALFORMED_QUALITY_PROVENANCE", {7, 8}),
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
    inputs[2]["authorization"] = None
    inputs[2]["authorization_comment_id"] = None
    inputs[2]["policy_blocks"] = [block]
    inputs[2]["overall_result"] = "BLOCK"
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["refactor"] = "failure"

    payload = _compose(inputs, source_outcomes=outcomes)

    assert _results(payload) == ["PASS"] * 5 + ["BLOCK"] + ["PASS"] * 2
    assert _entry(payload, 6)["result"] == "BLOCK"
    assert _entry(payload, 6)["policy_blocks"] == [block]
    assert _technical_standards(payload) == set()
    assert payload["shared_failures"] == []

    mismatched = _compose(inputs, source_outcomes=SUCCESS_OUTCOMES)
    assert _entry(mismatched, 6)["technical_errors"] == ["MALFORMED_REFACTOR_RESULT"]


def test_failed_quality_capture_authenticates_its_gate_seven_block() -> None:
    inputs = _inputs()
    block = "QUALITY_GATE_FAILED:python.pytest.v1"
    _quality_command(inputs, "python.pytest.v1")["exit_code"] = 1
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

    payload = _compose(inputs)

    assert _entry(payload, 7)["result"] == "BLOCK"
    assert _entry(payload, 7)["policy_blocks"] == [block]
    assert _technical_standards(payload) == set()


def test_failed_quality_capture_without_gate_seven_block_is_malformed() -> None:
    inputs = _inputs()
    _quality_command(inputs, "python.pytest.v1")["exit_code"] = 1
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["quality"] = "failure"

    payload = _compose(inputs, source_outcomes=outcomes)

    assert _technical_standards(payload) == {4, 7, 8}
    code = "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE"
    assert all(_entry(payload, standard)["technical_errors"] == [code] for standard in (4, 7, 8))


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
    _set_quality_commands(inputs, language, (path,))
    inputs[0]["review_evidence"]["architecture"]["reviewed_paths"] = [path]
    inputs[0]["review_evidence"]["responsibility_boundary"]["path"] = path
    profile_command = _quality_command(inputs, adapter)
    profile_command["exit_code"] = 1
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["complexity"] = "failure"

    payload = _compose(inputs, source_outcomes=outcomes)

    assert _results(payload) == ["BLOCK", *("PASS",) * 7]
    assert _entry(payload, 1)["policy_blocks"] == ["FUNCTION_COMPLEXITY:too_complex"]
    assert payload["shared_failures"] == []

    forged = copy.deepcopy(inputs)
    forged[0]["policy_blocks"] = [f"QUALITY_GATE_FAILED:{adapter}"]
    forged_payload = _compose(forged, source_outcomes=outcomes)
    assert _technical_standards(forged_payload) == {4, 7, 8}


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
    _set_quality_commands(inputs, language, (path,))
    inputs[0]["review_evidence"]["architecture"]["reviewed_paths"] = [path]
    inputs[0]["review_evidence"]["responsibility_boundary"]["path"] = path
    _quality_command(inputs, architecture_adapter)["exit_code"] = 1
    outcomes = dict(SUCCESS_OUTCOMES)
    outcomes["complexity"] = "failure"

    payload = _compose(inputs, source_outcomes=outcomes)

    assert _results(payload) == ["PASS", "PASS", "BLOCK", *("PASS",) * 5]
    assert _entry(payload, 3)["policy_blocks"] == [block]
    assert payload["shared_failures"] == []


def test_architecture_policy_exit_without_gate_three_evidence_is_malformed() -> None:
    inputs = _inputs()
    _quality_command(inputs, "python.import-linter.v1")["exit_code"] = 1

    payload = _compose(inputs, source_outcomes=SUCCESS_OUTCOMES)

    assert _technical_standards(payload) == {3, 4}
    code = "COMPLEXITY_RESULT:ARCHITECTURE_RESULT_BINDING_MISMATCH"
    assert _entry(payload, 3)["technical_errors"] == [code]
    assert _entry(payload, 4)["technical_errors"] == [code]
    assert _entry(payload, 7)["result"] == "PASS"


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
    profile_command = _quality_command(inputs, "python.c901-touched.v1")
    profile_command["exit_code"] = 1

    payload = _compose(inputs, source_outcomes=SUCCESS_OUTCOMES)

    assert _results(payload) == ["PASS"] * 8
    assert payload["source_outcomes"]["quality"] == "success"
    assert payload["review_handoff"]["gaps"] == [
        {"path": "src/sample.py", "qualified_name": "legacy", "remaining_gap": 2}
    ]
    assert payload["review_handoff"]["follow_up"] == [
        {"next_target": 10, "path": "src/sample.py", "qualified_name": "legacy"}
    ]

    false_no_gap = copy.deepcopy(payload)
    false_no_gap["review_handoff"]["gaps"] = []
    false_no_gap["review_handoff_sha256"] = hashlib.sha256(
        _canonical(false_no_gap["review_handoff"])
    ).hexdigest()
    with pytest.raises(
        standard_results.StandardResultsError,
        match="HANDOFF_RESULT_BINDING_MISMATCH",
    ):
        standard_results.validate_payload(false_no_gap, IDENTITY, standard=8)

    forged = copy.deepcopy(inputs)
    forged[0]["language"] = "typescript"
    forged[0]["ruff_diagnostics"] = []
    assert _technical_standards(_compose(forged, source_outcomes=SUCCESS_OUTCOMES)) == set(
        range(1, 9)
    )


def test_completed_legacy_function_is_not_follow_up_work() -> None:
    inputs = _inputs()
    base = _metric("src/sample.py", "legacy", 14)
    head = _metric("src/sample.py", "legacy", 9)
    inputs[0]["functions"] = [
        _function(
            base,
            head,
            state="EXISTING_LEGACY",
            decision="PASS",
            debt=0,
            next_target=10,
        )
    ]
    inputs[0]["touched_qualified_functions"] = ["legacy"]

    handoff = _compose(inputs)["review_handoff"]

    assert handoff["functions"][0]["next_target"] == 10
    assert handoff["gaps"] == []
    assert handoff["follow_up"] == []


def test_complexity_adapter_tool_failure_still_blocks_gate_seven() -> None:
    inputs = _inputs()
    block = "QUALITY_GATE_FAILED:python.c901-touched.v1"
    profile_command = _quality_command(inputs, "python.c901-touched.v1")
    profile_command["exit_code"] = 2
    inputs[0]["policy_blocks"] = [block]
    inputs[0]["overall_result"] = "BLOCK"

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


def test_producer_writes_valid_evidence_without_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments, _, output = _producer_arguments(tmp_path)

    assert standard_results_producer.main(arguments) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert standard_results.validate_payload(payload, IDENTITY) is None
    assert capsys.readouterr().out == ""


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
        (
            "quality_profile",
            {4, 7, 8},
            "COMPLEXITY_RESULT:MALFORMED_QUALITY_EVIDENCE",
            "complexity-result:technical-errors",
        ),
        (
            "characterization",
            {5, 6, 8},
            "MALFORMED_CHARACTERIZATION_RESULT",
            "characterization-result",
        ),
        ("refactor", {6, 8}, "MALFORMED_REFACTOR_RESULT", "refactor-policy-result"),
        (
            "quality_provenance",
            {7, 8},
            "MALFORMED_QUALITY_RESULT_BINDING",
            "quality-profile:artifact-binding",
        ),
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


def _enforcer_arguments(
    path: Path,
    standard: int,
    sources: tuple[dict[str, Any], ...] | None = None,
    *,
    include_handoff_sources: bool = True,
) -> list[str]:
    arguments = [
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
    if standard == 8 and include_handoff_sources:
        source_values = sources or _inputs()
        complexity = path.with_name("complexity-result.json")
        provenance = path.with_name("quality-provenance.json")
        complexity.write_text(json.dumps(source_values[0]), encoding="utf-8")
        provenance.write_text(json.dumps(source_values[3]), encoding="utf-8")
        arguments.extend(
            [
                "--complexity-result",
                str(complexity),
                "--quality-provenance",
                str(provenance),
            ]
        )
    return arguments


def test_short_task_gate_eight_does_not_require_handoff_sources(tmp_path: Path) -> None:
    payload = _compose(_inputs("docs/release-note.md", [1], status="ADDED"))
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        standard_results_enforcer.main(_enforcer_arguments(path, 8, include_handoff_sources=False))
        == 0
    )


def test_exact_result_artifact_identity_mismatch_is_shared_via_enforcer(tmp_path: Path) -> None:
    payload = _compose(_inputs())
    payload["head_sha"] = "e" * 40
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert {
        standard_results_enforcer.main(_enforcer_arguments(path, standard))
        for standard in range(1, 9)
    } == {2}


def test_handoff_tampering_is_technical_failure_for_gate_eight_only(
    tmp_path: Path,
) -> None:
    payload = _compose(_inputs())
    payload["review_handoff"]["identity"]["head_sha"] = "e" * 40
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert [
        standard_results_enforcer.main(_enforcer_arguments(path, standard))
        for standard in range(1, 9)
    ] == [*([0] * 7), 2]


@pytest.mark.parametrize("poison", ["changed-file", "command", "citation", "standard"])
def test_rehashed_handoff_fact_tampering_fails_gate_eight_only(tmp_path: Path, poison: str) -> None:
    payload = _compose(_inputs())
    handoff = payload["review_handoff"]
    if poison == "changed-file":
        handoff["changed_files"][0]["new_path"] = "src/invented.py"
    elif poison == "command":
        handoff["validation"]["commands"][0]["executed_arguments"].append("--invented")
    elif poison == "citation":
        handoff["sources"]["change"]["citations"] = ["invented.json:changed_files"]
    else:
        handoff["validation"]["standards"][0]["result"] = "BLOCK"
    payload["review_handoff_sha256"] = hashlib.sha256(_canonical(handoff)).hexdigest()
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert standard_results_enforcer.main(_enforcer_arguments(path, 1)) == 0
    assert standard_results_enforcer.main(_enforcer_arguments(path, 8)) == 2


def test_coordinated_rehash_cannot_detach_change_fact_from_authenticated_source(
    tmp_path: Path,
) -> None:
    payload = _compose(_inputs())
    handoff = payload["review_handoff"]
    handoff["changed_files"][0]["new_path"] = "src/invented.py"
    handoff["sources"]["change"]["sha256"] = hashlib.sha256(
        _canonical(handoff["changed_files"])
    ).hexdigest()
    payload["review_handoff_sha256"] = hashlib.sha256(_canonical(handoff)).hexdigest()
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert standard_results_enforcer.main(_enforcer_arguments(path, 1)) == 0
    assert standard_results_enforcer.main(_enforcer_arguments(path, 8)) == 2


@pytest.mark.parametrize("poison", ["command", "coverage"])
def test_coordinated_rehash_cannot_detach_handoff_from_authenticated_sources(
    tmp_path: Path, poison: str
) -> None:
    inputs = _inputs()
    payload = _compose(inputs)
    handoff = payload["review_handoff"]
    if poison == "command":
        handoff["validation"]["commands"][0]["executed_arguments"].append("--invented")
        source = "validation"
        fact = handoff["validation"]["commands"]
    else:
        handoff["coverage"]["maximum_complexity"] = 999
        source = "coverage"
        fact = handoff["coverage"]
    handoff["sources"][source]["sha256"] = hashlib.sha256(_canonical(fact)).hexdigest()
    payload["review_handoff_sha256"] = hashlib.sha256(_canonical(handoff)).hexdigest()
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert standard_results_enforcer.main(_enforcer_arguments(path, 1, inputs)) == 0
    assert standard_results_enforcer.main(_enforcer_arguments(path, 8, inputs)) == 2


def test_rehashed_false_no_risk_claim_fails_gate_eight_only(tmp_path: Path) -> None:
    inputs = _inputs()
    inputs[0]["policy_blocks"] = ["QUALITY_SCOPE_NARROWING"]
    inputs[0]["overall_result"] = "BLOCK"
    payload = _compose(inputs)
    handoff = payload["review_handoff"]
    assert handoff["risks"]
    handoff["risks"] = []
    payload["review_handoff_sha256"] = hashlib.sha256(_canonical(handoff)).hexdigest()
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert standard_results_enforcer.main(_enforcer_arguments(path, 1)) == 0
    assert standard_results_enforcer.main(_enforcer_arguments(path, 8)) == 2


def _producer_arguments(tmp_path: Path) -> tuple[list[str], dict[str, Path], Path]:
    inputs = _inputs()
    _bind_quality(inputs)
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
            "--expected-base-characterization-artifact-id",
            EXPECTED_CHARACTERIZATION_ARTIFACTS["base"]["id"],
            "--expected-base-characterization-artifact-digest",
            EXPECTED_CHARACTERIZATION_ARTIFACTS["base"]["digest"],
            "--expected-base-characterization-capture-sha256",
            EXPECTED_CHARACTERIZATION_ARTIFACTS["base"]["capture_sha256"],
            "--expected-head-characterization-artifact-id",
            EXPECTED_CHARACTERIZATION_ARTIFACTS["head"]["id"],
            "--expected-head-characterization-artifact-digest",
            EXPECTED_CHARACTERIZATION_ARTIFACTS["head"]["digest"],
            "--expected-head-characterization-capture-sha256",
            EXPECTED_CHARACTERIZATION_ARTIFACTS["head"]["capture_sha256"],
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
            {5, 6, 8},
            "MISSING_CHARACTERIZATION_RESULT",
            "characterization-result",
        ),
        ("refactor", {6, 8}, "MISSING_REFACTOR_RESULT", "refactor-policy-result"),
        (
            "quality",
            {7, 8},
            "MISSING_QUALITY_PROVENANCE",
            "quality-profile:artifact-binding",
        ),
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
    assert capsys.readouterr().out == ""
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
    assert capsys.readouterr().out == ""
    assert _technical_standards(payload) == {5, 6, 8}
    assert payload["shared_failures"] == [
        {
            "affected_standards": [5, 6, 8],
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
    assert capsys.readouterr().out == ""
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
        ("characterization", {5, 6, 8}, "MALFORMED_CHARACTERIZATION_RESULT"),
        ("refactor", {6, 8}, "MALFORMED_REFACTOR_RESULT"),
        ("quality", {7, 8}, "MALFORMED_QUALITY_PROVENANCE"),
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
    assert capsys.readouterr().out == ""
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


def test_workflow_keeps_advisory_review_out_of_the_required_path() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/organization-required.yml"
    ).read_text(encoding="utf-8")
    quality = _job("quality-profile", "deterministic-evidence")
    evidence = _job("deterministic-evidence", "standard-results")
    matrix = _job("standard-results", "supportability-gate")
    gate = _job("supportability-gate", None)
    packaged_modules = {
        module.name for module in pkgutil.iter_modules(supportability_gate.__path__)
    }

    assert "capture-outcome: ${{ steps.capture.outcome }}" in quality
    assert "id: capture\n        continue-on-error: true" in quality
    assert "id: upload\n        if: always()" in quality
    assert 'exit "$status"' in quality
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
    for side in ("base", "head"):
        for field in ("artifact-id", "artifact-digest", "capture-sha256"):
            assert (
                f"--expected-{side}-characterization-{field} "
                f'"${{{{ needs.characterize-{side}.outputs.{field} }}}}"' in evidence
            )
    assert "observe-codex-review:" not in workflow
    assert "collect-codex-review:" not in workflow
    assert "review-required" not in workflow
    assert "review_required" not in workflow
    assert "@codex review" not in workflow
    assert "supportability_gate.codex_review" not in workflow
    assert {"codex_review", "focused_review"}.isdisjoint(packaged_modules)
    rows = re.findall(r"(?m)^          - standard: ([1-8])\n            context: (.+)$", matrix)
    assert rows == [
        (str(standard), context)
        for standard, context in enumerate(standard_results.CHECK_CONTEXTS, start=1)
    ]
    assert "fail-fast: false" in matrix
    assert "python -P -m supportability_gate.standard_results_enforcer" in matrix
    assert '--complexity-result "$RUNNER_TEMP/evidence/complexity-result.json"' in matrix
    assert '--quality-provenance "$RUNNER_TEMP/evidence/quality-provenance.json"' in matrix
    assert "if: always()" in matrix
    assert "name: Supportability Gate" in gate
    assert "standard-results" in gate
    assert "STANDARD_RESULTS_RESULT: ${{ needs.standard-results.result }}" in gate
    assert "OBSERVER_RESULT" not in gate
    assert "COLLECTOR_RESULT" not in gate
    assert "REVIEW_REQUIRED" not in gate
