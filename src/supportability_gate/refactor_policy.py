"""Verify authenticated, focused, runnable strangler-refactor increments."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supportability_gate import contract, function_changes, git_changes

AUTHORIZATION_PREFIX = "Supportability-Refactor-Authorization: "
AUTHORIZATION_SCHEMA = "1.0"
RESULT_SCHEMA = "refactor-policy-result.v1"
CHARACTERIZATION_SCHEMA = "characterization-result.v1"
TRUSTED_OWNER_ID = 229662739
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_JSON_BYTES = 1_000_000


class RefactorPolicyError(ValueError):
    """One fail-closed refactor-policy evidence error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Sequence:
    """One logical step tied to its immutable predecessor."""

    step: int
    predecessor_sha: str


@dataclass(frozen=True)
class Authorization:
    """Exact owner-authored pull-request authorization."""

    repository: str
    base_sha: str
    head_sha: str
    broad: bool
    scope: tuple[str, ...]
    targets: tuple[str, ...]
    sequence: Sequence


def _read_json(path: Path, code: str) -> tuple[dict[str, Any], bytes]:
    try:
        content = path.read_bytes()
        value = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RefactorPolicyError(code) from error
    if not content or len(content) > MAX_JSON_BYTES or not isinstance(value, dict):
        raise RefactorPolicyError(code)
    return value, content


def _exact_keys(value: object, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise RefactorPolicyError(code)
    return value


def _path_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    try:
        paths = tuple(contract.normalize_repository_path(item, field) for item in value)
    except contract.ContractError as error:
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION") from error
    if list(paths) != sorted(set(paths)):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    return paths


def _target_list(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or "::" not in item for item in value)
        or value != sorted(set(value))
    ):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    return tuple(value)


def _parse_authorization(body: object) -> Authorization:
    if not isinstance(body, str):
        raise RefactorPolicyError("MISSING_OWNER_AUTHORIZATION")
    rows = [
        line.removeprefix(AUTHORIZATION_PREFIX)
        for line in body.splitlines()
        if line.startswith(AUTHORIZATION_PREFIX)
    ]
    if not rows:
        raise RefactorPolicyError("MISSING_OWNER_AUTHORIZATION")
    if len(rows) != 1:
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    try:
        data = json.loads(rows[0])
    except json.JSONDecodeError as error:
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION") from error
    row = _exact_keys(
        data,
        {
            "base_sha",
            "broad",
            "head_sha",
            "repository",
            "schema_version",
            "scope",
            "sequence",
            "targets",
        },
        "MALFORMED_OWNER_AUTHORIZATION",
    )
    sequence = _exact_keys(
        row["sequence"], {"predecessor_sha", "step"}, "MALFORMED_OWNER_AUTHORIZATION"
    )
    if (
        row["schema_version"] != AUTHORIZATION_SCHEMA
        or not isinstance(row["repository"], str)
        or SHA.fullmatch(str(row["base_sha"])) is None
        or SHA.fullmatch(str(row["head_sha"])) is None
        or type(row["broad"]) is not bool
        or type(sequence["step"]) is not int
        or sequence["step"] < 1
        or SHA.fullmatch(str(sequence["predecessor_sha"])) is None
    ):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    return Authorization(
        row["repository"],
        row["base_sha"],
        row["head_sha"],
        row["broad"],
        _path_list(row["scope"], "authorization.scope"),
        _target_list(row["targets"]),
        Sequence(sequence["step"], sequence["predecessor_sha"]),
    )


def _pull_request(event: dict[str, Any]) -> dict[str, Any]:
    pull = event.get("pull_request")
    repository = event.get("repository")
    if not isinstance(pull, dict) or not isinstance(repository, dict):
        raise RefactorPolicyError("UNAUTHENTICATED_OWNER_AUTHORIZATION")
    return pull


def _event_values(event: dict[str, Any]) -> tuple[str, str, str, int]:
    pull = _pull_request(event)
    try:
        repository = event["repository"]["full_name"]
        base_sha = pull["base"]["sha"]
        head_sha = pull["head"]["sha"]
        number = pull["number"]
    except (KeyError, TypeError) as error:
        raise RefactorPolicyError("UNAUTHENTICATED_OWNER_AUTHORIZATION") from error
    if (
        not all(isinstance(item, str) for item in (repository, base_sha, head_sha))
        or type(number) is not int
    ):
        raise RefactorPolicyError("UNAUTHENTICATED_OWNER_AUTHORIZATION")
    return repository, base_sha, head_sha, number


def _authorization_blocks(event: dict[str, Any], authorization: Authorization) -> list[str]:
    repository, base_sha, head_sha, _ = _event_values(event)
    blocks: list[str] = []
    if authorization.repository != repository:
        blocks.append("AUTHORIZATION_REPOSITORY_MISMATCH")
    if authorization.base_sha != base_sha or authorization.head_sha != head_sha:
        blocks.append("STALE_OWNER_AUTHORIZATION")
    if authorization.sequence.predecessor_sha != base_sha:
        blocks.append("INVALID_STRANGLER_SEQUENCE")
    return blocks


def _sequence_blocks(
    authorization: Authorization,
    predecessor: Authorization | None,
    predecessor_block: str | None,
) -> list[str]:
    if predecessor_block is not None:
        return [predecessor_block]
    if authorization.sequence.step == 1:
        return ["INVALID_STRANGLER_SEQUENCE"] if predecessor is not None else []
    if predecessor is None or predecessor.sequence.step != authorization.sequence.step - 1:
        return ["INVALID_STRANGLER_SEQUENCE"]
    return []


def _owner_authorization(
    event: dict[str, Any], comments: tuple[dict[str, Any], ...]
) -> tuple[Authorization, int]:
    _, _, head_sha, _ = _event_values(event)
    candidates: list[tuple[Authorization, int]] = []
    stale = False
    untrusted = False
    for comment in comments:
        user = comment.get("user")
        if not isinstance(user, dict) or user.get("id") != TRUSTED_OWNER_ID:
            body = comment.get("body")
            untrusted = untrusted or (
                isinstance(body, str)
                and any(line.startswith(AUTHORIZATION_PREFIX) for line in body.splitlines())
            )
            continue
        try:
            authorization = _parse_authorization(comment.get("body"))
        except RefactorPolicyError as error:
            if error.code == "MISSING_OWNER_AUTHORIZATION":
                continue
            raise
        comment_id = comment.get("id")
        if type(comment_id) is not int:
            raise RefactorPolicyError("UNAUTHENTICATED_OWNER_AUTHORIZATION")
        if authorization.head_sha == head_sha:
            candidates.append((authorization, comment_id))
        else:
            stale = True
    if not candidates:
        raise RefactorPolicyError(
            "UNAUTHENTICATED_OWNER_AUTHORIZATION"
            if untrusted
            else "STALE_OWNER_AUTHORIZATION"
            if stale
            else "MISSING_OWNER_AUTHORIZATION"
        )
    if len(candidates) != 1:
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    return candidates[0]


def _github_comments(
    repository: str,
    pull_number: int,
    token: str,
    opener: Any = urllib.request.urlopen,
) -> tuple[dict[str, Any], ...]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/issues/{pull_number}/comments?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request, timeout=30) as response:
            value = json.loads(response.read())
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RefactorPolicyError("GITHUB_AUTHORIZATION_EVIDENCE_FAILURE") from error
    if (
        not isinstance(value, list)
        or len(value) >= 100
        or any(not isinstance(item, dict) for item in value)
    ):
        raise RefactorPolicyError("GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")
    return tuple(value)


def _predecessor_authorization(
    repository: str,
    base_sha: str,
    token: str,
    opener: Any = urllib.request.urlopen,
) -> tuple[Authorization | None, str | None]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/commits/{base_sha}/pulls?per_page=100",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request, timeout=30) as response:
            value = json.loads(response.read())
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return None, "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"
    if not isinstance(value, list) or len(value) >= 100:
        return None, "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"
    merged = [
        item
        for item in value
        if isinstance(item, dict)
        and item.get("merge_commit_sha") == base_sha
        and isinstance(item.get("merged_at"), str)
    ]
    if not merged:
        return None, None
    if len(merged) != 1 or type(merged[0].get("number")) is not int:
        return None, "INVALID_STRANGLER_SEQUENCE"
    event = {"repository": {"full_name": repository}, "pull_request": merged[0]}
    try:
        comments = _github_comments(repository, merged[0]["number"], token, opener)
        authorization, _ = _owner_authorization(event, comments)
    except RefactorPolicyError as error:
        if error.code == "MISSING_OWNER_AUTHORIZATION":
            return None, None
        return None, error.code
    if _authorization_blocks(event, authorization):
        return None, "INVALID_STRANGLER_SEQUENCE"
    return authorization, None


def _profile_source(path: str, language: str) -> bool:
    suffixes = (".py", ".pyi") if language == "python" else (".cts", ".mts", ".ts", ".tsx")
    return path.endswith(suffixes)


def _changed_scope(changes: tuple[git_changes.ChangedPath, ...]) -> tuple[str, ...]:
    return tuple(
        sorted({path for item in changes for path in (item.old_path, item.new_path) if path})
    )


def _change_spans(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    change: git_changes.ChangedPath,
    path: str,
    records: list[git_changes.CommandRecord],
) -> tuple[function_changes.ResponsibilitySpan, ...]:
    if change.new_path is None:
        content = git_changes.read_regular_blob(
            repository, identity.base_sha, path, records
        ).content
        return function_changes.responsibility_spans(
            path, content, set(range(1, len(content.splitlines()) + 1))
        )
    head = git_changes.read_regular_blob(repository, identity.head_sha, path, records).content
    head_lines = set(
        git_changes.changed_head_lines(
            repository,
            identity.base_sha,
            identity.head_sha,
            path,
            records,
            include_deletion_anchor=False,
        )
    )
    if change.old_path != change.new_path:
        return function_changes.responsibility_spans(path, head, head_lines)
    base = git_changes.read_regular_blob(repository, identity.base_sha, path, records).content
    base_lines = set(
        git_changes.changed_base_lines(
            repository, identity.base_sha, identity.head_sha, path, records
        )
    )
    surviving, deleted = function_changes.changed_responsibility_spans(
        path, base, head, base_lines, head_lines
    )
    return (*surviving, *deleted)


def _target_identities(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    changes: tuple[git_changes.ChangedPath, ...],
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    targets: list[str] = []
    unbounded: list[str] = []
    for change in changes:
        path = change.new_path or change.old_path
        if path is None or not policy.is_production_path(path):
            continue
        if not _profile_source(path, policy.language):
            unbounded.append(path)
            continue
        try:
            spans = _change_spans(repository, identity, change, path, records)
        except function_changes.PythonSourceError:
            if change.new_path is not None:
                raise
            unbounded.append(path)
            continue
        if not spans:
            unbounded.append(path)
            continue
        targets.extend(
            f"{path}::{item.kind}:{item.name}:{item.start_line}-{item.end_line}" for item in spans
        )
    return tuple(sorted(set(targets))), tuple(sorted(unbounded))


def _proof_path(path: str) -> bool:
    return path in {
        ".supportability-characterization.json",
        ".supportability-review.toml",
    } or path.startswith("tests/characterization/")


def _focus_blocks(
    authorization: Authorization,
    actual_scope: tuple[str, ...],
    targets: tuple[str, ...],
    unbounded: tuple[str, ...],
    policy: contract.Contract,
) -> list[str]:
    blocks: list[str] = []
    if authorization.scope != actual_scope:
        blocks.append("UNFOCUSED_DIFF_SCOPE")
    if authorization.targets != targets or unbounded:
        blocks.append("UNVERIFIABLE_BOUNDED_TARGET")
    production_paths = {item.split("::", 1)[0] for item in targets}
    unrelated = [
        path for path in actual_scope if path not in production_paths and not _proof_path(path)
    ]
    broad_required = len(targets) != 1 or len(production_paths) != 1 or bool(unrelated)
    if broad_required and not authorization.broad:
        blocks.append("BROAD_AUTHORIZATION_REQUIRED")
    if not targets or any(not policy.is_production_path(path) for path in production_paths):
        blocks.append("MISSING_BOUNDED_PRODUCTION_TARGET")
    return blocks


def _characterization_blocks(
    value: dict[str, Any],
    repository: str,
    base_sha: str,
    head_sha: str,
    production_paths: tuple[str, ...],
) -> list[str]:
    blocks: list[str] = []
    if value.get("schema_version") != CHARACTERIZATION_SCHEMA:
        return ["UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    if (
        value.get("repository") != f"github.com/{repository}"
        or value.get("base_sha") != base_sha
        or value.get("head_sha") != head_sha
    ):
        blocks.append("STALE_RUNNABILITY_EVIDENCE")
    if value.get("overall_result") != "PASS" or value.get("policy_blocks") != []:
        blocks.append("NON_RUNNABLE_LOGICAL_STEP")
    coverage = value.get("coverage")
    if not isinstance(coverage, dict):
        return [*blocks, "UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    required, covered = coverage.get("required_paths"), coverage.get("covered_paths")
    if (
        not isinstance(required, list)
        or not isinstance(covered, list)
        or any(path not in required or path not in covered for path in production_paths)
    ):
        blocks.append("MISSING_RUNNABILITY_COVERAGE")
    scenarios = value.get("scenarios")
    if (
        not isinstance(scenarios, list)
        or not scenarios
        or any(
            not isinstance(item, dict) or item.get("compatibility") != "PASS" for item in scenarios
        )
    ):
        blocks.append("NON_RUNNABLE_LOGICAL_STEP")
    return blocks


def verify_refactor(
    repository: Path,
    event: dict[str, Any],
    characterization: dict[str, Any],
    comments: tuple[dict[str, Any], ...],
    predecessor: Authorization | None = None,
    predecessor_block: str | None = None,
) -> dict[str, object]:
    """Return deterministic M8 authorization, focus, runnability, and sequence evidence."""
    records: list[git_changes.CommandRecord] = []
    repository = git_changes.validate_repository(repository, records)
    repository_name, base_sha, head_sha, _ = _event_values(event)
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
    policy_blob = git_changes.read_regular_blob(
        repository, base_sha, ".supportability.toml", records
    )
    policy = contract.parse_contract(policy_blob.content)
    changes = git_changes.changed_paths(repository, base_sha, head_sha, records)
    actual_scope = _changed_scope(changes)
    targets, unbounded = _target_identities(repository, identity, policy, changes, records)
    applicable = bool(targets or unbounded)
    blocks: list[str] = []
    authorization: Authorization | None = None
    authorization_comment_id: int | None = None
    if applicable:
        try:
            authorization, authorization_comment_id = _owner_authorization(event, comments)
            blocks.extend(_authorization_blocks(event, authorization))
            blocks.extend(_sequence_blocks(authorization, predecessor, predecessor_block))
            blocks.extend(_focus_blocks(authorization, actual_scope, targets, unbounded, policy))
        except RefactorPolicyError as error:
            blocks.append(error.code)
    production_paths = tuple(
        sorted(
            {
                change.new_path
                for change in changes
                if change.new_path and policy.is_production_path(change.new_path)
            }
        )
    )
    blocks.extend(
        _characterization_blocks(
            characterization, repository_name, base_sha, head_sha, production_paths
        )
    )
    unique_blocks = sorted(set(blocks))
    return {
        "applicable": applicable,
        "authorization": (
            {
                "base_sha": authorization.base_sha,
                "broad": authorization.broad,
                "head_sha": authorization.head_sha,
                "repository": authorization.repository,
                "scope": list(authorization.scope),
                "sequence": {
                    "predecessor_sha": authorization.sequence.predecessor_sha,
                    "step": authorization.sequence.step,
                },
                "targets": list(authorization.targets),
            }
            if authorization
            else None
        ),
        "authorization_comment_id": authorization_comment_id,
        "base_sha": base_sha,
        "characterization_sha256": hashlib.sha256(
            json.dumps(
                characterization, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode()
        ).hexdigest(),
        "changed_paths": list(actual_scope),
        "head_sha": head_sha,
        "other_standard_clauses_waived": False,
        "overall_result": "BLOCK" if unique_blocks else "PASS",
        "policy_blocks": unique_blocks,
        "repository": repository_name,
        "schema_version": RESULT_SCHEMA,
        "targets": list(targets),
        "unbounded_paths": list(unbounded),
    }


def _write_result(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-refactor-policy")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--characterization-result", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Verify one GitHub-hosted refactor increment without executing target code."""
    arguments = _parser().parse_args(argv)
    try:
        if (
            os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("GITHUB_EVENT_NAME") != "pull_request"
        ):
            raise RefactorPolicyError("UNAUTHENTICATED_HOSTED_CONTEXT")
        event, _ = _read_json(Path(arguments.event), "MALFORMED_GITHUB_EVENT")
        characterization, _ = _read_json(
            Path(arguments.characterization_result), "MALFORMED_CHARACTERIZATION_RESULT"
        )
        repository_name, _, _, pull_number = _event_values(event)
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RefactorPolicyError("GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")
        comments = _github_comments(repository_name, pull_number, token)
        _, base_sha, _, _ = _event_values(event)
        predecessor, predecessor_block = _predecessor_authorization(
            repository_name, base_sha, token
        )
        result = verify_refactor(
            Path(arguments.repository),
            event,
            characterization,
            comments,
            predecessor,
            predecessor_block,
        )
        _write_result(Path(arguments.output), result)
    except Exception as error:  # fail closed at hosted-job boundary
        print(getattr(error, "code", "TECHNICAL_FAILURE"))
        return 2
    print(result["overall_result"])
    return 1 if result["overall_result"] == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
