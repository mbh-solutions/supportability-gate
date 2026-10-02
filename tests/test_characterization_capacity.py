"""Finite evidence capacity and lossless machine-capture transport controls."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import test_characterization as legacy

from supportability_gate import characterization, refactor_policy


def _manifest(ordinary: int, observed: int) -> bytes:
    scenarios = []
    obligations = []
    for index in range(ordinary + observed):
        identifier = f"scenario-{index:03}"
        api = f"src/sample.py::function:calculate_{index}" if index >= ordinary else None
        scenarios.append(
            {"id": identifier, "kind": "regression", "covers": ["src/sample.py"], "api": api}
        )
        obligations.append(
            {
                "id": identifier,
                "category": "behavior" if api else "static",
                "scenario": identifier,
                "selector": "$",
                "target": api or f"scenario:{identifier}",
            }
        )
    return characterization._canonical(
        {
            "schema_version": "3.0",
            "scenarios": scenarios,
            "obligations": obligations,
            "transitions": [],
        }
    )


@pytest.mark.parametrize("ordinary,observed", [(50, 0), (51, 0), (64, 0), (33, 31), (14, 50)])
def test_manifest_admits_bounded_complete_evidence(ordinary: int, observed: int) -> None:
    manifest = characterization.parse_manifest(_manifest(ordinary, observed), "a" * 40)
    assert len(manifest.scenarios) == ordinary + observed
    assert sum(item.api is not None for item in manifest.scenarios) == observed


@pytest.mark.parametrize("ordinary,observed", [(65, 0), (15, 50), (0, 51), (13, 51)])
def test_manifest_rejects_total_or_observed_overflow_before_execution(
    ordinary: int, observed: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Overflow admission must not invoke any target execution")

    monkeypatch.setattr(legacy.hosted_characterization, "_prepare_container", forbidden)
    monkeypatch.setattr(legacy.hosted_characterization, "_scenario_capture", forbidden)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    repository, _, base_sha, _ = legacy._repository(tmp_path)
    (repository / characterization.MANIFEST_PATH).write_bytes(_manifest(ordinary, observed))
    head_sha = legacy._commit(repository, "synthetic overflow admission control")
    with pytest.raises(
        characterization.CharacterizationError, match="MALFORMED_CHARACTERIZATION_MANIFEST"
    ):
        legacy.hosted_characterization.capture_evidence(
            repository,
            repository,
            base_sha=base_sha,
            head_sha=head_sha,
            side="head",
            repository="example/fixture",
            repository_id="123",
            workflow_sha="f" * 40,
            run_id="456",
            run_attempt="1",
            job="characterize-head",
        )


def _result_rows(count: int) -> list[dict[str, object]]:
    return [
        {
            "id": f"scenario-{index:03}",
            "kind": "regression",
            "covers": ["src/sample.py"],
            "command": [
                "python3.12",
                "-P",
                f"tests/characterization/scenario-{index:03}.characterization.py",
            ],
            "base_behavior_sha256": "a" * 64,
            "head_behavior_sha256": "a" * 64,
            "golden_behavior_sha256": "a" * 64,
            "compatibility": "PASS",
        }
        for index in range(count)
    ]


def test_serialized_capacity_agrees_and_rejects_malformed_rows() -> None:
    assert len(characterization._result_scenarios(_result_rows(64))) == 64
    duplicate = _result_rows(64)
    duplicate[-1] = copy.deepcopy(duplicate[0])
    reordered = _result_rows(64)[::-1]
    extra = _result_rows(64)
    extra[0]["unknown"] = True
    for rows in (_result_rows(65), duplicate, reordered, extra):
        with pytest.raises(
            characterization.CharacterizationError, match="MALFORMED_CHARACTERIZATION_RESULT"
        ):
            characterization._result_scenarios(rows)
    with pytest.raises(
        characterization.CharacterizationError, match="MALFORMED_CHARACTERIZATION_RESULT"
    ):
        characterization._result_api_facts([{}] * 51)
    with pytest.raises(refactor_policy.RefactorPolicyError, match="MALFORMED_OWNER_AUTHORIZATION"):
        refactor_policy.parse_introduction_grants([{}] * 51)


def test_hosted_capture_is_compact_and_provenance_binds_actual_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "authentication": {"repository": "example/fixture"},
        "observations": [{"value": "é"}] * 32000,
    }
    environment = {"fixture": "synthetic transport control, not hosted evidence"}
    pretty = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    compact = characterization._canonical(payload) + b"\n"
    assert len(pretty) > characterization.MAX_JSON_BYTES
    assert len(compact) < characterization.MAX_JSON_BYTES
    monkeypatch.setattr(
        legacy.hosted_characterization,
        "capture_evidence",
        lambda *args, **kwargs: (payload, environment),
    )
    output = tmp_path / "capture.json"
    arguments = [
        "--target-repository",
        str(tmp_path),
        "--definition-repository",
        str(tmp_path),
        "--base-ref",
        "a" * 40,
        "--head-ref",
        "b" * 40,
        "--side",
        "head",
        "--repository",
        "example/fixture",
        "--repository-id",
        "123",
        "--workflow-sha",
        "f" * 40,
        "--run-id",
        "456",
        "--run-attempt",
        "1",
        "--job",
        "characterize-head",
        "--output",
        str(output),
    ]
    assert legacy.hosted_characterization.main(arguments) == 0
    assert output.read_bytes() == compact
    assert json.loads(output.read_bytes()) == payload
    sidecar = json.loads(characterization._provenance_path(output).read_bytes())
    assert sidecar["capture_sha256"] == characterization._sha256(compact)
    assert sidecar["authentication"] == payload["authentication"]
    assert sidecar["environment"] == environment
    assert sidecar["capture_sha256"] != characterization._sha256(pretty)
    result = tmp_path / "result.json"
    assert characterization._write_json(result, payload) == pretty


def test_transport_keeps_exact_json_reader_byte_boundary() -> None:
    exact = b'"' + b"x" * (characterization.MAX_JSON_BYTES - 2) + b'"'
    assert (
        len(characterization._read_json_bytes(exact, "OVERSIZED"))
        == characterization.MAX_JSON_BYTES - 2
    )
    with pytest.raises(characterization.CharacterizationError, match="OVERSIZED"):
        characterization._read_json_bytes(exact + b"\n", "OVERSIZED")
