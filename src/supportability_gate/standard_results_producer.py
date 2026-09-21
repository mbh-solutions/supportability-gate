"""Produce one canonical Standard-results artifact from four independent sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
        "complexity",
    ),
    (
        "characterization",
        "characterization_result",
        "MISSING_CHARACTERIZATION_RESULT",
        "MALFORMED_CHARACTERIZATION_RESULT",
        "characterization",
    ),
    (
        "refactor",
        "refactor_result",
        "MISSING_REFACTOR_RESULT",
        "MALFORMED_REFACTOR_RESULT",
        "refactor",
    ),
    (
        "quality_provenance",
        "quality_provenance",
        "MISSING_QUALITY_PROVENANCE",
        "MALFORMED_QUALITY_PROVENANCE",
        "quality",
    ),
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DIAGNOSTIC_CODE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_DIAGNOSTIC_STAGES = {
    "complexity": {"complexity"},
    "characterization": {"characterization", "characterization-base", "characterization-head"},
    "refactor": {"refactor"},
    "quality": {"quality", "quality-profile"},
    "install": {"install"},
}
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_CREDENTIAL = re.compile(
    r"(?i)\b(token|password|secret|authorization)\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s]+"
)
_ORIGINAL_CODE = re.compile(r"(?m)^\s*([A-Z][A-Z0-9_]{2,})\b")
_MAX_DIAGNOSTIC_BYTES = 8192


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


def _diagnostic_identity(value: object, arguments: argparse.Namespace, stage: str) -> bool:
    if not isinstance(value, dict):
        return False
    expected_keys = {
        "base_sha",
        "head_sha",
        "job",
        "repository",
        "repository_id",
        "run_attempt",
        "run_id",
        "workflow_sha",
    }
    actual = tuple(
        value.get(name)
        for name in (
            "repository",
            "repository_id",
            "base_sha",
            "head_sha",
            "workflow_sha",
            "run_id",
            "run_attempt",
        )
    )
    expected = (
        arguments.repository,
        str(arguments.repository_id),
        arguments.base_sha,
        arguments.head_sha,
        arguments.workflow_sha,
        str(arguments.run_id),
        str(arguments.run_attempt),
    )
    return set(value) == expected_keys and actual == expected and value["job"] == stage


def _diagnostic_logs(value: object, directory: Path) -> bool:
    if not isinstance(value, list) or len(value) != 2:
        return False
    streams = []
    for row in value:
        if not isinstance(row, dict) or set(row) != {
            "path",
            "retained_bytes",
            "sha256",
            "stream",
            "truncated",
        }:
            return False
        path = row["path"]
        if not isinstance(path, str) or Path(path).name != path:
            return False
        try:
            content = (directory / path).read_bytes()
        except OSError:
            return False
        if (
            row["stream"] not in {"stdout", "stderr"}
            or type(row["retained_bytes"]) is not int
            or row["retained_bytes"] != len(content)
            or row["retained_bytes"] > 8192
            or type(row["truncated"]) is not bool
            or row["sha256"] != hashlib.sha256(content).hexdigest()
        ):
            return False
        streams.append(row["stream"])
    return sorted(streams) == ["stderr", "stdout"]


def _read_diagnostic(path: Path, source: str, arguments: argparse.Namespace) -> tuple[str, str]:
    value, error = _read_json(path, "DIAGNOSTIC_UNAVAILABLE", "MALFORMED_DIAGNOSTIC")
    if error is not None:
        return source, error
    if set(value) != {"adapter", "code", "identity", "logs", "schema_version", "stage"}:
        return source, "MALFORMED_DIAGNOSTIC"
    stage = value["stage"]
    code = value["code"]
    if (
        value["schema_version"] != "stage-diagnostic.v1"
        or not isinstance(stage, str)
        or stage not in _DIAGNOSTIC_STAGES[source]
        or not isinstance(code, str)
        or _DIAGNOSTIC_CODE.fullmatch(code) is None
        or value["adapter"] is not None
        and not isinstance(value["adapter"], str)
        or not _diagnostic_logs(value["logs"], path.parent)
    ):
        return source, "MALFORMED_DIAGNOSTIC"
    if not _diagnostic_identity(value["identity"], arguments, stage):
        return source, "DIAGNOSTIC_IDENTITY_MISMATCH"
    return stage, code


def _sanitize_diagnostic(content: bytes) -> tuple[bytes, bool]:
    text = content.decode("utf-8", errors="replace")
    for name in ("GITHUB_WORKSPACE", "RUNNER_TEMP"):
        if value := os.environ.get(name):
            text = text.replace(value, f"<{name.lower()}>")
    for name, value in os.environ.items():
        if (
            value
            and len(value) >= 8
            and re.search(r"TOKEN|PASSWORD|SECRET|AUTHORIZATION", name, re.I)
        ):
            text = text.replace(value, "[REDACTED]")
    text = _ANSI_ESCAPE.sub("", text)
    text = _CREDENTIAL.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = "".join(character for character in text if character in "\n\r\t" or ord(character) >= 32)
    encoded = text.encode("utf-8")
    truncated = len(encoded) > _MAX_DIAGNOSTIC_BYTES
    retained = encoded[:_MAX_DIAGNOSTIC_BYTES].decode("utf-8", errors="ignore").encode("utf-8")
    return retained, truncated


def _raw_log(arguments: argparse.Namespace, source: str, stream: str) -> bytes:
    name = f"{source.replace('-', '_')}_{stream}_log"
    path = getattr(arguments, name, None)
    if not path:
        return b""
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


def _failure_code(arguments: argparse.Namespace, source: str, outcome: str) -> str:
    value = getattr(arguments, f"{source.replace('-', '_')}_failure_code", None)
    if isinstance(value, str) and _DIAGNOSTIC_CODE.fullmatch(value):
        return value
    return outcome.upper()


def _observed_code(stdout: bytes, stderr: bytes, fallback: str) -> str:
    for content in (stdout, stderr):
        if match := _ORIGINAL_CODE.search(content.decode("utf-8", errors="replace")):
            return match.group(1)
    return fallback


def _write_stage_diagnostic(
    arguments: argparse.Namespace, source: str, outcome: str
) -> Path | None:
    stdout = _raw_log(arguments, source, "stdout")
    stderr = _raw_log(arguments, source, "stderr")
    if not stdout and not stderr:
        return None
    directory = Path(arguments.output).parent / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    logs = []
    name = f"{source}--stage"
    for stream, content in (("stdout", stdout), ("stderr", stderr)):
        retained, truncated = _sanitize_diagnostic(content)
        path = directory / f"{name}.{stream}.log"
        path.write_bytes(retained)
        logs.append(
            {
                "path": path.name,
                "retained_bytes": len(retained),
                "sha256": hashlib.sha256(retained).hexdigest(),
                "stream": stream,
                "truncated": truncated,
            }
        )
    path = directory / f"{name}.json"
    payload = {
        "adapter": None,
        "code": _observed_code(stdout, stderr, _failure_code(arguments, source, outcome)),
        "identity": {
            "base_sha": arguments.base_sha,
            "head_sha": arguments.head_sha,
            "job": source,
            "repository": arguments.repository,
            "repository_id": str(arguments.repository_id),
            "run_attempt": str(arguments.run_attempt),
            "run_id": str(arguments.run_id),
            "workflow_sha": arguments.workflow_sha,
        },
        "logs": logs,
        "schema_version": "stage-diagnostic.v1",
        "stage": source,
    }
    _write_json(path, payload)
    return path


def _stage_failure(arguments: argparse.Namespace, source: str, outcome: str) -> str:
    diagnostics = getattr(arguments, f"{source.replace('-', '_')}_diagnostic", None) or []
    if isinstance(diagnostics, str):
        diagnostics = [diagnostics]
    existing = next((Path(path) for path in diagnostics if Path(path).is_file()), None)
    if existing is None:
        existing = _write_stage_diagnostic(arguments, source, outcome)
    if existing is not None:
        stage, code = _read_diagnostic(existing, source, arguments)
    elif _failure_code(arguments, source, outcome) != outcome.upper():
        stage, code = source, _failure_code(arguments, source, outcome)
    elif diagnostics:
        stage, code = source, "DIAGNOSTIC_UNAVAILABLE"
    else:
        stage, code = source, outcome.upper()
    return f"STAGE_FAILURE:{stage}:{code}"


def _load_sources(
    arguments: argparse.Namespace, outcomes: dict[str, str]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    sources: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for source, path_name, missing, malformed, stage in SOURCE_SPECS:
        value, error = _read_json(Path(getattr(arguments, path_name)), missing, malformed)
        sources[source] = value
        if error is not None:
            outcome = outcomes[stage]
            errors[source] = (
                error if outcome == "success" else _stage_failure(arguments, stage, outcome)
            )
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


def _expected_characterization(arguments: argparse.Namespace) -> dict[str, object] | None:
    values = (
        arguments.expected_base_characterization_artifact_id,
        arguments.expected_base_characterization_artifact_digest,
        arguments.expected_base_characterization_capture_sha256,
        arguments.expected_head_characterization_artifact_id,
        arguments.expected_head_characterization_artifact_digest,
        arguments.expected_head_characterization_capture_sha256,
    )
    if all(value is None for value in values):
        return None
    return {
        "base": {"id": values[0], "digest": values[1], "capture_sha256": values[2]},
        "head": {"id": values[3], "digest": values[4], "capture_sha256": values[5]},
    }


def _compose(arguments: argparse.Namespace) -> dict[str, object]:
    outcomes = _outcomes(arguments)
    sources, errors = _load_sources(arguments, outcomes)
    if arguments.install_outcome != "success":
        errors = {"gate_install": _stage_failure(arguments, "install", arguments.install_outcome)}
    return standard_results.compose_results(
        sources["complexity"],
        sources["characterization"],
        sources["refactor"],
        sources["quality_provenance"],
        _identity(arguments),
        expected_characterization_artifacts=_expected_characterization(arguments),
        expected_quality_artifact={
            "capture_sha256": arguments.expected_quality_capture_sha256,
            "digest": arguments.expected_quality_artifact_digest,
            "id": arguments.expected_quality_artifact_id,
        },
        source_outcomes=outcomes,
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
    parser.add_argument("--complexity-diagnostic", action="append")
    parser.add_argument("--characterization-diagnostic", action="append")
    parser.add_argument("--refactor-diagnostic", action="append")
    parser.add_argument("--quality-diagnostic", action="append")
    parser.add_argument("--install-diagnostic", action="append")
    for source in ("install", "complexity", "characterization", "refactor", "quality"):
        parser.add_argument(f"--{source}-stdout-log")
        parser.add_argument(f"--{source}-stderr-log")
        parser.add_argument(f"--{source}-failure-code")
    parser.add_argument("--expected-quality-artifact-id", required=True)
    parser.add_argument("--expected-quality-artifact-digest", required=True)
    parser.add_argument("--expected-quality-capture-sha256", required=True)
    parser.add_argument("--expected-base-characterization-artifact-id")
    parser.add_argument("--expected-base-characterization-artifact-digest")
    parser.add_argument("--expected-base-characterization-capture-sha256")
    parser.add_argument("--expected-head-characterization-artifact-id")
    parser.add_argument("--expected-head-characterization-artifact-digest")
    parser.add_argument("--expected-head-characterization-capture-sha256")
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
    try:
        payload = _compose(arguments)
        _write_json(Path(arguments.output), payload)
    except standard_results.StandardResultsError as error:
        print(error.code)
        return 2
    except Exception:
        print("UNEXPECTED_STANDARD_RESULTS_FAILURE")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
