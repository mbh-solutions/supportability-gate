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
    function_changes,
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


@pytest.mark.skipif(
    os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted",
    reason="native target execution requires hosted isolation",
)
@pytest.mark.parametrize("type_only", [False, True])
def test_hosted_typescript_import_only_does_not_prove_body_execution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], type_only: bool
) -> None:
    repo, _, _, _ = _repository(tmp_path, language="typescript")
    _write(
        repo / "package.json",
        '{"name":"coverage-fixture","version":"1.0.0","dependencies":{"typescript":"5.9.3"}}',
    )
    _write(
        repo / "src/sample.ts",
        (
            "export type Input = number;\nexport interface Shape {\n  value: number;\n}\n"
            if type_only
            else ""
        )
        + "/** Add one. */\nexport function calculate(value: number): number {\n  return 1 + value;\n}\n",
    )
    _write(
        repo / "tests/sample.test.mjs",
        """import {readFileSync} from "node:fs";
import {registerHooks} from "node:module";
import {fileURLToPath} from "node:url";
import test from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";
registerHooks({load(url, context, nextLoad) {
  if (!url.endsWith(".ts")) return nextLoad(url, context);
  const fileName = fileURLToPath(url);
  return {format:"module",shortCircuit:true,source:ts.transpileModule(readFileSync(fileName,"utf8"),{fileName,compilerOptions:{inlineSourceMap:true,inlineSources:true,module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText};
}});
test("runtime probe", async () => { const {calculate} = await import("../src/sample.ts"); assert.equal(typeof calculate,"function"); if (process.env.PROBE_CALL === "1") assert.equal(calculate(2),3); });
""",
    )
    subprocess.run(
        ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=120,
    )
    head = _commit(repo, "coverage fixture")
    dependencies = tmp_path / "trusted" / "characterization-dependencies"
    dependencies.mkdir(parents=True)
    hosted_characterization._install_node_dependencies(
        repo, head, dependencies.parent / "characterization-node", []
    )
    (repo / "node_modules").mkdir(exist_ok=True)
    hosted_characterization._prepare_container()
    driver = b"""import {spawnSync} from "node:child_process";
import {readFileSync} from "node:fs";
const behavior = {};
for (const mode of ["plain","maps","called"]) {
 const flags = mode === "plain" ? [] : ["--enable-source-maps"];
 const result = spawnSync(process.execPath,[...flags,"--test","--experimental-test-coverage","--test-coverage-include=src/sample.ts","--test-reporter=lcov","--test-reporter-destination=/work/coverage.lcov","/target/tests/sample.test.mjs"],{cwd:"/target",encoding:"utf8",env:{...process.env,PROBE_CALL:mode === "called" ? "1" : "0"}});
 behavior[mode] = {code:result.status, stderr:result.stderr, lcov:readFileSync("/work/coverage.lcov","utf8")};
}
console.log(JSON.stringify({schema_version:"1.0",scenario:"coverage",behavior}));
"""
    row = hosted_characterization._run_driver(
        repo,
        repo,
        characterization.Scenario("coverage", "regression", ("src/sample.ts",)),
        "typescript",
        driver,
        dependencies,
    )
    assert row["exit_code"] == 0, row
    behavior = row["behavior"]
    with capsys.disabled():
        print("HOSTED_TYPESCRIPT_COVERAGE_PROBE " + json.dumps(behavior, sort_keys=True))
    assert behavior["maps"]["code"] == 0, behavior
    if type_only:
        lines = behavior["maps"]["lcov"].splitlines()
        hits = int(next(line[3:] for line in lines if line.startswith("LH:")))
        executed = sum(int(line.split(",")[1]) > 0 for line in lines if line.startswith("DA:"))
        assert hits > executed, behavior
    observed, _ = quality_profile.typescript_lcov_observation(
        behavior["maps"]["lcov"],
        ("src/sample.ts",),
        Path("/target"),
        (f"src/sample.ts::function:calculate:{6 if type_only else 2}-{8 if type_only else 4}",),
    )
    assert not observed, behavior
    called, _ = quality_profile.typescript_lcov_observation(
        behavior["called"]["lcov"],
        ("src/sample.ts",),
        Path("/target"),
        (f"src/sample.ts::function:calculate:{6 if type_only else 2}-{8 if type_only else 4}",),
    )
    assert called == ("src/sample.ts",), behavior


def test_fixed_typescript_test_command_enables_original_source_coverage() -> None:
    commands = dict(quality_profile.command_templates("typescript"))
    assert "--enable-source-maps" in commands["typescript.test.v1"]


def test_runtime_module_spans_exclude_erased_types_without_hiding_executable_code() -> None:
    source = b"""declare global {
  interface Window { sample: string; }
}
// Runtime begins below.
const React = {
  createElement(value: string) {
    return value;
  },
};
export function Card() {
  return React.createElement("sample");
}
namespace Live { export const value = Card(); }
const mixed = Card(); /* same-line comment */
"""
    spans = function_changes.responsibility_spans("src/card.ts", source, set(range(1, 15)))
    retained = quality._runtime_spans("src/card.ts", source, spans)
    assert {span.name for span in retained if span.kind != "module"} == {"createElement", "Card"}
    module_lines = {
        line
        for span in retained
        if span.kind == "module"
        for line in range(span.start_line, span.end_line + 1)
    }
    assert {5, 13, 14} <= module_lines
    assert 9 not in module_lines
    erased = b"declare global { interface Window { sample: string; } }\n"
    erased_spans = function_changes.responsibility_spans("src/types.ts", erased, {1})
    assert quality._runtime_spans("src/types.ts", erased, erased_spans) == ()
