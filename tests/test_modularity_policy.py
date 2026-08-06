from __future__ import annotations

from types import SimpleNamespace

import pytest

from supportability_gate.architecture_policy import ArchitectureResult, ImportEdge
from supportability_gate.contract import parse_contract
from supportability_gate.function_changes import ChangedFileAssessment
from supportability_gate.git_changes import ChangedPath
from supportability_gate.modularity_policy import evaluate_modularity


def _policy():
    return parse_contract(
        b"""schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = []

[[gates]]
adapter = "python.c901-touched.v1"
paths = ["src"]

[[gates]]
adapter = "python.import-linter.v1"
paths = ["src"]

[[gates]]
adapter = "python.mypy-strict.v1"
paths = ["src"]

[[gates]]
adapter = "python.pytest.v1"
paths = ["src"]

[[gates]]
adapter = "python.ruff-lint.v1"
paths = ["src"]

[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
"""
    )


def _assessment(path: str) -> ChangedFileAssessment:
    return ChangedFileAssessment(ChangedPath("ADDED", None, path), False, True, True, (1,))


def _review(path: str, basis: str, owner_path: str | None = None) -> dict[str, object]:
    return {
        "module_boundaries": [
            {
                "path": path,
                "owner_path": owner_path or path,
                "basis": basis,
                "justification": "Exact source path owns one cohesive boundary.",
            }
        ]
    }


def _architecture(path: str, *, covered: bool = True, owner_path: str | None = None):
    nodes = tuple(sorted({path, *(value for value in [owner_path] if value)}))
    return ArchitectureResult(
        "python.import-linter.v1",
        True,
        (path,) if covered else (),
        nodes,
        (ImportEdge(path, owner_path, 1, "owner", True),) if owner_path else (),
        (),
    )


def _quality(path: str, *, unexecuted: str | None = None):
    return SimpleNamespace(
        commands=tuple(
            SimpleNamespace(
                adapter=gate.adapter,
                observed_paths=(path,),
                zero_statement_paths=(),
                executed=gate.adapter != unexecuted,
                exit_code=0,
            )
            for gate in _policy().gates
        )
    )


@pytest.mark.parametrize(
    ("path", "basis"),
    [
        ("src/validation/parser.py", "responsibility"),
        ("src/orders/model.py", "domain"),
    ],
)
def test_cohesive_owned_location_passes(path: str, basis: str) -> None:
    owner = str(path.rsplit("/", 1)[0]) + "/owner.py"
    result = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, basis, owner),
        _architecture(path, owner_path=owner),
        _quality(path),
    )

    assert result.blocks == ()
    assert result.new_paths == (path,)
    assert result.coverage[0].architecture is True
    assert len(result.coverage[0].adapters) == 5


@pytest.mark.parametrize("name", ["utils", "helpers", "common", "misc", "stuff"])
def test_vague_new_location_blocks(name: str) -> None:
    path = f"src/{name}/format.py"

    result = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, "responsibility", f"src/{name}/owner.py"),
        _architecture(path, owner_path=f"src/{name}/owner.py"),
        _quality(path),
    )

    assert result.blocks == (f"VAGUE_PRODUCTION_LOCATION:{path}:{name}",)


def test_unjustified_parallel_package_blocks() -> None:
    path = "src/order_copy/model.py"
    owner = "src/orders/model.py"

    result = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, "domain", owner),
        _architecture(path, owner_path=owner),
        _quality(path),
    )

    assert result.blocks == (f"PARALLEL_PACKAGE:{path}:{owner}",)
    assert result.coupling_edges[0].target == owner


def test_new_location_without_complete_architecture_coverage_blocks() -> None:
    path = "src/orders/model.py"
    owner = "src/orders/owner.py"

    result = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, "domain", owner),
        _architecture(path, owner_path=owner, covered=False),
        _quality(path),
    )
    unexecuted = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, "domain", owner),
        _architecture(path, owner_path=owner),
        _quality(path, unexecuted="python.pytest.v1"),
    )

    assert result.blocks == (f"NEW_LOCATION_GATE_COVERAGE:{path}",)
    assert unexecuted.blocks == (f"NEW_LOCATION_GATE_COVERAGE:{path}",)


def test_missing_or_unresolved_justification_blocks() -> None:
    path = "src/orders/model.py"
    missing = evaluate_modularity(
        _policy(), (_assessment(path),), None, _architecture(path), _quality(path)
    )
    unresolved = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, "domain", "src/orders/missing.py"),
        _architecture(path),
        _quality(path),
    )
    assert missing.blocks == (f"MISSING_NEW_LOCATION_JUSTIFICATION:{path}",)
    assert unresolved.blocks == (f"UNRESOLVED_MODULE_OWNER:{path}:src/orders/missing.py",)


def test_independent_new_location_can_own_itself() -> None:
    path = "src/orders/model.py"
    result = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, "domain"),
        _architecture(path),
        _quality(path),
    )

    assert result.blocks == ()


def test_new_location_cannot_claim_another_new_location_as_owner() -> None:
    path = "src/orders/model.py"
    owner = "src/orders/owner.py"
    result = evaluate_modularity(
        _policy(),
        (_assessment(path), _assessment(owner)),
        _review(path, "domain", owner),
        _architecture(path, owner_path=owner),
        _quality(path),
    )

    assert f"NEW_MODULE_OWNER_NOT_PREEXISTING:{path}:{owner}" in result.blocks


def test_modularity_evidence_is_deterministic() -> None:
    path = "src/orders/model.py"
    owner = "src/orders/owner.py"
    arguments = (
        _policy(),
        (_assessment(path),),
        _review(path, "domain", owner),
        _architecture(path, owner_path=owner),
        _quality(path),
    )

    assert evaluate_modularity(*arguments) == evaluate_modularity(*arguments)
