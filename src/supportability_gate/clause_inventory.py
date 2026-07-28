"""Validate complete traceability for the immutable Supportability Standard."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

STANDARD_SHA256 = "81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2"
BLOCKING_TEST = (
    "tests/test_clause_inventory.py::test_each_clause_blocks_when_required_mapping_is_missing"
)
PROFILES = {"python", "frontend"}
EXPECTED_SOURCE_LINES = (
    18,
    26,
    28,
    30,
    32,
    34,
    40,
    42,
    43,
    44,
    45,
    46,
    47,
    48,
    49,
    50,
    52,
    54,
    55,
    56,
    57,
    58,
    60,
    62,
    68,
    70,
    72,
    73,
    74,
    75,
    76,
    77,
    78,
    80,
    83,
    86,
    89,
    92,
    100,
    120,
    122,
    124,
    126,
    127,
    128,
    129,
    130,
    131,
    155,
    157,
    165,
    167,
    175,
    177,
    178,
    179,
    180,
    181,
    182,
    183,
    184,
    185,
    186,
    187,
    188,
    192,
    195,
    196,
    197,
    200,
    204,
    206,
    208,
    210,
    211,
    212,
    213,
    214,
    215,
    216,
    217,
    218,
    219,
    221,
    223,
    231,
    251,
    253,
    255,
    259,
    267,
    279,
    281,
    283,
    285,
    287,
    289,
    290,
    291,
    292,
    293,
    294,
    295,
    296,
    298,
    335,
    343,
    345,
    353,
    355,
    359,
    367,
    369,
    377,
    379,
    381,
    383,
    387,
    403,
    405,
    407,
    409,
    410,
    411,
    412,
    413,
    414,
    416,
    418,
    420,
    438,
    442,
    444,
    452,
    454,
    462,
    464,
    465,
    466,
    467,
    468,
    469,
    470,
    471,
    475,
    481,
    485,
    501,
    506,
    510,
    514,
    521,
    526,
    530,
    534,
    541,
    566,
    571,
    578,
    583,
    590,
    595,
    602,
    607,
    614,
    617,
    619,
    621,
    623,
    625,
    626,
    627,
    628,
    629,
    630,
    631,
    632,
    634,
    638,
    639,
    640,
    641,
    642,
    643,
    644,
    645,
    646,
    647,
    648,
    649,
    650,
    654,
    656,
    660,
    662,
    663,
    664,
    665,
    666,
    667,
    668,
    669,
    676,
    679,
    681,
    688,
    690,
    692,
    693,
    694,
    695,
    696,
    697,
    698,
    699,
    700,
    701,
    703,
)


class ClauseInventoryError(ValueError):
    """One fail-closed clause inventory defect."""

    def __init__(self, code: str, location: str) -> None:
        super().__init__(location)
        self.code = code
        self.location = location


@dataclass(frozen=True)
class Clause:
    """One validated normative statement mapping."""

    clause_id: str
    source_line: int
    statement: str
    profiles: tuple[str, ...]
    condition: str
    enforcement_owner: str
    evidence_requirement: str
    blocking_test: str


def _require_keys(data: dict[str, Any], expected: set[str], location: str) -> None:
    missing = sorted(expected - set(data))
    if missing:
        raise ClauseInventoryError("MISSING_FIELD", f"{location}.{missing[0]}")
    unknown = sorted(set(data) - expected)
    if unknown:
        raise ClauseInventoryError("MALFORMED_FIELD", f"{location}.{unknown[0]}")


def _text(value: object, location: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClauseInventoryError(code, location)
    return value


def _profiles(value: object, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ClauseInventoryError("UNSUPPORTED_NOT_APPLICABLE", location)
    profiles = tuple(value)
    if set(profiles) - PROFILES or len(profiles) != len(set(profiles)):
        raise ClauseInventoryError("UNSUPPORTED_NOT_APPLICABLE", location)
    return profiles


def _clause(item: object, index: int, standard_lines: list[str]) -> Clause:
    location = f"clauses[{index}]"
    if not isinstance(item, dict):
        raise ClauseInventoryError("MALFORMED_CLAUSE", location)
    _require_keys(
        item,
        {
            "clause_id",
            "source_line",
            "statement",
            "applicability",
            "enforcement_owner",
            "evidence_requirement",
            "blocking_test",
        },
        location,
    )
    source_line = item["source_line"]
    if type(source_line) is not int or not 1 <= source_line <= len(standard_lines):
        raise ClauseInventoryError("INVALID_SOURCE_LINE", f"{location}.source_line")
    statement = _text(item["statement"], f"{location}.statement", "MISSING_STATEMENT")
    if statement != standard_lines[source_line - 1].strip():
        raise ClauseInventoryError("SOURCE_TEXT_MISMATCH", f"{location}.statement")
    applicability = item["applicability"]
    if not isinstance(applicability, dict):
        raise ClauseInventoryError("UNSUPPORTED_NOT_APPLICABLE", f"{location}.applicability")
    _require_keys(applicability, {"profiles", "condition"}, f"{location}.applicability")
    return Clause(
        clause_id=_text(item["clause_id"], f"{location}.clause_id", "MISSING_CLAUSE_ID"),
        source_line=source_line,
        statement=statement,
        profiles=_profiles(applicability["profiles"], f"{location}.applicability.profiles"),
        condition=_text(
            applicability["condition"],
            f"{location}.applicability.condition",
            "UNSUPPORTED_NOT_APPLICABLE",
        ),
        enforcement_owner=_text(
            item["enforcement_owner"],
            f"{location}.enforcement_owner",
            "MISSING_ENFORCEMENT_OWNER",
        ),
        evidence_requirement=_text(
            item["evidence_requirement"],
            f"{location}.evidence_requirement",
            "MISSING_EVIDENCE_REQUIREMENT",
        ),
        blocking_test=_text(
            item["blocking_test"], f"{location}.blocking_test", "ABSENT_BLOCKING_TEST"
        ),
    )


def _verify_coverage(clauses: tuple[Clause, ...]) -> None:
    expected = {f"SS-{line:04d}" for line in EXPECTED_SOURCE_LINES}
    actual = {item.clause_id for item in clauses}
    if len(actual) != len(clauses):
        raise ClauseInventoryError("DUPLICATE_CLAUSE_ID", "clauses")
    if missing := sorted(expected - actual):
        raise ClauseInventoryError("OMITTED_NORMATIVE_CLAUSE", missing[0])
    if unknown := sorted(actual - expected):
        raise ClauseInventoryError("UNKNOWN_CLAUSE_ID", unknown[0])
    for clause in clauses:
        if clause.clause_id != f"SS-{clause.source_line:04d}":
            raise ClauseInventoryError("CLAUSE_SOURCE_MISMATCH", clause.clause_id)
        if clause.blocking_test != BLOCKING_TEST:
            raise ClauseInventoryError("ABSENT_BLOCKING_TEST", clause.clause_id)


def validate_inventory(standard_content: bytes, inventory_content: bytes) -> tuple[Clause, ...]:
    """Return validated clauses or fail closed on any incomplete mapping."""
    if hashlib.sha256(standard_content).hexdigest() != STANDARD_SHA256:
        raise ClauseInventoryError("STANDARD_HASH_MISMATCH", "standard")
    try:
        standard_lines = standard_content.decode("utf-8").splitlines()
        data = json.loads(inventory_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ClauseInventoryError("MALFORMED_INVENTORY", "document") from error
    if not isinstance(data, dict):
        raise ClauseInventoryError("MALFORMED_INVENTORY", "document")
    _require_keys(data, {"schema_version", "standard_sha256", "clauses"}, "inventory")
    if data["schema_version"] != "1.0" or data["standard_sha256"] != STANDARD_SHA256:
        raise ClauseInventoryError("STANDARD_HASH_MISMATCH", "inventory.standard_sha256")
    if not isinstance(data["clauses"], list):
        raise ClauseInventoryError("MALFORMED_INVENTORY", "clauses")
    clauses = tuple(
        _clause(item, index, standard_lines) for index, item in enumerate(data["clauses"])
    )
    _verify_coverage(clauses)
    return tuple(sorted(clauses, key=lambda item: item.clause_id))
