"""Synthetic observed module birth through actual Gate 5/6 integration paths."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import test_characterization as legacy
import test_introduced_characterization as birth
import test_module_observation as witness

from supportability_gate import characterization as gate
from supportability_gate import characterization as module_observation
from supportability_gate import git_changes, refactor_policy, standard_results

RUN = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
sys.path.insert(0, str(Path(sys.argv[3]) / 'src'))
import module_characterization_observer as collector
roots = json.loads(sys.argv[6]) if len(sys.argv) > 6 else None
result = collector.observe_module(Path(sys.argv[3]), sys.argv[5], Path(sys.argv[4]), roots)
print(json.dumps(result, sort_keys=True))
"""


def _actual_witness(
    repository: Path, driver: Path, roots: list[str] | None = None
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            RUN,
            str(Path(__file__).parent),
            str(Path(__file__).parents[1] / "src"),
            str(repository),
            str(driver),
            birth.API,
            *([json.dumps(roots)] if roots is not None else []),
        ],
        check=True,
        capture_output=True,
        timeout=15,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    return json.loads(completed.stdout)


def _fixture(
    tmp_path: Path,
    *,
    copied: bool = False,
    retired: bool = False,
    typed: bool = False,
    payload_size: int = 0,
    root_count: int = 0,
) -> tuple:
    repository, base_sha, _, base, head = birth._fixture(tmp_path, copied=copied)
    source = birth.SOURCE if copied else witness.SOURCE
    oracle = witness.ROOT_CASES[:2] if copied else witness.ROOT_CASES
    primary_cases = witness.CASES
    roots = [birth.API]
    if typed:
        source = witness.TYPED_SOURCE
        primary_cases, oracle = witness.typed_oracles("src/introduced.py", "introduced")
        roots.append("src/introduced.py::function:plain")
    if payload_size:
        source = b"def identity(value):\n    return value\n\ndef calculate(value):\n    if not value:\n        raise ValueError('empty')\n    return identity(value)\n"
        primary_cases, oracle = _large_oracles(payload_size)
    if root_count:
        source = b"def calculate(value):\n    return value * 2\n"
        oracle = copy.deepcopy(witness.ROOT_CASES[:2])
        for index in range(1, root_count):
            name = f"root_{index:03}"
            source += f"\ndef {name}(value):\n    return value + {index}\n".encode()
            roots.append(f"src/introduced.py::function:{name}")
            args = [{"name": "value", "value": {"type": "int", "value": 1}}]
            oracle.append(
                {
                    "root": name,
                    "input": args,
                    "arguments_after": args,
                    "outcome": "RETURN",
                    "output": {"type": "int", "value": index + 1},
                    "exception": None,
                }
            )
    legacy._write(repository / "src/introduced.py", source.decode())
    driver = repository / "tests/characterization/introduced.characterization.py"
    legacy._write(
        driver,
        "from introduced import calculate\ncalculate(1)\ncalculate(2)\n"
        + ("" if copied else "try:\n    calculate(-1)\nexcept ValueError:\n    pass\n"),
    )
    if typed:
        legacy._write(driver, "import introduced as target\n" + witness.TYPED_DRIVER)
        birth._json(repository / "tests/characterization/introduced.golden.json", primary_cases)
    if payload_size:
        legacy._write(
            driver,
            f"from introduced import calculate\ncalculate('a' * {payload_size})\ncalculate('b' * {payload_size + 1})\n",
        )
        birth._json(repository / "tests/characterization/introduced.golden.json", primary_cases)
    if root_count:
        legacy._write(
            driver,
            "import introduced as target\ntarget.calculate(1)\ntarget.calculate(2)\n"
            + "".join(f"target.root_{index:03}(1)\n" for index in range(1, root_count)),
        )
    if retired:
        legacy._write(
            repository / "src/sample.py", "def replacement(value):\n    return value + 1\n"
        )
    manifest = json.loads((repository / gate.MANIFEST_PATH).read_text())
    manifest["schema_version"] = "4.0"
    for scenario in manifest["scenarios"]:
        scenario["module_roots"] = roots if scenario["api"] else []
    manifest["obligations"].append(
        {
            "id": "module-introduced",
            "category": "behavior",
            "scenario": "introduced",
            "selector": "$",
            "target": "src/introduced.py",
        }
    )
    birth._json(repository / gate.MANIFEST_PATH, manifest)
    oracle_path = repository / "tests/characterization/introduced.module.golden.json"
    birth._json(oracle_path, oracle)
    birth._review(repository)
    review_path = repository / "tests/characterization/introduced.review.json"
    review = json.loads(review_path.read_text())
    inventory = module_observation.module_source_inventory(source, "src/introduced.py")
    review.update(
        schema_version="2.0",
        module_roots=roots,
        module_inventory_sha256=inventory["inventory_sha256"],
        module_oracle_sha256=birth._sha(oracle_path.read_bytes()),
    )
    birth._json(review_path, review)
    head_sha = legacy._commit(repository, "synthetic module observation introduction")
    definition = gate._manifest(repository, head_sha, [])
    scenario = next(row for row in definition.scenarios if row.api)
    actual = _actual_witness(repository, driver, roots)
    assert actual["primary"]["cases"] == primary_cases
    assert actual["root_cases"] == oracle
    for side, capture in (("base", base), ("head", head)):
        capture["authentication"].update(base_sha=base_sha, head_sha=head_sha)
        capture["definition_sha"] = head_sha
        capture["target_sha"] = base_sha if side == "base" else head_sha
        capture["manifest"] = gate._manifest_payload(definition)
        capture["schema_version"] = gate.MODULE_CAPTURE_SCHEMA
        row = birth._introduced(capture)
        row["driver_blob_sha"] = git_changes.read_regular_blob(
            repository, head_sha, "tests/characterization/introduced.characterization.py", []
        ).object_sha
        row["golden_blob_sha"] = git_changes.read_regular_blob(
            repository, head_sha, "tests/characterization/introduced.golden.json", []
        ).object_sha
        row["golden_behavior_sha256"] = birth._sha(gate._canonical(primary_cases))
        if side == "head":
            row.update(
                api_observation={**actual["primary"], "module_witness": actual},
                behavior=primary_cases,
                behavior_sha256=birth._sha(gate._canonical(primary_cases)),
                command=gate.scenario_command(scenario, "python"),
                stdout_sha256=birth._sha(gate._canonical(actual)),
            )
        birth._fingerprint(capture)
    return repository, base_sha, head_sha, base, head


def _large_oracles(size: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Literal expectations independent of target calls and collector output."""
    cases = []
    roots = []
    for value in ("a" * size, "b" * (size + 1)):
        encoded = {"type": "str", "value": value}
        arguments = [{"name": "value", "value": encoded}]
        cases.append({"input": arguments, "output": encoded})
        roots.append(
            {
                "root": "calculate",
                "input": arguments,
                "arguments_after": arguments,
                "outcome": "RETURN",
                "output": encoded,
                "exception": None,
            }
        )
    return cases, roots


def test_actual_large_module_receipt_keeps_individual_limits_and_all_joins(tmp_path: Path) -> None:
    nested = tmp_path / "large-unit-fixture"
    nested.mkdir()
    fixture = _fixture(nested, payload_size=45000)
    actual = birth._introduced(fixture[4])["api_observation"]["module_witness"]
    assert 262144 < len(gate._canonical(actual)) < gate.MODULE_MAX_JSON_BYTES
    assert max(len(gate._canonical(case["output"])) for case in actual["root_cases"]) < 262144
    assert any(row["missing_lines"] for row in actual["body_coverage"])
    result = birth._verify(tmp_path, fixture)
    assert result["overall_result"] == "PASS", result["policy_blocks"]
    legacy._validate_round_trip(result)
    assert birth._s6(fixture, result, [_grant(result)])["overall_result"] == "PASS"


def test_100_actual_roots_require_versioned_authorization_and_standard_join(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, root_count=100)
    receipt = birth._introduced(fixture[4])["api_observation"]["module_witness"]
    assert len(receipt["roots"]) == 100
    assert len({row["root"] for row in receipt["executions"]}) == 100
    result = birth._verify(tmp_path, fixture)
    assert result["overall_result"] == "PASS", result["policy_blocks"]
    legacy._validate_round_trip(result)
    grants = [_grant(result)]
    authorized = birth._s6(fixture, result, grants, authorization_version="4.0")
    assert authorized["overall_result"] == "PASS", authorized["policy_blocks"]
    payload = json.loads(json.dumps(authorized["authorization"]))
    assert payload["schema_version"] == "4.0"
    assert (
        standard_results._s02_refactor_authorization(
            payload, authorized["authorization_comment_id"], "INVALID"
        )
        == payload
    )
    standard_results._s02_introduction_binding(authorized, payload, result)
    assert birth._s6(fixture, result, grants)["overall_result"] == "BLOCK"
    for version in ("3.0", "2.0", "unknown"):
        with pytest.raises(refactor_policy.RefactorPolicyError):
            refactor_policy.parse_introduction_grants(grants, version=version)
    dropped = copy.deepcopy(payload)
    del dropped["schema_version"]
    with pytest.raises(standard_results.StandardResultsError):
        standard_results._s02_refactor_authorization(
            dropped, authorized["authorization_comment_id"], "INVALID"
        )
    overflow = copy.deepcopy(grants)
    overflow[0]["module_roots"].append("src/introduced.py::function:root_100")
    with pytest.raises(refactor_policy.RefactorPolicyError):
        refactor_policy.parse_introduction_grants(overflow, version="4.0")
    stale = copy.deepcopy(grants)
    stale[0]["module_oracle_sha256"] = "0" * 64
    assert refactor_policy.introduction_authorization_blocks(
        result, stale, tuple(authorized["targets"]), authorization_version="4.0"
    )


def test_schema5_module_facts_retain_exact_introduction_authorization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = birth._verify(tmp_path, fixture)
    result["schema_version"] = gate.CORRECTION_RESULT_SCHEMA
    grant = _grant(result)
    targets = tuple(result["refactor_runnability"]["targets"])

    assert (
        refactor_policy.introduction_authorization_blocks(
            result, [grant], targets, authorization_version="5.0"
        )
        == []
    )
    assert refactor_policy.introduction_authorization_blocks(
        result, [], targets, authorization_version="5.0"
    ) == ["INTRODUCTION_AUTHORIZATION_MISMATCH"]
    invalid = copy.deepcopy(result)
    invalid["api_observations"][0]["module"]["head_inventory"]["inventory_sha256"] = "0" * 64
    assert refactor_policy.introduction_authorization_blocks(
        invalid, [grant], targets, authorization_version="5.0"
    ) == ["MALFORMED_INTRODUCTION_AUTHORIZATION"]


def test_typed_module_codec2_actual_calls_pass_capture_result_and_owner_join(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, typed=True)
    result = birth._verify(tmp_path, fixture)
    assert result["overall_result"] == "PASS", result["policy_blocks"]
    legacy._validate_round_trip(result)
    assert birth._s6(fixture, result, [_grant(result)])["overall_result"] == "PASS"
    grant = _grant(result)
    grant["module_inventory_sha256"] = "0" * 64
    assert birth._s6(fixture, result, [grant])["overall_result"] == "BLOCK"


def _grant(result: dict[str, Any]) -> dict[str, Any]:
    grant = birth._grant(result)
    module = result["api_observations"][0]["module"]
    grant.update(
        module_roots=module["roots"],
        module_inventory_sha256=module["head_inventory"]["inventory_sha256"],
        module_oracle_sha256=module["oracle_sha256"],
    )
    return grant


@pytest.mark.skipif(
    sys.platform != "linux"
    or os.environ.get("GITHUB_ACTIONS") != "true"
    or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted",
    reason="Actual schema-4 collector proof requires native Linux GitHub-hosted Actions.",
)
def test_native_hosted_module_collector_and_unaccounted_body_rejections(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Real trusted container route; unit result wrappers are not adoption attestations."""
    hosted = legacy.hosted_characterization
    hosted._require_hosted_runner()
    image_id = hosted._prepare_container()
    fixture = _fixture(tmp_path)
    repository, base_sha, head_sha, base, head = fixture
    baseline = tmp_path / "actual-absent-side"
    legacy._git(repository, "worktree", "add", "--detach", str(baseline), base_sha)
    scenario = next(row for row in gate._manifest(repository, head_sha, []).scenarios if row.api)
    actual_base = hosted._scenario_capture(baseline, repository, head_sha, scenario, "python", [])
    actual_head = hosted._scenario_capture(repository, repository, head_sha, scenario, "python", [])
    assert actual_base["api_observation"] == {"api": birth.API, "state": "ABSENT"}
    assert actual_head["exit_code"] == 0 and actual_head["error"] is None
    assert actual_head["deterministic"] is True
    assert actual_head["behavior"] == witness.CASES
    observation = actual_head["api_observation"]["module_witness"]
    gate.verify_module_observation(
        witness.SOURCE,
        "src/introduced.py",
        birth.API,
        observation,
        witness.CASES,
        witness.ROOT_CASES,
    )
    for capture, row in ((base, actual_base), (head, actual_head)):
        capture["scenarios"] = [
            row if current["id"] == scenario.id else current for current in capture["scenarios"]
        ]
        birth._fingerprint(capture)
    result = birth._verify(tmp_path, fixture)
    assert result["overall_result"] == "PASS", result["policy_blocks"]
    legacy._validate_round_trip(result)
    assert birth._s6(fixture, result, [_grant(result)])["overall_result"] == "PASS"
    rejected = []
    typed_target = tmp_path / "typed-module-codec2"
    legacy._write(typed_target / "src/introduced.py", witness.TYPED_SOURCE.decode())
    typed_scenario = replace(
        scenario, module_roots=(birth.API, "src/introduced.py::function:plain")
    )
    typed = hosted._run_driver(
        typed_target,
        repository,
        typed_scenario,
        "python",
        ("import introduced as target\n" + witness.TYPED_DRIVER).encode(),
    )
    typed_replay = hosted._run_driver(
        typed_target,
        repository,
        typed_scenario,
        "python",
        ("import introduced as target\n" + witness.TYPED_DRIVER).encode(),
    )
    assert typed["exit_code"] == 0 and typed["error"] is None
    assert typed_replay["exit_code"] == 0 and typed_replay["error"] is None
    typed_replays_equal = typed == typed_replay
    assert typed_replays_equal
    typed_observation = typed["api_observation"]["module_witness"]
    typed_cases, typed_roots = witness.typed_oracles("src/introduced.py", "introduced")
    assert typed_observation["primary"]["cases"] == typed_cases
    assert typed_observation["root_cases"] == typed_roots
    assert typed_observation["schema_version"] == "module-witness.v3"
    assert typed_observation["primary"]["codec"] == "python-values-v2"
    assert typed_observation["root_cases"][-1]["exception"] == "builtins.ValueError"
    assert typed_observation["root_cases"][2]["exception"] == "builtins.TypeError"
    gate.verify_module_observation(
        witness.TYPED_SOURCE,
        "src/introduced.py",
        birth.API,
        typed_observation,
        typed_cases,
        typed_roots,
    )
    bad_typed = copy.deepcopy(typed_observation)
    bad_typed["primary"]["codec"] = "python-values-v1"
    assert (
        hosted._observed_behavior(
            gate._canonical(bad_typed), birth.API, typed_scenario.module_roots
        )[1]
        == "MALFORMED_MODULE_OBSERVATION"
    )
    hook_driver = "import introduced as target\nclass Hook:\n def __getattribute__(self, name):\n  raise AssertionError('TARGET_HOOK_INVOKED')\ntarget.calculate(Hook())\n"
    hook = hosted._run_driver(
        typed_target, repository, typed_scenario, "python", hook_driver.encode()
    )
    assert hook["exit_code"] == 2 and hook["behavior"] is None
    expected_hook_stdout = (
        gate._canonical({"schema_version": "1.0", "error": "OBSERVER_UNSUPPORTED_VALUE"}) + b"\n"
    )
    assert hook["stdout_sha256"] == birth._sha(expected_hook_stdout)
    large_directory = tmp_path / "large-native-fixture"
    large_directory.mkdir()
    large_fixture = _fixture(large_directory, payload_size=45000)
    large_repository, _, large_head, _, _ = large_fixture
    large_scenario = next(
        row for row in gate._manifest(large_repository, large_head, []).scenarios if row.api
    )
    large_capture = hosted._scenario_capture(
        large_repository, large_repository, large_head, large_scenario, "python", []
    )
    assert large_capture["exit_code"] == 0 and large_capture["error"] is None
    assert large_capture["deterministic"] is True
    large_witness = large_capture["api_observation"]["module_witness"]
    large_cases, large_roots = _large_oracles(45000)
    large_source = (large_repository / "src/introduced.py").read_bytes()
    gate.verify_module_observation(
        large_source, "src/introduced.py", birth.API, large_witness, large_cases, large_roots
    )
    large_bytes = len(gate._canonical(large_witness)) + 1
    assert 262144 < large_bytes <= gate.MODULE_MAX_JSON_BYTES
    max_value_bytes = max(len(gate._canonical(case["output"])) for case in large_roots)
    assert max_value_bytes <= 262144
    tampered = copy.deepcopy(large_witness)
    tampered["body_coverage"][0]["missing_lines"].append(999)
    with pytest.raises(gate.ModuleObservationError, match="INVALID_MODULE_BODY_METRIC"):
        gate.verify_module_observation(
            large_source, "src/introduced.py", birth.API, tampered, large_cases, large_roots
        )
    for source, error in (
        (
            b"def calculate(value):\n    values = (\n        value * 2\n        for _ in range(1)\n    )\n    return sum(values)\n",
            "UNSUPPORTED_MODULE_GENERATOR_EXPRESSION",
        ),
        (
            b"type Value = int\n\ndef calculate(value):\n    return value * 2\n",
            "UNSUPPORTED_MODULE_COMPILED_FUNCTION",
        ),
        (witness.runtime_source("__file__"), "OBSERVER_UNACCOUNTED_CODE"),
        (witness.runtime_source("'generated.py'"), "OBSERVER_UNACCOUNTED_CODE"),
    ):
        target = tmp_path / error
        legacy._write(target / "src/introduced.py", source.decode())
        row = hosted._run_driver(
            target,
            repository,
            scenario,
            "python",
            b"from introduced import calculate\ncalculate(1)\ncalculate(2)\n",
        )
        assert row["exit_code"] == 2 and row["behavior"] is None
        expected_stdout = gate._canonical({"schema_version": "1.0", "error": error}) + b"\n"
        assert row["stdout_sha256"] == birth._sha(expected_stdout)
        rejected.append({"error": error, "stdout_sha256": row["stdout_sha256"]})
    receipt = {
        "schema_version": "native-hosted-module-witness-canary.v1",
        "native_revision": os.environ.get("GITHUB_SHA"),
        "native_run_id": os.environ.get("GITHUB_RUN_ID"),
        "container_image": hosted.quality_runner.CONTAINER_IMAGE,
        "image_id": image_id,
        "api": birth.API,
        "source_sha256": observation["source_sha256"],
        "inventory_sha256": observation["inventory_sha256"],
        "command": actual_head["command"],
        "two_replays_equal": actual_head["deterministic"],
        "rejected_actual_unsupported_sources": rejected,
        "codec2_actual_command": typed["command"],
        "codec2_two_replays_equal": typed_replays_equal,
        "codec2_source_sha256": typed_observation["source_sha256"],
        "codec2_inventory_sha256": typed_observation["inventory_sha256"],
        "codec2_hook_rejection_stdout_sha256": hook["stdout_sha256"],
        "large_actual_command": large_capture["command"],
        "large_two_replays_equal": large_capture["deterministic"],
        "large_witness_bytes": large_bytes,
        "large_max_individual_value_bytes": max_value_bytes,
        "large_source_sha256": large_witness["source_sha256"],
        "large_inventory_sha256": large_witness["inventory_sha256"],
        "large_witness_sha256": birth._sha(gate._canonical(large_witness)),
        "large_stdout_sha256": large_capture["stdout_sha256"],
        "large_metric_tamper_rejected": True,
        "verification_authentication": "synthetic unit wrapper; not authenticated adoption",
        "owner_authorization": "synthetic unit grant; not an owner attestation",
    }
    with capsys.disabled():
        print("NATIVE_HOSTED_MODULE_WITNESS_RECEIPT " + json.dumps(receipt, sort_keys=True))


def test_real_synthetic_calls_pass_parser_capture_result_coverage_and_owner_join(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = birth._verify(tmp_path, fixture)
    assert result["overall_result"] == "PASS", result["policy_blocks"]
    assert result["schema_version"] == gate.MODULE_RESULT_SCHEMA
    assert "src/introduced.py::function:validate" in result["coverage"]["covered_obligations"]
    legacy._validate_round_trip(result)
    assert birth._s6(fixture, result, [_grant(result)])["overall_result"] == "PASS"


@pytest.mark.parametrize(
    "defect",
    [
        "unwitnessed",
        "unlinked",
        "empty",
        "changed-output",
        "stale-source",
        "stale-inventory",
        "extra-void-output",
        "stale-capture-schema",
    ],
)
def test_bad_module_captures_cannot_gain_file_responsibility_coverage(
    tmp_path: Path, defect: str
) -> None:
    fixture = _fixture(tmp_path)
    row = birth._introduced(fixture[4])
    observation = row["api_observation"]
    module = observation["module_witness"]
    if defect == "unwitnessed":
        module["calls"] = [call for call in module["calls"] if call["function"] != "validate"]
    elif defect == "unlinked":
        module["calls"][0]["execution"] = 127
    elif defect == "empty":
        module["primary"]["cases"] = []
    elif defect == "changed-output":
        module["root_cases"][0]["output"]["value"] = 999
    elif defect == "stale-source":
        module["source_sha256"] = "0" * 64
    elif defect == "stale-inventory":
        module["inventory_sha256"] = "0" * 64
    elif defect == "extra-void-output":
        module["calls"][0]["output"] = True
    else:
        fixture[4]["schema_version"] = gate.OBSERVED_CAPTURE_SCHEMA
        with pytest.raises(gate.CharacterizationError):
            birth._verify(tmp_path, fixture)
        return
    result = birth._verify(tmp_path, fixture)
    assert result["overall_result"] == "BLOCK"
    assert birth._s6(fixture, result, [_grant(result)])["overall_result"] == "BLOCK"


@pytest.mark.parametrize("copied,retired", [(True, False), (False, True)])
def test_current_copy_and_retirement_defenses_apply_unchanged_to_modules(
    tmp_path: Path, copied: bool, retired: bool
) -> None:
    fixture = _fixture(tmp_path, copied=copied, retired=retired)
    result = birth._verify(tmp_path, fixture)
    assert "INVALID_API_INTRODUCTION:introduced" in result["policy_blocks"]
    assert birth._s6(fixture, result, [_grant(result)])["overall_result"] == "BLOCK"


@pytest.mark.parametrize("defect", ["review", "oracle", "roots", "inventory"])
def test_unreviewed_module_source_oracle_and_inventory_fail_closed(
    tmp_path: Path, defect: str
) -> None:
    fixture = _fixture(tmp_path)
    repository = fixture[0]
    path = repository / "tests/characterization/introduced.review.json"
    review = json.loads(path.read_text())
    if defect == "review":
        review["verdict"] = "UNREVIEWED"
    elif defect == "oracle":
        review["module_oracle_sha256"] = "0" * 64
    elif defect == "roots":
        review["module_roots"] = []
    else:
        review["module_inventory_sha256"] = "0" * 64
    birth._json(path, review)
    fixture = birth._advance_head(fixture, "synthetic invalid module review")
    result = birth._verify(tmp_path, fixture)
    assert "INVALID_API_ORACLE_REVIEW:introduced" in result["policy_blocks"]


@pytest.mark.parametrize("defect", ["missing", "untrusted", "roots", "inventory", "oracle"])
def test_module_owner_grant_is_exact_and_cannot_waive_missing_witness(
    tmp_path: Path, defect: str
) -> None:
    fixture = _fixture(tmp_path)
    result = birth._verify(tmp_path, fixture)
    grant = _grant(result)
    if defect == "roots":
        grant["module_roots"] = ["src/introduced.py::function:double", birth.API]
    elif defect == "inventory":
        grant["module_inventory_sha256"] = "0" * 64
    elif defect == "oracle":
        grant["module_oracle_sha256"] = "0" * 64
    authorized = birth._s6(
        fixture, result, [] if defect == "missing" else [grant], trusted=defect != "untrusted"
    )
    assert authorized["overall_result"] == "BLOCK"


@pytest.mark.parametrize("panel_size", [50, 51])
def test_module_panel_entries_count_against_fixed_100_api_cap(panel_size: int) -> None:
    manifest = {"schema_version": "4.0", "scenarios": [], "obligations": [], "transitions": []}
    for index in (0, 1):
        identifier, path = f"panel-{index}", f"src/panel{index}.py"
        roots = sorted(f"{path}::function:root{number}" for number in range(panel_size))
        manifest["scenarios"].append(
            {
                "id": identifier,
                "kind": "regression",
                "covers": [path],
                "api": roots[0],
                "module_roots": roots,
            }
        )
        manifest["obligations"].append(
            {
                "id": identifier,
                "category": "behavior",
                "scenario": identifier,
                "selector": "$",
                "target": roots[0],
            }
        )
    if panel_size == 50:
        assert (
            sum(
                len(row.module_roots)
                for row in gate.parse_manifest(json.dumps(manifest).encode(), "0" * 40).scenarios
            )
            == 100
        )
    else:
        with pytest.raises(gate.CharacterizationError, match="MALFORMED_CHARACTERIZATION_MANIFEST"):
            gate.parse_manifest(json.dumps(manifest).encode(), "0" * 40)


def test_module_result_cannot_be_relabelled_as_old_schema(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = copy.deepcopy(birth._verify(tmp_path, fixture))
    result["schema_version"] = gate.OBSERVED_RESULT_SCHEMA
    with pytest.raises(gate.CharacterizationError):
        legacy._validate_round_trip(result)
