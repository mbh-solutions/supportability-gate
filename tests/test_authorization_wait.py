from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import hosted_authorization_evidence as hosted
import pytest
import test_standard_results as fixtures

from supportability_gate import qualification_bundle


def _waiting_evidence(directory: Path, extra_block: str | None = None) -> None:
    inputs = fixtures._inputs()
    refactor = inputs[2]
    refactor.update(
        authorization=None,
        authorization_comment_id=None,
        overall_result="BLOCK",
        policy_blocks=["MISSING_OWNER_AUTHORIZATION"],
    )
    if extra_block:
        refactor["policy_blocks"].append(extra_block)
    payload = fixtures._compose(inputs)
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
