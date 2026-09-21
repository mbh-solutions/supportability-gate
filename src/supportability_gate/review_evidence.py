"""Validate fixed structured review evidence without executing target code."""

from __future__ import annotations

import tomllib
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


def _record(blocks: list[str], error: ReviewEvidenceError) -> None:
    blocks.append(_error_block(error))


def _validate_root(data: dict[str, Any], review: ReviewEvidence, blocks: list[str]) -> None:
    expected = {"schema_version", *_TEXT_FIELDS, *_LIST_FIELDS, "module_boundaries"}
    for name in sorted(set(data) - expected):
        _record(blocks, ReviewEvidenceError("MALFORMED", f"review_evidence.{name}"))
    if "schema_version" not in data:
        _record(blocks, ReviewEvidenceError("MISSING", "review_evidence.schema_version"))
    elif data["schema_version"] != "1.0":
        _record(blocks, ReviewEvidenceError("MALFORMED", "schema_version"))
    else:
        review["schema_version"] = "1.0"


def _section_keys(name: str) -> set[str]:
    return {
        *_TEXT_FIELDS.get(name, ()),
        *_LIST_FIELDS.get(name, ()),
        *_SECTION_EXTRA_FIELDS.get(name, ()),
    }


def _collect_text_field(
    section: dict[str, Any], normalized: dict[str, object], name: str, field: str, blocks: list[str]
) -> None:
    try:
        _validate_text(section[field], f"{name}.{field}")
    except ReviewEvidenceError as error:
        _record(blocks, error)
    else:
        normalized[field] = section[field]


def _collect_list_field(
    section: dict[str, Any], normalized: dict[str, object], name: str, field: str, blocks: list[str]
) -> None:
    try:
        _validate_text_list(section[field], f"{name}.{field}")
    except ReviewEvidenceError as error:
        _record(blocks, error)
    else:
        normalized[field] = section[field]


def _collect_handoff_claims(normalized: dict[str, object], blocks: list[str]) -> None:
    expected = {
        "summary": HANDOFF_SENTINEL,
        "remaining_risks": [HANDOFF_SENTINEL],
    }
    for field, value in expected.items():
        if field in normalized and normalized[field] != value:
            _record(
                blocks,
                ReviewEvidenceError("UNSUPPORTED_HANDOFF_CLAIM", f"review_handoff.{field}"),
            )


def _collect_separation_boundaries(
    section: dict[str, Any],
    normalized: dict[str, object],
    expected: tuple[tuple[str, str, str], ...] | None,
    blocks: list[str],
) -> None:
    try:
        normalized["boundaries"] = _validate_separation_boundaries(section["boundaries"], expected)
    except ReviewEvidenceError as error:
        _record(blocks, error)


def _collect_declared_fields(
    section: dict[str, Any], name: str, normalized: dict[str, object], blocks: list[str]
) -> None:
    for field in _TEXT_FIELDS.get(name, ()):
        if field in section:
            _collect_text_field(section, normalized, name, field, blocks)
    for field in _LIST_FIELDS.get(name, ()):
        if field in section:
            _collect_list_field(section, normalized, name, field, blocks)


def _collect_special_fields(
    section: dict[str, Any],
    name: str,
    normalized: dict[str, object],
    expected_boundaries: tuple[tuple[str, str, str], ...] | None,
    blocks: list[str],
) -> None:
    if name == "separation_of_concerns" and "boundaries" in section:
        _collect_separation_boundaries(section, normalized, expected_boundaries, blocks)
    if name == "review_handoff":
        _collect_handoff_claims(normalized, blocks)


def _collect_section(
    data: dict[str, Any],
    name: str,
    expected_boundaries: tuple[tuple[str, str, str], ...] | None,
    review: ReviewEvidence,
    blocks: list[str],
) -> None:
    if name not in data:
        _record(blocks, ReviewEvidenceError("MISSING", f"review_evidence.{name}"))
        return
    section = data[name]
    if not isinstance(section, dict):
        _record(blocks, ReviewEvidenceError("MALFORMED", name))
        return
    expected = _section_keys(name)
    for field in sorted(expected - set(section)):
        _record(blocks, ReviewEvidenceError("MISSING", f"{name}.{field}"))
    for field in sorted(set(section) - expected):
        _record(blocks, ReviewEvidenceError("MALFORMED", f"{name}.{field}"))
    normalized: dict[str, object] = {}
    _collect_declared_fields(section, name, normalized, blocks)
    _collect_special_fields(section, name, normalized, expected_boundaries, blocks)
    if normalized:
        review[name] = normalized


def _collect_module_boundaries(
    data: dict[str, Any], review: ReviewEvidence, blocks: list[str]
) -> None:
    try:
        review["module_boundaries"] = _validate_module_boundaries(data.get("module_boundaries", []))
    except ReviewEvidenceError as error:
        _record(blocks, error)


def _evaluate_document(
    content: bytes, expected_boundaries: tuple[tuple[str, str, str], ...] | None
) -> tuple[ReviewEvidence | None, tuple[str, ...]]:
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, ("MALFORMED_REVIEW_EVIDENCE:document",)
    review: ReviewEvidence = {}
    blocks: list[str] = []
    _validate_root(data, review, blocks)
    for name in sorted(set(_TEXT_FIELDS) | set(_LIST_FIELDS)):
        _collect_section(data, name, expected_boundaries, review, blocks)
    _collect_module_boundaries(data, review, blocks)
    return review, tuple(sorted(set(blocks)))


def _exception_from_block(block: str) -> ReviewEvidenceError:
    if block.startswith("UNSUPPORTED_HANDOFF_CLAIM:"):
        return ReviewEvidenceError("UNSUPPORTED_HANDOFF_CLAIM", block.partition(":")[2])
    kind, _, location = block.partition("_REVIEW_EVIDENCE:")
    return ReviewEvidenceError(kind, location)


def parse_review_evidence(
    content: bytes, expected_boundaries: tuple[tuple[str, str, str], ...] | None
) -> ReviewEvidence:
    """Parse the only supported structured-review evidence schema."""
    review, blocks = _evaluate_document(content, expected_boundaries)
    if blocks:
        raise _exception_from_block(blocks[0])
    assert review is not None
    return review


def evaluate_review_evidence(
    content: bytes | None,
    expected_boundaries: tuple[tuple[str, str, str], ...] | None,
) -> tuple[ReviewEvidence | None, tuple[str, ...]]:
    """Preserve the deployed single-defect compatibility contract."""
    if content is None:
        return None, ("MISSING_REVIEW_EVIDENCE:document",)
    review, blocks = _evaluate_document(content, expected_boundaries)
    if not blocks:
        return review, ()
    gate_two = tuple(
        block for block in blocks if block.partition(":")[2].startswith("separation_of_concerns")
    )
    compatible: ReviewEvidence | None = None
    if review is not None and not gate_two and "separation_of_concerns" in review:
        compatible = {"separation_of_concerns": review["separation_of_concerns"]}
        if "module_boundaries" in review:
            compatible["module_boundaries"] = review["module_boundaries"]
    first = blocks[0]
    retained = (first,) if not gate_two or first in gate_two else (first, gate_two[0])
    return compatible, retained


def evaluate_review_sections(
    content: bytes | None,
    expected_boundaries: tuple[tuple[str, str, str], ...] | None,
) -> tuple[ReviewEvidence | None, tuple[str, ...]]:
    """Return every valid owned section and every deterministic defect."""
    if content is None:
        return None, ("MISSING_REVIEW_EVIDENCE:document",)
    return _evaluate_document(content, expected_boundaries)
