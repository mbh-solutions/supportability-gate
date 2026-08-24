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
    codex_review,
    review_evidence,
    standard_results,
    standard_results_enforcer,
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
    codex_error: codex_review.CodexReviewError | None = None,
) -> dict[str, object]:
    return standard_results.compose_results(*inputs, IDENTITY, _evidence(), codex_error)


def _poison(
    standard: int,
    inputs: tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]],
) -> None:
    complexity, characterization, refactor, _ = inputs
    if standard == 1:
        complexity["functions"] = [
            {"decision": "BLOCK", "head": {"qualified_name": "sample.complex"}}
        ]
    elif standard == 2:
        complexity["policy_blocks"].append("MISSING_REVIEW_EVIDENCE:separation_of_concerns.after")
    elif standard == 3:
        complexity["architecture"]["blocks"].append("IMPORT_CYCLE:src/a.py:1:src.b")
        complexity["policy_blocks"].append("IMPORT_CYCLE:src/a.py:1:src.b")
    elif standard == 4:
        complexity["modularity"]["blocks"].append("MISSING_NEW_LOCATION_JUSTIFICATION:src/new.py")
        complexity["policy_blocks"].append("MISSING_NEW_LOCATION_JUSTIFICATION:src/new.py")
    elif standard == 5:
        characterization["policy_blocks"] = ["MISSING_BASELINE"]
        characterization["overall_result"] = "BLOCK"
    elif standard == 6:
        refactor["applicable"] = True
        refactor["policy_blocks"] = ["MISSING_OWNER_AUTHORIZATION"]
        refactor["overall_result"] = "BLOCK"
    elif standard == 7:
        complexity["policy_blocks"].append("QUALITY_GATE_FAILED:python.pytest.v1")
    else:
        complexity["policy_blocks"].append("MISSING_REVIEW_EVIDENCE:review_handoff.summary")
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
    ("standard", "family"),
    [
        (standard, family)
        for standard, families in enumerate(standard_results.BLOCK_FAMILIES, start=1)
        for family in families
    ],
)
def test_every_declared_block_family_has_one_standard_owner(standard: int, family: str) -> None:
    block = f"{family}sample" if family.endswith(":") else family
    assert standard_results._require_owner(block) == standard


@pytest.mark.parametrize(
    ("standard", "location"),
    [(standard, location) for location, standard in standard_results.REVIEW_FIELD_OWNERS.items()]
    + [(4, "module_boundaries"), (4, "module_boundaries[0].basis")],
)
def test_every_review_field_family_has_one_standard_owner(standard: int, location: str) -> None:
    assert standard_results._require_owner(f"MISSING_REVIEW_EVIDENCE:{location}") == standard


def test_unknown_or_multiply_owned_blocks_make_all_results_technical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unknown = _inputs()
    unknown[1]["policy_blocks"] = ["UNKNOWN_CHARACTERIZATION_POLICY"]
    unknown[1]["overall_result"] = "BLOCK"
    unknown[2]["characterization_sha256"] = hashlib.sha256(_canonical(unknown[1])).hexdigest()

    unknown_payload = _compose(unknown)

    assert [entry["result"] for entry in unknown_payload["entries"]] == ["TECHNICAL_FAILURE"] * 8
    families = list(standard_results.BLOCK_FAMILIES)
    families[6] = (*families[6], "IMPORT_CYCLE:")
    monkeypatch.setattr(standard_results, "BLOCK_FAMILIES", tuple(families))
    multiple = _inputs()
    _poison(3, multiple)

    multiple_payload = _compose(multiple)

    assert [entry["result"] for entry in multiple_payload["entries"]] == ["TECHNICAL_FAILURE"] * 8


def test_shared_binding_or_codex_trust_corruption_makes_all_results_technical() -> None:
    binding = _inputs()
    binding[3]["run_id"] = "999"

    binding_payload = _compose(binding)
    codex_payload = _compose(
        _inputs(),
        codex_error=codex_review.CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE"),
    )

    assert [entry["result"] for entry in binding_payload["entries"]] == ["TECHNICAL_FAILURE"] * 8
    assert [entry["result"] for entry in codex_payload["entries"]] == ["TECHNICAL_FAILURE"] * 8


@pytest.mark.parametrize(
    "case",
    ["missing", "duplicate", "unknown", "identity", "artifact", "quality", "partial_technical"],
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
    else:
        entries[0]["result"] = "TECHNICAL_FAILURE"
        entries[0]["technical_errors"] = ["SHARED_TRUST_FAILURE"]

    with pytest.raises(standard_results.StandardResultsError):
        standard_results_enforcer.validate_payload(payload, IDENTITY)


def test_enforcer_returns_pass_block_or_technical(tmp_path: Path) -> None:
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
        "1",
    ]
    path = tmp_path / "standard-results.json"
    path.write_text(json.dumps(_compose(_inputs())), encoding="utf-8")
    assert standard_results_enforcer.main(common) == 0
    blocked = _inputs()
    _poison(1, blocked)
    path.write_text(json.dumps(_compose(blocked)), encoding="utf-8")
    assert standard_results_enforcer.main(common) == 1
    path.write_text("{}", encoding="utf-8")
    assert standard_results_enforcer.main(common) == 2


def test_structured_review_collects_every_applicable_field_defect() -> None:
    _, blocks = review_evidence.evaluate_review_evidence(
        b'schema_version = "1.0"\n\n[behavior]\nintended_behavior = ""\nproof = 1\n'
    )

    assert len(blocks) == 19
    assert "INSUFFICIENT_REVIEW_EVIDENCE:behavior.intended_behavior" in blocks
    assert "MALFORMED_REVIEW_EVIDENCE:behavior.proof" in blocks
    assert "MISSING_REVIEW_EVIDENCE:architecture.reviewed_paths" in blocks
    assert "MISSING_REVIEW_EVIDENCE:review_handoff.remaining_risks" in blocks


def test_workflow_wires_each_matrix_lane_to_the_exact_artifact_and_enforcer() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/organization-required.yml"
    ).read_text(encoding="utf-8")

    assert "fail-fast: false" in workflow
    assert re.search(r"(?m)^  standard-results:\n(?:.|\n)*?^    if: always\(\)$", workflow)
    assert not re.search(r"(?m)^\s+name: Supportability Gate\s*$", workflow)
    assert not re.search(r"(?m)^  supportability-gate:\s*$", workflow)
    job = workflow.split("\n  standard-results:\n", 1)[1]
    rows = re.findall(r"(?m)^          - standard: ([1-8])\n            context: (.+)$", job)
    assert rows == [
        (str(standard), context)
        for standard, context in enumerate(standard_results.CHECK_CONTEXTS, start=1)
    ]
    assert "needs: supportability-evidence" in job
    assert "artifact-ids: ${{ needs.supportability-evidence.outputs.artifact-id }}" in job
    assert "STANDARD: ${{ matrix.standard }}" in job
    assert "python -P -m supportability_gate.standard_results_enforcer \\" in job
    assert '--input "$RUNNER_TEMP/evidence/standard-results.json" \\' in job
    assert '--standard "$STANDARD"' in job
