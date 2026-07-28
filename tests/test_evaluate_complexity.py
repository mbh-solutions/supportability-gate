from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from supportability_gate import cli, complexity_metrics, function_changes, reporting

CONTRACT = """\
schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = []

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


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def _function_source(name: str, complexity: int, return_value: int = 0) -> str:
    lines = [f"def {name}(value: int) -> int:"]
    lines.extend(
        f"    if value == {index}:\n        return {index}" for index in range(complexity - 1)
    )
    lines.append(f"    return {return_value}")
    return "\n".join(lines) + "\n"


def _commit(repository: Path, message: str) -> str:
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", message)
    return _run_git(repository, "rev-parse", "HEAD")


def _initialize_repository(tmp_path: Path, contract_text: str = CONTRACT) -> Path:
    repository = tmp_path / "target"
    repository.mkdir()
    _run_git(repository, "init", "--initial-branch=main")
    _run_git(repository, "config", "user.name", "Fixture")
    _run_git(repository, "config", "user.email", "fixture@example.invalid")
    _run_git(repository, "config", "core.autocrlf", "false")
    _run_git(repository, "remote", "add", "origin", "https://github.com/example/fixture.git")
    _write(repository / ".supportability.toml", contract_text)
    return repository


def _repository(
    tmp_path: Path,
    base_source: str | None,
    head_source: str | None,
) -> tuple[Path, str, str]:
    repository = _initialize_repository(tmp_path)
    if base_source is not None:
        _write(repository / "src" / "sample.py", base_source)
    base_sha = _commit(repository, "base")
    sample = repository / "src" / "sample.py"
    if head_source is None:
        sample.unlink(missing_ok=True)
    else:
        _write(sample, head_source)
    head_sha = _commit(repository, "head")
    return repository, base_sha, head_sha


def _evaluate(
    repository: Path, base_sha: str, head_sha: str, output: Path
) -> tuple[int, dict[str, object]]:
    exit_code = cli.main(
        [
            "evaluate-complexity",
            "--repository",
            str(repository.resolve()),
            "--base-ref",
            base_sha,
            "--head-ref",
            head_sha,
            "--contract-path",
            ".supportability.toml",
            "--output-directory",
            str(output.resolve()),
        ]
    )
    result = json.loads((output / "complexity-result.json").read_text(encoding="utf-8"))
    return exit_code, result


def test_new_complexity_10_passes(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(tmp_path, None, _function_source("new", 10))

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["overall_result"] == "PASS"
    assert result["functions"][0]["decision"] == "PASS"
    assert result["functions"][0]["head"]["complexity"] == 10
    assert [gate["adapter"] for gate in result["gate_coverage"]] == [
        "python.c901-touched.v1",
        "python.import-linter.v1",
        "python.mypy-strict.v1",
        "python.pytest.v1",
        "python.ruff-lint.v1",
    ]


def test_new_complexity_11_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(tmp_path, None, _function_source("new", 11))

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["overall_result"] == "BLOCK"
    assert result["functions"][0]["decision"] == "BLOCK"
    assert result["ruff_diagnostics"][0]["complexity"] == 11


def test_legacy_14_to_12_passes_progressively(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("legacy", 14),
        _function_source("legacy", 12),
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    decision = result["functions"][0]
    assert exit_code == 0
    assert decision["decision"] == "PASS_PROGRESSIVE"
    assert decision["base"]["complexity"] == 14
    assert decision["head"]["complexity"] == 12
    assert decision["remaining_debt"] == 2
    assert decision["next_target"] == 10


def test_legacy_14_to_14_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("legacy", 14, 0),
        _function_source("legacy", 14, 1),
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["functions"][0]["decision"] == "BLOCK"
    assert result["functions"][0]["base"]["complexity"] == 14
    assert result["functions"][0]["head"]["complexity"] == 14


def test_legacy_14_to_15_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("legacy", 14),
        _function_source("legacy", 15),
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["functions"][0]["decision"] == "BLOCK"
    assert result["functions"][0]["head"]["complexity"] == 15


def test_existing_9_to_11_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("existing", 9),
        _function_source("existing", 11),
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["functions"][0]["state"] == "EXISTING"
    assert result["functions"][0]["decision"] == "BLOCK"


def test_extracted_helper_11_blocks(tmp_path: Path) -> None:
    base = _function_source("original", 1)
    head = base + "\n" + _function_source("extracted_helper", 11)
    repository, base_sha, head_sha = _repository(tmp_path, base, head)

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["functions"][0]["head"]["qualified_name"] == "extracted_helper"
    assert result["functions"][0]["state"] == "NEW"
    assert result["functions"][0]["decision"] == "BLOCK"


def test_renamed_file_with_legacy_improvement_passes_progressively(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    old_path = repository / "src" / "old.py"
    _write(old_path, _function_source("legacy", 14))
    base_sha = _commit(repository, "base")
    _run_git(repository, "mv", "src/old.py", "src/new.py")
    _write(repository / "src" / "new.py", _function_source("legacy", 12))
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["changed_files"][0]["status"] == "RENAMED"
    assert result["rename_bindings"] == [{"old_path": "src/old.py", "new_path": "src/new.py"}]
    assert result["functions"][0]["decision"] == "PASS_PROGRESSIVE"


def test_deleted_high_complexity_function_is_evidence_not_block(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("legacy", 14),
        None,
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["functions"][0]["decision"] == "DELETED"
    assert result["functions"][0]["head"] is None
    assert result["functions"][0]["base"]["complexity"] == 14


def test_non_production_change_is_reported_without_complexity_block(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    note = repository / "docs" / "note.md"
    _write(note, "base\n")
    base_sha = _commit(repository, "base")
    _write(note, "head\n")
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["changed_files"][0]["head_production"] is False
    assert result["changed_files"][0]["complexity_assessed"] is False
    assert result["functions"] == []


def test_nested_function_and_method_binding(tmp_path: Path) -> None:
    source = """\
def outer(value: int) -> int:
    def inner(inner_value: int) -> int:
        if inner_value:
            return 1
        return 0
    return inner(value)


class Widget:
    def method(self, value: int) -> int:
        if value:
            return 1
        return 0
"""
    repository, base_sha, head_sha = _repository(tmp_path, None, source)

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["touched_qualified_functions"] == [
        "Widget.method",
        "outer",
        "outer.inner",
    ]


def test_candidate_contract_change_blocks(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(repository / ".supportability.toml", CONTRACT + "\n")
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["overall_result"] == "BLOCK"
    assert result["policy_blocks"] == ["CANDIDATE_CONTRACT_CHANGE"]


def test_unapproved_gate_adapter_blocks(tmp_path: Path) -> None:
    policy = CONTRACT.replace("python.ruff-lint.v1", "python.unapproved.v1")
    repository = _initialize_repository(tmp_path, policy)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["overall_result"] == "BLOCK"
    assert "UNAPPROVED_ADAPTER:python.unapproved.v1" in result["policy_blocks"]


def test_changed_file_gate_coverage_gap_blocks(tmp_path: Path) -> None:
    policy = CONTRACT.replace(
        'adapter = "python.ruff-lint.v1"\npaths = ["src"]',
        'adapter = "python.ruff-lint.v1"\npaths = ["other"]',
    )
    repository = _initialize_repository(tmp_path, policy)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [
        "CHANGED_FILE_GATE_COVERAGE:python.ruff-lint.v1:src/sample.py"
    ]


def test_high_risk_file_gate_coverage_gap_blocks(tmp_path: Path) -> None:
    policy = CONTRACT.replace(
        "high_risk_paths = []",
        'high_risk_paths = ["src/risk.py"]',
    ).replace(
        'adapter = "python.ruff-lint.v1"\npaths = ["src"]',
        'adapter = "python.ruff-lint.v1"\npaths = ["src/sample.py"]',
    )
    repository = _initialize_repository(tmp_path, policy)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    _write(repository / "src" / "risk.py", _function_source("risk", 1))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [
        "HIGH_RISK_FILE_GATE_COVERAGE:python.ruff-lint.v1:src/risk.py"
    ]


def test_threshold_weakening_blocks(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    _write(repository / ".supportability.toml", CONTRACT.replace("maximum = 10", "maximum = 11"))
    head_sha = _commit(repository, "weaken threshold")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [
        "CANDIDATE_CONTRACT_CHANGE",
        "THRESHOLD_WEAKENING",
    ]


def test_gate_scope_narrowing_blocks(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    narrowed = CONTRACT.replace(
        'adapter = "python.ruff-lint.v1"\npaths = ["src"]',
        'adapter = "python.ruff-lint.v1"\npaths = ["src/package"]',
    )
    _write(repository / ".supportability.toml", narrowed)
    head_sha = _commit(repository, "narrow gate scope")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [
        "CANDIDATE_CONTRACT_CHANGE",
        "GATE_SCOPE_NARROWING",
    ]


def test_milestone_two_block_evidence_is_byte_identical(tmp_path: Path) -> None:
    policy = CONTRACT.replace(
        'adapter = "python.ruff-lint.v1"\npaths = ["src"]',
        'adapter = "python.ruff-lint.v1"\npaths = ["other"]',
    )
    repository = _initialize_repository(tmp_path, policy)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    head_sha = _commit(repository, "head")
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_exit, _ = _evaluate(repository, base_sha, head_sha, first)
    second_exit, _ = _evaluate(repository, base_sha, head_sha, second)

    assert first_exit == second_exit == 1
    assert (first / "complexity-result.json").read_bytes() == (
        second / "complexity-result.json"
    ).read_bytes()


def test_malformed_base_contract_is_technical_failure(tmp_path: Path) -> None:
    malformed = CONTRACT.replace('language = "python"\n', "")
    repository = _initialize_repository(tmp_path, malformed)
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 0))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 2
    assert result["overall_result"] == "TECHNICAL_FAILURE"
    assert result["technical_errors"][0]["code"] == "INVALID_SCHEMA_KEYS"


def test_syntax_error_is_technical_failure(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("existing", 1),
        "def broken(:\n",
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 2
    assert result["technical_errors"][0]["code"] == "SYNTAX_ERROR"


def test_ruff_parity_mismatch_is_technical_failure(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    source = _function_source("too_complex", 11)
    _write(repository / "src" / "sample.py", source)
    _commit(repository, "fixture")
    parsed = function_changes.parse_python_file("src/sample.py", source.encode())
    metrics = complexity_metrics.measure_definitions(parsed.functions)

    with pytest.raises(complexity_metrics.MetricsError, match="parity mismatch") as error:
        complexity_metrics.verify_ruff_parity(metrics, ())

    assert error.value.code == "RUFF_PARITY_MISMATCH"


def test_repeated_run_writes_byte_identical_json(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        None,
        _function_source("new", 10),
    )
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_exit, _ = _evaluate(repository, base_sha, head_sha, first)
    second_exit, _ = _evaluate(repository, base_sha, head_sha, second)

    assert first_exit == second_exit == 0
    assert (first / "complexity-result.json").read_bytes() == (
        second / "complexity-result.json"
    ).read_bytes()


def test_standard_hash_change_fails_source_validation(tmp_path: Path) -> None:
    standard = Path(__file__).parents[1] / "docs" / "supportability_standard.md"
    expected = "81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2"
    assert reporting.STANDARD_SHA256 == expected
    assert hashlib.sha256(standard.read_bytes()).hexdigest() == expected
    changed = tmp_path / "supportability_standard.md"
    changed.write_bytes(standard.read_bytes() + b"\nchanged\n")

    with pytest.raises(AssertionError):
        assert hashlib.sha256(changed.read_bytes()).hexdigest() == expected


def test_whitespace_validation_excludes_only_immutable_standard(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    standard = repository / "docs" / "supportability_standard.md"
    _write(standard, "owner text\n")
    base_sha = _commit(repository, "base")
    _write(standard, "owner text \n")
    standard_only_sha = _commit(repository, "standard whitespace")
    arguments = (
        "diff",
        "--check",
        base_sha,
        standard_only_sha,
        "--",
        ".",
        ":(exclude)docs/supportability_standard.md",
    )

    _run_git(repository, *arguments)

    _write(repository / "README.md", "invalid whitespace \n")
    other_file_sha = _commit(repository, "other whitespace")
    with pytest.raises(subprocess.CalledProcessError):
        _run_git(repository, *arguments[:3], other_file_sha, *arguments[4:])


def test_target_import_sentinel_is_not_executed(tmp_path: Path) -> None:
    source = """\
raise RuntimeError("target source executed")


def safe(value: int) -> int:
    return value
"""
    repository, base_sha, head_sha = _repository(tmp_path, None, source)

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["overall_result"] == "PASS"


def test_command_bearing_contract_is_technical_failure(tmp_path: Path) -> None:
    command_contract = CONTRACT + 'command = "python unsafe.py"\n'
    repository = _initialize_repository(tmp_path, command_contract)
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 0))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 2
    assert result["technical_errors"][0]["code"] == "INVALID_SCHEMA_KEYS"
