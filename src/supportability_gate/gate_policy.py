"""Enforce fixed gate adapters, coverage, and anti-weakening policy."""

from __future__ import annotations

from supportability_gate.contract import Contract, GateAdapter
from supportability_gate.function_changes import ChangedFileAssessment

APPROVED_ADAPTERS = (
    "python.c901-touched.v1",
    "python.import-linter.v1",
    "python.mypy-strict.v1",
    "python.pytest.v1",
    "python.ruff-lint.v1",
)
APPROVED_ADAPTERS_BY_LANGUAGE = {
    "python": APPROVED_ADAPTERS,
    "typescript": (
        "typescript.c901-equivalent-touched.v1",
        "typescript.import-boundaries.v1",
    ),
}
MAXIMUM_COMPLEXITY = 10
_SOURCE_SUFFIXES = (".cts", ".js", ".jsx", ".mts", ".py", ".pyi", ".ts", ".tsx")
_PROFILE_SUFFIXES = {
    "python": (".py", ".pyi"),
    "typescript": (".cts", ".mts", ".ts", ".tsx"),
}


def _gate_map(policy: Contract) -> dict[str, GateAdapter]:
    return {gate.adapter: gate for gate in policy.gates}


def _changed_production_paths(
    assessments: tuple[ChangedFileAssessment, ...],
) -> tuple[str, ...]:
    paths: set[str] = set()
    for item in assessments:
        if item.base_production and item.change.old_path:
            paths.add(item.change.old_path)
        if item.head_production and item.change.new_path:
            paths.add(item.change.new_path)
    return tuple(sorted(paths))


def _unassessed_source_paths(
    policy: Contract,
    assessments: tuple[ChangedFileAssessment, ...],
) -> tuple[str, ...]:
    allowed = _PROFILE_SUFFIXES[policy.language]
    paths: set[str] = set()
    for item in assessments:
        sides = (
            (item.change.old_path, item.base_production),
            (item.change.new_path, item.head_production),
        )
        paths.update(
            path
            for path, production in sides
            if path
            and production
            and path.endswith(_SOURCE_SUFFIXES)
            and not path.endswith(allowed)
        )
    return tuple(sorted(paths))


def evaluate_contract(
    policy: Contract,
    assessments: tuple[ChangedFileAssessment, ...],
) -> tuple[str, ...]:
    """Return deterministic base-contract adapter and coverage blocks."""
    gates = _gate_map(policy)
    approved_adapters = APPROVED_ADAPTERS_BY_LANGUAGE[policy.language]
    blocks = [
        f"UNAPPROVED_ADAPTER:{adapter}" for adapter in sorted(set(gates) - set(approved_adapters))
    ]
    blocks.extend(
        f"MISSING_REQUIRED_ADAPTER:{adapter}"
        for adapter in approved_adapters
        if adapter not in gates
    )
    if policy.maximum > MAXIMUM_COMPLEXITY:
        blocks.append("MAXIMUM_EXCEEDS_APPROVED_THRESHOLD")
    blocks.extend(
        f"PROFILE_SOURCE_MISMATCH:{path}" for path in _unassessed_source_paths(policy, assessments)
    )
    for adapter in approved_adapters:
        gate = gates.get(adapter)
        if gate is None:
            continue
        blocks.extend(
            f"CHANGED_FILE_GATE_COVERAGE:{adapter}:{path}"
            for path in _changed_production_paths(assessments)
            if not gate.covers(path)
        )
        blocks.extend(
            f"HIGH_RISK_FILE_GATE_COVERAGE:{adapter}:{path}"
            for path in policy.high_risk_paths
            if not gate.covers(path)
        )
    return tuple(sorted(blocks))


def contract_change_blocks(base: Contract, head: Contract | None) -> tuple[str, ...]:
    """Identify deterministic weakening within an already-blocked contract change."""
    if head is None:
        return ()
    blocks: list[str] = []
    if head.maximum > base.maximum:
        blocks.append("THRESHOLD_WEAKENING")
    head_gates = _gate_map(head)
    for adapter, base_gate in _gate_map(base).items():
        head_gate = head_gates.get(adapter)
        if head_gate is None or any(not head_gate.covers(path) for path in base_gate.paths):
            blocks.append("GATE_SCOPE_NARROWING")
            break
    return tuple(blocks)
