"""Write authoritative deterministic JSON and Markdown derived from it."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from supportability_gate.complexity_metrics import RuffCommandRecord, RuffDiagnostic
from supportability_gate.complexity_policy import FunctionDecision
from supportability_gate.function_changes import ChangedFileAssessment
from supportability_gate.git_changes import CommandRecord, RepositoryIdentity
from supportability_gate.review_evidence import ReviewEvidence

STANDARD_SHA256 = "81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2"


@dataclass(frozen=True)
class TechnicalError:
    """Stable fail-closed error evidence."""

    code: str
    message: str


@dataclass(frozen=True)
class EvaluationResult:
    """Complete typed evidence rendered by reporting only."""

    identity: RepositoryIdentity | None
    contract_path: str
    contract_blob_sha: str | None
    contract_sha256: str | None
    production_paths: tuple[str, ...]
    high_risk_paths: tuple[str, ...]
    gate_coverage: tuple[tuple[str, tuple[str, ...]], ...]
    changed_files: tuple[ChangedFileAssessment, ...]
    functions: tuple[FunctionDecision, ...]
    ruff_diagnostics: tuple[RuffDiagnostic, ...]
    technical_errors: tuple[TechnicalError, ...]
    policy_blocks: tuple[str, ...]
    overall_result: str
    tool_versions: dict[str, str]
    git_commands: tuple[CommandRecord, ...]
    ruff_commands: tuple[RuffCommandRecord, ...]
    review_evidence: ReviewEvidence | None = None
    language: str | None = None


def _span_metric(metric: object | None) -> dict[str, Any] | None:
    if metric is None:
        return None
    span = metric.span  # type: ignore[attr-defined]
    return {
        "complexity": metric.complexity,  # type: ignore[attr-defined]
        "end_line": span.end_line,
        "path": span.path,
        "qualified_name": span.qualified_name,
        "start_line": span.start_line,
    }


def _decision_payload(decision: FunctionDecision) -> dict[str, Any]:
    return {
        "base": _span_metric(decision.base),
        "decision": decision.decision,
        "ending_complexity": decision.head.complexity if decision.head else None,
        "head": _span_metric(decision.head),
        "next_target": decision.next_target,
        "remaining_debt": decision.remaining_debt,
        "remaining_gap": decision.remaining_debt,
        "starting_complexity": decision.base.complexity if decision.base else None,
        "state": decision.state,
    }


def _change_payload(assessment: ChangedFileAssessment) -> dict[str, Any]:
    return {
        "base_production": assessment.base_production,
        "changed_head_lines": list(assessment.changed_head_lines),
        "complexity_assessed": assessment.complexity_assessed,
        "head_production": assessment.head_production,
        "new_path": assessment.change.new_path,
        "old_path": assessment.change.old_path,
        "status": assessment.change.status,
    }


def _identity_payload(identity: RepositoryIdentity | None) -> dict[str, str | None]:
    return {
        "base_sha": identity.base_sha if identity else None,
        "base_tree_sha": identity.base_tree_sha if identity else None,
        "head_sha": identity.head_sha if identity else None,
        "head_tree_sha": identity.head_tree_sha if identity else None,
        "remote": identity.remote if identity else None,
    }


def result_payload(result: EvaluationResult) -> dict[str, Any]:
    """Convert typed evidence to the authoritative schema."""
    functions = [_decision_payload(item) for item in result.functions]
    touched = [item["head"]["qualified_name"] for item in functions if item["head"] is not None]
    renames = [
        {"new_path": item.change.new_path, "old_path": item.change.old_path}
        for item in result.changed_files
        if item.change.status == "RENAMED"
    ]
    commands = [asdict(item) for item in result.git_commands]
    commands.extend(asdict(item) for item in result.ruff_commands)
    identity = _identity_payload(result.identity)
    return {
        "base_contract_blob_sha": result.contract_blob_sha,
        "base_sha": identity["base_sha"],
        "base_tree_sha": identity["base_tree_sha"],
        "changed_files": [_change_payload(item) for item in result.changed_files],
        "commands": commands,
        "contract_path": result.contract_path,
        "contract_sha256": result.contract_sha256,
        "head_sha": identity["head_sha"],
        "head_tree_sha": identity["head_tree_sha"],
        "high_risk_paths": list(result.high_risk_paths),
        "language": result.language,
        "gate_coverage": [
            {"adapter": adapter, "paths": list(paths)} for adapter, paths in result.gate_coverage
        ],
        "functions": functions,
        "overall_result": result.overall_result,
        "policy_blocks": list(result.policy_blocks),
        "production_paths": list(result.production_paths),
        "rename_bindings": renames,
        "repository_remote": identity["remote"],
        "review_evidence": result.review_evidence,
        "review_evidence_path": ".supportability-review.toml",
        "ruff_diagnostics": [asdict(item) for item in result.ruff_diagnostics],
        "schema_version": "1.0",
        "standard_sha256": STANDARD_SHA256,
        "technical_errors": [asdict(item) for item in result.technical_errors],
        "tool_versions": dict(sorted(result.tool_versions.items())),
        "touched_qualified_functions": sorted(touched),
    }


def _escape(value: object) -> str:
    return str(value).replace("|", "\\|")


def markdown_from_json(payload: dict[str, Any]) -> str:
    """Derive Markdown only from the authoritative JSON object."""
    lines = [
        "# Supportability Complexity Result",
        "",
        f"- Overall result: `{payload['overall_result']}`",
        f"- Repository: `{payload['repository_remote']}`",
        f"- Base: `{payload['base_sha']}`",
        f"- Head: `{payload['head_sha']}`",
        "",
        "| Function | Start | End | Gap | Next target | Decision |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in payload["functions"]:
        metric = item["head"] or item["base"]
        lines.append(
            "| {name} | {base} | {head} | {gap} | {target} | {decision} |".format(
                name=_escape(metric["qualified_name"]),
                base=item["base"]["complexity"] if item["base"] else "—",
                head=item["head"]["complexity"] if item["head"] else "—",
                gap=item["remaining_gap"] if item["remaining_gap"] is not None else "—",
                target=item["next_target"] if item["next_target"] is not None else "—",
                decision=item["decision"],
            )
        )
    if payload["technical_errors"]:
        lines.extend(["", "## Technical errors", ""])
        lines.extend(
            f"- `{item['code']}`: {item['message']}" for item in payload["technical_errors"]
        )
    if payload["policy_blocks"]:
        lines.extend(["", "## Policy blocks", ""])
        lines.extend(f"- `{item}`" for item in payload["policy_blocks"])
    if payload["review_evidence"]:
        lines.extend(
            [
                "",
                "## Structured review evidence",
                "",
                "```json",
                json.dumps(
                    payload["review_evidence"], ensure_ascii=False, indent=2, sort_keys=True
                ),
                "```",
            ]
        )
    lines.extend(["", "Derived from `complexity-result.json`.", ""])
    return "\n".join(lines)


def write_reports(result: EvaluationResult, output_directory: Path) -> bytes:
    """Write byte-stable JSON then derive Markdown from parsed JSON."""
    output_directory.mkdir(parents=True, exist_ok=True)
    payload = result_payload(result)
    json_bytes = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    (output_directory / "complexity-result.json").write_bytes(json_bytes)
    authoritative = json.loads(json_bytes)
    (output_directory / "complexity-result.md").write_bytes(
        markdown_from_json(authoritative).encode("utf-8")
    )
    return json_bytes
