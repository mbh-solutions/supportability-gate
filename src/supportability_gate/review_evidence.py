"""Validate fixed structured review evidence without executing target code."""

from __future__ import annotations

import tomllib
from typing import Any

REVIEW_EVIDENCE_PATH = ".supportability-review.toml"
_TEXT_FIELDS = {
    "behavior": ("intended_behavior", "proof"),
    "characterization": ("captured_behavior", "proof"),
    "separation_of_concerns": ("before", "after"),
    "architecture": ("dependency_direction",),
    "responsibility_boundary": ("path", "owns", "does_not_own"),
    "incremental_refactor": ("target", "completed_step"),
    "review_handoff": ("summary",),
    "human_review": ("naming", "cohesion", "intended_behavior", "reviewability"),
}
_LIST_FIELDS = {
    "architecture": ("reviewed_paths",),
    "review_handoff": ("remaining_risks",),
}

ReviewEvidence = dict[str, object]


class ReviewEvidenceError(ValueError):
    """One deterministic structured-review evidence defect."""

    def __init__(self, kind: str, location: str) -> None:
        super().__init__(location)
        self.kind = kind
        self.location = location


def _require_keys(data: dict[str, Any], expected: set[str], location: str) -> None:
    missing = sorted(expected - set(data))
    if missing:
        raise ReviewEvidenceError("MISSING", f"{location}.{missing[0]}")
    unknown = sorted(set(data) - expected)
    if unknown:
        raise ReviewEvidenceError("MALFORMED", f"{location}.{unknown[0]}")


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data[name]
    if not isinstance(value, dict):
        raise ReviewEvidenceError("MALFORMED", name)
    return value


def _validate_text(value: object, location: str) -> None:
    if not isinstance(value, str):
        raise ReviewEvidenceError("MALFORMED", location)
    if not value.strip():
        raise ReviewEvidenceError("INSUFFICIENT", location)


def _validate_text_list(value: object, location: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReviewEvidenceError("MALFORMED", location)
    if not value or any(not item.strip() for item in value):
        raise ReviewEvidenceError("INSUFFICIENT", location)


def parse_review_evidence(content: bytes) -> ReviewEvidence:
    """Parse the only supported structured-review evidence schema."""
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ReviewEvidenceError("MALFORMED", "document") from error
    expected_sections = set(_TEXT_FIELDS) | set(_LIST_FIELDS)
    _require_keys(data, {"schema_version", *expected_sections}, "review_evidence")
    if data["schema_version"] != "1.0":
        raise ReviewEvidenceError("MALFORMED", "schema_version")
    for name in sorted(expected_sections):
        section = _section(data, name)
        text_fields = _TEXT_FIELDS.get(name, ())
        list_fields = _LIST_FIELDS.get(name, ())
        _require_keys(section, {*text_fields, *list_fields}, name)
        for field in text_fields:
            _validate_text(section[field], f"{name}.{field}")
        for field in list_fields:
            _validate_text_list(section[field], f"{name}.{field}")
    return data


def evaluate_review_evidence(
    content: bytes | None,
) -> tuple[ReviewEvidence | None, tuple[str, ...]]:
    """Return normalized evidence or one deterministic blocking reason."""
    if content is None:
        return None, ("MISSING_REVIEW_EVIDENCE:document",)
    try:
        return parse_review_evidence(content), ()
    except ReviewEvidenceError as error:
        return None, (f"{error.kind}_REVIEW_EVIDENCE:{error.location}",)
