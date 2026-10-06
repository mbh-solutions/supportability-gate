"""Verify authenticated, focused, runnable strangler-refactor increments."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supportability_gate import characterization as characterization_evidence
from supportability_gate import contract, git_changes


def __getattr__(name: str) -> Any:
    if name != "refactor_targets":
        raise AttributeError(name)
    from supportability_gate import refactor_targets

    return refactor_targets


AUTHORIZATION_PREFIX = "Supportability-Refactor-Authorization: "
AUTHORIZATION_SCHEMA = "2.0"
RESULT_SCHEMA = "refactor-policy-result.v1"
CHARACTERIZATION_SCHEMA = characterization_evidence.RESULT_SCHEMA
COMPATIBLE_CHARACTERIZATION_SCHEMAS = frozenset(
    {
        "characterization-result.v1",
        CHARACTERIZATION_SCHEMA,
        characterization_evidence.OBSERVED_RESULT_SCHEMA,
        characterization_evidence.MODULE_RESULT_SCHEMA,
        characterization_evidence.CORRECTION_RESULT_SCHEMA,
    }
)
RUNNABILITY_SCHEMA = characterization_evidence.RUNNABILITY_SCHEMA
TRUSTED_OWNER_ID = 229662739
SHA = re.compile(r"[0-9a-f]{40}\Z")
SERIES_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
MAX_JSON_BYTES = 1_000_000
MAX_GITHUB_PAGES = 10


class RefactorPolicyError(ValueError):
    """One fail-closed refactor-policy evidence error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DuplicateKeyError(ValueError):
    """One duplicate key in a JSON object."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKeyError(key)
        value[key] = item
    return value


@dataclass(frozen=True)
class Sequence:
    """One logical step tied to its immutable predecessor."""

    step: int
    predecessor_sha: str
    series_id: str


@dataclass(frozen=True)
class RelatedTest:
    """One exact fixed-profile test-to-target association."""

    path: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class Authorization:
    """Exact owner-authored pull-request authorization."""

    repository: str
    base_sha: str
    head_sha: str
    broad: bool
    related_tests: tuple[RelatedTest, ...]
    scope: tuple[str, ...]
    targets: tuple[str, ...]
    sequence: Sequence
    introductions: tuple[dict[str, Any], ...] = ()
    schema_version: str = AUTHORIZATION_SCHEMA
    correction_id: str | None = None
    oracle_commit_sha: str | None = None
    oracle_manifest_blob_sha: str | None = None
    oracle_manifest_sha256: str | None = None
    behavior_delta_sha256: str | None = None


@dataclass(frozen=True)
class PredecessorEvidence:
    """Authenticated immediate-predecessor PR facts or one owned lookup block."""

    authorization: Authorization | None = None
    authorization_comment_id: int | None = None
    base_sha: str | None = None
    block: str | None = None
    head_sha: str | None = None
    merge_sha: str | None = None
    pull_number: int | None = None


def _read_json(path: Path, code: str) -> tuple[dict[str, Any], bytes]:
    try:
        content = path.read_bytes()
        if not content or len(content) > MAX_JSON_BYTES:
            raise RefactorPolicyError(code)
        value = json.loads(content, object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError) as error:
        raise RefactorPolicyError(code) from error
    if not isinstance(value, dict):
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


def _related_tests(value: object) -> tuple[RelatedTest, ...]:
    if not isinstance(value, list):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    rows: list[RelatedTest] = []
    for item in value:
        row = _exact_keys(item, {"path", "targets"}, "MALFORMED_OWNER_AUTHORIZATION")
        try:
            path = contract.normalize_repository_path(row["path"], "authorization.related_tests")
        except contract.ContractError as error:
            raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION") from error
        rows.append(RelatedTest(path, _target_list(row["targets"])))
    if rows != sorted(set(rows), key=lambda item: (item.path, item.targets)):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    return tuple(rows)


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
        data = json.loads(rows[0], object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, _DuplicateKeyError) as error:
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION") from error
    if not isinstance(data, dict):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    version = data.get("schema_version")
    row = _exact_keys(
        data,
        {
            "base_sha",
            "broad",
            "head_sha",
            "related_tests",
            "repository",
            "schema_version",
            "scope",
            "sequence",
            "targets",
        }
        | ({"introductions"} if version in {"3.0", "4.0", "5.0"} else set())
        | (
            {
                "behavior_delta_sha256",
                "correction_id",
                "oracle_commit_sha",
                "oracle_manifest_blob_sha",
                "oracle_manifest_sha256",
            }
            if version == "5.0"
            else set()
        ),
        "MALFORMED_OWNER_AUTHORIZATION",
    )
    sequence = _exact_keys(
        row["sequence"],
        {"predecessor_sha", "series_id", "step"},
        "MALFORMED_OWNER_AUTHORIZATION",
    )
    if (
        row["schema_version"] not in {AUTHORIZATION_SCHEMA, "3.0", "4.0", "5.0"}
        or not isinstance(row["repository"], str)
        or not isinstance(row["base_sha"], str)
        or SHA.fullmatch(row["base_sha"]) is None
        or not isinstance(row["head_sha"], str)
        or SHA.fullmatch(row["head_sha"]) is None
        or type(row["broad"]) is not bool
        or type(sequence["step"]) is not int
        or sequence["step"] < 1
        or not isinstance(sequence["series_id"], str)
        or SERIES_ID.fullmatch(sequence["series_id"]) is None
        or not isinstance(sequence["predecessor_sha"], str)
        or SHA.fullmatch(sequence["predecessor_sha"]) is None
        or (
            version == "5.0"
            and (
                not isinstance(row["correction_id"], str)
                or characterization_evidence.SCENARIO_ID.fullmatch(row["correction_id"]) is None
                or not isinstance(row["oracle_commit_sha"], str)
                or SHA.fullmatch(row["oracle_commit_sha"]) is None
                or not isinstance(row["oracle_manifest_blob_sha"], str)
                or SHA.fullmatch(row["oracle_manifest_blob_sha"]) is None
                or not isinstance(row["oracle_manifest_sha256"], str)
                or characterization_evidence.SHA256.fullmatch(row["oracle_manifest_sha256"]) is None
                or not isinstance(row["behavior_delta_sha256"], str)
                or characterization_evidence.SHA256.fullmatch(row["behavior_delta_sha256"]) is None
            )
        )
    ):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    return Authorization(
        row["repository"],
        row["base_sha"],
        row["head_sha"],
        row["broad"],
        _related_tests(row["related_tests"]),
        _path_list(row["scope"], "authorization.scope"),
        _target_list(row["targets"]),
        Sequence(sequence["step"], sequence["predecessor_sha"], sequence["series_id"]),
        tuple(parse_introduction_grants(row["introductions"], version=version))
        if version in {"3.0", "4.0", "5.0"}
        else (),
        str(version),
        str(row["correction_id"]) if version == "5.0" else None,
        str(row["oracle_commit_sha"]) if version == "5.0" else None,
        str(row["oracle_manifest_blob_sha"]) if version == "5.0" else None,
        str(row["oracle_manifest_sha256"]) if version == "5.0" else None,
        str(row["behavior_delta_sha256"]) if version == "5.0" else None,
    )


def parse_introduction_grants(value: object, *, version: str = "3.0") -> list[dict[str, Any]]:
    """Validate precise owner-reviewed intended-oracle grants, never waivers."""
    limit = 100 if version in {"4.0", "5.0"} else 50
    if (
        version not in {"2.0", "3.0", "4.0", "5.0"}
        or not isinstance(value, list)
        or len(value) > limit
    ):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    rows = [_introduction_grant(item) for item in value]
    identities = [item["scenario"] for item in rows]
    if (
        identities != sorted(set(identities))
        or sum(len(row.get("module_roots", [row["api"]])) for row in rows) > limit
    ):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    if version in {"4.0", "5.0"}:
        roots = [root for row in rows for root in row.get("module_roots", [row["api"]])]
        if len(roots) != len(set(roots)):
            raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    return rows


def _read_characterization_result(
    path: Path, repository: Path, event: dict[str, Any]
) -> tuple[dict[str, Any], bytes]:
    """Select the fixed result transport using the actual exact-head manifest."""
    try:
        value, content = _read_json(path, "MALFORMED_CHARACTERIZATION_RESULT")
    except RefactorPolicyError:
        pass
    else:
        if value.get("schema_version") not in {
            characterization_evidence.MODULE_RESULT_SCHEMA,
            characterization_evidence.CORRECTION_RESULT_SCHEMA,
        }:
            return value, content
    _, _, head_sha, _ = _event_values(event)
    try:
        manifest = characterization_evidence._manifest(repository, head_sha, [])
        if manifest.schema_version not in {"4.0", "5.0"}:
            raise RefactorPolicyError("MALFORMED_CHARACTERIZATION_RESULT")
        with path.open("rb") as stream:
            content = stream.read(characterization_evidence.MODULE_AGGREGATE_JSON_BYTES + 1)
        value = characterization_evidence._read_module_aggregate(
            content,
            "MALFORMED_CHARACTERIZATION_RESULT",
            characterization_evidence.result_schema(manifest.schema_version),
        )
    except (OSError, git_changes.GitError, characterization_evidence.CharacterizationError):
        raise RefactorPolicyError("MALFORMED_CHARACTERIZATION_RESULT") from None
    return value, content


def _introduction_grant(value: object) -> dict[str, Any]:
    module_keys = {"module_roots", "module_inventory_sha256", "module_oracle_sha256"}
    module = isinstance(value, dict) and bool(module_keys & value.keys())
    row = _exact_keys(
        value,
        {
            "api",
            "scenario",
            "source_sha256",
            "driver_sha256",
            "oracle_sha256",
            "review_sha256",
            "intended_feature",
            "independent_oracle_reviewed",
        }
        | (module_keys if module else set()),
        "MALFORMED_OWNER_AUTHORIZATION",
    )
    hashes = ("source_sha256", "driver_sha256", "oracle_sha256", "review_sha256")
    if (
        any(
            not isinstance(row[key], str)
            or characterization_evidence.SHA256.fullmatch(row[key]) is None
            for key in hashes
        )
        or not isinstance(row["scenario"], str)
        or characterization_evidence.SCENARIO_ID.fullmatch(row["scenario"]) is None
        or not isinstance(row["api"], str)
        or "::function:" not in row["api"]
        or not isinstance(row["intended_feature"], str)
        or not row["intended_feature"].strip()
        or len(row["intended_feature"]) > 2000
        or row["independent_oracle_reviewed"] is not True
    ):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
    if module:
        _module_introduction_grant(row)
    return row


def _module_introduction_grant(row: dict[str, Any]) -> None:
    try:
        roots = characterization_evidence._module_roots(
            row["module_roots"], row["api"], (row["api"].split("::", 1)[0],)
        )
    except characterization_evidence.CharacterizationError as error:
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION") from error
    if not roots or any(
        not isinstance(row[key], str)
        or characterization_evidence.SHA256.fullmatch(row[key]) is None
        for key in ("module_inventory_sha256", "module_oracle_sha256")
    ):
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")


def introduction_authorization_blocks(
    characterization: object,
    grants: object,
    targets: tuple[str, ...],
    *,
    authorization_version: str = "3.0",
) -> list[str]:
    """Join authenticated owner intent to every exact measured API birth."""
    if not isinstance(characterization, dict):
        return ["UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    if authorization_version in {"4.0", "5.0"} and characterization.get("schema_version") not in {
        characterization_evidence.MODULE_RESULT_SCHEMA,
        characterization_evidence.CORRECTION_RESULT_SCHEMA,
    }:
        return ["INTRODUCTION_AUTHORIZATION_MISMATCH:version"]
    try:
        approved = parse_introduction_grants(grants, version=authorization_version)
        facts = characterization_evidence._result_api_facts(
            characterization.get("api_observations", []),
            allow_module=characterization.get("schema_version")
            in {
                characterization_evidence.MODULE_RESULT_SCHEMA,
                characterization_evidence.CORRECTION_RESULT_SCHEMA,
            },
        )
    except (RefactorPolicyError, characterization_evidence.CharacterizationError):
        return ["MALFORMED_INTRODUCTION_AUTHORIZATION"]
    births = {key: row for key, row in facts.items() if row["base_absent"]}
    by_id = {row["scenario"]: row for row in approved}
    if births.keys() != by_id.keys():
        return ["INTRODUCTION_AUTHORIZATION_MISMATCH"]
    return [
        f"INTRODUCTION_AUTHORIZATION_MISMATCH:{key}"
        for key, fact in births.items()
        if not _introduction_grant_matches(fact, by_id[key], targets)
    ]


def _introduction_grant_matches(
    fact: dict[str, Any], grant: dict[str, Any], targets: tuple[str, ...]
) -> bool:
    source = fact["head_source"]
    return bool(
        isinstance(source, dict)
        and fact["admissible"]
        and grant["api"] == fact["api"]
        and grant["source_sha256"] == source["source_sha256"]
        and grant["driver_sha256"] == fact["driver_sha256"]
        and grant["oracle_sha256"] == fact["oracle_sha256"]
        and fact["oracle_review_valid"]
        and grant["review_sha256"] == fact["review_sha256"]
        and grant["intended_feature"] == fact["intended_feature"]
        and fact["api"] in _target_identities(targets)
        and _module_introduction_matches(fact, grant)
    )


def _module_introduction_matches(fact: dict[str, Any], grant: dict[str, Any]) -> bool:
    module = fact.get("module")
    if module is None:
        return "module_roots" not in grant
    return bool(
        fact["base_execution_verified"]
        and fact["head_execution_verified"]
        and grant.get("module_roots") == module["roots"]
        and grant.get("module_inventory_sha256") == module["head_inventory"]["inventory_sha256"]
        and grant.get("module_oracle_sha256") == module["oracle_sha256"]
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
        same_series = (
            predecessor is not None
            and predecessor.sequence.series_id == authorization.sequence.series_id
        )
        return ["INVALID_STRANGLER_SEQUENCE"] if same_series else []
    if (
        predecessor is None
        or predecessor.sequence.series_id != authorization.sequence.series_id
        or predecessor.sequence.step != authorization.sequence.step - 1
        or _target_identities(predecessor.targets) != _target_identities(authorization.targets)
    ):
        return ["INVALID_STRANGLER_SEQUENCE"]
    return []


def _target_identities(targets: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(target.rsplit(":", 1)[0] for target in targets)


def _owner_authorization(
    event: dict[str, Any], comments: tuple[dict[str, Any], ...]
) -> tuple[Authorization, int]:
    _, _, head_sha, _ = _event_values(event)
    candidates: list[tuple[Authorization, int]] = []
    stale = False
    untrusted = False
    for comment in comments:
        user = comment.get("user")
        body = comment.get("body")
        if not isinstance(user, dict) or user.get("id") != TRUSTED_OWNER_ID:
            untrusted = untrusted or (
                isinstance(body, str)
                and any(line.startswith(AUTHORIZATION_PREFIX) for line in body.splitlines())
            )
            continue
        if not isinstance(body, str):
            raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION")
        try:
            authorization = _parse_authorization(body)
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
    missing_code = (
        "UNAUTHENTICATED_OWNER_AUTHORIZATION"
        if untrusted
        else "STALE_OWNER_AUTHORIZATION"
        if stale
        else "MISSING_OWNER_AUTHORIZATION"
    )
    if len(candidates) != 1:
        raise RefactorPolicyError("MALFORMED_OWNER_AUTHORIZATION" if candidates else missing_code)
    return candidates[0]


def _github_rows(endpoint: str, token: str, opener: Any) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_GITHUB_PAGES + 1):
        request = urllib.request.Request(
            f"{endpoint}?per_page=100&page={page}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with opener(request, timeout=30) as response:
                content = response.read(MAX_JSON_BYTES + 1)
                headers = getattr(response, "headers", {})
                link = headers.get("Link")
            value = json.loads(content, object_pairs_hook=_unique_object)
        except (
            OSError,
            TimeoutError,
            UnicodeDecodeError,
            http.client.HTTPException,
            urllib.error.URLError,
            json.JSONDecodeError,
            _DuplicateKeyError,
        ) as error:
            raise RefactorPolicyError("GITHUB_AUTHORIZATION_EVIDENCE_FAILURE") from error
        if (
            len(content) > MAX_JSON_BYTES
            or not isinstance(value, list)
            or any(not isinstance(item, dict) for item in value)
            or (link is not None and not isinstance(link, str))
        ):
            raise RefactorPolicyError("GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")
        rows.extend(value)
        if not link or re.search(r'<[^>]+>;\s*rel="next"', link) is None:
            return tuple(rows)
    raise RefactorPolicyError("GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")


def _github_comments(
    repository: str,
    pull_number: int,
    token: str,
    opener: Any = urllib.request.urlopen,
) -> tuple[dict[str, Any], ...]:
    comments = _github_rows(
        f"https://api.github.com/repos/{repository}/issues/{pull_number}/comments",
        token,
        opener,
    )
    if any(
        type(comment.get("id")) is not int
        or comment["id"] < 1
        or not isinstance(comment.get("body"), str)
        or (
            comment.get("user") is not None
            and (
                not isinstance(comment["user"], dict)
                or type(comment["user"].get("id")) is not int
                or comment["user"]["id"] < 1
            )
        )
        for comment in comments
    ):
        raise RefactorPolicyError("GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")
    return comments


def _valid_predecessor_pull(item: dict[str, Any]) -> bool:
    base = item.get("base")
    head = item.get("head")
    merge_sha = item.get("merge_commit_sha")
    merged_at = item.get("merged_at")
    return (
        type(item.get("number")) is int
        and item["number"] > 0
        and isinstance(base, dict)
        and isinstance(head, dict)
        and isinstance(base.get("sha"), str)
        and SHA.fullmatch(base["sha"]) is not None
        and isinstance(head.get("sha"), str)
        and SHA.fullmatch(head["sha"]) is not None
        and (
            merge_sha is None or isinstance(merge_sha, str) and SHA.fullmatch(merge_sha) is not None
        )
        and (merged_at is None or isinstance(merged_at, str) and bool(merged_at))
        and (merged_at is None or merge_sha is not None)
    )


def _predecessor_authorization(
    repository: str,
    base_sha: str,
    token: str,
    opener: Any = urllib.request.urlopen,
) -> PredecessorEvidence:
    try:
        value = _github_rows(
            f"https://api.github.com/repos/{repository}/commits/{base_sha}/pulls",
            token,
            opener,
        )
    except RefactorPolicyError:
        return PredecessorEvidence(block="GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")
    if any(not _valid_predecessor_pull(item) for item in value):
        return PredecessorEvidence(block="GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")
    merged = [item for item in value if item.get("merge_commit_sha") == base_sha]
    if not merged:
        return PredecessorEvidence()
    if any(not isinstance(item["merged_at"], str) for item in merged):
        return PredecessorEvidence(block="GITHUB_AUTHORIZATION_EVIDENCE_FAILURE")
    if len(merged) != 1:
        return PredecessorEvidence(block="INVALID_STRANGLER_SEQUENCE")
    event = {"repository": {"full_name": repository}, "pull_request": merged[0]}
    try:
        comments = _github_comments(repository, merged[0]["number"], token, opener)
        authorization, comment_id = _owner_authorization(event, comments)
    except RefactorPolicyError as error:
        block = {
            "MISSING_OWNER_AUTHORIZATION": None,
            "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE": error.code,
        }.get(error.code, "INVALID_STRANGLER_SEQUENCE")
        return PredecessorEvidence(block=block)
    if _authorization_blocks(event, authorization):
        return PredecessorEvidence(block="INVALID_STRANGLER_SEQUENCE")
    _, pull_base, pull_head, pull_number = _event_values(event)
    return PredecessorEvidence(
        authorization,
        comment_id,
        pull_base,
        None,
        pull_head,
        base_sha,
        pull_number,
    )


def _authorization_payload(authorization: Authorization | None) -> dict[str, object] | None:
    if authorization is None:
        return None
    return {
        "base_sha": authorization.base_sha,
        "broad": authorization.broad,
        "head_sha": authorization.head_sha,
        "related_tests": [
            {"path": related.path, "targets": list(related.targets)}
            for related in authorization.related_tests
        ],
        "repository": authorization.repository,
        "scope": list(authorization.scope),
        "sequence": {
            "predecessor_sha": authorization.sequence.predecessor_sha,
            "series_id": authorization.sequence.series_id,
            "step": authorization.sequence.step,
        },
        "targets": list(authorization.targets),
        **(
            {"introductions": list(authorization.introductions)}
            if authorization.introductions or authorization.schema_version in {"4.0", "5.0"}
            else {}
        ),
        **(
            {
                "behavior_delta_sha256": authorization.behavior_delta_sha256,
                "correction_id": authorization.correction_id,
                "oracle_commit_sha": authorization.oracle_commit_sha,
                "oracle_manifest_blob_sha": authorization.oracle_manifest_blob_sha,
                "oracle_manifest_sha256": authorization.oracle_manifest_sha256,
            }
            if authorization.schema_version == "5.0"
            else {}
        ),
        **(
            {"schema_version": authorization.schema_version}
            if authorization.schema_version in {"4.0", "5.0"}
            else {}
        ),
    }


def _predecessor_payload(evidence: PredecessorEvidence) -> dict[str, object]:
    return {
        "authorization": _authorization_payload(evidence.authorization),
        "authorization_comment_id": evidence.authorization_comment_id,
        "base_sha": evidence.base_sha,
        "block": evidence.block,
        "head_sha": evidence.head_sha,
        "merge_sha": evidence.merge_sha,
        "pull_number": evidence.pull_number,
    }


def _changed_scope(changes: tuple[git_changes.ChangedPath, ...]) -> tuple[str, ...]:
    return tuple(
        sorted({path for item in changes for path in (item.old_path, item.new_path) if path})
    )


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
    related_paths, invalid_related = _related_test_paths(
        authorization, actual_scope, targets, policy.language
    )
    if invalid_related:
        blocks.append("BROAD_AUTHORIZATION_REQUIRED")
    unrelated = [
        path
        for path in actual_scope
        if path not in production_paths and path not in related_paths and not _proof_path(path)
    ]
    broad_required = len(targets) != 1 or len(production_paths) != 1 or bool(unrelated)
    if broad_required and not authorization.broad:
        blocks.append("BROAD_AUTHORIZATION_REQUIRED")
    if not targets or any(not policy.is_production_path(path) for path in production_paths):
        blocks.append("MISSING_BOUNDED_PRODUCTION_TARGET")
    return blocks


def _related_test_paths(
    authorization: Authorization,
    actual_scope: tuple[str, ...],
    targets: tuple[str, ...],
    language: str,
) -> tuple[set[str], bool]:
    from supportability_gate import refactor_targets

    target_paths = {target: target.split("::", 1)[0] for target in targets}
    valid: set[str] = set()
    invalid = False
    for related in authorization.related_tests:
        matches = (
            related.path in actual_scope
            and bool(related.targets)
            and all(
                target in target_paths
                and refactor_targets.related_test_matches(
                    related.path, target_paths[target], language
                )
                for target in related.targets
            )
        )
        if matches:
            valid.add(related.path)
        else:
            invalid = True
    return valid, invalid


def _runnability_blocks(
    value: dict[str, Any],
    repository: str,
    base_sha: str,
    head_sha: str,
    targets: tuple[str, ...],
    unbounded_paths: tuple[str, ...],
    reconciled_blocks: frozenset[str] = frozenset(),
    corrected_scenarios: frozenset[str] = frozenset(),
) -> list[str]:
    evidence = value.get("refactor_runnability")
    keys = {
        "base_sha",
        "head_sha",
        "repository",
        "runnable",
        "schema_version",
        "targets",
        "unbounded_paths",
        "workflow_sha",
    }
    if not isinstance(evidence, dict) or set(evidence) != keys:
        return ["UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    evidence_targets = evidence["targets"]
    evidence_unbounded = evidence["unbounded_paths"]
    workflow_sha = value.get("workflow_sha")
    if (
        evidence["schema_version"] != RUNNABILITY_SCHEMA
        or type(evidence["runnable"]) is not bool
        or not isinstance(evidence["repository"], str)
        or not isinstance(evidence_targets, list)
        or evidence_targets != sorted(set(evidence_targets))
        or any(not isinstance(item, str) for item in evidence_targets)
        or not isinstance(evidence_unbounded, list)
        or evidence_unbounded != sorted(set(evidence_unbounded))
        or any(not isinstance(item, str) for item in evidence_unbounded)
        or not isinstance(workflow_sha, str)
        or SHA.fullmatch(workflow_sha) is None
        or not isinstance(evidence["workflow_sha"], str)
        or SHA.fullmatch(evidence["workflow_sha"]) is None
        or not isinstance(evidence["base_sha"], str)
        or SHA.fullmatch(evidence["base_sha"]) is None
        or not isinstance(evidence["head_sha"], str)
        or SHA.fullmatch(evidence["head_sha"]) is None
    ):
        return ["UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    try:
        normalized = tuple(
            contract.normalize_repository_path(path, "refactor_runnability.unbounded_paths")
            for path in evidence_unbounded
        )
    except contract.ContractError:
        return ["UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    if (
        tuple(evidence_targets) != targets
        or tuple(evidence_unbounded) != unbounded_paths
        or tuple(evidence_unbounded) != normalized
    ):
        return ["UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    stale = (
        evidence["repository"] != f"github.com/{repository}"
        or evidence["base_sha"] != base_sha
        or evidence["head_sha"] != head_sha
        or evidence["workflow_sha"] != workflow_sha
    )
    blocks = ["STALE_RUNNABILITY_EVIDENCE"] if stale else []
    coverage = value.get("coverage")
    scenarios = value.get("scenarios")
    if (
        not isinstance(coverage, dict)
        or not isinstance(coverage.get("covered_paths"), list)
        or any(not isinstance(path, str) for path in coverage["covered_paths"])
        or not isinstance(scenarios, list)
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("covers"), list)
            or any(not isinstance(path, str) for path in item["covers"])
            or item.get("compatibility") not in {"PASS", "BLOCK"}
            for item in scenarios
        )
    ):
        return [*blocks, "UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    target_paths = {target.split("::", 1)[0] for target in targets}
    if target_paths - set(coverage["covered_paths"]):
        blocks.append("MISSING_RUNNABILITY_COVERAGE")
    elif not evidence["runnable"]:
        blocks.append("NON_RUNNABLE_LOGICAL_STEP")
    remaining = set(value.get("policy_blocks", [])) - reconciled_blocks
    incompatible = {str(item.get("id")) for item in scenarios if item["compatibility"] != "PASS"}
    if remaining or incompatible - corrected_scenarios:
        blocks.append("NON_RUNNABLE_LOGICAL_STEP")
    return blocks


def _git_ancestor(
    repository: Path,
    ancestor: str,
    descendant: str,
    records: list[git_changes.CommandRecord],
) -> bool:
    try:
        git_changes.run_git(
            repository, ("merge-base", "--is-ancestor", ancestor, descendant), records
        )
    except git_changes.GitError:
        return False
    return True


def _correction_manifest_blocks(
    repository: Path,
    oracle_sha: str,
    head_sha: str,
    correction: dict[str, Any],
    records: list[git_changes.CommandRecord],
) -> list[str]:
    try:
        oracle = git_changes.read_regular_blob(
            repository, oracle_sha, characterization_evidence.MANIFEST_PATH, records
        )
        head = git_changes.read_regular_blob(
            repository, head_sha, characterization_evidence.MANIFEST_PATH, records
        )
    except git_changes.GitError:
        return ["CORRECTION_ORACLE_MISMATCH"]
    mismatch = (
        oracle.object_sha != correction["oracle_manifest_blob_sha"]
        or hashlib.sha256(oracle.content).hexdigest() != correction["oracle_manifest_sha256"]
        or oracle.content != head.content
    )
    return ["MODIFIED_CORRECTION_ORACLE"] if mismatch else []


def _correction_file_blocks(
    repository: Path,
    oracle_sha: str,
    head_sha: str,
    value: object,
    records: list[git_changes.CommandRecord],
) -> tuple[list[str], set[str]]:
    if not isinstance(value, list):
        return ["MALFORMED_CORRECTION"], set()
    blocks: list[str] = []
    paths: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "path", "sha256"}:
            blocks.append("MALFORMED_CORRECTION")
            continue
        path = str(item["path"])
        paths.add(path)
        try:
            oracle = git_changes.read_regular_blob(repository, oracle_sha, path, records)
            head = git_changes.read_regular_blob(repository, head_sha, path, records)
        except git_changes.GitError:
            blocks.append("CORRECTION_ORACLE_MISMATCH")
            continue
        if (
            hashlib.sha256(oracle.content).hexdigest() != item["sha256"]
            or oracle.content != head.content
        ):
            blocks.append("MODIFIED_CORRECTION_ORACLE")
    return blocks, paths


def _correction_scope_blocks(
    repository: Path,
    policy: contract.Contract,
    base_sha: str,
    oracle_sha: str,
    oracle_paths: set[str],
    records: list[git_changes.CommandRecord],
) -> list[str]:
    changes = git_changes.changed_paths(repository, base_sha, oracle_sha, records)
    changed = {
        path for item in changes for path in (item.old_path, item.new_path) if path is not None
    }
    blocks = (
        ["CORRECTION_ORACLE_MISMATCH"]
        if any(policy.is_production_path(path) for path in changed)
        else []
    )
    if changed - oracle_paths:
        blocks.append("UNDECLARED_CORRECTION_DELTA")
    return blocks


def _correction_history_blocks(
    repository: Path,
    oracle_sha: str,
    head_sha: str,
    oracle_paths: set[str],
    records: list[git_changes.CommandRecord],
) -> list[str]:
    try:
        commits = git_changes.run_git(
            repository, ("rev-list", "--ancestry-path", f"{oracle_sha}..{head_sha}"), records
        )
        paths = tuple(f":(literal){path}" for path in sorted(oracle_paths))
        for commit in commits.decode("ascii").split():
            changed = git_changes.run_git(
                repository,
                ("diff", "--name-only", "--no-renames", oracle_sha, commit, "--", *paths),
                records,
            )
            if changed:
                return ["MODIFIED_CORRECTION_ORACLE"]
    except git_changes.GitError:
        return ["CORRECTION_ORACLE_MISMATCH"]
    return []


def _correction_transaction_blocks(
    repository: Path,
    policy: contract.Contract,
    base_sha: str,
    head_sha: str,
    authorization: Authorization | None,
    value: dict[str, Any],
    records: list[git_changes.CommandRecord],
) -> tuple[list[str], frozenset[str], frozenset[str]]:
    correction = value.get("correction")
    if not isinstance(correction, dict):
        return ["MALFORMED_CORRECTION"], frozenset(), frozenset()
    required = {
        "behavior_delta",
        "behavior_delta_sha256",
        "id",
        "obligations",
        "oracle_files",
        "oracle_manifest_blob_sha",
        "oracle_manifest_sha256",
        "reconcilable_blocks",
        "scenarios",
        "targets",
        "verification_blocks",
    }
    if set(correction) != required:
        return ["MALFORMED_CORRECTION"], frozenset(), frozenset()
    reconciled = frozenset(str(item) for item in correction["reconcilable_blocks"])
    scenarios = frozenset(str(item) for item in correction["scenarios"])
    blocks = [str(item) for item in correction["verification_blocks"]]
    if authorization is None or authorization.schema_version != "5.0":
        return [*blocks, "UNAUTHORIZED_CORRECTION"], reconciled, scenarios
    if (
        authorization.correction_id != correction["id"]
        or authorization.oracle_manifest_blob_sha != correction["oracle_manifest_blob_sha"]
        or authorization.oracle_manifest_sha256 != correction["oracle_manifest_sha256"]
        or authorization.behavior_delta_sha256 != correction["behavior_delta_sha256"]
    ):
        blocks.append("UNAUTHORIZED_CORRECTION")
    oracle_sha = authorization.oracle_commit_sha
    if (
        oracle_sha is None
        or not _git_ancestor(repository, base_sha, oracle_sha, records)
        or not _git_ancestor(repository, oracle_sha, head_sha, records)
    ):
        return [*blocks, "STALE_CORRECTION_ORACLE"], reconciled, scenarios
    blocks.extend(
        _correction_manifest_blocks(repository, oracle_sha, head_sha, correction, records)
    )
    file_blocks, oracle_paths = _correction_file_blocks(
        repository, oracle_sha, head_sha, correction["oracle_files"], records
    )
    blocks.extend(file_blocks)
    oracle_paths.add(characterization_evidence.MANIFEST_PATH)
    blocks.extend(
        _correction_history_blocks(repository, oracle_sha, head_sha, oracle_paths, records)
    )
    blocks.extend(
        _correction_scope_blocks(repository, policy, base_sha, oracle_sha, oracle_paths, records)
    )
    return sorted(set(blocks)), reconciled, scenarios


def _characterization_blocks(
    value: dict[str, Any],
    repository: str,
    base_sha: str,
    head_sha: str,
    targets: tuple[str, ...],
    unbounded_paths: tuple[str, ...],
    repository_path: Path,
    policy: contract.Contract,
    authorization: Authorization | None,
    records: list[git_changes.CommandRecord],
) -> list[str]:
    blocks: list[str] = []
    if value.get("schema_version") not in COMPATIBLE_CHARACTERIZATION_SCHEMAS:
        return ["UNAUTHENTICATED_RUNNABILITY_EVIDENCE"]
    if (
        value.get("repository") != f"github.com/{repository}"
        or value.get("base_sha") != base_sha
        or value.get("head_sha") != head_sha
    ):
        blocks.append("STALE_RUNNABILITY_EVIDENCE")
    if value.get("schema_version") == characterization_evidence.CORRECTION_RESULT_SCHEMA:
        if "correction" not in value:
            if value.get("overall_result") != "PASS":
                return [*blocks, *[str(item) for item in value.get("policy_blocks", [])]]
            return [
                *blocks,
                *_runnability_blocks(
                    value, repository, base_sha, head_sha, targets, unbounded_paths
                ),
            ]
        correction_blocks, reconciled, scenarios = _correction_transaction_blocks(
            repository_path, policy, base_sha, head_sha, authorization, value, records
        )
        remaining = set(value.get("policy_blocks", [])) - reconciled
        return [
            *blocks,
            *correction_blocks,
            *sorted(remaining),
            *_runnability_blocks(
                value,
                repository,
                base_sha,
                head_sha,
                targets,
                unbounded_paths,
                reconciled,
                scenarios,
            ),
        ]
    if value.get("overall_result") != "PASS":
        return blocks
    return [
        *blocks,
        *_runnability_blocks(value, repository, base_sha, head_sha, targets, unbounded_paths),
    ]


def verify_refactor(
    repository: Path,
    event: dict[str, Any],
    characterization: dict[str, Any],
    comments: tuple[dict[str, Any], ...],
    predecessor: PredecessorEvidence | None = None,
    authorization_block: str | None = None,
) -> dict[str, object]:
    """Return deterministic M8 authorization, focus, runnability, and sequence evidence."""
    from supportability_gate import refactor_targets

    records: list[git_changes.CommandRecord] = []
    repository = git_changes.validate_repository(repository, records)
    repository_name, base_sha, head_sha, _ = _event_values(event)
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
    policy_blob = git_changes.read_regular_blob(
        repository, base_sha, ".supportability.toml", records
    )
    policy = contract.parse_contract(policy_blob.content)
    candidate_blob = git_changes.read_regular_blob(
        repository, head_sha, ".supportability.toml", records
    )
    changes = git_changes.changed_paths(repository, base_sha, head_sha, records)
    if candidate_blob.content != policy_blob.content:
        candidate_policy = contract.parse_contract(candidate_blob.content)
        deleted_paths = {
            item.old_path
            for item in changes
            if item.status == "DELETED" and item.old_path is not None and item.new_path is None
        }
        if contract.is_profile_expansion(
            policy, candidate_policy
        ) or contract.is_profile_retirement(policy, candidate_policy, deleted_paths):
            policy = candidate_policy
    actual_scope = _changed_scope(changes)
    targets, unbounded = refactor_targets.derive(repository, identity, policy, changes, records)
    applicable = bool(targets or unbounded)
    blocks: list[str] = []
    authorization: Authorization | None = None
    authorization_comment_id: int | None = None
    predecessor = predecessor or PredecessorEvidence()
    if applicable:
        if authorization_block is not None:
            blocks.append(authorization_block)
        else:
            try:
                authorization, authorization_comment_id = _owner_authorization(event, comments)
                blocks.extend(_authorization_blocks(event, authorization))
                blocks.extend(
                    _sequence_blocks(authorization, predecessor.authorization, predecessor.block)
                )
                blocks.extend(
                    _focus_blocks(authorization, actual_scope, targets, unbounded, policy)
                )
            except RefactorPolicyError as error:
                blocks.append(error.code)
    if applicable:
        blocks.extend(
            _characterization_blocks(
                characterization,
                repository_name,
                base_sha,
                head_sha,
                targets,
                unbounded,
                repository,
                policy,
                authorization,
                records,
            )
        )
        if authorization is not None:
            blocks.extend(
                introduction_authorization_blocks(
                    characterization,
                    list(authorization.introductions),
                    targets,
                    authorization_version=authorization.schema_version,
                )
            )
    unique_blocks = sorted(set(blocks))
    if not applicable:
        predecessor = PredecessorEvidence()
    return {
        "applicable": applicable,
        "authorization": _authorization_payload(authorization),
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
        "predecessor": _predecessor_payload(predecessor),
        "repository": repository_name,
        "schema_version": RESULT_SCHEMA,
        "targets": list(targets),
        "unbounded_paths": list(unbounded),
        **(
            {
                "result_classification": (
                    "BLOCK"
                    if unique_blocks
                    else "PASS_AUTHORIZED_BEHAVIOR_CORRECTION"
                    if "correction" in characterization
                    else "PASS_NO_BEHAVIOR_CHANGE"
                )
            }
            if characterization.get("schema_version")
            == characterization_evidence.CORRECTION_RESULT_SCHEMA
            else {}
        ),
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
        characterization, _ = _read_characterization_result(
            Path(arguments.characterization_result), Path(arguments.repository), event
        )
        repository_name, _, _, pull_number = _event_values(event)
        token = os.environ.get("GITHUB_TOKEN")
        predecessor = PredecessorEvidence()
        comments: tuple[dict[str, Any], ...]
        if not token:
            comments = ()
            authorization_block = "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE"
        else:
            try:
                comments = _github_comments(repository_name, pull_number, token)
                authorization_block = None
            except RefactorPolicyError as error:
                comments = ()
                authorization_block = error.code
            if authorization_block is None:
                try:
                    _owner_authorization(event, comments)
                except RefactorPolicyError:
                    pass
                else:
                    _, base_sha, _, _ = _event_values(event)
                    predecessor = _predecessor_authorization(repository_name, base_sha, token)
        result = verify_refactor(
            Path(arguments.repository),
            event,
            characterization,
            comments,
            predecessor,
            authorization_block,
        )
        _write_result(Path(arguments.output), result)
    except Exception as error:  # fail closed at hosted-job boundary
        print(getattr(error, "code", "TECHNICAL_FAILURE"))
        return 2
    print(result["overall_result"])
    return 1 if result["overall_result"] == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
