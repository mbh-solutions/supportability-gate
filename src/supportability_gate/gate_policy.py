"""Enforce fixed gate adapters, coverage, and anti-weakening policy."""

from __future__ import annotations

from supportability_gate import contract
from supportability_gate.contract import (
    FIXED_ADAPTERS_BY_LANGUAGE,
    Contract,
    GateAdapter,
)
from supportability_gate.function_changes import ChangedFileAssessment
from supportability_gate.git_changes import ChangedPath

APPROVED_ADAPTERS_BY_LANGUAGE = FIXED_ADAPTERS_BY_LANGUAGE
APPROVED_ADAPTERS = APPROVED_ADAPTERS_BY_LANGUAGE["python"]


def is_profile_expansion(base: Contract, head: Contract | None) -> bool:
    """Expose the inward contract transition predicate to existing callers."""
    return contract.is_profile_expansion(base, head)


def is_deleted_high_risk_transition(
    base: Contract,
    head: Contract | None,
    changes: tuple[ChangedPath, ...],
) -> bool:
    """Allow only high-risk entries whose exact tracked files were deleted."""
    if head is None:
        return False
    deleted = {
        item.old_path
        for item in changes
        if item.status == "DELETED" and item.old_path is not None and item.new_path is None
    }
    remaining = tuple(path for path in base.high_risk_paths if path not in deleted)
    return (
        remaining != base.high_risk_paths
        and head.high_risk_paths == remaining
        and head.schema_version == base.schema_version
        and head.language == base.language
        and head.languages == base.languages
        and head.production_paths == base.production_paths
        and head.adapter == base.adapter
        and head.maximum == base.maximum
        and head.gates == base.gates
    )


def is_allowed_contract_transition(
    base: Contract,
    head: Contract | None,
    changes: tuple[ChangedPath, ...],
) -> bool:
    """Return whether the exact candidate contract transition is approved."""
    return is_profile_expansion(base, head) or is_deleted_high_risk_transition(base, head, changes)


MAXIMUM_COMPLEXITY = 10
_SOURCE_SUFFIXES = (".cts", ".js", ".jsx", ".mts", ".py", ".pyi", ".ts", ".tsx")
_PROFILE_SUFFIXES = {
    "python": (".py", ".pyi"),
    "typescript": (".cts", ".mts", ".ts", ".tsx"),
    "mixed": (".cts", ".mts", ".py", ".pyi", ".ts", ".tsx"),
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
    if not set(base.languages).issubset(head.languages):
        blocks.append("LANGUAGE_PROFILE_NARROWING")
    head_gates = _gate_map(head)
    for adapter, base_gate in _gate_map(base).items():
        head_gate = head_gates.get(adapter)
        if head_gate is None or any(not head_gate.covers(path) for path in base_gate.paths):
            blocks.append("GATE_SCOPE_NARROWING")
            break
    return tuple(blocks)
