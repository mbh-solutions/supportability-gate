"""Enforce one entry from an authoritative eight-Standard artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from supportability_gate import standard_results


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise standard_results.StandardResultsError("MISSING_STANDARD_RESULTS") from error


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


def main(argv: list[str] | None = None) -> int:
    """Validate the artifact and enforce one exact Standard entry."""
    arguments = _parser().parse_args(argv)
    try:
        payload = standard_results.validate_payload(
            _read_json(Path(arguments.input)), _identity(arguments)
        )
        entry = payload["entries"][arguments.standard - 1]
    except (standard_results.StandardResultsError, IndexError) as error:
        print(getattr(error, "code", "MALFORMED_STANDARD_RESULTS"))
        return 2
    print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCK": 1, "TECHNICAL_FAILURE": 2}[entry["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
