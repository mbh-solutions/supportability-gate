from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from supportability_gate import contract, git_changes, quality_profile, quality_runner
from supportability_gate.function_changes import ChangedFileAssessment

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
WORKFLOW_SHA = "c" * 40
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
POLICY_TEXT = """schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = ["src/risk.py"]

[[gates]]
adapter = "python.c901-touched.v1"
paths = ["src"]

[[gates]]
adapter = "python.import-linter.v1"
paths = ["src"]

[[gates]]
adapter = "python.mypy-strict.v1"
paths = ["src"]

[[gates]]
adapter = "python.pytest.v1"
paths = ["src"]

[[gates]]
adapter = "python.ruff-lint.v1"
paths = ["src"]

[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
"""
POLICY = contract.parse_contract(POLICY_TEXT.encode())
IDENTITY = git_changes.RepositoryIdentity(
    "github.com/example/fixture",
    BASE_SHA,
    "d" * 40,
    HEAD_SHA,
    "e" * 40,
    "git version fixture",
)


def _commands() -> tuple[quality_profile.GateResult, ...]:
    return tuple(
        quality_profile.GateResult(
            adapter,
            arguments,
            quality_profile.expected_proof_kind(adapter),
            ("src/risk.py",),
            (),
            True,
            0,
            EMPTY_SHA,
            EMPTY_SHA,
            EMPTY_SHA,
        )
        for adapter, arguments in quality_profile.command_templates("python")
    )


def _evidence(**changes: object) -> quality_profile.QualityEvidence:
    values: dict[str, object] = {
        "base_sha": BASE_SHA,
        "changed_paths": (),
        "commands": _commands(),
        "exclusions": (),
        "head_sha": HEAD_SHA,
        "high_risk_paths": ("src/risk.py",),
        "language": "python",
        "maximum_complexity": 10,
        "production_files": ("src/risk.py",),
        "production_paths": ("src",),
        "repository": "example/fixture",
        "repository_id": "123",
        "repository_remote": "github.com/example/fixture",
        "run_attempt": "1",
        "run_id": "456",
        "runner_environment": "github-hosted",
        "schema_version": quality_profile.SCHEMA_VERSION,
        "workflow_sha": WORKFLOW_SHA,
        "job": "quality-profile",
        "artifact_id": "789",
        "artifact_digest": "d" * 64,
        "capture_sha256": "e" * 64,
    }
    values.update(changes)
    return quality_profile.QualityEvidence(**values)  # type: ignore[arg-type]


def _assessment(
    old_path: str | None,
    new_path: str | None,
    base_production: bool,
    head_production: bool,
) -> ChangedFileAssessment:
    return ChangedFileAssessment(
        git_changes.ChangedPath("RENAMED", old_path, new_path),
        base_production,
        head_production,
        True,
        (1,),
    )


def _blocks(
    evidence: quality_profile.QualityEvidence,
    assessments: tuple[ChangedFileAssessment, ...] = (),
    production_files: tuple[str, ...] = ("src/risk.py",),
) -> tuple[str, ...]:
    return quality_profile.evidence_blocks(
        evidence, POLICY, IDENTITY, assessments, production_files, WORKFLOW_SHA
    )


def test_complete_fixed_python_profile_passes() -> None:
    assert _blocks(_evidence()) == ()


def test_missing_required_command_blocks() -> None:
    assert "MISSING_QUALITY_COMMAND:python.ruff-lint.v1" in _blocks(
        _evidence(commands=_commands()[1:])
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("executed", False, "DECLARED_TOOL_NOT_EXECUTED:python.ruff-lint.v1"),
        ("exit_code", 1, "QUALITY_GATE_FAILED:python.ruff-lint.v1"),
        (
            "arguments",
            ("python", "unsafe.py"),
            "QUALITY_COMMAND_VECTOR_MISMATCH:python.ruff-lint.v1",
        ),
    ],
)
def test_untrusted_command_result_blocks(field: str, value: object, code: str) -> None:
    first = replace(_commands()[0], **{field: value})
    assert code in _blocks(_evidence(commands=(first, *_commands()[1:])))


def test_uncovered_changed_and_high_risk_paths_block() -> None:
    assessment = _assessment("src/changed.py", "src/changed.py", True, True)
    commands = tuple(replace(item, observed_paths=("other",)) for item in _commands())
    blocks = _blocks(
        _evidence(changed_paths=("src/changed.py",), commands=commands),
        (assessment,),
    )
    assert "QUALITY_CHANGED_FILE_COVERAGE:python.ruff-lint.v1:src/changed.py" in blocks
    assert "QUALITY_HIGH_RISK_FILE_COVERAGE:python.ruff-lint.v1:src/risk.py" in blocks


def test_incomplete_production_manifest_blocks() -> None:
    assert "QUALITY_PRODUCTION_MANIFEST_MISMATCH" in _blocks(
        _evidence(), production_files=("src/other.py", "src/risk.py")
    )


def test_unexecuted_python_file_is_derived_as_untested() -> None:
    """A passing pytest process cannot claim a source root as execution proof."""
    pytest_result = quality_profile.GateResult(
        adapter="python.pytest.v1",
        arguments=dict(quality_profile.command_templates("python"))["python.pytest.v1"],
        proof_kind="runtime-lines",
        observed_paths=(),
        zero_statement_paths=(),
        executed=True,
        exit_code=0,
        stderr_sha256=EMPTY_SHA,
        stdout_sha256=EMPTY_SHA,
        raw_proof_sha256=EMPTY_SHA,
    )
    commands = tuple(
        pytest_result if item.adapter == "python.pytest.v1" else item for item in _commands()
    )

    blocks = _blocks(_evidence(commands=commands))

    assert "UNTESTED_AREA:src/risk.py" in blocks
    assert "QUALITY_HIGH_RISK_FILE_COVERAGE:python.pytest.v1:src/risk.py" in blocks


def test_python_coverage_observation_excludes_unexecuted_statements() -> None:
    report = {
        "files": {
            "src/sample/covered.py": {"summary": {"num_statements": 2, "covered_lines": 1}},
            "src/sample/empty.py": {"summary": {"num_statements": 0, "covered_lines": 0}},
            "src/sample/unexecuted.py": {"summary": {"num_statements": 1, "covered_lines": 0}},
        }
    }

    observed, zero_statement = quality_profile.python_coverage_observation(
        report,
        (
            "src/sample/covered.py",
            "src/sample/empty.py",
            "src/sample/unexecuted.py",
        ),
    )

    assert observed == ("src/sample/covered.py",)
    assert zero_statement == ("src/sample/empty.py",)


def test_typescript_lcov_observation_excludes_unexecuted_statements(tmp_path: Path) -> None:
    report = """TN:
SF:src/sample/covered.ts
DA:1,1
LF:1
LH:1
end_of_record
TN:
SF:src/sample/unexecuted.ts
DA:1,0
LF:1
LH:0
end_of_record
"""

    observed, zero_statement = quality_profile.typescript_lcov_observation(
        report,
        ("src/sample/covered.ts", "src/sample/unexecuted.ts"),
        tmp_path,
    )

    assert observed == ("src/sample/covered.ts",)
    assert zero_statement == ()


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"exclusions": ("src/generated.py",)}, "QUALITY_EXCLUSION_ADDED:src/generated.py"),
        ({"maximum_complexity": 11}, "QUALITY_THRESHOLD_WEAKENING"),
        ({"production_paths": ("src/package",)}, "QUALITY_SCOPE_NARROWING"),
    ],
)
def test_anti_weakening_blocks(changes: dict[str, object], code: str) -> None:
    assert code in _blocks(_evidence(**changes))


def test_production_move_outside_scope_blocks() -> None:
    assessment = _assessment("src/risk.py", "legacy/risk.py", True, False)
    evidence = _evidence(changed_paths=("src/risk.py",))
    assert "PRODUCTION_PATH_MOVED_OUTSIDE_SCOPE:legacy/risk.py" in _blocks(evidence, (assessment,))


def test_quality_evidence_is_byte_identical(tmp_path: Path) -> None:
    first = quality_profile.write_evidence(_evidence(), tmp_path / "first.json")
    second = quality_profile.write_evidence(_evidence(), tmp_path / "second.json")
    assert first == second


def test_decision_payload_excludes_run_specific_provenance() -> None:
    changed_command = replace(_commands()[0], stdout_sha256="1" * 64, raw_proof_sha256="2" * 64)
    changed = _evidence(
        run_id="999",
        commands=(changed_command, *_commands()[1:]),
        artifact_id="999",
        artifact_digest="3" * 64,
        capture_sha256="4" * 64,
    )

    assert quality_profile.decision_payload(_evidence()) == quality_profile.decision_payload(
        changed
    )


def test_quality_artifact_requires_external_github_binding(tmp_path: Path) -> None:
    path = tmp_path / "quality-gates.json"
    metadata = tmp_path / "artifact.json"
    raw = replace(_evidence(), artifact_id="", artifact_digest="", capture_sha256="")
    content = quality_profile.write_evidence(raw, path)
    metadata.write_text(
        json.dumps(
            {
                "id": 789,
                "name": "quality-profile-456-1",
                "expired": False,
                "expires_at": "2026-08-29T00:00:00Z",
                "digest": f"sha256:{'d' * 64}",
                "url": "https://api.github.com/repos/example/fixture/actions/artifacts/789",
                "workflow_run": {"id": 456, "repository_id": 123, "head_sha": HEAD_SHA},
            }
        ),
        encoding="utf-8",
    )
    verified = quality_profile.verify_evidence_binding(
        path,
        metadata_path=metadata,
        repository="example/fixture",
        repository_id="123",
        run_id="456",
        run_attempt="1",
        job="quality-profile",
        artifact_id="789",
        artifact_digest="d" * 64,
        capture_sha256=hashlib.sha256(content).hexdigest(),
    )
    assert verified.artifact_id == "789"


def test_self_declared_quality_artifact_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "quality-gates.json"
    quality_profile.write_evidence(_evidence(), path)
    with pytest.raises(quality_profile.QualityProfileError) as caught:
        quality_profile.verify_evidence_binding(
            path,
            metadata_path=tmp_path / "missing-artifact.json",
            repository="example/fixture",
            repository_id="123",
            run_id="456",
            run_attempt="1",
            job="quality-profile",
            artifact_id="789",
            artifact_digest="d" * 64,
            capture_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    assert caught.value.code == "SELF_DECLARED_QUALITY_ARTIFACT"


def test_target_profile_refuses_owner_workstation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    runner = Path(__file__).with_name("hosted_quality_profile.py")
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            str(runner),
            "--repository",
            str(Path.cwd()),
            "--repository-name",
            "example/fixture",
            "--repository-id",
            "123",
            "--base-ref",
            BASE_SHA,
            "--head-ref",
            HEAD_SHA,
            "--workflow-sha",
            WORKFLOW_SHA,
            "--run-id",
            "456",
            "--run-attempt",
            "1",
            "--output",
            str(Path.cwd() / "evidence.json"),
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        timeout=10,
    )
    assert completed.returncode == 2
    assert not hasattr(quality_profile, "run_profile")


def test_fixed_vectors_never_invoke_a_shell() -> None:
    commands = [
        command
        for language in ("python", "typescript")
        for _, command in quality_profile.command_templates(language)
    ]
    arguments = [argument for command in commands for argument in command]
    assert all(command[0] not in {"bash", "cmd", "pwsh", "sh"} for command in commands)
    assert all("$(" not in argument and "`" not in argument for argument in arguments)


def test_fixed_python_tools_use_isolation_and_generated_source_paths(tmp_path: Path) -> None:
    repository = tmp_path / "target"
    output = tmp_path / "output"
    environment = quality_runner.fixed_environment(output, repository)
    plans = quality_runner.command_plans("python", repository, output, (), ("src/sample.py",))
    pytest_plan = next(plan for plan in plans if plan.adapter == "python.pytest.v1")

    assert "PYTHONPATH" not in environment
    assert all(plan.actual[1] == "-I" for plan in plans[:-1])
    assert Path(plans[-1].actual[0]).is_absolute()
    assert pytest_plan.actual[-2:] == ("--rootdir", str(repository))
    assert "testpaths = tests" in (output / "pytest.ini").read_text()
    assert (
        f"pythonpath =\n    {repository / 'src'}\n    {repository}"
        in (output / "pytest.ini").read_text()
    )
    assert "mypy_path = src" in (output / "mypy.ini").read_text()


def _run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


@pytest.mark.skipif(
    os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted",
    reason="target quality commands are forbidden outside GitHub-hosted runners",
)
def test_python_poison_file_passes_tests_but_blocks_as_unexecuted(tmp_path: Path) -> None:
    repository = tmp_path / "python-target"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Fixture")
    _run_git(repository, "config", "user.email", "fixture@example.invalid")
    _run_git(repository, "remote", "add", "origin", "https://github.com/example/python.git")
    (repository / ".supportability.toml").write_text(
        POLICY_TEXT.replace("src/risk.py", "src/sample/unexecuted.py"),
        encoding="utf-8",
        newline="\n",
    )
    (repository / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools==83.0.0']\n"
        "build-backend = 'setuptools.build_meta'\n\n"
        "[project]\nname = 'quality-fixture'\nversion = '1.0.0'\n"
        "requires-python = '>=3.12'\n\n"
        "[tool.setuptools]\npackage-dir = {'' = 'src'}\n\n"
        "[tool.setuptools.packages.find]\nwhere = ['src']\n",
        encoding="utf-8",
        newline="\n",
    )
    package = repository / "src" / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "covered.py").write_text(
        "def score(value: int) -> int:\n    return value + 1\n", encoding="utf-8"
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_covered.py").write_text(
        "from sample.covered import score\n\n\ndef test_score() -> None:\n    assert score(1) == 2\n",
        encoding="utf-8",
    )
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", "base")
    base_sha = _run_git(repository, "rev-parse", "HEAD")
    poison = "src/sample/unexecuted.py"
    (repository / poison).write_text(
        'raise RuntimeError("poison file executed")\n', encoding="utf-8"
    )
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", "head")
    head_sha = _run_git(repository, "rev-parse", "HEAD")
    output = tmp_path / "evidence" / "quality-gates.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            str(Path(__file__).with_name("hosted_quality_profile.py")),
            "--repository",
            str(repository.resolve()),
            "--repository-name",
            "example/python",
            "--repository-id",
            "123",
            "--base-ref",
            base_sha,
            "--head-ref",
            head_sha,
            "--workflow-sha",
            WORKFLOW_SHA,
            "--run-id",
            "456",
            "--run-attempt",
            "1",
            "--output",
            str(output.resolve()),
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    evidence = replace(
        quality_profile.load_evidence(output),
        artifact_id="789",
        artifact_digest="d" * 64,
        capture_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
    )
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, [])
    policy = contract.parse_contract((repository / ".supportability.toml").read_bytes())
    assessment = ChangedFileAssessment(
        git_changes.ChangedPath("ADDED", None, poison), False, True, True, (1,)
    )
    blocks = quality_profile.evidence_blocks(
        evidence, policy, identity, (assessment,), evidence.production_files, WORKFLOW_SHA
    )
    test_result = next(item for item in evidence.commands if item.adapter == "python.pytest.v1")

    assert poison not in test_result.observed_paths
    assert f"UNTESTED_AREA:{poison}" in blocks
    assert f"QUALITY_CHANGED_FILE_COVERAGE:python.pytest.v1:{poison}" in blocks


@pytest.mark.skipif(
    os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted",
    reason="target quality commands are forbidden outside GitHub-hosted runners",
)
def test_typescript_profile_executes_every_fixed_gate_on_hosted_runner(tmp_path: Path) -> None:
    repository = tmp_path / "typescript-target"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Fixture")
    _run_git(repository, "config", "user.email", "fixture@example.invalid")
    _run_git(repository, "remote", "add", "origin", "https://github.com/example/typescript.git")
    package = {
        "name": "typescript-target",
        "private": True,
        "version": "1.0.0",
        "type": "module",
        "scripts": {
            "preinstall": "node -e \"require('node:fs').writeFileSync('root-script-ran', '')\"",
            "test": "node -e \"require('node:fs').writeFileSync('target-command-ran', '')\"",
        },
        "dependencies": {"fixture-dependency": "file:vendor/fixture-dependency"},
    }
    (repository / "package.json").write_text(
        json.dumps(package, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    dependency = repository / "vendor" / "fixture-dependency"
    dependency.mkdir(parents=True)
    (dependency / "package.json").write_text(
        json.dumps(
            {
                "name": "fixture-dependency",
                "version": "1.0.0",
                "type": "module",
                "main": "index.js",
                "types": "index.d.ts",
                "scripts": {
                    "preinstall": "node -e \"require('node:fs').writeFileSync('dependency-script-ran', '')\""
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (dependency / "index.js").write_text(
        "export const increment = (value) => value + 1;\n", encoding="utf-8", newline="\n"
    )
    (dependency / "index.d.ts").write_text(
        "export function increment(value: number): number;\n", encoding="utf-8", newline="\n"
    )
    lock = subprocess.run(
        (
            shutil.which("npm") or "npm",
            "install",
            "--package-lock-only",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ),
        cwd=repository,
        check=False,
        capture_output=True,
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    assert lock.returncode == 0, lock.stderr.decode(errors="replace")
    (repository / ".supportability.toml").write_text(
        """schema_version = "1.0"
language = "typescript"
production_paths = ["src"]
high_risk_paths = ["src/domain/unexecuted.ts"]

[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["src"]

[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["src"]

[complexity]
adapter = "typescript.c901-equivalent-touched.v1"
maximum = 10
""",
        encoding="utf-8",
        newline="\n",
    )
    source = repository / "src" / "domain" / "model.ts"
    source.parent.mkdir(parents=True)
    source.write_text(
        'import { increment } from "fixture-dependency";\n\n'
        "export function score(value: number): number {\n  return value;\n}\n",
        encoding="utf-8",
        newline="\n",
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / "quality.test.mjs").write_text(
        'import assert from "node:assert/strict";\n'
        'import test from "node:test";\n\n'
        'import { score } from "../src/domain/model.ts";\n\n'
        'test("score", () => {\n  assert.equal(score(1), 2);\n});\n',
        encoding="utf-8",
        newline="\n",
    )
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", "base")
    base_sha = _run_git(repository, "rev-parse", "HEAD")
    source.write_text(
        'import { increment } from "fixture-dependency";\n\n'
        "export function score(value: number): number {\n  return increment(value);\n}\n",
        encoding="utf-8",
        newline="\n",
    )
    poison = "src/domain/unexecuted.ts"
    (repository / poison).write_text(
        'throw new Error("poison file executed");\n', encoding="utf-8", newline="\n"
    )
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", "head")
    head_sha = _run_git(repository, "rev-parse", "HEAD")

    output = tmp_path / "evidence" / "quality-gates.json"
    runner = Path(__file__).with_name("hosted_quality_profile.py")
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            str(runner),
            "--repository",
            str(repository.resolve()),
            "--repository-name",
            "example/typescript",
            "--repository-id",
            "123",
            "--base-ref",
            base_sha,
            "--head-ref",
            head_sha,
            "--workflow-sha",
            WORKFLOW_SHA,
            "--run-id",
            "456",
            "--run-attempt",
            "1",
            "--output",
            str(output.resolve()),
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    evidence = quality_profile.load_evidence(output)

    install_result = next(
        item for item in evidence.commands if item.adapter == "typescript.target-install.v1"
    )
    test_result = next(item for item in evidence.commands if item.adapter == "typescript.test.v1")
    assert install_result.exit_code == 0
    assert not (repository / "root-script-ran").exists()
    assert not (repository / "target-command-ran").exists()
    assert not (
        repository / "node_modules" / "fixture-dependency" / "dependency-script-ran"
    ).exists()

    source_files = ("src/domain/model.ts", poison)
    install_plan = next(
        plan
        for plan in quality_runner.command_plans(
            "typescript",
            repository,
            output.parent,
            ("tests/quality.test.mjs",),
            source_files,
        )
        if plan.adapter == "typescript.target-install.v1"
    )
    package_text = (repository / "package.json").read_text(encoding="utf-8")
    lock_text = (repository / "package-lock.json").read_text(encoding="utf-8")

    def install_exit() -> int:
        return subprocess.run(
            install_plan.actual,
            cwd=repository,
            env=quality_runner.fixed_environment(output.parent, repository),
            check=False,
            capture_output=True,
            timeout=quality_profile.TIMEOUT_SECONDS,
        ).returncode

    (repository / "package-lock.json").unlink()
    assert install_exit() != 0
    (repository / "package-lock.json").write_text("{", encoding="utf-8", newline="\n")
    assert install_exit() != 0
    (repository / "package-lock.json").write_text(lock_text, encoding="utf-8", newline="\n")
    out_of_sync = json.loads(package_text)
    out_of_sync["dependencies"]["missing-lock-entry"] = "1.0.0"
    (repository / "package.json").write_text(
        json.dumps(out_of_sync, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    assert install_exit() != 0
    (repository / "package.json").write_text(package_text, encoding="utf-8", newline="\n")
    (repository / "package-lock.json").write_text(lock_text, encoding="utf-8", newline="\n")
    authenticated = replace(
        evidence,
        artifact_id="789",
        artifact_digest="d" * 64,
        capture_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
    )
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, [])
    policy = contract.parse_contract((repository / ".supportability.toml").read_bytes())
    assessments = (
        ChangedFileAssessment(
            git_changes.ChangedPath("MODIFIED", "src/domain/model.ts", "src/domain/model.ts"),
            True,
            True,
            True,
            (2,),
        ),
        ChangedFileAssessment(
            git_changes.ChangedPath("ADDED", None, poison), False, True, True, (1,)
        ),
    )
    blocks = quality_profile.evidence_blocks(
        authenticated,
        policy,
        identity,
        assessments,
        evidence.production_files,
        WORKFLOW_SHA,
    )

    assert poison not in test_result.observed_paths
    assert f"UNTESTED_AREA:{poison}" in blocks
    assert f"QUALITY_CHANGED_FILE_COVERAGE:typescript.test.v1:{poison}" in blocks
    assert all(command.executed and command.exit_code == 0 for command in evidence.commands)
