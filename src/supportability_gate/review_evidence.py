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
_MODULE_BOUNDARY_FIELDS = {"basis", "justification", "owner_path", "path"}

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


def parse_review_evidence(content: bytes) -> ReviewEvidence:
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
        section = _section(data, name)
        text_fields = _TEXT_FIELDS.get(name, ())
        list_fields = _LIST_FIELDS.get(name, ())
        _require_keys(section, {*text_fields, *list_fields}, name)
        for field in text_fields:
            _validate_text(section[field], f"{name}.{field}")
        for field in list_fields:
            _validate_text_list(section[field], f"{name}.{field}")
    data["module_boundaries"] = _validate_module_boundaries(data.get("module_boundaries", []))
    return data


def _block(kind: str, location: str) -> str:
    return f"{kind}_REVIEW_EVIDENCE:{location}"


def _value_block(value: object, location: str, *, text_list: bool) -> str | None:
    if text_list:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return _block("MALFORMED", location)
        return (
            _block("INSUFFICIENT", location)
            if not value or any(not item.strip() for item in value)
            else None
        )
    if not isinstance(value, str):
        return _block("MALFORMED", location)
    return _block("INSUFFICIENT", location) if not value.strip() else None


def _section_blocks(data: dict[str, Any], name: str) -> list[str]:
    text_fields = _TEXT_FIELDS.get(name, ())
    list_fields = _LIST_FIELDS.get(name, ())
    fields = {*text_fields, *list_fields}
    value = data.get(name)
    if value is None:
        return [_block("MISSING", f"{name}.{field}") for field in sorted(fields)]
    if not isinstance(value, dict):
        return [_block("MALFORMED", f"{name}.{field}") for field in sorted(fields)]
    blocks = [_block("MISSING", f"{name}.{field}") for field in sorted(fields - set(value))]
    blocks.extend(_block("MALFORMED", f"{name}.{field}") for field in sorted(set(value) - fields))
    for field in sorted(fields & set(value)):
        if block := _value_block(value[field], f"{name}.{field}", text_list=field in list_fields):
            blocks.append(block)
    return blocks


def _module_boundary_blocks(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [_block("MALFORMED", "module_boundaries")]
    blocks: list[str] = []
    paths: list[str] = []
    for index, item in enumerate(value):
        location = f"module_boundaries[{index}]"
        if not isinstance(item, dict):
            blocks.append(_block("MALFORMED", location))
            continue
        blocks.extend(
            _block("MISSING", f"{location}.{field}")
            for field in sorted(_MODULE_BOUNDARY_FIELDS - set(item))
        )
        blocks.extend(
            _block("MALFORMED", f"{location}.{field}")
            for field in sorted(set(item) - _MODULE_BOUNDARY_FIELDS)
        )
        for field in sorted(_MODULE_BOUNDARY_FIELDS & set(item)):
            if block := _value_block(item[field], f"{location}.{field}", text_list=False):
                blocks.append(block)
        if "basis" in item and item.get("basis") not in {"domain", "responsibility"}:
            blocks.append(_block("MALFORMED", f"{location}.basis"))
        if isinstance(item.get("path"), str):
            paths.append(item["path"])
    if len(paths) != len(set(paths)):
        blocks.append(_block("MALFORMED", "module_boundaries.path"))
    return blocks


def _missing_document_blocks() -> tuple[str, ...]:
    return tuple(
        sorted(
            _block("MISSING", f"{section}.{field}")
            for section in set(_TEXT_FIELDS) | set(_LIST_FIELDS)
            for field in {*_TEXT_FIELDS.get(section, ()), *_LIST_FIELDS.get(section, ())}
        )
    )


def evaluate_review_evidence(
    content: bytes | None,
) -> tuple[ReviewEvidence | None, tuple[str, ...]]:
    """Return normalized evidence or every applicable deterministic defect."""
    if content is None:
        return None, _missing_document_blocks()
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, (_block("MALFORMED", "document"),)
    expected_sections = set(_TEXT_FIELDS) | set(_LIST_FIELDS)
    blocks: list[str] = []
    if "schema_version" not in data:
        blocks.append(_block("MISSING", "schema_version"))
    elif data["schema_version"] != "1.0":
        blocks.append(_block("MALFORMED", "schema_version"))
    blocks.extend(
        _block("MALFORMED", f"review_evidence.{name}")
        for name in sorted(set(data) - expected_sections - {"schema_version", "module_boundaries"})
    )
    for name in sorted(expected_sections):
        blocks.extend(_section_blocks(data, name))
    blocks.extend(_module_boundary_blocks(data.get("module_boundaries")))
    if blocks:
        return None, tuple(sorted(set(blocks)))
    return parse_review_evidence(content), ()
