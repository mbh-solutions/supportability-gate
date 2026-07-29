"""Trusted GitHub-hosted quality command runner."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from supportability_gate import contract, git_changes, quality_profile


def _run_command(
    plan: quality_profile._CommandPlan, repository: Path, output: Path
) -> quality_profile.GateResult:
    try:
        completed = subprocess.run(
            plan.actual,
            cwd=repository,
            env=quality_profile._fixed_environment(output, repository),
            check=False,
            capture_output=True,
            timeout=quality_profile.TIMEOUT_SECONDS,
        )
        return quality_profile.GateResult(
            plan.adapter,
            plan.evidence,
            plan.covered_paths,
            True,
            completed.returncode,
            quality_profile._sha256(completed.stderr),
            quality_profile._sha256(completed.stdout),
        )
    except subprocess.TimeoutExpired as error:
        return quality_profile.GateResult(
            plan.adapter,
            plan.evidence,
            plan.covered_paths,
            True,
            -1,
            quality_profile._sha256(error.stderr or b""),
            quality_profile._sha256(error.stdout or b""),
        )
    except OSError as error:
        return quality_profile.GateResult(
            plan.adapter,
            plan.evidence,
            plan.covered_paths,
            False,
            -127,
            quality_profile._sha256(str(error).encode()),
            quality_profile._sha256(b""),
        )


def run_profile(arguments: argparse.Namespace) -> quality_profile.QualityEvidence:
    """Execute fixed commands in the disposable quality job and return its capture."""
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise quality_profile.QualityProfileError(
            "NON_HOSTED_TARGET_EXECUTION", "quality profiles require a GitHub-hosted runner"
        )
    records: list[git_changes.CommandRecord] = []
    target = git_changes.validate_repository(Path(arguments.repository), records)
    identity = git_changes.inspect_repository(
        target, str(arguments.base_ref), str(arguments.head_ref), records
    )
    workflow_sha = str(arguments.workflow_sha)
    if (
        workflow_sha.lower() != workflow_sha
        or quality_profile._FULL_SHA.fullmatch(workflow_sha) is None
    ):
        raise quality_profile.QualityProfileError(
            "INVALID_WORKFLOW_SHA", "workflow SHA must be immutable"
        )
    policy = contract.parse_contract(
        git_changes.read_regular_blob(
            target, identity.base_sha, ".supportability.toml", records
        ).content
    )
    changes = git_changes.changed_paths(target, identity.base_sha, identity.head_sha, records)
    changed_paths = tuple(
        sorted(
            {
                path
                for change in changes
                for path in (change.old_path, change.new_path)
                if path and policy.is_production_path(path)
            }
        )
    )
    output = Path(arguments.output)
    source_files, test_files = quality_profile._profile_files(
        target, identity.head_sha, policy, records
    )
    plans = quality_profile._command_plans(
        policy.language, target, output.parent, test_files, source_files
    )
    results = tuple(_run_command(plan, target, output.parent) for plan in plans)
    failed = any(not item.executed or item.exit_code for item in results)
    return quality_profile.QualityEvidence(
        base_sha=identity.base_sha,
        changed_paths=changed_paths,
        commands=results,
        exclusions=(),
        head_sha=identity.head_sha,
        high_risk_paths=policy.high_risk_paths,
        language=policy.language,
        maximum_complexity=policy.maximum,
        production_files=source_files,
        production_paths=policy.production_paths,
        repository=str(arguments.repository_name),
        repository_id=str(arguments.repository_id),
        repository_remote=identity.remote,
        run_attempt=str(arguments.run_attempt),
        run_id=str(arguments.run_id),
        runner_environment="github-hosted",
        schema_version=quality_profile.SCHEMA_VERSION,
        untested_areas=source_files if failed else (),
        workflow_sha=workflow_sha,
        job="quality-profile",
        artifact_id="",
        artifact_digest="",
        capture_sha256="",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-name", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    output = Path(arguments.output)
    if not output.is_absolute():
        return 2
    try:
        evidence = run_profile(arguments)
        quality_profile.write_evidence(evidence, output)
    except Exception:
        return 2
    return 1 if evidence.untested_areas else 0


if __name__ == "__main__":
    raise SystemExit(main())
