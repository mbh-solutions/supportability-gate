from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zlib
from dataclasses import replace
from pathlib import Path

import pytest

from supportability_gate import contract, gate_policy, git_changes, quality_profile, quality_runner
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
TYPESCRIPT_POLICY_TEXT = """schema_version = "1.0"
language = "typescript"
production_paths = ["src"]
high_risk_paths = []

[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["src"]

[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["src"]

[complexity]
adapter = "typescript.c901-equivalent-touched.v1"
maximum = 10
"""
MIXED_POLICY_TEXT = POLICY_TEXT.replace(
    'schema_version = "1.0"\nlanguage = "python"',
    'schema_version = "1.1"\nlanguages = ["python", "typescript"]',
).replace(
    '[complexity]\nadapter = "python.c901-touched.v1"\nmaximum = 10',
    """[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["src"]

[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["src"]

[complexity]
maximum = 10""",
)
IDENTITY = git_changes.RepositoryIdentity(
    "github.com/example/fixture",
    BASE_SHA,
    "d" * 40,
    HEAD_SHA,
    "e" * 40,
    "git version fixture",
)


def test_mixed_contract_selects_and_partitions_both_fixed_profiles(tmp_path: Path) -> None:
    expanded_text = MIXED_POLICY_TEXT.replace(
        'production_paths = ["src"]', 'production_paths = ["src", "web"]'
    ).replace('paths = ["src"]', 'paths = ["src", "web"]')
    policy = contract.parse_contract(expanded_text.encode())
    later = contract.parse_contract(
        expanded_text.replace(
            '["src", "web"]', '["src", "web", "supabase/functions/ask-twmn-gateway"]'
        ).encode()
    )
    narrowed = contract.parse_contract(expanded_text.replace('["src", "web"]', '["web"]').encode())
    assessments = tuple(
        ChangedFileAssessment(git_changes.ChangedPath("ADDED", None, path), False, True, True, (1,))
        for path in ("src/backend.py", "web/frontend.ts")
    )
    sources = quality_profile.source_files(
        ("src/backend.py", "src/config.json", "web/frontend.ts"), policy.language
    )
    plans = quality_runner.command_plans(
        policy.language,
        tmp_path,
        tmp_path / "evidence",
        ("tests/backend_test.py", "tests/frontend.test.ts"),
        sources,
    )

    assert policy.languages == ("python", "typescript")
    assert contract.is_profile_expansion(POLICY, policy)
    assert contract.is_profile_expansion(policy, later)
    assert not contract.is_profile_expansion(POLICY, narrowed)
    assert gate_policy.evaluate_contract(policy, assessments) == ()
    assert sources == ("src/backend.py", "web/frontend.ts")
    assert {item.adapter for item in plans} == set(
        quality_profile.required_adapters("python")
    ) | set(quality_profile.required_adapters("typescript"))
    assert all(
        all(path.endswith((".py", ".pyi")) for path in item.source_files)
        if item.adapter.startswith("python.")
        else all(path.endswith((".ts", ".tsx", ".mts", ".cts")) for path in item.source_files)
        for item in plans
    )
    test_plan = next(item for item in plans if item.adapter == "typescript.test.v1")
    architecture_plan = next(
        item for item in plans if item.adapter == "typescript.import-boundaries.v1"
    )
    build_config = json.loads((tmp_path / "evidence" / "tsconfig-build.json").read_text())
    assert "--test-coverage-include=web/frontend.ts" in test_plan.actual
    assert architecture_plan.actual[-1] == "web/frontend.ts"
    assert build_config["compilerOptions"]["rootDir"] == str(tmp_path)


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
            arguments,
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
        "asset_receipts": (),
        "production_files": ("src/risk.py",),
        "source_files": ("src/risk.py",),
        "test_files": ("tests/test_sample.py",),
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
    source_files: tuple[str, ...] = ("src/risk.py",),
    asset_receipts: tuple[quality_profile.AssetReceipt, ...] = (),
    test_files: tuple[str, ...] = ("tests/test_sample.py",),
) -> tuple[str, ...]:
    return quality_profile.evidence_blocks(
        evidence,
        POLICY,
        IDENTITY,
        assessments,
        production_files,
        source_files,
        asset_receipts,
        test_files,
        WORKFLOW_SHA,
    )


def test_complete_fixed_python_profile_passes() -> None:
    assert _blocks(_evidence()) == ()


def test_missing_required_command_blocks() -> None:
    assert "MISSING_QUALITY_COMMAND:python.ruff-lint.v1" in _blocks(
        _evidence(commands=_commands()[1:])
    )


def test_architecture_policy_exit_emits_gate_three_evidence() -> None:
    commands = tuple(
        replace(item, exit_code=1) if item.adapter == "python.import-linter.v1" else item
        for item in _commands()
    )

    assert _blocks(_evidence(commands=commands)) == (
        "ARCHITECTURE_GATE_FAILED:python.import-linter.v1",
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
    sources = ("src/changed.py", "src/risk.py")
    blocks = _blocks(
        _evidence(
            changed_paths=("src/changed.py",),
            commands=commands,
            production_files=sources,
            source_files=sources,
        ),
        (assessment,),
        production_files=sources,
        source_files=sources,
    )
    assert "QUALITY_CHANGED_FILE_COVERAGE:python.ruff-lint.v1:src/changed.py" in blocks
    assert "QUALITY_HIGH_RISK_FILE_COVERAGE:python.ruff-lint.v1:src/risk.py" in blocks


def test_incomplete_production_manifest_blocks() -> None:
    assert "QUALITY_PRODUCTION_MANIFEST_MISMATCH" in _blocks(
        _evidence(), production_files=("src/other.py", "src/risk.py")
    )


def test_incomplete_test_manifest_blocks() -> None:
    assert "QUALITY_TEST_MANIFEST_MISMATCH" in _blocks(
        _evidence(), test_files=("tests/other.py", "tests/test_sample.py")
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
        executed_arguments=dict(quality_profile.command_templates("python"))["python.pytest.v1"],
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


@pytest.mark.parametrize("field", ["observed_paths", "zero_statement_paths"])
def test_quality_evidence_rejects_asset_command_observation(tmp_path: Path, field: str) -> None:
    receipt = quality_profile.AssetReceipt(
        "src/config.json", "json", "json.stdlib.v1", "1" * 64, "PASS"
    )
    command = replace(_commands()[0], **{field: ("src/config.json",)})
    evidence = _evidence(
        asset_receipts=(receipt,),
        commands=(command, *_commands()[1:]),
        production_files=("src/config.json", "src/risk.py"),
    )
    path = tmp_path / "quality.json"
    quality_profile.write_evidence(evidence, path)

    with pytest.raises(quality_profile.QualityProfileError) as caught:
        quality_profile.load_evidence(path)

    assert caught.value.code == "MALFORMED_QUALITY_EVIDENCE"


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
    assert quality_profile.provenance_payload(_evidence())["commands"][0][
        "executed_arguments"
    ] == list(_commands()[0].executed_arguments)


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
    assert (
        "--no-editorconfig"
        in dict(quality_profile.command_templates("typescript"))["typescript.prettier.v1"]
    )


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
    rcfile = next(
        item.removeprefix("--rcfile=")
        for item in pytest_plan.actual
        if item.startswith("--rcfile=")
    )
    assert Path(rcfile) == output / "coverage.ini"
    assert (output / "coverage.ini").read_text() == "[report]\nexclude_lines =\n"
    (output / "coverage.ini").write_text("[report]\nexclude_lines =\n    .+\n")
    assert quality_runner._write_coverage_config(output) == output / "coverage.ini"
    assert (output / "coverage.ini").read_bytes() == b"[report]\nexclude_lines =\n"
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


def _png_with_ihdr(ihdr: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")
        )

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def test_mixed_production_assets_are_attested_without_entering_source_manifest(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "plugin-target"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Fixture")
    _run_git(repository, "config", "user.email", "fixture@example.invalid")
    (repository / ".supportability.toml").write_text(
        TYPESCRIPT_POLICY_TEXT,
        encoding="utf-8",
        newline="\n",
    )
    source = repository / "src"
    source.mkdir()
    (source / "index.ts").write_text("export const ready = true;\n", encoding="utf-8")
    (source / "index.css").write_text("body { color: black; }\n", encoding="utf-8")
    (source / "manifest.json").write_text('{"name":"plugin"}\n', encoding="utf-8")
    (source / "README.md").write_text("# Plugin\n", encoding="utf-8")

    png = _png_with_ihdr(b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00")
    (source / "icon.png").write_bytes(png)
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", "fixture")
    head_sha = _run_git(repository, "rev-parse", "HEAD")
    policy = contract.parse_contract((repository / ".supportability.toml").read_bytes())
    records: list[git_changes.CommandRecord] = []

    production, source_files, tests = quality_runner.profile_files(
        repository, head_sha, policy, records
    )
    receipts = quality_profile.asset_receipts(
        repository, head_sha, production, source_files, records
    )

    assert production == (
        "src/README.md",
        "src/icon.png",
        "src/index.css",
        "src/index.ts",
        "src/manifest.json",
    )
    assert source_files == ("src/index.ts",)
    assert tests == ()
    assert tuple(receipt.path for receipt in receipts) == (
        "src/README.md",
        "src/icon.png",
        "src/index.css",
        "src/manifest.json",
    )
    assert all(receipt.result == "PASS" for receipt in receipts)
    assert tuple(receipt.blob_sha256 for receipt in receipts) == tuple(
        hashlib.sha256((repository / receipt.path).read_bytes()).hexdigest() for receipt in receipts
    )


def test_valid_asset_can_pass_without_code_command_observation() -> None:
    policy = contract.parse_contract(
        TYPESCRIPT_POLICY_TEXT.replace(
            "high_risk_paths = []", 'high_risk_paths = ["src/config.json"]'
        ).encode()
    )
    receipt = quality_profile.AssetReceipt(
        "src/config.json", "json", "json.stdlib.v1", "1" * 64, "PASS"
    )
    commands = tuple(
        quality_profile.GateResult(
            adapter,
            arguments,
            quality_profile.expected_proof_kind(adapter),
            ()
            if quality_profile.expected_proof_kind(adapter) == "provisioning"
            else ("src/index.ts",),
            (),
            True,
            0,
            EMPTY_SHA,
            EMPTY_SHA,
            EMPTY_SHA,
            arguments,
        )
        for adapter, arguments in quality_profile.command_templates("typescript")
    )
    evidence = replace(
        _evidence(),
        asset_receipts=(receipt,),
        changed_paths=("src/config.json",),
        commands=commands,
        high_risk_paths=("src/config.json",),
        language="typescript",
        production_files=("src/config.json", "src/index.ts"),
        source_files=("src/index.ts",),
        test_files=(),
    )
    assessment = _assessment(None, "src/config.json", False, True)

    blocks = quality_profile.evidence_blocks(
        evidence,
        policy,
        IDENTITY,
        (assessment,),
        evidence.production_files,
        evidence.source_files,
        evidence.asset_receipts,
        evidence.test_files,
        WORKFLOW_SHA,
    )

    assert blocks == ()
    assert all(
        "src/config.json" not in (*command.observed_paths, *command.zero_statement_paths)
        for command in commands
    )


@pytest.mark.parametrize(
    ("name", "content", "result", "block"),
    [
        ("bad.json", b"{", "MALFORMED", "MALFORMED_PRODUCTION_ASSET:src/bad.json"),
        pytest.param(
            "duplicate.json",
            b'{"name":"first","name":"second"}',
            "MALFORMED",
            "MALFORMED_PRODUCTION_ASSET:src/duplicate.json",
            id="duplicate-json-keys",
        ),
        ("bad.css", b"body {\x00}", "MALFORMED", "MALFORMED_PRODUCTION_ASSET:src/bad.css"),
        ("bad.md", b"\xff", "MALFORMED", "MALFORMED_PRODUCTION_ASSET:src/bad.md"),
        (
            "bad.png",
            b"\x89PNG\r\n\x1a\n\x00",
            "MALFORMED",
            "MALFORMED_PRODUCTION_ASSET:src/bad.png",
        ),
        (
            "bad.bin",
            b"unsupported",
            "UNSUPPORTED",
            "UNSUPPORTED_PRODUCTION_ASSET:src/bad.bin",
        ),
    ],
)
def test_invalid_or_unsupported_production_asset_blocks(
    tmp_path: Path,
    name: str,
    content: bytes,
    result: str,
    block: str,
) -> None:
    repository = tmp_path / "asset-target"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Fixture")
    _run_git(repository, "config", "user.email", "fixture@example.invalid")
    (repository / ".supportability.toml").write_text(
        TYPESCRIPT_POLICY_TEXT, encoding="utf-8", newline="\n"
    )
    source = repository / "src"
    source.mkdir()
    (source / "index.ts").write_text("export const ready = true;\n", encoding="utf-8")
    (source / name).write_bytes(content)
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", "fixture")
    head_sha = _run_git(repository, "rev-parse", "HEAD")
    policy = contract.parse_contract((repository / ".supportability.toml").read_bytes())
    records: list[git_changes.CommandRecord] = []
    production, sources, tests = quality_runner.profile_files(repository, head_sha, policy, records)
    receipts = quality_profile.asset_receipts(repository, head_sha, production, sources, records)
    evidence = replace(
        _evidence(),
        asset_receipts=receipts,
        high_risk_paths=(),
        language="typescript",
        production_files=production,
        production_paths=("src",),
        source_files=sources,
        test_files=tests,
    )

    blocks = quality_profile._policy_blocks(
        evidence, policy, (), production, sources, receipts, tests
    )

    assert receipts[0].result == result
    assert block in blocks


@pytest.mark.parametrize(
    "ihdr",
    [
        pytest.param(
            b"\x00\x00\x00\x00\x00\x00\x00\x01\x08\x06\x00\x00\x00",
            id="zero-width",
        ),
        pytest.param(
            b"\x00\x00\x00\x01\x00\x00\x00\x00\x08\x06\x00\x00\x00",
            id="zero-height",
        ),
        pytest.param(
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x01\x02\x00\x00\x00",
            id="illegal-bit-depth-color-type",
        ),
        pytest.param(
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x01\x00\x00",
            id="invalid-compression",
        ),
        pytest.param(
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x01\x00",
            id="invalid-filter",
        ),
        pytest.param(
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x02",
            id="invalid-interlace",
        ),
    ],
)
def test_crc_valid_png_with_invalid_ihdr_is_malformed(ihdr: bytes) -> None:
    assert quality_profile._asset_result("png.crc.v1", _png_with_ihdr(ihdr)) == "MALFORMED"


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "forged"])
def test_asset_receipt_evidence_mismatch_blocks(mutation: str) -> None:
    receipt = quality_profile.AssetReceipt(
        "src/config.json", "json", "json.stdlib.v1", "1" * 64, "PASS"
    )
    poisoned = {
        "missing": (),
        "duplicate": (receipt, receipt),
        "forged": (replace(receipt, blob_sha256="2" * 64),),
    }[mutation]
    production = ("src/config.json", "src/risk.py")

    blocks = _blocks(
        _evidence(
            asset_receipts=poisoned,
            production_files=production,
            source_files=("src/risk.py",),
        ),
        production_files=production,
        source_files=("src/risk.py",),
        asset_receipts=(receipt,),
    )

    assert "QUALITY_ASSET_RECEIPT_MISMATCH" in blocks


def test_duplicate_asset_receipts_are_rejected_by_strict_loader(tmp_path: Path) -> None:
    receipt = quality_profile.AssetReceipt(
        "src/config.json", "json", "json.stdlib.v1", "1" * 64, "PASS"
    )
    path = tmp_path / "quality.json"
    quality_profile.write_evidence(_evidence(asset_receipts=(receipt, receipt)), path)

    with pytest.raises(quality_profile.QualityProfileError) as caught:
        quality_profile.load_evidence(path)

    assert caught.value.code == "MALFORMED_QUALITY_EVIDENCE"


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
    (repository / ".coveragerc").write_text(
        "[run]\nomit =\n    *\n\n[report]\nexclude_lines =\n    .+\n",
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
        "import os\n"
        "from pathlib import Path\n\n"
        "from sample.covered import score\n\n\n"
        "def test_score() -> None:\n"
        '    output = Path(os.environ["PYTHONPYCACHEPREFIX"]).parent\n'
        '    (output / "coverage.ini").write_text("[report]\\nexclude_lines =\\n    .+\\n")\n'
        "    assert score(1) == 2\n",
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
    assert completed.returncode != 2, completed.stderr.decode(errors="replace")
    raw_evidence = quality_profile.load_evidence(output)
    assert completed.returncode == 0, [
        (item.adapter, item.exit_code) for item in raw_evidence.commands
    ]
    evidence = replace(
        raw_evidence,
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
        evidence,
        policy,
        identity,
        (assessment,),
        evidence.production_files,
        evidence.source_files,
        evidence.asset_receipts,
        evidence.test_files,
        WORKFLOW_SHA,
    )
    test_result = next(item for item in evidence.commands if item.adapter == "python.pytest.v1")
    plans = quality_runner.command_plans(
        "python",
        repository,
        output.parent,
        ("tests/test_covered.py",),
        evidence.production_files,
    )

    assert tuple(item.arguments for item in evidence.commands) == tuple(
        plan.evidence for plan in plans
    )
    assert tuple(item.executed_arguments for item in evidence.commands) == tuple(
        plan.actual for plan in plans
    )
    assert poison not in test_result.observed_paths
    assert poison not in test_result.zero_statement_paths
    assert f"UNTESTED_AREA:{poison}" in blocks
    assert f"QUALITY_CHANGED_FILE_COVERAGE:python.pytest.v1:{poison}" in blocks
    assert f"QUALITY_HIGH_RISK_FILE_COVERAGE:python.pytest.v1:{poison}" in blocks


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
        "devDependencies": {"typescript": "5.9.3"},
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
production_paths = ["web"]
high_risk_paths = ["web/presentation/Card.tsx"]

[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["web"]

[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["web"]

[complexity]
adapter = "typescript.c901-equivalent-touched.v1"
maximum = 10
""",
        encoding="utf-8",
        newline="\n",
    )
    source = repository / "web" / "domain" / "model.ts"
    source.parent.mkdir(parents=True)
    source.write_text(
        'import { increment } from "fixture-dependency";\n\n'
        "export function score(value: number): number {\n  return value;\n}\n",
        encoding="utf-8",
        newline="\n",
    )
    component = repository / "web" / "presentation" / "Card.tsx"
    component.parent.mkdir(parents=True)
    component.write_text(
        "declare global {\n"
        "  namespace JSX {\n"
        "    interface IntrinsicElements {\n"
        "      section: { children?: unknown };\n"
        "    }\n"
        "  }\n"
        "}\n\n"
        "const React = {\n"
        "  createElement(type: string, properties: object | null, child: unknown) {\n"
        "    return { child, properties, type };\n"
        "  },\n"
        "};\n\n"
        "export default function Card({ label }: { label: string }) {\n"
        "  return <section>{label}</section>;\n"
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / "quality.test.mjs").write_text(
        'import assert from "node:assert/strict";\n'
        'import { readFileSync } from "node:fs";\n'
        'import { registerHooks } from "node:module";\n'
        'import test from "node:test";\n\n'
        'import { fileURLToPath } from "node:url";\n'
        'import ts from "typescript";\n\n'
        "registerHooks({\n"
        "  load(url, context, nextLoad) {\n"
        '    if (url.endsWith(".ts") || url.endsWith(".tsx")) {\n'
        "      const fileName = fileURLToPath(url);\n"
        '      const source = readFileSync(fileName, "utf8");\n'
        "      return {\n"
        '        format: "module",\n'
        "        shortCircuit: true,\n"
        "        source: ts.transpileModule(source, {\n"
        "          compilerOptions: {\n"
        "            inlineSourceMap: true,\n"
        "            inlineSources: true,\n"
        "            jsx: ts.JsxEmit.React,\n"
        "            module: ts.ModuleKind.ESNext,\n"
        "            target: ts.ScriptTarget.ES2022,\n"
        "          },\n"
        "          fileName,\n"
        "        }).outputText,\n"
        "      };\n"
        "    }\n"
        "    return nextLoad(url, context);\n"
        "  },\n"
        "});\n\n"
        'test("covered component", async () => {\n'
        "  const [{ score }, { default: Card }] = await Promise.all([\n"
        '    import("../web/domain/model.ts"),\n'
        '    import("../web/presentation/Card.tsx"),\n'
        "  ]);\n"
        "  assert.equal(score(1), 2);\n"
        '  assert.equal(Card({ label: " ready " }).child, "ready");\n'
        "});\n",
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
    component.write_text(
        component.read_text().replace("{label}", "{label.trim()}"), encoding="utf-8", newline="\n"
    )
    poison = "web/domain/unexecuted.ts"
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
    assert completed.returncode != 2, completed.stderr.decode(errors="replace")
    evidence = quality_profile.load_evidence(output)
    assert completed.returncode == 0, [(item.adapter, item.exit_code) for item in evidence.commands]

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

    source_files = evidence.production_files
    plans = quality_runner.command_plans(
        "typescript",
        repository,
        output.parent,
        ("tests/quality.test.mjs",),
        source_files,
    )
    assert tuple(item.arguments for item in evidence.commands) == tuple(
        plan.evidence for plan in plans
    )
    assert tuple(item.executed_arguments for item in evidence.commands) == tuple(
        plan.actual for plan in plans
    )
    install_plan = next(plan for plan in plans if plan.adapter == "typescript.target-install.v1")
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
            git_changes.ChangedPath("MODIFIED", "web/domain/model.ts", "web/domain/model.ts"),
            True,
            True,
            True,
            (2,),
        ),
        ChangedFileAssessment(
            git_changes.ChangedPath("ADDED", None, poison), False, True, True, (1,)
        ),
        ChangedFileAssessment(
            git_changes.ChangedPath(
                "MODIFIED", "web/presentation/Card.tsx", "web/presentation/Card.tsx"
            ),
            True,
            True,
            True,
            (15,),
        ),
    )
    blocks = quality_profile.evidence_blocks(
        authenticated,
        policy,
        identity,
        assessments,
        evidence.production_files,
        evidence.source_files,
        evidence.asset_receipts,
        evidence.test_files,
        WORKFLOW_SHA,
    )

    assert poison not in test_result.observed_paths
    assert "web/presentation/Card.tsx" in test_result.observed_paths
    assert not any(
        block.endswith(":web/presentation/Card.tsx")
        and block.startswith("QUALITY_HIGH_RISK_FILE_COVERAGE:")
        for block in blocks
    )
    assert f"UNTESTED_AREA:{poison}" in blocks
    assert f"QUALITY_CHANGED_FILE_COVERAGE:typescript.test.v1:{poison}" in blocks
    assert all(command.executed and command.exit_code == 0 for command in evidence.commands)
