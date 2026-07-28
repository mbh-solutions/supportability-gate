"""Command-line orchestration for deterministic supportability evidence."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from supportability_gate import (
    __version__,
    complexity_metrics,
    complexity_policy,
    contract,
    function_changes,
    gate_policy,
    git_changes,
    reporting,
    review_evidence,
)


@dataclass(frozen=True)
class _AnalyzedChanges:
    assessments: tuple[function_changes.ChangedFileAssessment, ...]
    analyses: tuple[function_changes.FileFunctionAnalysis, ...]
    head_sources: dict[str, bytes]


def _is_profile_source(path: str | None, language: str) -> bool:
    if path is None:
        return False
    return (
        path.endswith((".py", ".pyi"))
        if language == "python"
        else path.endswith((".cts", ".mts", ".ts", ".tsx"))
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-gate")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate-complexity")
    evaluate.add_argument("--repository", required=True)
    evaluate.add_argument("--base-ref", required=True)
    evaluate.add_argument("--head-ref", required=True)
    evaluate.add_argument("--contract-path", required=True)
    evaluate.add_argument("--output-directory", required=True)
    return parser


def _classify_changes(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    changes: tuple[git_changes.ChangedPath, ...],
    records: list[git_changes.CommandRecord],
) -> tuple[function_changes.ChangedFileAssessment, ...]:
    assessments: list[function_changes.ChangedFileAssessment] = []
    for change in changes:
        base_production = bool(change.old_path and policy.is_production_path(change.old_path))
        head_production = bool(change.new_path and policy.is_production_path(change.new_path))
        profile_source = bool(
            (base_production and _is_profile_source(change.old_path, policy.language))
            or (head_production and _is_profile_source(change.new_path, policy.language))
        )
        lines = (
            git_changes.changed_head_lines(
                repository,
                identity.base_sha,
                identity.head_sha,
                change.new_path,
                records,
            )
            if profile_source and change.new_path
            else ()
        )
        assessments.append(
            function_changes.ChangedFileAssessment(
                change,
                base_production,
                head_production,
                profile_source,
                lines,
            )
        )
    return tuple(assessments)


def _regular_source(
    repository: Path,
    commit: str,
    path: str | None,
    production: bool,
    language: str,
    records: list[git_changes.CommandRecord],
) -> bytes | None:
    if not production or not _is_profile_source(path, language):
        return None
    if path is None:
        return None
    return git_changes.read_regular_blob(repository, commit, path, records).content


def _analyze_changes(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    records: list[git_changes.CommandRecord],
) -> _AnalyzedChanges:
    analyses: list[function_changes.FileFunctionAnalysis] = []
    head_sources: dict[str, bytes] = {}
    for assessment in assessments:
        if not assessment.complexity_assessed:
            continue
        change = assessment.change
        base_content = _regular_source(
            repository,
            identity.base_sha,
            change.old_path,
            assessment.base_production,
            policy.language,
            records,
        )
        head_content = _regular_source(
            repository,
            identity.head_sha,
            change.new_path,
            assessment.head_production,
            policy.language,
            records,
        )
        analysis = function_changes.analyze_file(
            assessment, base_content, head_content, policy.language
        )
        analyses.append(analysis)
        if change.new_path and head_content is not None:
            head_sources[change.new_path] = head_content
    return _AnalyzedChanges(assessments, tuple(analyses), head_sources)


def _all_definitions(
    analyses: tuple[function_changes.FileFunctionAnalysis, ...],
    side: str,
) -> tuple[function_changes.FunctionDefinition, ...]:
    definitions: list[function_changes.FunctionDefinition] = []
    for analysis in analyses:
        parsed = analysis.base if side == "base" else analysis.head
        if parsed:
            definitions.extend(parsed.functions)
    return tuple(definitions)


def _all_deltas(
    analyses: tuple[function_changes.FileFunctionAnalysis, ...],
) -> tuple[function_changes.FunctionDelta, ...]:
    return tuple(delta for analysis in analyses for delta in analysis.deltas)


def _versions(identity: git_changes.RepositoryIdentity | None) -> dict[str, str]:
    versions = complexity_metrics.tool_versions()
    if identity:
        versions["git"] = identity.git_version
    return versions


def _gate_coverage(policy: contract.Contract) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple((gate.adapter, gate.paths) for gate in policy.gates)


def _result(
    identity: git_changes.RepositoryIdentity,
    contract_path: str,
    blob: git_changes.GitBlob,
    policy: contract.Contract,
    candidate_policy: contract.Contract | None,
    analyzed: _AnalyzedChanges,
    records: list[git_changes.CommandRecord],
    ruff_records: list[complexity_metrics.RuffCommandRecord],
    structured_review: review_evidence.ReviewEvidence | None,
    review_blocks: tuple[str, ...],
) -> reporting.EvaluationResult:
    if any(
        contract_path in {item.change.old_path, item.change.new_path}
        for item in analyzed.assessments
    ):
        return reporting.EvaluationResult(
            identity,
            contract_path,
            blob.object_sha,
            policy.sha256,
            policy.production_paths,
            policy.high_risk_paths,
            _gate_coverage(policy),
            analyzed.assessments,
            (),
            (),
            (),
            (
                "CANDIDATE_CONTRACT_CHANGE",
                *gate_policy.contract_change_blocks(policy, candidate_policy),
            ),
            "BLOCK",
            _versions(identity),
            tuple(records),
            (),
            structured_review,
            policy.language,
        )
    policy_blocks = (*gate_policy.evaluate_contract(policy, analyzed.assessments), *review_blocks)
    if policy_blocks:
        return reporting.EvaluationResult(
            identity,
            contract_path,
            blob.object_sha,
            policy.sha256,
            policy.production_paths,
            policy.high_risk_paths,
            _gate_coverage(policy),
            analyzed.assessments,
            (),
            (),
            (),
            policy_blocks,
            "BLOCK",
            _versions(identity),
            tuple(records),
            (),
            structured_review,
            policy.language,
        )
    base_definitions = _all_definitions(analyzed.analyses, "base")
    head_definitions = _all_definitions(analyzed.analyses, "head")
    base_metrics = complexity_metrics.measure_definitions(base_definitions, policy.language)
    head_metrics = complexity_metrics.measure_definitions(head_definitions, policy.language)
    ruff = (
        complexity_metrics.run_ruff(analyzed.head_sources, head_definitions)
        if policy.language == "python"
        else complexity_metrics.RuffResult((), None)
    )
    if ruff.command:
        ruff_records.append(ruff.command)
    if policy.language == "python":
        complexity_metrics.verify_ruff_parity(head_metrics, ruff.diagnostics)
    decisions = complexity_policy.decide_functions(
        policy,
        _all_deltas(analyzed.analyses),
        base_metrics,
        head_metrics,
    )
    complexity_policy.validate_reporting(decisions, policy.maximum)
    overall = "BLOCK" if any(item.decision == "BLOCK" for item in decisions) else "PASS"
    return reporting.EvaluationResult(
        identity,
        contract_path,
        blob.object_sha,
        policy.sha256,
        policy.production_paths,
        policy.high_risk_paths,
        _gate_coverage(policy),
        analyzed.assessments,
        decisions,
        ruff.diagnostics,
        (),
        (),
        overall,
        _versions(identity),
        tuple(records),
        tuple(ruff_records),
        structured_review,
        policy.language,
    )


def _read_review_evidence(
    repository: Path,
    head_sha: str,
    records: list[git_changes.CommandRecord],
) -> tuple[review_evidence.ReviewEvidence | None, tuple[str, ...]]:
    try:
        blob = git_changes.read_regular_blob(
            repository,
            head_sha,
            review_evidence.REVIEW_EVIDENCE_PATH,
            records,
        )
    except git_changes.GitError as error:
        if error.code == "MISSING_BLOB":
            return review_evidence.evaluate_review_evidence(None)
        if error.code == "SYMLINK_OR_NONFILE":
            return None, ("MALFORMED_REVIEW_EVIDENCE:document",)
        raise
    return review_evidence.evaluate_review_evidence(blob.content)


def _technical_result(
    identity: git_changes.RepositoryIdentity | None,
    contract_path: str,
    records: list[git_changes.CommandRecord],
    ruff_records: list[complexity_metrics.RuffCommandRecord],
    blob: git_changes.GitBlob | None,
    policy: contract.Contract | None,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    error: Exception,
) -> reporting.EvaluationResult:
    code = getattr(error, "code", "UNEXPECTED_ERROR")
    message = str(error) if code != "UNEXPECTED_ERROR" else type(error).__name__
    return reporting.EvaluationResult(
        identity,
        contract_path,
        blob.object_sha if blob else None,
        contract.content_sha256(blob.content) if blob else None,
        policy.production_paths if policy else (),
        policy.high_risk_paths if policy else (),
        _gate_coverage(policy) if policy else (),
        assessments,
        (),
        (),
        (reporting.TechnicalError(str(code), message),),
        (),
        "TECHNICAL_FAILURE",
        _versions(identity),
        tuple(records),
        tuple(ruff_records),
        None,
        policy.language if policy else None,
    )


def _evaluate(arguments: argparse.Namespace) -> reporting.EvaluationResult:
    records: list[git_changes.CommandRecord] = []
    ruff_records: list[complexity_metrics.RuffCommandRecord] = []
    identity: git_changes.RepositoryIdentity | None = None
    blob: git_changes.GitBlob | None = None
    policy: contract.Contract | None = None
    assessments: tuple[function_changes.ChangedFileAssessment, ...] = ()
    candidate_policy: contract.Contract | None = None
    structured_review: review_evidence.ReviewEvidence | None = None
    review_blocks: tuple[str, ...] = ()
    contract_path = str(arguments.contract_path)
    try:
        contract_path = contract.validate_contract_path(contract_path)
        repository = git_changes.validate_repository(Path(arguments.repository), records)
        identity = git_changes.inspect_repository(
            repository,
            str(arguments.base_ref),
            str(arguments.head_ref),
            records,
        )
        blob = git_changes.read_regular_blob(
            repository,
            identity.base_sha,
            contract_path,
            records,
        )
        policy = contract.parse_contract(blob.content)
        changes = git_changes.changed_paths(
            repository,
            identity.base_sha,
            identity.head_sha,
            records,
        )
        assessments = _classify_changes(repository, identity, policy, changes, records)
        structured_review, review_blocks = _read_review_evidence(
            repository,
            identity.head_sha,
            records,
        )
        contract_changed = any(
            contract_path in {item.change.old_path, item.change.new_path} for item in assessments
        )
        if contract_changed and any(item.change.new_path == contract_path for item in assessments):
            try:
                candidate_blob = git_changes.read_regular_blob(
                    repository,
                    identity.head_sha,
                    contract_path,
                    records,
                )
                candidate_policy = contract.parse_contract(candidate_blob.content)
            except (contract.ContractError, git_changes.GitError):
                candidate_policy = None
        analyzed = (
            _AnalyzedChanges(assessments, (), {})
            if contract_changed
            else _analyze_changes(repository, identity, policy, assessments, records)
        )
        return _result(
            identity,
            contract_path,
            blob,
            policy,
            candidate_policy,
            analyzed,
            records,
            ruff_records,
            structured_review,
            review_blocks,
        )
    except Exception as error:  # fail closed at the CLI trust boundary
        return _technical_result(
            identity,
            contract_path,
            records,
            ruff_records,
            blob,
            policy,
            assessments,
            error,
        )


def main(argv: list[str] | None = None) -> int:
    """Run the supportability gate CLI."""
    arguments = _parser().parse_args(argv)
    output_directory = Path(arguments.output_directory)
    if not output_directory.is_absolute():
        print("TECHNICAL_FAILURE")
        return 2
    result = _evaluate(arguments)
    try:
        reporting.write_reports(result, output_directory)
    except OSError:
        print("TECHNICAL_FAILURE")
        return 2
    print(result.overall_result)
    return {"PASS": 0, "BLOCK": 1, "TECHNICAL_FAILURE": 2}[result.overall_result]
