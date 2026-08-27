"""Validate fixed structured review evidence without executing target code."""

from __future__ import annotations

import tomllib
from contextlib import suppress
from typing import Any

REVIEW_EVIDENCE_PATH = ".supportability-review.toml"
HANDOFF_SENTINEL = "DERIVED_FROM_AUTHENTICATED_EVIDENCE"
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
_SECTION_EXTRA_FIELDS = {"separation_of_concerns": ("boundaries",)}
_MODULE_BOUNDARY_FIELDS = {"basis", "justification", "owner_path", "path"}
_SEPARATION_BOUNDARY_FIELDS = {"after", "before", "kind", "path", "symbol"}
_GATE_TWO_INDEPENDENT_SECTIONS = {
    *_TEXT_FIELDS,
    *_LIST_FIELDS,
    "module_boundaries",
} - {"separation_of_concerns"}

ReviewEvidence = dict[str, object]


class ReviewEvidenceError(ValueError):
    """One deterministic structured-review evidence defect."""

    def __init__(self, kind: str, location: str) -> None:
        super().__init__(location)
        self.kind = kind
        self.location = location


def _error_block(error: ReviewEvidenceError) -> str:
    prefix = (
        error.kind if error.kind == "UNSUPPORTED_HANDOFF_CLAIM" else f"{error.kind}_REVIEW_EVIDENCE"
    )
    return f"{prefix}:{error.location}"


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


def _validate_module_boundaries(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ReviewEvidenceError("MALFORMED", "module_boundaries")
    for index, item in enumerate(value):
        location = f"module_boundaries[{index}]"
        if not isinstance(item, dict):
            raise ReviewEvidenceError("MALFORMED", location)
        _require_keys(item, _MODULE_BOUNDARY_FIELDS, location)
        for field in sorted(_MODULE_BOUNDARY_FIELDS):
            _validate_text(item[field], f"{location}.{field}")
        if item["basis"] not in {"domain", "responsibility"}:
            raise ReviewEvidenceError("MALFORMED", f"{location}.basis")
    paths = [item["path"] for item in value]
    if len(paths) != len(set(paths)):
        raise ReviewEvidenceError("MALFORMED", "module_boundaries.path")
    return value


def _validate_separation_boundaries(
    value: object, expected: tuple[tuple[str, str, str], ...] | None
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ReviewEvidenceError("MALFORMED", "separation_of_concerns.boundaries")
    identities: set[tuple[str, str, str]] = set()
    for index, item in enumerate(value):
        location = f"separation_of_concerns.boundaries[{index}]"
        if not isinstance(item, dict):
            raise ReviewEvidenceError("MALFORMED", location)
        _require_keys(item, _SEPARATION_BOUNDARY_FIELDS, location)
        for field in sorted(_SEPARATION_BOUNDARY_FIELDS):
            _validate_text(item[field], f"{location}.{field}")
        if item["kind"] not in {"function", "component", "module"}:
            raise ReviewEvidenceError("MALFORMED", f"{location}.kind")
        identity = (item["path"], item["kind"], item["symbol"])
        if identity in identities:
            raise ReviewEvidenceError("MALFORMED", "separation_of_concerns.boundaries")
        identities.add(identity)
    if expected is not None and identities != set(expected):
        raise ReviewEvidenceError("INSUFFICIENT", "separation_of_concerns.boundaries")
    return value


def _validate_handoff(section: dict[str, Any]) -> None:
    if section["summary"] != HANDOFF_SENTINEL:
        raise ReviewEvidenceError("UNSUPPORTED_HANDOFF_CLAIM", "review_handoff.summary")
    if section["remaining_risks"] != [HANDOFF_SENTINEL]:
        raise ReviewEvidenceError("UNSUPPORTED_HANDOFF_CLAIM", "review_handoff.remaining_risks")


def _validate_section(
    data: dict[str, Any],
    name: str,
    expected_boundaries: tuple[tuple[str, str, str], ...] | None,
) -> None:
    section = _section(data, name)
    text_fields = _TEXT_FIELDS.get(name, ())
    list_fields = _LIST_FIELDS.get(name, ())
    fields = {*text_fields, *list_fields, *_SECTION_EXTRA_FIELDS.get(name, ())}
    _require_keys(section, fields, name)
    for field in text_fields:
        _validate_text(section[field], f"{name}.{field}")
    for field in list_fields:
        _validate_text_list(section[field], f"{name}.{field}")
    if name == "review_handoff":
        _validate_handoff(section)
    if name == "separation_of_concerns":
        _validate_separation_boundaries(section["boundaries"], expected_boundaries)


def parse_review_evidence(
    content: bytes, expected_boundaries: tuple[tuple[str, str, str], ...] | None
) -> ReviewEvidence:
    """Parse the only supported structured-review evidence schema."""
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ReviewEvidenceError("MALFORMED", "document") from error
    expected_sections = set(_TEXT_FIELDS) | set(_LIST_FIELDS)
    required = {"schema_version", *expected_sections}
    missing = sorted(required - set(data))
    unknown = sorted(set(data) - required - {"module_boundaries"})
    if missing:
        raise ReviewEvidenceError("MISSING", f"review_evidence.{missing[0]}")
    if unknown:
        raise ReviewEvidenceError("MALFORMED", f"review_evidence.{unknown[0]}")
    if data["schema_version"] != "1.0":
        raise ReviewEvidenceError("MALFORMED", "schema_version")
    for name in sorted(expected_sections):
        _validate_section(data, name, expected_boundaries)
    data["module_boundaries"] = _validate_module_boundaries(data.get("module_boundaries", []))
    return data


def evaluate_review_evidence(
    content: bytes | None,
    expected_boundaries: tuple[tuple[str, str, str], ...] | None,
) -> tuple[ReviewEvidence | None, tuple[str, ...]]:
    """Return normalized evidence or deterministic blocking reasons."""
    if content is None:
        return None, ("MISSING_REVIEW_EVIDENCE:document",)
    gate_two_block: str | None = None
    gate_two_review: ReviewEvidence | None = None
    try:
        data = tomllib.loads(content.decode("utf-8"))
        section = _section(data, "separation_of_concerns")
        fields = {*_TEXT_FIELDS["separation_of_concerns"], "boundaries"}
        _require_keys(section, fields, "separation_of_concerns")
        for field in _TEXT_FIELDS["separation_of_concerns"]:
            _validate_text(section[field], f"separation_of_concerns.{field}")
        _validate_separation_boundaries(section["boundaries"], expected_boundaries)
        gate_two_review = {"separation_of_concerns": section}
        with suppress(ReviewEvidenceError):
            gate_two_review["module_boundaries"] = _validate_module_boundaries(
                data.get("module_boundaries", [])
            )
    except (KeyError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        pass
    except ReviewEvidenceError as error:
        gate_two_block = _error_block(error)
    try:
        return parse_review_evidence(content, expected_boundaries), ()
    except ReviewEvidenceError as error:
        block = _error_block(error)
        location = error.location.removeprefix("review_evidence.")
        root = location.partition(".")[0].partition("[")[0]
        review = gate_two_review if root in _GATE_TWO_INDEPENDENT_SECTIONS else None
        if gate_two_block is not None and gate_two_block != block:
            return review, (block, gate_two_block)
        return review, (block,)
