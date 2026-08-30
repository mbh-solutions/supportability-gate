"""Command-line orchestration for deterministic supportability evidence."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

from supportability_gate import (
    __version__,
    architecture_policy,
    complexity_metrics,
    complexity_policy,
    contract,
    function_changes,
    gate_policy,
    git_changes,
    modularity_policy,
    quality_profile,
    refactor_targets,
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
    suffixes = {
        "python": (".py", ".pyi"),
        "typescript": (".cts", ".mts", ".ts", ".tsx"),
        "mixed": (".cts", ".mts", ".py", ".pyi", ".ts", ".tsx"),
    }
    return path.endswith(suffixes[language])


def _source_language(path: str) -> str:
    return "python" if path.endswith((".py", ".pyi")) else "typescript"


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
    evaluate.add_argument("--quality-evidence", required=True)
    evaluate.add_argument("--quality-repository", required=True)
    evaluate.add_argument("--quality-repository-id", required=True)
    evaluate.add_argument("--quality-run-id", required=True)
    evaluate.add_argument("--quality-run-attempt", required=True)
    evaluate.add_argument("--quality-job", required=True)
    evaluate.add_argument("--quality-artifact-id", required=True)
    evaluate.add_argument("--quality-artifact-digest", required=True)
    evaluate.add_argument("--quality-artifact-metadata", required=True)
    evaluate.add_argument("--quality-capture-sha256", required=True)
    evaluate.add_argument("--workflow-sha", required=True)
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
        short_document = bool(
            change.status == "ADDED"
            and change.old_path is None
            and change.new_path
            and (
                change.new_path == "README.md"
                or (change.new_path.startswith("docs/") and change.new_path.endswith(".md"))
            )
        )
        lines = (
            git_changes.changed_head_lines(
                repository,
                identity.base_sha,
                identity.head_sha,
                change.new_path,
                records,
            )
            if (profile_source or short_document) and change.new_path
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
        path = change.new_path or change.old_path
        if path is None:
            continue
        analysis = function_changes.analyze_file(
            assessment, base_content, head_content, _source_language(path)
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


def _architecture_inputs(
    repository: Path,
    head_sha: str,
    policy: contract.Contract,
    records: list[git_changes.CommandRecord],
) -> tuple[dict[str, bytes], bytes | None]:
    suffixes = quality_profile.SOURCE_SUFFIXES[policy.language]
    roots = (
        (*policy.production_paths, "tsconfig.json")
        if "typescript" in policy.languages
        else policy.production_paths
    )
    blobs = git_changes.list_regular_blobs(repository, head_sha, roots, records)
    contents = {
        item.path: git_changes.read_regular_blob(repository, head_sha, item.path, records).content
        for item in blobs
        if item.path.endswith(suffixes) or item.path == "tsconfig.json"
    }
    sources = {path: content for path, content in contents.items() if path.endswith(suffixes)}
    return sources, contents.get("tsconfig.json")


def _complexity_evidence(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    records: list[git_changes.CommandRecord],
    ruff_records: list[complexity_metrics.RuffCommandRecord],
    errors: list[Exception],
) -> tuple[
    tuple[complexity_policy.FunctionDecision, ...],
    tuple[complexity_metrics.RuffDiagnostic, ...],
]:
    try:
        analyzed = _analyze_changes(repository, identity, policy, assessments, records)
        base_definitions = _all_definitions(analyzed.analyses, "base")
        head_definitions = _all_definitions(analyzed.analyses, "head")
        base_metrics = tuple(
            metric
            for language in policy.languages
            for metric in complexity_metrics.measure_definitions(
                tuple(
                    item
                    for item in base_definitions
                    if _source_language(item.span.path) == language
                ),
                language,
            )
        )
        head_metrics = tuple(
            metric
            for language in policy.languages
            for metric in complexity_metrics.measure_definitions(
                tuple(
                    item
                    for item in head_definitions
                    if _source_language(item.span.path) == language
                ),
                language,
            )
        )
        python_definitions = tuple(
            item for item in head_definitions if _source_language(item.span.path) == "python"
        )
        ruff = (
            complexity_metrics.run_ruff(
                {
                    path: content
                    for path, content in analyzed.head_sources.items()
                    if _source_language(path) == "python"
                },
                python_definitions,
            )
            if "python" in policy.languages
            else complexity_metrics.RuffResult((), None)
        )
        if ruff.command:
            ruff_records.append(ruff.command)
        if "python" in policy.languages:
            complexity_metrics.verify_ruff_parity(
                tuple(item for item in head_metrics if item.span.path.endswith((".py", ".pyi"))),
                ruff.diagnostics,
            )
        decisions = complexity_policy.decide_functions(
            policy,
            _all_deltas(analyzed.analyses),
            base_metrics,
            head_metrics,
        )
        complexity_policy.validate_reporting(decisions, policy.maximum)
        return decisions, ruff.diagnostics
    except (function_changes.PythonSourceError, git_changes.GitError) as error:
        code = (
            "COMPLEXITY_SOURCE_UNAVAILABLE"
            if isinstance(error, git_changes.GitError)
            else f"COMPLEXITY_{error.code}"
        )
        errors.append(function_changes.PythonSourceError(code, str(error)))
    except (complexity_metrics.MetricsError, complexity_policy.ComplexityPolicyError) as error:
        errors.append(error)
    return (), ()


def _architecture_evidence(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    records: list[git_changes.CommandRecord],
    errors: list[Exception],
) -> architecture_policy.ArchitectureResult | None:
    try:
        sources, typescript_config = _architecture_inputs(
            repository, identity.head_sha, policy, records
        )
        results = []
        for language in policy.languages:
            adapter = architecture_policy.ARCHITECTURE_ADAPTERS[language]
            gate = next((item for item in policy.gates if item.adapter == adapter), None)
            profile_sources = {
                path: content
                for path, content in sources.items()
                if _source_language(path) == language
            }
            profile_policy = replace(
                policy,
                language=language,
                languages=(language,),
                adapter=contract.COMPLEXITY_ADAPTERS[language],
            )
            results.append(
                architecture_policy.evaluate_architecture(
                    profile_policy, profile_sources, gate, typescript_config
                )
            )
        return architecture_policy.ArchitectureResult(
            "+".join(item.adapter for item in results),
            all(item.executed for item in results),
            tuple(sorted(path for item in results for path in item.covered_paths)),
            tuple(sorted(path for item in results for path in item.nodes)),
            tuple(
                sorted(
                    (edge for item in results for edge in item.edges),
                    key=lambda edge: (edge.source, edge.line, edge.specifier),
                )
            ),
            tuple(sorted(block for item in results for block in item.blocks)),
        )
    except (function_changes.PythonSourceError, git_changes.GitError) as error:
        code = (
            error.code if error.code.startswith("ARCHITECTURE_") else f"ARCHITECTURE_{error.code}"
        )
        errors.append(function_changes.PythonSourceError(code, str(error)))
        return None


def _quality_evidence(
    arguments: argparse.Namespace,
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    records: list[git_changes.CommandRecord],
    errors: list[Exception],
) -> tuple[quality_profile.QualityEvidence | None, tuple[str, ...]]:
    try:
        production = quality_profile.production_files(
            repository, identity.head_sha, policy, records
        )
        sources = quality_profile.source_files(production, policy.language)
        receipts = quality_profile.asset_receipts(
            repository, identity.head_sha, production, sources, records
        )
        tests = quality_profile.test_files(repository, identity.head_sha, policy.language, records)
        path = Path(arguments.quality_evidence)
        if not path.is_absolute():
            raise quality_profile.QualityProfileError(
                "RELATIVE_QUALITY_EVIDENCE", "quality evidence path must be absolute"
            )
        evidence = quality_profile.verify_evidence_binding(
            path,
            metadata_path=Path(arguments.quality_artifact_metadata),
            repository=str(arguments.quality_repository),
            repository_id=str(arguments.quality_repository_id),
            run_id=str(arguments.quality_run_id),
            run_attempt=str(arguments.quality_run_attempt),
            job=str(arguments.quality_job),
            artifact_id=str(arguments.quality_artifact_id),
            artifact_digest=str(arguments.quality_artifact_digest),
            capture_sha256=str(arguments.quality_capture_sha256),
        )
        blocks = quality_profile.evidence_blocks(
            evidence,
            policy,
            identity,
            assessments,
            production,
            sources,
            receipts,
            tests,
            str(arguments.workflow_sha),
        )
        return evidence, blocks
    except quality_profile.QualityProfileError as error:
        errors.append(error)
    except git_changes.GitError as error:
        errors.append(quality_profile.QualityProfileError(f"QUALITY_{error.code}", str(error)))
    return None, ()


def _modularity_evidence(
    policy: contract.Contract,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    structured_review: review_evidence.ReviewEvidence | None,
    architecture: architecture_policy.ArchitectureResult | None,
    quality: quality_profile.QualityEvidence | None,
) -> modularity_policy.ModularityResult | None:
    if architecture is None or quality is None:
        return None
    return modularity_policy.evaluate_modularity(
        policy, assessments, structured_review, architecture, quality
    )


def _contract_blocks(
    contract_path: str,
    policy: contract.Contract,
    candidate_policy: contract.Contract | None,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
) -> tuple[str, ...]:
    expansion = gate_policy.is_profile_expansion(policy, candidate_policy)
    candidate = (
        (
            *(() if expansion else ("CANDIDATE_CONTRACT_CHANGE",)),
            *gate_policy.contract_change_blocks(policy, candidate_policy),
        )
        if any(
            contract_path in {item.change.old_path, item.change.new_path} for item in assessments
        )
        else ()
    )
    effective = candidate_policy if expansion and candidate_policy is not None else policy
    return (*candidate, *gate_policy.evaluate_contract(effective, assessments))


def _result(
    identity: git_changes.RepositoryIdentity,
    contract_path: str,
    blob: git_changes.GitBlob,
    policy: contract.Contract,
    contract_blocks: tuple[str, ...],
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    responsibility_targets: tuple[str, ...],
    unbounded_production_paths: tuple[str, ...],
    decisions: tuple[complexity_policy.FunctionDecision, ...],
    ruff_diagnostics: tuple[complexity_metrics.RuffDiagnostic, ...],
    records: list[git_changes.CommandRecord],
    ruff_records: list[complexity_metrics.RuffCommandRecord],
    structured_review: review_evidence.ReviewEvidence | None,
    review_blocks: tuple[str, ...],
    review_binding: dict[str, object],
    architecture: architecture_policy.ArchitectureResult,
    modularity: modularity_policy.ModularityResult,
    quality: quality_profile.QualityEvidence,
    quality_blocks: tuple[str, ...],
) -> reporting.EvaluationResult:
    policy_blocks = (
        *contract_blocks,
        *architecture.blocks,
        *modularity.blocks,
        *review_blocks,
        *quality_blocks,
    )
    overall = (
        "BLOCK" if policy_blocks or any(item.decision == "BLOCK" for item in decisions) else "PASS"
    )
    return reporting.EvaluationResult(
        identity,
        contract_path,
        blob.object_sha,
        policy.sha256,
        policy.production_paths,
        policy.high_risk_paths,
        _gate_coverage(policy),
        assessments,
        responsibility_targets,
        unbounded_production_paths,
        decisions,
        ruff_diagnostics,
        (),
        policy_blocks,
        overall,
        _versions(identity),
        tuple(records),
        tuple(ruff_records),
        structured_review,
        policy.language,
        architecture,
        modularity,
        quality,
        review_binding,
    )


def _separation_boundaries(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[str, str, str], ...]:
    boundaries: set[tuple[str, str, str]] = set()
    for assessment in assessments:
        if not assessment.complexity_assessed:
            continue
        change = assessment.change
        if change.old_path is None and change.new_path:
            path = change.new_path
            content = _regular_source(
                repository,
                identity.head_sha,
                change.new_path,
                assessment.head_production,
                policy.language,
                records,
            )
            if content is None:
                continue
            spans = function_changes.responsibility_spans(
                path, content, set(range(1, len(content.splitlines()) + 1))
            )
        elif change.new_path is None and change.old_path:
            path = change.old_path
            content = _regular_source(
                repository,
                identity.base_sha,
                change.old_path,
                assessment.base_production,
                policy.language,
                records,
            )
            if content is None:
                continue
            spans = function_changes.responsibility_spans(
                path, content, set(range(1, len(content.splitlines()) + 1))
            )
        elif change.old_path != change.new_path:
            base = _regular_source(
                repository,
                identity.base_sha,
                change.old_path,
                assessment.base_production,
                policy.language,
                records,
            )
            head = _regular_source(
                repository,
                identity.head_sha,
                change.new_path,
                assessment.head_production,
                policy.language,
                records,
            )
            base_spans = (
                function_changes.responsibility_spans(
                    change.old_path or "", base, set(range(1, len(base.splitlines()) + 1))
                )
                if base is not None
                else ()
            )
            head_spans = (
                function_changes.responsibility_spans(
                    change.new_path or "", head, set(range(1, len(head.splitlines()) + 1))
                )
                if head is not None
                else ()
            )
            spans = (*base_spans, *head_spans)
            boundaries.update((change.old_path or "", span.kind, span.name) for span in base_spans)
            boundaries.update((change.new_path or "", span.kind, span.name) for span in head_spans)
            continue
        else:
            retained_path = change.new_path or change.old_path
            if retained_path is None:
                continue
            path = retained_path
            base = _regular_source(
                repository,
                identity.base_sha,
                path,
                assessment.base_production,
                policy.language,
                records,
            )
            head = _regular_source(
                repository,
                identity.head_sha,
                path,
                assessment.head_production,
                policy.language,
                records,
            )
            base_lines = set(
                git_changes.changed_base_lines(
                    repository, identity.base_sha, identity.head_sha, path, records
                )
            )
            head_lines = set(
                git_changes.changed_head_lines(
                    repository,
                    identity.base_sha,
                    identity.head_sha,
                    path,
                    records,
                    include_deletion_anchor=False,
                )
            )
            if base is None or head is None:
                continue
            surviving, deleted = function_changes.changed_responsibility_spans(
                path, base, head, base_lines, head_lines
            )
            spans = (*surviving, *deleted)
        boundaries.update((path, span.kind, span.name) for span in spans)
    return tuple(sorted(boundaries))


def _read_review_evidence(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    records: list[git_changes.CommandRecord],
    errors: list[Exception],
) -> tuple[review_evidence.ReviewEvidence | None, tuple[str, ...], dict[str, object]]:
    try:
        expected_boundaries = _separation_boundaries(
            repository, identity, policy, assessments, records
        )
    except (function_changes.PythonSourceError, git_changes.GitError) as error:
        errors.append(
            function_changes.PythonSourceError("SEPARATION_BOUNDARY_DERIVATION_FAILURE", str(error))
        )
        expected_boundaries = None
    try:
        base_blob = git_changes.read_regular_blob(
            repository,
            identity.base_sha,
            review_evidence.REVIEW_EVIDENCE_PATH,
            records,
        )
    except git_changes.GitError as error:
        if error.code != "MISSING_BLOB":
            errors.append(
                function_changes.PythonSourceError(
                    "REVIEW_EVIDENCE_BINDING_UNAVAILABLE", str(error)
                )
            )
        base_blob = None
    try:
        head_blob = git_changes.read_regular_blob(
            repository,
            identity.head_sha,
            review_evidence.REVIEW_EVIDENCE_PATH,
            records,
        )
    except git_changes.GitError as error:
        if error.code == "MISSING_BLOB":
            review, blocks = review_evidence.evaluate_review_evidence(None, ())
            return review, blocks, {"base": _review_binding(base_blob), "head": None}
        if error.code == "SYMLINK_OR_NONFILE":
            return (
                None,
                ("MALFORMED_REVIEW_EVIDENCE:document",),
                {
                    "base": _review_binding(base_blob),
                    "head": None,
                },
            )
        errors.append(function_changes.PythonSourceError("REVIEW_EVIDENCE_UNAVAILABLE", str(error)))
        return None, (), {"base": _review_binding(base_blob), "head": None}
    review, blocks = review_evidence.evaluate_review_evidence(
        head_blob.content, expected_boundaries
    )
    return (
        review,
        blocks,
        {
            "base": _review_binding(base_blob),
            "head": _review_binding(head_blob),
        },
    )


def _review_binding(blob: git_changes.GitBlob | None) -> dict[str, str] | None:
    if blob is None:
        return None
    return {
        "blob_sha": blob.object_sha,
        "sha256": hashlib.sha256(blob.content).hexdigest(),
    }


def _refactor_target_evidence(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    changes: tuple[git_changes.ChangedPath, ...],
    records: list[git_changes.CommandRecord],
    errors: list[Exception],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        return refactor_targets.derive(repository, identity, policy, changes, records)
    except git_changes.GitError as error:
        errors.append(
            function_changes.PythonSourceError("REFACTOR_TARGET_DERIVATION_FAILURE", str(error))
        )
        return (), ()


def _technical_result(
    identity: git_changes.RepositoryIdentity | None,
    contract_path: str,
    records: list[git_changes.CommandRecord],
    ruff_records: list[complexity_metrics.RuffCommandRecord],
    blob: git_changes.GitBlob | None,
    policy: contract.Contract | None,
    assessments: tuple[function_changes.ChangedFileAssessment, ...],
    responsibility_targets: tuple[str, ...],
    unbounded_production_paths: tuple[str, ...],
    decisions: tuple[complexity_policy.FunctionDecision, ...],
    ruff_diagnostics: tuple[complexity_metrics.RuffDiagnostic, ...],
    contract_blocks: tuple[str, ...],
    structured_review: review_evidence.ReviewEvidence | None,
    review_blocks: tuple[str, ...],
    review_binding: dict[str, object],
    architecture: architecture_policy.ArchitectureResult | None,
    modularity: modularity_policy.ModularityResult | None,
    quality: quality_profile.QualityEvidence | None,
    quality_blocks: tuple[str, ...],
    errors: tuple[Exception, ...],
) -> reporting.EvaluationResult:
    technical_errors: list[reporting.TechnicalError] = []
    for error in errors:
        code = getattr(error, "code", "UNEXPECTED_ERROR")
        message = str(error) if code != "UNEXPECTED_ERROR" else type(error).__name__
        technical_errors.append(reporting.TechnicalError(str(code), message))
    policy_blocks = (
        *contract_blocks,
        *(architecture.blocks if architecture else ()),
        *(modularity.blocks if modularity else ()),
        *review_blocks,
        *quality_blocks,
    )
    return reporting.EvaluationResult(
        identity,
        contract_path,
        blob.object_sha if blob else None,
        contract.content_sha256(blob.content) if blob else None,
        policy.production_paths if policy else (),
        policy.high_risk_paths if policy else (),
        _gate_coverage(policy) if policy else (),
        assessments,
        responsibility_targets,
        unbounded_production_paths,
        decisions,
        ruff_diagnostics,
        tuple(technical_errors),
        policy_blocks,
        "TECHNICAL_FAILURE",
        _versions(identity),
        tuple(records),
        tuple(ruff_records),
        structured_review,
        policy.language if policy else None,
        architecture,
        modularity,
        quality,
        review_binding,
    )


def _evaluate(arguments: argparse.Namespace) -> reporting.EvaluationResult:
    records: list[git_changes.CommandRecord] = []
    ruff_records: list[complexity_metrics.RuffCommandRecord] = []
    identity: git_changes.RepositoryIdentity | None = None
    blob: git_changes.GitBlob | None = None
    policy: contract.Contract | None = None
    assessments: tuple[function_changes.ChangedFileAssessment, ...] = ()
    responsibility_targets: tuple[str, ...] = ()
    unbounded_production_paths: tuple[str, ...] = ()
    decisions: tuple[complexity_policy.FunctionDecision, ...] = ()
    ruff_diagnostics: tuple[complexity_metrics.RuffDiagnostic, ...] = ()
    contract_blocks: tuple[str, ...] = ()
    structured_review: review_evidence.ReviewEvidence | None = None
    review_blocks: tuple[str, ...] = ()
    review_binding: dict[str, object] = {"base": None, "head": None}
    architecture: architecture_policy.ArchitectureResult | None = None
    modularity: modularity_policy.ModularityResult | None = None
    quality: quality_profile.QualityEvidence | None = None
    quality_blocks: tuple[str, ...] = ()
    evidence_errors: list[Exception] = []
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
        base_policy = contract.parse_contract(blob.content)
        policy = base_policy
        changes = git_changes.changed_paths(
            repository,
            identity.base_sha,
            identity.head_sha,
            records,
        )
        candidate_policy: contract.Contract | None = None
        contract_changed = any(contract_path in {item.old_path, item.new_path} for item in changes)
        if contract_changed and any(item.new_path == contract_path for item in changes):
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
        if candidate_policy is not None and gate_policy.is_profile_expansion(
            base_policy, candidate_policy
        ):
            policy = candidate_policy
        assessments = _classify_changes(repository, identity, policy, changes, records)
        responsibility_targets, unbounded_production_paths = _refactor_target_evidence(
            repository, identity, policy, changes, records, evidence_errors
        )
        structured_review, review_blocks, review_binding = _read_review_evidence(
            repository,
            identity,
            policy,
            assessments,
            records,
            evidence_errors,
        )
        contract_blocks = _contract_blocks(
            contract_path, base_policy, candidate_policy, assessments
        )
        decisions, ruff_diagnostics = _complexity_evidence(
            repository,
            identity,
            policy,
            assessments,
            records,
            ruff_records,
            evidence_errors,
        )
        architecture = _architecture_evidence(
            repository, identity, policy, records, evidence_errors
        )
        quality, quality_blocks = _quality_evidence(
            arguments,
            repository,
            identity,
            policy,
            assessments,
            records,
            evidence_errors,
        )
        modularity = _modularity_evidence(
            policy, assessments, structured_review, architecture, quality
        )
        if evidence_errors:
            return _technical_result(
                identity,
                contract_path,
                records,
                ruff_records,
                blob,
                policy,
                assessments,
                responsibility_targets,
                unbounded_production_paths,
                decisions,
                ruff_diagnostics,
                contract_blocks,
                structured_review,
                review_blocks,
                review_binding,
                architecture,
                modularity,
                quality,
                quality_blocks,
                tuple(evidence_errors),
            )
        assert architecture is not None and modularity is not None and quality is not None
        return _result(
            identity,
            contract_path,
            blob,
            policy,
            contract_blocks,
            assessments,
            responsibility_targets,
            unbounded_production_paths,
            decisions,
            ruff_diagnostics,
            records,
            ruff_records,
            structured_review,
            review_blocks,
            review_binding,
            architecture,
            modularity,
            quality,
            quality_blocks,
        )
    except Exception as error:  # fail closed at the CLI trust boundary
        evidence_errors.append(error)
        return _technical_result(
            identity,
            contract_path,
            records,
            ruff_records,
            blob,
            policy,
            assessments,
            responsibility_targets,
            unbounded_production_paths,
            decisions,
            ruff_diagnostics,
            contract_blocks,
            structured_review,
            review_blocks,
            review_binding,
            architecture,
            modularity,
            quality,
            quality_blocks,
            tuple(evidence_errors),
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
