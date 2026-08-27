from __future__ import annotations

import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

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
    standard_results,
)


def test_reported_package_version_matches_distribution_version() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8"))
    assert complexity_metrics.tool_versions()["supportability_gate"] == __version__
    assert __version__ == metadata["project"]["version"]


WORKFLOW_SHA = "f" * 40
HANDOFF_SENTINEL = "DERIVED_FROM_AUTHENTICATED_EVIDENCE"


def _executed_quality_arguments(
    arguments: tuple[str, ...] | list[str],
    production_files: tuple[str, ...] | list[str],
    test_files: tuple[str, ...] | list[str],
) -> tuple[str, ...]:
    scalar_values = {
        "$LINT_IMPORTS": "lint-imports",
        "$NODE": "node",
        "$NPM": "npm",
        "$OUTPUT": "C:/quality",
        "$PYTHON": "python",
        "$REPOSITORY": "C:/repo/target",
        "$TOOLS": "C:/quality/quality-tools",
    }
    list_values = {
        "$SOURCE_FILES": tuple(f"C:/repo/target/{path}" for path in production_files),
        "$TEST_FILES": tuple(f"C:/repo/target/{path}" for path in test_files),
    }
    executed: list[str] = []
    for argument in arguments:
        if argument in list_values:
            executed.extend(list_values[argument])
            continue
        for token, replacement in scalar_values.items():
            argument = argument.replace(token, replacement)
        executed.append(argument)
    return tuple(executed)


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
summary = "DERIVED_FROM_AUTHENTICATED_EVIDENCE"
remaining_risks = ["DERIVED_FROM_AUTHENTICATED_EVIDENCE"]

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


def _review_evidence_with_boundary_rows(rows: str, *, new_path: str | None = None) -> str:
    document = _review_evidence_for_new_path(new_path) if new_path else REVIEW_EVIDENCE
    return document.replace(
        "\n[architecture]",
        f"\nboundaries = {rows}\n\n[architecture]",
    )


def _review_evidence_with_boundaries(
    *identities: tuple[str, str, str], new_path: str | None = None
) -> str:
    rows = ", ".join(
        "{{ path = {}, kind = {}, symbol = {}, before = {}, after = {} }}".format(
            *(json.dumps(value) for value in (*identity, "Before.", "After."))
        )
        for identity in identities
    )
    return _review_evidence_with_boundary_rows(f"[{rows}]", new_path=new_path)


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


def _literal_review_commit(repository: Path, message: str) -> str:
    """Commit only caller-written review evidence; S04 expectations stay independent."""
    _run_git(repository, "add", "--all")
    _run_git(repository, "commit", "-m", message)
    return _run_git(repository, "rev-parse", "HEAD")


def _commit(repository: Path, message: str) -> str:
    # Legacy non-S04 tests need valid Gate 2 config to keep testing their original concern.
    # S04 tests must use _literal_review_commit: production-derived rows cannot prove binding.
    _literal_review_commit(repository, message)
    has_parent = (
        subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD~1"],
            cwd=repository,
            check=False,
            capture_output=True,
            timeout=10,
        ).returncode
        == 0
    )
    if message != "base" and has_parent:
        review_path = repository / ".supportability-review.toml"
        review = review_path.read_text(encoding="utf-8")
        if "[separation_of_concerns]" in review and "boundaries =" not in review:
            try:
                records: list[git_changes.CommandRecord] = []
                identity = git_changes.inspect_repository(
                    repository,
                    _run_git(repository, "rev-parse", "HEAD~1"),
                    _run_git(repository, "rev-parse", "HEAD"),
                    records,
                )
                policy = contract.parse_contract(
                    git_changes.read_regular_blob(
                        repository, identity.base_sha, ".supportability.toml", records
                    ).content
                )
                changes = git_changes.changed_paths(
                    repository, identity.base_sha, identity.head_sha, records
                )
                assessments = cli._classify_changes(repository, identity, policy, changes, records)
                boundaries = cli._separation_boundaries(
                    repository, identity, policy, assessments, records
                )
            except (contract.ContractError, function_changes.PythonSourceError):
                boundaries = ()
            rows = ", ".join(
                "{{ path = {}, kind = {}, symbol = {}, before = {}, after = {} }}".format(
                    *(json.dumps(value) for value in (*boundary, "Before.", "After."))
                )
                for boundary in boundaries
            )
            _write(
                review_path,
                review.replace(
                    "\n[architecture]",
                    f"\nboundaries = [{rows}]\n\n[architecture]",
                ),
            )
            _run_git(repository, "add", ".supportability-review.toml")
            _run_git(repository, "commit", "--amend", "--no-edit")
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


def test_typescript_architecture_reads_exact_head_tsconfig_alias(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path, TYPESCRIPT_CONTRACT)
    _write(repository / "src" / "domain" / "model.ts", "export const value = 1;\n")
    _write(
        repository / "tsconfig.json",
        '{"compilerOptions":{"baseUrl":".","paths":{"@domain/*":["src/domain/*"]}}}\n',
    )
    base_sha = _commit(repository, "base")
    _write(
        repository / "src" / "application" / "useCase.ts",
        "import { value } from '@domain/model';\nexport const current = value;\n",
    )
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_for_new_path("src/application/useCase.ts").replace(
            'owner_path = "src/owner.ts"',
            'owner_path = "src/application/useCase.ts"',
        ),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["architecture"]["edges"] == [
        {
            "internal": True,
            "line": 1,
            "source": "src/application/useCase.ts",
            "specifier": "@domain/model",
            "target": "src/domain/model.ts",
        }
    ]
    assert any(
        command["arguments"] == ["cat-file", "blob", f"{head_sha}:tsconfig.json"]
        for command in result["commands"]
    )


def test_unresolved_typescript_alias_blocks_gate_three_through_real_evaluator(
    tmp_path: Path,
) -> None:
    repository = _initialize_repository(tmp_path, TYPESCRIPT_CONTRACT)
    _write(
        repository / "tsconfig.json",
        '{"compilerOptions":{"baseUrl":".","paths":{"@domain/*":["src/domain/*"]}}}\n',
    )
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.ts", "import { value } from '@domain/model';\n")
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_for_new_path("src/sample.ts"),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")
    aggregate = _compose_cli_result(result, base_sha, head_sha, "src/sample.ts")
    block = "UNRESOLVED_TYPESCRIPT_ALIAS:src/sample.ts:1:@domain/model"

    assert exit_code == 1
    assert result["overall_result"] == "BLOCK"
    assert result["architecture"]["blocks"] == [block]
    assert result["policy_blocks"] == [block]
    assert [entry["result"] for entry in aggregate["entries"]] == [
        "PASS",
        "PASS",
        "BLOCK",
        *["PASS"] * 5,
    ]
    assert aggregate["entries"][2]["policy_blocks"] == [block]
    assert all(entry["technical_errors"] == [] for entry in aggregate["entries"])
    assert aggregate["shared_failures"] == []


def _evaluate(
    repository: Path,
    base_sha: str,
    head_sha: str,
    output: Path,
    *,
    complexity_exit_code: int = 0,
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
        test_files = quality_profile.test_files(repository, head_sha, policy.language, records)
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
                complexity_exit_code
                if adapter == contract.COMPLEXITY_ADAPTERS[policy.language]
                else 0,
                hashlib.sha256(b"").hexdigest(),
                hashlib.sha256(b"").hexdigest(),
                hashlib.sha256(b"").hexdigest(),
                _executed_quality_arguments(arguments, production_files, test_files),
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
                test_files=test_files,
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


def _compose_cli_result(
    result: dict[str, Any], base_sha: str, head_sha: str, path: str = "src/sample.py"
) -> dict[str, Any]:
    identity = standard_results.RunIdentity(
        "example/fixture", 123, base_sha, head_sha, WORKFLOW_SHA, 456, 1
    )
    behavior = hashlib.sha256(
        json.dumps([["sample", "e" * 64]], separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    targets = result["responsibility_targets"]
    unbounded = result["unbounded_production_paths"]
    changed = result["changed_files"]
    assert isinstance(targets, list) and isinstance(unbounded, list)
    assert isinstance(changed, list)
    scope = sorted(
        {
            changed_path
            for row in changed
            for changed_path in (row["old_path"], row["new_path"])
            if changed_path
        }
    )
    characterization: dict[str, Any] = {
        "artifacts": {
            "base": {"capture_sha256": "3" * 64, "digest": "4" * 64, "id": "701"},
            "head": {"capture_sha256": "5" * 64, "digest": "6" * 64, "id": "702"},
        },
        "base_sha": base_sha,
        "behavior_fingerprint": behavior,
        "coverage": {
            "covered_paths": [path],
            "required_paths": [path],
        },
        "head_sha": head_sha,
        "manifest_blob_sha": "8" * 40,
        "manifest_sha256": "9" * 64,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": "github.com/example/fixture",
        "refactor_runnability": {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "repository": "github.com/example/fixture",
            "runnable": True,
            "schema_version": "refactor-runnability.v1",
            "targets": targets,
            "unbounded_paths": unbounded,
            "workflow_sha": WORKFLOW_SHA,
        },
        "scenarios": [
            {
                "base_behavior_sha256": "e" * 64,
                "command": ["python", "tests/characterization/sample.characterization.py"],
                "compatibility": "PASS",
                "covers": [path],
                "golden_behavior_sha256": "e" * 64,
                "head_behavior_sha256": "e" * 64,
                "id": "sample",
                "kind": "golden",
            }
        ],
        "schema_version": "characterization-result.v1",
        "workflow_sha": WORKFLOW_SHA,
    }
    characterization_sha = hashlib.sha256(
        json.dumps(characterization, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    refactor = {
        "applicable": True,
        "authorization": {
            "base_sha": base_sha,
            "broad": len(targets) != 1,
            "head_sha": head_sha,
            "repository": "example/fixture",
            "scope": scope,
            "sequence": {"predecessor_sha": base_sha, "step": 1},
            "targets": targets,
        },
        "authorization_comment_id": 11,
        "base_sha": base_sha,
        "characterization_sha256": characterization_sha,
        "changed_paths": scope,
        "head_sha": head_sha,
        "other_standard_clauses_waived": False,
        "overall_result": "PASS",
        "policy_blocks": [],
        "predecessor": {
            "authorization": None,
            "authorization_comment_id": None,
            "base_sha": None,
            "block": None,
            "head_sha": None,
            "merge_sha": None,
            "pull_number": None,
        },
        "repository": "example/fixture",
        "schema_version": "refactor-policy-result.v1",
        "targets": targets,
        "unbounded_paths": unbounded,
    }
    profile = result["quality_profile"]
    assert isinstance(profile, dict)
    commands = profile["commands"]
    assert isinstance(commands, list)
    production_files = profile["production_files"]
    test_files = profile["test_files"]
    assert isinstance(production_files, list) and isinstance(test_files, list)
    provenance = {
        "artifact_digest": "d" * 64,
        "artifact_id": "789",
        "capture_sha256": "c" * 64,
        "commands": [
            {
                "adapter": command["adapter"],
                "executed_arguments": list(
                    _executed_quality_arguments(command["arguments"], production_files, test_files)
                ),
                "raw_proof_sha256": "a" * 64,
                "stderr_sha256": "b" * 64,
                "stdout_sha256": "c" * 64,
            }
            for command in commands
        ],
        "job": "quality-profile",
        "repository": "example/fixture",
        "repository_id": "123",
        "run_attempt": "1",
        "run_id": "456",
        "runner_environment": "github-hosted",
    }
    capture_profile = {
        **profile,
        **{
            name: provenance[name]
            for name in (
                "job",
                "repository",
                "repository_id",
                "run_attempt",
                "run_id",
                "runner_environment",
            )
        },
        "artifact_digest": "",
        "artifact_id": "",
        "capture_sha256": "",
        "commands": [
            {**command, **proof}
            for command, proof in zip(commands, provenance["commands"], strict=True)
        ],
    }
    capture_sha256 = hashlib.sha256(
        (json.dumps(capture_profile, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()
    provenance["capture_sha256"] = capture_sha256
    return standard_results.compose_results(
        result,
        characterization,
        refactor,
        provenance,
        identity,
        expected_quality_artifact={
            "capture_sha256": capture_sha256,
            "digest": "d" * 64,
            "id": "789",
        },
        source_outcomes={
            "install": "success",
            "complexity": "failure",
            "characterization": "success",
            "refactor": "success",
            "quality": "success",
        },
    )


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


def test_new_complexity_11_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(tmp_path, None, _function_source("new", 11))

    exit_code, result = _evaluate(
        repository,
        base_sha,
        head_sha,
        tmp_path / "result",
        complexity_exit_code=1,
    )

    assert exit_code == 1
    assert result["overall_result"] == "BLOCK"
    assert result["functions"][0]["decision"] == "BLOCK"
    assert result["ruff_diagnostics"][0]["complexity"] == 11
    assert result["policy_blocks"] == []


def test_decorated_python_complexity_binds_ruff_to_definition_line(tmp_path: Path) -> None:
    base = "def marker(function: object) -> object:\n    return function\n"
    head = base + "\n@marker\n" + _function_source("decorated", 11)
    repository, base_sha, head_sha = _repository(tmp_path, base, head)

    exit_code, result = _evaluate(
        repository,
        base_sha,
        head_sha,
        tmp_path / "result",
        complexity_exit_code=1,
    )

    decorated = next(
        item for item in result["functions"] if item["head"]["qualified_name"] == "decorated"
    )
    diagnostic = result["ruff_diagnostics"][0]
    assert exit_code == 1
    assert decorated["head"]["start_line"] == 4
    assert diagnostic["qualified_name"] == "decorated"
    assert diagnostic["line"] == 5


def test_untouched_ruff_diagnostic_is_not_serialized_as_touched_evidence(
    tmp_path: Path,
) -> None:
    legacy = _function_source("legacy", 11)
    repository, base_sha, head_sha = _repository(
        tmp_path,
        legacy + "\n" + _function_source("changed", 1),
        legacy + "\n" + _function_source("changed", 1, 1),
    )

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["touched_qualified_functions"] == ["changed"]
    assert result["ruff_diagnostics"] == []


def test_legacy_14_to_12_passes_progressively(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("legacy", 14),
        _function_source("legacy", 12),
    )

    exit_code, result = _evaluate(
        repository,
        base_sha,
        head_sha,
        tmp_path / "result",
        complexity_exit_code=1,
    )

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
    assert result["policy_blocks"] == []


def test_complexity_adapter_tool_failure_remains_a_quality_block(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("clean", 1),
        _function_source("clean", 10),
    )

    exit_code, result = _evaluate(
        repository,
        base_sha,
        head_sha,
        tmp_path / "result",
        complexity_exit_code=2,
    )

    assert exit_code == 1
    assert result["policy_blocks"] == ["QUALITY_GATE_FAILED:python.c901-touched.v1"]


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

    exit_code, result = _evaluate(
        repository,
        base_sha,
        head_sha,
        tmp_path / "result",
        complexity_exit_code=1,
    )

    assert exit_code == 1
    assert result["functions"][0]["decision"] == "BLOCK"
    assert result["functions"][0]["ending_complexity"] == 11
    assert result["policy_blocks"] == []


def test_typescript_legacy_must_improve_and_reports_gap(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _typescript_repository(
        tmp_path,
        _typescript_source("legacyFlow", 14, 0),
        _typescript_source("legacyFlow", 12, 1),
    )

    exit_code, result = _evaluate(
        repository,
        base_sha,
        head_sha,
        tmp_path / "result",
        complexity_exit_code=1,
    )

    decision = result["functions"][0]
    assert exit_code == 0
    assert decision["decision"] == "PASS_PROGRESSIVE"
    assert decision["starting_complexity"] == 14
    assert decision["ending_complexity"] == 12
    assert decision["remaining_gap"] == 2
    assert decision["next_target"] == 10
    assert result["policy_blocks"] == []


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


def test_typescript_declarator_only_change_does_not_touch_gate_one_function(
    tmp_path: Path,
) -> None:
    base = "export const handler: (value: number) => number =\n  (value) => value + 1;\n"
    head = (
        "export const handler: (value: number) => number | undefined =\n  (value) => value + 1;\n"
    )
    repository, base_sha, head_sha = _typescript_repository(tmp_path, base, head)

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["touched_qualified_functions"] == []
    assert result["responsibility_targets"] == ["src/sample.ts::function:handler:1-2"]


def test_typescript_anonymous_callback_gets_stable_identity(tmp_path: Path) -> None:
    source = "export const values = [1].map((value) => value + 1);\n"
    repository, base_sha, head_sha = _typescript_repository(tmp_path, None, source)

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert result["touched_qualified_functions"] == ["anonymous@1:31"]


def test_typescript_metric_matches_eslint_classic_function_constructs() -> None:
    source = b"""\
export function eslintClassic(
  value: number = 0,
  box?: { next?: () => number; [key: string]: unknown },
): number {
  value &&= 1;
  box?.next;
  box?.["next"];
  box?.next?.();
  return value;
}
"""

    parsed = function_changes.parse_typescript_file("src/sample.ts", source)
    metrics = complexity_metrics.measure_definitions(parsed.functions, "typescript")

    assert [(item.span.qualified_name, item.complexity) for item in metrics] == [
        ("eslintClassic", 7)
    ]


def test_typescript_parameter_default_callback_has_own_identity_and_metric() -> None:
    source = b"""\
export function outer(
  callback = (value: number) => value > 0 ? 1 : 0,
): number {
  return callback(1);
}
"""

    parsed = function_changes.parse_typescript_file("src/sample.ts", source)
    metrics = {
        item.span.qualified_name: item.complexity
        for item in complexity_metrics.measure_definitions(parsed.functions, "typescript")
    }

    nested = next(name for name in metrics if name != "outer")
    assert nested.startswith("outer.anonymous@")
    assert metrics == {"outer": 2, nested: 2}


def test_typescript_class_code_paths_do_not_leak_into_enclosing_function() -> None:
    source = b"""\
export function outer(value: number): number {
  class Inner {
    [value || 0] = value || 0;
    static { if (value) { value += 1; } }
  }
  return value;
}
"""

    parsed = function_changes.parse_typescript_file("src/sample.ts", source)
    metrics = complexity_metrics.measure_definitions(parsed.functions, "typescript")

    assert [(item.span.qualified_name, item.complexity) for item in metrics] == [("outer", 2)]


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


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_complexity_poison_is_byte_identical(language: str, tmp_path: Path) -> None:
    if language == "python":
        base = _function_source("existing", 1)
        repository, base_sha, head_sha = _repository(
            tmp_path, base, base + "\n" + _function_source("poison", 11)
        )
    else:
        base = _typescript_source("existing", 1)
        repository, base_sha, head_sha = _typescript_repository(
            tmp_path, base, base + "\n" + _typescript_source("poison", 11)
        )
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_exit, first_result = _evaluate(
        repository, base_sha, head_sha, first, complexity_exit_code=1
    )
    second_exit, _ = _evaluate(repository, base_sha, head_sha, second, complexity_exit_code=1)

    assert first_exit == second_exit == 1
    assert first_result["policy_blocks"] == []
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
    assert '"review_handoff"' not in markdown
    assert HANDOFF_SENTINEL not in markdown


def test_review_handoff_binds_exact_base_and_head_blobs(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    _write(
        repository / ".supportability-review.toml",
        REVIEW_EVIDENCE.replace(
            "Names express owned responsibilities.",
            "Names precisely express owned responsibilities.",
        ),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    base_blob = git_changes.read_regular_blob(
        repository, base_sha, ".supportability-review.toml", []
    )
    head_blob = git_changes.read_regular_blob(
        repository, head_sha, ".supportability-review.toml", []
    )
    assert exit_code == 0
    assert result["review_evidence_binding"] == {
        "base": {
            "blob_sha": base_blob.object_sha,
            "sha256": hashlib.sha256(base_blob.content).hexdigest(),
        },
        "head": {
            "blob_sha": head_blob.object_sha,
            "sha256": hashlib.sha256(head_blob.content).hexdigest(),
        },
    }


def test_unsupported_handoff_summary_blocks(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    _write(
        repository / ".supportability-review.toml",
        REVIEW_EVIDENCE.replace(
            f'summary = "{HANDOFF_SENTINEL}"',
            'summary = "All gates green; fictional-check --all passed."',
        ),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == ["UNSUPPORTED_HANDOFF_CLAIM:review_handoff.summary"]


def test_false_no_risk_handoff_claim_blocks(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    _write(
        repository / ".supportability-review.toml",
        REVIEW_EVIDENCE.replace(
            f'remaining_risks = ["{HANDOFF_SENTINEL}"]',
            'remaining_risks = ["No risks, gaps, or follow-up work."]',
        ),
    )
    head_sha = _commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == ["UNSUPPORTED_HANDOFF_CLAIM:review_handoff.remaining_risks"]


def _boundary_identities(result: dict[str, object]) -> list[tuple[str, str, str]]:
    review = result["review_evidence"]
    assert isinstance(review, dict)
    separation = review["separation_of_concerns"]
    assert isinstance(separation, dict)
    boundaries = separation["boundaries"]
    assert isinstance(boundaries, list)
    return [(row["path"], row["kind"], row["symbol"]) for row in boundaries]


def test_modified_boundary_binds_retained_head_identity(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("existing", 1, 1))
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(
            ("src/sample.py", "function", "existing"),
        ),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert _boundary_identities(result) == [("src/sample.py", "function", "existing")]


def test_deleted_boundary_binds_base_identity(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("removed", 1))
    base_sha = _literal_review_commit(repository, "base")
    (repository / "src" / "sample.py").unlink()
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(("src/sample.py", "function", "removed")),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert _boundary_identities(result) == [("src/sample.py", "function", "removed")]


def test_deleted_boundary_in_retained_file_does_not_bind_unchanged_neighbor(
    tmp_path: Path,
) -> None:
    repository = _initialize_repository(tmp_path)
    _write(
        repository / "src" / "sample.py",
        _function_source("removed", 1) + _function_source("retained", 1),
    )
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("retained", 1))
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(("src/sample.py", "function", "removed")),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert _boundary_identities(result) == [("src/sample.py", "function", "removed")]


def test_renamed_boundary_binds_base_and_head_identities(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "old.py", _function_source("existing", 1))
    base_sha = _literal_review_commit(repository, "base")
    _run_git(repository, "mv", "src/old.py", "src/new.py")
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(
            ("src/old.py", "function", "existing"),
            ("src/new.py", "function", "existing"),
            new_path="src/new.py",
        ),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert sorted(_boundary_identities(result)) == [
        ("src/new.py", "function", "existing"),
        ("src/old.py", "function", "existing"),
    ]


def test_added_tsx_boundary_binds_component_and_function_identities(tmp_path: Path) -> None:
    source = """\
export class Widget extends React.Component {
  render() { return null; }
}
"""
    repository = _initialize_repository(tmp_path, TYPESCRIPT_CONTRACT)
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.tsx", source)
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(
            ("src/sample.tsx", "component", "Widget"),
            ("src/sample.tsx", "function", "Widget.render"),
            new_path="src/sample.tsx",
        ),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert sorted(_boundary_identities(result)) == [
        ("src/sample.tsx", "component", "Widget"),
        ("src/sample.tsx", "function", "Widget.render"),
    ]


def test_module_boundary_uses_path_as_symbol(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", "SETTING = 1\n")
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", "SETTING = 2\n")
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(
            ("src/sample.py", "module", "src/sample.py"),
        ),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert _boundary_identities(result) == [
        ("src/sample.py", "module", "src/sample.py"),
    ]


def test_empty_boundaries_pass_when_diff_has_no_applicable_identity(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "README.md", "one line\n")
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 0
    assert _boundary_identities(result) == []


@pytest.mark.parametrize(
    ("rows", "block"),
    [
        (None, "MISSING"),
        ('"bad"', "MALFORMED"),
        ("[]", "INSUFFICIENT"),
        (
            '[{ path = "src/sample.py", kind = "function", symbol = "current", before = "Before.", after = "After." }, { path = "src/sample.py", kind = "function", symbol = "current", before = "Before.", after = "After." }]',
            "MALFORMED",
        ),
        (
            '[{ path = "src/sample.py", kind = "function", symbol = "missing", before = "Before.", after = "After." }]',
            "INSUFFICIENT",
        ),
        (
            '[{ path = "src/sample.py", kind = "function", symbol = "unchanged", before = "Before.", after = "After." }]',
            "INSUFFICIENT",
        ),
        (
            '[{ path = "src/old.py", kind = "function", symbol = "current", before = "Before.", after = "After." }]',
            "INSUFFICIENT",
        ),
        (
            '[{ path = "src/sample.py", kind = "component", symbol = "current", before = "Before.", after = "After." }]',
            "INSUFFICIENT",
        ),
    ],
    ids=[
        "missing",
        "malformed",
        "empty",
        "duplicate",
        "nonexistent",
        "unchanged",
        "stale",
        "mismatched",
    ],
)
def test_real_diff_separation_boundary_poison_blocks(
    tmp_path: Path, rows: str | None, block: str
) -> None:
    repository = _initialize_repository(tmp_path)
    base_source = _function_source("current", 1) + _function_source("unchanged", 1)
    head_source = _function_source("current", 1, 1) + _function_source("unchanged", 1)
    _write(repository / "src" / "sample.py", base_source)
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", head_source)
    _write(
        repository / ".supportability-review.toml",
        REVIEW_EVIDENCE if rows is None else _review_evidence_with_boundary_rows(rows),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [f"{block}_REVIEW_EVIDENCE:separation_of_concerns.boundaries"]


def test_extra_nonduplicate_boundary_rejects_cardinality_mismatch(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("current", 1))
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("current", 1, 1))
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(
            ("src/sample.py", "function", "current"),
            ("src/sample.py", "function", "extra"),
        ),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 1
    assert result["policy_blocks"] == [
        "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries"
    ]


@pytest.mark.parametrize(
    ("rows", "block"),
    [
        (
            '[{ path = "src/sample.py", kind = "function", symbol = "current", before = "Before." }]',
            "MISSING_REVIEW_EVIDENCE:separation_of_concerns.boundaries[0].after",
        ),
        (
            '[{ path = "src/sample.py", kind = "function", symbol = "current", before = "Before.", after = "" }]',
            "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries[0].after",
        ),
        (
            '[{ path = "src/sample.py", kind = "function", symbol = "current", before = "Before.", after = 1 }]',
            "MALFORMED_REVIEW_EVIDENCE:separation_of_concerns.boundaries[0].after",
        ),
    ],
    ids=["missing-row-key", "empty-row-field", "malformed-row-field"],
)
def test_real_indexed_boundary_poison_blocks_only_gate_two_after_aggregation(
    tmp_path: Path, rows: str, block: str
) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("current", 1))
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("current", 1, 1))
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundary_rows(rows),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")
    aggregate = _compose_cli_result(result, base_sha, head_sha)

    assert exit_code == 1
    assert result["policy_blocks"] == [block]
    assert [entry["result"] for entry in aggregate["entries"]] == [
        "PASS",
        "BLOCK",
        *["PASS"] * 6,
    ]
    assert aggregate["entries"][1]["policy_blocks"] == [block]
    assert all(entry["technical_errors"] == [] for entry in aggregate["entries"])
    assert aggregate["shared_failures"] == []


def test_cross_lane_review_poison_still_validates_gate_two_boundaries(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("current", 1))
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("current", 1, 1))
    _write(
        repository / ".supportability-review.toml",
        REVIEW_EVIDENCE.replace(
            'dependency_direction = "Dependencies continue to point toward domain policy."',
            "dependency_direction = 1",
        ),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")
    aggregate = _compose_cli_result(result, base_sha, head_sha)

    assert exit_code == 1
    assert result["policy_blocks"] == [
        "MALFORMED_REVIEW_EVIDENCE:architecture.dependency_direction",
        "MISSING_REVIEW_EVIDENCE:separation_of_concerns.boundaries",
    ]
    assert [entry["result"] for entry in aggregate["entries"]] == [
        "PASS",
        "BLOCK",
        "BLOCK",
        *["PASS"] * 5,
    ]
    assert all(entry["technical_errors"] == [] for entry in aggregate["entries"])
    assert aggregate["shared_failures"] == []


def test_cross_lane_review_poison_preserves_valid_gate_two_evidence(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("current", 1))
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("current", 1, 1))
    review = _review_evidence_with_boundaries(("src/sample.py", "function", "current"))
    _write(
        repository / ".supportability-review.toml",
        review.replace(
            'dependency_direction = "Dependencies continue to point toward domain policy."',
            "dependency_direction = 1",
        ),
    )
    head_sha = _literal_review_commit(repository, "head")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")
    aggregate = _compose_cli_result(result, base_sha, head_sha)

    assert exit_code == 1
    assert result["policy_blocks"] == [
        "MALFORMED_REVIEW_EVIDENCE:architecture.dependency_direction"
    ]
    assert _boundary_identities(result) == [("src/sample.py", "function", "current")]
    assert [entry["result"] for entry in aggregate["entries"]] == [
        "PASS",
        "PASS",
        "BLOCK",
        *["PASS"] * 5,
    ]
    assert all(entry["technical_errors"] == [] for entry in aggregate["entries"])
    assert aggregate["shared_failures"] == []


def test_separation_boundary_outputs_are_byte_identical(tmp_path: Path) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("current", 1))
    base_sha = _literal_review_commit(repository, "base")
    _write(repository / "src" / "sample.py", _function_source("current", 1, 1))
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(("src/sample.py", "function", "current")),
    )
    valid_sha = _literal_review_commit(repository, "valid")

    valid_first_exit, _ = _evaluate(repository, base_sha, valid_sha, tmp_path / "valid-first")
    valid_second_exit, _ = _evaluate(repository, base_sha, valid_sha, tmp_path / "valid-second")
    valid_first = (tmp_path / "valid-first" / "complexity-result.json").read_bytes()
    valid_second = (tmp_path / "valid-second" / "complexity-result.json").read_bytes()

    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(
            ("src/sample.py", "function", "current"),
            ("src/sample.py", "function", "extra"),
        ),
    )
    poisoned_sha = _literal_review_commit(repository, "poisoned")
    poison_first_exit, _ = _evaluate(repository, base_sha, poisoned_sha, tmp_path / "poison-first")
    poison_second_exit, _ = _evaluate(
        repository, base_sha, poisoned_sha, tmp_path / "poison-second"
    )
    poison_first = (tmp_path / "poison-first" / "complexity-result.json").read_bytes()
    poison_second = (tmp_path / "poison-second" / "complexity-result.json").read_bytes()

    assert valid_first_exit == valid_second_exit == 0
    assert poison_first_exit == poison_second_exit == 1
    assert valid_first == valid_second
    assert poison_first == poison_second
    assert valid_first != poison_first


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
    assert [item["code"] for item in result["technical_errors"]] == [
        "SEPARATION_BOUNDARY_DERIVATION_FAILURE",
        "COMPLEXITY_SYNTAX_ERROR",
        "ARCHITECTURE_SYNTAX_ERROR",
    ]
    assert result["review_evidence"] is not None
    assert result["quality_profile"] is not None
    assert (tmp_path / "result" / "quality-provenance.json").is_file()


def test_refactor_target_derivation_failure_preserves_other_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base_sha, head_sha = _repository(
        tmp_path,
        _function_source("existing", 1),
        _function_source("existing", 1, 1),
    )

    def fail(*args: object, **kwargs: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
        raise git_changes.GitError("GIT_TIMEOUT", "target derivation timed out")

    monkeypatch.setattr(cli.refactor_targets, "derive", fail)

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 2
    assert [item["code"] for item in result["technical_errors"]] == [
        "REFACTOR_TARGET_DERIVATION_FAILURE"
    ]
    assert result["touched_qualified_functions"] == ["existing"]
    assert result["review_evidence"] is not None
    assert result["architecture"] is not None
    assert result["modularity"] is not None
    assert result["quality_profile"] is not None


def test_non_regular_production_source_reports_boundary_derivation_failure(
    tmp_path: Path,
) -> None:
    repository = _initialize_repository(tmp_path)
    _write(repository / "src" / "sample.py", _function_source("existing", 1))
    base_sha = _literal_review_commit(repository, "base")
    _write(
        repository / ".supportability-review.toml",
        _review_evidence_with_boundaries(("src/sample.py", "function", "existing")),
    )
    _run_git(repository, "add", ".supportability-review.toml")
    link = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repository,
            input=b"elsewhere.py\n",
            check=True,
            capture_output=True,
            timeout=10,
        )
        .stdout.decode()
        .strip()
    )
    _run_git(repository, "update-index", "--add", "--cacheinfo", f"120000,{link},src/sample.py")
    _run_git(repository, "commit", "-m", "head")
    head_sha = _run_git(repository, "rev-parse", "HEAD")

    exit_code, result = _evaluate(repository, base_sha, head_sha, tmp_path / "result")

    assert exit_code == 2
    assert "SEPARATION_BOUNDARY_DERIVATION_FAILURE" in {
        item["code"] for item in result["technical_errors"]
    }
    assert result["review_evidence"] is not None


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
