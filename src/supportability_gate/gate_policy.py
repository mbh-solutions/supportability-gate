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
MAXIMUM_COMPLEXITY = 10


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


def evaluate_contract(
    policy: Contract,
    assessments: tuple[ChangedFileAssessment, ...],
) -> tuple[str, ...]:
    """Return deterministic base-contract adapter and coverage blocks."""
    gates = _gate_map(policy)
    blocks = [
        f"UNAPPROVED_ADAPTER:{adapter}" for adapter in sorted(set(gates) - set(APPROVED_ADAPTERS))
    ]
    blocks.extend(
        f"MISSING_REQUIRED_ADAPTER:{adapter}"
        for adapter in APPROVED_ADAPTERS
        if adapter not in gates
    )
    if policy.maximum > MAXIMUM_COMPLEXITY:
        blocks.append("MAXIMUM_EXCEEDS_APPROVED_THRESHOLD")
    for adapter in APPROVED_ADAPTERS:
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
