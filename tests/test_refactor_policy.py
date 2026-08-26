from __future__ import annotations

import http.client
import json
import subprocess
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from supportability_gate import (
    contract,
    git_changes,
    refactor_policy,
    refactor_targets,
)

PYTHON_CONTRACT = """\
schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = []

[[gates]]
adapter = "python.pytest.v1"
paths = ["src"]

[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
"""


class _Reply:
    def __init__(self, value: object, link: str | None = None) -> None:
        self.content = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.headers = {"Link": link} if link else {}

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, *args: object) -> bytes:
        return self.content


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _repository(tmp_path: Path, language: str = "python") -> tuple[Path, str, str]:
    repository = tmp_path / "fixture"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "remote", "add", "origin", "https://github.com/example/fixture.git")
    contract = PYTHON_CONTRACT
    path = "src/sample.py"
    source = "def calculate(value: int) -> int:\n    return value + 1\n"
    if language in {"typescript", "tsx"}:
        contract = contract.replace('language = "python"', 'language = "typescript"').replace(
            'adapter = "python.c901-touched.v1"',
            'adapter = "typescript.c901-equivalent-touched.v1"',
        )
        if language == "tsx":
            path = "src/sample.tsx"
            source = (
                "export function Card(value: number) {\n"
                "  return <section>{value + 1}</section>;\n"
                "}\n"
            )
        else:
            path = "src/sample.ts"
            source = "export function calculate(value: number): number {\n  return value + 1;\n}\n"
    _write(repository / ".supportability.toml", contract)
    _write(repository / path, source)
    base_sha = _commit(repository, "base")
    _write(repository / path, source.replace("+ 1", "+ 2"))
    head_sha = _commit(repository, "head")
    return repository, base_sha, head_sha


def _authorization(
    base_sha: str,
    head_sha: str,
    scope: list[str],
    targets: list[str],
    *,
    broad: bool = False,
    predecessor_sha: str | None = None,
    repository: str = "example/fixture",
    step: int = 1,
) -> str:
    value = {
        "base_sha": base_sha,
        "broad": broad,
        "head_sha": head_sha,
        "repository": repository,
        "schema_version": "1.0",
        "scope": sorted(scope),
        "sequence": {"predecessor_sha": predecessor_sha or base_sha, "step": step},
        "targets": sorted(targets),
    }
    return refactor_policy.AUTHORIZATION_PREFIX + json.dumps(value, separators=(",", ":"))


def _event(base_sha: str, head_sha: str, body: str | None) -> dict[str, object]:
    return {
        "repository": {"full_name": "example/fixture"},
        "pull_request": {
            "author_association": "MEMBER",
            "base": {"sha": base_sha},
            "body": body,
            "head": {"sha": head_sha},
            "number": 7,
            "user": {
                "id": refactor_policy.TRUSTED_OWNER_ID,
                "login": "markheck-solutions",
            },
        },
    }


def _characterization(
    base_sha: str,
    head_sha: str,
    paths: list[str],
    *,
    runnable: bool = True,
) -> dict[str, object]:
    return {
        "base_sha": base_sha,
        "coverage": {"covered_paths": paths, "required_paths": paths},
        "head_sha": head_sha,
        "overall_result": "PASS" if runnable else "BLOCK",
        "policy_blocks": [] if runnable else ["GOLDEN_BEHAVIOR_MISMATCH:sample"],
        "repository": "github.com/example/fixture",
        "scenarios": [{"compatibility": "PASS" if runnable else "BLOCK", "covers": paths}],
        "schema_version": refactor_policy.CHARACTERIZATION_SCHEMA,
        "workflow_sha": "f" * 40,
    }


def _derived_targets(
    repository: Path, base_sha: str, head_sha: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    records: list[git_changes.CommandRecord] = []
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
    policy = contract.parse_contract(
        git_changes.read_regular_blob(
            repository, identity.base_sha, ".supportability.toml", records
        ).content
    )
    changes = git_changes.changed_paths(repository, identity.base_sha, identity.head_sha, records)
    return refactor_targets.derive(repository, identity, policy, changes, records)


def _verify(
    repository: Path,
    event: dict[str, object],
    characterization: dict[str, object],
    *,
    owner_id: int = refactor_policy.TRUSTED_OWNER_ID,
    predecessor: refactor_policy.Authorization | None = None,
    predecessor_block: str | None = None,
    add_runnability: bool = True,
    runnable: bool = True,
) -> dict[str, object]:
    if add_runnability and "refactor_runnability" not in characterization:
        records: list[git_changes.CommandRecord] = []
        pull = event["pull_request"]
        assert isinstance(pull, dict)
        base = pull["base"]
        head = pull["head"]
        assert isinstance(base, dict) and isinstance(head, dict)
        identity = git_changes.inspect_repository(
            repository, str(base["sha"]), str(head["sha"]), records
        )
        policy = contract.parse_contract(
            git_changes.read_regular_blob(
                repository, identity.base_sha, ".supportability.toml", records
            ).content
        )
        changes = git_changes.changed_paths(
            repository, identity.base_sha, identity.head_sha, records
        )
        targets, unbounded = refactor_targets.derive(repository, identity, policy, changes, records)
        characterization["refactor_runnability"] = {
            "base_sha": identity.base_sha,
            "head_sha": identity.head_sha,
            "repository": identity.remote,
            "runnable": runnable,
            "schema_version": refactor_policy.RUNNABILITY_SCHEMA,
            "targets": list(targets),
            "unbounded_paths": list(unbounded),
            "workflow_sha": characterization["workflow_sha"],
        }
    body = event["pull_request"]["body"]  # type: ignore[index]
    comments = () if body is None else ({"body": body, "id": 11, "user": {"id": owner_id}},)
    pull = event["pull_request"]
    assert isinstance(pull, dict)
    base = pull["base"]
    assert isinstance(base, dict)
    predecessor_evidence = refactor_policy.PredecessorEvidence(block=predecessor_block)
    if predecessor is not None:
        predecessor_evidence = refactor_policy.PredecessorEvidence(
            predecessor,
            10,
            predecessor.base_sha,
            None,
            predecessor.head_sha,
            str(base["sha"]),
            6,
        )
    return refactor_policy.verify_refactor(
        repository, event, characterization, comments, predecessor_evidence
    )


@pytest.mark.parametrize("code", ["MALFORMED_GITHUB_EVENT", "MALFORMED_CHARACTERIZATION_RESULT"])
def test_json_inputs_reject_duplicate_root_keys(tmp_path: Path, code: str) -> None:
    path = tmp_path / "duplicate.json"
    _write(path, '{"value":1,"value":2}')

    with pytest.raises(refactor_policy.RefactorPolicyError, match=code):
        refactor_policy._read_json(path, code)


@pytest.mark.parametrize(
    ("language", "path", "kind", "name"),
    [
        ("python", "src/sample.py", "function", "calculate"),
        ("typescript", "src/sample.ts", "function", "calculate"),
        ("tsx", "src/sample.tsx", "component", "Card"),
    ],
)
def test_bounded_runnable_python_and_typescript_refactors_pass(
    tmp_path: Path, language: str, path: str, kind: str, name: str
) -> None:
    repository, base_sha, head_sha = _repository(tmp_path, language)
    end_line = 2 if language == "python" else 3
    target = f"{path}::{kind}:{name}:1-{end_line}"
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, [path], [target]))
    characterization = _characterization(base_sha, head_sha, [path])

    assert _derived_targets(repository, base_sha, head_sha) == ((target,), ())
    first = _verify(repository, event, characterization)
    second = _verify(repository, event, characterization)

    assert first == second
    assert first["overall_result"] == "PASS"
    assert first["targets"] == [target]
    assert first["other_standard_clauses_waived"] is False


def test_gate_five_policy_block_does_not_manufacture_gate_six_block(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    path = "src/sample.py"
    target = f"{path}::function:calculate:1-2"
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, [path], [target]))
    characterization = _characterization(base_sha, head_sha, [path])
    characterization["overall_result"] = "BLOCK"
    characterization["policy_blocks"] = [f"MISSING_CHARACTERIZATION_COVERAGE:{path}"]
    coverage = characterization["coverage"]
    assert isinstance(coverage, dict)
    coverage["covered_paths"] = []

    result = _verify(repository, event, characterization)

    assert result["overall_result"] == "PASS"
    assert result["policy_blocks"] == []


@pytest.mark.parametrize(
    ("defect", "expected"),
    [
        ("authorization", "STALE_OWNER_AUTHORIZATION"),
        ("schema", "UNAUTHENTICATED_RUNNABILITY_EVIDENCE"),
        ("identity", "STALE_RUNNABILITY_EVIDENCE"),
    ],
)
def test_gate_five_block_preserves_independent_gate_six_blocks(
    tmp_path: Path, defect: str, expected: str
) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    path = "src/sample.py"
    target = f"{path}::function:calculate:1-2"
    authorized_head = "f" * 40 if defect == "authorization" else head_sha
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, authorized_head, [path], [target]),
    )
    characterization = _characterization(base_sha, head_sha, [path], runnable=False)
    if defect == "schema":
        characterization["schema_version"] = "0.0"
    elif defect == "identity":
        characterization["head_sha"] = "f" * 40

    result = _verify(repository, event, characterization)

    assert result["policy_blocks"] == [expected]


def test_repo_wide_cleanup_requires_exact_broad_authorization(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(
        repository / "src/sample.py", "def calculate(value: int) -> int:\n    return value + 2\n"
    )
    _write(repository / "src/other.py", "def normalize(value: int) -> int:\n    return value + 1\n")
    head_sha = _commit(repository, "wide")
    paths = ["src/other.py", "src/sample.py"]
    targets = [
        "src/other.py::function:normalize:1-2",
        "src/sample.py::function:calculate:1-2",
    ]
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, paths, targets))

    result = _verify(repository, event, _characterization(base_sha, head_sha, paths))

    assert result["overall_result"] == "BLOCK"
    assert result["policy_blocks"] == ["BROAD_AUTHORIZATION_REQUIRED"]


def test_exact_broad_authorization_cannot_waive_other_clauses(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(
        repository / "src/sample.py", "def calculate(value: int) -> int:\n    return value + 2\n"
    )
    _write(repository / "src/other.py", "def normalize(value: int) -> int:\n    return value + 1\n")
    head_sha = _commit(repository, "wide")
    paths = ["src/other.py", "src/sample.py"]
    targets = [
        "src/other.py::function:normalize:1-2",
        "src/sample.py::function:calculate:1-2",
    ]
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, paths, targets, broad=True),
    )

    result = _verify(repository, event, _characterization(base_sha, head_sha, paths))

    assert result["overall_result"] == "PASS"
    assert result["other_standard_clauses_waived"] is False


def test_unrelated_churn_requires_broad_authorization(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(
        repository / "src/sample.py", "def calculate(value: int) -> int:\n    return value + 2\n"
    )
    _write(repository / "docs/churn.md", "unrelated\n")
    head_sha = _commit(repository, "churn")
    scope = ["docs/churn.md", "src/sample.py"]
    target = "src/sample.py::function:calculate:1-2"
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, scope, [target]))

    result = _verify(repository, event, _characterization(base_sha, head_sha, ["src/sample.py"]))

    assert result["policy_blocks"] == ["BROAD_AUTHORIZATION_REQUIRED"]


def test_multiple_unbounded_targets_block(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(
        repository / "src/sample.py",
        "def calculate(value: int) -> int:\n    return value + 2\n\n"
        "def normalize(value: int) -> int:\n    return value + 1\n",
    )
    head_sha = _commit(repository, "multiple")
    event = _event(
        base_sha,
        head_sha,
        _authorization(
            base_sha,
            head_sha,
            ["src/sample.py"],
            ["src/sample.py::module:*:1-1"],
        ),
    )

    result = _verify(repository, event, _characterization(base_sha, head_sha, ["src/sample.py"]))

    assert "UNVERIFIABLE_BOUNDED_TARGET" in result["policy_blocks"]
    assert "BROAD_AUTHORIZATION_REQUIRED" in result["policy_blocks"]


def test_non_runnable_intermediate_state_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    path = "src/sample.py"
    target = f"{path}::function:calculate:1-2"
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, [path], [target]))
    characterization = _characterization(base_sha, head_sha, [path])
    result = _verify(repository, event, characterization, runnable=False)

    assert result["policy_blocks"] == ["NON_RUNNABLE_LOGICAL_STEP"]


@pytest.mark.parametrize(
    ("defect", "expected"),
    [
        ("missing", "UNAUTHENTICATED_RUNNABILITY_EVIDENCE"),
        ("schema", "UNAUTHENTICATED_RUNNABILITY_EVIDENCE"),
        ("identity", "STALE_RUNNABILITY_EVIDENCE"),
        ("targets", "UNAUTHENTICATED_RUNNABILITY_EVIDENCE"),
    ],
)
def test_runnability_extension_failures_are_exact(
    tmp_path: Path, defect: str, expected: str
) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    path = "src/sample.py"
    target = f"{path}::function:calculate:1-2"
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, [path], [target]))
    characterization = _characterization(base_sha, head_sha, [path])
    if defect == "missing":
        result = _verify(repository, event, characterization, add_runnability=False)
    else:
        _verify(repository, event, characterization)
        evidence = characterization["refactor_runnability"]
        assert isinstance(evidence, dict)
        if defect == "schema":
            evidence["schema_version"] = "0.0"
        elif defect == "identity":
            evidence["head_sha"] = "f" * 40
        else:
            evidence["targets"] = ["src/sample.py::function:forged:999-999"]
        result = _verify(repository, event, characterization)

    assert result["policy_blocks"] == [expected]


def test_pass_claim_with_policy_blocks_checks_remaining_runnability(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    path = "src/sample.py"
    target = f"{path}::function:calculate:1-2"
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, [path], [target]))
    characterization = _characterization(base_sha, head_sha, [path])
    characterization["policy_blocks"] = ["GOLDEN_BEHAVIOR_MISMATCH:sample"]
    coverage = characterization["coverage"]
    assert isinstance(coverage, dict)
    coverage["covered_paths"] = []

    result = _verify(repository, event, characterization)

    assert result["policy_blocks"] == [
        "MISSING_RUNNABILITY_COVERAGE",
        "NON_RUNNABLE_LOGICAL_STEP",
    ]


def test_missing_owner_authorization_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    result = _verify(
        repository,
        _event(base_sha, head_sha, None),
        _characterization(base_sha, head_sha, ["src/sample.py"]),
    )
    assert result["policy_blocks"] == ["MISSING_OWNER_AUTHORIZATION"]


@pytest.mark.parametrize("field", ["base_sha", "head_sha", "predecessor_sha"])
def test_authorization_requires_string_shas(field: str) -> None:
    body = _authorization(
        "1" * 40,
        "2" * 40,
        ["src/sample.py"],
        ["src/sample.py::function:calculate:1-2"],
    )
    value = json.loads(body.removeprefix(refactor_policy.AUTHORIZATION_PREFIX))
    if field == "predecessor_sha":
        value["sequence"][field] = int("1" * 40)
    else:
        value[field] = int("1" * 40)

    with pytest.raises(refactor_policy.RefactorPolicyError, match="MALFORMED_OWNER_AUTHORIZATION"):
        refactor_policy._parse_authorization(
            refactor_policy.AUTHORIZATION_PREFIX + json.dumps(value, separators=(",", ":"))
        )


@pytest.mark.parametrize("nested", [False, True])
def test_authorization_rejects_duplicate_json_keys(nested: bool) -> None:
    raw = _authorization(
        "1" * 40,
        "2" * 40,
        ["src/sample.py"],
        ["src/sample.py::function:calculate:1-2"],
    )
    if nested:
        raw = raw.replace(
            '"sequence":{"predecessor_sha":',
            '"sequence":{"predecessor_sha":"' + "3" * 40 + '","predecessor_sha":',
        )
    else:
        raw = raw.replace('"base_sha":', '"base_sha":"' + "3" * 40 + '","base_sha":', 1)

    with pytest.raises(refactor_policy.RefactorPolicyError, match="MALFORMED_OWNER_AUTHORIZATION"):
        refactor_policy._parse_authorization(raw)


def test_multiple_current_owner_authorizations_are_malformed() -> None:
    head_sha = "2" * 40
    body = _authorization(
        "1" * 40,
        head_sha,
        ["src/sample.py"],
        ["src/sample.py::function:calculate:1-2"],
    )
    comments = tuple(
        {"body": body, "id": comment_id, "user": {"id": refactor_policy.TRUSTED_OWNER_ID}}
        for comment_id in (11, 12)
    )

    with pytest.raises(refactor_policy.RefactorPolicyError, match="MALFORMED_OWNER_AUTHORIZATION"):
        refactor_policy._owner_authorization(_event("1" * 40, head_sha, None), comments)
    with pytest.raises(refactor_policy.RefactorPolicyError, match="MALFORMED_OWNER_AUTHORIZATION"):
        refactor_policy._parse_authorization(f"{body}\n{body}")


def test_refactor_targets_deletion_uses_base_responsibilities(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    (repository / "src/sample.py").unlink()
    head_sha = _commit(repository, "delete")
    path = "src/sample.py"
    target = f"{path}::function:calculate:1-2"
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, [path], [target]),
    )

    assert _derived_targets(repository, base_sha, head_sha) == ((target,), ())
    first = _verify(repository, event, _characterization(base_sha, head_sha, [path]))
    second = _verify(repository, event, _characterization(base_sha, head_sha, [path]))

    assert first == second
    assert first["overall_result"] == "PASS"
    assert first["targets"] == [target]
    assert first["unbounded_paths"] == []


def test_deleted_target_without_run_coverage_blocks(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    (repository / "src/sample.py").unlink()
    head_sha = _commit(repository, "delete without coverage")
    path = "src/sample.py"
    target = f"{path}::function:calculate:1-2"
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, [path], [target]),
    )

    result = _verify(repository, event, _characterization(base_sha, head_sha, []))

    assert result["policy_blocks"] == ["MISSING_RUNNABILITY_COVERAGE"]


def test_refactor_targets_move_out_uses_base_responsibility(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _git(repository, "mv", "src/sample.py", "sample.py")
    head_sha = _commit(repository, "move out of production")
    scope = ["sample.py", "src/sample.py"]
    target = "src/sample.py::function:calculate:1-2"
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, scope, [target], broad=True),
    )

    assert _derived_targets(repository, base_sha, head_sha) == ((target,), ())
    result = _verify(repository, event, _characterization(base_sha, head_sha, ["src/sample.py"]))

    assert result["applicable"] is True
    assert result["overall_result"] == "PASS"
    assert result["targets"] == [target]


def test_malformed_move_out_of_production_is_unbounded(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    path = "src/broken.py"
    _write(repository / path, "def broken(:\n")
    base_sha = _commit(repository, "malformed production base")
    _git(repository, "mv", path, "broken.py")
    head_sha = _commit(repository, "move malformed source out of production")
    event = _event(
        base_sha,
        head_sha,
        _authorization(
            base_sha,
            head_sha,
            ["broken.py", path],
            [f"{path}::module:{path}:1-1"],
            broad=True,
        ),
    )

    result = _verify(repository, event, _characterization(base_sha, head_sha, []))

    assert result["unbounded_paths"] == [path]
    assert result["policy_blocks"] == [
        "MISSING_BOUNDED_PRODUCTION_TARGET",
        "UNVERIFIABLE_BOUNDED_TARGET",
    ]


def test_refactor_targets_rename_keeps_modified_and_deleted_responsibilities(
    tmp_path: Path,
) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(
        repository / "src/sample.py",
        "def calculate(value: int) -> int:\n    return value + 1\n\n"
        "def normalize(value: int) -> int:\n    return value - 1\n\n"
        "def keep_a(value: int) -> int:\n    return value * 2\n\n"
        "def keep_b(value: int) -> int:\n    return value // 2\n",
    )
    base_sha = _commit(repository, "two production responsibilities")
    _git(repository, "mv", "src/sample.py", "src/renamed.py")
    _write(
        repository / "src/renamed.py",
        "def calculate(value: int) -> int:\n    return value + 2\n\n"
        "def keep_a(value: int) -> int:\n    return value * 2\n\n"
        "def keep_b(value: int) -> int:\n    return value // 2\n",
    )
    head_sha = _commit(repository, "rename modify and delete")
    scope = ["src/renamed.py", "src/sample.py"]
    targets = [
        "src/renamed.py::function:calculate:1-2",
        "src/renamed.py::function:keep_a:4-5",
        "src/renamed.py::function:keep_b:7-8",
        "src/sample.py::function:normalize:4-5",
    ]
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, scope, targets, broad=True),
    )

    assert _derived_targets(repository, base_sha, head_sha) == (tuple(targets), ())
    result = _verify(repository, event, _characterization(base_sha, head_sha, scope))

    assert result["overall_result"] == "PASS"
    assert result["targets"] == targets


def test_refactor_targets_pure_rename_targets_the_moved_responsibility(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _git(repository, "mv", "src/sample.py", "src/renamed.py")
    head_sha = _commit(repository, "rename production responsibility")
    scope = ["src/renamed.py", "src/sample.py"]
    target = "src/renamed.py::function:calculate:1-2"
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, scope, [target], broad=True),
    )

    assert _derived_targets(repository, base_sha, head_sha) == ((target,), ())
    result = _verify(repository, event, _characterization(base_sha, head_sha, ["src/renamed.py"]))

    assert result["overall_result"] == "PASS"
    assert result["targets"] == [target]
    assert result["unbounded_paths"] == []


def test_refactor_targets_rename_timeout_keeps_head_target_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _git(repository, "mv", "src/sample.py", "src/renamed.py")
    head_sha = _commit(repository, "rename production responsibility")

    def timeout(*args: object, **kwargs: object) -> tuple[int, ...]:
        raise git_changes.GitError("GIT_TIMEOUT", "bounded rename diff timed out")

    monkeypatch.setattr(git_changes, "changed_base_lines", timeout)

    assert _derived_targets(repository, base_sha, head_sha) == (
        ("src/renamed.py::function:calculate:1-2",),
        ("src/sample.py",),
    )


def test_empty_renamed_production_file_keeps_both_paths_fail_closed(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _git(repository, "mv", "src/sample.py", "src/empty.py")
    _write(repository / "src/empty.py", "")
    head_sha = _commit(repository, "empty renamed production file")
    old_target = "src/sample.py::function:calculate:1-2"
    scope = ["src/empty.py", "src/sample.py"]
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, scope, [old_target], broad=True),
    )
    records: list[git_changes.CommandRecord] = []
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
    policy = contract.parse_contract(
        git_changes.read_regular_blob(
            repository, identity.base_sha, ".supportability.toml", records
        ).content
    )

    assert refactor_targets.derive(
        repository,
        identity,
        policy,
        (git_changes.ChangedPath("RENAMED", "src/sample.py", "src/empty.py"),),
        records,
    ) == (
        (old_target,),
        ("src/empty.py",),
    )
    result = _verify(repository, event, _characterization(base_sha, head_sha, ["src/sample.py"]))

    assert result["overall_result"] == "BLOCK"
    assert result["policy_blocks"] == ["UNVERIFIABLE_BOUNDED_TARGET"]


def test_refactor_targets_move_in_targets_every_entering_responsibility(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _git(repository, "mv", "src/sample.py", "sample.py")
    _write(
        repository / "sample.py",
        "def calculate(value: int) -> int:\n    return value + 1\n\n"
        "def normalize(value: int) -> int:\n    return value - 1\n",
    )
    base_sha = _commit(repository, "nonproduction responsibilities")
    _git(repository, "mv", "sample.py", "src/sample.py")
    _write(
        repository / "src/sample.py",
        "def calculate(value: int) -> int:\n    return value + 2\n",
    )
    head_sha = _commit(repository, "move responsibilities into production")
    scope = ["sample.py", "src/sample.py"]
    targets = ["src/sample.py::function:calculate:1-2"]
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, scope, targets, broad=True),
    )

    assert _derived_targets(repository, base_sha, head_sha) == (tuple(targets), ())
    result = _verify(repository, event, _characterization(base_sha, head_sha, ["src/sample.py"]))

    assert result["overall_result"] == "PASS"
    assert result["targets"] == targets


def test_refactor_targets_typescript_to_tsx_rename_uses_each_side_parser(
    tmp_path: Path,
) -> None:
    repository, base_sha, _ = _repository(tmp_path, "typescript")
    _git(repository, "reset", "--hard", base_sha)
    _write(
        repository / "src/sample.ts",
        "export function Card(value: number): number {\n"
        "  const one = value + 1;\n"
        "  const two = one + 1;\n"
        "  const three = two + 1;\n"
        "  const four = three + 1;\n"
        "  const five = four + 1;\n"
        "  return five;\n"
        "}\n",
    )
    base_sha = _commit(repository, "typescript component")
    _git(repository, "mv", "src/sample.ts", "src/sample.tsx")
    _write(
        repository / "src/sample.tsx",
        "export function Card(value: number) {\n"
        "  const one = value + 1;\n"
        "  const two = one + 1;\n"
        "  const three = two + 1;\n"
        "  const four = three + 1;\n"
        "  const five = four + 1;\n"
        "  return <section>{five}</section>;\n"
        "}\n",
    )
    head_sha = _commit(repository, "move component to tsx")
    scope = ["src/sample.ts", "src/sample.tsx"]
    target = "src/sample.tsx::component:Card:1-8"
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, scope, [target], broad=True),
    )

    assert _derived_targets(repository, base_sha, head_sha) == ((target,), ())
    result = _verify(repository, event, _characterization(base_sha, head_sha, ["src/sample.tsx"]))

    assert result["overall_result"] == "PASS"
    assert result["targets"] == [target]


@pytest.mark.parametrize(
    (
        "language",
        "path",
        "base_source",
        "head_source",
        "kind",
        "old_name",
        "new_name",
        "lines",
    ),
    [
        (
            "typescript",
            "src/sample.ts",
            "export const Old =\n  () => 1;\n",
            "export const New =\n  () => 1;\n",
            "component",
            "Old",
            "New",
            "1-2",
        ),
        (
            "tsx",
            "src/sample.tsx",
            "export const OldCard: React.FC =\n  () => <div />;\n",
            "export const NewCard: React.FC =\n  () => <div />;\n",
            "component",
            "OldCard",
            "NewCard",
            "1-2",
        ),
        (
            "typescript",
            "src/sample.ts",
            "const handlers = {\n  save:\n    () => 1,\n};\n",
            "const handlers = {\n  store:\n    () => 1,\n};\n",
            "function",
            "save",
            "store",
            "2-3",
        ),
        (
            "typescript",
            "src/sample.ts",
            "class Service {\n  handle =\n    () => 1;\n}\n",
            "class Service {\n  process =\n    () => 1;\n}\n",
            "function",
            "Service.handle",
            "Service.process",
            "2-3",
        ),
        (
            "tsx",
            "src/sample.tsx",
            "export const Old =\n"
            "  class extends React.Component {\n"
            "    render() { return null; }\n"
            "  };\n",
            "export const New =\n"
            "  class extends React.Component {\n"
            "    render() { return null; }\n"
            "  };\n",
            "component",
            "Old",
            "New",
            "1-4",
        ),
    ],
)
def test_multiline_typescript_declarator_rename_binds_old_and_new_identities(
    tmp_path: Path,
    language: str,
    path: str,
    base_source: str,
    head_source: str,
    kind: str,
    old_name: str,
    new_name: str,
    lines: str,
) -> None:
    repository, base_sha, _ = _repository(tmp_path, language)
    _git(repository, "reset", "--hard", base_sha)
    _write(repository / path, base_source)
    base_sha = _commit(repository, "multiline declarator")
    _write(repository / path, head_source)
    head_sha = _commit(repository, "rename multiline declarator")
    targets = sorted(
        [
            f"{path}::{kind}:{new_name}:{lines}",
            f"{path}::{kind}:{old_name}:{lines}",
        ]
    )
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, [path], targets, broad=True),
    )

    assert _derived_targets(repository, base_sha, head_sha) == (tuple(targets), ())
    narrow = _verify(
        repository,
        _event(base_sha, head_sha, _authorization(base_sha, head_sha, [path], targets)),
        _characterization(base_sha, head_sha, [path]),
    )
    result = _verify(repository, event, _characterization(base_sha, head_sha, [path]))

    assert narrow["policy_blocks"] == ["BROAD_AUTHORIZATION_REQUIRED"]
    assert result["overall_result"] == "PASS"
    assert result["targets"] == targets


@pytest.mark.parametrize(
    ("old_path", "expected_unbounded", "expected_blocks"),
    [
        ("sample.bin", [], []),
        ("src/sample.bin", ["src/sample.bin"], ["UNVERIFIABLE_BOUNDED_TARGET"]),
    ],
)
def test_refactor_targets_unprofiled_to_python_rename_distinguishes_old_production(
    tmp_path: Path,
    old_path: str,
    expected_unbounded: list[str],
    expected_blocks: list[str],
) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    (repository / "src/sample.py").unlink()
    _write(
        repository / old_path,
        "def calculate(value: int) -> int:\n    return value + 1\n",
    )
    base_sha = _commit(repository, "unprofiled production source")
    _git(repository, "mv", old_path, "src/sample.py")
    _write(
        repository / "src/sample.py",
        "def calculate(value: int) -> int:\n    return value + 2\n",
    )
    head_sha = _commit(repository, "move source into Python profile")
    scope = sorted([old_path, "src/sample.py"])
    target = "src/sample.py::function:calculate:1-2"
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, scope, [target], broad=True),
    )

    assert _derived_targets(repository, base_sha, head_sha) == (
        (target,),
        tuple(expected_unbounded),
    )
    result = _verify(repository, event, _characterization(base_sha, head_sha, ["src/sample.py"]))

    assert result["overall_result"] == ("BLOCK" if expected_blocks else "PASS")
    assert result["targets"] == [target]
    assert result["unbounded_paths"] == expected_unbounded
    assert result["policy_blocks"] == expected_blocks


@pytest.mark.parametrize(
    ("new_path", "head_source"),
    [("src/sample.txt", "not Python\n"), ("src/renamed.py", "def broken(:\n")],
)
def test_renamed_profiled_source_retains_old_target_when_head_is_unbounded(
    tmp_path: Path, new_path: str, head_source: str
) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _git(repository, "mv", "src/sample.py", new_path)
    _write(repository / new_path, head_source)
    head_sha = _commit(repository, "unbounded renamed head")
    records: list[git_changes.CommandRecord] = []
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
    policy = contract.parse_contract(
        git_changes.read_regular_blob(
            repository, identity.base_sha, ".supportability.toml", records
        ).content
    )

    assert refactor_targets.derive(
        repository,
        identity,
        policy,
        (git_changes.ChangedPath("RENAMED", "src/sample.py", new_path),),
        records,
    ) == (
        ("src/sample.py::function:calculate:1-2",),
        (new_path,),
    )


def test_renamed_profiled_source_retains_head_target_when_base_is_unbounded(
    tmp_path: Path,
) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(repository / "src/sample.py", "def broken(:\n")
    base_sha = _commit(repository, "unbounded rename base")
    _git(repository, "mv", "src/sample.py", "src/renamed.py")
    _write(
        repository / "src/renamed.py",
        "def calculate(value: int) -> int:\n    return value + 1\n",
    )
    head_sha = _commit(repository, "bounded renamed head")
    records: list[git_changes.CommandRecord] = []
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
    policy = contract.parse_contract(
        git_changes.read_regular_blob(
            repository, identity.base_sha, ".supportability.toml", records
        ).content
    )

    assert refactor_targets.derive(
        repository,
        identity,
        policy,
        (git_changes.ChangedPath("RENAMED", "src/sample.py", "src/renamed.py"),),
        records,
    ) == (
        ("src/renamed.py::function:calculate:1-2",),
        ("src/sample.py",),
    )


def test_retained_file_deletion_uses_deleted_base_responsibility(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    path = "src/sample.py"
    _write(
        repository / path,
        "def removed() -> int:\n    return 1\n\ndef retained() -> int:\n    return 2\n",
    )
    base_sha = _commit(repository, "two functions")
    _write(repository / path, "def retained() -> int:\n    return 2\n")
    head_sha = _commit(repository, "delete first function")
    target = f"{path}::function:removed:1-2"
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, [path], [target]))

    result = _verify(repository, event, _characterization(base_sha, head_sha, [path]))

    assert result["overall_result"] == "PASS"
    assert result["targets"] == [target]


def test_refactor_targets_addition_is_exact(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    path = "src/added.py"
    _write(repository / path, "def normalize(value: int) -> int:\n    return value + 1\n")
    head_sha = _commit(repository, "add")
    target = f"{path}::function:normalize:1-2"
    event = _event(
        base_sha,
        head_sha,
        _authorization(base_sha, head_sha, [path], [target]),
    )

    assert _derived_targets(repository, base_sha, head_sha) == ((target,), ())
    result = _verify(repository, event, _characterization(base_sha, head_sha, [path]))

    assert result["overall_result"] == "PASS"
    assert result["targets"] == [target]


@pytest.mark.parametrize(
    ("defect", "code"),
    [
        ("missing", "MISSING_OWNER_AUTHORIZATION"),
        ("head", "STALE_OWNER_AUTHORIZATION"),
        ("scope", "UNFOCUSED_DIFF_SCOPE"),
        ("target", "UNVERIFIABLE_BOUNDED_TARGET"),
        ("sequence", "INVALID_STRANGLER_SEQUENCE"),
    ],
)
def test_python_deletion_authorization_remains_exact(
    tmp_path: Path, defect: str, code: str
) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    (repository / "src/sample.py").unlink()
    head_sha = _commit(repository, "delete")
    path = "src/sample.py"
    target = f"{path}::function:calculate:1-2"
    body = None
    if defect != "missing":
        body = _authorization(
            base_sha,
            "f" * 40 if defect == "head" else head_sha,
            ["docs/outside.md"] if defect == "scope" else [path],
            [f"{path}::function:wrong:1-2"] if defect == "target" else [target],
            predecessor_sha="e" * 40 if defect == "sequence" else base_sha,
        )

    result = _verify(
        repository,
        _event(base_sha, head_sha, body),
        _characterization(base_sha, head_sha, [path]),
    )

    assert result["policy_blocks"] == [code]


@pytest.mark.parametrize(
    ("path", "content"),
    [("src/data.bin", "data\n"), ("src/broken.py", "def broken(:\n")],
)
def test_unbounded_deleted_production_files_fail_closed(
    tmp_path: Path, path: str, content: str
) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(repository / path, content)
    base_sha = _commit(repository, "unbounded base")
    (repository / path).unlink()
    head_sha = _commit(repository, "delete unbounded")
    event = _event(
        base_sha,
        head_sha,
        _authorization(
            base_sha,
            head_sha,
            [path],
            [f"{path}::module:{path}:1-1"],
            broad=True,
        ),
    )

    result = _verify(repository, event, _characterization(base_sha, head_sha, []))

    assert result["overall_result"] == "BLOCK"
    assert result["unbounded_paths"] == [path]
    assert result["policy_blocks"] == [
        "MISSING_BOUNDED_PRODUCTION_TARGET",
        "UNVERIFIABLE_BOUNDED_TARGET",
    ]


def test_non_production_change_is_not_a_refactor(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(repository / "docs/evidence.md", "record\n")
    head_sha = _commit(repository, "evidence")
    predecessor = refactor_policy._parse_authorization(
        _authorization(
            "c" * 40,
            "d" * 40,
            ["src/sample.py"],
            ["src/sample.py::function:calculate:1-2"],
        )
    )

    result = _verify(
        repository,
        _event(base_sha, head_sha, None),
        _characterization(base_sha, head_sha, []),
        predecessor=predecessor,
        add_runnability=False,
    )

    assert result["applicable"] is False
    assert result["overall_result"] == "PASS"
    assert result["policy_blocks"] == []
    assert result["predecessor"] == {
        "authorization": None,
        "authorization_comment_id": None,
        "base_sha": None,
        "block": None,
        "head_sha": None,
        "merge_sha": None,
        "pull_number": None,
    }


def test_github_comment_evidence_uses_fixed_authenticated_endpoint() -> None:
    requests: list[Any] = []

    def opener(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        assert kwargs == {"timeout": 30}
        return _Reply([{"body": "authorization", "id": 11, "user": {"id": 7}}])

    comments = refactor_policy._github_comments("example/fixture", 7, "token", opener)

    assert comments[0]["id"] == 11
    assert requests[0].full_url.endswith(
        "/repos/example/fixture/issues/7/comments?per_page=100&page=1"
    )
    assert requests[0].get_header("Authorization") == "Bearer token"


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"body": None, "id": 11, "user": {"id": refactor_policy.TRUSTED_OWNER_ID}},
        {"body": "note", "id": 0, "user": None},
        {"body": "note", "id": 11, "user": {}},
    ],
)
def test_malformed_github_comment_row_is_owned_evidence_failure(row: dict[str, object]) -> None:
    with pytest.raises(
        refactor_policy.RefactorPolicyError,
        match="GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
    ):
        refactor_policy._github_comments(
            "example/fixture", 7, "token", lambda *args, **kwargs: _Reply([row])
        )


@pytest.mark.parametrize("failure", ["api", "missing_token"])
def test_current_authorization_evidence_failure_is_a_gate_six_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    event_path = tmp_path / "event.json"
    characterization_path = tmp_path / "characterization.json"
    output = tmp_path / "refactor-result.json"
    _write(event_path, json.dumps(_event(base_sha, head_sha, None)))
    characterization = _characterization(base_sha, head_sha, ["src/sample.py"])
    characterization["refactor_runnability"] = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "repository": "github.com/example/fixture",
        "runnable": True,
        "schema_version": refactor_policy.RUNNABILITY_SCHEMA,
        "targets": ["src/sample.py::function:calculate:1-2"],
        "unbounded_paths": [],
        "workflow_sha": "f" * 40,
    }
    _write(characterization_path, json.dumps(characterization))
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    if failure == "api":
        monkeypatch.setenv("GITHUB_TOKEN", "token")
    else:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def fail_comments(*args: object) -> tuple[dict[str, Any], ...]:
        raise refactor_policy.RefactorPolicyError("GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")

    if failure == "api":
        monkeypatch.setattr(refactor_policy, "_github_comments", fail_comments)

    exit_code = refactor_policy.main(
        [
            "--repository",
            str(repository),
            "--event",
            str(event_path),
            "--characterization-result",
            str(characterization_path),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 1
    assert json.loads(output.read_text(encoding="utf-8"))["policy_blocks"] == [
        "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"
    ]


def test_invalid_utf8_comment_response_is_owned_github_evidence_failure() -> None:
    with pytest.raises(
        refactor_policy.RefactorPolicyError,
        match="GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
    ):
        refactor_policy._github_comments(
            "example/fixture", 7, "token", lambda *args, **kwargs: _Reply(b"\xff")
        )


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", hdrs=None, fp=None),
        http.client.IncompleteRead(b"partial", 10),
        http.client.BadStatusLine("invalid status"),
    ],
)
def test_github_transport_failure_is_owned(error: Exception) -> None:
    def fail(*args: object, **kwargs: object) -> _Reply:
        raise error

    with pytest.raises(
        refactor_policy.RefactorPolicyError,
        match="GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
    ):
        refactor_policy._github_comments("example/fixture", 7, "token", fail)

    predecessor = refactor_policy._predecessor_authorization(
        "example/fixture", "a" * 40, "token", fail
    )
    assert predecessor.authorization is None
    assert predecessor.block == "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"


def test_github_comment_pagination_reaches_next_page() -> None:
    head_sha = "2" * 40
    body = _authorization(
        "1" * 40,
        head_sha,
        ["src/sample.py"],
        ["src/sample.py::function:calculate:1-2"],
    )
    replies = iter(
        [
            _Reply(
                [{"body": "note", "id": index + 100, "user": None} for index in range(100)],
                '<https://api.github.com/repositories/1/issues/7/comments?per_page=100&page=2>; rel="next"',
            ),
            _Reply([{"body": body, "id": 11, "user": {"id": refactor_policy.TRUSTED_OWNER_ID}}]),
        ]
    )
    requests: list[Any] = []

    def opener(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        return next(replies)

    comments = refactor_policy._github_comments("example/fixture", 7, "token", opener)
    authorization, comment_id = refactor_policy._owner_authorization(
        _event("1" * 40, head_sha, None), comments
    )

    assert authorization.head_sha == head_sha
    assert comment_id == 11
    assert [request.full_url.rsplit("page=", 1)[-1] for request in requests] == ["1", "2"]


def test_github_comment_pagination_has_a_finite_evidence_bound() -> None:
    requests: list[Any] = []

    def opener(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        return _Reply([], '<https://api.github.com/repeated>; rel="next"')

    with pytest.raises(
        refactor_policy.RefactorPolicyError,
        match="GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
    ):
        refactor_policy._github_comments("example/fixture", 7, "token", opener)

    assert len(requests) == refactor_policy.MAX_GITHUB_PAGES


def test_predecessor_pagination_reaches_next_page() -> None:
    merge_sha, prior_base, prior_head = "a" * 40, "b" * 40, "c" * 40
    body = _authorization(
        prior_base,
        prior_head,
        ["src/sample.py"],
        ["src/sample.py::function:calculate:1-2"],
        step=2,
    )
    replies = iter(
        [
            _Reply(
                [
                    {
                        "base": {"sha": "d" * 40},
                        "head": {"sha": "e" * 40},
                        "merge_commit_sha": None,
                        "merged_at": None,
                        "number": index + 100,
                    }
                    for index in range(100)
                ],
                '<https://api.github.com/repositories/1/commits/a/pulls?per_page=100&page=2>; rel="next"',
            ),
            _Reply(
                [
                    {
                        "base": {"sha": prior_base},
                        "head": {"sha": prior_head},
                        "merge_commit_sha": merge_sha,
                        "merged_at": "2026-08-04T00:00:00Z",
                        "number": 6,
                    }
                ]
            ),
            _Reply([{"body": body, "id": 11, "user": {"id": refactor_policy.TRUSTED_OWNER_ID}}]),
        ]
    )
    requests: list[Any] = []

    def opener(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        return next(replies)

    predecessor = refactor_policy._predecessor_authorization(
        "example/fixture", merge_sha, "token", opener
    )

    assert predecessor.block is None
    assert predecessor.authorization is not None
    assert predecessor.authorization.sequence.step == 2
    assert predecessor.authorization_comment_id == 11
    assert predecessor.merge_sha == merge_sha
    assert predecessor.pull_number == 6
    assert [request.full_url.rsplit("page=", 1)[-1] for request in requests] == ["1", "2", "1"]


@pytest.mark.parametrize(
    "row",
    [
        None,
        {},
        {
            "base": {"sha": "b" * 40},
            "head": {"sha": "c" * 40},
            "merge_commit_sha": None,
            "merged_at": "2026-08-04T00:00:00Z",
            "number": 6,
        },
    ],
)
def test_malformed_predecessor_row_is_owned_github_evidence_failure(row: object) -> None:
    predecessor = refactor_policy._predecessor_authorization(
        "example/fixture", "a" * 40, "token", lambda *args, **kwargs: _Reply([row])
    )

    assert predecessor.authorization is None
    assert predecessor.block == "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"


@pytest.mark.parametrize(
    "row",
    [
        {"merge_commit_sha": "a" * 40},
        {
            "base": {"sha": "b" * 40},
            "head": {"sha": "c" * 40},
            "merge_commit_sha": "a" * 40,
            "merged_at": "",
            "number": 6,
        },
        {
            "base": {"sha": "bad"},
            "head": {"sha": "c" * 40},
            "merge_commit_sha": "a" * 40,
            "merged_at": "2026-08-04T00:00:00Z",
            "number": 6,
        },
    ],
)
def test_matching_malformed_predecessor_row_is_owned_github_evidence_failure(
    row: dict[str, object],
) -> None:
    predecessor = refactor_policy._predecessor_authorization(
        "example/fixture", "a" * 40, "token", lambda *args, **kwargs: _Reply([row])
    )

    assert predecessor.authorization is None
    assert predecessor.block == "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"


def test_predecessor_uses_exact_merged_pr_and_trusted_authorization() -> None:
    merge_sha, prior_base, prior_head = "a" * 40, "b" * 40, "c" * 40
    body = _authorization(
        prior_base,
        prior_head,
        ["src/sample.py"],
        ["src/sample.py::function:calculate:1-2"],
        step=2,
    )
    responses = iter(
        [
            [
                {
                    "base": {"sha": prior_base},
                    "head": {"sha": prior_head},
                    "merge_commit_sha": merge_sha,
                    "merged_at": "2026-08-04T00:00:00Z",
                    "number": 6,
                }
            ],
            [{"body": body, "id": 11, "user": {"id": refactor_policy.TRUSTED_OWNER_ID}}],
        ]
    )
    requests: list[Any] = []

    def opener(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        assert kwargs == {"timeout": 30}
        return _Reply(next(responses))

    predecessor = refactor_policy._predecessor_authorization(
        "example/fixture", merge_sha, "token", opener
    )

    assert predecessor.block is None
    assert predecessor.authorization is not None
    assert predecessor.authorization.sequence.step == 2
    assert predecessor.authorization_comment_id == 11
    assert predecessor.base_sha == prior_base
    assert predecessor.head_sha == prior_head
    assert predecessor.merge_sha == merge_sha
    assert predecessor.pull_number == 6
    assert requests[0].full_url.endswith(f"/commits/{merge_sha}/pulls?per_page=100&page=1")
    assert requests[1].full_url.endswith("/issues/6/comments?per_page=100&page=1")


@pytest.mark.parametrize(
    ("defect", "expected"),
    [
        ("malformed", "INVALID_STRANGLER_SEQUENCE"),
        ("non_string", "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"),
        ("stale", "INVALID_STRANGLER_SEQUENCE"),
        ("untrusted", "INVALID_STRANGLER_SEQUENCE"),
    ],
)
def test_invalid_predecessor_comment_is_an_exact_gate_six_block(defect: str, expected: str) -> None:
    merge_sha, prior_base, prior_head = "a" * 40, "b" * 40, "c" * 40
    body = _authorization(
        prior_base,
        "d" * 40 if defect == "stale" else prior_head,
        ["src/sample.py"],
        ["src/sample.py::function:calculate:1-2"],
        step=2,
    )
    if defect == "malformed":
        body = refactor_policy.AUTHORIZATION_PREFIX + "{"
    elif defect == "non_string":
        body = None
    responses = iter(
        [
            [
                {
                    "base": {"sha": prior_base},
                    "head": {"sha": prior_head},
                    "merge_commit_sha": merge_sha,
                    "merged_at": "2026-08-04T00:00:00Z",
                    "number": 6,
                }
            ],
            [
                {
                    "body": body,
                    "id": 11,
                    "user": {
                        "id": 1 if defect == "untrusted" else refactor_policy.TRUSTED_OWNER_ID
                    },
                }
            ],
        ]
    )

    predecessor = refactor_policy._predecessor_authorization(
        "example/fixture",
        merge_sha,
        "token",
        lambda *args, **kwargs: _Reply(next(responses)),
    )

    assert predecessor.authorization is None
    assert predecessor.block == expected


def test_sequence_step_requires_immediate_authenticated_predecessor() -> None:
    previous = refactor_policy._parse_authorization(
        _authorization(
            "d" * 40,
            "e" * 40,
            ["src/sample.py"],
            ["src/sample.py::function:calculate:1-2"],
            step=2,
        )
    )
    step_three = refactor_policy._parse_authorization(
        _authorization(
            "a" * 40,
            "b" * 40,
            ["src/sample.py"],
            ["src/sample.py::function:calculate:1-2"],
            step=3,
        )
    )
    reset = refactor_policy._parse_authorization(
        _authorization(
            "a" * 40,
            "b" * 40,
            ["src/sample.py"],
            ["src/sample.py::function:calculate:1-2"],
        )
    )

    assert refactor_policy._sequence_blocks(step_three, previous, None) == []
    assert refactor_policy._sequence_blocks(reset, previous, None) == ["INVALID_STRANGLER_SEQUENCE"]
    assert refactor_policy._sequence_blocks(step_three, None, None) == [
        "INVALID_STRANGLER_SEQUENCE"
    ]
    assert refactor_policy._sequence_blocks(
        step_three, previous, "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"
    ) == ["GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"]


@pytest.mark.parametrize(
    ("defect", "code"),
    [
        ("identity", "UNAUTHENTICATED_OWNER_AUTHORIZATION"),
        ("repository", "AUTHORIZATION_REPOSITORY_MISMATCH"),
        ("head", "STALE_OWNER_AUTHORIZATION"),
        ("scope", "UNFOCUSED_DIFF_SCOPE"),
        ("target", "UNVERIFIABLE_BOUNDED_TARGET"),
        ("sequence", "INVALID_STRANGLER_SEQUENCE"),
    ],
)
def test_authorization_focus_and_sequence_are_exact(tmp_path: Path, defect: str, code: str) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    path = "src/sample.py"
    targets = [f"{path}::function:calculate:1-2"]
    scope = [path]
    authorized_head = "f" * 40 if defect == "head" else head_sha
    authorized_scope = ["docs/outside.md"] if defect == "scope" else scope
    authorized_targets = [f"{path}::function:wrong:1-2"] if defect == "target" else targets
    predecessor = "e" * 40 if defect == "sequence" else base_sha
    event = _event(
        base_sha,
        head_sha,
        _authorization(
            base_sha,
            authorized_head,
            authorized_scope,
            authorized_targets,
            predecessor_sha=predecessor,
            repository="example/other" if defect == "repository" else "example/fixture",
        ),
    )
    result = _verify(
        repository,
        event,
        _characterization(base_sha, head_sha, scope),
        owner_id=1 if defect == "identity" else refactor_policy.TRUSTED_OWNER_ID,
    )

    assert result["policy_blocks"] == [code]


def test_main_checks_predecessor_before_allowing_step_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_sha, head_sha = "b" * 40, "a" * 40
    body = _authorization(
        base_sha,
        head_sha,
        ["src/sample.py"],
        ["src/sample.py::function:calculate:1-2"],
    )
    event = _event(base_sha, head_sha, body)
    result = {
        "overall_result": "BLOCK",
        "policy_blocks": ["INVALID_STRANGLER_SEQUENCE"],
    }
    predecessor = refactor_policy.PredecessorEvidence(block="INVALID_STRANGLER_SEQUENCE")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(
        refactor_policy,
        "_read_json",
        lambda _, code: ((event if code == "MALFORMED_GITHUB_EVENT" else {}), b"{}"),
    )
    monkeypatch.setattr(
        refactor_policy,
        "_github_comments",
        lambda *args: ({"body": body, "id": 11, "user": {"id": refactor_policy.TRUSTED_OWNER_ID}},),
    )
    monkeypatch.setattr(refactor_policy, "_predecessor_authorization", lambda *args: predecessor)

    def verify(*args: object) -> dict[str, object]:
        assert args[4] is predecessor
        return result

    monkeypatch.setattr(refactor_policy, "verify_refactor", verify)

    exit_code = refactor_policy.main(
        [
            "--repository",
            str(tmp_path),
            "--event",
            str(tmp_path / "event.json"),
            "--characterization-result",
            str(tmp_path / "characterization.json"),
            "--output",
            str(tmp_path / "refactor-result.json"),
        ]
    )

    assert exit_code == 1


def test_refactor_result_is_independent_of_connector_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "refactor-result.json"
    result = {"overall_result": "BLOCK", "policy_blocks": ["EXAMPLE_BLOCK"]}
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(refactor_policy, "_read_json", lambda *args: ({}, b"{}"))
    monkeypatch.setattr(
        refactor_policy,
        "_event_values",
        lambda event: ("example/repository", "b" * 40, "a" * 40, 7),
    )
    monkeypatch.setattr(refactor_policy, "_github_comments", lambda *args: ())
    monkeypatch.setattr(
        refactor_policy,
        "_predecessor_authorization",
        lambda *args: pytest.fail("predecessor lookup must wait for current authorization"),
    )
    monkeypatch.setattr(refactor_policy, "verify_refactor", lambda *args: result)

    exit_code = refactor_policy.main(
        [
            "--repository",
            str(tmp_path),
            "--event",
            str(tmp_path / "event.json"),
            "--characterization-result",
            str(tmp_path / "characterization.json"),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 1
    assert json.loads(output.read_text(encoding="utf-8")) == result
