"""Regression proof for Node prerequisites and revision-correct runtime obligations."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
from test_characterization import _commit, _repository, _write, hosted_characterization

from supportability_gate import (
    characterization,
    contract,
    git_changes,
    quality_profile,
    refactor_targets,
)

_SPEC = importlib.util.spec_from_file_location(
    "runtime_quality", Path(__file__).with_name("hosted_quality_profile.py")
)
assert _SPEC and _SPEC.loader
quality = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(quality)


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_runtime_targets_exclude_deleted_bodies_but_preserve_review_targets(
    tmp_path: Path, language: str
) -> None:
    repo, _, _, _ = _repository(tmp_path, language=language)
    path = "src/sample.py" if language == "python" else "src/sample.ts"
    base_text = "def removed():\n    return 1\n\ndef retained():\n    return 2\n    return 3\n"
    head_text = "def retained():\n    return 2\n"
    if language == "typescript":
        base_text = "export function removed() {\n  return 1;\n}\nexport function retained() {\n  return 2;\n  return 3;\n}\n"
        head_text = "export function retained() {\n  return 2;\n}\n"
    _write(repo / path, base_text)
    base = _commit(repo, "base responsibilities")
    _write(repo / path, head_text)
    head = _commit(repo, "remove function and dead line")
    records: list[git_changes.CommandRecord] = []
    identity = git_changes.inspect_repository(repo, base, head, records)
    policy = contract.parse_contract((repo / ".supportability.toml").read_bytes())
    changes = git_changes.changed_paths(repo, base, head, records)
    runtime = quality._runtime_test_targets(repo, identity, policy, changes, (path,), records)
    review, _ = refactor_targets.derive(repo, identity, policy, changes, records)
    assert any("removed:" in target for target in review)
    assert not any("removed:" in target for target in runtime)
    assert any("retained:1-" in target for target in runtime)
    assert all(
        quality_profile._span_observed(quality_profile._target_span(target, path), {2})
        for target in runtime
    )
    assert not all(
        quality_profile._span_observed(quality_profile._target_span(target, path), {1})
        for target in runtime
    )


@pytest.mark.parametrize("change", ["rename", "delete", "add"])
def test_runtime_targets_follow_head_paths(tmp_path: Path, change: str) -> None:
    repo, _, _, _ = _repository(tmp_path)
    _write(repo / "src/sample.py", "def calculate():\n    return 1\n")
    base = _commit(repo, "base")
    if change == "rename":
        (repo / "src/sample.py").rename(repo / "src/moved.py")
        sources = ("src/moved.py",)
    elif change == "delete":
        (repo / "src/sample.py").unlink()
        sources = ()
    else:
        _write(repo / "src/new.py", "def introduced():\n    return 2\n")
        sources = ("src/sample.py", "src/new.py")
    head = _commit(repo, change)
    records: list[git_changes.CommandRecord] = []
    identity = git_changes.inspect_repository(repo, base, head, records)
    policy = contract.parse_contract((repo / ".supportability.toml").read_bytes())
    changes = git_changes.changed_paths(repo, base, head, records)
    targets = quality._runtime_test_targets(repo, identity, policy, changes, sources, records)
    assert all(target.split("::")[0] in sources for target in targets)
    assert bool(targets) == (change != "delete")
    if change == "add":
        assert any("introduced" in target for target in targets)
        assert any("calculate" in target for target in targets)  # unchanged high-risk body


@pytest.mark.parametrize(
    "entry",
    [
        {"link": True},
        {"resolved": "git+https://example.com/repo"},
        {"resolved": "file:../escape"},
        {"resolved": "https://registry.npmjs.org/x"},
    ],
)
def test_node_lock_rejects_unverifiable_or_executable_fetches(entry: dict[str, object]) -> None:
    raw = json.dumps({"lockfileVersion": 3, "packages": {"": {}, "node_modules/x": entry}}).encode()
    with pytest.raises(
        characterization.CharacterizationError, match="CHARACTERIZATION_NODE_LOCK_UNSUPPORTED"
    ):
        hosted_characterization._validate_node_lock(raw)


def test_node_install_uses_committed_manifests_and_no_target_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _, _ = _repository(tmp_path)
    _write(repo / "package.json", '{"name":"fixture","version":"1.0.0"}')
    _write(
        repo / "package-lock.json",
        '{"lockfileVersion":3,"packages":{"":{"name":"fixture","version":"1.0.0"}}}',
    )
    _write(repo / ".npmrc", "registry=https://untrusted.invalid\n")
    head = _commit(repo, "locked prerequisites")
    _write(repo / "package.json", "dirty untrusted bytes")
    destination = tmp_path / "dependencies"
    monkeypatch.setenv("NODE_OPTIONS", "--require=untrusted")
    monkeypatch.setenv("NPM_TOKEN", "secret")
    calls = []

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        assert kwargs["cwd"] == destination
        assert kwargs["capture_output"] and kwargs["timeout"] == 120
        assert "NODE_OPTIONS" not in kwargs["env"] and "NPM_TOKEN" not in kwargs["env"]
        environment = kwargs["env"]
        assert environment["NPM_CONFIG_USERCONFIG"] != environment["NPM_CONFIG_GLOBALCONFIG"]
        assert Path(environment["NPM_CONFIG_GLOBALCONFIG"]).read_bytes() == b""
        assert not (destination / ".npmrc").exists()
        assert json.loads((destination / "package.json").read_bytes())["name"] == "fixture"
        assert "--ignore-scripts" in arguments and "--include=dev" in arguments
        return subprocess.CompletedProcess(arguments, 0, b"", b"")

    # Keep real Git blob reads; intercept only the fixed npm vector.
    original = subprocess.run
    monkeypatch.setattr(
        hosted_characterization.subprocess,
        "run",
        lambda args, **kwargs: run(args, **kwargs) if "ci" in args else original(args, **kwargs),
    )
    receipts = hosted_characterization._install_node_dependencies(repo, head, destination, [])
    assert len(calls) == 1
    assert receipts[0].startswith("npm-characterization-lock:sha256:")


@pytest.mark.skipif(
    os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted",
    reason="native target execution requires hosted isolation",
)
def test_hosted_node_dependencies_are_readonly_and_network_remains_disabled(tmp_path: Path) -> None:
    repo, _, _, _ = _repository(tmp_path, language="typescript")
    _write(
        repo / "package.json",
        json.dumps(
            {
                "name": "fixture",
                "version": "1.0.0",
                "scripts": {"preinstall": 'node -e "process.exit(99)"'},
                "dependencies": {"is-number": "7.0.0"},
            }
        ),
    )
    subprocess.run(
        ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=120,
    )
    head = _commit(repo, "locked node fixture")
    dependencies = tmp_path / "trusted" / "characterization-dependencies"
    dependencies.mkdir(parents=True)
    receipts = hosted_characterization._install_node_dependencies(
        repo, head, dependencies.parent / "characterization-node", []
    )
    assert any("npm-characterization-installed:" in value for value in receipts)
    (repo / "node_modules").mkdir(exist_ok=True)
    hosted_characterization._prepare_container()
    driver = b"""import {createRequire} from "node:module";
import {writeFileSync} from "node:fs";
import net from "node:net";
const target = createRequire("/target/package.json")("is-number");
const definition = createRequire("/definition/package.json")("is-number");
let denied = false;
try { writeFileSync("/target/node_modules/is-number/index.js", "poison"); } catch { denied = true; }
const network = await new Promise(resolve => { const s = net.connect(443, "1.1.1.1"); s.on("connect", () => {s.destroy(); resolve(true)}); s.on("error", () => resolve(false)); s.setTimeout(1000, () => {s.destroy(); resolve(false)}); });
console.log(JSON.stringify({schema_version:"1.0", scenario:"locked", behavior:{number:target(3), text:definition("no"), denied, network}}));
"""
    scenario = characterization.Scenario("locked", "regression", ("src/sample.ts",))
    row = hosted_characterization._run_driver(
        repo, repo, scenario, "typescript", driver, dependencies
    )
    assert row["exit_code"] == 0
    assert row["behavior"] == {"number": True, "text": False, "denied": True, "network": False}
