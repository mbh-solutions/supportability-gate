"""Native fixed-container qualification of a synthetic 64-row mixed endpoint."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import test_characterization as legacy

from supportability_gate import characterization, git_changes

# Read native state at collection, before any fixture can set environment values.
_NATIVE_HOSTED = (
    sys.platform == "linux"
    and os.environ.get("GITHUB_ACTIONS") == "true"
    and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
)


def _capacity_json(path: Path, value: object) -> None:
    legacy._write(path, json.dumps(value, sort_keys=True) + "\n")


def _capacity_sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _capacity_manifest(
    repository: Path,
    scenarios: list[dict[str, Any]],
    obligations: list[dict[str, Any]],
    version: str,
) -> None:
    _capacity_json(
        repository / characterization.MANIFEST_PATH,
        {
            "schema_version": version,
            "scenarios": sorted(scenarios, key=lambda row: row["id"]),
            "obligations": sorted(obligations, key=lambda row: row["id"]),
            "transitions": [],
        },
    )


def _capacity_fixture(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repository, baseline, _, _ = legacy._repository(tmp_path)
    root = repository / "tests/characterization"
    source = "\n".join(
        f"def retained_{index}(value):\n    return value + {index + 1}\n" for index in range(33)
    )
    legacy._write(repository / "src/sample.py", source)
    scenarios: list[dict[str, Any]] = []
    obligations: list[dict[str, Any]] = []
    for index in range(33):
        identifier = f"retained-{index:02}"
        scenarios.append({"id": identifier, "kind": "golden", "covers": ["src/sample.py"]})
        obligations.append(
            {
                "id": identifier,
                "category": "behavior",
                "scenario": identifier,
                "selector": "$",
                "target": "src/sample.py",
            }
        )
        legacy._write(
            root / f"{identifier}.characterization.py",
            "import json\n"
            f"from sample import retained_{index}\n"
            f"cases = [{{'input': value, 'output': retained_{index}(value)}} for value in (1, 2)]\n"
            f"print(json.dumps({{'schema_version': '1.0', 'scenario': '{identifier}', "
            "'behavior': cases}, sort_keys=True))\n",
        )
        _capacity_json(
            root / f"{identifier}.golden.json",
            [{"input": value, "output": value + index + 1} for value in (1, 2)],
        )
    _capacity_manifest(repository, scenarios, obligations, "2.0")
    base_sha = legacy._commit(repository, "native capacity retained fixture baseline")
    legacy._git(baseline, "checkout", "--detach", base_sha)
    for scenario in scenarios:
        scenario["api"] = None
    observed_source = "\n".join(
        f"def observed_{index}(value):\n    return value * {index + 2}\n" for index in range(31)
    )
    source_path = repository / "src/introduced.py"
    legacy._write(source_path, observed_source)
    for index in range(31):
        identifier = f"observed-{index:02}"
        api = f"src/introduced.py::function:observed_{index}"
        scenarios.append(
            {"id": identifier, "kind": "golden", "covers": ["src/introduced.py"], "api": api}
        )
        obligations.append(
            {
                "id": identifier,
                "category": "behavior",
                "scenario": identifier,
                "selector": "$",
                "target": api,
            }
        )
        values = (1, 2, 3) if index < 19 else (1, 2)
        driver_path = root / f"{identifier}.characterization.py"
        golden_path = root / f"{identifier}.golden.json"
        legacy._write(
            driver_path,
            f"from introduced import observed_{index}\n"
            + "".join(f"observed_{index}({value})\n" for value in values),
        )
        _capacity_json(
            golden_path,
            [
                {
                    "input": [{"name": "value", "value": {"type": "int", "value": value}}],
                    "output": {"type": "int", "value": value * (index + 2)},
                }
                for value in values
            ],
        )
        _capacity_json(
            root / f"{identifier}.review.json",
            {
                "schema_version": "1.0",
                "api": api,
                "source_sha256": _capacity_sha(source_path.read_bytes()),
                "driver_sha256": _capacity_sha(driver_path.read_bytes()),
                "oracle_sha256": _capacity_sha(golden_path.read_bytes()),
                "intended_feature": f"Multiply the supplied integer by {index + 2}.",
                "reviewer": "Declared synthetic capacity fixture oracle; not owner authorization",
                "verdict": "ACCEPTED",
            },
        )
    _capacity_manifest(repository, scenarios, obligations, "3.0")
    head_sha = legacy._commit(repository, "native capacity 64-row mixed fixture head")
    return repository, baseline, base_sha, head_sha


@pytest.mark.skipif(
    not _NATIVE_HOSTED,
    reason="Actual fixed-container capacity proof requires native Linux GitHub-hosted Actions.",
)
def test_native_hosted_exact_64_mixed_capacity_endpoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Actual native producer; unit artifact wrappers are explicitly synthetic."""
    hosted = legacy.hosted_characterization
    hosted._require_hosted_runner()
    repository, baseline, base_sha, head_sha = _capacity_fixture(tmp_path)
    manifest = characterization._manifest(repository, head_sha, [])
    assert len(manifest.scenarios) == 64
    assert len({row.api for row in manifest.scenarios if row.api is not None}) == 31
    receipt: dict[str, Any] = {
        "schema_version": "native-mixed-capacity-endpoint.v1",
        "native_revision": os.environ["GITHUB_SHA"],
        "native_run_id": os.environ["GITHUB_RUN_ID"],
        "native_run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
        "fixture_base_sha": base_sha,
        "fixture_head_sha": head_sha,
        "total_scenarios": 64,
        "retained_scenarios": 33,
        "distinct_observed_apis": 31,
        "expected_observed_cases": 81,
        "container_image": hosted.quality_runner.CONTAINER_IMAGE,
        "source": "Synthetic immutable temporary Git fixture; no consumer source execution",
        "verification_authentication": "Synthetic unit artifact IDs/digests; not adoption proof",
        "owner_authorization": "No owner grant or handoff credit",
        "sides": {},
    }
    paths: dict[str, Path] = {}
    for side, target in (("base", baseline), ("head", repository)):
        started = time.monotonic()
        capture, environment = hosted.capture_evidence(
            target,
            repository,
            base_sha=base_sha,
            head_sha=head_sha,
            side=side,
            repository="example/fixture",
            repository_id="123",
            workflow_sha="f" * 40,
            run_id="456",
            run_attempt="1",
            job=f"characterize-{side}",
            diagnostics=tmp_path / side,
        )
        elapsed = time.monotonic() - started
        assert elapsed < 900
        assert len(capture["scenarios"]) == 64
        observed_cases = 0
        actual_rows = []
        for scenario, row in zip(manifest.scenarios, capture["scenarios"], strict=True):
            assert row["id"] == scenario.id
            assert row["deterministic"] is True
            assert row["exit_code"] == 0 and row["error"] is None
            if scenario.api is not None and side == "base":
                assert row["command"] is None
                assert row["api_observation"] == {"api": scenario.api, "state": "ABSENT"}
                continue
            golden = git_changes.read_regular_blob(
                repository, head_sha, f"tests/characterization/{scenario.id}.golden.json", []
            )
            assert row["behavior"] == json.loads(golden.content)
            assert row["command"] == characterization.scenario_command(scenario, "python")
            if scenario.api is not None:
                source = git_changes.read_regular_blob(
                    repository, head_sha, "src/introduced.py", []
                )
                observation = row["api_observation"]
                for key, value in characterization.api_source_identity(
                    source.content, scenario.api
                ).items():
                    assert observation[key] == value
                observed_cases += len(observation["cases"])
            actual_rows.append(
                {"id": row["id"], "command": row["command"], "stdout_sha256": row["stdout_sha256"]}
            )
        assert observed_cases == (81 if side == "head" else 0)
        path = tmp_path / f"{side}.json"
        characterization._write_json(path, capture, compact=True)
        content = path.read_bytes()
        assert len(content) <= characterization.MAX_JSON_BYTES
        assert content == characterization._canonical(capture) + b"\n"
        provenance = {
            "authentication": capture["authentication"],
            "capture_sha256": _capacity_sha(content),
            "environment": environment,
            "schema_version": characterization.PROVENANCE_SCHEMA,
        }
        characterization._write_json(characterization._provenance_path(path), provenance)
        assert characterization._read_json_bytes(content, "BAD_NATIVE_CAPTURE") == capture
        receipt["sides"][side] = {
            "elapsed_seconds": elapsed,
            "capture_bytes": len(content),
            "capture_sha256": _capacity_sha(content),
            "provenance_sha256": _capacity_sha(
                characterization._provenance_path(path).read_bytes()
            ),
            "environment": environment,
            "actual_executed_rows": actual_rows,
            "expected_docker_driver_executions": 128 if side == "head" else 66,
        }
        paths[side] = path
    result = legacy._verify(repository, base_sha, head_sha, paths["base"], paths["head"])
    assert result["overall_result"] == "PASS"
    assert len(result["api_observations"]) == 31
    legacy._validate_round_trip(result)
    receipt["s5_result"] = result["overall_result"]
    receipt["result_sha256"] = _capacity_sha(characterization._canonical(result))
    receipt["shared_result_round_trip"] = "PASS"
    receipt["not_ready_hand_off"] = True
    with capsys.disabled():
        print("NATIVE_MIXED_CAPACITY_ENDPOINT_RECEIPT " + json.dumps(receipt, sort_keys=True))
