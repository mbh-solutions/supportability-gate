"""Produce one exact-identity artifact from existing Supportability evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from supportability_gate import codex_review, focused_review, standard_results


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise standard_results.StandardResultsError(code) from error
    if not isinstance(value, dict):
        raise standard_results.StandardResultsError(code)
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    )


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


def _compose(arguments: argparse.Namespace) -> dict[str, object]:
    identity = _identity(arguments)
    try:
        complexity = _read_json(Path(arguments.complexity_result), "MISSING_COMPLEXITY_RESULT")
        characterization = _read_json(
            Path(arguments.characterization_result), "MISSING_CHARACTERIZATION_RESULT"
        )
        refactor = _read_json(Path(arguments.refactor_result), "MISSING_REFACTOR_RESULT")
        quality = _read_json(Path(arguments.quality_provenance), "MISSING_QUALITY_PROVENANCE")
    except standard_results.StandardResultsError as error:
        return standard_results.compose_results({}, {}, {}, {}, identity, (), error.code)
    token = os.environ.get("GITHUB_TOKEN")
    evidence: tuple[focused_review.FocusedReviewEvidence, ...] = ()
    codex_error: str | None = None
    if not token:
        codex_error = "GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE"
    else:
        try:
            evidence = codex_review.require_focused_completion(
                identity.repository,
                arguments.pull_number,
                identity.head_sha,
                identity.run_id,
                token,
            )
        except codex_review.CodexReviewError as error:
            codex_error = error.code
            try:
                _, evidence = codex_review.focused_completion_snapshot(
                    identity.repository,
                    arguments.pull_number,
                    identity.head_sha,
                    identity.run_id,
                    token,
                )
            except codex_review.CodexReviewError as snapshot_error:
                evidence = ()
                codex_error = snapshot_error.code
    return standard_results.compose_results(
        complexity,
        characterization,
        refactor,
        quality,
        identity,
        evidence,
        codex_error,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-standard-results-producer")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", required=True, type=int)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--pull-number", required=True, type=int)
    parser.add_argument("--complexity-result", required=True)
    parser.add_argument("--characterization-result", required=True)
    parser.add_argument("--refactor-result", required=True)
    parser.add_argument("--quality-provenance", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Compose and write one validated authoritative Standard results artifact."""
    arguments = _parser().parse_args(argv)
    payload = _compose(arguments)
    _write_json(Path(arguments.output), payload)
    print("STANDARD_RESULTS_EMITTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
