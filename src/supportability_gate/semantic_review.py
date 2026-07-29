"""Validate semantic-review responses against exact-head evidence."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from supportability_gate.semantic_contract import (
    MODEL,
    RUBRIC_VERSION,
    SCHEMA_VERSION,
    SHA_PATTERN,
    STANDARD_SHA256,
    BoundaryEvidence,
    EvidencePacket,
    SemanticReviewError,
    SemanticVerdict,
    result_schema,
)

SourceIndex = tuple[int, dict[int, str], frozenset[tuple[int, int, str, str]]]


def _output_text(response: dict[str, Any]) -> str:
    if response.get("status") != "completed" or response.get("error") is not None:
        raise SemanticReviewError("INCOMPLETE_RESPONSE")
    output = response.get("output")
    if not isinstance(output, list) or any(not isinstance(item, dict) for item in output):
        raise SemanticReviewError("MALFORMED_RESPONSE")
    if any(item.get("type") not in {"reasoning", "message"} for item in output):
        raise SemanticReviewError("TOOL_OR_MALFORMED_OUTPUT")
    messages = [item for item in output if item.get("type") == "message"]
    if len(messages) != 1:
        raise SemanticReviewError("MALFORMED_RESPONSE")
    content = messages[0].get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise SemanticReviewError("MALFORMED_RESPONSE")
    if content[0].get("type") == "refusal":
        raise SemanticReviewError("REFUSAL")
    if content[0].get("type") != "output_text" or not isinstance(content[0].get("text"), str):
        raise SemanticReviewError("SCHEMA_ESCAPE")
    return str(content[0]["text"])


def _findings(data: dict[str, Any]) -> tuple[str, ...]:
    findings = data.get("findings")
    if not isinstance(findings, list) or any(
        not isinstance(item, str) or not item for item in findings
    ):
        raise SemanticReviewError("MALFORMED_SCHEMA")
    return tuple(findings)


def _trusted_boundaries(value: object, line_count: int) -> frozenset[tuple[int, int, str, str]]:
    if not isinstance(value, list) or not value:
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    boundaries: set[tuple[int, int, str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"start_line", "end_line", "kind", "name"}:
            raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
        start, end, kind, name = item["start_line"], item["end_line"], item["kind"], item["name"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end <= line_count
            or kind not in {"function", "module", "component"}
            or not isinstance(name, str)
            or not name
        ):
            raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
        boundaries.add((start, end, kind, name))
    if len(boundaries) != len(value):
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    return frozenset(boundaries)


def _indexed_source(source: object) -> tuple[str, SourceIndex]:
    keys = {"blob_sha", "boundaries", "line_count", "lines", "path"}
    if not isinstance(source, dict) or set(source) != keys:
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    path, blob_sha = source["path"], source["blob_sha"]
    line_count, source_lines = source["line_count"], source["lines"]
    if not isinstance(path, str) or not isinstance(blob_sha, str):
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or parsed.as_posix() != path:
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    if (
        not SHA_PATTERN.fullmatch(blob_sha)
        or type(line_count) is not int
        or line_count < 1
        or not isinstance(source_lines, list)
        or not source_lines
    ):
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    lines: dict[int, str] = {}
    for item in source_lines:
        if not isinstance(item, dict) or set(item) != {"line", "text"}:
            raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
        number, text = item["line"], item["text"]
        if type(number) is not int or not 1 <= number <= line_count or not isinstance(text, str):
            raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
        if number in lines:
            raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
        lines[number] = text
    if tuple(lines) != tuple(sorted(lines)):
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    return path, (line_count, lines, _trusted_boundaries(source["boundaries"], line_count))


def _source_index(packet: EvidencePacket) -> dict[str, SourceIndex]:
    sources = packet.evidence.get("reviewed_sources")
    if not isinstance(sources, list):
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    indexed = dict(_indexed_source(source) for source in sources)
    if len(indexed) != len(sources):
        raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
    return dict(sorted(indexed.items()))


def _boundary(item: object, sources: dict[str, SourceIndex]) -> BoundaryEvidence:
    keys = {"path", "start_line", "end_line", "kind", "name", "owns", "does_not_own"}
    if not isinstance(item, dict) or set(item) != keys:
        raise SemanticReviewError("MALFORMED_SCHEMA")
    if type(item["start_line"]) is not int or type(item["end_line"]) is not int:
        raise SemanticReviewError("MALFORMED_SCHEMA")
    text_keys = keys - {"start_line", "end_line"}
    if any(not isinstance(item[key], str) or not item[key].strip() for key in text_keys):
        raise SemanticReviewError("MALFORMED_SCHEMA")
    path = item["path"]
    if path not in sources:
        raise SemanticReviewError("EVIDENCE_OUTSIDE_HEAD")
    start_line, end_line = item["start_line"], item["end_line"]
    line_count, lines, trusted = sources[path]
    cited_numbers = range(start_line, end_line + 1)
    if not 1 <= start_line <= end_line <= line_count or any(
        number not in lines for number in cited_numbers
    ):
        raise SemanticReviewError("EVIDENCE_OUTSIDE_HEAD")
    kind, name = item["kind"], item["name"]
    if kind not in {"function", "module", "component"}:
        raise SemanticReviewError("MALFORMED_SCHEMA")
    if path.endswith(".py") and kind == "component":
        raise SemanticReviewError("MALFORMED_SCHEMA")
    if (start_line, end_line, kind, name) not in trusted:
        raise SemanticReviewError("UNSUPPORTED_OWNERSHIP_CLAIM")
    if item["owns"] == item["does_not_own"]:
        raise SemanticReviewError("VAGUE_BOUNDARY")
    return BoundaryEvidence(
        path=path,
        start_line=start_line,
        end_line=end_line,
        kind=kind,
        name=name,
        owns=item["owns"],
        does_not_own=item["does_not_own"],
    )


def _boundary_evidence(
    packet: EvidencePacket, data: dict[str, Any], findings: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[BoundaryEvidence, ...]]:
    sources = _source_index(packet)
    reviewed = data.get("reviewed_paths")
    if not isinstance(reviewed, list) or any(not isinstance(path, str) for path in reviewed):
        raise SemanticReviewError("MALFORMED_SCHEMA")
    if any(path not in sources for path in reviewed):
        raise SemanticReviewError("EVIDENCE_OUTSIDE_HEAD")
    expected_paths = tuple(sources)
    if tuple(reviewed) != expected_paths:
        raise SemanticReviewError("MISSING_REVIEWED_PATHS")
    raw_boundaries = data.get("boundaries")
    if not isinstance(raw_boundaries, list) or len(raw_boundaries) > 100:
        raise SemanticReviewError("MALFORMED_SCHEMA")
    boundaries = tuple(
        sorted(
            (_boundary(item, sources) for item in raw_boundaries),
            key=lambda item: (item.path, item.start_line, item.end_line, item.kind, item.name),
        )
    )
    identities = {
        (item.path, item.start_line, item.end_line, item.kind, item.name) for item in boundaries
    }
    if len(identities) != len(boundaries):
        raise SemanticReviewError("MALFORMED_SCHEMA")
    expected_boundaries = {
        (path, start, end, kind, name)
        for path, (_, _, trusted) in sources.items()
        for start, end, kind, name in trusted
    }
    if identities != expected_boundaries:
        raise SemanticReviewError("MISSING_BOUNDARY_EVIDENCE")
    prefixes = tuple(f"{item.path}:{item.start_line}-{item.end_line}" for item in boundaries)
    if any(not finding.startswith(prefixes) for finding in findings):
        raise SemanticReviewError("UNSUPPORTED_FINDING")
    return expected_paths, boundaries


def _validate_bindings(data: dict[str, Any], bindings: dict[str, object]) -> None:
    if type(data.get("app_id")) is not int:
        raise SemanticReviewError("MALFORMED_SCHEMA")
    if any(type(data.get(key)) is not str for key in bindings if key != "app_id"):
        raise SemanticReviewError("MALFORMED_SCHEMA")
    if any(data.get(key) != value for key, value in bindings.items()):
        raise SemanticReviewError("EVIDENCE_BINDING_MISMATCH")


def _response_data(response: object) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise SemanticReviewError("MALFORMED_RESPONSE")
    if response.get("model") != MODEL:
        raise SemanticReviewError("MODEL_DRIFT")
    try:
        data = json.loads(_output_text(response))
    except json.JSONDecodeError as error:
        raise SemanticReviewError("MALFORMED_SCHEMA") from error
    expected = set(result_schema()["properties"])
    if not isinstance(data, dict) or set(data) != expected:
        raise SemanticReviewError("MALFORMED_SCHEMA")
    return data


def _trusted_verdict(packet: EvidencePacket, data: dict[str, Any]) -> SemanticVerdict:
    findings = _findings(data)
    reviewed_paths, boundaries = _boundary_evidence(packet, data, findings)
    bindings = {
        "app_id": packet.app_id,
        "repository": packet.repository,
        "base_sha": packet.base_sha,
        "head_sha": packet.head_sha,
        "evidence_sha256": packet.sha256,
        "rubric_version": RUBRIC_VERSION,
        "schema_version": SCHEMA_VERSION,
        "standard_sha256": STANDARD_SHA256,
    }
    _validate_bindings(data, bindings)
    verdict = data.get("verdict")
    if verdict == "UNCERTAIN":
        raise SemanticReviewError("UNCERTAIN_VERDICT")
    if verdict not in {"PASS", "BLOCK"}:
        raise SemanticReviewError("MALFORMED_SCHEMA")
    if (verdict == "PASS") != (not findings):
        raise SemanticReviewError("CONFLICTING_VERDICT")
    return SemanticVerdict(
        verdict=verdict,
        findings=findings,
        app_id=packet.app_id,
        repository=packet.repository,
        base_sha=packet.base_sha,
        head_sha=packet.head_sha,
        evidence_sha256=packet.sha256,
        rubric_version=RUBRIC_VERSION,
        schema_version=SCHEMA_VERSION,
        standard_sha256=STANDARD_SHA256,
        reviewed_paths=reviewed_paths,
        boundaries=boundaries,
    )


def parse_response(packet: EvidencePacket, response: object) -> SemanticVerdict:
    """Orchestrate response parsing and exact-binding verdict validation."""
    return _trusted_verdict(packet, _response_data(response))
