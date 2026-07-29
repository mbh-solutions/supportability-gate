from __future__ import annotations

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


@pytest.mark.parametrize(
    ("path", "basis"),
    [
        ("src/validation/parser.py", "responsibility"),
        ("src/orders/model.py", "domain"),
    ],
)
def test_cohesive_owned_location_passes(path: str, basis: str) -> None:
    result = evaluate_modularity(
        _policy(), (_assessment(path),), _review(path, basis), _architecture(path)
    )

    assert result.blocks == ()
    assert result.new_paths == (path,)
    assert result.coverage[0].architecture is True
    assert len(result.coverage[0].adapters) == 5


@pytest.mark.parametrize("name", ["utils", "helpers", "common", "misc", "stuff"])
def test_vague_new_location_blocks(name: str) -> None:
    path = f"src/{name}/format.py"

    result = evaluate_modularity(
        _policy(), (_assessment(path),), _review(path, "responsibility"), _architecture(path)
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
    )

    assert result.blocks == (f"PARALLEL_PACKAGE:{path}:{owner}",)
    assert result.coupling_edges[0].target == owner


def test_new_location_without_complete_architecture_coverage_blocks() -> None:
    path = "src/orders/model.py"

    result = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, "domain"),
        _architecture(path, covered=False),
    )

    assert result.blocks == (f"NEW_LOCATION_GATE_COVERAGE:{path}",)


def test_missing_or_unresolved_justification_blocks() -> None:
    path = "src/orders/model.py"
    missing = evaluate_modularity(_policy(), (_assessment(path),), None, _architecture(path))
    unresolved = evaluate_modularity(
        _policy(),
        (_assessment(path),),
        _review(path, "domain", "src/orders/missing.py"),
        _architecture(path),
    )

    assert missing.blocks == (f"MISSING_NEW_LOCATION_JUSTIFICATION:{path}",)
    assert unresolved.blocks == (f"UNRESOLVED_MODULE_OWNER:{path}:src/orders/missing.py",)


def test_modularity_evidence_is_deterministic() -> None:
    path = "src/orders/model.py"
    arguments = (_policy(), (_assessment(path),), _review(path, "domain"), _architecture(path))

    assert evaluate_modularity(*arguments) == evaluate_modularity(*arguments)
