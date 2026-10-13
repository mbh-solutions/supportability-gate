from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import hosted_authorization_evidence as hosted
import hosted_check_publisher as publisher
import pytest
import test_standard_results as fixtures

from supportability_gate import qualification_bundle


def _waiting_evidence(
    directory: Path,
    extra_block: str | None = None,
    *,
    correction: bool = False,
    missing_code: str = "MISSING_OWNER_AUTHORIZATION",
) -> None:
    inputs = fixtures._correction_inputs() if correction else fixtures._inputs()
    refactor = inputs[2]
    refactor.update(
        authorization=None,
        authorization_comment_id=None,
        overall_result="BLOCK",
        policy_blocks=[missing_code],
    )
    if extra_block:
        refactor["policy_blocks"].append(extra_block)
    if correction:
        refactor["policy_blocks"].append("UNAUTHORIZED_CORRECTION")
        refactor["result_classification"] = "BLOCK"
    outcomes = None
    if correction:
        outcomes = {**fixtures.SUCCESS_OUTCOMES, "refactor": "failure"}
    payload = fixtures._compose(inputs, source_outcomes=outcomes)
    directory.mkdir()
    for name, source in zip(hosted.SOURCE_FILES, inputs, strict=True):
        (directory / name).write_bytes(json.dumps(source).encode())
    (directory / "standard-results.json").write_bytes(json.dumps(payload).encode())
    qualification_bundle.build_bundle(directory, directory / "qualification-bundle.zip")


def test_missing_authorization_waits_but_canonical_decision_stays_block(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    _waiting_evidence(directory)
    waiting = hosted.awaiting_standards(directory, fixtures.IDENTITY)
    assert waiting == frozenset({6})
    assert (
        qualification_bundle.restore_bundle(directory / "qualification-bundle.zip").decision
        == "BLOCK"
    )


def test_other_policy_failure_is_not_hidden_by_wait(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    _waiting_evidence(directory, "BROAD_AUTHORIZATION_REQUIRED")
    assert not hosted.awaiting_standards(directory, fixtures.IDENTITY)


def test_earlier_head_grant_waits_for_current_authorization_without_becoming_authority(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "evidence"
    _waiting_evidence(directory, missing_code="STALE_OWNER_AUTHORIZATION")
    assert hosted.awaiting_standards(directory, fixtures.IDENTITY) == frozenset({6})
    refactor = json.loads((directory / "refactor-policy-result.json").read_bytes())
    assert refactor["authorization"] is None
    assert refactor["overall_result"] == "BLOCK"


def test_observed_correction_waits_without_reconciling_unauthorized_behavior(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "evidence"
    _waiting_evidence(directory, correction=True)
    waiting = hosted.awaiting_standards(directory, fixtures.IDENTITY)
    assert waiting == frozenset({5, 6})
    payload = json.loads((directory / "standard-results.json").read_bytes())
    assert payload["result_classification"] == "BLOCK"
    assert payload["entries"][4]["policy_blocks"] == ["INCOMPATIBLE_POST_CHANGE_BEHAVIOR:sample"]


@pytest.mark.parametrize("field", ["head_sha", "base_sha", "workflow_sha"])
def test_wait_rejects_wrong_identity(tmp_path: Path, field: str) -> None:
    directory = tmp_path / "evidence"
    _waiting_evidence(directory)
    with pytest.raises(ValueError):
        hosted.awaiting_standards(directory, replace(fixtures.IDENTITY, **{field: "9" * 40}))


def test_wait_rejects_substituted_sources(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    _waiting_evidence(directory)
    path = directory / "refactor-policy-result.json"
    value = json.loads(path.read_bytes())
    value["policy_blocks"] = ["STALE_OWNER_AUTHORIZATION"]
    path.write_bytes(json.dumps(value).encode())
    with pytest.raises(ValueError, match="EVIDENCE_MISMATCH"):
        hosted.awaiting_standards(directory, fixtures.IDENTITY)


def test_pending_publication_has_no_success_conclusion(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    _waiting_evidence(directory)
    summary = tmp_path / "summary.txt"
    summary.write_text("MISSING_OWNER_AUTHORIZATION")
    arguments = publisher._parser().parse_args(
        [
            "--repository",
            fixtures.IDENTITY.repository,
            "--repository-id",
            "123",
            "--head-sha",
            fixtures.IDENTITY.head_sha,
            "--base-sha",
            fixtures.IDENTITY.base_sha,
            "--workflow-sha",
            fixtures.IDENTITY.workflow_sha,
            "--run-id",
            "456",
            "--run-attempt",
            "1",
            "--standard",
            "6",
            "--result-exit-code",
            "1",
            "--summary",
            str(summary),
            "--evidence-directory",
            str(directory),
        ]
    )
    value = publisher._payload(arguments)
    assert value["status"] == "in_progress"
    assert "conclusion" not in value
    assert value["output"]["title"] == "Awaiting owner authorization"
