"""Trusted GitHub-hosted characterization capture entry point."""

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
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

from supportability_gate import characterization, contract, git_changes, quality_runner

EXECUTION_TIMEOUT_SECONDS = 120
MAX_DIAGNOSTIC_BYTES = 8192
_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_CREDENTIAL = re.compile(
    r"(?i)\b(token|password|secret|authorization)\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s]+"
)
_SANDBOX_DENIALS = (b"Read-only file system", b"Errno 30", b"EROFS")


def _prepare_container() -> str:
    commands = (
        (
            "TARGET_SANDBOX_IMAGE_FAILED",
            [
                "docker",
                "pull",
                "--platform",
                quality_runner.CONTAINER_PLATFORM,
                quality_runner.CONTAINER_IMAGE,
            ],
        ),
        (
            "TARGET_SANDBOX_IMAGE_FAILED",
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                quality_runner.CONTAINER_IMAGE,
            ],
        ),
    )
    image_id = ""
    for code, command in commands:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=EXECUTION_TIMEOUT_SECONDS,
        )
        if completed.returncode:
            raise characterization.CharacterizationError(code)
        image_id = completed.stdout.decode().strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise characterization.CharacterizationError("TARGET_SANDBOX_IMAGE_FAILED")
    return image_id


def _dependency_receipts(destination: Path) -> tuple[str, ...]:
    receipts: list[str] = []
    for distribution in importlib.metadata.distributions(path=[str(destination)]):
        name = distribution.metadata.get("Name")
        if not name:
            continue
        record = distribution.read_text("RECORD") or distribution.read_text("METADATA")
        if record is None:
            raise characterization.CharacterizationError("UNVERIFIABLE_DEPENDENCY_IDENTITY")
        receipts.append(
            f"python:{name.lower()}=={distribution.version}:sha256:{characterization._sha256(record.encode())}"
        )
    return tuple(sorted(set(receipts)))


def _runtime_probe(
    target: Path,
    output: Path,
    executable: str,
    adapter: str,
    diagnostics: Path | None = None,
    stage: str = "characterization-runtime",
    identity: dict[str, str] | None = None,
) -> str:
    resolved = (
        Path(executable) if Path(executable).is_absolute() else Path(shutil.which(executable) or "")
    )
    if not resolved.is_file():
        raise characterization.CharacterizationError("TARGET_SANDBOX_RUNTIME_FAILED")
    plan = quality_runner.CommandPlan(adapter, (str(resolved), "--version"), (), "provisioning", ())
    try:
        completed = subprocess.run(
            quality_runner.sandbox_command(
                plan,
                repository=target,
                output=output,
                collector=Path(__file__).resolve().parent,
            ),
            check=False,
            capture_output=True,
            timeout=EXECUTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout or b"", error.stderr or b""
        if diagnostics is not None and identity is not None:
            _retain_diagnostic(
                diagnostics,
                stage=stage,
                code="TARGET_SANDBOX_RUNTIME_FAILED",
                adapter=adapter,
                stdout=stdout,
                stderr=stderr,
                roots=(target, output, diagnostics),
                identity=identity,
            )
        raise characterization.CharacterizationError("TARGET_SANDBOX_RUNTIME_FAILED") from error
    if completed.returncode:
        if diagnostics is not None and identity is not None:
            _retain_diagnostic(
                diagnostics,
                stage=stage,
                code="TARGET_SANDBOX_RUNTIME_FAILED",
                adapter=adapter,
                stdout=completed.stdout,
                stderr=completed.stderr,
                roots=(target, output, diagnostics),
                identity=identity,
            )
        raise characterization.CharacterizationError("TARGET_SANDBOX_RUNTIME_FAILED")
    version = (completed.stdout or completed.stderr).decode().strip()
    return f"{version}:sha256:{characterization._sha256(resolved.read_bytes())}"


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


def _retain_diagnostic(
    output: Path,
    *,
    stage: str,
    code: str,
    adapter: str | None,
    stdout: bytes,
    stderr: bytes,
    roots: tuple[Path, ...],
    identity: dict[str, str],
) -> None:
    try:
        directory = output / "diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", f"{stage}--{adapter or 'stage'}")
        diagnostic_path = directory / f"{name}.json"
        if diagnostic_path.is_file():
            return
        logs = []
        for stream, content in (("stdout", stdout), ("stderr", stderr)):
            retained, truncated = _sanitize_diagnostic(content, roots)
            path = directory / f"{name}.{stream}.log"
            path.write_bytes(retained)
            logs.append(
                {
                    "path": path.name,
                    "retained_bytes": len(retained),
                    "sha256": characterization._sha256(retained),
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
        diagnostic_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    except Exception:
        # Diagnostic rendering must never change the independently captured result.
        pass


def _require_hosted_runner() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise characterization.CharacterizationError(
            "CHARACTERIZATION_REQUIRES_GITHUB_HOSTED_RUNNER"
        )


def _safe_environment(
    target: Path, definition: Path, dependencies: Path | None = None
) -> dict[str, str]:
    allowed = {"HOME", "LANG", "PATH", "RUNNER_TEMP", "SYSTEMROOT", "TEMP", "TMP", "WINDIR"}
    environment = {name: value for name, value in os.environ.items() if name in allowed}
    paths = [str(target / "src")]
    if dependencies is not None:
        paths.append(str(dependencies))
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["SUPPORTABILITY_CHARACTERIZATION_TARGET"] = str(target)
    environment["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"] = str(definition)
    return environment


def _python_dependencies(
    target: Path, target_sha: str, records: list[git_changes.CommandRecord]
) -> tuple[str, ...]:
    try:
        metadata = git_changes.read_regular_blob(target, target_sha, "pyproject.toml", records)
    except git_changes.GitError as error:
        if error.code == "MISSING_BLOB":
            return ()
        raise
    try:
        project = tomllib.loads(metadata.content.decode("utf-8")).get("project", {})
        dynamic = project.get("dynamic", [])
        dependencies = project.get("dependencies", [])
        if not isinstance(dynamic, list) or any(not isinstance(item, str) for item in dynamic):
            raise ValueError("invalid dynamic metadata")
        if "dependencies" in dynamic or not isinstance(dependencies, list):
            raise ValueError("runtime dependencies must be static")
        for item in dependencies:
            if (
                not isinstance(item, str)
                or any(character in item for character in "\r\n\0")
                or Requirement(item).url is not None
            ):
                raise ValueError("runtime dependencies must use index requirements")
        return tuple(dependencies)
    except (AttributeError, TypeError, ValueError) as error:
        raise characterization.CharacterizationError(
            "CHARACTERIZATION_PREREQUISITE_METADATA_INVALID"
        ) from error


def _install_python_dependencies(
    target: Path,
    target_sha: str,
    destination: Path,
    records: list[git_changes.CommandRecord],
    diagnostics: Path | None = None,
    stage: str = "characterization",
    identity: dict[str, str] | None = None,
) -> None:
    dependencies = _python_dependencies(target, target_sha, records)
    if not dependencies:
        return
    environment = {
        key: value
        for key, value in _safe_environment(target, target).items()
        if not key.startswith(("PYTHON", "SUPPORTABILITY_"))
    }
    environment["PIP_CONFIG_FILE"] = os.devnull
    try:
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--only-binary=:all:",
                "--ignore-installed",
                "--target",
                str(destination),
                "--index-url",
                "https://pypi.org/simple",
                *dependencies,
            ],
            cwd=destination,
            env=environment,
            check=True,
            capture_output=True,
            timeout=EXECUTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        code = "CHARACTERIZATION_PREREQUISITE_TIMEOUT"
        stdout, stderr = error.stdout or b"", error.stderr or b""
    except subprocess.CalledProcessError as error:
        code = "CHARACTERIZATION_PREREQUISITE_FAILED"
        stdout, stderr = error.stdout or b"", error.stderr or b""
    except OSError as error:
        code = "CHARACTERIZATION_PREREQUISITE_SETUP_FAILED"
        stdout, stderr = b"", str(error).encode(errors="replace")
    else:
        return
    if diagnostics is not None and identity is not None:
        _retain_diagnostic(
            diagnostics,
            stage=stage,
            code=code,
            adapter=None,
            stdout=stdout,
            stderr=stderr,
            roots=(target, destination, diagnostics),
            identity=identity,
        )
    raise characterization.CharacterizationError("CHARACTERIZATION_PREREQUISITE_FAILED")


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
    dependencies: Path | None = None,
    diagnostics: Path | None = None,
    stage: str = "characterization",
    identity: dict[str, str] | None = None,
    execution_output: Path | None = None,
) -> dict[str, object]:
    relative_driver, _ = characterization._scenario_paths(scenario, language)
    with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP")) as temporary:
        materialized = Path(temporary) / Path(relative_driver).name
        materialized.write_bytes(content)
        arguments, recorded = _command(language, relative_driver, materialized)
        container_driver = f"/driver/{materialized.name}"
        inner = (
            (arguments[0], "-P", container_driver)
            if language == "python"
            else (arguments[0], container_driver)
        )
        output = execution_output or Path(temporary) / "supervisor"
        plan = quality_runner.CommandPlan(
            f"characterization-{scenario.id}", inner, (), "runtime-lines", scenario.covers
        )
        mounts = [(definition, "/definition"), (Path(temporary), "/driver")]
        if dependencies is not None:
            mounts.append((dependencies, "/dependencies"))
        sandbox = quality_runner.sandbox_command(
            plan,
            repository=target,
            output=output,
            collector=Path(__file__).resolve().parent,
            extra_mounts=tuple(mounts),
            extra_environment={
                "PYTHONPATH": "/target/src:/dependencies",
                "SUPPORTABILITY_CHARACTERIZATION_DEFINITION": "/definition",
                "SUPPORTABILITY_CHARACTERIZATION_TARGET": "/target",
            },
        )
        try:
            completed = subprocess.run(
                sandbox,
                check=False,
                capture_output=True,
                timeout=EXECUTION_TIMEOUT_SECONDS,
            )
            stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
            if any(marker in stdout + stderr for marker in _SANDBOX_DENIALS):
                raise characterization.CharacterizationError(
                    "CHARACTERIZATION_SANDBOX_WRITE_DENIED"
                )
            if exit_code == 125:
                if diagnostics is not None and identity is not None:
                    _retain_diagnostic(
                        diagnostics,
                        stage=stage,
                        code="TARGET_SANDBOX_RUNTIME_FAILED",
                        adapter=scenario.id,
                        stdout=stdout,
                        stderr=stderr,
                        roots=(target, definition, diagnostics),
                        identity=identity,
                    )
                raise characterization.CharacterizationError("TARGET_SANDBOX_RUNTIME_FAILED")
        except subprocess.TimeoutExpired as error:
            cidfile = output / "container-ids" / f"characterization-{scenario.id}.cid"
            if cidfile.is_file():
                subprocess.run(
                    ["docker", "rm", "--force", cidfile.read_text().strip()],
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
            stdout, stderr, exit_code = error.stdout or b"", error.stderr or b"", -1
        except OSError as error:
            stdout, stderr, exit_code = b"", str(error).encode(errors="replace"), -127
    behavior, error_code = _behavior(stdout, scenario.id) if exit_code == 0 else (None, None)
    failure_code = (
        error_code
        or ("CHARACTERIZATION_TIMEOUT" if exit_code == -1 else None)
        or ("CHARACTERIZATION_COMMAND_SETUP_FAILED" if exit_code == -127 else None)
        or ("CHARACTERIZATION_COMMAND_FAILED" if exit_code else None)
    )
    if diagnostics is not None and identity is not None and failure_code is not None:
        _retain_diagnostic(
            diagnostics,
            stage=stage,
            code=failure_code,
            adapter=scenario.id,
            stdout=stdout,
            stderr=stderr,
            roots=(target, definition, diagnostics),
            identity=identity,
        )
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
    dependencies: Path | None = None,
    diagnostics: Path | None = None,
    stage: str = "characterization",
    identity: dict[str, str] | None = None,
    execution_output: Path | None = None,
) -> dict[str, object]:
    profile = characterization.scenario_language(scenario, language)
    driver_path, golden_path = characterization._scenario_paths(scenario, language)
    driver = git_changes.read_regular_blob(definition, definition_sha, driver_path, records)
    golden = git_changes.read_regular_blob(definition, definition_sha, golden_path, records)
    golden_behavior = characterization._read_json_bytes(golden.content, "MALFORMED_GOLDEN_OUTPUT")
    first = _run_driver(
        target,
        definition,
        scenario,
        profile,
        driver.content,
        dependencies,
        diagnostics,
        stage,
        identity,
        execution_output,
    )
    second = _run_driver(
        target,
        definition,
        scenario,
        profile,
        driver.content,
        dependencies,
        diagnostics,
        stage,
        identity,
        execution_output,
    )
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
    diagnostics: Path | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
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
        definition, head_sha, ".supportability.toml", records
    )
    policy = contract.parse_contract(policy_blob.content)
    manifest = characterization._manifest(definition, head_sha, records)
    container_id = _prepare_container()
    diagnostic_identity = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "job": job,
        "repository": repository,
        "repository_id": repository_id,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "workflow_sha": workflow_sha,
    }
    with tempfile.TemporaryDirectory(dir=os.environ.get("RUNNER_TEMP")) as temporary:
        supervisor_output = Path(temporary) / "supervisor"
        dependencies = (
            quality_runner.trusted_directory(supervisor_output) / "characterization-dependencies"
        )
        dependencies.mkdir(parents=True, exist_ok=True)
        if policy.language in {"python", "mixed"}:
            _install_python_dependencies(
                definition,
                head_sha,
                dependencies,
                records,
                diagnostics,
                f"characterization-{side}",
                diagnostic_identity,
            )
        python_runtime = _runtime_probe(
            target,
            supervisor_output,
            sys.executable,
            "characterization-python-runtime",
            diagnostics,
            f"characterization-{side}-runtime",
            diagnostic_identity,
        )
        node_runtime = ""
        if policy.language in {"typescript", "mixed"}:
            node_runtime = _runtime_probe(
                target,
                supervisor_output,
                "node",
                "characterization-node-runtime",
                diagnostics,
                f"characterization-{side}-runtime",
                diagnostic_identity,
            )
        resolved_dependencies = _dependency_receipts(dependencies)
        scenarios = [
            _scenario_capture(
                target,
                definition,
                head_sha,
                item,
                policy.language,
                records,
                dependencies,
                diagnostics,
                f"characterization-{side}",
                diagnostic_identity,
            )
            for item in manifest.scenarios
        ]
    fingerprint = characterization._sha256(
        characterization._canonical([[item["id"], item["behavior_sha256"]] for item in scenarios])
    )
    evidence = {
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
    environment = {
        "container_digest": quality_runner.CONTAINER_IMAGE.split("@", 1)[1],
        "container_id": container_id,
        "container_image": quality_runner.CONTAINER_IMAGE,
        "node_runtime": node_runtime,
        "python_runtime": python_runtime,
        "resolved_dependencies": list(resolved_dependencies),
    }
    return evidence, environment


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
        result, environment = capture_evidence(
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
            diagnostics=Path(arguments.output).parent,
        )
        output = Path(arguments.output)
        characterization._write_json(output, result)
        characterization._write_json(
            characterization._provenance_path(output),
            {
                "authentication": result["authentication"],
                "capture_sha256": characterization._sha256(output.read_bytes()),
                "environment": environment,
                "schema_version": characterization.PROVENANCE_SCHEMA,
            },
        )
    except Exception as error:
        code = getattr(error, "code", "UNEXPECTED_CHARACTERIZATION_FAILURE")
        output = Path(arguments.output)
        _retain_diagnostic(
            output.parent,
            stage=f"characterization-{arguments.side}",
            code=code,
            adapter=None,
            stdout=b"",
            stderr=f"{type(error).__name__}: {error}".encode(errors="replace"),
            roots=(Path(arguments.target_repository), Path(arguments.definition_repository)),
            identity={
                "base_sha": str(arguments.base_ref),
                "head_sha": str(arguments.head_ref),
                "job": str(arguments.job),
                "repository": str(arguments.repository),
                "repository_id": str(arguments.repository_id),
                "run_attempt": str(arguments.run_attempt),
                "run_id": str(arguments.run_id),
                "workflow_sha": str(arguments.workflow_sha),
            },
        )
        print(code)
        return 2
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
