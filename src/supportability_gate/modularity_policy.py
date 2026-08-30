"""Prove new production locations are owned, justified, and fully governed."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from supportability_gate.contract import ContractError, command_failed, normalize_repository_path

if TYPE_CHECKING:
    from supportability_gate.architecture_policy import ArchitectureResult, ImportEdge
    from supportability_gate.contract import Contract
    from supportability_gate.function_changes import ChangedFileAssessment
    from supportability_gate.quality_profile import QualityEvidence
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


def _package(path: str, production_paths: tuple[str, ...]) -> str:
    root = max(
        (root for root in production_paths if path == root or path.startswith(f"{root}/")),
        key=len,
    )
    relative = PurePosixPath(path).relative_to(root)
    return relative.parts[0] if len(relative.parts) > 1 else PurePosixPath(root).name


def _path_blocks(path: str) -> tuple[str, ...]:
    names = {part.lower() for part in PurePosixPath(path).parts[:-1]}
    names.add(PurePosixPath(path).stem.lower())
    return tuple(
        f"VAGUE_PRODUCTION_LOCATION:{path}:{name}" for name in sorted(names & VAGUE_LOCATION_NAMES)
    )


def _claim_blocks(
    production_paths: tuple[str, ...],
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
        elif claim.owner_path in new_paths and claim.owner_path != path:
            blocks.append(f"NEW_MODULE_OWNER_NOT_PREEXISTING:{path}:{claim.owner_path}")
        elif _package(claim.owner_path, production_paths) != _package(path, production_paths):
            blocks.append(f"PARALLEL_PACKAGE:{path}:{claim.owner_path}")
    return tuple(blocks)


def derive_modularity_blocks(
    production_paths: tuple[str, ...],
    new_paths: tuple[str, ...],
    justifications: tuple[LocationJustification, ...],
    nodes: tuple[str, ...],
    coverage: tuple[LocationCoverage, ...],
    required_gate_count: int,
    language: str = "python",
) -> tuple[str, ...]:
    """Derive canonical Gate 4 policy blocks from authenticated facts."""
    blocks = [block for path in new_paths for block in _path_blocks(path)]
    blocks.extend(_claim_blocks(production_paths, new_paths, justifications, nodes))
    blocks.extend(
        f"NEW_LOCATION_GATE_COVERAGE:{item.path}"
        for item in coverage
        if len(item.adapters)
        != (
            5
            if language == "mixed" and item.path.endswith((".py", ".pyi"))
            else 2
            if language == "mixed"
            else required_gate_count
        )
        or not item.architecture
    )
    return tuple(sorted(blocks))


def evaluate_modularity(
    policy: Contract,
    assessments: tuple[ChangedFileAssessment, ...],
    review: ReviewEvidence | None,
    architecture: ArchitectureResult,
    quality: QualityEvidence,
) -> ModularityResult:
    """Return deterministic exact-path modularity evidence without executing target code."""
    changed_paths = _changed_paths(assessments)
    new_paths = _new_paths(assessments)
    justifications = _justifications(review)
    coverage = tuple(
        LocationCoverage(
            path,
            tuple(
                sorted(
                    gate.adapter
                    for gate in policy.gates
                    for command in quality.commands
                    if gate.adapter == command.adapter
                    and gate.covers(path)
                    and not command_failed(
                        policy.language, command.adapter, command.executed, command.exit_code
                    )
                    and path in (*command.observed_paths, *command.zero_statement_paths)
                )
            ),
            path in architecture.covered_paths,
        )
        for path in new_paths
    )
    blocks = derive_modularity_blocks(
        policy.production_paths,
        new_paths,
        justifications,
        architecture.nodes,
        coverage,
        len(policy.gates),
        policy.language,
    )
    coupling = tuple(edge for edge in architecture.edges if edge.source in changed_paths)
    return ModularityResult(
        changed_paths,
        new_paths,
        justifications,
        coupling,
        coverage,
        blocks,
    )
