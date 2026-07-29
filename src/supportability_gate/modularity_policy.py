"""Prove new production locations are owned, justified, and fully governed."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from supportability_gate.architecture_policy import ArchitectureResult, ImportEdge
from supportability_gate.contract import Contract, ContractError, normalize_repository_path
from supportability_gate.function_changes import ChangedFileAssessment
from supportability_gate.review_evidence import ReviewEvidence

VAGUE_LOCATION_NAMES = frozenset({"common", "helpers", "misc", "stuff", "utils"})


@dataclass(frozen=True)
class LocationJustification:
    """One exact-path ownership claim for a new production source location."""

    path: str
    owner_path: str
    basis: str
    justification: str


@dataclass(frozen=True)
class LocationCoverage:
    """Executed gate coverage for one new production source location."""

    path: str
    adapters: tuple[str, ...]
    architecture: bool


@dataclass(frozen=True)
class ModularityResult:
    """Deterministic module ownership, coupling, and coverage evidence."""

    changed_paths: tuple[str, ...]
    new_paths: tuple[str, ...]
    justifications: tuple[LocationJustification, ...]
    coupling_edges: tuple[ImportEdge, ...]
    coverage: tuple[LocationCoverage, ...]
    blocks: tuple[str, ...]


def _changed_paths(assessments: tuple[ChangedFileAssessment, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.change.new_path
                for item in assessments
                if item.head_production and item.change.new_path and item.complexity_assessed
            }
        )
    )


def _new_paths(assessments: tuple[ChangedFileAssessment, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.change.new_path
                for item in assessments
                if item.head_production
                and item.change.new_path
                and item.complexity_assessed
                and (not item.base_production or item.change.old_path != item.change.new_path)
            }
        )
    )


def _justifications(review: ReviewEvidence | None) -> tuple[LocationJustification, ...]:
    raw = review.get("module_boundaries", []) if review else []
    if not isinstance(raw, list):
        return ()
    return tuple(
        LocationJustification(
            str(item["path"]),
            str(item["owner_path"]),
            str(item["basis"]),
            str(item["justification"]),
        )
        for item in raw
        if isinstance(item, dict)
    )


def _package(path: str, policy: Contract) -> str:
    root = max(
        (root for root in policy.production_paths if path == root or path.startswith(f"{root}/")),
        key=len,
    )
    relative = PurePosixPath(path).relative_to(root)
    return relative.parts[0] if len(relative.parts) > 1 else relative.stem


def _path_blocks(path: str) -> tuple[str, ...]:
    names = {part.lower() for part in PurePosixPath(path).parts[:-1]}
    names.add(PurePosixPath(path).stem.lower())
    return tuple(
        f"VAGUE_PRODUCTION_LOCATION:{path}:{name}" for name in sorted(names & VAGUE_LOCATION_NAMES)
    )


def _claim_blocks(
    policy: Contract,
    new_paths: tuple[str, ...],
    justifications: tuple[LocationJustification, ...],
    nodes: tuple[str, ...],
) -> tuple[str, ...]:
    blocks: list[str] = []
    claims = {item.path: item for item in justifications}
    for path in new_paths:
        claim = claims.get(path)
        if claim is None:
            blocks.append(f"MISSING_NEW_LOCATION_JUSTIFICATION:{path}")
            continue
        try:
            normalize_repository_path(claim.path, "module_boundaries.path")
            normalize_repository_path(claim.owner_path, "module_boundaries.owner_path")
        except ContractError:
            blocks.append(f"INVALID_NEW_LOCATION_JUSTIFICATION:{path}")
            continue
        if claim.owner_path not in nodes:
            blocks.append(f"UNRESOLVED_MODULE_OWNER:{path}:{claim.owner_path}")
        elif claim.owner_path != path and _package(claim.owner_path, policy) != _package(
            path, policy
        ):
            blocks.append(f"PARALLEL_PACKAGE:{path}:{claim.owner_path}")
    return tuple(blocks)


def evaluate_modularity(
    policy: Contract,
    assessments: tuple[ChangedFileAssessment, ...],
    review: ReviewEvidence | None,
    architecture: ArchitectureResult,
) -> ModularityResult:
    """Return deterministic exact-path modularity evidence without executing target code."""
    changed_paths = _changed_paths(assessments)
    new_paths = _new_paths(assessments)
    justifications = _justifications(review)
    coverage = tuple(
        LocationCoverage(
            path,
            tuple(sorted(gate.adapter for gate in policy.gates if gate.covers(path))),
            path in architecture.covered_paths,
        )
        for path in new_paths
    )
    blocks = [block for path in new_paths for block in _path_blocks(path)]
    blocks.extend(_claim_blocks(policy, new_paths, justifications, architecture.nodes))
    blocks.extend(
        f"NEW_LOCATION_GATE_COVERAGE:{item.path}"
        for item in coverage
        if len(item.adapters) != len(policy.gates) or not item.architecture
    )
    coupling = tuple(edge for edge in architecture.edges if edge.source in changed_paths)
    return ModularityResult(
        changed_paths,
        new_paths,
        justifications,
        coupling,
        coverage,
        tuple(sorted(blocks)),
    )
