from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_characterization import (
    PYTHON_CONTRACT,
    TYPESCRIPT_CONTRACT,
    _commit,
    _git,
    _verify,
    _write,
    _write_artifacts,
    hosted_characterization,
)

from supportability_gate import baseline_evidence as baseline
from supportability_gate import (
    characterization,
    contract,
    git_changes,
    refactor_policy,
    refactor_targets,
)


def _observe(
    tmp_path: Path, language: str, invoke: bool = True, custom_source: bytes | None = None
):
    root = tmp_path / "target"
    root.mkdir()
    if language == "python":
        path = "model.py"
        source = b"def grade(value):\n    return value * 2\n"
        driver = tmp_path / "driver.py"
        driver.write_text(
            "import json,sys,os\nsys.path.insert(0,os.environ['SUPPORTABILITY_CHARACTERIZATION_TARGET'])\nimport model\n"
            + (
                "cases=[{'input':i,'output':model.grade(i)} for i in [1,3]]\n"
                if invoke
                else "cases=[]\n"
            )
            + "print(json.dumps({'schema_version':'1.0','scenario':'sample','behavior':cases}))\n"
        )
        command = [
            sys.executable,
            "-P",
            str(Path(__file__).with_name("baseline_observer.py")),
            str(driver),
        ]
    else:
        if shutil.which("node") is None:
            pytest.skip("Node runtime unavailable")
        path = "model.js"
        source = b"export function grade(value) { return value * 2; }\n"
        driver = tmp_path / "driver.mjs"
        driver.write_text(
            "import {pathToFileURL} from 'node:url';\nconst model = await import(pathToFileURL(process.env.SUPPORTABILITY_CHARACTERIZATION_TARGET+'/model.js'));\n"
            + (
                "const cases=await Promise.all([1,3].map(async input=>({input,output:await model.grade(input)})));\n"
                if invoke
                else "const cases=[];\n"
            )
            + "console.log(JSON.stringify({schema_version:'1.0',scenario:'sample',behavior:cases}));\n"
        )
        command = ["node", str(Path(__file__).with_name("baseline_observer.mjs")), str(driver)]
    source = custom_source if custom_source is not None else source
    (root / path).write_bytes(source)
    completed = subprocess.run(
        command,
        env={**os.environ, "SUPPORTABILITY_CHARACTERIZATION_TARGET": str(root)},
        check=True,
        capture_output=True,
        timeout=30,
    )
    return {path: source}, json.loads(completed.stdout)


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_real_observer_requires_body_execution(tmp_path: Path, language: str) -> None:
    sources, observed = _observe(tmp_path, language)
    assert baseline.execution_verified(sources, observed["witness"])
    behavior = baseline.behavior(sources, observed["driver"]["behavior"])
    assert baseline.meaningful(behavior, tuple(sources))
    path = next(iter(sources))
    assert not baseline.execution_verified({path: sources[path] + b"\n"}, observed["witness"])
    assert not baseline.execution_verified(sources, {})


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_import_only_is_not_execution_proof(tmp_path: Path, language: str) -> None:
    sources, observed = _observe(tmp_path, language, invoke=False)
    assert not baseline.execution_verified(sources, observed["witness"])


@pytest.mark.parametrize(
    "language,source",
    [
        (
            "python",
            b"def grade(value):\n    twice = lambda x: x * 2\n    return sum(twice(x) for x in [value])\n",
        ),
        (
            "typescript",
            "const label = 'é';\nconst twice = value => value * 2;\nclass Scale { apply(value) { return twice(value); } }\nexport async function grade(value) { return new Scale().apply(value); }\n".encode(),
        ),
    ],
)
def test_real_nested_and_async_bodies_keep_original_source_identity(
    tmp_path: Path, language: str, source: bytes
) -> None:
    sources, observed = _observe(tmp_path, language, custom_source=source)
    assert baseline.execution_verified(sources, observed["witness"]), observed["witness"]


def test_constant_module_still_requires_execution(tmp_path: Path) -> None:
    sources, observed = _observe(tmp_path, "python", invoke=False, custom_source=b"VALUE = 1\n")
    assert baseline.execution_verified(sources, observed["witness"])
    assert not baseline.execution_verified(
        sources, {"model.py": {"sha256": baseline.digest(sources["model.py"]), "execution": {}}}
    )


def test_asset_inventory_covers_every_file_without_obligation_per_file() -> None:
    sources = {f"content/{i}.json": str(i).encode() for i in range(400)}
    behavior = baseline.behavior(sources, [])
    assert baseline.meaningful(behavior, tuple(sources))
    assert baseline.execution_verified(sources, {})
    removed = copy.deepcopy(behavior)
    removed["assets"].pop("content/399.json")
    assert not baseline.meaningful(removed, tuple(sources))
    changed = {**sources, "content/399.json": b"changed"}
    assert baseline.behavior(changed, []) != behavior


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"cases": [], "assets": []},
        {"cases": [], "assets": {"a.json": "x" * 64}},
        {"cases": [{"input": 1, "output": 2}, {"input": 2, "output": 2}], "assets": {}},
        {"cases": [{"input": 1, "output": 2}, {"input": 1, "output": 3}], "assets": {}},
        {"cases": [{"constant": 2}], "assets": {}},
    ],
)
def test_meaningless_or_malformed_proof_is_rejected(value) -> None:
    assert not baseline.meaningful(value, ("model.py",))


@pytest.mark.parametrize(
    "witness",
    [None, [], {}, {"model.py": None}, {"model.py": {"sha256": "0" * 64, "execution": {}}}],
)
def test_missing_and_substituted_runtime_proof_is_rejected(witness) -> None:
    assert not baseline.execution_verified({"model.py": b"def f(): return 1\n"}, witness)


def _manifest(paths=("web/model.js",)):
    return {
        "schema_version": "5.0",
        "scenarios": [
            {
                "id": "sample",
                "kind": "baseline",
                "covers": list(paths),
                "api": None,
                "module_roots": [],
            }
        ],
        "obligations": [
            {
                "id": "sample-cases",
                "category": "baseline",
                "scenario": "sample",
                "selector": "$",
                "target": "scenario:sample",
            }
        ],
        "transitions": [],
        "corrections": [],
    }


def test_grouped_baseline_manifest_remains_bounded_and_source_bound() -> None:
    value = _manifest(tuple(f"content/{i}.json" for i in range(400)))
    parsed = characterization.parse_manifest(json.dumps(value).encode(), "a" * 40)
    assert len(parsed.obligations) == 1
    assert len(parsed.scenarios[0].covers) == 400
    value["obligations"][0]["target"] = "scenario:other"
    with pytest.raises(characterization.CharacterizationError):
        characterization.parse_manifest(json.dumps(value).encode(), "a" * 40)


def test_group_coverage_requires_meaningful_and_compatible_evidence() -> None:
    targets = ["web/model.js::function:grade"]
    scenario = [{"id": "sample", "kind": "baseline", "covers": ["web/model.js"]}]
    row = {
        "id": "sample-cases",
        "category": "baseline",
        "scenario": "sample",
        "target": "scenario:sample",
        "compatibility": "BLOCK",
        "meaningful": True,
    }
    assert characterization._verified_obligation_coverage(targets, [row], scenarios=scenario) == []
    assert (
        characterization._verified_obligation_coverage(
            targets, [row], frozenset({"sample-cases"}), scenario
        )
        == targets
    )
    row["meaningful"] = False
    assert (
        characterization._verified_obligation_coverage(
            targets, [row], frozenset({"sample-cases"}), scenario
        )
        == []
    )


def test_baseline_command_uses_only_fixed_observers() -> None:
    scenario = characterization.Scenario("sample", "baseline", ("web/model.js",))
    assert characterization.scenario_command(scenario, "mixed") == [
        "node",
        "/collector/baseline_observer.mjs",
        "tests/characterization/sample.characterization.mjs",
    ]
    python = characterization.Scenario("sample", "baseline", ("src/model.py",))
    assert characterization.scenario_command(python, "mixed") == [
        "python3.12",
        "-P",
        "/collector/baseline_observer.py",
        "tests/characterization/sample.characterization.py",
    ]


@pytest.mark.parametrize("version", ["1.0", "2.0", "3.0", "4.0"])
def test_baseline_cannot_be_smuggled_into_older_schemas(version: str) -> None:
    manifest = _manifest()
    manifest["schema_version"] = version
    with pytest.raises(characterization.CharacterizationError):
        characterization.parse_manifest(json.dumps(manifest).encode(), "a" * 40)


def _first_release(tmp_path: Path, language: str):
    sources, observed = _observe(tmp_path, language)
    source = next(iter(sources))
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "remote", "add", "origin", "https://github.com/example/fixture.git")
    path = "src/" + source
    text = (
        (PYTHON_CONTRACT if language == "python" else TYPESCRIPT_CONTRACT)
        .replace('high_risk_paths = ["src/sample.py"]', "high_risk_paths = []")
        .replace('high_risk_paths = ["src/sample.ts"]', "high_risk_paths = []")
    )
    _write(repository / ".supportability.toml", text)
    base = _commit(repository, "contract before application")
    _write(repository / path, sources[source].decode())
    preview = _commit(repository, "derive candidate scope")
    policy = contract.parse_contract(text.encode())
    identity = git_changes.inspect_repository(repository, base, preview, [])
    targets, _ = refactor_targets.derive(
        repository, identity, policy, git_changes.changed_paths(repository, base, preview, []), []
    )
    _git(repository, "reset", "--hard", base)
    manifest = _manifest((path,))
    extension = "py" if language == "python" else "mjs"
    driver_path = f"tests/characterization/sample.characterization.{extension}"
    driver = (tmp_path / f"driver.{extension}").read_text().replace("/model.js", "/src/model.js")
    if language == "python":
        driver = driver.replace(
            "os.environ['SUPPORTABILITY_CHARACTERIZATION_TARGET']",
            "os.environ['SUPPORTABILITY_CHARACTERIZATION_TARGET']+'/src'",
        )
    golden = baseline.behavior({path: sources[source]}, observed["driver"]["behavior"])
    files = {
        driver_path: ("expected_case", driver),
        "tests/characterization/sample.golden.json": ("golden", json.dumps(golden)),
        "tests/characterization/sample.review.json": (
            "review",
            '{"evidence_class":"author_declaration","feature":"grade doubles a value"}',
        ),
        "tests/characterization/sample.source.json": (
            "source_receipt",
            json.dumps({path: baseline.digest(sources[source])}),
        ),
    }
    for name, (_, content) in files.items():
        _write(repository / name, content)
    manifest["corrections"] = [
        {
            "id": "first-release",
            "scenarios": ["sample"],
            "obligations": ["sample-cases"],
            "targets": list(targets),
            "oracle_files": [
                {"path": name, "kind": kind, "sha256": baseline.digest(content.encode())}
                for name, (kind, content) in sorted(files.items())
            ],
        }
    ]
    _write(repository / characterization.MANIFEST_PATH, json.dumps(manifest))
    oracle = _commit(repository, "freeze expected cases before implementation")
    _write(repository / path, sources[source].decode())
    head = _commit(repository, "introduce application")
    base_checkout = tmp_path / "base"
    _git(repository, "worktree", "add", "--detach", str(base_checkout), base)
    return repository, base_checkout, base, oracle, head, policy


def _capture_row(target: Path, repository: Path, head: str, policy, monkeypatch):
    scenario = characterization._manifest(repository, head, []).scenarios[0]
    monkeypatch.setenv("SUPPORTABILITY_CHARACTERIZATION_TARGET", str(target))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")

    def command(plan, **kwargs):
        mounts = dict((container, host) for host, container in kwargs["extra_mounts"])
        values = []
        for value in plan.actual:
            if value.startswith("/collector/"):
                value = str(Path(__file__).parent / value.removeprefix("/collector/"))
            elif value.startswith("/driver/"):
                value = str(mounts["/driver"] / value.removeprefix("/driver/"))
            values.append(value)
        return values

    monkeypatch.setattr(hosted_characterization.quality_runner, "sandbox_command", command)
    return hosted_characterization._scenario_capture(
        target, repository, head, scenario, policy.language, []
    )


def _capture_envelope(row, manifest, policy, base, head, side):
    return {
        "authentication": {
            "base_sha": base,
            "head_sha": head,
            "repository": "example/fixture",
            "repository_id": "123",
            "workflow_sha": "f" * 40,
            "run_id": "456",
            "run_attempt": "1",
            "job": f"characterize-{side}",
            "side": side,
        },
        "behavior_fingerprint": characterization._sha256(
            characterization._canonical([[row["id"], row["behavior_sha256"]]])
        ),
        "definition_sha": head,
        "environment": {
            "container_digest": "sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4",
            "container_id": "sha256:" + "1" * 64,
            "container_image": "ubuntu@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4",
            "node_runtime": "v24.20.0:sha256:" + "4" * 64
            if policy.language == "typescript"
            else "",
            "python_runtime": "Python 3.12.14:sha256:" + "3" * 64,
            "resolved_dependencies": [],
        },
        "language": policy.language,
        "manifest": characterization._manifest_payload(manifest),
        "scenarios": [row],
        "schema_version": characterization.CORRECTION_CAPTURE_SCHEMA,
        "target_sha": base if side == "base" else head,
    }


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_first_release_captures_real_head_and_git_absence(
    tmp_path: Path, monkeypatch, language: str
):
    repository, base_checkout, base, oracle, head, policy = _first_release(tmp_path, language)
    manifest = characterization._manifest(repository, head, [])
    before = _capture_row(base_checkout, repository, head, policy, monkeypatch)
    after = _capture_row(repository, repository, head, policy, monkeypatch)
    assert before["command"] is None
    assert after["deterministic"] is True
    assert after["error"] is None
    artifacts = [
        _capture_envelope(row, manifest, policy, base, head, side)
        for row, side in [(before, "base"), (after, "head")]
    ]
    paths = _write_artifacts(tmp_path, *artifacts)
    result = _verify(repository, base, head, *paths)
    assert result["policy_blocks"] == [
        "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:obligation:sample-cases",
        "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:sample",
    ]
    assert not result["correction"]["verification_blocks"]
    correction = result["correction"]
    denied, _, _ = refactor_policy._correction_transaction_blocks(
        repository, policy, base, head, None, result, []
    )
    assert "UNAUTHORIZED_CORRECTION" in denied
    authorization = refactor_policy.Authorization(
        "example/fixture",
        base,
        head,
        True,
        (),
        tuple(
            sorted(
                c.new_path or c.old_path
                for c in git_changes.changed_paths(repository, base, head, [])
            )
        ),
        tuple(correction["targets"]),
        refactor_policy.Sequence(1, base, "first-release"),
        (),
        "5.0",
        correction["id"],
        oracle,
        correction["oracle_manifest_blob_sha"],
        correction["oracle_manifest_sha256"],
        correction["behavior_delta_sha256"],
    )
    accepted, _, _ = refactor_policy._correction_transaction_blocks(
        repository, policy, base, head, authorization, result, []
    )
    assert accepted == []
    assert result["refactor_runnability"]["runnable"] is True
    assert result["coverage"]["covered_obligations"] == [
        manifest.scenarios[0].covers[0] + "::function:grade"
    ]
    # A real existing production tree cannot be relabelled as absent.
    assert not characterization._baseline_birth_valid(
        repository, head, head, policy, manifest, manifest.scenarios[0], []
    )
    # Substituted evidence does not receive correction coverage credit.
    artifacts[1]["scenarios"][0]["baseline_witness"] = {}
    bad = _verify(repository, base, head, *_write_artifacts(tmp_path, *artifacts))
    assert "CHARACTERIZATION_EXECUTION_FAILED:sample" in bad["policy_blocks"]
    assert not bad["coverage"]["covered_obligations"]
    # Once established, the scenario is ordinary two-sided preservation evidence.
    _write(repository / "notes.txt", "documentation only")
    successor = _commit(repository, "later change")
    old_capture = _capture_envelope(after, manifest, policy, head, successor, "base")
    new_capture = _capture_envelope(after, manifest, policy, head, successor, "head")
    # Restore the valid, independently executed witness changed in the tamper test.
    fresh = _capture_row(repository, repository, successor, policy, monkeypatch)
    for capture in (old_capture, new_capture):
        capture["scenarios"] = [fresh]
    preserved = _verify(
        repository, head, successor, *_write_artifacts(tmp_path, old_capture, new_capture)
    )
    assert preserved["overall_result"] == "PASS", preserved["policy_blocks"]
    assert "correction" not in preserved
    # A forged changed output cannot reuse its old behavior hash to claim compatibility.
    altered = copy.deepcopy(new_capture)
    altered["scenarios"][0]["behavior"]["cases"][0]["output"] = "invented"
    assert not characterization._baseline_row_valid(
        manifest.scenarios[0],
        altered["scenarios"][0],
        characterization.baseline_sources(repository, successor, manifest.scenarios[0], []),
        policy.language,
    )
