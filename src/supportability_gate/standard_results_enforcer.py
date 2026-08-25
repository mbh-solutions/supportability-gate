"""Validate and enforce one row from an exact-identity Standard-results artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from supportability_gate import standard_results


class _DuplicateJsonKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise _DuplicateJsonKeyError
    return value


def _read_json(path: Path) -> object:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise standard_results.StandardResultsError("MISSING_STANDARD_RESULTS") from error
    try:
        value: object = json.loads(content, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError) as error:
        raise standard_results.StandardResultsError("MALFORMED_STANDARD_RESULTS") from error
    return value


def _identity(arguments: argparse.Namespace) -> standard_results.RunIdentity:
    return standard_results.RunIdentity(
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


def _entry(arguments: argparse.Namespace) -> dict[str, object]:
    payload = _read_json(Path(arguments.input))
    standard_results.validate_payload(payload, _identity(arguments))
    rows = cast(dict[str, object], payload)["entries"]
    return cast(list[dict[str, object]], rows)[int(arguments.standard) - 1]


def main(argv: list[str] | None = None) -> int:
    """Print and enforce one independently owned Standard result."""
    arguments = _parser().parse_args(argv)
    try:
        entry = _entry(arguments)
    except standard_results.StandardResultsError as error:
        print(error.code)
        return 2
    print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    result = entry["result"]
    return {
        "PASS": 0,
        "NOT_APPLICABLE_SHORT_TASK": 0,
        "BLOCK": 1,
        "TECHNICAL_FAILURE": 2,
    }[cast(str, result)]


if __name__ == "__main__":
    raise SystemExit(main())
