"""Trusted GitHub-hosted quality command runner."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import zipfile
from pathlib import Path

from supportability_gate import (
    architecture_policy,
    contract,
    gate_policy,
    git_changes,
    quality_profile,
    quality_runner,
)


def _manifest_proof(paths: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    raw = (json.dumps(list(paths), sort_keys=True) + "\n").encode()
    return paths, (), quality_profile._sha256(raw)


def _python_coverage_proof(
    plan: quality_runner.CommandPlan, repository: Path, output: Path
) -> tuple[tuple[str, ...], tuple[str, ...], str, int]:
    report = output / "coverage.json"
    config = quality_runner._write_coverage_config(output)
    completed = subprocess.run(
        (
            plan.actual[0],
            "-I",
            "-m",
            "coverage",
            "json",
            f"--rcfile={config}",
            f"--data-file={output / '.coverage'}",
            "-o",
            str(report),
            "-q",
        ),
        cwd=repository,
        env=quality_runner.fixed_environment(output, repository),
        check=False,
        capture_output=True,
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    if completed.returncode or not report.is_file():
        return (), (), quality_profile._sha256(b""), completed.returncode or -2
    raw = report.read_bytes()
    observed, zero_statement = quality_profile.python_coverage_observation(
        json.loads(raw), plan.source_files
    )
    return observed, zero_statement, quality_profile._sha256(raw), 0


def _wheel_proof(
    plan: quality_runner.CommandPlan, output: Path
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    wheels = tuple((output / "wheel").glob("*.whl"))
    if len(wheels) != 1:
        return (), (), quality_profile._sha256(b"")
    with zipfile.ZipFile(wheels[0]) as wheel:
        members = tuple(sorted(wheel.namelist()))
    observed = tuple(path for path in plan.source_files if path.removeprefix("src/") in members)
    raw = (json.dumps(list(members), sort_keys=True) + "\n").encode()
    return observed, (), quality_profile._sha256(raw)


def _typescript_coverage_proof(
    plan: quality_runner.CommandPlan, repository: Path, output: Path
) -> tuple[tuple[str, ...], tuple[str, ...], str, int]:
    report = output / "coverage.lcov"
    if not report.is_file():
        return (), (), quality_profile._sha256(b""), -2
    raw = report.read_bytes()
    observed, zero_statement = quality_profile.typescript_lcov_observation(
        raw.decode("utf-8"), plan.source_files, repository
    )
    return observed, zero_statement, quality_profile._sha256(raw), 0


def _typescript_build_proof(
    plan: quality_runner.CommandPlan, output: Path
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    built = tuple(
        sorted(
            path.relative_to(output / "build").as_posix() for path in (output / "build").rglob("*")
        )
    )
    suffixes = {".cts": ".cjs", ".mts": ".mjs", ".tsx": ".jsx"}
    observed = tuple(
        source
        for source in plan.source_files
        if str(Path(source).with_suffix(suffixes.get(Path(source).suffix, ".js"))).replace(
            "\\", "/"
        )
        in built
    )
    raw = (json.dumps(list(built), sort_keys=True) + "\n").encode()
    return observed, (), quality_profile._sha256(raw)


def _parser_proof(
    plan: quality_runner.CommandPlan, repository: Path
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    parsed = {
        path: architecture_policy.source_imports(path, (repository / path).read_bytes())
        for path in plan.source_files
    }
    raw = (json.dumps(parsed, sort_keys=True) + "\n").encode()
    return tuple(parsed), (), quality_profile._sha256(raw)


def _proof(
    plan: quality_runner.CommandPlan, repository: Path, output: Path
) -> tuple[tuple[str, ...], tuple[str, ...], str, int]:
    if plan.adapter == "python.pytest.v1":
        return _python_coverage_proof(plan, repository, output)
    if plan.adapter == "typescript.test.v1":
        return _typescript_coverage_proof(plan, repository, output)
    if plan.adapter == "python.build-wheel.v1":
        observed, zero, digest = _wheel_proof(plan, output)
    elif plan.adapter == "typescript.build.v1":
        observed, zero, digest = _typescript_build_proof(plan, output)
    elif plan.adapter in {"python.import-linter.v1", "typescript.import-boundaries.v1"}:
        observed, zero, digest = _parser_proof(plan, repository)
    elif plan.proof_kind == "provisioning":
        observed, zero, digest = (), (), quality_profile._sha256(b"")
    else:
        observed, zero, digest = _manifest_proof(plan.source_files)
    return observed, zero, digest, 0


def _run_command(
    plan: quality_runner.CommandPlan, repository: Path, output: Path
) -> quality_profile.GateResult:
    try:
        completed = subprocess.run(
            plan.actual,
            cwd=repository / "src" if plan.adapter == "python.import-linter.v1" else repository,
            env=quality_runner.fixed_environment(output, repository),
            check=False,
            capture_output=True,
            timeout=quality_profile.TIMEOUT_SECONDS,
        )
        observed, zero_statement, raw_digest, proof_exit = _proof(plan, repository, output)
        return quality_profile.GateResult(
            plan.adapter,
            plan.evidence,
            plan.proof_kind,
            observed,
            zero_statement,
            True,
            completed.returncode or proof_exit,
            quality_profile._sha256(completed.stderr),
            quality_profile._sha256(completed.stdout),
            raw_digest,
            plan.actual,
        )
    except subprocess.TimeoutExpired as error:
        return quality_profile.GateResult(
            plan.adapter,
            plan.evidence,
            plan.proof_kind,
            (),
            (),
            True,
            -1,
            quality_profile._sha256(error.stderr or b""),
            quality_profile._sha256(error.stdout or b""),
            quality_profile._sha256(b""),
            plan.actual,
        )
    except OSError as error:
        return quality_profile.GateResult(
            plan.adapter,
            plan.evidence,
            plan.proof_kind,
            (),
            (),
            False,
            -127,
            quality_profile._sha256(str(error).encode()),
            quality_profile._sha256(b""),
            quality_profile._sha256(b""),
            plan.actual,
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
    base_policy = contract.parse_contract(
        git_changes.read_regular_blob(
            target, identity.base_sha, ".supportability.toml", records
        ).content
    )
    candidate_policy = contract.parse_contract(
        git_changes.read_regular_blob(
            target, identity.head_sha, ".supportability.toml", records
        ).content
    )
    policy = (
        candidate_policy
        if gate_policy.is_profile_expansion(base_policy, candidate_policy)
        else base_policy
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
    production_files, source_files, test_files = quality_runner.profile_files(
        target, identity.head_sha, policy, records
    )
    receipts = quality_profile.asset_receipts(
        target, identity.head_sha, production_files, source_files, records
    )
    plans = quality_runner.command_plans(
        policy.language, target, output.parent, test_files, source_files
    )
    results = tuple(_run_command(plan, target, output.parent) for plan in plans)
    return quality_profile.QualityEvidence(
        base_sha=identity.base_sha,
        changed_paths=changed_paths,
        commands=results,
        exclusions=(),
        head_sha=identity.head_sha,
        high_risk_paths=policy.high_risk_paths,
        language=policy.language,
        maximum_complexity=policy.maximum,
        asset_receipts=receipts,
        production_files=production_files,
        source_files=source_files,
        test_files=test_files,
        production_paths=policy.production_paths,
        repository=str(arguments.repository_name),
        repository_id=str(arguments.repository_id),
        repository_remote=identity.remote,
        run_attempt=str(arguments.run_attempt),
        run_id=str(arguments.run_id),
        runner_environment="github-hosted",
        schema_version=quality_profile.SCHEMA_VERSION,
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
    return int(
        any(
            contract.command_failed(evidence.language, item.adapter, item.executed, item.exit_code)
            for item in evidence.commands
        )
        or any(item.result != "PASS" for item in evidence.asset_receipts)
    )


if __name__ == "__main__":
    raise SystemExit(main())
