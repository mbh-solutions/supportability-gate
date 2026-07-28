from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from supportability_gate.clause_inventory import ClauseInventoryError, validate_inventory

ROOT = Path(__file__).parents[1]
STANDARD = (ROOT / "docs" / "supportability_standard.md").read_bytes()
INVENTORY = (ROOT / "docs" / "normative_clause_inventory.json").read_bytes()


def _data() -> dict[str, object]:
    return json.loads(INVENTORY)


def _encoded(data: dict[str, object]) -> bytes:
    return json.dumps(data, sort_keys=True).encode()


def _assert_block(data: dict[str, object], code: str) -> None:
    with pytest.raises(ClauseInventoryError) as caught:
        validate_inventory(STANDARD, _encoded(data))
    assert caught.value.code == code


def test_complete_inventory_maps_every_normative_clause() -> None:
    clauses = validate_inventory(STANDARD, INVENTORY)
    assert len(clauses) == 218
    assert {profile for clause in clauses for profile in clause.profiles} == {"python", "frontend"}


def test_omitted_normative_clause_blocks() -> None:
    data = _data()
    data["clauses"] = data["clauses"][1:]  # type: ignore[index]
    _assert_block(data, "OMITTED_NORMATIVE_CLAUSE")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("enforcement_owner", "", "MISSING_ENFORCEMENT_OWNER"),
        ("evidence_requirement", "", "MISSING_EVIDENCE_REQUIREMENT"),
        ("blocking_test", "", "ABSENT_BLOCKING_TEST"),
    ],
)
def test_each_clause_blocks_when_required_mapping_is_missing(
    field: str, value: str, code: str
) -> None:
    data = deepcopy(_data())
    data["clauses"][0][field] = value  # type: ignore[index]
    _assert_block(data, code)


def test_unsupported_not_applicable_blocks() -> None:
    data = _data()
    data["clauses"][0]["applicability"]["profiles"] = []  # type: ignore[index]
    _assert_block(data, "UNSUPPORTED_NOT_APPLICABLE")


def test_standard_hash_mismatch_blocks() -> None:
    with pytest.raises(ClauseInventoryError) as caught:
        validate_inventory(STANDARD + b"\n", INVENTORY)
    assert caught.value.code == "STANDARD_HASH_MISMATCH"
