"""Validate complete traceability for the immutable Supportability Standard."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

STANDARD_SHA256 = "81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2"
ASSURANCE_CONTRACT_SHA256 = "d29e24d05be23b8ae9d9c63f3bb1ee94e350269fadc053be1939a7c48fb42820"
CANONICAL_IDENTITY_SHA256 = "c0d53decba5b77f0e45d53313a8403a0f2dc7c624755e499d8e8a61aa62e64a1"
CANONICAL_APPLICABILITY_SHA256 = "c7e8b6bfcaa5f9fb824888d2eb12111d85af930084101a2ae4bb368b6ab6f88d"
CANONICAL_MAPPING_SHA256 = "a5adb59d94b9928934448cd4004c63c2da7dadc7b3c8e6a0f38db6b3ba45d69b"
PROFILES = {"python", "frontend"}
PROOF_CLASSES = {
    "measured_fact",
    "deterministic_decision",
    "author_declaration",
    "authenticated_owner_attestation",
}
TEST_PROOF_CLASSES = {
    "behavioral_enforcement",
    "schema_provenance_validation",
    "process_evidence",
    "attestation_boundary",
}
FRONTEND_ONLY_LINES = frozenset(
    {204, 206, 208, 210, 211, 212, 213, 214, 215, 216, 217, 218, 219, 221, 223}
)
PYTHON_ONLY_LINES = frozenset(
    {120, 122, 124, 126, 127, 128, 129, 130, 131, 155, 157, 571, 626, 638, 639}
)
PROFILE_BY_LINE = {
    **dict.fromkeys(FRONTEND_ONLY_LINES, frozenset({"frontend"})),
    **dict.fromkeys(PYTHON_ONLY_LINES, frozenset({"python"})),
}
TEST_REFERENCE_PROOF = {
    "tests/test_clause_inventory.py::test_each_clause_blocks_when_required_mapping_is_missing": (
        "schema_provenance_validation"
    ),
    "tests/test_evaluate_complexity.py::test_new_complexity_11_blocks": ("behavioral_enforcement"),
    "tests/test_evaluate_complexity.py::test_insufficient_milestone_three_evidence_blocks[human_review]": (
        "attestation_boundary"
    ),
    "tests/test_evaluate_complexity.py::test_insufficient_milestone_three_evidence_blocks[responsibility_boundary]": (
        "attestation_boundary"
    ),
    "tests/test_evaluate_complexity.py::test_insufficient_milestone_three_evidence_blocks[architecture]": (
        "attestation_boundary"
    ),
    "tests/test_evaluate_complexity.py::test_insufficient_milestone_three_evidence_blocks[behavior]": (
        "attestation_boundary"
    ),
    "tests/test_evaluate_complexity.py::test_missing_milestone_three_evidence_blocks[review_handoff]": (
        "process_evidence"
    ),
    "tests/test_architecture_policy.py::test_cross_layer_inversion_blocks": (
        "behavioral_enforcement"
    ),
    "tests/test_modularity_policy.py::test_vague_new_location_blocks": ("behavioral_enforcement"),
    "tests/test_modularity_policy.py::test_new_location_without_complete_architecture_coverage_blocks": (
        "behavioral_enforcement"
    ),
    "tests/test_modularity_policy.py::test_missing_or_unresolved_justification_blocks": (
        "attestation_boundary"
    ),
    "tests/test_characterization.py::test_incompatible_behavior_blocks": ("behavioral_enforcement"),
    "tests/test_refactor_policy.py::test_non_runnable_intermediate_state_blocks": (
        "behavioral_enforcement"
    ),
    "tests/test_refactor_policy.py::test_repo_wide_cleanup_requires_exact_broad_authorization": (
        "attestation_boundary"
    ),
    "tests/test_quality_profile.py::test_missing_required_command_blocks": (
        "behavioral_enforcement"
    ),
    "tests/test_quality_profile.py::test_self_declared_quality_artifact_is_rejected": (
        "schema_provenance_validation"
    ),
}
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
        self.decision = (
            "TECHNICAL_FAILURE"
            if code
            in {
                "MALFORMED_INVENTORY",
                "MALFORMED_ASSURANCE_CONTRACT",
                "MALFORMED_CLAUSE",
                "MALFORMED_FIELD",
                "MISSING_FIELD",
            }
            else "BLOCK"
        )


@dataclass(frozen=True)
class Clause:
    """One validated normative statement mapping."""

    clause_id: str
    source_line: int
    statement: str
    profiles: tuple[str, ...]
    condition: str
    enforcement_owner: str
    proof_class: str
    evidence_requirement: str
    blocking_test: str
    test_proof: str


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


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
            "proof_class",
            "evidence_requirement",
            "blocking_test",
            "test_proof",
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
    proof_class = _text(item["proof_class"], f"{location}.proof_class", "INVALID_PROOF_CLASS")
    if proof_class not in PROOF_CLASSES:
        raise ClauseInventoryError("INVALID_PROOF_CLASS", f"{location}.proof_class")
    blocking_test = _text(
        item["blocking_test"], f"{location}.blocking_test", "ABSENT_BLOCKING_TEST"
    )
    if blocking_test not in TEST_REFERENCE_PROOF:
        raise ClauseInventoryError("INVALID_TEST_REFERENCE", f"{location}.blocking_test")
    test_proof = _text(item["test_proof"], f"{location}.test_proof", "INVALID_TEST_PROOF")
    if test_proof not in TEST_PROOF_CLASSES:
        raise ClauseInventoryError("INVALID_TEST_PROOF", f"{location}.test_proof")
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
        proof_class=proof_class,
        evidence_requirement=_text(
            item["evidence_requirement"],
            f"{location}.evidence_requirement",
            "MISSING_EVIDENCE_REQUIREMENT",
        ),
        blocking_test=blocking_test,
        test_proof=test_proof,
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
    ordered = tuple(sorted(clauses, key=lambda item: item.clause_id))
    for clause in ordered:
        expected_profiles = PROFILE_BY_LINE.get(clause.source_line, PROFILES)
        checks = (
            (clause.clause_id == f"SS-{clause.source_line:04d}", "CLAUSE_SOURCE_MISMATCH"),
            (set(clause.profiles) == expected_profiles, "UNSUPPORTED_NOT_APPLICABLE"),
            (
                TEST_REFERENCE_PROOF[clause.blocking_test] == clause.test_proof,
                "INVALID_TEST_PROOF",
            ),
        )
        for valid, error_code in checks:
            if not valid:
                raise ClauseInventoryError(error_code, clause.clause_id)
    identities = [[clause.clause_id, clause.source_line, clause.statement] for clause in ordered]
    applicability = [
        [
            clause.clause_id,
            {"profiles": list(clause.profiles), "condition": clause.condition},
        ]
        for clause in ordered
    ]
    mappings = [
        [
            clause.clause_id,
            clause.enforcement_owner,
            clause.proof_class,
            clause.evidence_requirement,
            clause.blocking_test,
            clause.test_proof,
        ]
        for clause in ordered
    ]
    if _digest(identities) != CANONICAL_IDENTITY_SHA256:
        raise ClauseInventoryError("UNAUTHORIZED_IDENTITY_CHANGE", "clauses")
    if _digest(applicability) != CANONICAL_APPLICABILITY_SHA256:
        raise ClauseInventoryError("UNAUTHORIZED_APPLICABILITY", "clauses")
    if _digest(mappings) != CANONICAL_MAPPING_SHA256:
        raise ClauseInventoryError("UNAUTHORIZED_MAPPING_CHANGE", "clauses")


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
    _require_keys(
        data,
        {"schema_version", "standard_sha256", "assurance_contract", "clauses"},
        "inventory",
    )
    if data["schema_version"] != "2.0" or data["standard_sha256"] != STANDARD_SHA256:
        raise ClauseInventoryError("STANDARD_HASH_MISMATCH", "inventory.standard_sha256")
    assurance_contract = data["assurance_contract"]
    if not isinstance(assurance_contract, dict):
        raise ClauseInventoryError("MALFORMED_ASSURANCE_CONTRACT", "assurance_contract")
    if _digest(assurance_contract) != ASSURANCE_CONTRACT_SHA256:
        raise ClauseInventoryError("UNAUTHORIZED_ASSURANCE_CONTRACT_CHANGE", "assurance_contract")
    if not isinstance(data["clauses"], list):
        raise ClauseInventoryError("MALFORMED_INVENTORY", "clauses")
    clauses = tuple(
        _clause(item, index, standard_lines) for index, item in enumerate(data["clauses"])
    )
    _verify_coverage(clauses)
    return tuple(sorted(clauses, key=lambda item: item.clause_id))
