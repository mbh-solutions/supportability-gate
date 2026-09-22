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
    build_config = json.loads(
        (tmp_path / "evidence" / "trusted" / "tsconfig-build.json").read_text()
    )
    assert "--test-coverage-include=web/frontend.ts" in test_plan.actual
    assert architecture_plan.actual[-1] == "web/frontend.ts"
    assert build_config["compilerOptions"]["rootDir"] == "/target"


def test_mixed_profile_can_retire_to_either_existing_fixed_profile() -> None:
    mixed = contract.parse_contract(MIXED_POLICY_TEXT.encode())
    typescript = contract.parse_contract(TYPESCRIPT_POLICY_TEXT.encode())
    deleted_risk = (git_changes.ChangedPath("DELETED", "src/risk.py", None),)

    assert gate_policy.is_profile_retirement(mixed, POLICY, ())
    assert gate_policy.is_profile_retirement(mixed, typescript, deleted_risk)
    assert not gate_policy.is_profile_retirement(
        mixed,
        contract.parse_contract(TYPESCRIPT_POLICY_TEXT.replace('["src"]', '["web"]').encode()),
        deleted_risk,
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


def test_python_import_only_coverage_is_not_meaningful_behavior_proof() -> None:
    report = {
        "files": {
            "src/sample.py": {
                "executed_lines": [1],
                "summary": {"num_statements": 2, "covered_lines": 1},
            }
        }
    }

    observed, zero_statement = quality_profile.python_coverage_observation(
        report,
        ("src/sample.py",),
        ("src/sample.py::function:explode:1-2",),
    )

    assert observed == ()
    assert zero_statement == ()


def test_python_targeted_body_execution_is_meaningful_runtime_proof() -> None:
    report = {
        "files": {
            "src/sample.py": {
                "executed_lines": [1, 2],
                "summary": {"num_statements": 2, "covered_lines": 2},
            }
        }
    }

    observed, zero_statement = quality_profile.python_coverage_observation(
        report,
        ("src/sample.py",),
        ("src/sample.py::function:calculate:1-2",),
    )

    assert observed == ("src/sample.py",)
    assert zero_statement == ()


def test_python_malformed_runtime_trace_fails_closed() -> None:
    report = {
        "files": {
            "src/sample.py": {
                "executed_lines": ["2"],
                "summary": {"num_statements": 2, "covered_lines": 1},
            }
        }
    }

    with pytest.raises(quality_profile.QualityProfileError, match="src/sample.py"):
        quality_profile.python_coverage_observation(
            report,
            ("src/sample.py",),
            ("src/sample.py::function:calculate:1-2",),
        )


def test_python_missing_required_runtime_trace_fails_closed() -> None:
    report = {
        "files": {
            "src/sample.py": {
                "summary": {"num_statements": 2, "covered_lines": 2},
            }
        }
    }

    with pytest.raises(quality_profile.QualityProfileError, match="src/sample.py"):
        quality_profile.python_coverage_observation(
            report,
            ("src/sample.py",),
            ("src/sample.py::function:calculate:1-2",),
        )


def test_python_module_and_one_line_function_targets_can_be_observed() -> None:
    report = {
        "files": {
            "src/sample.py": {
                "executed_lines": [1, 3],
                "summary": {"num_statements": 2, "covered_lines": 2},
            }
        }
    }

    observed, _ = quality_profile.python_coverage_observation(
        report,
        ("src/sample.py",),
        (
            "src/sample.py::module:src/sample.py:1-1",
            "src/sample.py::function:ready:3-3",
        ),
    )

    assert observed == ("src/sample.py",)


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
    "report",
    [
        "SF:src/sample.ts\nDA:1,1\nLF:not-a-number\nLH:1\nend_of_record\n",
        "SF:src/sample.ts\nDA:1,1\nLF:1\nLH:1\n",
        "SF:src/sample.ts\nDA:1,1\nLF:2\nLH:2\nend_of_record\n",
    ],
)
def test_typescript_malformed_runtime_trace_fails_closed(tmp_path: Path, report: str) -> None:
    with pytest.raises(quality_profile.QualityProfileError) as captured:
        quality_profile.typescript_lcov_observation(
            report,
            ("src/sample.ts",),
            tmp_path,
            ("src/sample.ts::function:calculate:1-2",),
        )
    assert captured.value.code == "MALFORMED_QUALITY_PROOF"


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


@pytest.mark.parametrize(
    ("path", "content", "adapters", "expected"),
    [
        (
            "src/sample.py",
            b"# ruff: noqa\n# mypy: ignore-errors\ndef broken() -> int:\n    return missing\n",
            ("python.ruff-lint.v1", "python.mypy-strict.v1"),
            {
                ("python.ruff-lint.v1", 1, "blanket", "ruff-file-noqa"),
                ("python.mypy-strict.v1", 2, "blanket", "mypy-ignore-errors"),
            },
        ),
        (
            "src/sample.py",
            b"# ruff: noqa: F821\ndef broken() -> int:\n    return missing  # type: ignore[name-defined]\n",
            ("python.ruff-lint.v1", "python.mypy-strict.v1"),
            {
                ("python.ruff-lint.v1", 1, "region", "ruff-file-noqa-codes"),
                ("python.mypy-strict.v1", 3, "narrow", "mypy-line-ignore"),
            },
        ),
        (
            "src/sample.ts",
            b"// @TS-NOCHECK\n/* eslint-disable no-debugger */\n"
            b"debugger;\n/* eslint-enable no-debugger */\n",
            ("typescript.eslint.v1", "typescript.typecheck.v1"),
            {
                ("typescript.typecheck.v1", 1, "blanket", "typescript-ts-nocheck"),
                ("typescript.eslint.v1", 2, "region", "eslint-disable-region"),
            },
        ),
        (
            "src/sample.ts",
            b"// @ts-ignore\nconst broken: string = 1;\n"
            b"// eslint-disable-next-line no-debugger\ndebugger;\n",
            ("typescript.eslint.v1", "typescript.typecheck.v1"),
            {
                ("typescript.typecheck.v1", 1, "narrow", "typescript-ts-ignore"),
                ("typescript.eslint.v1", 3, "narrow", "eslint-disable-next-line"),
            },
        ),
        (
            "src/variants.py",
            b"#ruff:noqa\n# RUFF: NOQA\n#mypy: ignore-errors\n"
            b"# MYPY: IGNORE-ERRORS\n# mypy: disable-error-code = name-defined\n"
            b"value = missing\n",
            ("python.ruff-lint.v1", "python.mypy-strict.v1"),
            {
                ("python.ruff-lint.v1", 1, "blanket", "ruff-file-noqa"),
                ("python.mypy-strict.v1", 5, "region", "mypy-file-control"),
            },
        ),
        (
            "src/variants.ts",
            b"//    @TS-NOCHECK\n/* eslint-disable no-debugger */\n"
            b"/* eslint-enable no-debugger */\nconst broken: string = 1;\n",
            ("typescript.eslint.v1", "typescript.typecheck.v1"),
            {
                ("typescript.typecheck.v1", 1, "blanket", "typescript-ts-nocheck"),
                ("typescript.eslint.v1", 2, "region", "eslint-disable-region"),
            },
        ),
        (
            "src/placement.ts",
            b"const text = '/* eslint-disable */';\n// @ts-nocheck\n"
            b"/* ESLINT-DISABLE */\nconst broken: string = 1;\n",
            ("typescript.eslint.v1", "typescript.typecheck.v1"),
            set(),
        ),
    ],
)
def test_pinned_suppression_grammar_is_classified(
    path: str,
    content: bytes,
    adapters: tuple[str, ...],
    expected: set[tuple[str, int, str, str]],
) -> None:
    records = quality_profile.suppression_records(path, content, "head", adapters)

    assert {
        (record.adapter, record.line, record.scope, record.reason)
        for record in map(quality_profile.parse_suppression_record, records)
    } == expected


def test_blanket_and_region_suppressions_block_but_narrow_annotations_remain_visible() -> None:
    digest = "1" * 64
    records = tuple(
        quality_profile.encode_suppression_record(record)
        for record in (
            quality_profile.SuppressionRecord(
                "python.ruff-lint.v1",
                1,
                "src/risk.py",
                "ruff-file-noqa",
                "blanket",
                "base",
                digest,
            ),
            quality_profile.SuppressionRecord(
                "python.ruff-lint.v1",
                1,
                "src/risk.py",
                "ruff-file-noqa",
                "blanket",
                "head",
                digest,
            ),
            quality_profile.SuppressionRecord(
                "python.mypy-strict.v1",
                3,
                "src/risk.py",
                "mypy-line-ignore",
                "narrow",
                "head",
                digest,
            ),
        )
    )

    assert _blocks(_evidence(exclusions=records)) == (
        "QUALITY_BLANKET_SUPPRESSION:python.ruff-lint.v1:src/risk.py:1:ruff-file-noqa",
    )
    assert quality_profile.suppression_policy_blocks(records[:1]) == ()


def test_malformed_suppression_analysis_fails_closed() -> None:
    with pytest.raises(quality_profile.QualityProfileError) as caught:
        _blocks(_evidence(exclusions=('suppression:{"side":"head"}',)))

    assert caught.value.code == "MALFORMED_SUPPRESSION_ANALYSIS"


@pytest.mark.parametrize(
    ("path", "content"), [("src/bad.py", b"# coding: unknown\n"), ("src/bad.ts", b"\xff")]
)
def test_unreadable_suppression_source_fails_closed(path: str, content: bytes) -> None:
    with pytest.raises(quality_profile.QualityProfileError) as caught:
        quality_profile.suppression_records(path, content, "head", ())

    assert caught.value.code == "MALFORMED_SUPPRESSION_ANALYSIS"


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


def test_isolated_profile_timeout_covers_the_full_bounded_suite() -> None:
    assert quality_profile.TIMEOUT_SECONDS == 600


def test_sandbox_vector_has_fixed_read_only_and_resource_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "target"
    collector = tmp_path / "collector"
    toolcache = tmp_path / "toolcache"
    output = tmp_path / "supervisor"
    for directory in (repository, collector, toolcache, quality_runner.trusted_directory(output)):
        directory.mkdir(parents=True)
    fixed_tools = tmp_path / "fixed-tools"
    fixed_tools.mkdir()
    git = fixed_tools / "git"
    node = fixed_tools / "node"
    git.write_bytes(b"git")
    node.write_bytes(b"node")
    git_core = tmp_path / "git-core"
    git_share = tmp_path / "git-share"
    git_core.mkdir()
    git_share.mkdir()
    monkeypatch.setattr(
        quality_runner.shutil,
        "which",
        lambda name: str(git if name == "git" else node if name == "node" else name),
    )
    monkeypatch.setattr(
        quality_runner,
        "_GIT_SUPPORT_DIRECTORIES",
        ((git_core, "/usr/lib/git-core"), (git_share, "/usr/share/git-core")),
    )
    trusted = quality_runner.trusted_directory(output)
    (trusted / "quality-tools").mkdir()
    (trusted / "coverage.ini").write_text("[report]\n", encoding="utf-8")
    plan = quality_runner.CommandPlan(
        "python.pytest.v1",
        ("/opt/hostedtoolcache/python", "-I", "-m", "pytest"),
        (),
        "runtime-lines",
        (),
    )

    command = quality_runner.sandbox_command(
        plan,
        repository=repository,
        output=output,
        collector=collector,
        toolcache=toolcache,
    )

    assert "--init" in command
    assert ("--read-only", "--network", "none", "--cap-drop", "ALL") == command[8:13]
    assert "no-new-privileges" in command
    assert quality_runner.CONTAINER_IMAGE in command
    assert quality_runner.CONTAINER_PLATFORM in command
    assert quality_runner.CONTAINER_MEMORY in command
    assert quality_runner.CONTAINER_CPUS in command
    assert quality_runner.CONTAINER_PIDS_LIMIT in command
    mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
    assert any("dst=/target,readonly" in item for item in mounts)
    assert any("dst=/trusted,readonly" in item for item in mounts)
    assert any("dst=/collector,readonly" in item for item in mounts)
    assert any("dst=/evidence,readonly" in item for item in mounts)
    assert any("dst=/work" in item and not item.endswith(",readonly") for item in mounts)
    assert any("dst=/work/coverage.ini,readonly" in item for item in mounts)
    assert any("dst=/work/quality-tools,readonly" in item for item in mounts)
    assert any("dst=/usr/bin/git,readonly" in item for item in mounts)
    assert any("dst=/usr/lib/git-core,readonly" in item for item in mounts)
    assert any("dst=/usr/share/git-core,readonly" in item for item in mounts)
    assert not any("TOKEN=" in item or "SECRET=" in item for item in command)


def test_sandbox_command_creates_every_mount_source(tmp_path: Path) -> None:
    repository = tmp_path / "target"
    collector = tmp_path / "collector"
    toolcache = tmp_path / "toolcache"
    output = tmp_path / "supervisor"
    for directory in (repository, collector, toolcache):
        directory.mkdir()
    plan = quality_runner.CommandPlan(
        "characterization-scenario",
        ("python3.12", "--version"),
        (),
        "provisioning",
        (),
    )

    quality_runner.sandbox_command(
        plan,
        repository=repository,
        output=output,
        collector=collector,
        toolcache=toolcache,
    )

    assert quality_runner.trusted_directory(output).is_dir()


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
    trusted = output / "trusted"
    assert Path(rcfile) == output / "coverage.ini"
    sandbox = quality_runner.sandbox_command(
        pytest_plan,
        repository=repository,
        output=output,
        collector=Path(__file__).parent,
    )
    assert sandbox[-2:] == ("--rootdir", "/target")
    assert "--rcfile=/work/coverage.ini" in sandbox
    assert (output / "coverage.ini").read_text() == "[report]\nexclude_lines =\n"
    assert (trusted / "coverage.ini").read_text() == "[report]\nexclude_lines =\n"
    (trusted / "coverage.ini").write_text("[report]\nexclude_lines =\n    .+\n")
    assert quality_runner._write_coverage_config(trusted) == trusted / "coverage.ini"
    assert (trusted / "coverage.ini").read_bytes() == b"[report]\nexclude_lines =\n"
    assert "testpaths = tests" in (trusted / "pytest.ini").read_text()
    assert "pythonpath =\n    /target/src\n    /target" in (output / "pytest.ini").read_text()
    trusted_pytest = (trusted / "pytest.ini").read_text().replace("\\", "/")
    assert "pythonpath =\n    /target/src\n    /target" in trusted_pytest
    assert "mypy_path = src" in (output / "trusted" / "mypy.ini").read_text()


def test_typescript_configs_share_the_read_only_tool_mount(tmp_path: Path) -> None:
    repository = tmp_path / "target"
    output = tmp_path / "output"
    repository.mkdir()

    plans = quality_runner.command_plans(
        "typescript", repository, output, (), ("web/domain/model.ts",)
    )

    for tools in (output / "quality-tools", output / "trusted" / "quality-tools"):
        assert (tools / "eslint.config.mjs").is_file()
        assert (tools / "prettier.json").read_text() == "{}\n"
        assert (tools / "prettier.ignore").is_file()
        assert (tools / "dependency-cruiser.json").is_file()
    eslint = next(plan for plan in plans if plan.adapter == "typescript.eslint.v1")
    sandbox = quality_runner.sandbox_command(
        eslint,
        repository=repository,
        output=output,
        collector=Path(__file__).parent,
    )
    assert "/work/quality-tools/eslint.config.mjs" in sandbox


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


HOSTILE_CAPTURE_FIXTURES = (
    "source_blank",
    "source_restore",
    "tool_overwrite",
    "collector_overwrite",
    "evidence_overwrite",
)

HOSTILE_CAPTURE_TESTS = {
    "source_blank": (
        "from pathlib import Path\n\n\n"
        "def test_source_blank() -> None:\n"
        '    source = Path(__file__).parents[1] / "src" / "sample" / "risk.py"\n'
        '    source.write_text("", encoding="utf-8")\n'
    ),
    "source_restore": (
        "from pathlib import Path\n\n\n"
        "def test_source_restore() -> None:\n"
        '    source = Path(__file__).parents[1] / "src" / "sample" / "risk.py"\n'
        "    original = source.read_bytes()\n"
        '    source.write_bytes(b"")\n'
        "    source.write_bytes(original)\n"
    ),
    "tool_overwrite": (
        "from pathlib import Path\n\n\n"
        "def test_tool_overwrite() -> None:\n"
        '    configuration = Path("/work/coverage.ini")\n'
        '    configuration.write_text("[report]\\nexclude_lines =\\n    .+\\n")\n'
    ),
    "collector_overwrite": (
        "from pathlib import Path\n\n\n"
        "def test_collector_overwrite() -> None:\n"
        '    collector = Path("/collector/hosted_quality_profile.py")\n'
        '    collector.write_text("raise SystemExit(0)\\n")\n'
    ),
    "evidence_overwrite": (
        "from pathlib import Path\n\n\n"
        "def test_evidence_overwrite() -> None:\n"
        '    evidence = Path("/evidence/quality-gates.json")\n'
        '    evidence.write_text("{}\\n")\n'
    ),
}


def _hostile_repository(tmp_path: Path, fixture: str) -> tuple[Path, str, str, Path, str]:
    repository = tmp_path / f"{fixture.replace('_', '-')}-target"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Fixture")
    _run_git(repository, "config", "user.email", "fixture@example.invalid")
    _run_git(repository, "remote", "add", "origin", "https://github.com/example/source-blank.git")
    (repository / ".supportability.toml").write_text(
        POLICY_TEXT.replace("src/risk.py", "src/sample/risk.py"),
        encoding="utf-8",
        newline="\n",
    )
    (repository / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools==83.0.0']\n"
        "build-backend = 'setuptools.build_meta'\n\n"
        "[project]\nname = 'source-blank-fixture'\nversion = '1.0.0'\n"
        "requires-python = '>=3.12'\n\n"
        "[tool.setuptools]\npackage-dir = {'' = 'src'}\n\n"
        "[tool.setuptools.packages.find]\nwhere = ['src']\n",
        encoding="utf-8",
        newline="\n",
    )
    package = repository / "src" / "sample"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8", newline="\n")
    source = package / "risk.py"
    source.write_text(
        "def untested() -> float:\n    return 1 / 0\n", encoding="utf-8", newline="\n"
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / f"test_{fixture}.py").write_text(
        HOSTILE_CAPTURE_TESTS[fixture],
        encoding="utf-8",
        newline="\n",
    )
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", "base")
    base_sha = _run_git(repository, "rev-parse", "HEAD")
    (repository / "docs").mkdir()
    (repository / "docs" / "change.md").write_text("head\n", encoding="utf-8", newline="\n")
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", "head")
    head_sha = _run_git(repository, "rev-parse", "HEAD")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    return repository, base_sha, head_sha, source, source_sha256


@pytest.mark.skipif(
    os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted",
    reason="hostile target execution is permitted only on the GitHub-hosted qualification surface",
)
@pytest.mark.parametrize("fixture", HOSTILE_CAPTURE_FIXTURES)
def test_hostile_fixture_is_denied_without_authoritative_mutation(
    tmp_path: Path, fixture: str
) -> None:
    repository, base_sha, head_sha, source, source_sha256 = _hostile_repository(tmp_path, fixture)
    output = tmp_path / "evidence" / "quality-gates.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            str(Path(__file__).with_name("hosted_quality_profile.py")),
            "--repository",
            str(repository.resolve()),
            "--repository-name",
            f"example/{fixture.replace('_', '-')}",
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
    evidence = quality_profile.load_evidence(output) if output.is_file() else None
    source_after = hashlib.sha256(source.read_bytes()).hexdigest()
    reproduction = {
        "command_exit_codes": (
            [[item.adapter, item.exit_code] for item in evidence.commands]
            if evidence is not None
            else None
        ),
        "runner_exit_code": completed.returncode,
        "source_after_sha256": source_after,
        "source_before_sha256": source_sha256,
        "stderr": completed.stderr.decode(errors="replace"),
        "stdout": completed.stdout.decode(errors="replace"),
        "diagnostics": _retained_diagnostics(output),
    }

    assert completed.returncode == 2, json.dumps(reproduction, sort_keys=True)
    assert completed.stdout.decode().strip() == "TARGET_SANDBOX_WRITE_DENIED", json.dumps(
        reproduction, sort_keys=True
    )
    assert source_after == source_sha256
    assert not output.exists()


def _retained_diagnostics(output: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((output.parent / "diagnostics").glob("*"))
    }


def test_all_s02_hostile_capture_fixtures_are_retained() -> None:
    assert tuple(HOSTILE_CAPTURE_TESTS) == HOSTILE_CAPTURE_FIXTURES
    assert "Path(__file__).parents[1]" in HOSTILE_CAPTURE_TESTS["source_blank"]
    assert "return 1 / 0" not in HOSTILE_CAPTURE_TESTS["source_blank"]


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


def test_source_receipts_and_zero_eligibility_come_from_exact_git_blobs(
    tmp_path: Path,
) -> None:
    repository, _base_sha, head_sha, source, committed_sha256 = _hostile_repository(
        tmp_path, "source_blank"
    )
    source.write_bytes(b"")

    receipts = quality_profile.source_receipts(repository, head_sha, ("src/sample/risk.py",), [])

    assert receipts == (
        quality_profile.SourceReceipt(
            "src/sample/risk.py",
            _run_git(repository, "rev-parse", f"{head_sha}:src/sample/risk.py"),
            committed_sha256,
            False,
        ),
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
        "import socket\n"
        "from pathlib import Path\n\n"
        "import pytest\n\n"
        "from sample.covered import score\n\n\n"
        "def test_score() -> None:\n"
        '    assert not any("TOKEN" in name or "SECRET" in name for name in os.environ)\n'
        "    with pytest.raises(OSError):\n"
        '        socket.create_connection(("1.1.1.1", 53), timeout=0.2)\n'
        '    assert Path("/sys/fs/cgroup/memory.max").read_text().strip() == "2147483648"\n'
        '    assert Path("/sys/fs/cgroup/pids.max").read_text().strip() == "256"\n'
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
    assert completed.returncode != 2, json.dumps(
        {
            "diagnostics": _retained_diagnostics(output),
            "stderr": completed.stderr.decode(errors="replace"),
            "stdout": completed.stdout.decode(errors="replace"),
        },
        sort_keys=True,
    )
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
@pytest.mark.parametrize("profile", ["typescript", "mixed"])
def test_typescript_and_mixed_profiles_execute_every_fixed_gate_on_hosted_runner(
    tmp_path: Path, profile: str
) -> None:
    repository = tmp_path / f"{profile}-target"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Fixture")
    _run_git(repository, "config", "user.email", "fixture@example.invalid")
    _run_git(repository, "remote", "add", "origin", f"https://github.com/example/{profile}.git")
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
    typescript_policy = """schema_version = "1.0"
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
"""
    mixed_policy = """schema_version = "1.1"
languages = ["python", "typescript"]
production_paths = ["src", "web"]
high_risk_paths = ["src/sample/math.py", "web/presentation/Card.tsx"]

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

[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["web"]

[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["web"]

[complexity]
maximum = 10
"""
    (repository / ".supportability.toml").write_text(
        mixed_policy if profile == "mixed" else typescript_policy,
        encoding="utf-8",
        newline="\n",
    )
    if profile == "mixed":
        (repository / "pyproject.toml").write_text(
            "[build-system]\nrequires = ['setuptools==83.0.0']\n"
            "build-backend = 'setuptools.build_meta'\n\n"
            "[project]\nname = 'mixed-quality-fixture'\nversion = '1.0.0'\n"
            "requires-python = '>=3.12'\n\n"
            "[tool.setuptools]\npackage-dir = {'' = 'src'}\n\n"
            "[tool.setuptools.packages.find]\nwhere = ['src']\n",
            encoding="utf-8",
            newline="\n",
        )
        python_package = repository / "src" / "sample"
        python_package.mkdir(parents=True)
        (python_package / "__init__.py").write_text("", encoding="utf-8", newline="\n")
        (python_package / "math.py").write_text(
            "def increment(value: int) -> int:\n    return value + 1\n",
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
    if profile == "mixed":
        (tests / "test_math.py").write_text(
            "from sample.math import increment\n\n\n"
            "def test_increment() -> None:\n    assert increment(1) == 2\n",
            encoding="utf-8",
            newline="\n",
        )
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
    if profile == "typescript":
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
            f"example/{profile}",
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
    assert completed.returncode != 2, json.dumps(
        {
            "diagnostics": _retained_diagnostics(output),
            "stderr": completed.stderr.decode(errors="replace"),
            "stdout": completed.stdout.decode(errors="replace"),
        },
        sort_keys=True,
    )
    evidence = quality_profile.load_evidence(output)
    assert completed.returncode == 0, [(item.adapter, item.exit_code) for item in evidence.commands]
    provenance = json.loads(
        (output.parent / "isolation-provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["schema_version"] == "isolated-target-provenance.v1"
    assert provenance["quality_evidence_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert provenance["container"]["image"] == quality_runner.CONTAINER_IMAGE
    assert provenance["security_controls"]["init_process"] is True
    assert any(
        item.startswith("npm-target:fixture-dependency@1.0.0:sha256:")
        for item in provenance["resolved_dependencies"]
    )
    assert [item["path"] for item in provenance["source_receipts"]] == list(evidence.source_files)
    assert all(
        item["observation_provenance"]
        in {"provisioning-supervisor", "supervisor-observed", "target-generated-untrusted"}
        for item in provenance["commands"]
    )
    assert not (output.parent / "trusted").exists()
    assert not (output.parent / "sandbox").exists()

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

    source_files = evidence.source_files
    plans = quality_runner.command_plans(
        profile,
        repository,
        output.parent,
        evidence.test_files,
        source_files,
    )
    assert tuple(item.arguments for item in evidence.commands) == tuple(
        plan.evidence for plan in plans
    )
    assert tuple(item.executed_arguments for item in evidence.commands) == tuple(
        plan.actual for plan in plans
    )
    install_plan = next(plan for plan in plans if plan.adapter == "typescript.target-install.v1")
    dependency_root = quality_runner.trusted_directory(output.parent) / "target-dependencies"
    shutil.copytree(repository, dependency_root, ignore=shutil.ignore_patterns(".git"))
    package_path = dependency_root / "package.json"
    lock_path = dependency_root / "package-lock.json"
    package_text = package_path.read_text(encoding="utf-8")
    lock_text = lock_path.read_text(encoding="utf-8")

    def install_exit() -> int:
        return subprocess.run(
            quality_runner.provisioning_command(install_plan, output.parent),
            cwd=dependency_root,
            env=quality_runner.fixed_environment(output.parent, repository),
            check=False,
            capture_output=True,
            timeout=quality_profile.TIMEOUT_SECONDS,
        ).returncode

    lock_path.unlink()
    assert install_exit() != 0
    lock_path.write_text("{", encoding="utf-8", newline="\n")
    assert install_exit() != 0
    lock_path.write_text(lock_text, encoding="utf-8", newline="\n")
    out_of_sync = json.loads(package_text)
    out_of_sync["dependencies"]["missing-lock-entry"] = "1.0.0"
    package_path.write_text(
        json.dumps(out_of_sync, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    assert install_exit() != 0
    package_path.write_text(package_text, encoding="utf-8", newline="\n")
    lock_path.write_text(lock_text, encoding="utf-8", newline="\n")
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
            git_changes.ChangedPath(
                "MODIFIED", "web/presentation/Card.tsx", "web/presentation/Card.tsx"
            ),
            True,
            True,
            True,
            (15,),
        ),
    )
    if profile == "typescript":
        assessments = (
            assessments[0],
            ChangedFileAssessment(
                git_changes.ChangedPath("ADDED", None, poison), False, True, True, (1,)
            ),
            assessments[1],
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

    assert "web/presentation/Card.tsx" in test_result.observed_paths
    assert not any(
        block.endswith(":web/presentation/Card.tsx")
        and block.startswith("QUALITY_HIGH_RISK_FILE_COVERAGE:")
        for block in blocks
    )
    if profile == "typescript":
        assert poison not in test_result.observed_paths
        assert f"UNTESTED_AREA:{poison}" in blocks
        assert f"QUALITY_CHANGED_FILE_COVERAGE:typescript.test.v1:{poison}" in blocks
    else:
        assert not blocks
    assert all(command.executed and command.exit_code == 0 for command in evidence.commands)
