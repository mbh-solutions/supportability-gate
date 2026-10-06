"""Finite evidence capacity and lossless machine-capture transport controls."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import test_characterization as legacy
import test_introduced_characterization as birth
import test_module_introduction as module_birth
import test_module_observation as witness

from supportability_gate import (
    characterization,
    refactor_policy,
    standard_results,
)
from supportability_gate import (
    standard_results_producer as composer,
)


def _identity_records(size: int, parameter: str, extra: int = 0) -> tuple[list, list]:
    cases, roots = module_birth._large_oracles(size)
    for index, value in enumerate(("a" * (size + extra), "b" * (size + 1))):
        arguments = [{"name": parameter, "value": {"type": "str", "value": value}}]
        cases[index] = {"input": arguments, "output": {"type": "str", "value": value}}
        roots[index].update(
            input=arguments, arguments_after=arguments, output=cases[index]["output"]
        )
    return cases, roots


def _git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def _identity_driver(index: int, cases: list[dict[str, Any]]) -> bytes:
    first, second = (len(case["output"]["value"]) for case in cases)
    return (
        f"from panel_{index:03} import calculate\n"
        f"calculate('a' * {first})\ncalculate('b' * {second})\n"
    ).encode()


def _transport_module(
    fact_template: dict[str, Any],
    capture_template: dict[str, Any],
    index: int,
    size: int,
    parameter: str = "value",
    extra: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    """Synthetic transport records; source pins and identity oracles are independently derived."""
    identifier, path = f"panel-{index:03}", f"src/panel_{index:03}.py"
    api = path + "::function:calculate"
    source = (
        "def identity(value):\n    return value\n\n"
        f"def calculate({parameter}):\n    if not {parameter}:\n"
        f"        raise ValueError('empty')\n    return identity({parameter})\n"
    ).encode()
    inventory = characterization.module_source_inventory(source, path)
    identity = characterization.api_source_identity(source, api)
    cases, roots = _identity_records(size, parameter, extra)
    case_digest = characterization._sha256(characterization._canonical(cases))
    driver = _identity_driver(index, cases)
    driver_digest = characterization._sha256(driver)
    golden_digest = characterization._sha256(characterization._canonical(cases) + b"\n")
    module_digest = characterization._sha256(characterization._canonical(roots) + b"\n")
    scenario = characterization.Scenario(identifier, "golden", (path,), api, (api,))
    fact = copy.deepcopy(fact_template)
    fact.update(
        api=api,
        scenario=identifier,
        head_source=identity,
        driver_sha256=driver_digest,
        oracle_sha256=golden_digest,
        head_cases_sha256=case_digest,
        intended_feature="Return the exact nonempty string through the identity helper.",
    )
    fact["module"].update(
        roots=[api], head_inventory=inventory, oracle_cases=roots, oracle_sha256=module_digest
    )
    # This is a source-owned synthetic reader fixture, not an owner review or hosted adoption.
    fact["review_sha256"] = characterization._sha256(
        characterization._canonical(
            {"synthetic_transport": identifier, "module_sha256": module_digest}
        )
    )
    capture = copy.deepcopy(capture_template)
    observation = capture["api_observation"]
    observation.update(**identity, api=api, cases=cases)
    module = observation["module_witness"]
    module.update(**inventory, api=api, path=path, roots=[api], root_cases=roots)
    module["primary"] = {
        key: value for key, value in observation.items() if key != "module_witness"
    }
    characterization.verify_module_observation(source, path, api, module, cases, roots)
    stdout = characterization._canonical(module) + b"\n"
    assert len(stdout) <= characterization.MODULE_MAX_JSON_BYTES
    capture.update(
        id=identifier,
        covers=[path],
        command=characterization.scenario_command(scenario, "python"),
        behavior=cases,
        behavior_sha256=case_digest,
        golden_behavior_sha256=case_digest,
        stdout_sha256=characterization._sha256(stdout),
        driver_blob_sha=_git_blob_sha(driver),
        golden_blob_sha=_git_blob_sha(characterization._canonical(cases) + b"\n"),
    )
    serialized = {
        "id": identifier,
        "kind": "golden",
        "covers": [path],
        "command": capture["command"],
        "base_behavior_sha256": characterization._sha256(
            characterization._canonical({"api_absent": api})
        ),
        "head_behavior_sha256": case_digest,
        "golden_behavior_sha256": case_digest,
        "compatibility": "PASS",
    }
    return fact, capture, serialized, source


def _transport_documents(
    template: dict[str, Any],
    head: dict[str, Any],
    size: int,
    parameter_extra: int = 0,
    value_extra: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, bytes]]:
    """53 distinct source-bound modules, 54 scenarios and 107 obligations; no filler fields."""
    result = copy.deepcopy(template)
    capture = copy.deepcopy(head)
    capture.pop("environment", None)
    result["api_observations"] = []
    result["scenarios"] = [copy.deepcopy(template["scenarios"][0])]
    result["obligations"] = [copy.deepcopy(template["obligations"][0])]
    capture["scenarios"] = [copy.deepcopy(head["scenarios"][0])]
    manifest = {
        "schema_version": "4.0",
        "scenarios": [copy.deepcopy(head["manifest"]["scenarios"][0])],
        "obligations": [copy.deepcopy(head["manifest"]["obligations"][0])],
        "transitions": [],
    }
    sources = {}
    required = []
    targets = []
    for index in range(53):
        fact, row, serialized, source = _transport_module(
            template["api_observations"][0],
            birth._introduced(head),
            index,
            size,
            "value" + "v" * (parameter_extra if index == 0 else 0),
            value_extra if index == 0 else 0,
        )
        api, identifier = fact["api"], fact["scenario"]
        path = api.split("::", 1)[0]
        sources[path] = source
        result["api_observations"].append(fact)
        result["scenarios"].append(serialized)
        capture["scenarios"].append(row)
        manifest["scenarios"].append(
            {
                "id": identifier,
                "kind": "golden",
                "covers": [path],
                "api": api,
                "module_roots": [api],
            }
        )
        for suffix, target in (("primary", api), ("module", path)):
            obligation = {
                "id": f"{identifier}-{suffix}",
                "category": "behavior",
                "scenario": identifier,
                "selector": "$",
                "target": target,
            }
            manifest["obligations"].append(obligation)
            result["obligations"].append(
                {key: value for key, value in obligation.items() if key != "selector"}
                | {
                    "base_assertion_sha256": serialized["base_behavior_sha256"],
                    "head_assertion_sha256": serialized["head_behavior_sha256"],
                    "meaningful": True,
                    "compatibility": "PASS",
                }
            )
        for function in fact["module"]["head_inventory"]["functions"]:
            name = function["name"]
            required.append(f"{path}::function:{name}")
            # Exact source spans agree with the independently derived fixed source.
            first, last = (4, 7) if name == "calculate" else (1, 2)
            targets.append(f"{path}::function:{name}:{first}-{last}")
    for document in (result, manifest):
        document["obligations"].sort(key=lambda row: row["id"])
    paths = sorted(["src/sample.py", *sources])
    result["coverage"] = {
        "required_paths": paths,
        "covered_paths": paths,
        "required_obligations": sorted(required),
        "covered_obligations": sorted(required),
    }
    result["refactor_runnability"]["targets"] = sorted(targets)
    _result_fingerprint(result)
    raw_manifest = characterization._canonical(manifest)
    definition = characterization.parse_manifest(raw_manifest, "a" * 40)
    result.update(manifest_blob_sha=definition.blob_sha, manifest_sha256=definition.sha256)
    capture["manifest"] = characterization._manifest_payload(definition)
    birth._fingerprint(capture)
    return result, capture, manifest, sources


def _result_fingerprint(result: dict[str, Any]) -> None:
    result["behavior_fingerprint"] = characterization._sha256(
        characterization._canonical(
            {
                "obligations": [
                    [row["id"], row["head_assertion_sha256"]] for row in result["obligations"]
                ],
                "scenarios": [
                    [row["id"], row["head_behavior_sha256"]] for row in result["scenarios"]
                ],
                "api_observations": result["api_observations"],
            }
        )
    )


def _exact_transport(
    template: dict[str, Any], head: dict[str, Any], *, capture: bool, overflow: int = 0
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, bytes]]:
    """Tune identity arguments and a real parameter name, with no irrelevant JSON padding."""
    limit = characterization.MODULE_AGGREGATE_JSON_BYTES + overflow
    size = 33000 if capture else 100000
    initial = _transport_documents(template, head, size)
    selected = initial[1 if capture else 0]
    remaining = limit - len(characterization._canonical(selected)) - 1
    assert remaining > 0
    # Every input string is repeated nine times in capture and three times in result.
    # A parameter name occurs ten/four times respectively. These coprime weights
    # allow an exact boundary while each value retains its identity-call meaning.
    value_weight, parameter_weight = (9, 10) if capture else (3, 4)
    parameter_extra = next(
        extra
        for extra in range(value_weight)
        if (remaining - extra * parameter_weight) % value_weight == 0
    )
    value_extra = (remaining - parameter_extra * parameter_weight) // value_weight
    assert size + value_extra < 262000
    documents = _transport_documents(template, head, size, parameter_extra, value_extra)
    assert len(characterization._canonical(documents[1 if capture else 0])) + 1 == limit
    return documents


def _commit_transport_manifest(
    fixture: tuple, documents: tuple[dict, dict, dict, dict]
) -> dict[str, Any]:
    repository, base_sha, _, _, _ = fixture
    result, capture, manifest, sources = documents
    for path, source in sources.items():
        (repository / path).write_bytes(source)
    for index, row in enumerate(capture["scenarios"][1:]):
        driver = _identity_driver(index, row["behavior"])
        driver_path = repository / "tests/characterization" / f"{row['id']}.characterization.py"
        driver_path.write_bytes(driver)
        (driver_path.parent / f"{row['id']}.golden.json").write_bytes(
            characterization._canonical(row["behavior"]) + b"\n"
        )
        (driver_path.parent / f"{row['id']}.module.golden.json").write_bytes(
            characterization._canonical(row["api_observation"]["module_witness"]["root_cases"])
            + b"\n"
        )
    (repository / characterization.MANIFEST_PATH).write_bytes(characterization._canonical(manifest))
    head_sha = legacy._commit(repository, "synthetic finite transport modules; not hosted adoption")
    definition = characterization._manifest(repository, head_sha, [])
    result.update(
        head_sha=head_sha, manifest_blob_sha=definition.blob_sha, manifest_sha256=definition.sha256
    )
    result["refactor_runnability"]["head_sha"] = head_sha
    capture.update(definition_sha=head_sha, target_sha=head_sha)
    capture["authentication"]["head_sha"] = head_sha
    capture["manifest"] = characterization._manifest_payload(definition)
    return {
        "repository": {"full_name": "example/fixture"},
        "pull_request": {"number": 1, "base": {"sha": base_sha}, "head": {"sha": head_sha}},
    }


def _verify_actual_transport_calls(repository: Path, capture: dict[str, Any]) -> None:
    """Real Windows unit calls of owned synthetic sources; never hosted adoption or consumer code."""
    for row in capture["scenarios"][1:]:
        expected = row["api_observation"]["module_witness"]
        driver = repository / "tests/characterization" / f"{row['id']}.characterization.py"
        completed = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                module_birth.RUN,
                str(Path(__file__).parent),
                str(Path(__file__).parents[1] / "src"),
                str(repository),
                str(driver),
                expected["api"],
                json.dumps(expected["roots"]),
            ],
            check=True,
            capture_output=True,
            timeout=15,
            env={**os.environ, "PYTHONHASHSEED": "0"},
        )
        actual = json.loads(completed.stdout)
        assert actual == expected
        # Expectations are literal identity oracles, independently constructed above.
        characterization.verify_module_observation(
            (repository / expected["path"]).read_bytes(),
            expected["path"],
            expected["api"],
            actual,
            row["behavior"],
            expected["root_cases"],
        )


def _standard_transport_endpoints(
    path: Path,
    result: dict[str, Any],
    repository: Path,
    event: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    overflow: bool = False,
) -> None:
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repository.parent))
    unused = path.parent / "other-sources.json"
    unused.write_bytes(b"{}\n")
    arguments = argparse.Namespace(
        head_sha=event["pull_request"]["head"]["sha"],
        **{
            name: str(path if source == "characterization" else unused)
            for source, name, *_ in composer.SOURCE_SPECS
        },
    )
    loaded, errors = composer._load_sources(
        arguments,
        {
            "complexity": "success",
            "characterization": "success",
            "refactor": "success",
            "quality": "success",
        },
    )
    identity = standard_results.RunIdentity(
        result["repository"].removeprefix("github.com/"),
        123,
        result["base_sha"],
        result["head_sha"],
        result["workflow_sha"],
        456,
        1,
    )
    if overflow:
        assert loaded["characterization"] == {}
        assert errors["characterization"] == "MALFORMED_CHARACTERIZATION_RESULT"
        with pytest.raises(
            standard_results.StandardResultsError, match="MALFORMED_CHARACTERIZATION_RESULT"
        ):
            standard_results._s02_characterization(
                result,
                identity,
                tuple(result["coverage"]["required_paths"]),
                None,
                result["artifacts"],
            )
    else:
        assert loaded["characterization"] == result
        assert "characterization" not in errors
        assert (
            standard_results._s02_characterization(
                loaded["characterization"],
                identity,
                tuple(result["coverage"]["required_paths"]),
                None,
                result["artifacts"],
            )
            == []
        )
    composed = standard_results.compose_results(
        loaded["complexity"],
        loaded["characterization"],
        loaded["refactor"],
        loaded["quality_provenance"],
        identity,
        expected_quality_artifact=None,
        expected_characterization_artifacts=result["artifacts"],
        source_errors=errors,
    )
    assert ("MALFORMED_CHARACTERIZATION_RESULT" in json.dumps(composed)) is overflow


@pytest.mark.parametrize("version", ["4.0", "5.0"])
def test_large_module_result_readers_select_exact_manifest_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fixture = module_birth._fixture(workspace, payload_size=10)
    target = workspace / "target"
    fixture[0].rename(target)
    fixture = (target, *fixture[1:])
    template = birth._verify(tmp_path, fixture)
    documents = _transport_documents(template, fixture[4], size=4000)
    result, _, manifest, _ = documents
    manifest["schema_version"] = version
    result["schema_version"] = characterization.result_schema(version)
    if version == "5.0":
        manifest["corrections"] = []
    event = _commit_transport_manifest(fixture, documents)
    _verify_actual_transport_calls(target, documents[1])
    path = tmp_path / "module-result.json"
    raw = characterization._write_json(path, result)
    assert refactor_policy.MAX_JSON_BYTES < len(raw) < characterization.MODULE_AGGREGATE_JSON_BYTES
    assert refactor_policy._read_characterization_result(path, target, event) == (result, raw)
    _standard_transport_endpoints(path, result, target, event, monkeypatch)

    other_schema = characterization.result_schema("4.0" if version == "5.0" else "5.0")
    invalid_payloads = [
        characterization._canonical({**result, "schema_version": other_schema}),
        b'{"schema_version":"duplicate","schema_version":"duplicate"}',
        b" " * (characterization.MODULE_AGGREGATE_JSON_BYTES + 1),
    ]
    for invalid in invalid_payloads:
        path.write_bytes(invalid)
        with pytest.raises(
            refactor_policy.RefactorPolicyError, match="MALFORMED_CHARACTERIZATION_RESULT"
        ):
            refactor_policy._read_characterization_result(path, target, event)
        assert composer._read_characterization(
            path, "MISSING", "MALFORMED", result["head_sha"]
        ) == (
            {},
            "MALFORMED",
        )

    path.write_bytes(raw)
    legacy_manifest = copy.deepcopy(manifest)
    legacy_manifest["schema_version"] = "3.0"
    legacy_manifest.pop("corrections", None)
    for scenario in legacy_manifest["scenarios"]:
        scenario.pop("module_roots")
    (target / characterization.MANIFEST_PATH).write_bytes(
        characterization._canonical(legacy_manifest)
    )
    legacy_head = legacy._commit(target, "legacy manifest cannot authorize module result transport")
    event["pull_request"]["head"]["sha"] = legacy_head
    with pytest.raises(
        refactor_policy.RefactorPolicyError, match="MALFORMED_CHARACTERIZATION_RESULT"
    ):
        refactor_policy._read_characterization_result(path, target, event)
    assert composer._read_characterization(path, "MISSING", "MALFORMED", legacy_head) == (
        {},
        "MALFORMED",
    )


def test_schema4_result_exact_32m_transport_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fixture = module_birth._fixture(workspace, payload_size=10)
    target = workspace / "target"
    fixture[0].rename(target)
    fixture = (target, *fixture[1:])
    template = birth._verify(tmp_path, fixture)
    documents = _exact_transport(template, fixture[4], capture=False)
    result = documents[0]
    event = _commit_transport_manifest(fixture, documents)
    _verify_actual_transport_calls(fixture[0], documents[1])
    legacy._validate_round_trip(result)
    assert len(result["scenarios"]) == 54
    assert len(result["api_observations"]) == 53
    assert len(result["obligations"]) == 107
    path = tmp_path / "result-32m.json"
    raw = characterization._write_json(path, result)
    assert len(raw) == 32000000
    parsed = characterization._read_module_aggregate(
        raw, "OVERSIZED", characterization.MODULE_RESULT_SCHEMA
    )
    legacy._validate_round_trip(parsed)
    assert refactor_policy._read_characterization_result(path, fixture[0], event) == (parsed, raw)
    # Fixed hosted checkout selection, without a new flag, consumer limit, or payload authority.
    _standard_transport_endpoints(path, parsed, fixture[0], event, monkeypatch)
    bad = tmp_path / "duplicate-result.json"
    bad.write_bytes(b'{"schema_version":"characterization-result.v4","schema_version":"x"}')
    assert composer._read_characterization(bad, "MISSING", "MALFORMED", result["head_sha"]) == (
        {},
        "MALFORMED",
    )
    assert composer._read_characterization(
        tmp_path / "absent.json", "MISSING", "MALFORMED", result["head_sha"]
    ) == ({}, "MISSING")
    with pytest.raises(characterization.CharacterizationError, match="OVERSIZED"):
        characterization._read_json_bytes(raw, "OVERSIZED")
    # Coherent identity arguments and the source parameter name tune a complete
    # structured result to exactly one byte above the reader/writer boundary.
    oversized = _exact_transport(template, fixture[4], capture=False, overflow=1)[0]
    oversized_raw = characterization._canonical(oversized) + b"\n"
    assert len(oversized_raw) == 32000001
    with pytest.raises(characterization.CharacterizationError, match="OVERSIZED"):
        characterization._read_module_aggregate(
            oversized_raw, "OVERSIZED", characterization.MODULE_RESULT_SCHEMA
        )
    path.write_bytes(oversized_raw)
    _standard_transport_endpoints(path, oversized, fixture[0], event, monkeypatch, overflow=True)
    with pytest.raises(
        refactor_policy.RefactorPolicyError, match="MALFORMED_CHARACTERIZATION_RESULT"
    ):
        refactor_policy._read_characterization_result(path, fixture[0], event)
    with pytest.raises(
        characterization.CharacterizationError, match="MODULE_AGGREGATE_VALUE_LIMIT"
    ):
        characterization._write_json(tmp_path / "overflow.json", oversized)
    result["schema_version"] = characterization.OBSERVED_RESULT_SCHEMA
    path.write_bytes(characterization._canonical(result) + b"\n")
    with pytest.raises(
        refactor_policy.RefactorPolicyError, match="MALFORMED_CHARACTERIZATION_RESULT"
    ):
        refactor_policy._read_characterization_result(path, fixture[0], event)
    # A result4 self-label cannot enlarge transport selected by the actual HEAD.
    result["schema_version"] = characterization.MODULE_RESULT_SCHEMA
    path.write_bytes(characterization._canonical(result) + b"\n")
    (fixture[0] / characterization.MANIFEST_PATH).write_bytes(_manifest(0, 1))
    legacy_head = legacy._commit(fixture[0], "synthetic legacy manifest transport control")
    assert composer._read_characterization(path, "MISSING", "MALFORMED", legacy_head) == (
        {},
        "MALFORMED",
    )
    event["pull_request"]["head"]["sha"] = legacy_head
    with pytest.raises(
        refactor_policy.RefactorPolicyError, match="MALFORMED_CHARACTERIZATION_RESULT"
    ):
        refactor_policy._read_characterization_result(path, fixture[0], event)


def test_schema4_capture_exact_32m_transport_endpoints(tmp_path: Path) -> None:
    fixture = module_birth._fixture(tmp_path, payload_size=10)
    template = birth._verify(tmp_path, fixture)
    documents = _exact_transport(template, fixture[4], capture=True)
    result, capture, _, _ = documents
    _commit_transport_manifest(fixture, documents)
    _verify_actual_transport_calls(fixture[0], capture)
    legacy._validate_round_trip(result)
    path = tmp_path / "capture-32m.json"
    raw = characterization._write_json(path, capture)
    assert len(raw) == 32000000
    parsed = characterization._read_module_aggregate(
        raw, "OVERSIZED", characterization.MODULE_CAPTURE_SCHEMA
    )
    loaded, error = characterization._load_capture(path, "MISSING", module=True)
    assert error is None and loaded is not None
    assert loaded.pop("_capture_sha256") == characterization._sha256(raw)
    assert loaded == parsed == capture
    assert (
        characterization._load_capture(path, "MISSING")[1]
        == "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"
    )
    oversized = _exact_transport(template, fixture[4], capture=True, overflow=1)[1]
    oversized_raw = characterization._canonical(oversized) + b"\n"
    assert len(oversized_raw) == 32000001
    with pytest.raises(
        characterization.CharacterizationError, match="MODULE_AGGREGATE_VALUE_LIMIT"
    ):
        characterization._write_json(tmp_path / "overflow-capture.json", oversized)
    with pytest.raises(characterization.CharacterizationError, match="OVERSIZED"):
        characterization._read_module_aggregate(
            oversized_raw, "OVERSIZED", characterization.MODULE_CAPTURE_SCHEMA
        )
    path.write_bytes(oversized_raw)
    assert (
        characterization._load_capture(path, "MISSING", module=True)[1]
        == "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"
    )
    capture["schema_version"] = characterization.OBSERVED_CAPTURE_SCHEMA
    path.write_bytes(characterization._canonical(capture) + b"\n")
    assert (
        characterization._load_capture(path, "MISSING", module=True)[1]
        == "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"
    )


@pytest.mark.parametrize("defect", ["global-duplicate", "local-duplicate", "cross-file", "primary"])
def test_schema4_module_root_identity_negatives(defect: str) -> None:
    manifest = json.loads(_manifest(0, 2))
    manifest["schema_version"] = "4.0"
    for scenario in manifest["scenarios"]:
        scenario["module_roots"] = [scenario["api"]]
    first, second = manifest["scenarios"]
    if defect == "global-duplicate":
        second.update(api=first["api"], module_roots=[first["api"]])
        manifest["obligations"][1]["target"] = first["api"]
    elif defect == "local-duplicate":
        first["module_roots"] *= 2
    elif defect == "cross-file":
        first["module_roots"].append("src/other.py::function:calculate")
        first["module_roots"].sort()
    else:
        first["module_roots"] = ["src/sample.py::function:other"]
    with pytest.raises(
        characterization.CharacterizationError, match="MALFORMED_CHARACTERIZATION_MANIFEST"
    ):
        characterization.parse_manifest(characterization._canonical(manifest), "a" * 40)


def test_module_exact_wire_boundary_uses_real_collector_and_verifier(tmp_path: Path) -> None:
    """Tune substantive identity calls, never expected-output emissions or a large opaque value."""
    limit = characterization.MODULE_MAX_JSON_BYTES
    selected = None
    for length in range(1, 6):
        parameter = "v" * length
        source = f"def calculate({parameter}):\n    return {parameter}\n".encode()
        for difference in (1, 2):
            driver = f"target.calculate('a' * 100)\ntarget.calculate('b' * {100 + difference})\n"
            base = witness.observe(tmp_path, source, driver)
            remaining = limit - len(characterization._canonical(base)) - 1
            if remaining % 10 == 0:
                selected = parameter, source, difference, 100 + remaining // 10
                break
        if selected:
            break
    assert selected is not None
    parameter, source, difference, size = selected
    driver = f"target.calculate('a' * {size})\ntarget.calculate('b' * {size + difference})\n"
    actual = witness.observe(tmp_path, source, driver)
    raw = characterization._canonical(actual) + b"\n"
    assert len(raw) == limit
    cases, roots = module_birth._large_oracles(size)
    for index, value in enumerate(("a" * size, "b" * (size + difference))):
        arguments = [{"name": parameter, "value": {"type": "str", "value": value}}]
        cases[index] = {"input": arguments, "output": {"type": "str", "value": value}}
        roots[index].update(
            input=arguments, arguments_after=arguments, output=cases[index]["output"]
        )
    parsed = characterization._read_module_json(raw, "OVERSIZED")
    characterization.verify_module_observation(
        source, "observed_fixture.py", witness.API, parsed, cases, roots
    )
    for case in cases:
        characterization.module_validate_value(case["output"], "observed_fixture.py", [])
    with pytest.raises(characterization.CharacterizationError, match="OVERSIZED"):
        characterization._read_module_json(raw + b"\n", "OVERSIZED")
    with pytest.raises(characterization.CharacterizationError, match="OVERSIZED"):
        characterization._read_json_bytes(raw, "OVERSIZED")
    too_large = witness.observe(
        tmp_path, source, driver.replace(f"{size + difference}", f"{size + difference + 1}")
    )
    assert too_large == {"error": "OBSERVER_VALUE_LIMIT"}


def test_module_oracle_reader_exact_bound_with_finite_typed_cases() -> None:
    limit = characterization.MODULE_MAX_JSON_BYTES
    # 128 finite source identity calls; each primitive remains below262144.
    records = [
        copy.deepcopy(row) for _ in range(64) for row in module_birth._large_oracles(5000)[1]
    ]
    raw = characterization._canonical(records)
    remaining = limit - len(raw)
    # A root parameter is part of every exact input/mutation record. Its two
    # occurrences let us select the byte residue without adding irrelevant data.
    if remaining % 3:
        extra = 1 if remaining % 3 == 2 else 2
        for field in ("input", "arguments_after"):
            records[0][field][0]["name"] = "value" + "v" * extra
        remaining -= 2 * extra
    assert remaining % 3 == 0
    value = records[0]["output"]["value"] + "c" * (remaining // 3)
    for field in ("input", "arguments_after"):
        records[0][field][0]["value"] = {"type": "str", "value": value}
    records[0]["output"] = {"type": "str", "value": value}
    raw = characterization._canonical(records)
    assert len(raw) == limit
    parsed = characterization._read_module_json(raw, "OVERSIZED")
    characterization._module_root_cases(parsed, records, 128)
    characterization._module_case_values(parsed, "observed_fixture.py", [])
    with pytest.raises(characterization.CharacterizationError, match="OVERSIZED"):
        characterization._read_module_json(raw + b"\n", "OVERSIZED")


def test_module_readers_reject_duplicate_keys_and_wrong_aggregate_version() -> None:
    with pytest.raises(characterization.CharacterizationError):
        characterization._read_module_json(b'{"codec":"x","codec":"y"}', "DUPLICATE")
    for schema in (
        characterization.CAPTURE_SCHEMA,
        characterization.OBSERVED_CAPTURE_SCHEMA,
        "unknown",
    ):
        with pytest.raises(characterization.CharacterizationError):
            characterization._read_module_aggregate(
                characterization._canonical({"schema_version": schema}),
                "INVALID",
                characterization.MODULE_CAPTURE_SCHEMA,
            )


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
