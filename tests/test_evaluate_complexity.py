from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from supportability_gate import (
    __version__,
    cli,
    complexity_metrics,
    complexity_policy,
    contract,
    function_changes,
    git_changes,
    quality_profile,
    reporting,
)


def test_reported_package_version_matches_distribution_version() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8"))
    assert complexity_metrics.tool_versions()["supportability_gate"] == __version__
    assert __version__ == metadata["project"]["version"]


WORKFLOW_SHA = "f" * 40

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

TYPESCRIPT_CONTRACT = """\
schema_version = "1.0"
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

REVIEW_EVIDENCE = """\
schema_version = "1.0"

[behavior]
intended_behavior = "Changed behavior remains covered by focused tests."
proof = "tests/test_behavior.py::test_changed_behavior"

[characterization]
captured_behavior = "Pre-change behavior is captured before refactoring."
proof = "tests/test_behavior.py::test_characterized_behavior"

[separation_of_concerns]
before = "The changed boundary previously mixed responsibilities."
after = "The changed boundary now has one named responsibility."

[architecture]
dependency_direction = "Dependencies continue to point toward domain policy."
reviewed_paths = ["src/sample.py"]

[responsibility_boundary]
path = "src/sample.py"
owns = "Sample behavior."
does_not_own = "CLI presentation."

[incremental_refactor]
target = "One focused sample boundary."
completed_step = "Characterized and changed only that boundary."

[review_handoff]
summary = "Focused behavior-preserving change ready for review."
remaining_risks = ["No known remaining risk in the focused boundary."]

[human_review]
naming = "Names express owned responsibilities."
cohesion = "Changed code remains cohesive."
intended_behavior = "Reported behavior remains intended."
reviewability = "Change is small enough for direct review."
"""


def _review_evidence_for_new_path(path: str) -> str:
    owner_path = "src/owner.py" if path.endswith((".py", ".pyi")) else "src/owner.ts"
    return (
        REVIEW_EVIDENCE
        + f'''\

[[module_boundaries]]
path = "{path}"
owner_path = "{owner_path}"
basis = "responsibility"
justification = "Exact source path owns one cohesive fixture boundary."
'''
    )


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


def _typescript_source(name: str, complexity: int, return_value: int = 0) -> str:
    lines = [f"export function {name}(value: number): number {{"]
    lines.extend(
        f"  if (value === {index}) {{ return {index}; }}" for index in range(complexity - 1)
    )
    lines.append(f"  return {return_value};")
    lines.append("}")
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
    _write(repository / ".supportability-review.toml", REVIEW_EVIDENCE)
    owner = "owner.ts" if 'language = "typescript"' in contract_text else "owner.py"
    owner_source = "export const OWNER = true;\n" if owner.endswith(".ts") else "OWNER = True\n"
    _write(repository / "src" / owner, owner_source)
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
        if base_source is None:
            _write(
                repository / ".supportability-review.toml",
                _review_evidence_for_new_path("src/sample.py"),
            )
    head_sha = _commit(repository, "head")
    return repository, base_sha, head_sha


def _typescript_repository(
    tmp_path: Path,
    base_source: str | None,
    head_source: str | None,
    *,
    extension: str = ".ts",
) -> tuple[Path, str, str]:
    repository = _initialize_repository(tmp_path, TYPESCRIPT_CONTRACT)
    sample = repository / "src" / f"sample{extension}"
    if base_source is not None:
        _write(sample, base_source)
    base_sha = _commit(repository, "base")
    if head_source is None:
        sample.unlink(missing_ok=True)
    else:
        _write(sample, head_source)
        if base_source is None:
            _write(
                repository / ".supportability-review.toml",
                _review_evidence_for_new_path(f"src/sample{extension}"),
            )
    head_sha = _commit(repository, "head")
    return repository, base_sha, head_sha


def _evaluate(
    repository: Path, base_sha: str, head_sha: str, output: Path
) -> tuple[int, dict[str, object]]:
    quality_path = output.parent / f"{output.name}-quality.json"
    try:
        records: list[git_changes.CommandRecord] = []
        identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
        policy = contract.parse_contract(
            git_changes.read_regular_blob(
                repository, identity.base_sha, ".supportability.toml", records
            ).content
        )
        changes = git_changes.changed_paths(repository, base_sha, head_sha, records)
        changed_paths = tuple(
            sorted(
                {
                    path
                    for change in changes
                    for path in (change.old_path, change.new_path)
                    if path and policy.is_production_path(path)
                }
            )
        )
        suffixes = (
            (".py", ".pyi")
            if policy.language == "python"
            else (".cts", ".js", ".jsx", ".mts", ".ts", ".tsx")
        )
        production_files = tuple(
            item.path
            for item in git_changes.list_regular_blobs(
                repository, head_sha, policy.production_paths, records
            )
            if item.path.endswith(suffixes)
        )
        commands = tuple(
            quality_profile.GateResult(
                adapter,
                arguments,
                quality_profile.expected_proof_kind(adapter),
                ()
                if quality_profile.expected_proof_kind(adapter) == "provisioning"
                else production_files,
                (),
                True,
                0,
                hashlib.sha256(b"").hexdigest(),
                hashlib.sha256(b"").hexdigest(),
                hashlib.sha256(b"").hexdigest(),
            )
            for adapter, arguments in quality_profile.command_templates(policy.language)
        )
        quality_profile.write_evidence(
            quality_profile.QualityEvidence(
                base_sha=base_sha,
                changed_paths=changed_paths,
                commands=commands,
                exclusions=(),
                head_sha=head_sha,
                high_risk_paths=policy.high_risk_paths,
                language=policy.language,
                maximum_complexity=policy.maximum,
                production_files=production_files,
                production_paths=policy.production_paths,
                repository="example/fixture",
                repository_id="123",
                repository_remote=identity.remote,
                run_attempt="1",
                run_id="456",
                runner_environment="github-hosted",
                schema_version=quality_profile.SCHEMA_VERSION,
                workflow_sha=WORKFLOW_SHA,
                job="quality-profile",
                artifact_id="",
                artifact_digest="",
                capture_sha256="",
            ),
            quality_path,
        )
        (quality_path.parent / "artifact.json").write_text(
            json.dumps(
                {
                    "id": 789,
                    "name": "quality-profile-456-1",
                    "expired": False,
                    "expires_at": "2026-08-29T00:00:00Z",
                    "digest": f"sha256:{'d' * 64}",
                    "url": "https://api.github.com/repos/example/fixture/actions/artifacts/789",
                    "workflow_run": {
                        "id": 456,
                        "repository_id": 123,
                        "head_sha": head_sha,
                    },
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        quality_path.parent.mkdir(parents=True, exist_ok=True)
        quality_path.write_text("{}\n", encoding="utf-8")
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
            "--quality-evidence",
            str(quality_path.resolve()),
            "--quality-repository",
            "example/fixture",
            "--quality-repository-id",
            "123",
            "--quality-run-id",
            "456",
            "--quality-run-attempt",
            "1",
            "--quality-job",
            "quality-profile",
            "--quality-artifact-id",
            "789",
            "--quality-artifact-digest",
            "d" * 64,
            "--quality-artifact-metadata",
            str((quality_path.parent / "artifact.json").resolve()),
            "--quality-capture-sha256",
            hashlib.sha256(quality_path.read_bytes()).hexdigest(),
            "--workflow-sha",
            WORKFLOW_SHA,
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
    assert result["language"] == "python"
    assert result["architecture"]["executed"] is True
    assert result["architecture"]["covered_paths"] == ["src/owner.py", "src/sample.py"]
    assert result["modularity"]["new_paths"] == ["src/sample.py"]
    assert result["modularity"]["coverage"][0]["architecture"] is True
    assert len(result["modularity"]["coverage"][0]["adapters"]) == 5
    assert (
        "changed production paths ['src/sample.py']" in result["dependency_direction_explanation"]
    )
    assert [gate["adapter"] for gate in result["gate_coverage"]] == [
        "python.c901-touched.v1",
        "python.import-linter.v1",
        "python.mypy-strict.v1",
        "python.pytest.v1",
        "python.ruff-lint.v1",
    ]
    assert result["standard_blocks"] == [
        {"blocks": [], "standard": standard} for standard in range(1, 9)
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
    assert decision["remaining_gap"] == 2
    assert decision["starting_complexity"] == 14
    assert decision["ending_complexity"] == 12
    assert decision["next_target"] == 10


def test_typescript_clean_complexity_10_passes(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _typescript_repository(
        tmp_path, None, _typescript_source("cleanChange", 10)
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["language"] == "typescript"
    assert result["functions"][0]["head"]["complexity"] == 10
    assert result["touched_qualified_functions"] == ["cleanChange"]
    assert result["ruff_diagnostics"] == []


def test_typescript_complexity_11_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _typescript_repository(
        tmp_path, None, _typescript_source("tooComplex", 11)
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["functions"][0]["decision"] == "BLOCK"
    assert result["functions"][0]["ending_complexity"] == 11


def test_typescript_legacy_must_improve_and_reports_gap(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _typescript_repository(
        tmp_path,
        _typescript_source("legacyFlow", 14, 0),
        _typescript_source("legacyFlow", 12, 1),
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    decision = result["functions"][0]
    assert exit_code == 0
    assert decision["decision"] == "PASS_PROGRESSIVE"
    assert decision["starting_complexity"] == 14
    assert decision["ending_complexity"] == 12
    assert decision["remaining_gap"] == 2
    assert decision["next_target"] == 10


def test_typescript_non_improving_legacy_touch_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _typescript_repository(
        tmp_path,
        _typescript_source("legacyFlow", 14, 0),
        _typescript_source("legacyFlow", 14, 1),
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["functions"][0]["decision"] == "BLOCK"


def test_typescript_new_over_limit_extraction_blocks(tmp_path: Path) -> None:
    base = _typescript_source("existingFlow", 1)
    head = base + "\n" + _typescript_source("validateCustomerRows", 11)
    repository, base_sha, head_sha = _typescript_repository(tmp_path, base, head)

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    extracted = next(
        item
        for item in result["functions"]
        if item["head"]["qualified_name"] == "validateCustomerRows"
    )
    assert exit_code == 1
    assert extracted["state"] == "NEW"
    assert extracted["decision"] == "BLOCK"


def test_typescript_tsx_arrow_function_is_bound(tmp_path: Path) -> None:
    source = "\n" * 300 + (
        "export const CustomerCard = (active: boolean) => active ? <div /> : null;\n"
    )
    repository, base_sha, head_sha = _typescript_repository(
        tmp_path, None, source, extension=".tsx"
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["touched_qualified_functions"] == ["CustomerCard"]
    assert result["functions"][0]["head"]["start_line"] == 301
    assert result["functions"][0]["ending_complexity"] == 2


def test_typescript_anonymous_callback_gets_stable_identity(tmp_path: Path) -> None:
    source = "export const values = [1].map((value) => value + 1);\n"
    repository, base_sha, head_sha = _typescript_repository(tmp_path, None, source)

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["touched_qualified_functions"] == ["anonymous@1:31"]


def test_typescript_profile_mismatch_blocks_instead_of_skipping(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path, TYPESCRIPT_CONTRACT)
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.js", "export function skipped() { return 1; }\n")
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == ["PROFILE_SOURCE_MISMATCH:src/sample.js"]


def test_typescript_threshold_weakening_blocks(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path, TYPESCRIPT_CONTRACT)
    base_sha = _commit(repository, "base")
    _write(
        repository / ".supportability.toml",
        TYPESCRIPT_CONTRACT.replace("maximum = 10", "maximum = 11"),
    )
    head_sha = _commit(repository, "weaken threshold")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [
        "CANDIDATE_CONTRACT_CHANGE",
        "THRESHOLD_WEAKENING",
    ]


def test_typescript_evidence_is_byte_identical(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _typescript_repository(
        tmp_path, None, _typescript_source("stableResult", 10)
    )
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_exit, _ = _evaluate(repository, base_sha, head_sha, first)
    second_exit, _ = _evaluate(repository, base_sha, head_sha, second)

    assert first_exit == second_exit == 0
    assert (first / "complexity-result.json").read_bytes() == (
        second / "complexity-result.json"
    ).read_bytes()


def test_incomplete_remaining_gap_blocks() -> None:
    span = function_changes.FunctionSpan("src/sample.py", "legacy", 1, 2)
    base = complexity_metrics.FunctionMetric(span, 14)
    head = complexity_metrics.FunctionMetric(span, 12)
    incomplete = complexity_policy.FunctionDecision(
        base, head, "EXISTING_LEGACY", "PASS_PROGRESSIVE", None, None
    )

    with pytest.raises(complexity_policy.ComplexityPolicyError, match="INCOMPLETE_REMAINING_GAP"):
        complexity_policy.validate_reporting((incomplete,), 10)


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
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_for_new_path("src/new.py"),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert any(item["status"] == "RENAMED" for item in result["changed_files"])
    assert result["rename_bindings"] == [{"old_path": "src/old.py", "new_path": "src/new.py"}]
    assert result["functions"][0]["decision"] == "PASS_PROGRESSIVE"


def test_new_location_without_exact_justification_blocks(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "unowned.py", _function_source("new", 1))
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == ["MISSING_NEW_LOCATION_JUSTIFICATION:src/unowned.py"]


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


def test_candidate_contract_change_and_complexity_block_are_reported_together(
    tmp_path: Path,
) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(
        repository / ".supportability.toml",
        CONTRACT.replace("maximum = 10", "maximum = 11"),
    )
    _write(repository / "src" / "sample.py", _function_source("existing", 11))
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["overall_result"] == "BLOCK"
    assert result["policy_blocks"] == [
        "CANDIDATE_CONTRACT_CHANGE",
        "THRESHOLD_WEAKENING",
    ]
    assert result["functions"][0]["head"]["complexity"] == 11
    assert result["functions"][0]["decision"] == "BLOCK"


def test_policy_and_complexity_blocks_are_reported_together(tmp_path: Path) -> None:
    policy = CONTRACT.replace("python.ruff-lint.v1", "python.unapproved.v1")
    repository = _initialize_repository(tmp_path, policy)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 11))
    head_sha = _commit(repository, "exceed complexity")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["overall_result"] == "BLOCK"
    assert "UNAPPROVED_ADAPTER:python.unapproved.v1" in result["policy_blocks"]
    assert result["standard_blocks"][6] == {
        "blocks": [
            "MISSING_REQUIRED_ADAPTER:python.ruff-lint.v1",
            "UNAPPROVED_ADAPTER:python.unapproved.v1",
        ],
        "standard": 7,
    }
    assert result["functions"][0]["decision"] == "BLOCK"
    assert result["functions"][0]["head"]["complexity"] == 11


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


def _section_span(content: str, section: str) -> tuple[int, int, list[str]]:
    lines = content.splitlines()
    start = lines.index(f"[{section}]")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("[")),
        len(lines),
    )
    return start, end, lines


def _defective_review_evidence(section: str, defect: str) -> str:
    start, end, lines = _section_span(REVIEW_EVIDENCE, section)
    if defect == "missing":
        del lines[start:end]
    else:
        field_index = next(index for index in range(start + 1, end) if "=" in lines[index])
        field = lines[field_index].split("=", 1)[0]
        lines[field_index] = f"{field}= {1 if defect == 'malformed' else '""'}"
    return "\n".join(lines) + "\n"


def _review_evidence_with_module_boundaries(definition: str, *, top_level: bool) -> str:
    if top_level:
        return REVIEW_EVIDENCE.replace(
            'schema_version = "1.0"\n',
            f'schema_version = "1.0"\n{definition}',
            1,
        )
    return f"{REVIEW_EVIDENCE}\n{definition}"


@pytest.mark.parametrize(
    "section",
    [
        "behavior",
        "characterization",
        "separation_of_concerns",
        "architecture",
        "responsibility_boundary",
        "incremental_refactor",
        "review_handoff",
        "human_review",
    ],
)
def test_missing_milestone_three_evidence_blocks(tmp_path: Path, section: str) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    _write(
        repository / ".supportability-review.toml", _defective_review_evidence(section, "missing")
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"][0].startswith("MISSING_REVIEW_EVIDENCE:")
    assert (
        sorted(block for row in result["standard_blocks"] for block in row["blocks"])
        == result["policy_blocks"]
    )


def test_missing_review_document_emits_every_field_in_its_standard_lane(
    tmp_path: Path,
) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    (repository / ".supportability-review.toml").unlink()
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["standard_blocks"] == [
        {
            "standard": 1,
            "blocks": [
                "MISSING_REVIEW_EVIDENCE:human_review.naming",
                "MISSING_REVIEW_EVIDENCE:human_review.reviewability",
            ],
        },
        {
            "standard": 2,
            "blocks": [
                "MISSING_REVIEW_EVIDENCE:separation_of_concerns.after",
                "MISSING_REVIEW_EVIDENCE:separation_of_concerns.before",
            ],
        },
        {
            "standard": 3,
            "blocks": [
                "MISSING_REVIEW_EVIDENCE:architecture.dependency_direction",
                "MISSING_REVIEW_EVIDENCE:architecture.reviewed_paths",
            ],
        },
        {
            "standard": 4,
            "blocks": [
                "MISSING_REVIEW_EVIDENCE:human_review.cohesion",
                "MISSING_REVIEW_EVIDENCE:responsibility_boundary.does_not_own",
                "MISSING_REVIEW_EVIDENCE:responsibility_boundary.owns",
                "MISSING_REVIEW_EVIDENCE:responsibility_boundary.path",
            ],
        },
        {
            "standard": 5,
            "blocks": [
                "MISSING_REVIEW_EVIDENCE:behavior.intended_behavior",
                "MISSING_REVIEW_EVIDENCE:behavior.proof",
                "MISSING_REVIEW_EVIDENCE:characterization.captured_behavior",
                "MISSING_REVIEW_EVIDENCE:characterization.proof",
                "MISSING_REVIEW_EVIDENCE:human_review.intended_behavior",
            ],
        },
        {
            "standard": 6,
            "blocks": [
                "MISSING_REVIEW_EVIDENCE:incremental_refactor.completed_step",
                "MISSING_REVIEW_EVIDENCE:incremental_refactor.target",
            ],
        },
        {"standard": 7, "blocks": []},
        {
            "standard": 8,
            "blocks": [
                "MISSING_REVIEW_EVIDENCE:review_handoff.remaining_risks",
                "MISSING_REVIEW_EVIDENCE:review_handoff.summary",
            ],
        },
    ]


@pytest.mark.parametrize(
    "section",
    [
        "behavior",
        "characterization",
        "separation_of_concerns",
        "architecture",
        "responsibility_boundary",
        "incremental_refactor",
        "review_handoff",
        "human_review",
    ],
)
def test_malformed_milestone_three_evidence_blocks(tmp_path: Path, section: str) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    _write(
        repository / ".supportability-review.toml",
        _defective_review_evidence(section, "malformed"),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"][0].startswith("MALFORMED_REVIEW_EVIDENCE:")


@pytest.mark.parametrize(
    "section",
    [
        "behavior",
        "characterization",
        "separation_of_concerns",
        "architecture",
        "responsibility_boundary",
        "incremental_refactor",
        "review_handoff",
        "human_review",
    ],
)
def test_insufficient_milestone_three_evidence_blocks(tmp_path: Path, section: str) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    _write(
        repository / ".supportability-review.toml",
        _defective_review_evidence(section, "insufficient"),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"][0].startswith("INSUFFICIENT_REVIEW_EVIDENCE:")


@pytest.mark.parametrize(
    ("original", "replacement", "standard", "block"),
    [
        (
            'reviewed_paths = ["src/sample.py"]',
            "reviewed_paths = [1]",
            3,
            "MALFORMED_REVIEW_EVIDENCE:architecture.reviewed_paths",
        ),
        (
            'reviewed_paths = ["src/sample.py"]',
            "reviewed_paths = []",
            3,
            "INSUFFICIENT_REVIEW_EVIDENCE:architecture.reviewed_paths",
        ),
        (
            'reviewed_paths = ["src/sample.py"]',
            'reviewed_paths = [""]',
            3,
            "INSUFFICIENT_REVIEW_EVIDENCE:architecture.reviewed_paths",
        ),
        (
            'remaining_risks = ["No known remaining risk in the focused boundary."]',
            "remaining_risks = [1]",
            8,
            "MALFORMED_REVIEW_EVIDENCE:review_handoff.remaining_risks",
        ),
        (
            'remaining_risks = ["No known remaining risk in the focused boundary."]',
            "remaining_risks = []",
            8,
            "INSUFFICIENT_REVIEW_EVIDENCE:review_handoff.remaining_risks",
        ),
        (
            'remaining_risks = ["No known remaining risk in the focused boundary."]',
            'remaining_risks = [""]',
            8,
            "INSUFFICIENT_REVIEW_EVIDENCE:review_handoff.remaining_risks",
        ),
    ],
)
def test_review_list_defects_emit_exact_owned_lane(
    tmp_path: Path,
    original: str,
    replacement: str,
    standard: int,
    block: str,
) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    _write(
        repository / ".supportability-review.toml",
        REVIEW_EVIDENCE.replace(original, replacement, 1),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [block]
    assert result["standard_blocks"] == [
        {"blocks": [block] if item == standard else [], "standard": item} for item in range(1, 9)
    ]


@pytest.mark.parametrize(
    ("definition", "top_level", "block"),
    [
        (
            'module_boundaries = "invalid"\n',
            True,
            "MALFORMED_REVIEW_EVIDENCE:module_boundaries",
        ),
        (
            'module_boundaries = ["invalid"]\n',
            True,
            "MALFORMED_REVIEW_EVIDENCE:module_boundaries[0]",
        ),
        (
            """[[module_boundaries]]
path = "src/new.py"
owner_path = "src/owner.py"
justification = "One boundary."
""",
            False,
            "MISSING_REVIEW_EVIDENCE:module_boundaries[0].basis",
        ),
        (
            """[[module_boundaries]]
path = "src/new.py"
owner_path = "src/owner.py"
basis = "responsibility"
justification = "One boundary."
extra = "invalid"
""",
            False,
            "MALFORMED_REVIEW_EVIDENCE:module_boundaries[0].extra",
        ),
        (
            """[[module_boundaries]]
path = "src/new.py"
owner_path = "src/owner.py"
basis = "invalid"
justification = "One boundary."
""",
            False,
            "MALFORMED_REVIEW_EVIDENCE:module_boundaries[0].basis",
        ),
        (
            """[[module_boundaries]]
path = "src/new.py"
owner_path = "src/owner.py"
basis = "responsibility"
justification = ""
""",
            False,
            "INSUFFICIENT_REVIEW_EVIDENCE:module_boundaries[0].justification",
        ),
        (
            """[[module_boundaries]]
path = "src/new.py"
owner_path = "src/owner.py"
basis = "responsibility"
justification = "One boundary."

[[module_boundaries]]
path = "src/new.py"
owner_path = "src/owner.py"
basis = "responsibility"
justification = "Duplicate boundary."
""",
            False,
            "MALFORMED_REVIEW_EVIDENCE:module_boundaries.path",
        ),
    ],
)
def test_module_boundary_defects_emit_exact_standard_four_result(
    tmp_path: Path,
    definition: str,
    top_level: bool,
    block: str,
) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _commit(repository, "base")
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_module_boundaries(definition, top_level=top_level),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [block]
    assert result["standard_blocks"] == [
        {"blocks": [block] if standard == 4 else [], "standard": standard}
        for standard in range(1, 9)
    ]


def test_valid_milestone_three_evidence_passes_and_reports_judgment(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("existing", 1),
        _function_source("existing", 1, 1),
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["review_evidence"]["behavior"]["proof"].endswith("::test_changed_behavior")
    assert result["review_evidence"]["human_review"] == {
        "cohesion": "Changed code remains cohesive.",
        "intended_behavior": "Reported behavior remains intended.",
        "naming": "Names express owned responsibilities.",
        "reviewability": "Change is small enough for direct review.",
    }
    markdown = (tmp_path / "result" / "complexity-result.md").read_text(encoding="utf-8")
    assert "## Structured review evidence" in markdown
    assert '"reviewability": "Change is small enough for direct review."' in markdown


def test_milestone_three_evidence_is_byte_identical(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("existing", 1),
        _function_source("existing", 1, 1),
    )
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_exit, _ = _evaluate(repository, base_sha, head_sha, first)
    second_exit, _ = _evaluate(repository, base_sha, head_sha, second)

    assert first_exit == second_exit == 0
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
