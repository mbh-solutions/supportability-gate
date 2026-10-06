"""Join four native quality captures without accepting an incomplete test partition."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

from coverage import CoverageData

from supportability_gate import contract, gate_policy, git_changes, quality_profile

COUNT = 4


def trusted_collector() -> ModuleType:
    path = Path(__file__).resolve().with_name("hosted_quality_profile.py")
    specification = importlib.util.spec_from_file_location("quality_shard_collector", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("MISSING_TRUSTED_QUALITY_COLLECTOR")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


collector = trusted_collector()


def fail(message: str) -> NoReturn:
    raise quality_profile.QualityProfileError("INVALID_QUALITY_PARTITION", message)


def read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        fail(f"missing {path.name}")
    return json.loads(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def native_parts(arguments: argparse.Namespace, root: Path) -> list[Path]:
    repository = arguments.repository_name
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        fail("invalid repository")
    route = f"repos/{repository}/actions/runs/{arguments.run_id}/artifacts?per_page=100"
    listing = json.loads(subprocess.check_output(["gh", "api", route]))
    write_json(root / "native-artifacts.json", listing)
    parts = []
    for index in range(arguments.pytest_shard_count):
        name = f"quality-profile-shard-{index}-{arguments.run_id}-{arguments.run_attempt}"
        matches = [item for item in listing["artifacts"] if item["name"] == name]
        if len(matches) != 1:
            fail(f"missing or duplicate native shard {index}")
        artifact = matches[0]
        if (
            artifact["expired"]
            or artifact["workflow_run"]["head_sha"] != arguments.head_ref
            or str(artifact["workflow_run"]["id"]) != str(arguments.run_id)
            or str(artifact["workflow_run"]["repository_id"]) != str(arguments.repository_id)
        ):
            fail("stale artifact")
        route = f"repos/{repository}/actions/artifacts/{artifact['id']}/zip"
        raw = subprocess.check_output(["gh", "api", route])
        if (
            len(raw) > 128 * 1024 * 1024
            or artifact["digest"] != "sha256:" + hashlib.sha256(raw).hexdigest()
        ):
            fail("artifact digest mismatch")
        archive = root / f"shard-{index}.zip"
        archive.write_bytes(raw)
        destination = root / "shards" / str(index)
        with zipfile.ZipFile(archive) as zipped:
            for item in zipped.infolist():
                resolved = (destination / item.filename).resolve()
                if (
                    not resolved.is_relative_to(destination.resolve())
                    or item.file_size > 128 * 1024 * 1024
                ):
                    fail("unsafe artifact member")
            zipped.extractall(destination)
        parts.append(destination)
    return parts


def validate_partition(
    partition: Any, completion: Any, raw_completion: bytes, index: int
) -> tuple[str, ...]:
    if (
        partition.get("schema_version") != "quality-pytest-partition.v1"
        or partition.get("count") != COUNT
        or partition.get("index") != index
        or partition.get("completion_sha256") != hashlib.sha256(raw_completion).hexdigest()
    ):
        fail("partition identity mismatch")
    full = partition.get("full_collection")
    selected = partition.get("selected_collection")
    if not isinstance(full, list) or not full or not all(isinstance(item, str) for item in full):
        fail("empty or malformed full collection")
    if len(set(full)) != len(full) or selected != full[index::COUNT]:
        fail("missing, duplicate, or misplaced test")
    if (
        completion.get("guard_passed") is not True
        or completion.get("errors") != []
        or completion.get("collected") != len(selected)
        or completion.get("completed") != len(selected)
        or completion.get("collection_sha256")
        != hashlib.sha256(json.dumps(selected).encode()).hexdigest()
        or completion.get("outcomes", {}).get("failed") != 0
        or completion.get("subtests", {}).get("failed") != 0
        or sum(completion.get("outcomes", {}).values()) != len(selected)
    ):
        fail("incomplete or failed test execution")
    workers = completion.get("workers", [])
    if [worker.get("id") for worker in workers] != ["gw0", "gw1"] or any(
        not all(
            worker.get(key) is True
            for key in (
                "completed",
                "coverage_ack",
                "isolated",
                "trusted_helper",
                "branch_coverage",
            )
        )
        for worker in workers
    ):
        fail("missing worker or branch coverage")
    return tuple(full)


def validate_profiles(
    parts: list[Path], arguments: argparse.Namespace
) -> list[quality_profile.QualityEvidence]:
    profiles = [quality_profile.load_evidence(part / "quality-gates.json") for part in parts]
    if len(profiles) != (4 if profiles[0].language in {"python", "mixed"} else 1):
        fail("incorrect partition count for the stack")
    identities = []
    environments = []
    for index, profile in enumerate(profiles):
        expected = (
            arguments.base_ref,
            arguments.head_ref,
            arguments.workflow_sha,
            str(arguments.run_id),
            str(arguments.run_attempt),
            arguments.repository_name,
            str(arguments.repository_id),
            f"quality-profile-shard-{index}",
        )
        observed = (
            profile.base_sha,
            profile.head_sha,
            profile.workflow_sha,
            profile.run_id,
            profile.run_attempt,
            profile.repository,
            profile.repository_id,
            profile.job,
        )
        if observed != expected or profile.runner_environment != "github-hosted":
            fail("stale capture identity")
        validate_command_prefix(profile.language, profile.commands)
        identity = asdict(profile)
        for key in ("job", "commands"):
            del identity[key]
        identities.append(identity)
        provenance = read_json(parts[index] / "isolation-provenance.json")
        if (
            provenance["quality_evidence_sha256"]
            != hashlib.sha256((parts[index] / "quality-gates.json").read_bytes()).hexdigest()
        ):
            fail("capture provenance mismatch")
        keys = (
            "base_sha",
            "head_sha",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "job",
            "repository",
            "repository_id",
        )
        if provenance.get("schema_version") != collector._PROVENANCE_SCHEMA or any(
            provenance.get(key) != getattr(profile, key) for key in keys
        ):
            fail("provenance identity mismatch")
        environments.append(
            {
                key: provenance[key]
                for key in (
                    "runtime",
                    "resolved_dependencies",
                    "source_receipts",
                    "security_controls",
                    "container",
                )
            }
        )
    if any(identity != identities[0] for identity in identities):
        fail("capture scope or source inventory mismatch")
    if has_failure(profiles):
        return profiles
    if any(environment != environments[0] for environment in environments):
        fail("runtime, dependency, or sandbox mismatch")
    validate_command_scope(profiles)
    return profiles


def validate_command_prefix(
    language: str, commands: tuple[quality_profile.GateResult, ...]
) -> None:
    required = quality_profile.required_adapters(language)
    observed = tuple(command.adapter for command in commands)
    if not observed or observed != required[: len(observed)]:
        fail("invalid command profile")
    failures = [
        contract.command_failed(language, command.adapter, command.executed, command.exit_code)
        for command in commands
    ]
    if any(failures[:-1]) or (observed != required and not failures[-1]):
        fail("unexplained command prefix")


def has_failure(profiles: list[quality_profile.QualityEvidence]) -> bool:
    return any(
        contract.command_failed(
            profile.language, command.adapter, command.executed, command.exit_code
        )
        for profile in profiles
        for command in profile.commands
    ) or any(asset.result != "PASS" for profile in profiles for asset in profile.asset_receipts)


def validate_command_scope(profiles: list[quality_profile.QualityEvidence]) -> None:
    for commands in zip(*(profile.commands for profile in profiles), strict=True):
        expected = commands[0]
        for command in commands[1:]:
            fixed = (
                "adapter",
                "arguments",
                "executed_arguments",
                "proof_kind",
                "zero_statement_paths",
            )
            if any(getattr(command, key) != getattr(expected, key) for key in fixed):
                fail("command scope mismatch")
            if (
                command.adapter != "python.pytest.v1"
                and command.observed_paths != expected.observed_paths
            ):
                fail("non-partitioned proof mismatch")


def required_targets(repository: Path, profile: quality_profile.QualityEvidence) -> tuple[str, ...]:
    records: list[git_changes.CommandRecord] = []
    identity = git_changes.inspect_repository(
        repository, profile.base_sha, profile.head_sha, records
    )
    base = contract.parse_contract(
        git_changes.read_regular_blob(
            repository, profile.base_sha, ".supportability.toml", records
        ).content
    )
    head = contract.parse_contract(
        git_changes.read_regular_blob(
            repository, profile.head_sha, ".supportability.toml", records
        ).content
    )
    changes = git_changes.changed_paths(repository, profile.base_sha, profile.head_sha, records)
    policy = head if gate_policy.is_allowed_contract_transition(base, head, changes) else base
    return tuple(
        collector._runtime_test_targets(
            repository, identity, policy, changes, profile.source_files, records
        )
    )


def combined_coverage(parts: list[Path], repository: Path, output: Path) -> dict[str, Any]:
    merged = CoverageData(basename=str(output / ".coverage"))

    def map_source(path: str) -> str:
        relative = path.removeprefix("/target/")
        resolved = (repository / relative).resolve()
        if (
            relative == path
            or not resolved.is_relative_to(repository.resolve())
            or not resolved.is_file()
        ):
            fail("unverifiable coverage source")
        return str(resolved)

    for part in parts:
        data = CoverageData(basename=str(part / "python-shard" / ".coverage"))
        data.read()
        if data and not data.has_arcs():
            fail("missing branch data")
        merged.update(data, map_path=map_source)
    if not merged.has_arcs():
        fail("no combined branch coverage")
    merged.write()
    configuration = output / "coverage.ini"
    configuration.write_text("[report]\nexclude_lines =\n", encoding="utf-8", newline="\n")
    command = [
        sys.executable,
        "-I",
        "-m",
        "coverage",
        "json",
        f"--rcfile={configuration}",
        f"--data-file={output / '.coverage'}",
        "-o",
        str(output / "coverage.json"),
        "-q",
    ]
    subprocess.run(command, cwd=repository, check=True, capture_output=True, timeout=60)
    result = read_json(output / "coverage.json")
    if not isinstance(result, dict):
        fail("malformed combined coverage report")
    return result


def combined_python(
    parts: list[Path],
    profiles: list[quality_profile.QualityEvidence],
    repository: Path,
    output: Path,
) -> quality_profile.GateResult:
    full: tuple[str, ...] | None = None
    completions = []
    for index, part in enumerate(parts):
        raw = (part / "python-shard" / "pytest-completion.json").read_bytes()
        completion = json.loads(raw)
        collection = validate_partition(
            read_json(part / "python-shard" / "pytest-partition.json"), completion, raw, index
        )
        if full is not None and full != collection:
            fail("full collections differ")
        full = collection
        completions.append(completion)
    output.mkdir(parents=True, exist_ok=True)
    targets = required_targets(repository, profiles[0])
    if any(
        tuple(read_json(part / "python-shard" / "required-targets.json")) != targets
        for part in parts
    ):
        fail("coverage obligations mismatch")
    report = combined_coverage(parts, repository, output)
    observed, _zero = quality_profile.python_coverage_observation(
        report, profiles[0].source_files, targets
    )
    commands = [
        next(command for command in profile.commands if command.adapter == "python.pytest.v1")
        for profile in profiles
    ]
    digests = {}
    for stream in ("stdout", "stderr"):
        payload = {
            "schema_version": "quality-shard-stream-digests.v1",
            "shards": [getattr(command, f"{stream}_sha256") for command in commands],
        }
        path = output / f"{stream}.json"
        write_json(path, payload)
        digests[stream] = hashlib.sha256(path.read_bytes()).hexdigest()
    summary = {
        "full_collection_count": len(full or ()),
        "completed": sum(item["completed"] for item in completions),
        "shards": completions,
    }
    if summary["completed"] != summary["full_collection_count"]:
        fail("test collection was not covered exactly once")
    write_json(output / "completion.json", summary)
    return replace(
        commands[0],
        observed_paths=observed,
        raw_proof_sha256=hashlib.sha256((output / "coverage.json").read_bytes()).hexdigest(),
        stdout_sha256=digests["stdout"],
        stderr_sha256=digests["stderr"],
    )


def aggregate(arguments: argparse.Namespace, parts: list[Path]) -> quality_profile.QualityEvidence:
    profiles = validate_profiles(parts, arguments)
    output = Path(arguments.output)
    repository = Path(arguments.repository).resolve()
    commands = profiles[0].commands
    selected = profiles[0]
    if has_failure(profiles):
        selected = next(profile for profile in profiles if has_failure([profile]))
        commands = selected.commands
    elif profiles[0].language in {"python", "mixed"}:
        combined = combined_python(parts, profiles, repository, output.parent / "python-combined")
        commands = tuple(
            combined if item.adapter == combined.adapter else item for item in commands
        )
    result = replace(selected, job="quality-profile", commands=commands)
    pending = output.with_suffix(".pending")
    quality_profile.write_evidence(result, pending)
    origins = [read_json(part / "isolation-provenance.json") for part in parts]
    # Retain actual invocation receipts; the aggregate explicitly identifies its derivation.
    write_json(
        output.parent / "isolation-provenance.json",
        {
            "schema_version": "isolated-target-provenance.sharded.v1",
            "quality_evidence_sha256": hashlib.sha256(pending.read_bytes()).hexdigest(),
            "base_sha": result.base_sha,
            "head_sha": result.head_sha,
            "workflow_sha": result.workflow_sha,
            "run_id": result.run_id,
            "run_attempt": result.run_attempt,
            "job": result.job,
            "shards": origins,
        },
    )
    pending.replace(output)
    return result


def main() -> int:
    parser = collector._parser()
    parser.add_argument("--plan", action="store_true")
    arguments = parser.parse_args()
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        fail("non-hosted aggregation")
    if arguments.plan:
        records: list[git_changes.CommandRecord] = []
        repository = Path(arguments.repository).resolve()
        base = contract.parse_contract(
            git_changes.read_regular_blob(
                repository, arguments.base_ref, ".supportability.toml", records
            ).content
        )
        head = contract.parse_contract(
            git_changes.read_regular_blob(
                repository, arguments.head_ref, ".supportability.toml", records
            ).content
        )
        changes = git_changes.changed_paths(
            repository, arguments.base_ref, arguments.head_ref, records
        )
        policy = head if gate_policy.is_allowed_contract_transition(base, head, changes) else base
        count = 4 if policy.language in {"python", "mixed"} else 1
        print("matrix=" + json.dumps({"shard": list(range(count))}))
        print(f"count={count}")
        return 0
    root = Path(arguments.output).parent
    root.mkdir(parents=True, exist_ok=True)
    try:
        parts = native_parts(arguments, root)
        result = aggregate(arguments, parts)
    except Exception as error:
        write_json(
            root / "aggregation-failure.json",
            {"code": getattr(error, "code", "QUALITY_AGGREGATION_FAILED"), "message": str(error)},
        )
        print(getattr(error, "code", "QUALITY_AGGREGATION_FAILED"))
        return 2
    return int(has_failure([result]))


if __name__ == "__main__":
    raise SystemExit(main())
