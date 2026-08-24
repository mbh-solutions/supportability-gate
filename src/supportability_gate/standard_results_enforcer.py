"""Validate one authoritative eight-Standard artifact and enforce one entry."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from supportability_gate import clause_inventory
from supportability_gate.standard_results import (
    CHECK_CONTEXTS,
    EVIDENCE_SOURCES,
    RESULTS,
    SCHEMA_VERSION,
    SHA64,
    RunIdentity,
    StandardResultsError,
    _require_owner,
    _string_list,
    _validate_identity,
)


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY") from error
    if parsed.tzinfo is None:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    return parsed


def _completion_identity(
    completion: object, request_id: int | None, requested_time: datetime | None
) -> tuple[tuple[str, int] | None, datetime | None]:
    if completion is None:
        return None, None
    if (
        not isinstance(completion, dict)
        or set(completion) != {"completed_at", "id", "kind"}
        or type(completion.get("id")) is not int
        or completion["id"] < 1
        or completion.get("kind") not in {"comment", "reaction", "review"}
        or not isinstance(completion.get("completed_at"), str)
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if request_id is None or requested_time is None:
        raise StandardResultsError("MISSING_STANDARD_CODEX_BINDING")
    completed_time = _time(completion["completed_at"])
    if completed_time < requested_time:
        raise StandardResultsError("STANDARD_CODEX_BINDING_MISMATCH")
    return (completion["kind"], completion["id"]), completed_time


def _validate_codex(
    value: object, focus: str, result: str, blocks: list[str]
) -> tuple[int | None, datetime | None, tuple[str, int] | None, datetime | None]:
    expected = {"completion", "focus", "request_id", "requested_at"}
    if not isinstance(value, dict) or set(value) != expected or value.get("focus") != focus:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    request_id, requested_at = value.get("request_id"), value.get("requested_at")
    if request_id is not None and (type(request_id) is not int or request_id < 1):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if (request_id is None) != (requested_at is None):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    requested_time = _time(requested_at) if requested_at is not None else None
    artifact, completed_time = _completion_identity(
        value.get("completion"), request_id, requested_time
    )
    codex_blocks = {
        f"FOCUSED_CODEX_REVIEW_PENDING_{focus}",
        f"MISSING_FOCUSED_CODEX_REVIEW_REQUEST_{focus}",
    } & set(blocks)
    if result == "PASS" and artifact is None:
        raise StandardResultsError("MISSING_STANDARD_CODEX_BINDING")
    if result == "BLOCK" and artifact is None and not codex_blocks:
        raise StandardResultsError("MISSING_STANDARD_CODEX_BINDING")
    if artifact is not None and codex_blocks:
        raise StandardResultsError("STANDARD_CODEX_BINDING_MISMATCH")
    return request_id, requested_time, artifact, completed_time


def _validate_entry(
    value: object, standard: int
) -> tuple[int | None, datetime | None, tuple[str, int] | None, datetime | None]:
    expected = {
        "applicable",
        "blocks",
        "check_context",
        "codex_review",
        "evidence_sources",
        "result",
        "standard",
        "technical_errors",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    result = value.get("result")
    if (
        value.get("standard") != standard
        or value.get("check_context") != CHECK_CONTEXTS[standard - 1]
        or type(value.get("applicable")) is not bool
        or result not in RESULTS
        or value.get("evidence_sources") != list(EVIDENCE_SOURCES[standard - 1])
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    blocks = _string_list(value.get("blocks"), "MALFORMED_STANDARD_RESULT_ENTRY")
    technical = _string_list(value.get("technical_errors"), "MALFORMED_STANDARD_RESULT_ENTRY")
    if blocks != sorted(blocks) or technical != sorted(technical):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    codex_families = {
        f"FOCUSED_CODEX_REVIEW_PENDING_{standard}",
        f"MISSING_FOCUSED_CODEX_REVIEW_REQUEST_{standard}",
    }
    for block in set(blocks) - codex_families:
        _require_owner(block, standard)
    if result == "PASS" and (blocks or technical):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if result == "BLOCK" and (not blocks or technical):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    if result == "TECHNICAL_FAILURE" and not technical:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    return _validate_codex(value.get("codex_review"), str(standard), str(result), blocks)


def _validate_quality_artifact(value: object, technical: bool) -> None:
    if value is None and technical:
        return
    if (
        not isinstance(value, dict)
        or set(value) != {"capture_sha256", "digest", "id"}
        or type(value.get("id")) is not int
        or value["id"] < 1
        or not isinstance(value.get("digest"), str)
        or SHA64.fullmatch(value["digest"]) is None
        or not isinstance(value.get("capture_sha256"), str)
        or SHA64.fullmatch(value["capture_sha256"]) is None
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_ARTIFACT")


def validate_payload(value: object, identity: RunIdentity | None = None) -> dict[str, Any]:
    """Validate ordering, identities, bindings, and every lane result."""
    expected = {
        "base_sha",
        "entries",
        "head_sha",
        "quality_artifact",
        "repository",
        "repository_id",
        "run_attempt",
        "run_id",
        "schema_version",
        "standard_sha256",
        "workflow_sha",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS")
    repository_id = value.get("repository_id")
    run_id = value.get("run_id")
    run_attempt = value.get("run_attempt")
    actual = RunIdentity(
        str(value.get("repository")),
        repository_id if type(repository_id) is int else 0,
        str(value.get("base_sha")),
        str(value.get("head_sha")),
        str(value.get("workflow_sha")),
        run_id if type(run_id) is int else 0,
        run_attempt if type(run_attempt) is int else 0,
    )
    _validate_identity(actual)
    if identity is not None and actual != identity:
        raise StandardResultsError("STANDARD_RESULTS_BINDING_MISMATCH")
    entries = value.get("entries")
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("standard_sha256") != clause_inventory.STANDARD_SHA256
        or not isinstance(entries, list)
        or len(entries) != 8
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS")
    codex = [_validate_entry(entry, standard) for standard, entry in enumerate(entries, start=1)]
    request_ids = [item[0] for item in codex if item[0] is not None]
    artifacts = [item[2] for item in codex if item[2] is not None]
    if len(request_ids) != len(set(request_ids)) or len(artifacts) != len(set(artifacts)):
        raise StandardResultsError("REUSED_FOCUSED_CODEX_REVIEW_EVIDENCE")
    for current, following in zip(codex, codex[1:], strict=False):
        if (current[1] is not None and following[1] is not None and current[1] >= following[1]) or (
            current[3] is not None and following[1] is not None and current[3] >= following[1]
        ):
            raise StandardResultsError("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_EVIDENCE")
    technical_rows = [entry.get("result") == "TECHNICAL_FAILURE" for entry in entries]
    if any(technical_rows) != all(technical_rows):
        raise StandardResultsError("INCONSISTENT_SHARED_TECHNICAL_FAILURE")
    technical = all(technical_rows)
    if technical and len({tuple(entry["technical_errors"]) for entry in entries}) != 1:
        raise StandardResultsError("INCONSISTENT_SHARED_TECHNICAL_FAILURE")
    _validate_quality_artifact(value.get("quality_artifact"), technical)
    return value


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise StandardResultsError("MISSING_STANDARD_RESULTS") from error


def _identity(arguments: argparse.Namespace) -> RunIdentity:
    return RunIdentity(
        arguments.repository,
        arguments.repository_id,
        arguments.base_sha,
        arguments.head_sha,
        arguments.workflow_sha,
        arguments.run_id,
        arguments.run_attempt,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-standard-result-enforcer")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--input", required=True)
    parser.add_argument("--standard", required=True, type=int, choices=range(1, 9))
    return parser


def main(argv: list[str] | None = None) -> int:
    """Enforce one entry from an exact-identity Standard results artifact."""
    arguments = _parser().parse_args(argv)
    try:
        payload = validate_payload(_read_json(Path(arguments.input)), _identity(arguments))
        entry = payload["entries"][arguments.standard - 1]
    except (StandardResultsError, IndexError) as error:
        print(getattr(error, "code", "MALFORMED_STANDARD_RESULTS"))
        return 2
    print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCK": 1, "TECHNICAL_FAILURE": 2}[entry["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
