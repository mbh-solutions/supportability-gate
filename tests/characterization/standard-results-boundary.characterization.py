from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _write(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, sort_keys=True).encode())


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
        "separation_of_concerns": {"after": "One owner.", "before": "Mixed owners."},
    }


def _changed_file(path: str, *, status: str = "MODIFIED") -> dict[str, object]:
    production = path.startswith("src/")
    return {
        "base_production": production,
        "changed_head_lines": [7],
        "complexity_assessed": production,
        "head_production": production,
        "new_path": path,
        "old_path": None if status == "ADDED" else path,
        "status": status,
    }


def _complexity(identity: Any, standard_sha256: str, path: str, status: str) -> dict[str, Any]:
    production_paths = [path] if path.startswith("src/") else []
    return {
        "architecture": {
            "adapter": "python.ast-imports.v1",
            "blocks": [],
            "covered_paths": production_paths,
            "edges": [],
            "executed": True,
            "nodes": production_paths,
        },
        "base_contract_blob_sha": "1" * 40,
        "base_sha": identity.base_sha,
        "base_tree_sha": "c" * 40,
        "changed_files": [_changed_file(path, status=status)],
        "commands": [
            {
                "arguments": ["diff", "--name-status", identity.base_sha, identity.head_sha],
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
        "head_sha": identity.head_sha,
        "head_tree_sha": "d" * 40,
        "high_risk_paths": [],
        "language": "python",
        "modularity": {
            "blocks": [],
            "changed_paths": production_paths,
            "coupling_edges": [],
            "coverage": [],
            "justifications": [],
            "new_paths": [],
        },
        "overall_result": "PASS",
        "policy_blocks": [],
        "production_paths": ["src"],
        "quality_profile": {
            "base_sha": identity.base_sha,
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
            "head_sha": identity.head_sha,
            "high_risk_paths": [],
            "language": "python",
            "maximum_complexity": 10,
            "production_files": ["src/sample.py"],
            "production_paths": ["src"],
            "repository_remote": f"github.com/{identity.repository}",
            "schema_version": "quality-gates.v3",
            "workflow_sha": identity.workflow_sha,
        },
        "rename_bindings": [],
        "repository_remote": f"github.com/{identity.repository}",
        "review_evidence": _review_evidence(),
        "review_evidence_path": ".supportability-review.toml",
        "ruff_diagnostics": [],
        "schema_version": "1.0",
        "standard_sha256": standard_sha256,
        "technical_errors": [],
        "tool_versions": {},
        "touched_qualified_functions": [],
    }


def _characterization(identity: Any, path: str) -> dict[str, Any]:
    behavior = hashlib.sha256(_canonical([["sample", "e" * 64]])).hexdigest()
    production_paths = [path] if path.startswith("src/") else []
    return {
        "artifacts": {
            "base": {"capture_sha256": "3" * 64, "digest": "4" * 64, "id": "701"},
            "head": {"capture_sha256": "5" * 64, "digest": "6" * 64, "id": "702"},
        },
        "base_sha": identity.base_sha,
        "behavior_fingerprint": behavior,
        "coverage": {"covered_paths": production_paths, "required_paths": production_paths},
        "head_sha": identity.head_sha,
        "manifest_blob_sha": "8" * 40,
        "manifest_sha256": "9" * 64,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": f"github.com/{identity.repository}",
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
        "workflow_sha": identity.workflow_sha,
    }


def _refactor(identity: Any, characterization: dict[str, Any], path: str) -> dict[str, Any]:
    return {
        "applicable": False,
        "authorization": None,
        "authorization_comment_id": None,
        "base_sha": identity.base_sha,
        "characterization_sha256": hashlib.sha256(_canonical(characterization)).hexdigest(),
        "changed_paths": [path],
        "head_sha": identity.head_sha,
        "other_standard_clauses_waived": False,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": identity.repository,
        "schema_version": "refactor-policy-result.v1",
        "targets": [],
        "unbounded_paths": [],
    }


def _quality(identity: Any) -> dict[str, Any]:
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
        "repository": identity.repository,
        "repository_id": str(identity.repository_id),
        "run_attempt": str(identity.run_attempt),
        "run_id": str(identity.run_id),
        "runner_environment": "github-hosted",
    }


def _inputs(
    identity: Any, standard_sha256: str, path: str = "src/sample.py", status: str = "MODIFIED"
) -> dict[str, dict[str, Any]]:
    characterization = _characterization(identity, path)
    return {
        "complexity": _complexity(identity, standard_sha256, path, status),
        "characterization": characterization,
        "refactor": _refactor(identity, characterization, path),
        "quality": _quality(identity),
    }


def _common(identity: Any) -> list[str]:
    return [
        "--repository",
        identity.repository,
        "--repository-id",
        str(identity.repository_id),
        "--base-sha",
        identity.base_sha,
        "--head-sha",
        identity.head_sha,
        "--workflow-sha",
        identity.workflow_sha,
        "--run-id",
        str(identity.run_id),
        "--run-attempt",
        str(identity.run_attempt),
    ]


def _run_case(
    directory: Path,
    name: str,
    inputs: dict[str, dict[str, Any]],
    identity: Any,
    producer: Any,
    enforcer: Any,
) -> dict[str, object]:
    source_paths = {source: directory / f"{name}-{source}.json" for source in inputs}
    for source, value in inputs.items():
        _write(source_paths[source], value)
    output = directory / f"{name}-standard-results.json"
    common = _common(identity)
    outcomes = {
        source: "success" if inputs[source]["overall_result"] == "PASS" else "failure"
        for source in ("complexity", "characterization", "refactor")
    }
    outcomes["quality"] = (
        "failure"
        if any(
            not command["executed"] or command["exit_code"]
            for command in inputs["complexity"]["quality_profile"]["commands"]
        )
        else "success"
    )
    arguments = [
        *common,
        "--complexity-result",
        str(source_paths["complexity"]),
        "--characterization-result",
        str(source_paths["characterization"]),
        "--refactor-result",
        str(source_paths["refactor"]),
        "--quality-provenance",
        str(source_paths["quality"]),
        "--expected-quality-artifact-id",
        "789",
        "--expected-quality-artifact-digest",
        "d" * 64,
        "--expected-quality-capture-sha256",
        "c" * 64,
        "--complexity-outcome",
        outcomes["complexity"],
        "--characterization-outcome",
        outcomes["characterization"],
        "--install-outcome",
        "success",
        "--refactor-outcome",
        outcomes["refactor"],
        "--quality-outcome",
        outcomes["quality"],
        "--output",
        str(output),
    ]
    producer_stdout = io.StringIO()
    with contextlib.redirect_stdout(producer_stdout):
        producer_exit = producer.main(arguments)
    with contextlib.redirect_stdout(io.StringIO()):
        enforcer_exits = [
            enforcer.main([*common, "--input", str(output), "--standard", str(standard)])
            for standard in range(1, 9)
        ]
    payload = json.loads(output.read_bytes())
    return {
        "applicability_evidence": payload["applicability_evidence"],
        "enforcer_exits": enforcer_exits,
        "lane_failures": [
            {
                "policy_blocks": entry["policy_blocks"],
                "standard": entry["standard"],
                "technical_errors": entry["technical_errors"],
            }
            for entry in payload["entries"]
            if entry["policy_blocks"] or entry["technical_errors"]
        ],
        "producer_exit": producer_exit,
        "review_required": producer_stdout.getvalue().rstrip("\n"),
        "rows": [
            {
                "applicable": entry["applicable"],
                "evidence_sources": entry["evidence_sources"],
                "result": entry["result"],
            }
            for entry in payload["entries"]
        ],
        "shared_failures": payload["shared_failures"],
        "short_task": payload["short_task"],
    }


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    definition = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"])
    modules = (
        "standard_block_ownership.py",
        "standard_results.py",
        "standard_results_enforcer.py",
        "standard_results_producer.py",
    )
    root = (
        target
        if all((target / "src/supportability_gate" / name).is_file() for name in modules)
        else definition
    )
    sys.path.insert(0, str(root / "src"))

    from supportability_gate import (  # noqa: PLC0415
        clause_inventory,
        standard_results,
        standard_results_enforcer,
        standard_results_producer,
    )

    identity = standard_results.RunIdentity(
        "example/repository", 123, "b" * 40, "a" * 40, "f" * 40, 456, 1
    )
    cases: dict[str, dict[str, dict[str, Any]]] = {}
    cases["clean"] = _inputs(identity, clause_inventory.STANDARD_SHA256)

    gate_three = copy.deepcopy(cases["clean"])
    cycle = "IMPORT_CYCLE:src/a.py:1:src.b"
    gate_three["complexity"]["architecture"]["blocks"] = [cycle]
    gate_three["complexity"]["policy_blocks"] = [cycle]
    gate_three["complexity"]["overall_result"] = "BLOCK"
    cases["gate-3-policy"] = gate_three

    gate_seven = copy.deepcopy(cases["clean"])
    gate_seven["quality"]["artifact_digest"] = "e" * 64
    cases["gate-7-technical"] = gate_seven

    simultaneous = copy.deepcopy(gate_three)
    simultaneous["quality"]["artifact_digest"] = "e" * 64
    cases["simultaneous"] = simultaneous

    shared_complexity = copy.deepcopy(cases["clean"])
    shared_complexity["complexity"]["head_sha"] = "e" * 40
    cases["shared-complexity-identity"] = shared_complexity

    shared_review = copy.deepcopy(cases["clean"])
    review_block = "MALFORMED_REVIEW_EVIDENCE:document"
    shared_review["complexity"]["review_evidence"] = None
    shared_review["complexity"]["policy_blocks"] = [review_block]
    shared_review["complexity"]["overall_result"] = "BLOCK"
    cases["shared-review-document"] = shared_review

    cases["short-task"] = _inputs(
        identity,
        clause_inventory.STANDARD_SHA256,
        "docs/release-note.md",
        "ADDED",
    )

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        behavior_cases = {
            name: _run_case(
                directory,
                name,
                inputs,
                identity,
                standard_results_producer,
                standard_results_enforcer,
            )
            for name, inputs in cases.items()
        }
    behavior = {
        "cases": behavior_cases,
        "contexts": [
            entry["check_context"]
            for entry in standard_results.compose_results(
                *cases["clean"].values(),
                identity,
                expected_quality_artifact={
                    "capture_sha256": "c" * 64,
                    "digest": "d" * 64,
                    "id": "789",
                },
                source_outcomes={
                    name: "success"
                    for name in ("install", "complexity", "characterization", "refactor", "quality")
                },
            )["entries"]
        ],
        "schema_version": standard_results.SCHEMA_VERSION,
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "standard-results-boundary",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
