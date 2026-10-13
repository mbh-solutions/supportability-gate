"""Recognize an authorization wait without changing any canonical Gate decision.

This is hosted presentation/orchestration code. The production composer remains
the sole policy evaluator; a wait never manufactures a PASS or an attestation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from supportability_gate import qualification_bundle, standard_results

SOURCE_FILES = (
    "complexity-result.json",
    "characterization-result.json",
    "refactor-policy-result.json",
    "quality-provenance.json",
)


def read_sources(directory: Path) -> tuple[dict[str, Any], ...]:
    return tuple(json.loads((directory / name).read_bytes()) for name in SOURCE_FILES)


def identity(payload: dict[str, Any]) -> standard_results.RunIdentity:
    return standard_results.RunIdentity(
        **{name: payload[name] for name in standard_results.RunIdentity.__dataclass_fields__}
    )


def awaiting_standards(directory: Path, expected: standard_results.RunIdentity) -> frozenset[int]:
    """A valid, otherwise clean evidence bundle may wait for one missing owner grant."""
    qualification_bundle.restore_bundle(directory / "qualification-bundle.zip")
    payload = json.loads((directory / "standard-results.json").read_bytes())
    standard_results.validate_payload(payload, expected)
    complexity, characterization, refactor, quality = read_sources(directory)
    reproduced = standard_results.compose_results(
        complexity,
        characterization,
        refactor,
        quality,
        expected,
        expected_quality_artifact=quality_artifact(payload),
        expected_characterization_artifacts=characterization.get("artifacts"),
        source_outcomes=payload["source_outcomes"],
    )
    if reproduced != payload:
        raise ValueError("AUTHORIZATION_WAIT_EVIDENCE_MISMATCH")
    return _waiting_rows(payload, characterization, refactor)


def quality_artifact(payload: dict[str, Any]) -> dict[str, object]:
    artifact = payload["quality_artifact"]
    return {**artifact, "id": str(artifact["id"])}


def _waiting_rows(
    payload: dict[str, Any], characterization: dict[str, Any], refactor: dict[str, Any]
) -> frozenset[int]:
    blocks = set(refactor["policy_blocks"])
    if refactor["authorization"] is not None or "MISSING_OWNER_AUTHORIZATION" not in blocks:
        return frozenset()
    if blocks - {"MISSING_OWNER_AUTHORIZATION", "UNAUTHORIZED_CORRECTION"}:
        return frozenset()
    behavior_blocks = set(characterization["policy_blocks"])
    correction = characterization.get("correction")
    if behavior_blocks and (
        not isinstance(correction, dict)
        or correction["verification_blocks"]
        or behavior_blocks != set(correction["reconcilable_blocks"])
    ):
        return frozenset()
    allowed = blocks | behavior_blocks
    waiting: set[int] = set()
    for row in payload["entries"]:
        if row["result"] in {"PASS", "NOT_APPLICABLE_SHORT_TASK"}:
            continue
        if (
            row["standard"] not in {5, 6, 8}
            or row["result"] != "BLOCK"
            or row["technical_errors"]
            or not row["policy_blocks"]
            or set(row["policy_blocks"]) - allowed
        ):
            return frozenset()
        waiting.add(row["standard"])
    return frozenset(waiting)
