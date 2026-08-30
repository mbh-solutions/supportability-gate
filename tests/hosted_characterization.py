"""Trusted GitHub-hosted characterization capture entry point."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from supportability_gate import characterization, contract, git_changes

EXECUTION_TIMEOUT_SECONDS = 120


def _require_hosted_runner() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise characterization.CharacterizationError(
            "CHARACTERIZATION_REQUIRES_GITHUB_HOSTED_RUNNER"
        )


def _safe_environment(target: Path, definition: Path) -> dict[str, str]:
    allowed = {"HOME", "LANG", "PATH", "RUNNER_TEMP", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
    environment = {name: value for name, value in os.environ.items() if name in allowed}
    environment["PYTHONPATH"] = str(target / "src")
    environment["SUPPORTABILITY_CHARACTERIZATION_TARGET"] = str(target)
    environment["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"] = str(definition)
    return environment


def _command(language: str, relative_driver: str, driver: Path) -> tuple[list[str], list[str]]:
    if language == "python":
        return [sys.executable, "-P", str(driver)], ["python3.12", "-P", relative_driver]
    return ["node", str(driver)], ["node", relative_driver]


def _behavior(stdout: bytes, scenario_id: str) -> tuple[object | None, str | None]:
    try:
        data = characterization._exact_keys(
            characterization._read_json_bytes(stdout, "MALFORMED_BEHAVIOR_OUTPUT"),
            {"behavior", "scenario", "schema_version"},
            "MALFORMED_BEHAVIOR_OUTPUT",
        )
    except characterization.CharacterizationError as error:
        return None, error.code
    if data["schema_version"] != "1.0" or data["scenario"] != scenario_id:
        return None, "MALFORMED_BEHAVIOR_OUTPUT"
    return data["behavior"], None


def _run_driver(
    target: Path,
    definition: Path,
    scenario: characterization.Scenario,
    language: str,
    content: bytes,
) -> dict[str, object]:
    relative_driver, _ = characterization._scenario_paths(scenario, language)
    with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP")) as temporary:
        materialized = Path(temporary) / Path(relative_driver).name
        materialized.write_bytes(content)
        arguments, recorded = _command(language, relative_driver, materialized)
        try:
            completed = subprocess.run(
                arguments,
                cwd=target,
                env=_safe_environment(target, definition),
                check=False,
                capture_output=True,
                timeout=EXECUTION_TIMEOUT_SECONDS,
            )
            stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired as error:
            stdout, stderr, exit_code = error.stdout or b"", error.stderr or b"", -1
        except OSError as error:
            stdout, stderr, exit_code = b"", str(error).encode(errors="replace"), -127
    behavior, error_code = _behavior(stdout, scenario.id) if exit_code == 0 else (None, None)
    return {
        "behavior": behavior,
        "behavior_sha256": (
            characterization._sha256(characterization._canonical(behavior))
            if behavior is not None
            else None
        ),
        "command": recorded,
        "error": error_code,
        "exit_code": exit_code,
        "stderr_sha256": characterization._sha256(stderr),
        "stdout_sha256": characterization._sha256(stdout),
    }


def _scenario_capture(
    target: Path,
    definition: Path,
    definition_sha: str,
    scenario: characterization.Scenario,
    language: str,
    records: list[git_changes.CommandRecord],
) -> dict[str, object]:
    profile = characterization.scenario_language(scenario, language)
    driver_path, golden_path = characterization._scenario_paths(scenario, language)
    driver = git_changes.read_regular_blob(definition, definition_sha, driver_path, records)
    golden = git_changes.read_regular_blob(definition, definition_sha, golden_path, records)
    golden_behavior = characterization._read_json_bytes(golden.content, "MALFORMED_GOLDEN_OUTPUT")
    first = _run_driver(target, definition, scenario, profile, driver.content)
    second = _run_driver(target, definition, scenario, profile, driver.content)
    return {
        "behavior": first["behavior"],
        "behavior_sha256": first["behavior_sha256"],
        "command": first["command"],
        "covers": list(scenario.covers),
        "deterministic": first == second,
        "driver_blob_sha": driver.object_sha,
        "error": first["error"],
        "exit_code": first["exit_code"],
        "golden_behavior_sha256": characterization._sha256(
            characterization._canonical(golden_behavior)
        ),
        "golden_blob_sha": golden.object_sha,
        "id": scenario.id,
        "kind": scenario.kind,
        "stderr_sha256": first["stderr_sha256"],
        "stdout_sha256": first["stdout_sha256"],
    }


def capture_evidence(
    target: Path,
    definition: Path,
    *,
    base_sha: str,
    head_sha: str,
    side: str,
    repository: str,
    repository_id: str,
    workflow_sha: str,
    run_id: str,
    run_attempt: str,
    job: str,
) -> dict[str, object]:
    """Execute fixed-convention scenarios only on a GitHub-hosted runner."""
    _require_hosted_runner()
    records: list[git_changes.CommandRecord] = []
    target = git_changes.validate_repository(target, records)
    definition = git_changes.validate_repository(definition, records)
    target_sha = git_changes.run_git(target, ("rev-parse", "HEAD"), records).decode().strip()
    definition_sha = (
        git_changes.run_git(definition, ("rev-parse", "HEAD"), records).decode().strip()
    )
    expected_target = base_sha if side == "base" else head_sha
    if side not in {"base", "head"} or target_sha != expected_target or definition_sha != head_sha:
        raise characterization.CharacterizationError("STALE_CHARACTERIZATION_CHECKOUT")
    policy_blob = git_changes.read_regular_blob(
        definition, base_sha, ".supportability.toml", records
    )
    policy = contract.parse_contract(policy_blob.content)
    manifest = characterization._manifest(definition, head_sha, records)
    scenarios = [
        _scenario_capture(target, definition, head_sha, item, policy.language, records)
        for item in manifest.scenarios
    ]
    fingerprint = characterization._sha256(
        characterization._canonical([[item["id"], item["behavior_sha256"]] for item in scenarios])
    )
    return {
        "authentication": {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "job": job,
            "repository": repository,
            "repository_id": repository_id,
            "run_attempt": run_attempt,
            "run_id": run_id,
            "side": side,
            "workflow_sha": workflow_sha,
        },
        "behavior_fingerprint": fingerprint,
        "definition_sha": definition_sha,
        "language": policy.language,
        "manifest": characterization._manifest_payload(manifest),
        "scenarios": scenarios,
        "schema_version": characterization.CAPTURE_SCHEMA,
        "target_sha": target_sha,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-repository", required=True)
    parser.add_argument("--definition-repository", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--side", choices=("base", "head"), required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = capture_evidence(
            Path(arguments.target_repository),
            Path(arguments.definition_repository),
            base_sha=arguments.base_ref,
            head_sha=arguments.head_ref,
            side=arguments.side,
            repository=arguments.repository,
            repository_id=arguments.repository_id,
            workflow_sha=arguments.workflow_sha,
            run_id=arguments.run_id,
            run_attempt=arguments.run_attempt,
            job=arguments.job,
        )
        characterization._write_json(Path(arguments.output), result)
    except Exception as error:
        print(getattr(error, "code", "TECHNICAL_FAILURE"))
        return 2
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
