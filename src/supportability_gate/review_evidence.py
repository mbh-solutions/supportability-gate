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
    data["module_boundaries"] = data.get("module_boundaries", [])
    return data, ()
