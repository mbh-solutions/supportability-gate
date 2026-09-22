from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from supportability_gate import (
    characterization,
    contract,
    git_changes,
    refactor_targets,
)

_SPEC = importlib.util.spec_from_file_location(
    "hosted_characterization", Path(__file__).with_name("hosted_characterization.py")
)
assert _SPEC and _SPEC.loader
hosted_characterization = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hosted_characterization)


@pytest.fixture(autouse=True)
def _hosted_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")


def test_retained_quality_runner_has_characterization() -> None:
    root = Path(__file__).parents[1]
    manifest = characterization.parse_manifest(
        (root / ".supportability-characterization.json").read_bytes(), "0" * 40
    )
    covered = {path for scenario in manifest.scenarios for path in scenario.covers}

    assert "src/supportability_gate/quality_runner.py" in covered


def test_s05_live_driver_matches_golden(capsys: pytest.CaptureFixture[str]) -> None:
    root = Path(__file__).parents[1]
    driver_path = root / "tests/characterization/s05-meaningful-behavior.characterization.py"
    specification = importlib.util.spec_from_file_location("s05_meaningful_behavior", driver_path)
    assert specification and specification.loader
    driver = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(driver)

    driver.main()

    actual = json.loads(capsys.readouterr().out)["behavior"]
    expected = json.loads(
        (root / "tests/characterization/s05-meaningful-behavior.golden.json").read_text(
            encoding="utf-8"
        )
    )
    assert actual == expected


def test_runtime_probe_retains_sandbox_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    output = tmp_path / "supervisor"
    diagnostics = tmp_path / "retained"
    executable = tmp_path / "python"
    target.mkdir()
    executable.write_bytes(b"python")
    monkeypatch.setattr(
        hosted_characterization.quality_runner,
        "sandbox_command",
        lambda *args, **kwargs: ("docker", "run"),
    )
    monkeypatch.setattr(
        hosted_characterization.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["docker", "run"], 125, b"runtime stdout", b"runtime stderr"
        ),
    )

    with pytest.raises(
        characterization.CharacterizationError, match="TARGET_SANDBOX_RUNTIME_FAILED"
    ):
        hosted_characterization._runtime_probe(
            target,
            output,
            str(executable),
            "characterization-python-runtime",
            diagnostics,
            "characterization-head-runtime",
            {"workflow_sha": "f" * 40},
        )

    retained = diagnostics / "diagnostics"
    payload = json.loads(
        (
            retained / "characterization-head-runtime--characterization-python-runtime.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["code"] == "TARGET_SANDBOX_RUNTIME_FAILED"
    assert (retained / payload["logs"][1]["path"]).read_text() == "runtime stderr"


def test_scenario_retains_sandbox_runtime_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    definition = tmp_path / "definition"
    diagnostics = tmp_path / "retained"
    target.mkdir()
    definition.mkdir()
    monkeypatch.setattr(
        hosted_characterization.quality_runner,
        "sandbox_command",
        lambda *args, **kwargs: ("docker", "run"),
    )
    monkeypatch.setattr(
        hosted_characterization.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["docker", "run"], 125, b"scenario stdout", b"scenario stderr"
        ),
    )
    scenario = characterization.Scenario("victim", "regression", ("src/sample.py",))

    with pytest.raises(
        characterization.CharacterizationError, match="TARGET_SANDBOX_RUNTIME_FAILED"
    ):
        hosted_characterization._run_driver(
            target,
            definition,
            scenario,
            "python",
            b"print('driver')\n",
            diagnostics=diagnostics,
            stage="characterization-head",
            identity={"workflow_sha": "f" * 40},
        )

    retained = diagnostics / "diagnostics"
    payload = json.loads(
        (retained / "characterization-head--victim.json").read_text(encoding="utf-8")
    )
    assert payload["code"] == "TARGET_SANDBOX_RUNTIME_FAILED"
    assert (retained / payload["logs"][1]["path"]).read_text() == "scenario stderr"


PYTHON_CONTRACT = """\
schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = ["src/sample.py"]

[[gates]]
adapter = "python.c901-touched.v1"
paths = ["src"]

[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
"""

TYPESCRIPT_CONTRACT = PYTHON_CONTRACT.replace(
    'language = "python"', 'language = "typescript"'
).replace(
    'adapter = "python.c901-touched.v1"',
    'adapter = "typescript.c901-equivalent-touched.v1"',
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _manifest(scenarios: list[dict[str, object]]) -> str:
    return json.dumps({"schema_version": "1.0", "scenarios": scenarios}, indent=2) + "\n"


def _manifest_v2(
    scenarios: list[dict[str, object]],
    obligations: list[dict[str, object]],
    transitions: list[dict[str, object]] | None = None,
) -> str:
    return (
        json.dumps(
            {
                "obligations": sorted(obligations, key=lambda item: str(item["id"])),
                "scenarios": scenarios,
                "schema_version": "2.0",
                "transitions": sorted(transitions or [], key=lambda item: str(item["from"])),
            },
            indent=2,
        )
        + "\n"
    )


def _obligation(identifier: str, scenario: str, target: str) -> dict[str, object]:
    return {
        "category": "behavior",
        "id": identifier,
        "scenario": scenario,
        "selector": identifier,
        "target": target,
    }


def _behavior(identifier: str, offset: int = 1) -> dict[str, object]:
    return {
        identifier: [
            {"input": 0, "output": offset},
            {"input": 2, "output": 2 + offset},
        ]
    }


def _add_behavior_scenario(repository: Path, identifier: str, behavior: dict[str, object]) -> None:
    _write(
        repository / f"tests/characterization/{identifier}.characterization.py",
        "import json\n"
        f"print(json.dumps({{'schema_version': '1.0', 'scenario': '{identifier}', "
        f"'behavior': {behavior!r}}}, sort_keys=True))\n",
    )
    _write(
        repository / f"tests/characterization/{identifier}.golden.json",
        json.dumps(behavior) + "\n",
    )


def _scenario(identifier: str, path: str) -> dict[str, object]:
    return {"id": identifier, "kind": "golden", "covers": [path]}


def _driver(identifier: str, source_path: str, language: str) -> str:
    if language == "python":
        return f'''\
import json
import os
from pathlib import Path

target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
behavior = {{"source": (target / "{source_path}").read_text(encoding="utf-8")}}
print(json.dumps({{"schema_version": "1.0", "scenario": "{identifier}", "behavior": behavior}}, sort_keys=True))
'''
    return f'''\
import {{ readFileSync }} from "node:fs";
import {{ join }} from "node:path";
const target = process.env.SUPPORTABILITY_CHARACTERIZATION_TARGET;
const behavior = {{ source: readFileSync(join(target, "{source_path}"), "utf8") }};
console.log(JSON.stringify({{ schema_version: "1.0", scenario: "{identifier}", behavior }}));
'''


def _add_scenario_files(
    repository: Path,
    identifier: str,
    source_path: str,
    language: str,
    expected: str,
) -> None:
    extension = "py" if language == "python" else "mjs"
    root = repository / "tests" / "characterization"
    _write(
        root / f"{identifier}.characterization.{extension}",
        _driver(identifier, source_path, language),
    )
    _write(root / f"{identifier}.golden.json", json.dumps({"source": expected}) + "\n")


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _repository(
    tmp_path: Path,
    *,
    language: str = "python",
    add_scenario: bool = False,
    changed_source: bool = False,
    changed_golden: bool = False,
    deleted_source: bool = False,
    uncovered_high_risk: bool = False,
) -> tuple[Path, Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "remote", "add", "origin", "https://github.com/example/fixture.git")
    source_path = "src/sample.py" if language == "python" else "src/sample.ts"
    contract_text = PYTHON_CONTRACT if language == "python" else TYPESCRIPT_CONTRACT
    contract_text = contract_text.replace("src/sample.py", source_path)
    if uncovered_high_risk:
        contract_text = contract_text.replace(
            f'high_risk_paths = ["{source_path}"]',
            f'high_risk_paths = ["{source_path}", "src/risk.py"]',
        )
    _write(repository / ".supportability.toml", contract_text)
    _write(repository / source_path, "base\n")
    if uncovered_high_risk:
        _write(repository / "src/risk.py", "risk\n")
    scenarios = [_scenario("existing", source_path)]
    if deleted_source:
        _write(repository / "src/retained.py", "retained\n")
        scenarios[0]["covers"] = [source_path, "src/retained.py"]
        scenarios.append(_scenario("retained", "src/retained.py"))
    _write(repository / characterization.MANIFEST_PATH, _manifest(scenarios))
    _add_scenario_files(repository, "existing", source_path, language, "base\n")
    if deleted_source:
        _add_scenario_files(repository, "retained", "src/retained.py", language, "retained\n")
    base_sha = _commit(repository, "base")
    _write(repository / "docs/note.md", "head\n")
    if deleted_source:
        (repository / source_path).unlink()
        extension = "py" if language == "python" else "mjs"
        (repository / f"tests/characterization/existing.characterization.{extension}").unlink()
        (repository / "tests/characterization/existing.golden.json").unlink()
        _write(
            repository / characterization.MANIFEST_PATH,
            _manifest([_scenario("retained", "src/retained.py")]),
        )
    if changed_source:
        _write(repository / source_path, "head\n")
    if changed_golden:
        _write(
            repository / "tests/characterization/existing.golden.json",
            json.dumps({"source": "head\n"}) + "\n",
        )
    if add_scenario:
        scenarios.append(_scenario("new-scenario", source_path))
        _write(repository / characterization.MANIFEST_PATH, _manifest(scenarios))
        _add_scenario_files(repository, "new-scenario", source_path, language, "base\n")
    head_sha = _commit(repository, "head")
    base_checkout = tmp_path / "base"
    _git(repository, "worktree", "add", "--detach", str(base_checkout), base_sha)
    return repository, base_checkout, base_sha, head_sha


def _captures(
    repository: Path,
    base_checkout: Path,
    base_sha: str,
    head_sha: str,
) -> tuple[dict[str, object], dict[str, object]]:
    common = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "repository": "example/fixture",
        "repository_id": "123",
        "workflow_sha": "f" * 40,
        "run_id": "456",
        "run_attempt": "1",
    }
    base = _synthetic_capture(base_checkout, repository, "base", common)
    head = _synthetic_capture(repository, repository, "head", common)
    return base, head


def _synthetic_capture(
    target: Path,
    definition: Path,
    side: str,
    common: dict[str, str],
) -> dict[str, object]:
    records: list[git_changes.CommandRecord] = []
    target_sha = _git(target, "rev-parse", "HEAD")
    head_sha = common["head_sha"]
    policy = contract.parse_contract(
        git_changes.read_regular_blob(definition, head_sha, ".supportability.toml", records).content
    )
    manifest = characterization._manifest(definition, head_sha, records)
    scenarios: list[dict[str, object]] = []
    for scenario in manifest.scenarios:
        driver_path, golden_path = characterization._scenario_paths(scenario, policy.language)
        driver = git_changes.read_regular_blob(definition, head_sha, driver_path, records)
        golden = git_changes.read_regular_blob(definition, head_sha, golden_path, records)
        source = (target / scenario.covers[0]).read_text(encoding="utf-8")
        golden_behavior = characterization._read_json_bytes(
            golden.content, "MALFORMED_GOLDEN_OUTPUT"
        )
        driver_text = driver.content.decode("utf-8")
        if "read_text" in driver_text or "readFileSync" in driver_text:
            behavior: object = {"source": source}
        elif "s16_runtime_fixture" in source:
            behavior = {"value": 5}
        else:
            behavior = golden_behavior
        behavior_sha = characterization._sha256(characterization._canonical(behavior))
        scenarios.append(
            {
                "behavior": behavior,
                "behavior_sha256": behavior_sha,
                "command": [
                    "python3.12" if policy.language == "python" else "node",
                    *(["-P"] if policy.language == "python" else []),
                    driver_path,
                ],
                "covers": list(scenario.covers),
                "deterministic": True,
                "driver_blob_sha": driver.object_sha,
                "error": None,
                "exit_code": 0,
                "golden_behavior_sha256": characterization._sha256(
                    characterization._canonical(golden_behavior)
                ),
                "golden_blob_sha": golden.object_sha,
                "id": scenario.id,
                "kind": scenario.kind,
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "stdout_sha256": behavior_sha,
            }
        )
    environment = {
        "container_digest": (
            "sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4"
        ),
        "container_id": "sha256:" + "1" * 64,
        "container_image": (
            "ubuntu@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4"
        ),
        "node_runtime": "v24.8.0:sha256:" + "4" * 64 if policy.language == "typescript" else "",
        "python_runtime": "Python 3.12.14:sha256:" + "3" * 64,
        "resolved_dependencies": ["python:fixture==1.0:sha256:" + "2" * 64],
    }
    return {
        "authentication": {
            **common,
            "job": f"characterize-{side}",
            "side": side,
        },
        "behavior_fingerprint": characterization._sha256(
            characterization._canonical(
                [[item["id"], item["behavior_sha256"]] for item in scenarios]
            )
        ),
        "definition_sha": head_sha,
        "environment": environment,
        "language": policy.language,
        "manifest": characterization._manifest_payload(manifest),
        "scenarios": scenarios,
        "schema_version": characterization.CAPTURE_SCHEMA,
        "target_sha": target_sha,
    }


def test_driver_executes_authenticated_blob_from_fresh_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    definition = tmp_path / "definition"
    target.mkdir()
    relative, _ = characterization._scenario_paths(
        characterization.Scenario("victim", "regression", ("src/sample.py",)), "python"
    )
    mutable = definition / relative
    _write(
        mutable,
        "import json\n"
        "print(json.dumps({'schema_version':'1.0','scenario':'victim',"
        "'behavior':{'source':'mutable'}}))\n",
    )
    authenticated = mutable.read_bytes().replace(b"mutable", b"authenticated")
    seen: list[Path] = []
    command = hosted_characterization._command

    def record(language: str, driver: str, materialized: Path) -> tuple[list[str], list[str]]:
        seen.append(materialized)
        return command(language, driver, materialized)

    monkeypatch.setattr(hosted_characterization, "_command", record)
    monkeypatch.setattr(
        hosted_characterization.quality_runner,
        "sandbox_command",
        lambda *args, **kwargs: ("docker", "run"),
    )

    def completed(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        assert b"authenticated" in seen[-1].read_bytes()
        return subprocess.CompletedProcess(
            ["docker", "run"],
            0,
            b'{"behavior":{"source":"authenticated"},"scenario":"victim","schema_version":"1.0"}\n',
            b"",
        )

    monkeypatch.setattr(hosted_characterization.subprocess, "run", completed)
    scenario = characterization.Scenario("victim", "regression", ("src/sample.py",))

    first = hosted_characterization._run_driver(
        target, definition, scenario, "python", authenticated
    )
    second = hosted_characterization._run_driver(
        target, definition, scenario, "python", authenticated
    )

    assert first["behavior"] == second["behavior"] == {"source": "authenticated"}
    assert first["command"] == ["python3.12", "-P", relative]
    assert len(set(seen)) == 2
    assert all(not path.is_relative_to(definition) and not path.exists() for path in seen)


def _dependency_repository(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repository, base_checkout, _, _ = _repository(tmp_path)
    _write(
        repository / "src/sample.py",
        "from s16_runtime_fixture import increment\n\ndef calculate():\n    return increment(3)\n",
    )
    _write(
        repository / "tests/characterization/existing.characterization.py",
        "import json\nimport os\nfrom pathlib import Path\nfrom sample import calculate\n"
        "Path(os.environ['SUPPORTABILITY_CHARACTERIZATION_TARGET'], 'driver-ran').touch()\n"
        "print(json.dumps({'schema_version': '1.0', 'scenario': 'existing', "
        "'behavior': {'value': calculate()}}, sort_keys=True))\n",
    )
    _write(repository / "tests/characterization/existing.golden.json", '{"value": 4}\n')
    metadata = '[project]\ndependencies = ["s16-characterization-fixture==1.0"]\n'
    _write(repository / "pyproject.toml", metadata)
    base_sha = _commit(repository, "base runtime dependency")
    _write(repository / "pyproject.toml", metadata.replace("==1.0", ">=2,<3"))
    head_sha = _commit(repository, "head runtime dependency")
    _git(base_checkout, "checkout", "--detach", base_sha)
    return repository, base_checkout, base_sha, head_sha


def test_base_and_head_capture_use_the_same_exact_head_dependency_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base_checkout, base_sha, head_sha = _dependency_repository(tmp_path)
    _write(repository / "pyproject.toml", '[project]\ndependencies = ["untrusted==9.0"]\n')
    installed: list[tuple[Path, str]] = []

    def provision(
        source: Path,
        source_sha: str,
        destination: Path,
        records: list[git_changes.CommandRecord],
        diagnostics: Path | None = None,
        stage: str = "characterization",
        identity: dict[str, str] | None = None,
    ) -> None:
        del destination, records, diagnostics, stage, identity
        installed.append((source, source_sha))

    behavior_sha = characterization._sha256(characterization._canonical({"value": 5}))
    monkeypatch.setattr(hosted_characterization, "_prepare_container", lambda: "sha256:" + "1" * 64)
    monkeypatch.setattr(hosted_characterization, "_install_python_dependencies", provision)
    monkeypatch.setattr(
        hosted_characterization,
        "_runtime_probe",
        lambda *args, **kwargs: "Python 3.12.14:sha256:" + "3" * 64,
    )
    monkeypatch.setattr(
        hosted_characterization,
        "_dependency_receipts",
        lambda destination: ("python:s16-characterization-fixture==2.0:sha256:" + "2" * 64,),
    )
    monkeypatch.setattr(
        hosted_characterization,
        "_scenario_capture",
        lambda *args, **kwargs: {
            "behavior": {"value": 5},
            "behavior_sha256": behavior_sha,
            "command": ["python3.12", "-P", "tests/characterization/existing.characterization.py"],
            "covers": ["src/sample.py"],
            "deterministic": True,
            "driver_blob_sha": "1" * 40,
            "error": None,
            "exit_code": 0,
            "golden_behavior_sha256": behavior_sha,
            "golden_blob_sha": "2" * 40,
            "id": "existing",
            "kind": "golden",
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "stdout_sha256": behavior_sha,
        },
    )
    common = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "repository": "example/fixture",
        "repository_id": "123",
        "workflow_sha": "f" * 40,
        "run_id": "456",
        "run_attempt": "1",
    }
    base, base_environment = hosted_characterization.capture_evidence(
        base_checkout, repository, side="base", job="characterize-base", **common
    )
    head, head_environment = hosted_characterization.capture_evidence(
        repository, repository, side="head", job="characterize-head", **common
    )

    assert installed == [(repository, head_sha), (repository, head_sha)]
    assert base_environment == head_environment
    assert "environment" not in base
    assert "environment" not in head
    assert hosted_characterization._python_dependencies(repository, head_sha, []) == (
        "s16-characterization-fixture>=2,<3",
    )
    assert not (base_checkout / "driver-ran").exists()
    assert not (repository / "driver-ran").exists()


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing"])
def test_runtime_dependency_setup_failure_prevents_driver_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    repository, base_checkout, _base_sha, head_sha = _dependency_repository(tmp_path)
    run = subprocess.run

    def provision(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if arguments[:5] != [sys.executable, "-I", "-m", "pip", "install"]:
            return run(arguments, **kwargs)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, 120)
        if failure == "missing":
            raise OSError("pip unavailable")
        raise subprocess.CalledProcessError(1, arguments, b"", b"dependency unavailable")

    monkeypatch.setattr(hosted_characterization.subprocess, "run", provision)
    with pytest.raises(
        characterization.CharacterizationError, match="CHARACTERIZATION_PREREQUISITE_FAILED"
    ):
        hosted_characterization._install_python_dependencies(
            repository, head_sha, tmp_path / "dependencies", []
        )
    assert not (base_checkout / "driver-ran").exists()
    assert not (repository / "driver-ran").exists()


@pytest.mark.parametrize(
    "metadata",
    [
        "[project",
        'project = "malformed"\n',
        '[project]\ndynamic = ["dependencies"]\n',
        '[project]\ndependencies = "fixture==1.0"\n',
        "[project]\ndependencies = [false]\n",
        '[project]\ndependencies = ["fixture @ https://example.invalid/fixture.whl"]\n',
        '[project]\ndependencies = ["./fixture"]\n',
        '[project]\ndependencies = ["--extra-index-url=https://example.invalid"]\n',
        '[project]\ndependencies = ["fixture==1.0\\n--extra-index-url=https://example.invalid"]\n',
    ],
)
def test_runtime_dependency_metadata_fails_closed_before_pip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: str
) -> None:
    repository, base_checkout, base_sha, _ = _dependency_repository(tmp_path)
    _write(repository / "pyproject.toml", metadata)
    head_sha = _commit(repository, "unsupported dependency metadata")
    run = subprocess.run

    def refuse_pip(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if arguments[:5] == [sys.executable, "-I", "-m", "pip", "install"]:
            pytest.fail("unsupported metadata reached pip")
        return run(arguments, **kwargs)

    monkeypatch.setattr(hosted_characterization.subprocess, "run", refuse_pip)
    with pytest.raises(
        characterization.CharacterizationError,
        match="CHARACTERIZATION_PREREQUISITE_METADATA_INVALID",
    ):
        hosted_characterization._install_python_dependencies(
            repository, head_sha, tmp_path / "dependencies", []
        )
    assert not (repository / "driver-ran").exists()


def _write_artifacts(
    tmp_path: Path, base: dict[str, object], head: dict[str, object]
) -> tuple[Path, Path]:
    base_path, head_path = tmp_path / "base.json", tmp_path / "head.json"
    for path, source in ((base_path, base), (head_path, head)):
        capture = json.loads(json.dumps(source))
        environment = capture.pop("environment", None)
        _write(path, json.dumps(capture, sort_keys=True) + "\n")
        if environment is not None:
            _write(
                characterization._provenance_path(path),
                json.dumps(
                    {
                        "authentication": capture["authentication"],
                        "capture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "environment": environment,
                        "schema_version": characterization.PROVENANCE_SCHEMA,
                    },
                    sort_keys=True,
                )
                + "\n",
            )
    return base_path, head_path


def _verify(
    repository: Path,
    base_sha: str,
    head_sha: str,
    base_path: Path,
    head_path: Path,
    *,
    base_artifact_id: str = "10",
    head_artifact_id: str = "11",
    base_capture_sha256: str | None = None,
    head_capture_sha256: str | None = None,
) -> dict[str, object]:
    return characterization.verify_evidence(
        repository,
        base_sha,
        head_sha,
        base_path,
        head_path,
        repository_name="example/fixture",
        repository_id="123",
        workflow_sha="f" * 40,
        run_id="456",
        run_attempt="1",
        base_artifact_id=base_artifact_id,
        base_artifact_digest="a" * 64,
        base_capture_sha256=(
            base_capture_sha256
            if base_capture_sha256 is not None
            else hashlib.sha256(base_path.read_bytes()).hexdigest()
            if base_path.exists()
            else "0" * 64
        ),
        head_artifact_id=head_artifact_id,
        head_artifact_digest="b" * 64,
        head_capture_sha256=(
            head_capture_sha256
            if head_capture_sha256 is not None
            else hashlib.sha256(head_path.read_bytes()).hexdigest()
            if head_path.exists()
            else "0" * 64
        ),
    )


def _validate_round_trip(result: dict[str, object]) -> None:
    coverage = result["coverage"]
    assert isinstance(coverage, dict)
    required_paths = coverage["required_paths"]
    assert isinstance(required_paths, list)
    expected_artifacts = json.loads(json.dumps(result["artifacts"]))
    for side in ("base", "head"):
        if expected_artifacts[side]["capture_sha256"] is None:
            expected_artifacts[side]["capture_sha256"] = "0" * 64
    assert (
        characterization.validate_result(
            result,
            repository=str(result["repository"]),
            base_sha=str(result["base_sha"]),
            head_sha=str(result["head_sha"]),
            workflow_sha=str(result["workflow_sha"]),
            required_paths=tuple(required_paths),
            expected_artifacts=expected_artifacts,
        )
        == result["policy_blocks"]
    )


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_existing_characterization_passes_with_exact_identity(
    tmp_path: Path, language: str
) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, language=language)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert result["base_sha"] == base_sha
    assert result["head_sha"] == head_sha
    assert result["scenarios"][0]["compatibility"] == "PASS"
    assert result["coverage"]["required_paths"] == [
        "src/sample.py" if language == "python" else "src/sample.ts"
    ]
    _validate_round_trip(result)
    assert result["refactor_runnability"] == {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "repository": "github.com/example/fixture",
        "runnable": True,
        "schema_version": characterization.RUNNABILITY_SCHEMA,
        "targets": [],
        "unbounded_paths": [],
        "workflow_sha": "f" * 40,
    }


def test_logical_step_runnability_requires_same_recorded_command() -> None:
    scenario = characterization.Scenario("sample", "golden", ("src/sample.py",))
    manifest = characterization.Manifest((scenario,), "a" * 40, "b" * 64)
    targets = ("src/sample.py::function:calculate:1-2",)
    command = ["python3.12", "-P", "tests/characterization/sample.characterization.py"]
    base = {"sample": {"command": command}}
    head = {"sample": {"command": command}}

    assert characterization._logical_step_runnable(manifest, base, head, targets, "python") is True
    assert characterization._logical_step_runnable(manifest, base, {}, targets, "python") is False
    assert (
        characterization._logical_step_runnable(
            manifest,
            base,
            head,
            (*targets, "src/missing.py::function:missing:1-2"),
            "python",
        )
        is False
    )
    head["sample"]["command"] = ["python3.12", "-P", "other.py"]
    assert characterization._logical_step_runnable(manifest, base, head, targets, "python") is False
    base["sample"]["command"] = head["sample"]["command"]
    assert characterization._logical_step_runnable(manifest, base, head, targets, "python") is False

    typescript_command = ["node", "tests/characterization/sample.characterization.mjs"]
    typescript = {"sample": {"command": typescript_command}}
    assert (
        characterization._logical_step_runnable(
            manifest, typescript, typescript, targets, "typescript"
        )
        is True
    )


def test_constant_output_cannot_hide_changed_responsibility_behavior(tmp_path: Path) -> None:
    repository, base_checkout, original_base, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", original_base)
    _write(
        repository / "tests/characterization/existing.characterization.py",
        "import json\n"
        "print(json.dumps({'schema_version': '1.0', 'scenario': 'existing', "
        "'behavior': {'stable': True}}, sort_keys=True))\n",
    )
    _write(
        repository / "tests/characterization/existing.golden.json",
        json.dumps({"stable": True}) + "\n",
    )
    _write(
        repository / "src/sample.py",
        "def calculate(value: int) -> int:\n    return value + 1\n",
    )
    base_sha = _commit(repository, "stable characterization")
    _write(
        repository / "src/sample.py",
        "def calculate(value: int) -> int:\n    return value + 2\n",
    )
    head_sha = _commit(repository, "bounded source change")
    _git(base_checkout, "reset", "--hard", base_sha)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "BLOCK"
    assert (
        "MISSING_CHARACTERIZATION_COVERAGE:obligation:src/sample.py::function:calculate"
        in result["policy_blocks"]
    )
    assert result["refactor_runnability"] == {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "repository": "github.com/example/fixture",
        "runnable": True,
        "schema_version": characterization.RUNNABILITY_SCHEMA,
        "targets": ["src/sample.py::function:calculate:1-2"],
        "unbounded_paths": [],
        "workflow_sha": "f" * 40,
    }


def test_target_derivation_failure_preserves_characterization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base_checkout, original_base, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", original_base)
    _write(
        repository / "tests/characterization/existing.characterization.py",
        "import json\n"
        "print(json.dumps({'schema_version': '1.0', 'scenario': 'existing', "
        "'behavior': {'stable': True}}, sort_keys=True))\n",
    )
    _write(
        repository / "tests/characterization/existing.golden.json",
        json.dumps({"stable": True}) + "\n",
    )
    _write(
        repository / "src/sample.py",
        "def calculate(value: int) -> int:\n    return value + 1\n",
    )
    base_sha = _commit(repository, "stable characterization")
    _write(
        repository / "src/sample.py",
        "def calculate(value: int) -> int:\n    return value + 2\n",
    )
    head_sha = _commit(repository, "bounded source change")
    _git(base_checkout, "reset", "--hard", base_sha)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    def fail(*args: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
        raise git_changes.GitError("GIT_TIMEOUT", "target derivation timed out")

    monkeypatch.setattr(refactor_targets, "derive", fail)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert result["policy_blocks"] == []
    assert result["scenarios"][0]["compatibility"] == "PASS"
    assert result["refactor_runnability"]["runnable"] is False
    assert result["refactor_runnability"]["targets"] == []
    assert result["refactor_runnability"]["unbounded_paths"] == ["src/sample.py"]


def test_separate_constant_scenarios_do_not_cover_changed_responsibilities(
    tmp_path: Path,
) -> None:
    repository, base_checkout, original_base, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", original_base)
    sources = {
        "existing": ("src/sample.py", "calculate"),
        "other": ("src/other.py", "normalize"),
    }
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest([_scenario(identifier, path) for identifier, (path, _) in sources.items()]),
    )
    for identifier, (path, name) in sources.items():
        _write(
            repository / path,
            f"def {name}(value: int) -> int:\n    return value + 1\n",
        )
        _write(
            repository / f"tests/characterization/{identifier}.characterization.py",
            "import json\n"
            f"print(json.dumps({{'schema_version': '1.0', 'scenario': '{identifier}', "
            f"'behavior': {{'stable': '{identifier}'}}}}, sort_keys=True))\n",
        )
        _write(
            repository / f"tests/characterization/{identifier}.golden.json",
            json.dumps({"stable": identifier}) + "\n",
        )
    base_sha = _commit(repository, "separate runnable scenarios")
    for path, name in sources.values():
        _write(
            repository / path,
            f"def {name}(value: int) -> int:\n    return value + 2\n",
        )
    head_sha = _commit(repository, "multi-target change")
    _git(base_checkout, "reset", "--hard", base_sha)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "BLOCK"
    assert {
        "MISSING_CHARACTERIZATION_COVERAGE:obligation:src/other.py::function:normalize",
        "MISSING_CHARACTERIZATION_COVERAGE:obligation:src/sample.py::function:calculate",
    }.issubset(result["policy_blocks"])
    assert result["refactor_runnability"]["targets"] == [
        "src/other.py::function:normalize:1-2",
        "src/sample.py::function:calculate:1-2",
    ]
    assert result["refactor_runnability"]["runnable"] is True


def test_deleted_source_uses_retained_characterization_only(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, deleted_source=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert result["coverage"]["required_paths"] == []
    assert [item["id"] for item in result["scenarios"]] == ["retained"]


def test_new_scenario_runs_against_same_base_and_head(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, add_scenario=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert [item["id"] for item in result["scenarios"]] == ["existing", "new-scenario"]


def test_missing_baseline_and_head_only_claim_block(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    _, head = _captures(repository, base_checkout, base_sha, head_sha)
    missing = tmp_path / "missing.json"
    _, head_path = _write_artifacts(tmp_path, {}, head)

    result = _verify(repository, base_sha, head_sha, missing, head_path)

    assert "MISSING_BASELINE" in result["policy_blocks"]
    assert "HEAD_ONLY_CHARACTERIZATION_CLAIM" in result["policy_blocks"]


def test_missing_head_is_incomplete_not_head_only(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, _ = _captures(repository, base_checkout, base_sha, head_sha)
    base_path, _ = _write_artifacts(tmp_path, base, {})

    result = _verify(repository, base_sha, head_sha, base_path, tmp_path / "missing-head.json")

    assert "INCOMPLETE_CHARACTERIZATION_EVIDENCE" in result["policy_blocks"]
    assert "HEAD_ONLY_CHARACTERIZATION_CLAIM" not in result["policy_blocks"]


def test_result_round_trip_distinguishes_success_failure_incomplete_and_malformed(
    tmp_path: Path,
) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    success_paths = _write_artifacts(tmp_path / "success", base, head)

    success = _verify(repository, base_sha, head_sha, *success_paths)
    assert success["scenarios"][0]["compatibility"] == "PASS"
    _validate_round_trip(success)
    successful_base = json.loads(json.dumps(base))
    successful_head = json.loads(json.dumps(head))

    for capture in (base, head):
        scenario = capture["scenarios"][0]
        scenario.update(behavior=None, behavior_sha256=None, error="fixture failure", exit_code=1)
        capture["behavior_fingerprint"] = characterization._sha256(
            characterization._canonical([["existing", None]])
        )
    failed_paths = _write_artifacts(tmp_path / "failed", base, head)

    failed = _verify(repository, base_sha, head_sha, *failed_paths)
    assert failed["scenarios"][0]["compatibility"] == "BLOCK"
    assert "CHARACTERIZATION_EXECUTION_FAILED:existing" in failed["policy_blocks"]
    _validate_round_trip(failed)

    incomplete_paths = _write_artifacts(tmp_path / "incomplete", successful_base, {})
    incomplete = _verify(
        repository,
        base_sha,
        head_sha,
        incomplete_paths[0],
        tmp_path / "incomplete" / "missing-head.json",
    )
    assert incomplete["scenarios"][0]["compatibility"] == "BLOCK"
    assert "INCOMPLETE_CHARACTERIZATION_EVIDENCE" in incomplete["policy_blocks"]
    _validate_round_trip(incomplete)

    malformed_capture_paths = _write_artifacts(
        tmp_path / "malformed-capture", successful_base, successful_head
    )
    _write(malformed_capture_paths[1], "{")
    characterization._provenance_path(malformed_capture_paths[1]).unlink()
    malformed_capture = _verify(repository, base_sha, head_sha, *malformed_capture_paths)
    assert malformed_capture["scenarios"][0]["compatibility"] == "BLOCK"
    assert "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE" in malformed_capture["policy_blocks"]
    _validate_round_trip(malformed_capture)

    malformed = json.loads(json.dumps(failed))
    malformed["scenarios"][0]["compatibility"] = "PASS"
    with pytest.raises(
        characterization.CharacterizationError, match="MALFORMED_CHARACTERIZATION_RESULT"
    ):
        characterization.validate_result(
            malformed,
            repository=malformed["repository"],
            base_sha=base_sha,
            head_sha=head_sha,
            workflow_sha="f" * 40,
            required_paths=tuple(malformed["coverage"]["required_paths"]),
            required_targets=tuple(malformed["refactor_runnability"]["targets"]),
            expected_artifacts=malformed["artifacts"],
        )


def test_changed_characterization_definition_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, _ = _repository(tmp_path)
    changed = _scenario("existing", "src/sample.py")
    changed["kind"] = "regression"
    _write(repository / characterization.MANIFEST_PATH, _manifest([changed]))
    head_sha = _commit(repository, "change characterization definition")
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHANGED_CHARACTERIZATION_DEFINITION:existing" in result["policy_blocks"]


def test_removed_characterization_scenario_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, _ = _repository(tmp_path)
    replacement = _scenario("replacement", ".supportability.toml")
    _write(repository / characterization.MANIFEST_PATH, _manifest([replacement]))
    _add_scenario_files(
        repository,
        "replacement",
        ".supportability.toml",
        "python",
        PYTHON_CONTRACT,
    )
    head_sha = _commit(repository, "remove characterization scenario")
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "REMOVED_CHARACTERIZATION_SCENARIO:existing" in result["policy_blocks"]


def test_same_coverage_replacement_cannot_drop_retained_obligation(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, _ = _repository(tmp_path)
    replacement = _scenario("replacement", "src/sample.py")
    _write(repository / characterization.MANIFEST_PATH, _manifest([replacement]))
    _add_scenario_files(repository, "replacement", "src/sample.py", "python", "base\n")
    extension = "py"
    (repository / f"tests/characterization/existing.characterization.{extension}").unlink()
    (repository / "tests/characterization/existing.golden.json").unlink()
    head_sha = _commit(repository, "replace scenario without retaining obligation")
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "REMOVED_CHARACTERIZATION_SCENARIO:existing" in result["policy_blocks"]


def test_stable_obligation_survives_scenario_rename(tmp_path: Path) -> None:
    repository, base_checkout, original_base, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", original_base)
    target = "src/sample.py"
    behavior = _behavior("calculate-contract")
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2(
            [_scenario("old-package", target)],
            [_obligation("calculate-contract", "old-package", target)],
        ),
    )
    _add_behavior_scenario(repository, "old-package", behavior)
    base_sha = _commit(repository, "stable obligation base")
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2(
            [_scenario("new-package", target)],
            [_obligation("calculate-contract", "new-package", target)],
        ),
    )
    _add_behavior_scenario(repository, "new-package", behavior)
    (repository / "tests/characterization/old-package.characterization.py").unlink()
    (repository / "tests/characterization/old-package.golden.json").unlink()
    head_sha = _commit(repository, "rename scenario packaging")
    _git(base_checkout, "reset", "--hard", base_sha)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert [item["id"] for item in result["obligations"]] == ["calculate-contract"]


def test_obligation_version_requires_exact_baseline_transition(tmp_path: Path) -> None:
    repository, base_checkout, original_base, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", original_base)
    target = "src/sample.py"
    old_behavior = _behavior("calculate-v1")
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2(
            [_scenario("old-package", target)],
            [_obligation("calculate-v1", "old-package", target)],
        ),
    )
    _add_behavior_scenario(repository, "old-package", old_behavior)
    base_sha = _commit(repository, "versioned obligation base")
    new_behavior = _behavior("calculate-v2")
    transition = {
        "baseline_sha256": characterization._sha256(
            characterization._canonical(old_behavior["calculate-v1"])
        ),
        "from": "calculate-v1",
        "to": ["calculate-v2"],
    }
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2(
            [_scenario("new-package", target)],
            [_obligation("calculate-v2", "new-package", target)],
            [transition],
        ),
    )
    _add_behavior_scenario(repository, "new-package", new_behavior)
    (repository / "tests/characterization/old-package.characterization.py").unlink()
    (repository / "tests/characterization/old-package.golden.json").unlink()
    head_sha = _commit(repository, "explicit obligation transition")
    _git(base_checkout, "reset", "--hard", base_sha)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert [item["id"] for item in result["obligations"]] == ["calculate-v2"]


def test_obligation_version_with_wrong_baseline_digest_blocks(tmp_path: Path) -> None:
    repository, base_checkout, original_base, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", original_base)
    target = "src/sample.py"
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2(
            [_scenario("old-package", target)],
            [_obligation("calculate-v1", "old-package", target)],
        ),
    )
    _add_behavior_scenario(repository, "old-package", _behavior("calculate-v1"))
    base_sha = _commit(repository, "versioned obligation base")
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2(
            [_scenario("new-package", target)],
            [_obligation("calculate-v2", "new-package", target)],
            [{"baseline_sha256": "0" * 64, "from": "calculate-v1", "to": ["calculate-v2"]}],
        ),
    )
    _add_behavior_scenario(repository, "new-package", _behavior("calculate-v2"))
    (repository / "tests/characterization/old-package.characterization.py").unlink()
    (repository / "tests/characterization/old-package.golden.json").unlink()
    head_sha = _commit(repository, "forged obligation transition")
    _git(base_checkout, "reset", "--hard", base_sha)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "REMOVED_CHARACTERIZATION_SCENARIO:obligation:calculate-v1" in result["policy_blocks"]


def test_stronger_additive_obligation_preserves_baseline(tmp_path: Path) -> None:
    repository, base_checkout, original_base, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", original_base)
    target = "src/sample.py"
    base_behavior = _behavior("calculate-v1")
    scenario = _scenario("behavior-package", target)
    retained = _obligation("calculate-v1", "behavior-package", target)
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2([scenario], [retained]),
    )
    _add_behavior_scenario(repository, "behavior-package", base_behavior)
    base_sha = _commit(repository, "additive obligation base")
    added = _obligation("calculate-negative-v1", "behavior-package", target)
    head_behavior = {**base_behavior, **_behavior("calculate-negative-v1", -1)}
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2([scenario], [retained, added]),
    )
    _add_behavior_scenario(repository, "behavior-package", head_behavior)
    head_sha = _commit(repository, "add stronger behavior obligation")
    _git(base_checkout, "reset", "--hard", base_sha)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert [item["id"] for item in result["obligations"]] == [
        "calculate-negative-v1",
        "calculate-v1",
    ]


def test_constant_behavior_cases_block_as_nonmeaningful_obligation(tmp_path: Path) -> None:
    repository, base_checkout, original_base, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", original_base)
    target = "src/sample.py"
    behavior = {
        "calculate-contract": [
            {"input": 0, "output": 1},
            {"input": 2, "output": 1},
        ]
    }
    _write(
        repository / characterization.MANIFEST_PATH,
        _manifest_v2(
            [_scenario("behavior-package", target)],
            [_obligation("calculate-contract", "behavior-package", target)],
        ),
    )
    _add_behavior_scenario(repository, "behavior-package", behavior)
    base_sha = _commit(repository, "constant behavior obligation")
    _write(repository / "docs/note.md", "unchanged behavior\n")
    head_sha = _commit(repository, "candidate")
    _git(base_checkout, "reset", "--hard", base_sha)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "BLOCK"
    assert "GOLDEN_BEHAVIOR_MISMATCH:obligation:calculate-contract" in result["policy_blocks"]


def test_changed_golden_output_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, changed_golden=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHANGED_GOLDEN_OUTPUT:existing" in result["policy_blocks"]


def test_unauthenticated_proof_text_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    base["authentication"] = {"claim": "trust me"}
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE" in result["policy_blocks"]


@pytest.mark.parametrize(
    ("field", "value", "block"),
    [
        (
            "kind",
            "regression",
            "CHARACTERIZATION_DEFINITION_MISMATCH:existing",
        ),
        ("exit_code", 1, "CHARACTERIZATION_EXECUTION_FAILED:existing"),
        ("golden_blob_sha", "0" * 40, "GOLDEN_ARTIFACT_IDENTITY_MISMATCH:existing"),
        ("golden_behavior_sha256", "0" * 64, "GOLDEN_BEHAVIOR_MISMATCH:existing"),
    ],
)
def test_scenario_capture_claim_mismatch_blocks(
    tmp_path: Path, field: str, value: object, block: str
) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    scenarios = head["scenarios"]
    assert isinstance(scenarios, list) and isinstance(scenarios[0], dict)
    scenarios[0][field] = value
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert block in result["policy_blocks"]


def test_characterization_fingerprint_mismatch_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    head["behavior_fingerprint"] = "0" * 64
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHARACTERIZATION_FINGERPRINT_MISMATCH" in result["policy_blocks"]


def test_stale_artifact_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    head["target_sha"] = base_sha
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "STALE_POST_CHANGE_ARTIFACT" in result["policy_blocks"]


def test_stale_baseline_artifact_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    base["target_sha"] = head_sha
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "STALE_BASELINE_ARTIFACT" in result["policy_blocks"]


def test_incompatible_behavior_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, changed_source=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:existing" in result["policy_blocks"]


def test_replay_drift_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    head["scenarios"][0]["deterministic"] = False
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHARACTERIZATION_REPLAY_DRIFT:existing" in result["policy_blocks"]


def test_environment_drift_is_explicitly_blocked(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    head["environment"]["resolved_dependencies"] = ["python:fixture==2.0:sha256:" + "3" * 64]
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHARACTERIZATION_ENVIRONMENT_DRIFT" in result["policy_blocks"]


def test_uncovered_high_risk_path_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, uncovered_high_risk=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "MISSING_CHARACTERIZATION_COVERAGE:src/risk.py" in result["policy_blocks"]


def test_wrong_driver_artifact_identity_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    head["scenarios"][0]["driver_blob_sha"] = "0" * 40
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHARACTERIZATION_DRIVER_IDENTITY_MISMATCH:existing" in result["policy_blocks"]


def test_capture_digest_mismatch_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    base_result = _verify(repository, base_sha, head_sha, *paths, base_capture_sha256="0" * 64)
    head_result = _verify(repository, base_sha, head_sha, *paths, head_capture_sha256="0" * 64)

    assert "BASE_CAPTURE_DIGEST_MISMATCH" in base_result["policy_blocks"]
    assert "HEAD_CAPTURE_DIGEST_MISMATCH" in head_result["policy_blocks"]


def test_zero_artifact_id_is_invalid(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths, base_artifact_id="0")

    assert "INVALID_ARTIFACT_IDENTITY" in result["policy_blocks"]


def test_capture_requires_github_hosted_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS")

    with pytest.raises(
        characterization.CharacterizationError,
        match="CHARACTERIZATION_REQUIRES_GITHUB_HOSTED_RUNNER",
    ):
        hosted_characterization.capture_evidence(
            tmp_path,
            tmp_path,
            base_sha="a" * 40,
            head_sha="b" * 40,
            side="base",
            repository="example/fixture",
            repository_id="123",
            workflow_sha="f" * 40,
            run_id="456",
            run_attempt="1",
            job="characterize-base",
        )


@pytest.mark.parametrize("kind", sorted(characterization.KINDS))
def test_all_characterization_types_use_same_manifest_format(kind: str) -> None:
    content = _manifest([{"id": "scenario", "kind": kind, "covers": ["src/sample.py"]}])

    parsed = characterization.parse_manifest(content.encode(), "a" * 40)

    assert parsed.scenarios[0].kind == kind


def test_verification_is_byte_deterministic(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    first = _verify(repository, base_sha, head_sha, *paths)
    second = _verify(repository, base_sha, head_sha, *paths)

    assert characterization._canonical(first) == characterization._canonical(second)
