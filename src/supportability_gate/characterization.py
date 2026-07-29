"""Capture and verify authenticated base/head characterization evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supportability_gate import contract, git_changes

MANIFEST_PATH = ".supportability-characterization.json"
SCENARIO_ROOT = "tests/characterization"
CAPTURE_SCHEMA = "characterization-capture.v1"
RESULT_SCHEMA = "characterization-result.v1"
KINDS = frozenset({"test", "sample_io", "snapshot", "golden", "cli", "regression"})
SCENARIO_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256 = re.compile(r"[0-9a-f]{64}")
EXECUTION_TIMEOUT_SECONDS = 120
MAX_JSON_BYTES = 1_000_000


class CharacterizationError(ValueError):
    """One fail-closed characterization defect."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class Scenario:
    """One fixed-convention characterization scenario."""

    id: str
    kind: str
    covers: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    """Validated scenario manifest at one immutable commit."""

    scenarios: tuple[Scenario, ...]
    blob_sha: str
    sha256: str


def _manifest_payload(manifest: Manifest) -> dict[str, object]:
    return {
        "blob_sha": manifest.blob_sha,
        "scenarios": [
            {"covers": list(item.covers), "id": item.id, "kind": item.kind}
            for item in manifest.scenarios
        ],
        "sha256": manifest.sha256,
    }


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json_bytes(content: bytes, code: str) -> Any:
    if not content or len(content) > MAX_JSON_BYTES:
        raise CharacterizationError(code)
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CharacterizationError(code) from error


def _exact_keys(value: object, expected: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CharacterizationError(code)
    return value


def _path_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST", field)
    paths = tuple(contract.normalize_repository_path(item, field) for item in value)
    if len(paths) != len(set(paths)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST", field)
    return paths


def parse_manifest(content: bytes, blob_sha: str) -> Manifest:
    """Parse the single fixed characterization manifest schema."""
    data = _exact_keys(
        _read_json_bytes(content, "MALFORMED_CHARACTERIZATION_MANIFEST"),
        {"schema_version", "scenarios"},
        "MALFORMED_CHARACTERIZATION_MANIFEST",
    )
    scenarios = data["scenarios"]
    if data["schema_version"] != "1.0" or not isinstance(scenarios, list) or not scenarios:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    parsed: list[Scenario] = []
    for item in scenarios:
        row = _exact_keys(item, {"covers", "id", "kind"}, "MALFORMED_CHARACTERIZATION_MANIFEST")
        identifier, kind = row["id"], row["kind"]
        if (
            not isinstance(identifier, str)
            or SCENARIO_ID.fullmatch(identifier) is None
            or kind not in KINDS
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
        parsed.append(Scenario(identifier, str(kind), _path_list(row["covers"], "covers")))
    if len(parsed) != len({item.id for item in parsed}) or len(parsed) > 50:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return Manifest(tuple(sorted(parsed, key=lambda item: item.id)), blob_sha, _sha256(content))


def _manifest(
    repository: Path,
    commit_sha: str,
    records: list[git_changes.CommandRecord],
) -> Manifest:
    blob = git_changes.read_regular_blob(repository, commit_sha, MANIFEST_PATH, records)
    return parse_manifest(blob.content, blob.object_sha)


def _scenario_paths(scenario: Scenario, language: str) -> tuple[str, str]:
    extension = "py" if language == "python" else "mjs"
    base = f"{SCENARIO_ROOT}/{scenario.id}"
    return f"{base}.characterization.{extension}", f"{base}.golden.json"


def _safe_environment(target: Path, definition: Path) -> dict[str, str]:
    allowed = {"HOME", "LANG", "PATH", "RUNNER_TEMP", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
    environment = {name: value for name, value in os.environ.items() if name in allowed}
    environment["PYTHONPATH"] = str(target / "src")
    environment["SUPPORTABILITY_CHARACTERIZATION_TARGET"] = str(target)
    environment["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"] = str(definition)
    return environment


def _require_hosted_runner() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise CharacterizationError("CHARACTERIZATION_REQUIRES_GITHUB_HOSTED_RUNNER")


def _command(language: str, relative_driver: str, driver: Path) -> tuple[list[str], list[str]]:
    if language == "python":
        return [sys.executable, "-P", str(driver)], ["python3.12", "-P", relative_driver]
    return ["node", str(driver)], ["node", relative_driver]


def _behavior(stdout: bytes, scenario_id: str) -> tuple[object | None, str | None]:
    try:
        data = _exact_keys(
            _read_json_bytes(stdout, "MALFORMED_BEHAVIOR_OUTPUT"),
            {"behavior", "scenario", "schema_version"},
            "MALFORMED_BEHAVIOR_OUTPUT",
        )
    except CharacterizationError as error:
        return None, error.code
    if data["schema_version"] != "1.0" or data["scenario"] != scenario_id:
        return None, "MALFORMED_BEHAVIOR_OUTPUT"
    return data["behavior"], None


def _run_driver(
    target: Path,
    definition: Path,
    scenario: Scenario,
    language: str,
) -> dict[str, object]:
    relative_driver, _ = _scenario_paths(scenario, language)
    driver = definition / Path(relative_driver)
    arguments, recorded = _command(language, relative_driver, driver)
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
        "behavior_sha256": _sha256(_canonical(behavior)) if behavior is not None else None,
        "command": recorded,
        "error": error_code,
        "exit_code": exit_code,
        "stderr_sha256": _sha256(stderr),
        "stdout_sha256": _sha256(stdout),
    }


def _scenario_capture(
    target: Path,
    definition: Path,
    definition_sha: str,
    scenario: Scenario,
    language: str,
    records: list[git_changes.CommandRecord],
) -> dict[str, object]:
    driver_path, golden_path = _scenario_paths(scenario, language)
    driver = git_changes.read_regular_blob(definition, definition_sha, driver_path, records)
    golden = git_changes.read_regular_blob(definition, definition_sha, golden_path, records)
    golden_behavior = _read_json_bytes(golden.content, "MALFORMED_GOLDEN_OUTPUT")
    first = _run_driver(target, definition, scenario, language)
    second = _run_driver(target, definition, scenario, language)
    deterministic = first == second
    return {
        "behavior": first["behavior"],
        "behavior_sha256": first["behavior_sha256"],
        "command": first["command"],
        "covers": list(scenario.covers),
        "deterministic": deterministic,
        "driver_blob_sha": driver.object_sha,
        "error": first["error"],
        "exit_code": first["exit_code"],
        "golden_behavior_sha256": _sha256(_canonical(golden_behavior)),
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
    """Execute every fixed-convention scenario twice and return authenticated evidence."""
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
        raise CharacterizationError("STALE_CHARACTERIZATION_CHECKOUT")
    policy_blob = git_changes.read_regular_blob(
        definition, base_sha, ".supportability.toml", records
    )
    policy = contract.parse_contract(policy_blob.content)
    manifest = _manifest(definition, head_sha, records)
    scenarios = [
        _scenario_capture(target, definition, head_sha, item, policy.language, records)
        for item in manifest.scenarios
    ]
    fingerprint = _sha256(_canonical([[item["id"], item["behavior_sha256"]] for item in scenarios]))
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
        "manifest": _manifest_payload(manifest),
        "scenarios": scenarios,
        "schema_version": CAPTURE_SCHEMA,
        "target_sha": target_sha,
    }


def _load_capture(path: Path, missing_code: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        content = path.read_bytes()
        value = _read_json_bytes(content, "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE")
    except FileNotFoundError:
        return None, missing_code
    except (OSError, CharacterizationError):
        return None, "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"
    if not isinstance(value, dict):
        return None, "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"
    value["_capture_sha256"] = _sha256(content)
    return value, None


def _capture_digest_blocks(
    base: dict[str, Any] | None,
    head: dict[str, Any] | None,
    base_sha256: str,
    head_sha256: str,
) -> list[str]:
    blocks: list[str] = []
    if base is not None and base.get("_capture_sha256") != base_sha256:
        blocks.append("BASE_CAPTURE_DIGEST_MISMATCH")
    if head is not None and head.get("_capture_sha256") != head_sha256:
        blocks.append("HEAD_CAPTURE_DIGEST_MISMATCH")
    return blocks


def _authentication_blocks(
    artifact: dict[str, Any],
    expected: dict[str, str],
) -> list[str]:
    expected_keys = {
        "_capture_sha256",
        "authentication",
        "behavior_fingerprint",
        "definition_sha",
        "language",
        "manifest",
        "scenarios",
        "schema_version",
        "target_sha",
    }
    if set(artifact) != expected_keys or artifact.get("schema_version") != CAPTURE_SCHEMA:
        return ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"]
    authentication = artifact.get("authentication")
    if not isinstance(authentication, dict) or authentication != expected:
        return ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"]
    return []


def _definition_blocks(
    repository: Path,
    base_sha: str,
    head_sha: str,
    language: str,
    head: Manifest,
    records: list[git_changes.CommandRecord],
) -> list[str]:
    try:
        base = _manifest(repository, base_sha, records)
    except git_changes.GitError as error:
        if error.code == "MISSING_BLOB":
            return []
        raise
    blocks: list[str] = []
    base_by_id = {item.id: item for item in base.scenarios}
    head_by_id = {item.id: item for item in head.scenarios}
    for identifier in sorted(set(base_by_id) - set(head_by_id)):
        blocks.append(f"REMOVED_CHARACTERIZATION_SCENARIO:{identifier}")
    for identifier in sorted(set(base_by_id) & set(head_by_id)):
        if base_by_id[identifier] != head_by_id[identifier]:
            blocks.append(f"CHANGED_CHARACTERIZATION_DEFINITION:{identifier}")
            continue
        driver_path, golden_path = _scenario_paths(head_by_id[identifier], language)
        base_driver = git_changes.read_regular_blob(repository, base_sha, driver_path, records)
        head_driver = git_changes.read_regular_blob(repository, head_sha, driver_path, records)
        base_golden = git_changes.read_regular_blob(repository, base_sha, golden_path, records)
        head_golden = git_changes.read_regular_blob(repository, head_sha, golden_path, records)
        if base_driver.object_sha != head_driver.object_sha:
            blocks.append(f"CHANGED_CHARACTERIZATION_DEFINITION:{identifier}")
        if base_golden.object_sha != head_golden.object_sha:
            blocks.append(f"CHANGED_GOLDEN_OUTPUT:{identifier}")
    return blocks


def _capture_blocks(
    artifact: dict[str, Any],
    manifest: Manifest,
    language: str,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    blocks: list[str] = []
    rows = artifact.get("scenarios")
    if (
        artifact.get("language") != language
        or artifact.get("manifest") != _manifest_payload(manifest)
        or not isinstance(rows, list)
    ):
        return ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"], {}
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            by_id[row["id"]] = row
    if set(by_id) != {item.id for item in manifest.scenarios} or len(by_id) != len(rows):
        return ["INCOMPLETE_CHARACTERIZATION_EVIDENCE"], {}
    for scenario in manifest.scenarios:
        row = by_id[scenario.id]
        blocks.extend(_scenario_row_blocks(scenario, row))
    expected_fingerprint = _sha256(
        _canonical(
            [[item.id, by_id[item.id].get("behavior_sha256")] for item in manifest.scenarios]
        )
    )
    if artifact.get("behavior_fingerprint") != expected_fingerprint:
        blocks.append("CHARACTERIZATION_FINGERPRINT_MISMATCH")
    return blocks, by_id


def _scenario_row_blocks(scenario: Scenario, row: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    if row.get("kind") != scenario.kind or row.get("covers") != list(scenario.covers):
        blocks.append(f"CHARACTERIZATION_DEFINITION_MISMATCH:{scenario.id}")
    if row.get("exit_code") != 0 or row.get("error") is not None:
        blocks.append(f"CHARACTERIZATION_EXECUTION_FAILED:{scenario.id}")
    if row.get("deterministic") is not True:
        blocks.append(f"CHARACTERIZATION_REPLAY_DRIFT:{scenario.id}")
    if row.get("behavior_sha256") != row.get("golden_behavior_sha256"):
        blocks.append(f"GOLDEN_BEHAVIOR_MISMATCH:{scenario.id}")
    return blocks


def _artifact_identity_blocks(
    repository: Path,
    head_sha: str,
    language: str,
    manifest: Manifest,
    rows: dict[str, dict[str, Any]],
    records: list[git_changes.CommandRecord],
) -> list[str]:
    blocks: list[str] = []
    for scenario in manifest.scenarios:
        row = rows.get(scenario.id)
        if row is None:
            continue
        driver_path, golden_path = _scenario_paths(scenario, language)
        driver = git_changes.read_regular_blob(repository, head_sha, driver_path, records)
        golden = git_changes.read_regular_blob(repository, head_sha, golden_path, records)
        if row.get("driver_blob_sha") != driver.object_sha:
            blocks.append(f"CHARACTERIZATION_DRIVER_IDENTITY_MISMATCH:{scenario.id}")
        if row.get("golden_blob_sha") != golden.object_sha:
            blocks.append(f"GOLDEN_ARTIFACT_IDENTITY_MISMATCH:{scenario.id}")
    return blocks


def _coverage_blocks(
    repository: Path,
    base_sha: str,
    head_sha: str,
    policy: contract.Contract,
    manifest: Manifest,
    records: list[git_changes.CommandRecord],
) -> tuple[list[str], list[str], list[str]]:
    changes = git_changes.changed_paths(repository, base_sha, head_sha, records)
    changed = {
        path
        for item in changes
        for path in (item.old_path, item.new_path)
        if path and policy.is_production_path(path)
    }
    required = sorted(changed | set(policy.high_risk_paths))
    covered = sorted({path for item in manifest.scenarios for path in item.covers})
    blocks = [
        f"MISSING_CHARACTERIZATION_COVERAGE:{path}" for path in required if path not in covered
    ]
    return blocks, required, covered


def _verified_capture_rows(
    artifact: dict[str, Any] | None,
    side: str,
    expected_common: dict[str, str],
    target_sha: str,
    head_sha: str,
    repository: Path,
    policy: contract.Contract,
    manifest: Manifest,
    records: list[git_changes.CommandRecord],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if artifact is None:
        return [], {}
    expected = {**expected_common, "job": f"characterize-{side}", "side": side}
    blocks = _authentication_blocks(artifact, expected)
    if artifact.get("target_sha") != target_sha or artifact.get("definition_sha") != head_sha:
        blocks.append(f"STALE_{'BASELINE' if side == 'base' else 'POST_CHANGE'}_ARTIFACT")
    capture_blocks, rows = _capture_blocks(artifact, manifest, policy.language)
    blocks.extend(capture_blocks)
    blocks.extend(
        _artifact_identity_blocks(repository, head_sha, policy.language, manifest, rows, records)
    )
    return blocks, rows


def _compatibility_evidence(
    manifest: Manifest,
    base_rows: dict[str, dict[str, Any]],
    head_rows: dict[str, dict[str, Any]],
) -> tuple[list[str], list[dict[str, object]]]:
    blocks: list[str] = []
    scenarios: list[dict[str, object]] = []
    for item in manifest.scenarios:
        base_row, head_row = base_rows.get(item.id), head_rows.get(item.id)
        compatible = bool(
            base_row
            and head_row
            and base_row.get("behavior_sha256") == head_row.get("behavior_sha256")
        )
        if base_row and head_row and not compatible:
            blocks.append(f"INCOMPATIBLE_POST_CHANGE_BEHAVIOR:{item.id}")
        scenarios.append(
            {
                "base_behavior_sha256": base_row.get("behavior_sha256") if base_row else None,
                "command": head_row.get("command") if head_row else None,
                "compatibility": "PASS" if compatible else "BLOCK",
                "covers": list(item.covers),
                "golden_behavior_sha256": (
                    head_row.get("golden_behavior_sha256") if head_row else None
                ),
                "head_behavior_sha256": head_row.get("behavior_sha256") if head_row else None,
                "id": item.id,
                "kind": item.kind,
            }
        )
    return blocks, scenarios


def _verification_result(
    identity: git_changes.RepositoryIdentity,
    manifest: Manifest,
    base: dict[str, Any] | None,
    head: dict[str, Any] | None,
    scenarios: list[dict[str, object]],
    blocks: list[str],
    required: list[str],
    covered: list[str],
    workflow_sha: str,
    base_artifact_id: str,
    base_artifact_digest: str,
    head_artifact_id: str,
    head_artifact_digest: str,
) -> dict[str, object]:
    unique_blocks = sorted(set(blocks))
    return {
        "artifacts": {
            "base": {
                "capture_sha256": base.get("_capture_sha256") if base else None,
                "digest": base_artifact_digest,
                "id": base_artifact_id,
            },
            "head": {
                "capture_sha256": head.get("_capture_sha256") if head else None,
                "digest": head_artifact_digest,
                "id": head_artifact_id,
            },
        },
        "base_sha": identity.base_sha,
        "behavior_fingerprint": _sha256(
            _canonical([[item["id"], item["head_behavior_sha256"]] for item in scenarios])
        ),
        "coverage": {"covered_paths": covered, "required_paths": required},
        "head_sha": identity.head_sha,
        "manifest_blob_sha": manifest.blob_sha,
        "manifest_sha256": manifest.sha256,
        "overall_result": "BLOCK" if unique_blocks else "PASS",
        "policy_blocks": unique_blocks,
        "repository": identity.remote,
        "scenarios": scenarios,
        "schema_version": RESULT_SCHEMA,
        "workflow_sha": workflow_sha,
    }


def verify_evidence(
    repository: Path,
    base_sha: str,
    head_sha: str,
    base_path: Path,
    head_path: Path,
    *,
    repository_name: str,
    repository_id: str,
    workflow_sha: str,
    run_id: str,
    run_attempt: str,
    base_artifact_id: str,
    base_artifact_digest: str,
    base_capture_sha256: str,
    head_artifact_id: str,
    head_artifact_digest: str,
    head_capture_sha256: str,
) -> dict[str, object]:
    """Verify exact-identity captures, compatibility, golden immutability, and coverage."""
    records: list[git_changes.CommandRecord] = []
    repository = git_changes.validate_repository(repository, records)
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
    policy_blob = git_changes.read_regular_blob(
        repository, base_sha, ".supportability.toml", records
    )
    policy = contract.parse_contract(policy_blob.content)
    manifest = _manifest(repository, head_sha, records)
    base, base_error = _load_capture(base_path, "MISSING_BASELINE")
    head, head_error = _load_capture(head_path, "HEAD_ONLY_CHARACTERIZATION_CLAIM")
    blocks = [item for item in (base_error, head_error) if item]
    if base is None and head is not None:
        blocks.append("HEAD_ONLY_CHARACTERIZATION_CLAIM")
    blocks.extend(_capture_digest_blocks(base, head, base_capture_sha256, head_capture_sha256))
    expected_common = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "repository": repository_name,
        "repository_id": repository_id,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "workflow_sha": workflow_sha,
    }
    base_blocks, base_rows = _verified_capture_rows(
        base, "base", expected_common, base_sha, head_sha, repository, policy, manifest, records
    )
    head_blocks, head_rows = _verified_capture_rows(
        head, "head", expected_common, head_sha, head_sha, repository, policy, manifest, records
    )
    blocks.extend((*base_blocks, *head_blocks))
    blocks.extend(
        _definition_blocks(repository, base_sha, head_sha, policy.language, manifest, records)
    )
    coverage_blocks, required, covered = _coverage_blocks(
        repository, base_sha, head_sha, policy, manifest, records
    )
    blocks.extend(coverage_blocks)
    if (
        not base_artifact_id.isdecimal()
        or not head_artifact_id.isdecimal()
        or SHA256.fullmatch(base_artifact_digest) is None
        or SHA256.fullmatch(base_capture_sha256) is None
        or SHA256.fullmatch(head_artifact_digest) is None
        or SHA256.fullmatch(head_capture_sha256) is None
    ):
        blocks.append("INVALID_ARTIFACT_IDENTITY")
    compatibility_blocks, scenarios = _compatibility_evidence(manifest, base_rows, head_rows)
    blocks.extend(compatibility_blocks)
    return _verification_result(
        identity,
        manifest,
        base,
        head,
        scenarios,
        blocks,
        required,
        covered,
        workflow_sha,
        base_artifact_id,
        base_artifact_digest,
        head_artifact_id,
        head_artifact_digest,
    )


def _write_json(path: Path, value: object) -> bytes:
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-characterization")
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--target-repository", required=True)
    capture.add_argument("--definition-repository", required=True)
    capture.add_argument("--base-ref", required=True)
    capture.add_argument("--head-ref", required=True)
    capture.add_argument("--side", choices=("base", "head"), required=True)
    capture.add_argument("--repository", required=True)
    capture.add_argument("--repository-id", required=True)
    capture.add_argument("--workflow-sha", required=True)
    capture.add_argument("--run-id", required=True)
    capture.add_argument("--run-attempt", required=True)
    capture.add_argument("--job", required=True)
    capture.add_argument("--output", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--repository", required=True)
    verify.add_argument("--repository-name", required=True)
    verify.add_argument("--repository-id", required=True)
    verify.add_argument("--base-ref", required=True)
    verify.add_argument("--head-ref", required=True)
    verify.add_argument("--workflow-sha", required=True)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--run-attempt", required=True)
    verify.add_argument("--base-evidence", required=True)
    verify.add_argument("--head-evidence", required=True)
    verify.add_argument("--base-artifact-id", required=True)
    verify.add_argument("--base-artifact-digest", required=True)
    verify.add_argument("--base-capture-sha256", required=True)
    verify.add_argument("--head-artifact-id", required=True)
    verify.add_argument("--head-artifact-digest", required=True)
    verify.add_argument("--head-capture-sha256", required=True)
    verify.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Capture or verify characterization evidence."""
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "capture":
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
        else:
            result = verify_evidence(
                Path(arguments.repository),
                arguments.base_ref,
                arguments.head_ref,
                Path(arguments.base_evidence),
                Path(arguments.head_evidence),
                repository_name=arguments.repository_name,
                repository_id=arguments.repository_id,
                workflow_sha=arguments.workflow_sha,
                run_id=arguments.run_id,
                run_attempt=arguments.run_attempt,
                base_artifact_id=arguments.base_artifact_id,
                base_artifact_digest=arguments.base_artifact_digest,
                base_capture_sha256=arguments.base_capture_sha256,
                head_artifact_id=arguments.head_artifact_id,
                head_artifact_digest=arguments.head_artifact_digest,
                head_capture_sha256=arguments.head_capture_sha256,
            )
        _write_json(Path(arguments.output), result)
    except Exception as error:  # fail closed at isolated-job boundary
        print(getattr(error, "code", "TECHNICAL_FAILURE"))
        return 2
    overall = str(result.get("overall_result", "PASS"))
    print(overall)
    return 1 if overall == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
