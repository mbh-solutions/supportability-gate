"""Trusted GitHub-hosted quality command runner."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from dataclasses import asdict
from pathlib import Path

from supportability_gate import (
    architecture_policy,
    contract,
    gate_policy,
    git_changes,
    quality_profile,
    quality_runner,
)

MAX_DIAGNOSTIC_BYTES = 8192
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_CREDENTIAL = re.compile(
    r"(?i)\b(token|password|secret|authorization)\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s]+"
)
_SANDBOX_DENIALS = (b"Read-only file system", b"Errno 30", b"EROFS")
_PROVENANCE_SCHEMA = "isolated-target-provenance.v1"


def _container_failure(code: str, completed: subprocess.CompletedProcess[bytes]) -> None:
    if completed.returncode:
        raise quality_profile.QualityProfileError(
            code,
            (completed.stderr or completed.stdout).decode("utf-8", errors="replace")[:1000],
        )


def _prepare_container() -> str:
    docker = subprocess.run(
        [
            "docker",
            "pull",
            "--platform",
            quality_runner.CONTAINER_PLATFORM,
            quality_runner.CONTAINER_IMAGE,
        ],
        check=False,
        capture_output=True,
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    _container_failure("TARGET_SANDBOX_IMAGE_FAILED", docker)
    inspected = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", quality_runner.CONTAINER_IMAGE],
        check=False,
        capture_output=True,
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    _container_failure("TARGET_SANDBOX_IMAGE_FAILED", inspected)
    image_id = inspected.stdout.decode().strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise quality_profile.QualityProfileError(
            "TARGET_SANDBOX_IMAGE_FAILED", "container image ID is not immutable"
        )
    return image_id


def _distribution_receipts(paths: tuple[Path, ...] | None = None) -> tuple[str, ...]:
    receipts: list[str] = []
    distributions = (
        importlib.metadata.distributions(path=[str(path) for path in paths])
        if paths
        else importlib.metadata.distributions()
    )
    for distribution in distributions:
        name = distribution.metadata.get("Name")
        if not name:
            continue
        record = distribution.read_text("RECORD") or distribution.read_text("METADATA")
        if record is None:
            raise quality_profile.QualityProfileError("UNVERIFIABLE_DEPENDENCY_IDENTITY", str(name))
        receipts.append(
            f"python:{name.lower()}=={distribution.version}:sha256:{quality_profile._sha256(record.encode())}"
        )
    return tuple(sorted(set(receipts)))


def _node_lock_receipts(root: Path, kind: str) -> tuple[str, ...]:
    path = root / "package-lock.json"
    if not path.is_file():
        return ()
    raw = path.read_bytes()
    data = json.loads(raw)
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise quality_profile.QualityProfileError(
            "UNVERIFIABLE_DEPENDENCY_IDENTITY", f"{kind} package-lock"
        )
    receipts = [f"npm-{kind}-lock:sha256:{quality_profile._sha256(raw)}"]
    for location, value in packages.items():
        if not location or not isinstance(value, dict):
            continue
        name, version, integrity = value.get("name"), value.get("version"), value.get("integrity")
        if not all(isinstance(item, str) and item for item in (name, version)):
            manifest_identity = _node_manifest_identity(root, location)
            if manifest_identity is not None:
                name, version = manifest_identity
        if not all(isinstance(item, str) and item for item in (name, version)):
            continue
        identity = (
            integrity
            if isinstance(integrity, str) and integrity
            else _node_tree_hash(root, location)
        )
        receipts.append(f"npm-{kind}:{name}@{version}:{identity}")
    return tuple(sorted(set(receipts)))


def _node_manifest_identity(root: Path, location: str) -> tuple[object, object] | None:
    try:
        package = (root / location).resolve(strict=True)
        package.relative_to(root.resolve(strict=True))
        manifest = package / "package.json"
        if not manifest.is_file():
            return None
        payload = json.loads(manifest.read_bytes())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise quality_profile.QualityProfileError(
            "UNVERIFIABLE_DEPENDENCY_IDENTITY", location
        ) from error
    if not isinstance(payload, dict):
        raise quality_profile.QualityProfileError("UNVERIFIABLE_DEPENDENCY_IDENTITY", location)
    return payload.get("name"), payload.get("version")


def _node_tree_hash(root: Path, location: str) -> str:
    try:
        package = (root / location).resolve(strict=True)
        package.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise quality_profile.QualityProfileError(
            "UNVERIFIABLE_DEPENDENCY_IDENTITY", location
        ) from error
    candidates = tuple(package.rglob("*"))
    if any(path.is_symlink() for path in candidates):
        raise quality_profile.QualityProfileError("UNVERIFIABLE_DEPENDENCY_IDENTITY", location)
    files = tuple(sorted(path for path in candidates if path.is_file()))
    if not files:
        raise quality_profile.QualityProfileError("UNVERIFIABLE_DEPENDENCY_IDENTITY", location)
    manifest = [
        [path.relative_to(package).as_posix(), quality_profile._sha256(path.read_bytes())]
        for path in files
    ]
    return f"sha256:{quality_profile._sha256((json.dumps(manifest, separators=(',', ':')) + chr(10)).encode())}"


def _runtime_probe(
    executable: str,
    repository: Path,
    output: Path,
    adapter: str,
) -> str:
    plan = quality_runner.CommandPlan(adapter, (executable, "--version"), (), "provisioning", ())
    command = quality_runner.sandbox_command(
        plan,
        repository=repository,
        output=output,
        collector=Path(__file__).resolve().parent,
    )
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    _container_failure("TARGET_SANDBOX_RUNTIME_FAILED", completed)
    return (completed.stdout or completed.stderr).decode().strip()


def _stage_node_target(
    repository: Path,
    head_sha: str,
    output: Path,
    records: list[git_changes.CommandRecord],
) -> None:
    destination = quality_runner.trusted_directory(output) / "target-dependencies"
    destination.mkdir(parents=True, exist_ok=True)
    paths = _materialize_git_tree(repository, head_sha, destination, records)
    if not set(paths).issuperset({"package.json", "package-lock.json"}):
        raise quality_profile.QualityProfileError(
            "TARGET_DEPENDENCY_MANIFEST_MISSING", "package.json and package-lock.json are required"
        )


def _materialize_git_tree(
    repository: Path,
    head_sha: str,
    destination: Path,
    records: list[git_changes.CommandRecord],
) -> tuple[str, ...]:
    paths = git_changes.list_regular_blobs(repository, head_sha, (".",), records)
    for item in paths:
        blob = git_changes.read_regular_blob(repository, head_sha, item.path, records)
        path = destination / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob.content)
    return tuple(item.path for item in paths)


def _verify_materialized_source(
    destination: Path, receipts: tuple[quality_profile.SourceReceipt, ...]
) -> None:
    if any(
        not (destination / item.path).is_file()
        or quality_profile._sha256((destination / item.path).read_bytes()) != item.content_sha256
        for item in receipts
    ):
        raise quality_profile.QualityProfileError(
            "SOURCE_MATERIALIZATION_MISMATCH", "materialized source differs from Git blobs"
        )


def _runtime_receipts(
    language: str,
    repository: Path,
    output: Path,
) -> tuple[str, str, tuple[str, ...]]:
    python_version = _runtime_probe(sys.executable, repository, output, "python-runtime-probe")
    python_runtime = (
        f"{python_version}:sha256:{quality_profile._sha256(Path(sys.executable).read_bytes())}"
    )
    node_runtime = ""
    if language in {"typescript", "mixed"}:
        node_executable = Path(subprocess.check_output(["which", "node"], text=True).strip())
        node_version = _runtime_probe(
            str(node_executable), repository, output, "node-runtime-probe"
        )
        node_runtime = (
            f"{node_version}:sha256:{quality_profile._sha256(node_executable.read_bytes())}"
        )
    trusted = quality_runner.trusted_directory(output)
    dependencies = (
        *_distribution_receipts(),
        *_node_lock_receipts(trusted / "quality-tools", "tool"),
        *_node_lock_receipts(trusted / "target-dependencies", "target"),
    )
    return python_runtime, node_runtime, tuple(sorted(set(dependencies)))


def _sanitize_diagnostic(content: bytes, roots: tuple[Path, ...]) -> tuple[bytes, bool]:
    text = content.decode("utf-8", errors="replace")
    for root in roots:
        rendered = str(root.resolve())
        text = text.replace(rendered, "<workspace>").replace(
            rendered.replace("\\", "/"), "<workspace>"
        )
    for name in ("GITHUB_WORKSPACE", "RUNNER_TEMP"):
        if value := os.environ.get(name):
            text = text.replace(value, f"<{name.lower()}>")
    for name, value in os.environ.items():
        if (
            value
            and len(value) >= 8
            and re.search(r"TOKEN|PASSWORD|SECRET|AUTHORIZATION", name, re.I)
        ):
            text = text.replace(value, "[REDACTED]")
    text = _ANSI_ESCAPE.sub("", text)
    text = _CREDENTIAL.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = "".join(character for character in text if character in "\n\r\t" or ord(character) >= 32)
    encoded = text.encode("utf-8")
    truncated = len(encoded) > MAX_DIAGNOSTIC_BYTES
    retained = encoded[:MAX_DIAGNOSTIC_BYTES].decode("utf-8", errors="ignore").encode("utf-8")
    return retained, truncated


def _diagnostic_name(stage: str, adapter: str | None) -> str:
    identity = f"{stage}--{adapter or 'stage'}"
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", identity)


def _write_diagnostic(
    output: Path,
    *,
    stage: str,
    code: str,
    adapter: str | None,
    stdout: bytes,
    stderr: bytes,
    roots: tuple[Path, ...],
    identity: dict[str, str] | None,
) -> None:
    directory = output / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    name = _diagnostic_name(stage, adapter)
    logs = []
    for stream, content in (("stdout", stdout), ("stderr", stderr)):
        retained, truncated = _sanitize_diagnostic(content, roots)
        path = directory / f"{name}.{stream}.log"
        path.write_bytes(retained)
        logs.append(
            {
                "path": path.relative_to(directory).as_posix(),
                "retained_bytes": len(retained),
                "sha256": quality_profile._sha256(retained),
                "stream": stream,
                "truncated": truncated,
            }
        )
    payload = {
        "adapter": adapter,
        "code": code,
        "identity": identity,
        "logs": logs,
        "schema_version": "stage-diagnostic.v1",
        "stage": stage,
    }
    (directory / f"{name}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _retain_diagnostic(
    output: Path,
    *,
    stage: str,
    code: str,
    adapter: str | None = None,
    stdout: bytes = b"",
    stderr: bytes = b"",
    roots: tuple[Path, ...] = (),
    identity: dict[str, str] | None = None,
) -> None:
    try:
        _write_diagnostic(
            output,
            stage=stage,
            code=code,
            adapter=adapter,
            stdout=stdout,
            stderr=stderr,
            roots=roots,
            identity=identity,
        )
    except Exception:
        # Diagnostic rendering must never change the independently captured result.
        pass


def _manifest_proof(paths: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    raw = (json.dumps(list(paths), sort_keys=True) + "\n").encode()
    return paths, (), quality_profile._sha256(raw)


def _python_coverage_proof(
    plan: quality_runner.CommandPlan,
    repository: Path,
    output: Path,
    trusted_zero: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], str, int]:
    report = output / "coverage.json"
    config = "/trusted/coverage.ini"
    proof = quality_runner.CommandPlan(
        plan.adapter,
        (
            plan.actual[0],
            "-I",
            "-m",
            "coverage",
            "json",
            f"--rcfile={config}",
            "--data-file=/work/.coverage",
            "-o",
            "/work/coverage.json",
            "-q",
        ),
        (),
        "runtime-lines",
        plan.source_files,
    )
    completed = subprocess.run(
        quality_runner.sandbox_command(
            proof,
            repository=repository,
            output=output.parent.parent,
            collector=Path(__file__).resolve().parent,
        ),
        check=False,
        capture_output=True,
        timeout=quality_profile.TIMEOUT_SECONDS,
    )
    if completed.returncode or not report.is_file():
        return (), (), quality_profile._sha256(b""), completed.returncode or -2
    raw = report.read_bytes()
    observed, _reported_zero = quality_profile.python_coverage_observation(
        json.loads(raw), plan.source_files
    )
    return observed, trusted_zero, quality_profile._sha256(raw), 0


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
    plan: quality_runner.CommandPlan,
    repository: Path,
    output: Path,
    trusted_zero: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], str, int]:
    report = output / "coverage.lcov"
    if not report.is_file():
        return (), (), quality_profile._sha256(b""), -2
    raw = report.read_bytes()
    observed, _reported_zero = quality_profile.typescript_lcov_observation(
        raw.decode("utf-8"), plan.source_files, Path("/target")
    )
    return observed, trusted_zero, quality_profile._sha256(raw), 0


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
    plan: quality_runner.CommandPlan,
    repository: Path,
    head_sha: str,
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    parsed = {
        path: architecture_policy.source_imports(
            path, git_changes.read_regular_blob(repository, head_sha, path, records).content
        )
        for path in plan.source_files
    }
    raw = (json.dumps(parsed, sort_keys=True) + "\n").encode()
    return tuple(parsed), (), quality_profile._sha256(raw)


def _proof(
    plan: quality_runner.CommandPlan,
    repository: Path,
    execution_target: Path,
    output: Path,
    source_receipts: tuple[quality_profile.SourceReceipt, ...],
    head_sha: str,
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[str, ...], tuple[str, ...], str, int]:
    trusted_zero = tuple(
        item.path
        for item in source_receipts
        if item.path in plan.source_files and item.zero_statement_eligible
    )
    if plan.adapter == "python.pytest.v1":
        return _python_coverage_proof(plan, execution_target, output, trusted_zero)
    if plan.adapter == "typescript.test.v1":
        return _typescript_coverage_proof(plan, repository, output, trusted_zero)
    if plan.adapter == "python.build-wheel.v1":
        observed, zero, digest = _wheel_proof(plan, output)
    elif plan.adapter == "typescript.build.v1":
        observed, zero, digest = _typescript_build_proof(plan, output)
    elif plan.adapter in {"python.import-linter.v1", "typescript.import-boundaries.v1"}:
        observed, zero, digest = _parser_proof(plan, repository, head_sha, records)
    elif plan.proof_kind == "provisioning":
        observed, zero, digest = (), (), quality_profile._sha256(b"")
    else:
        observed, zero, digest = _manifest_proof(plan.source_files)
    return observed, zero, digest, 0


def _observation_provenance(plan: quality_runner.CommandPlan) -> str:
    if plan.proof_kind == "provisioning":
        return "provisioning-supervisor"
    if plan.proof_kind in {"artifact-members", "compiler-output", "runtime-lines"}:
        return "target-generated-untrusted"
    return "supervisor-observed"


def _record_command_provenance(
    records: list[dict[str, object]] | None,
    plan: quality_runner.CommandPlan,
    invocation: tuple[str, ...],
    result: quality_profile.GateResult,
) -> None:
    if records is None:
        return
    records.append(
        {
            "adapter": result.adapter,
            "executed": result.executed,
            "executed_arguments": list(result.executed_arguments),
            "exit_code": result.exit_code,
            "invocation_sha256": quality_profile._sha256(
                (json.dumps(invocation, separators=(",", ":")) + "\n").encode()
            ),
            "observation_provenance": _observation_provenance(plan),
            "proof_kind": result.proof_kind,
            "raw_proof_sha256": result.raw_proof_sha256,
            "stderr_sha256": result.stderr_sha256,
            "stdout_sha256": result.stdout_sha256,
        }
    )


def _run_command(
    plan: quality_runner.CommandPlan,
    repository: Path,
    output: Path,
    source_receipts: tuple[quality_profile.SourceReceipt, ...] = (),
    head_sha: str = "",
    records: list[git_changes.CommandRecord] | None = None,
    identity: dict[str, str] | None = None,
    provenance_records: list[dict[str, object]] | None = None,
    diagnostic_output: Path | None = None,
    execution_target: Path | None = None,
    evidence_plan: quality_runner.CommandPlan | None = None,
) -> quality_profile.GateResult:
    records = records or []
    work = quality_runner.command_work_directory(output, plan.adapter)
    work.mkdir(parents=True, exist_ok=True)
    provisioning = plan.proof_kind == "provisioning"
    approved = plan.adapter in {
        *quality_profile.required_adapters("python"),
        *quality_profile.required_adapters("typescript"),
    }
    sandboxed = approved and not provisioning
    mounted_target = execution_target or repository
    sandbox_workdir = None
    if plan.adapter == "python.build-wheel.v1":
        build_source = work / "source"
        build_source.mkdir(parents=True, exist_ok=True)
        _materialize_git_tree(repository, head_sha, build_source, records)
        sandbox_workdir = "/work/source"
    actual = (
        quality_runner.provisioning_command(plan, output)
        if provisioning
        else quality_runner.sandbox_command(
            plan,
            repository=mounted_target,
            output=output,
            collector=Path(__file__).resolve().parent,
            workdir=sandbox_workdir,
        )
        if sandboxed
        else plan.actual
    )
    try:
        completed = subprocess.run(
            actual,
            cwd=(
                quality_runner.trusted_directory(output) / "target-dependencies"
                if plan.adapter == "typescript.target-install.v1"
                else work
                if provisioning
                else repository
                if not sandboxed
                else None
            ),
            env=quality_runner.fixed_environment(output, mounted_target),
            check=False,
            capture_output=True,
            timeout=quality_profile.TIMEOUT_SECONDS,
        )
        combined = completed.stdout + completed.stderr
        if sandboxed and any(marker in combined for marker in _SANDBOX_DENIALS):
            _retain_diagnostic(
                diagnostic_output or output,
                stage="quality-command",
                code="TARGET_SANDBOX_WRITE_DENIED",
                adapter=plan.adapter,
                stdout=completed.stdout,
                stderr=completed.stderr,
                roots=(repository, mounted_target, output),
                identity=identity,
            )
            raise quality_profile.QualityProfileError("TARGET_SANDBOX_WRITE_DENIED", plan.adapter)
        if completed.returncode == 125 and sandboxed:
            raise quality_profile.QualityProfileError("TARGET_SANDBOX_RUNTIME_FAILED", plan.adapter)
        observed, zero_statement, raw_digest, proof_exit = _proof(
            plan, repository, mounted_target, work, source_receipts, head_sha, records
        )
        exit_code = completed.returncode or proof_exit
        if exit_code:
            _retain_diagnostic(
                diagnostic_output or output,
                stage="quality-command",
                code=("QUALITY_COMMAND_FAILED" if completed.returncode else "QUALITY_PROOF_FAILED"),
                adapter=plan.adapter,
                stdout=completed.stdout,
                stderr=completed.stderr,
                roots=(repository, mounted_target, output),
                identity=identity,
            )
        public = evidence_plan or plan
        result = quality_profile.GateResult(
            public.adapter,
            public.evidence,
            public.proof_kind,
            observed,
            zero_statement,
            True,
            exit_code,
            quality_profile._sha256(completed.stderr),
            quality_profile._sha256(completed.stdout),
            raw_digest,
            public.actual,
        )
        _record_command_provenance(provenance_records, public, actual, result)
        return result
    except subprocess.TimeoutExpired as error:
        cidfile = output / "container-ids" / f"{plan.adapter}.cid"
        if cidfile.is_file():
            subprocess.run(
                ["docker", "rm", "--force", cidfile.read_text().strip()],
                check=False,
                capture_output=True,
                timeout=30,
            )
        _retain_diagnostic(
            diagnostic_output or output,
            stage="quality-command",
            code="QUALITY_COMMAND_TIMEOUT",
            adapter=plan.adapter,
            stdout=error.stdout or b"",
            stderr=error.stderr or b"",
            roots=(repository, mounted_target, output),
            identity=identity,
        )
        public = evidence_plan or plan
        result = quality_profile.GateResult(
            public.adapter,
            public.evidence,
            public.proof_kind,
            (),
            (),
            True,
            -1,
            quality_profile._sha256(error.stderr or b""),
            quality_profile._sha256(error.stdout or b""),
            quality_profile._sha256(b""),
            public.actual,
        )
        _record_command_provenance(provenance_records, public, actual, result)
        return result
    except OSError as error:
        rendered = str(error).encode(errors="replace")
        _retain_diagnostic(
            diagnostic_output or output,
            stage="quality-command",
            code="QUALITY_COMMAND_SETUP_FAILED",
            adapter=plan.adapter,
            stderr=rendered,
            roots=(repository, mounted_target, output),
            identity=identity,
        )
        public = evidence_plan or plan
        result = quality_profile.GateResult(
            public.adapter,
            public.evidence,
            public.proof_kind,
            (),
            (),
            False,
            -127,
            quality_profile._sha256(rendered),
            quality_profile._sha256(b""),
            quality_profile._sha256(b""),
            public.actual,
        )
        _record_command_provenance(provenance_records, public, actual, result)
        return result


def _write_capture_provenance(
    output: Path,
    evidence: quality_profile.QualityEvidence,
    source_receipts: tuple[quality_profile.SourceReceipt, ...],
    container_id: str,
    python_runtime: str,
    node_runtime: str,
    dependencies: tuple[str, ...],
    commands: list[dict[str, object]],
) -> None:
    expected_commands = [
        {
            "adapter": result.adapter,
            "executed": result.executed,
            "executed_arguments": list(result.executed_arguments),
            "exit_code": result.exit_code,
            "proof_kind": result.proof_kind,
            "raw_proof_sha256": result.raw_proof_sha256,
            "stderr_sha256": result.stderr_sha256,
            "stdout_sha256": result.stdout_sha256,
        }
        for result in evidence.commands
    ]
    observed_commands = [
        {
            key: value
            for key, value in command.items()
            if key not in {"invocation_sha256", "observation_provenance"}
        }
        for command in commands
    ]
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", container_id) is None
        or re.fullmatch(r".+:sha256:[0-9a-f]{64}", python_runtime) is None
        or (
            evidence.language in {"typescript", "mixed"}
            and re.fullmatch(r".+:sha256:[0-9a-f]{64}", node_runtime) is None
        )
        or not dependencies
        or dependencies != tuple(sorted(set(dependencies)))
        or tuple(item.path for item in source_receipts) != evidence.source_files
        or observed_commands != expected_commands
        or any(
            command.get("observation_provenance")
            not in {"provisioning-supervisor", "supervisor-observed", "target-generated-untrusted"}
            or re.fullmatch(r"[0-9a-f]{64}", str(command.get("invocation_sha256"))) is None
            for command in commands
        )
    ):
        raise quality_profile.QualityProfileError(
            "UNVERIFIABLE_CAPTURE_PROVENANCE", "capture provenance is incomplete"
        )
    evidence_bytes = (json.dumps(asdict(evidence), indent=2, sort_keys=True) + "\n").encode()
    digest = quality_runner.CONTAINER_IMAGE.split("@", 1)[1]
    payload = {
        "base_sha": evidence.base_sha,
        "commands": commands,
        "container": {
            "digest": digest,
            "id": container_id,
            "image": quality_runner.CONTAINER_IMAGE,
            "platform": quality_runner.CONTAINER_PLATFORM,
        },
        "head_sha": evidence.head_sha,
        "job": evidence.job,
        "quality_evidence_sha256": quality_profile._sha256(evidence_bytes),
        "repository": evidence.repository,
        "repository_id": evidence.repository_id,
        "resolved_dependencies": list(dependencies),
        "run_attempt": evidence.run_attempt,
        "run_id": evidence.run_id,
        "runtime": {"node": node_runtime, "python": python_runtime},
        "schema_version": _PROVENANCE_SCHEMA,
        "security_controls": {
            "capabilities": "drop-all",
            "collector_mount": "read-only",
            "cpus": quality_runner.CONTAINER_CPUS,
            "evidence_mount": "read-only",
            "memory": quality_runner.CONTAINER_MEMORY,
            "network": "none",
            "no_new_privileges": True,
            "pids_limit": quality_runner.CONTAINER_PIDS_LIMIT,
            "root_filesystem": "read-only",
            "source_mount": "read-only",
            "tool_mounts": "read-only",
            "writable_mount": "per-command /work only",
        },
        "source_receipts": [asdict(item) for item in source_receipts],
        "workflow_sha": evidence.workflow_sha,
    }
    path = output.parent / "isolation-provenance.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
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
    changes = git_changes.changed_paths(target, identity.base_sha, identity.head_sha, records)
    policy = (
        candidate_policy
        if gate_policy.is_allowed_contract_transition(base_policy, candidate_policy, changes)
        else base_policy
    )
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
    output.parent.mkdir(parents=True, exist_ok=True)
    production_files, source_files, test_files = quality_runner.profile_files(
        target, identity.head_sha, policy, records
    )
    receipts = quality_profile.asset_receipts(
        target, identity.head_sha, production_files, source_files, records
    )
    source_receipts = quality_profile.source_receipts(
        target, identity.head_sha, source_files, records
    )
    diagnostic_identity = {
        "base_sha": identity.base_sha,
        "head_sha": identity.head_sha,
        "job": "quality-profile",
        "repository": str(arguments.repository_name),
        "repository_id": str(arguments.repository_id),
        "run_attempt": str(arguments.run_attempt),
        "run_id": str(arguments.run_id),
        "workflow_sha": workflow_sha,
    }
    command_provenance: list[dict[str, object]] = []
    public_plans = quality_runner.command_plans(
        policy.language, target, output.parent, test_files, source_files
    )
    shutil.rmtree(quality_runner.trusted_directory(output.parent))
    with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP")) as temporary:
        supervisor = Path(temporary) / "supervisor"
        trusted = quality_runner.trusted_directory(supervisor)
        execution_target = trusted / "target-source"
        execution_target.mkdir(parents=True)
        _materialize_git_tree(target, identity.head_sha, execution_target, records)
        _verify_materialized_source(execution_target, source_receipts)
        if policy.language in {"typescript", "mixed"}:
            _stage_node_target(target, identity.head_sha, supervisor, records)
            (execution_target / "node_modules").mkdir()
        container_id = _prepare_container()
        plans = quality_runner.command_plans(
            policy.language, execution_target, supervisor, test_files, source_files
        )
        results = tuple(
            _run_command(
                plan,
                target,
                supervisor,
                source_receipts,
                identity.head_sha,
                records,
                diagnostic_identity,
                command_provenance,
                output.parent,
                execution_target,
                public_plan,
            )
            for plan, public_plan in zip(plans, public_plans, strict=True)
        )
        python_runtime, node_runtime, dependencies = _runtime_receipts(
            policy.language, execution_target, supervisor
        )
    evidence = quality_profile.QualityEvidence(
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
    _write_capture_provenance(
        output,
        evidence,
        source_receipts,
        container_id,
        python_runtime,
        node_runtime,
        dependencies,
        command_provenance,
    )
    return evidence


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


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    output = Path(arguments.output)
    if not output.is_absolute():
        print("RELATIVE_QUALITY_OUTPUT")
        return 2
    try:
        evidence = run_profile(arguments)
        quality_profile.write_evidence(evidence, output)
    except Exception as error:
        code = getattr(error, "code", "UNEXPECTED_QUALITY_PROFILE_FAILURE")
        if code != "NON_HOSTED_TARGET_EXECUTION":
            _retain_diagnostic(
                output.parent,
                stage="quality-profile",
                code=code,
                stderr=traceback.format_exc().encode(errors="replace"),
                roots=(Path(arguments.repository), output.parent),
                identity={
                    "base_sha": str(arguments.base_ref),
                    "head_sha": str(arguments.head_ref),
                    "job": "quality-profile",
                    "repository": str(arguments.repository_name),
                    "repository_id": str(arguments.repository_id),
                    "run_attempt": str(arguments.run_attempt),
                    "run_id": str(arguments.run_id),
                    "workflow_sha": str(arguments.workflow_sha),
                },
            )
        print(code)
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
