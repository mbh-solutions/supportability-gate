from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from supportability_gate import refactor_policy

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
    def __init__(self, value: object) -> None:
        self.content = json.dumps(value).encode()

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
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
    if language == "typescript":
        contract = contract.replace('language = "python"', 'language = "typescript"').replace(
            'adapter = "python.c901-touched.v1"',
            'adapter = "typescript.c901-equivalent-touched.v1"',
        )
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
    step: int = 1,
) -> str:
    value = {
        "base_sha": base_sha,
        "broad": broad,
        "head_sha": head_sha,
        "repository": "example/fixture",
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
        "scenarios": [{"compatibility": "PASS" if runnable else "BLOCK"}],
        "schema_version": refactor_policy.CHARACTERIZATION_SCHEMA,
    }


def _verify(
    repository: Path,
    event: dict[str, object],
    characterization: dict[str, object],
    *,
    owner_id: int = refactor_policy.TRUSTED_OWNER_ID,
    predecessor: refactor_policy.Authorization | None = None,
    predecessor_block: str | None = None,
) -> dict[str, object]:
    body = event["pull_request"]["body"]  # type: ignore[index]
    comments = () if body is None else ({"body": body, "id": 11, "user": {"id": owner_id}},)
    return refactor_policy.verify_refactor(
        repository, event, characterization, comments, predecessor, predecessor_block
    )


@pytest.mark.parametrize(
    ("language", "path"), [("python", "src/sample.py"), ("typescript", "src/sample.ts")]
)
def test_bounded_runnable_python_and_typescript_refactors_pass(
    tmp_path: Path, language: str, path: str
) -> None:
    repository, base_sha, head_sha = _repository(tmp_path, language)
    end_line = 2 if language == "python" else 3
    target = f"{path}::function:calculate:1-{end_line}"
    event = _event(base_sha, head_sha, _authorization(base_sha, head_sha, [path], [target]))
    characterization = _characterization(base_sha, head_sha, [path])

    first = _verify(repository, event, characterization)
    second = _verify(repository, event, characterization)

    assert first == second
    assert first["overall_result"] == "PASS"
    assert first["targets"] == [target]
    assert first["other_standard_clauses_waived"] is False


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
    assert "BROAD_AUTHORIZATION_REQUIRED" in result["policy_blocks"]


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

    assert "BROAD_AUTHORIZATION_REQUIRED" in result["policy_blocks"]


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

    result = _verify(
        repository,
        event,
        _characterization(base_sha, head_sha, [path], runnable=False),
    )

    assert "NON_RUNNABLE_LOGICAL_STEP" in result["policy_blocks"]


def test_missing_owner_authorization_blocks(tmp_path: Path) -> None:
    repository, base_sha, head_sha = _repository(tmp_path)
    result = _verify(
        repository,
        _event(base_sha, head_sha, None),
        _characterization(base_sha, head_sha, ["src/sample.py"]),
    )
    assert result["policy_blocks"] == ["MISSING_OWNER_AUTHORIZATION"]


def test_exact_authorized_python_deletion_uses_base_responsibilities(tmp_path: Path) -> None:
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

    first = _verify(repository, event, _characterization(base_sha, head_sha, []))
    second = _verify(repository, event, _characterization(base_sha, head_sha, []))

    assert first == second
    assert first["overall_result"] == "PASS"
    assert first["targets"] == [target]
    assert first["unbounded_paths"] == []


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


def test_existing_addition_enforcement_is_unchanged(tmp_path: Path) -> None:
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
        _characterization(base_sha, head_sha, []),
    )

    assert code in result["policy_blocks"]


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
        _authorization(base_sha, head_sha, [path], [f"{path}::module:{path}:1-1"]),
    )

    result = _verify(repository, event, _characterization(base_sha, head_sha, []))

    assert result["overall_result"] == "BLOCK"
    assert result["unbounded_paths"] == [path]
    assert "UNVERIFIABLE_BOUNDED_TARGET" in result["policy_blocks"]


def test_non_production_change_is_not_a_refactor(tmp_path: Path) -> None:
    repository, base_sha, _ = _repository(tmp_path)
    _git(repository, "reset", "--hard", base_sha)
    _write(repository / "docs/evidence.md", "record\n")
    head_sha = _commit(repository, "evidence")

    result = _verify(
        repository,
        _event(base_sha, head_sha, None),
        _characterization(base_sha, head_sha, []),
    )

    assert result["applicable"] is False
    assert result["overall_result"] == "PASS"


def test_github_comment_evidence_uses_fixed_authenticated_endpoint() -> None:
    requests: list[Any] = []

    def opener(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        assert kwargs == {"timeout": 30}
        return _Reply([{"body": "authorization", "id": 11, "user": {"id": 7}}])

    comments = refactor_policy._github_comments("example/fixture", 7, "token", opener)

    assert comments[0]["id"] == 11
    assert requests[0].full_url.endswith("/repos/example/fixture/issues/7/comments?per_page=100")
    assert requests[0].get_header("Authorization") == "Bearer token"


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

    predecessor, block = refactor_policy._predecessor_authorization(
        "example/fixture", merge_sha, "token", opener
    )

    assert block is None
    assert predecessor is not None and predecessor.sequence.step == 2
    assert requests[0].full_url.endswith(f"/commits/{merge_sha}/pulls?per_page=100")
    assert requests[1].full_url.endswith("/issues/6/comments?per_page=100")


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
        ),
    )
    result = _verify(
        repository,
        event,
        _characterization(base_sha, head_sha, scope),
        owner_id=1 if defect == "identity" else refactor_policy.TRUSTED_OWNER_ID,
    )

    assert code in result["policy_blocks"]
