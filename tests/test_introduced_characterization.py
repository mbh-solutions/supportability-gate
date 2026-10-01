"""Synthetic Git/capture integration fixtures, never represented as hosted proof."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
import test_characterization as legacy
import test_refactor_policy as owner

from supportability_gate import characterization, git_changes, refactor_policy, standard_results

API = "src/introduced.py::function:calculate"
SOURCE = b"def calculate(value):\n    return value * 2\n"
CASES = [
    {
        "input": [{"name": "value", "value": {"type": "int", "value": 1}}],
        "output": {"type": "int", "value": 2},
    },
    {
        "input": [{"name": "value", "value": {"type": "int", "value": 2}}],
        "output": {"type": "int", "value": 4},
    },
]


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json(path: Path, value: object) -> None:
    legacy._write(path, json.dumps(value, sort_keys=True) + "\n")


def _fingerprint(capture: dict[str, Any]) -> None:
    capture["behavior_fingerprint"] = characterization._sha256(
        characterization._canonical(
            [[item["id"], item["behavior_sha256"]] for item in capture["scenarios"]]
        )
    )


def _set_behavior(row: dict[str, Any], behavior: object) -> None:
    row["behavior"] = behavior
    row["behavior_sha256"] = _sha(characterization._canonical(behavior))
    if isinstance(row.get("api_observation"), dict) and "cases" in row["api_observation"]:
        row["api_observation"]["cases"] = behavior


def _review(repository: Path) -> None:
    root = repository / "tests/characterization"
    _json(
        root / "introduced.review.json",
        {
            "schema_version": "1.0",
            "api": API,
            "source_sha256": _sha((repository / "src/introduced.py").read_bytes()),
            "driver_sha256": _sha((root / "introduced.characterization.py").read_bytes()),
            "oracle_sha256": _sha((root / "introduced.golden.json").read_bytes()),
            "intended_feature": "Double an explicit integer input.",
            "reviewer": "Independent synthetic unit fixture reviewer",
            "verdict": "ACCEPTED",
        },
    )


def _fixture(
    tmp_path: Path, *, copied: bool = False, present: bool = False
) -> tuple[Path, str, str, dict[str, Any], dict[str, Any]]:
    repository, baseline, _, _ = legacy._repository(tmp_path)
    retained = SOURCE if copied else b"def retained(value):\n    return value + 1\n"
    legacy._write(repository / "src/sample.py", retained.decode())
    _json(repository / "tests/characterization/existing.golden.json", {"source": retained.decode()})
    base_sha = legacy._commit(repository, "synthetic retained baseline")
    legacy._git(baseline, "checkout", "--detach", base_sha)
    templates = legacy._captures(repository, baseline, base_sha, base_sha)
    scenarios = [
        {"id": "existing", "kind": "golden", "covers": ["src/sample.py"], "api": None},
        {"id": "introduced", "kind": "golden", "covers": ["src/introduced.py"], "api": API},
    ]
    obligations = [
        {
            "id": "existing",
            "category": "static",
            "scenario": "existing",
            "selector": "$",
            "target": "scenario:existing",
        },
        {
            "id": "introduced",
            "category": "behavior",
            "scenario": "introduced",
            "selector": "$",
            "target": API,
        },
    ]
    _json(
        repository / characterization.MANIFEST_PATH,
        {
            "schema_version": "3.0",
            "scenarios": scenarios,
            "obligations": obligations,
            "transitions": [],
        },
    )
    legacy._write(repository / "src/introduced.py", SOURCE.decode())
    legacy._write(
        repository / "tests/characterization/introduced.characterization.py",
        "from introduced import calculate\ncalculate(1)\ncalculate(2)\n",
    )
    _json(repository / "tests/characterization/introduced.golden.json", CASES)
    _review(repository)
    head_sha = legacy._commit(repository, "synthetic API introduction")
    if present:
        base_sha = head_sha
        legacy._write(repository / "docs/post-merge.md", "Synthetic compatible follow-up.\n")
        head_sha = legacy._commit(repository, "synthetic post-merge follow-up")
    records: list[git_changes.CommandRecord] = []
    manifest = characterization._manifest(repository, head_sha, records)
    scenario = next(item for item in manifest.scenarios if item.api)
    driver_path, golden_path = characterization._scenario_paths(scenario, "python")
    driver = git_changes.read_regular_blob(repository, head_sha, driver_path, records)
    golden = git_changes.read_regular_blob(repository, head_sha, golden_path, records)
    observation = {
        "schema_version": "1.0",
        "codec": "python-values-v1",
        "api": API,
        **characterization.api_source_identity(SOURCE, API),
        "start_line": 1,
        "end_line": 2,
        "cases": copy.deepcopy(CASES),
    }
    observed = {
        "behavior": copy.deepcopy(CASES),
        "behavior_sha256": _sha(characterization._canonical(CASES)),
        "command": characterization.scenario_command(scenario, "python"),
        "covers": list(scenario.covers),
        "deterministic": True,
        "driver_blob_sha": driver.object_sha,
        "error": None,
        "exit_code": 0,
        "golden_behavior_sha256": _sha(characterization._canonical(CASES)),
        "golden_blob_sha": golden.object_sha,
        "id": scenario.id,
        "kind": scenario.kind,
        "stderr_sha256": _sha(b""),
        "stdout_sha256": _sha(characterization._canonical(observation)),
        "api_observation": observation,
    }
    absent = legacy.hosted_characterization._absent_api_capture(scenario, driver, golden, CASES)
    captures = []
    for side, template in zip(("base", "head"), templates, strict=True):
        capture = copy.deepcopy(template)
        capture["authentication"].update(base_sha=base_sha, head_sha=head_sha)
        capture["definition_sha"] = head_sha
        capture["target_sha"] = base_sha if side == "base" else head_sha
        capture["manifest"] = characterization._manifest_payload(manifest)
        capture["schema_version"] = characterization.OBSERVED_CAPTURE_SCHEMA
        capture["scenarios"].append(
            copy.deepcopy(absent if side == "base" and not present else observed)
        )
        _fingerprint(capture)
        captures.append(capture)
    return repository, base_sha, head_sha, captures[0], captures[1]


def _verify(
    tmp_path: Path, fixture: tuple[Path, str, str, dict[str, Any], dict[str, Any]]
) -> dict[str, Any]:
    repository, base_sha, head_sha, base, head = fixture
    paths = legacy._write_artifacts(tmp_path, base, head)
    return legacy._verify(repository, base_sha, head_sha, *paths)


def _introduced(capture: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in capture["scenarios"] if item["id"] == "introduced")


def _grant(result: dict[str, Any]) -> dict[str, Any]:
    fact = result["api_observations"][0]
    return {
        "api": fact["api"],
        "scenario": fact["scenario"],
        "source_sha256": fact["head_source"]["source_sha256"],
        "driver_sha256": fact["driver_sha256"],
        "oracle_sha256": fact["oracle_sha256"],
        "review_sha256": fact["review_sha256"],
        "intended_feature": fact["intended_feature"],
        "independent_oracle_reviewed": True,
    }


def _s6(
    fixture: tuple,
    result: dict[str, Any],
    grants: list[dict[str, Any]] | None,
    *,
    trusted: bool = True,
) -> dict[str, Any]:
    repository, base_sha, head_sha, _, _ = fixture
    targets, _ = owner._derived_targets(repository, base_sha, head_sha)
    scope = legacy._git(repository, "diff", "--name-only", base_sha, head_sha).splitlines()
    body = owner._authorization(base_sha, head_sha, scope, list(targets), broad=True)
    if grants is not None:
        payload = json.loads(body.removeprefix(refactor_policy.AUTHORIZATION_PREFIX))
        payload.update(schema_version="3.0", introductions=grants)
        body = refactor_policy.AUTHORIZATION_PREFIX + json.dumps(payload)
    return owner._verify(
        repository,
        owner._event(base_sha, head_sha, body),
        result,
        owner_id=refactor_policy.TRUSTED_OWNER_ID if trusted else 1,
        add_runnability=False,
    )


def test_genuine_whole_file_introduction_passes_synthetic_s5(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "PASS"
    assert result["schema_version"] == characterization.OBSERVED_RESULT_SCHEMA
    assert result["api_observations"][0]["base_absent"] is True
    assert result["api_observations"][0]["head_execution_verified"] is True
    legacy._validate_round_trip(result)


@pytest.mark.parametrize(
    "defect",
    [
        "missing-api-key",
        "unknown-key",
        "mismatched-cover",
        "unbound-obligation",
        "invalid-api",
        "whole-file-target",
        "extra-sibling-obligation",
    ],
)
def test_observed_manifest_is_closed_and_source_bound(defect: str) -> None:
    manifest = {
        "schema_version": "3.0",
        "scenarios": [
            {"id": "introduced", "kind": "golden", "covers": ["src/introduced.py"], "api": API}
        ],
        "obligations": [
            {
                "id": "introduced",
                "category": "behavior",
                "scenario": "introduced",
                "selector": "$",
                "target": API,
            }
        ],
        "transitions": [],
    }
    row = manifest["scenarios"][0]
    if defect == "missing-api-key":
        row.pop("api")
    elif defect == "unknown-key":
        row["command"] = "caller-chosen-program"
    elif defect == "mismatched-cover":
        row["covers"] = ["src/unrelated.py"]
    elif defect == "unbound-obligation":
        manifest["obligations"][0].update(category="static", target="scenario:introduced")
    elif defect == "whole-file-target":
        manifest["obligations"][0]["target"] = "src/introduced.py"
    elif defect == "extra-sibling-obligation":
        manifest["obligations"].append(
            {
                "id": "introduced-sibling",
                "category": "behavior",
                "scenario": "introduced",
                "selector": "$",
                "target": "src/introduced.py::function:uncalled_sibling",
            }
        )
    else:
        row["api"] = "src/introduced.py::function:<invalid>"
    with pytest.raises(
        characterization.CharacterizationError, match="MALFORMED_CHARACTERIZATION_MANIFEST"
    ):
        characterization.parse_manifest(json.dumps(manifest).encode(), "a" * 40)


def test_post_merge_presence_automatically_restores_compatibility(tmp_path: Path) -> None:
    result = _verify(tmp_path, _fixture(tmp_path, present=True))
    assert result["overall_result"] == "PASS"
    assert result["api_observations"][0]["base_absent"] is False
    legacy._validate_round_trip(result)


def test_copied_legacy_source_cannot_be_introduced(tmp_path: Path) -> None:
    result = _verify(tmp_path, _fixture(tmp_path, copied=True))
    assert result["overall_result"] == "BLOCK"
    assert "INVALID_API_INTRODUCTION:introduced" in result["policy_blocks"]


def _advance_head(fixture: tuple, message: str) -> tuple:
    repository, base_sha, _, base, head = fixture
    head_sha = legacy._commit(repository, message)
    manifest = characterization._manifest(repository, head_sha, [])
    for side, capture in (("base", base), ("head", head)):
        capture["authentication"]["head_sha"] = head_sha
        capture["definition_sha"] = head_sha
        capture["target_sha"] = base_sha if side == "base" else head_sha
        capture["manifest"] = characterization._manifest_payload(manifest)
    return repository, base_sha, head_sha, base, head


def test_renamed_legacy_source_cannot_be_introduced(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, copied=True)
    (fixture[0] / "src/sample.py").unlink()
    fixture = _advance_head(fixture, "synthetic rename of retained source")
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert "INVALID_API_INTRODUCTION:introduced" in result["policy_blocks"]


def test_legacy_scenario_cannot_be_relabelled_as_observed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    path = fixture[0] / characterization.MANIFEST_PATH
    manifest = json.loads(path.read_bytes())
    manifest["scenarios"][0]["api"] = "src/sample.py::function:retained"
    manifest["obligations"][0].update(
        category="behavior", target="src/sample.py::function:retained"
    )
    _json(path, manifest)
    fixture = _advance_head(fixture, "synthetic attempted legacy relabel")
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert "CHANGED_CHARACTERIZATION_DEFINITION:existing" in result["policy_blocks"]


@pytest.mark.parametrize("defect", ["missing", "source", "driver", "oracle", "verdict", "intent"])
def test_review_artifact_must_be_exact_and_accepted(tmp_path: Path, defect: str) -> None:
    fixture = _fixture(tmp_path)
    path = fixture[0] / "tests/characterization/introduced.review.json"
    if defect == "missing":
        path.unlink()
    else:
        review = json.loads(path.read_bytes())
        field = {
            "source": "source_sha256",
            "driver": "driver_sha256",
            "oracle": "oracle_sha256",
            "verdict": "verdict",
            "intent": "intended_feature",
        }[defect]
        review[field] = (
            "0" * 64 if field.endswith("sha256") else ("REJECTED" if defect == "verdict" else "")
        )
        _json(path, review)
    fixture = _advance_head(fixture, "synthetic invalid intended-oracle review")
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert "INVALID_API_ORACLE_REVIEW:introduced" in result["policy_blocks"]


def test_review_duplicate_pin_is_rejected_even_when_last_value_matches(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    path = fixture[0] / "tests/characterization/introduced.review.json"
    accepted = path.read_text(encoding="utf-8")
    duplicate = '{"source_sha256":"' + "0" * 64 + '",' + accepted[1:]
    # Ordinary last-value-wins decoding still sees the expected sealed source.
    assert json.loads(duplicate) == json.loads(accepted)
    legacy._write(path, duplicate)
    fixture = _advance_head(fixture, "synthetic duplicate review pin")
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert "INVALID_API_ORACLE_REVIEW:introduced" in result["policy_blocks"]


def test_absent_producer_never_invokes_driver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    repository, base_sha, head_sha, _, _ = fixture
    baseline = tmp_path / "absent-side"
    legacy._git(repository, "worktree", "add", "--detach", str(baseline), base_sha)
    scenario = next(
        item for item in characterization._manifest(repository, head_sha, []).scenarios if item.api
    )
    monkeypatch.setattr(
        legacy.hosted_characterization,
        "_run_driver",
        lambda *args, **kwargs: pytest.fail("absent API must not execute"),
    )
    row = legacy.hosted_characterization._scenario_capture(
        baseline, repository, head_sha, scenario, "python", []
    )
    assert row["api_observation"] == {"api": API, "state": "ABSENT"}
    assert row["command"] is None


def test_head_producer_executes_exactly_two_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    repository, _, head_sha, _, head = fixture
    scenario = next(
        item for item in characterization._manifest(repository, head_sha, []).scenarios if item.api
    )
    template = _introduced(head)
    calls = []

    def replay(*arguments: Any, **keywords: Any) -> dict[str, Any]:
        calls.append(arguments)
        return {
            key: copy.deepcopy(template[key])
            for key in (
                "behavior",
                "behavior_sha256",
                "command",
                "error",
                "exit_code",
                "stderr_sha256",
                "stdout_sha256",
                "api_observation",
            )
        }

    monkeypatch.setattr(legacy.hosted_characterization, "_run_driver", replay)
    row = legacy.hosted_characterization._scenario_capture(
        repository, repository, head_sha, scenario, "python", []
    )
    assert len(calls) == 2
    assert row["deterministic"] is True
    assert row["api_observation"]["cases"] == CASES


@pytest.mark.parametrize(
    "defect",
    [
        "source",
        "code",
        "api",
        "missing",
        "span",
        "replay",
        "driver",
        "golden",
        "one-case",
        "constant-output",
        "duplicate-input",
    ],
)
def test_invalid_head_observation_is_policy_block(tmp_path: Path, defect: str) -> None:
    fixture = _fixture(tmp_path)
    row = _introduced(fixture[4])
    observation = row["api_observation"]
    if defect == "source":
        observation["source_sha256"] = "0" * 64
    elif defect == "code":
        observation["function_code_sha256"] = "0" * 64
    elif defect == "api":
        observation["api"] = "src/introduced.py::function:forged"
    elif defect == "missing":
        row["api_observation"] = None
    elif defect == "span":
        observation["start_line"], observation["end_line"] = 99, 100
    elif defect == "replay":
        row["deterministic"] = False
    elif defect == "driver":
        row["driver_blob_sha"] = "0" * 40
    elif defect == "golden":
        row["golden_blob_sha"] = "0" * 40
    else:
        cases = copy.deepcopy(CASES[:1] if defect == "one-case" else CASES)
        if defect == "constant-output":
            cases[1]["output"] = cases[0]["output"]
        if defect == "duplicate-input":
            cases[1]["input"] = cases[0]["input"]
        _set_behavior(row, cases)
        _fingerprint(fixture[4])
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"


def test_forged_base_absence_on_existing_target_blocks(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, present=True)
    base = _introduced(fixture[3])
    base["api_observation"] = {"api": API, "state": "ABSENT"}
    base["command"] = None
    _set_behavior(base, {"api_absent": API})
    _fingerprint(fixture[3])
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert "MISSING_API_EXECUTION:introduced" in result["policy_blocks"]


def test_legacy_behavior_drift_remains_blocked(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _set_behavior(fixture[4]["scenarios"][0], {"source": "forged legacy behavior"})
    _fingerprint(fixture[4])
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:existing" in result["policy_blocks"]


def test_legitimate_s5_birth_requires_authentic_exact_s6_grant(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "PASS"
    denied = _s6(fixture, result, None)
    assert denied["overall_result"] == "BLOCK"
    assert "INTRODUCTION_AUTHORIZATION_MISMATCH" in denied["policy_blocks"]
    allowed = _s6(fixture, result, [_grant(result)])
    assert allowed["overall_result"] == "PASS"
    assert _s6(fixture, result, [_grant(result)], trusted=False)["overall_result"] == "BLOCK"
    no_owner = owner._verify(
        fixture[0], owner._event(fixture[1], fixture[2], None), result, add_runnability=False
    )
    assert no_owner["overall_result"] == "BLOCK"
    assert "MISSING_OWNER_AUTHORIZATION" in no_owner["policy_blocks"]


@pytest.mark.parametrize(
    "field",
    ["api", "source_sha256", "driver_sha256", "oracle_sha256", "review_sha256", "intended_feature"],
)
def test_s6_grants_join_every_exact_birth_pin(tmp_path: Path, field: str) -> None:
    fixture = _fixture(tmp_path)
    result = _verify(tmp_path, fixture)
    grant = _grant(result)
    grant[field] = "0" * 64 if field.endswith("sha256") else str(grant[field]) + "-forged"
    denied = _s6(fixture, result, [grant])
    assert denied["overall_result"] == "BLOCK"
    assert any(
        block.startswith("INTRODUCTION_AUTHORIZATION_MISMATCH") for block in denied["policy_blocks"]
    )


def test_shared_consumer_rederives_introduction_authorization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _verify(tmp_path, fixture)
    denied = _s6(fixture, result, None)
    identity = standard_results.RunIdentity(
        "example/fixture", 123, fixture[1], fixture[2], "f" * 40, 456, 1
    )
    assert standard_results._s02_refactor(denied, result, identity, None) == denied["policy_blocks"]
    allowed = _s6(fixture, result, [_grant(result)])
    assert standard_results._s02_refactor(allowed, result, identity, None) == []
    standard_results._s02_introduction_binding(denied, denied["authorization"], result)
    tampered = copy.deepcopy(denied)
    tampered["policy_blocks"] = []
    tampered["overall_result"] = "PASS"
    with pytest.raises(
        standard_results.StandardResultsError, match="REFACTOR_RESULT_BINDING_MISMATCH"
    ):
        standard_results._s02_introduction_binding(tampered, tampered["authorization"], result)
    with pytest.raises(
        standard_results.StandardResultsError, match="REFACTOR_RESULT_BINDING_MISMATCH"
    ):
        standard_results._s02_refactor(tampered, result, identity, None)


def test_shared_result_cannot_credit_observed_api_to_whole_file(tmp_path: Path) -> None:
    result = _verify(tmp_path, _fixture(tmp_path))
    assert result["overall_result"] == "PASS"
    obligation = next(item for item in result["obligations"] if item["scenario"] == "introduced")
    obligation["target"] = "src/introduced.py"
    result["behavior_fingerprint"] = _sha(
        characterization._canonical(
            {
                "obligations": [
                    [item["id"], item["head_assertion_sha256"]] for item in result["obligations"]
                ],
                "scenarios": [
                    [item["id"], item["head_behavior_sha256"]] for item in result["scenarios"]
                ],
                "api_observations": result["api_observations"],
            }
        )
    )
    with pytest.raises(
        characterization.CharacterizationError, match="MALFORMED_CHARACTERIZATION_RESULT"
    ):
        legacy._validate_round_trip(result)


def test_stale_source_bytes_cannot_reuse_bound_observation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    legacy._write(fixture[0] / "src/introduced.py", SOURCE.decode().replace("* 2", "* 3"))
    fixture = _advance_head(fixture, "synthetic stale executed-source capture")
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert "MISSING_API_EXECUTION:introduced" in result["policy_blocks"]


def test_observed_driver_and_oracle_are_immutable_after_merge(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, present=True)
    repository = fixture[0]
    driver = repository / "tests/characterization/introduced.characterization.py"
    legacy._write(driver, driver.read_text(encoding="utf-8") + "# substituted driver\n")
    _json(
        repository / "tests/characterization/introduced.golden.json", [{"input": 1, "output": 999}]
    )
    fixture = _advance_head(fixture, "synthetic substituted legacy oracle and driver")
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert "CHANGED_CHARACTERIZATION_DEFINITION:introduced" in result["policy_blocks"]


@pytest.mark.skipif(
    sys.platform != "linux"
    or os.environ.get("GITHUB_ACTIONS") != "true"
    or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted",
    reason="Actual fixed-container observer proof requires native Linux GitHub-hosted Actions.",
)
def test_native_hosted_container_observer_birth_and_rejection_controls(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real container calls joined to unit wrappers, never an adoption attestation."""
    hosted = legacy.hosted_characterization
    hosted._require_hosted_runner()
    image_id = hosted._prepare_container()
    fixture = _fixture(tmp_path)
    repository, base_sha, head_sha, base, head = fixture
    baseline = tmp_path / "actual-absent-side"
    legacy._git(repository, "worktree", "add", "--detach", str(baseline), base_sha)
    scenario = next(
        item for item in characterization._manifest(repository, head_sha, []).scenarios if item.api
    )
    actual_base = hosted._scenario_capture(baseline, repository, head_sha, scenario, "python", [])
    actual_head = hosted._scenario_capture(repository, repository, head_sha, scenario, "python", [])
    assert actual_base["command"] is None
    assert actual_base["api_observation"] == {"api": API, "state": "ABSENT"}
    assert actual_head["exit_code"] == 0 and actual_head["error"] is None
    assert actual_head["deterministic"] is True
    assert actual_head["behavior"] == CASES
    observation = actual_head["api_observation"]
    assert observation["api"] == API
    for key, value in characterization.api_source_identity(SOURCE, API).items():
        assert observation[key] == value
    assert actual_head["command"] == characterization.scenario_command(scenario, "python")
    for capture, actual_row in ((base, actual_base), (head, actual_head)):
        capture["scenarios"] = [
            actual_row if row["id"] == scenario.id else row for row in capture["scenarios"]
        ]
        _fingerprint(capture)
    result = _verify(tmp_path, fixture)
    assert result["overall_result"] == "PASS"
    legacy._validate_round_trip(result)
    authorized_unit_result = _s6(fixture, result, [_grant(result)])
    assert authorized_unit_result["overall_result"] == "PASS"
    identity = standard_results.RunIdentity(
        "example/fixture", 123, base_sha, head_sha, "f" * 40, 456, 1
    )
    assert standard_results._s02_refactor(authorized_unit_result, result, identity, None) == []
    rejected = []
    for defect in ("missing", "tampered"):
        negative = copy.deepcopy(fixture)
        row = _introduced(negative[4])
        if defect == "missing":
            row["api_observation"] = None
        else:
            row["api_observation"]["source_sha256"] = "0" * 64
        blocked = _verify(tmp_path, negative)
        assert blocked["overall_result"] == "BLOCK"
        assert "MISSING_API_EXECUTION:introduced" in blocked["policy_blocks"]
        rejected.append(defect)
    receipt = {
        "schema_version": "native-hosted-observer-canary.v1",
        "native_revision": os.environ.get("GITHUB_SHA"),
        "native_run_id": os.environ.get("GITHUB_RUN_ID"),
        "container_image": hosted.quality_runner.CONTAINER_IMAGE,
        "container_id": image_id,
        "api": API,
        "source_sha256": observation["source_sha256"],
        "function_code_sha256": observation["function_code_sha256"],
        "source_span": [observation["start_line"], observation["end_line"]],
        "cases_sha256": actual_head["behavior_sha256"],
        "observer_envelope_sha256": _sha(characterization._canonical(observation)),
        "stdout_sha256": actual_head["stdout_sha256"],
        "command": actual_head["command"],
        "two_replays_equal": actual_head["deterministic"],
        "rejected_actual_row_mutations": rejected,
        "verification_authentication": "synthetic unit wrapper; not authenticated adoption",
        "owner_authorization": "synthetic unit grant; not an owner attestation",
    }
    with capsys.disabled():
        print("NATIVE_HOSTED_OBSERVER_RECEIPT " + json.dumps(receipt, sort_keys=True))
