"""Produce one canonical Standard-results artifact from four independent sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from supportability_gate import standard_results

GITHUB_OUTCOMES = ("success", "failure", "cancelled", "skipped")
SOURCE_SPECS = (
    (
        "complexity",
        "complexity_result",
        "MISSING_COMPLEXITY_RESULT",
        "MALFORMED_COMPLEXITY_RESULT",
    ),
    (
        "characterization",
        "characterization_result",
        "MISSING_CHARACTERIZATION_RESULT",
        "MALFORMED_CHARACTERIZATION_RESULT",
    ),
    (
        "refactor",
        "refactor_result",
        "MISSING_REFACTOR_RESULT",
        "MALFORMED_REFACTOR_RESULT",
    ),
    (
        "quality_provenance",
        "quality_provenance",
        "MISSING_QUALITY_PROVENANCE",
        "MALFORMED_QUALITY_PROVENANCE",
    ),
)


class _DuplicateJsonKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise _DuplicateJsonKeyError
    return value


def _read_json(path: Path, missing: str, malformed: str) -> tuple[dict[str, Any], str | None]:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return {}, missing
    except OSError:
        return {}, malformed
    try:
        value: object = json.loads(content, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError):
        return {}, malformed
    if not isinstance(value, dict):
        return {}, malformed
    return value, None


def _load_sources(
    arguments: argparse.Namespace,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    sources: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for source, path_name, missing, malformed in SOURCE_SPECS:
        value, error = _read_json(Path(getattr(arguments, path_name)), missing, malformed)
        sources[source] = value
        if error is not None:
            errors[source] = error
    return sources, errors


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


def _outcomes(arguments: argparse.Namespace) -> dict[str, str]:
    return {
        "complexity": arguments.complexity_outcome,
        "characterization": arguments.characterization_outcome,
        "install": arguments.install_outcome,
        "refactor": arguments.refactor_outcome,
        "quality": arguments.quality_outcome,
    }


def _compose(arguments: argparse.Namespace) -> dict[str, object]:
    sources, errors = _load_sources(arguments)
    if arguments.install_outcome != "success":
        errors = {"gate_install": "GATE_INSTALL_FAILURE"}
    return standard_results.compose_results(
        sources["complexity"],
        sources["characterization"],
        sources["refactor"],
        sources["quality_provenance"],
        _identity(arguments),
        expected_quality_artifact={
            "capture_sha256": arguments.expected_quality_capture_sha256,
            "digest": arguments.expected_quality_artifact_digest,
            "id": arguments.expected_quality_artifact_id,
        },
        source_outcomes=_outcomes(arguments),
        source_errors=errors,
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
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
    parser.add_argument("--complexity-result", required=True)
    parser.add_argument("--characterization-result", required=True)
    parser.add_argument("--refactor-result", required=True)
    parser.add_argument("--quality-provenance", required=True)
    parser.add_argument("--expected-quality-artifact-id", required=True)
    parser.add_argument("--expected-quality-artifact-digest", required=True)
    parser.add_argument("--expected-quality-capture-sha256", required=True)
    parser.add_argument("--complexity-outcome", required=True, choices=GITHUB_OUTCOMES)
    parser.add_argument("--characterization-outcome", required=True, choices=GITHUB_OUTCOMES)
    parser.add_argument("--install-outcome", required=True, choices=GITHUB_OUTCOMES)
    parser.add_argument("--refactor-outcome", required=True, choices=GITHUB_OUTCOMES)
    parser.add_argument("--quality-outcome", required=True, choices=GITHUB_OUTCOMES)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write authoritative results even when an individual source is unavailable."""
    arguments = _parser().parse_args(argv)
    payload = _compose(arguments)
    _write_json(Path(arguments.output), payload)
    print(str(standard_results.review_required(payload)).lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
