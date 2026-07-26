"""Apply the fixed progressive legacy-complexity decision table."""

from __future__ import annotations

from dataclasses import dataclass

from supportability_gate.complexity_metrics import FunctionMetric
from supportability_gate.contract import Contract
from supportability_gate.function_changes import FunctionDelta


@dataclass(frozen=True)
class FunctionDecision:
    """One fixed-table decision with exact base/head metrics."""

    base: FunctionMetric | None
    head: FunctionMetric | None
    state: str
    decision: str
    remaining_debt: int | None
    next_target: int | None


def _metric(
    definition: object,
    metrics: dict[tuple[str, str], FunctionMetric],
) -> FunctionMetric | None:
    if definition is None:
        return None
    span = definition.span  # type: ignore[attr-defined]
    return metrics[(span.path, span.qualified_name)]


def decide_functions(
    contract: Contract,
    deltas: tuple[FunctionDelta, ...],
    base_metrics: tuple[FunctionMetric, ...],
    head_metrics: tuple[FunctionMetric, ...],
) -> tuple[FunctionDecision, ...]:
    """Apply the complete immutable decision table."""
    base_by_name = {(item.span.path, item.span.qualified_name): item for item in base_metrics}
    head_by_name = {(item.span.path, item.span.qualified_name): item for item in head_metrics}
    decisions = [
        _decide_one(
            contract.maximum,
            _metric(delta.base, base_by_name),
            _metric(delta.head, head_by_name),
        )
        for delta in deltas
    ]

    def identity(decision: FunctionDecision) -> tuple[str, str]:
        metric = decision.head or decision.base
        if metric is None:
            raise ValueError("function decision has no base or head")
        return metric.span.path, metric.span.qualified_name

    return tuple(sorted(decisions, key=identity))


def _decide_one(
    maximum: int,
    base: FunctionMetric | None,
    head: FunctionMetric | None,
) -> FunctionDecision:
    if head is None:
        return FunctionDecision(base, None, "DELETED", "DELETED", None, None)
    if base is None:
        decision = "PASS" if head.complexity <= maximum else "BLOCK"
        return FunctionDecision(None, head, "NEW", decision, None, None)
    if base.complexity <= maximum:
        decision = "PASS" if head.complexity <= maximum else "BLOCK"
        return FunctionDecision(base, head, "EXISTING", decision, None, None)
    if head.complexity <= maximum:
        return FunctionDecision(base, head, "EXISTING_LEGACY", "PASS", 0, maximum)
    if head.complexity < base.complexity:
        return FunctionDecision(
            base,
            head,
            "EXISTING_LEGACY",
            "PASS_PROGRESSIVE",
            head.complexity - maximum,
            maximum,
        )
    return FunctionDecision(
        base,
        head,
        "EXISTING_LEGACY",
        "BLOCK",
        head.complexity - maximum,
        maximum,
    )
