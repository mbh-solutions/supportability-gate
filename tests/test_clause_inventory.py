from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import supportability_gate.clause_inventory as inventory_module
from supportability_gate.clause_inventory import ClauseInventoryError, validate_inventory
from supportability_gate.review_evidence import parse_review_evidence

ROOT = Path(__file__).parents[1]
STANDARD = (ROOT / "docs" / "supportability_standard.md").read_bytes()
INVENTORY = (ROOT / "docs" / "normative_clause_inventory.json").read_bytes()
CLAUSES = json.loads(INVENTORY)["clauses"]
CLAUSE_CASES = tuple((index, clause["clause_id"]) for index, clause in enumerate(CLAUSES))
OWNERS = tuple(sorted({clause["enforcement_owner"] for clause in CLAUSES}))


def _data() -> dict[str, object]:
    return json.loads(INVENTORY)


def _encoded(data: dict[str, object]) -> bytes:
    return json.dumps(data, sort_keys=True).encode()


def _assert_block(data: dict[str, object], code: str) -> None:
    with pytest.raises(ClauseInventoryError) as caught:
        validate_inventory(STANDARD, _encoded(data))
    assert caught.value.code == code


def _reassign_owner(clause: dict[str, object]) -> None:
    current_owner = clause["enforcement_owner"]
    donor = next(item for item in CLAUSES if item["enforcement_owner"] != current_owner)
    for field in (
        "enforcement_owner",
        "proof_class",
        "evidence_requirement",
        "blocking_test",
        "test_proof",
    ):
        clause[field] = donor[field]


def test_complete_inventory_maps_every_normative_clause() -> None:
    clauses = validate_inventory(STANDARD, INVENTORY)
    assert len(clauses) == 218
    assert {profile for clause in clauses for profile in clause.profiles} == {"python", "frontend"}
    frontend = {204, 206, 208, 210, 211, 212, 213, 214, 215, 216, 217, 218, 219, 221, 223}
    python = {120, 122, 124, 126, 127, 128, 129, 130, 131, 155, 157, 571, 626, 638, 639}
    for clause in clauses:
        expected = (
            {"frontend"}
            if clause.source_line in frontend
            else {"python"}
            if clause.source_line in python
            else {"python", "frontend"}
        )
        assert set(clause.profiles) == expected
        assert clause.enforcement_owner != "Milestone 1 traceability validator"
        assert clause.blocking_test.startswith("tests/test_")


def test_inventory_uses_all_truthful_evidence_classes() -> None:
    clauses = validate_inventory(STANDARD, INVENTORY)
    assert {clause.proof_class for clause in clauses} == inventory_module.PROOF_CLASSES
    assert {clause.test_proof for clause in clauses} <= inventory_module.TEST_PROOF_CLASSES


@pytest.mark.parametrize("node_id", sorted(inventory_module.TEST_REFERENCE_PROOF))
def test_each_referenced_blocking_node_collects(node_id: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", node_id],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert node_id.partition("[")[0] in completed.stdout


def test_qualitative_prose_is_classified_not_semantically_judged() -> None:
    phrase = "I did not review this."
    content = f'''\
schema_version = "1.0"

[behavior]
intended_behavior = "{phrase}"
proof = "{phrase}"

[characterization]
captured_behavior = "{phrase}"
proof = "{phrase}"

[separation_of_concerns]
before = "{phrase}"
after = "{phrase}"
boundaries = []

[architecture]
dependency_direction = "{phrase}"
reviewed_paths = ["src/sample.py"]

[responsibility_boundary]
path = "src/sample.py"
owns = "{phrase}"
does_not_own = "{phrase}"

[incremental_refactor]
target = "{phrase}"
completed_step = "{phrase}"

[review_handoff]
summary = "DERIVED_FROM_AUTHENTICATED_EVIDENCE"
remaining_risks = ["DERIVED_FROM_AUTHENTICATED_EVIDENCE"]

[human_review]
naming = "{phrase}"
cohesion = "{phrase}"
intended_behavior = "{phrase}"
reviewability = "{phrase}"
'''
    parsed = parse_review_evidence(content.encode(), ())
    assert parsed["human_review"] == {
        "naming": phrase,
        "cohesion": phrase,
        "intended_behavior": phrase,
        "reviewability": phrase,
    }
    clauses = validate_inventory(STANDARD, INVENTORY)
    assert all(
        clause.proof_class == "author_declaration"
        for clause in clauses
        if clause.enforcement_owner
        in {
            "Supportability 2 - Separation of Concerns",
            "Supportability 8 - Review Handoff",
        }
    )


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
    data["clauses"][0]["applicability"]["profiles"] = ["python"]  # type: ignore[index]
    _assert_block(data, "UNSUPPORTED_NOT_APPLICABLE")


@pytest.mark.parametrize(("index", "clause_id"), CLAUSE_CASES, ids=lambda value: str(value))
def test_each_clause_blocks_owner_reassignment(index: int, clause_id: str) -> None:
    data = deepcopy(_data())
    clause = data["clauses"][index]  # type: ignore[index]
    assert clause["clause_id"] == clause_id
    _reassign_owner(clause)
    _assert_block(data, "UNAUTHORIZED_MAPPING_CHANGE")


@pytest.mark.parametrize(("index", "clause_id"), CLAUSE_CASES, ids=lambda value: str(value))
def test_each_clause_blocks_applicability_narrowing(index: int, clause_id: str) -> None:
    data = deepcopy(_data())
    clause = data["clauses"][index]  # type: ignore[index]
    assert clause["clause_id"] == clause_id
    clause["applicability"]["condition"] = "Never applies to any change."
    _assert_block(data, "UNAUTHORIZED_APPLICABILITY")


@pytest.mark.parametrize("owner", OWNERS)
def test_uncollected_blocking_test_reference_blocks(owner: str) -> None:
    data = deepcopy(_data())
    invalid_node = "tests/test_missing_inventory_reference.py::test_does_not_collect"
    for clause in data["clauses"]:  # type: ignore[union-attr]
        if clause["enforcement_owner"] == owner:
            clause["blocking_test"] = invalid_node
    _assert_block(data, "INVALID_TEST_REFERENCE")


def test_ss_0165_cannot_be_reassigned_to_inventory_presence() -> None:
    data = deepcopy(_data())
    clause = next(
        item
        for item in data["clauses"]
        if item["clause_id"] == "SS-0165"  # type: ignore[union-attr]
    )
    _reassign_owner(clause)
    _assert_block(data, "UNAUTHORIZED_MAPPING_CHANGE")


def test_malformed_registry_is_technical_failure() -> None:
    with pytest.raises(ClauseInventoryError) as caught:
        validate_inventory(STANDARD, b"{")
    assert caught.value.code == "MALFORMED_INVENTORY"
    assert caught.value.decision == "TECHNICAL_FAILURE"


def test_mapping_tamper_is_policy_block() -> None:
    data = deepcopy(_data())
    _reassign_owner(data["clauses"][0])  # type: ignore[index]
    with pytest.raises(ClauseInventoryError) as caught:
        validate_inventory(STANDARD, _encoded(data))
    assert caught.value.code == "UNAUTHORIZED_MAPPING_CHANGE"
    assert caught.value.decision == "BLOCK"


def test_standard_hash_mismatch_blocks() -> None:
    with pytest.raises(ClauseInventoryError) as caught:
        validate_inventory(STANDARD + b"\n", INVENTORY)
    assert caught.value.code == "STANDARD_HASH_MISMATCH"
