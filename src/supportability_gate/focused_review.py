"""Define the transport-neutral one-time focused-review contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

FOCUSED_REVIEWS = (
    (
        "1",
        "@codex review for maze-like control flow, misleading extraction, or helpers that lower "
        "measured complexity without improving readability, testability, or naming in changed "
        "code only",
    ),
    (
        "2",
        "@codex review for mixed responsibilities or unclear single ownership in changed code only",
    ),
    (
        "3",
        "@codex review for unclear, inverted, cyclic, or unjustified dependency direction across "
        "changed boundaries only",
    ),
    (
        "4",
        "@codex review for weak domain ownership, low cohesion, avoidable coupling, "
        "or unjustified module boundaries only",
    ),
    (
        "5",
        "@codex review for missing, weak, misleading, or nondeterministic characterization of "
        "behavior at risk from this change only",
    ),
    (
        "6",
        "@codex review for oversized, non-runnable, big-bang, or insufficiently bounded refactor "
        "steps in this change only",
    ),
    (
        "7",
        "@codex review for validation evidence that omits changed or high-risk behavior, weakens "
        "scope or thresholds, hides failures, or overstates what ran only",
    ),
    (
        "8",
        "@codex review for unsupported, contradictory, stale, incomplete, or misleading "
        "handoff claims about change, boundaries, validation, risk, or gate coverage only",
    ),
)
FOCUSES = tuple(item[0] for item in FOCUSED_REVIEWS)


@dataclass(frozen=True)
class CompletionArtifact:
    """One trusted connector completion artifact."""

    kind: str
    artifact_id: int
    completed_at: datetime


@dataclass(frozen=True)
class FocusedReviewEvidence:
    """One successful focus request and its unique completion."""

    focus: str
    request_id: int
    requested_at: datetime
    completion: CompletionArtifact
