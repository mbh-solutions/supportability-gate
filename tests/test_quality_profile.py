from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from supportability_gate import contract, git_changes, quality_profile
from supportability_gate.function_changes import ChangedFileAssessment

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
WORKFLOW_SHA = "c" * 40
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
POLICY = contract.parse_contract(
    b"""schema_version = "1.0"
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
)
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
            ("src",),
            True,
            0,
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
        "untested_areas": (),
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
) -> tuple[str, ...]:
    return quality_profile.evidence_blocks(evidence, POLICY, IDENTITY, assessments, WORKFLOW_SHA)


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
    commands = tuple(replace(item, covered_paths=("other",)) for item in _commands())
    blocks = _blocks(
        _evidence(changed_paths=("src/changed.py",), commands=commands),
        (assessment,),
    )
    assert "QUALITY_CHANGED_FILE_COVERAGE:python.ruff-lint.v1:src/changed.py" in blocks
    assert "QUALITY_HIGH_RISK_FILE_COVERAGE:python.ruff-lint.v1:src/risk.py" in blocks


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"exclusions": ("src/generated.py",)}, "QUALITY_EXCLUSION_ADDED:src/generated.py"),
        ({"maximum_complexity": 11}, "QUALITY_THRESHOLD_WEAKENING"),
        ({"production_paths": ("src/package",)}, "QUALITY_SCOPE_NARROWING"),
        ({"untested_areas": ("src/risk.py",)}, "UNTESTED_AREA:src/risk.py"),
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


def test_quality_artifact_requires_external_github_binding(tmp_path: Path) -> None:
    path = tmp_path / "quality-gates.json"
    raw = replace(_evidence(), artifact_id="", artifact_digest="", capture_sha256="")
    content = quality_profile.write_evidence(raw, path)
    authenticated = quality_profile.authenticate_evidence(
        path,
        repository="example/fixture",
        repository_id="123",
        run_id="456",
        run_attempt="1",
        job="quality-profile",
        artifact_id="789",
        artifact_digest="d" * 64,
        capture_sha256=hashlib.sha256(content).hexdigest(),
    )
    assert authenticated.artifact_id == "789"


def test_self_declared_quality_artifact_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "quality-gates.json"
    quality_profile.write_evidence(_evidence(), path)
    with pytest.raises(quality_profile.QualityProfileError) as caught:
        quality_profile.authenticate_evidence(
            path,
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


def test_fixed_environment_imports_only_the_target_source(tmp_path: Path) -> None:
    repository = tmp_path / "target"
    environment = quality_profile._fixed_environment(tmp_path / "output", repository)

    assert environment["PYTHONPATH"] == str(repository / "src")


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
def test_typescript_profile_executes_every_fixed_gate_on_hosted_runner(tmp_path: Path) -> None:
    repository = tmp_path / "typescript-target"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Fixture")
    _run_git(repository, "config", "user.email", "fixture@example.invalid")
    _run_git(repository, "remote", "add", "origin", "https://github.com/example/typescript.git")
    (repository / ".supportability.toml").write_text(
        """schema_version = "1.0"
language = "typescript"
production_paths = ["src"]
high_risk_paths = ["src/domain/model.ts"]

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
        "export function score(value: number): number {\n  return value + 1;\n}\n",
        encoding="utf-8",
        newline="\n",
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
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    evidence = quality_profile.load_evidence(output)

    assert evidence.untested_areas == ()
    assert all(command.executed and command.exit_code == 0 for command in evidence.commands)
