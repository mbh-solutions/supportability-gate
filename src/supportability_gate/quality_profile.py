"""Run and verify fixed GitHub-hosted stack-native quality profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from supportability_gate import contract, git_changes
from supportability_gate.function_changes import ChangedFileAssessment

SCHEMA_VERSION = "quality-gates.v1"
TIMEOUT_SECONDS = 180
_FULL_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_SOURCE_SUFFIXES = {
    "python": (".py", ".pyi"),
    "typescript": (".cts", ".js", ".jsx", ".mts", ".ts", ".tsx"),
}
_PYTHON_COMMANDS = (
    (
        "python.ruff-lint.v1",
        (
            "$PYTHON",
            "-m",
            "ruff",
            "check",
            "src",
            "tests",
            "--select",
            "E4,E7,E9,F,I,UP",
            "--line-length",
            "100",
            "--target-version",
            "py312",
            "--isolated",
            "--no-cache",
        ),
    ),
    (
        "python.ruff-format.v1",
        (
            "$PYTHON",
            "-m",
            "ruff",
            "format",
            "--check",
            "src",
            "tests",
            "--line-length",
            "100",
            "--target-version",
            "py312",
            "--isolated",
            "--no-cache",
        ),
    ),
    (
        "python.c901-touched.v1",
        (
            "$PYTHON",
            "-m",
            "ruff",
            "check",
            "src",
            "--select",
            "C901",
            "--config",
            "lint.mccabe.max-complexity = 10",
            "--target-version",
            "py312",
            "--isolated",
            "--no-cache",
        ),
    ),
    (
        "python.mypy-strict.v1",
        (
            "$PYTHON",
            "-m",
            "mypy",
            "--config-file",
            "$OUTPUT/mypy.ini",
            "--cache-dir",
            "$OUTPUT/mypy-cache",
            "src",
        ),
    ),
    (
        "python.pytest.v1",
        ("$PYTHON", "-m", "pytest", "-q", "-c", "$OUTPUT/pytest.ini"),
    ),
    (
        "python.build-wheel.v1",
        (
            "$PYTHON",
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            "$OUTPUT/wheel",
        ),
    ),
    (
        "python.import-linter.v1",
        ("$LINT_IMPORTS", "--config", "$OUTPUT/importlinter.ini", "--no-cache"),
    ),
)
_TYPESCRIPT_COMMANDS = (
    (
        "typescript.tool-install.v1",
        (
            "$NPM",
            "install",
            "--prefix",
            "$TOOLS",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "eslint@9.39.3",
            "@typescript-eslint/parser@8.65.0",
            "prettier@3.9.6",
            "typescript@5.9.3",
            "dependency-cruiser@18.1.0",
        ),
    ),
    (
        "typescript.eslint.v1",
        (
            "$TOOLS/node_modules/.bin/eslint",
            "--config",
            "$TOOLS/eslint.config.mjs",
            "--no-config-lookup",
            "--no-ignore",
            "--no-inline-config",
            "src",
            "tests",
        ),
    ),
    (
        "typescript.prettier.v1",
        (
            "$TOOLS/node_modules/.bin/prettier",
            "--check",
            "--config",
            "$TOOLS/prettier.json",
            "--ignore-path",
            "$TOOLS/prettier.ignore",
            "src",
            "tests",
        ),
    ),
    (
        "typescript.c901-equivalent-touched.v1",
        (
            "$TOOLS/node_modules/.bin/eslint",
            "--config",
            "$TOOLS/eslint.config.mjs",
            "--no-config-lookup",
            "--no-ignore",
            "--no-inline-config",
            "--rule",
            "complexity: [error, 10]",
            "src",
            "tests",
        ),
    ),
    (
        "typescript.typecheck.v1",
        ("$TOOLS/node_modules/.bin/tsc", "--project", "$OUTPUT/tsconfig-check.json"),
    ),
    ("typescript.test.v1", ("$NODE", "--test", "$TEST_FILES")),
    (
        "typescript.build.v1",
        ("$TOOLS/node_modules/.bin/tsc", "--project", "$OUTPUT/tsconfig-build.json"),
    ),
    (
        "typescript.import-boundaries.v1",
        (
            "$TOOLS/node_modules/.bin/depcruise",
            "--config",
            "$TOOLS/dependency-cruiser.json",
            "--ts-config",
            "$OUTPUT/tsconfig-check.json",
            "--",
            "src",
        ),
    ),
)


class QualityProfileError(ValueError):
    """Fail-closed quality-profile evidence error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GateResult:
    """One fixed command result."""

    adapter: str
    arguments: tuple[str, ...]
    covered_paths: tuple[str, ...]
    executed: bool
    exit_code: int
    stderr_sha256: str
    stdout_sha256: str


@dataclass(frozen=True)
class QualityEvidence:
    """Exact immutable quality-profile attestation."""

    base_sha: str
    changed_paths: tuple[str, ...]
    commands: tuple[GateResult, ...]
    exclusions: tuple[str, ...]
    head_sha: str
    high_risk_paths: tuple[str, ...]
    language: str
    maximum_complexity: int
    production_files: tuple[str, ...]
    production_paths: tuple[str, ...]
    repository: str
    repository_id: str
    repository_remote: str
    run_attempt: str
    run_id: str
    runner_environment: str
    schema_version: str
    untested_areas: tuple[str, ...]
    workflow_sha: str
    job: str
    artifact_id: str
    artifact_digest: str
    capture_sha256: str


@dataclass(frozen=True)
class _CommandPlan:
    adapter: str
    actual: tuple[str, ...]
    evidence: tuple[str, ...]
    covered_paths: tuple[str, ...]


def command_templates(language: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return the immutable approved command templates for one stack."""
    if language == "python":
        return _PYTHON_COMMANDS
    if language == "typescript":
        return _TYPESCRIPT_COMMANDS
    raise QualityProfileError("UNSUPPORTED_LANGUAGE", f"unsupported language: {language}")


def required_adapters(language: str) -> tuple[str, ...]:
    """Return required gate identities, including fixed TypeScript tool installation."""
    return tuple(adapter for adapter, _ in command_templates(language))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", f"{field} must be a string list")
    return tuple(value)


def _gate_result(value: object) -> GateResult:
    if not isinstance(value, dict) or set(value) != {
        "adapter",
        "arguments",
        "covered_paths",
        "executed",
        "exit_code",
        "stderr_sha256",
        "stdout_sha256",
    }:
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid command result")
    if (
        not isinstance(value["adapter"], str)
        or type(value["executed"]) is not bool
        or type(value["exit_code"]) is not int
        or not isinstance(value["stdout_sha256"], str)
        or not isinstance(value["stderr_sha256"], str)
    ):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid command result types")
    return GateResult(
        value["adapter"],
        _string_tuple(value["arguments"], "arguments"),
        _string_tuple(value["covered_paths"], "covered_paths"),
        value["executed"],
        value["exit_code"],
        value["stderr_sha256"],
        value["stdout_sha256"],
    )


def _parse_evidence(data: object) -> QualityEvidence:
    expected = {
        "base_sha",
        "changed_paths",
        "commands",
        "exclusions",
        "head_sha",
        "high_risk_paths",
        "language",
        "maximum_complexity",
        "production_files",
        "production_paths",
        "repository",
        "repository_id",
        "repository_remote",
        "run_attempt",
        "run_id",
        "runner_environment",
        "schema_version",
        "untested_areas",
        "workflow_sha",
        "job",
        "artifact_id",
        "artifact_digest",
        "capture_sha256",
    }
    if not isinstance(data, dict) or set(data) != expected:
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "quality evidence keys mismatch")
    scalar_strings = (
        "base_sha",
        "head_sha",
        "language",
        "repository",
        "repository_id",
        "repository_remote",
        "run_attempt",
        "run_id",
        "runner_environment",
        "schema_version",
        "workflow_sha",
        "job",
        "artifact_id",
        "artifact_digest",
        "capture_sha256",
    )
    if any(not isinstance(data[field], str) for field in scalar_strings):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid quality identity")
    if type(data["maximum_complexity"]) is not int or not isinstance(data["commands"], list):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid quality values")
    return QualityEvidence(
        data["base_sha"],
        _string_tuple(data["changed_paths"], "changed_paths"),
        tuple(_gate_result(item) for item in data["commands"]),
        _string_tuple(data["exclusions"], "exclusions"),
        data["head_sha"],
        _string_tuple(data["high_risk_paths"], "high_risk_paths"),
        data["language"],
        data["maximum_complexity"],
        _string_tuple(data["production_files"], "production_files"),
        _string_tuple(data["production_paths"], "production_paths"),
        data["repository"],
        data["repository_id"],
        data["repository_remote"],
        data["run_attempt"],
        data["run_id"],
        data["runner_environment"],
        data["schema_version"],
        _string_tuple(data["untested_areas"], "untested_areas"),
        data["workflow_sha"],
        data["job"],
        data["artifact_id"],
        data["artifact_digest"],
        data["capture_sha256"],
    )


def load_evidence(path: Path) -> QualityEvidence:
    """Load strict quality evidence without accepting unknown fields."""
    try:
        data = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise QualityProfileError("MISSING_QUALITY_EVIDENCE", str(error)) from error
    return _parse_evidence(data)


def authenticate_evidence(
    path: Path,
    *,
    repository: str,
    repository_id: str,
    run_id: str,
    run_attempt: str,
    job: str,
    artifact_id: str,
    artifact_digest: str,
    capture_sha256: str,
) -> QualityEvidence:
    """Bind one downloaded GitHub Actions artifact to trusted workflow metadata."""
    try:
        content = path.read_bytes()
        evidence = _parse_evidence(json.loads(content))
    except (OSError, json.JSONDecodeError) as error:
        raise QualityProfileError("MISSING_QUALITY_EVIDENCE", str(error)) from error
    expected = (repository, repository_id, run_id, run_attempt, job, "github-hosted")
    actual = (
        evidence.repository,
        evidence.repository_id,
        evidence.run_id,
        evidence.run_attempt,
        evidence.job,
        evidence.runner_environment,
    )
    if actual != expected:
        raise QualityProfileError("QUALITY_ARTIFACT_BINDING_MISMATCH", "artifact identity mismatch")
    if any((evidence.artifact_id, evidence.artifact_digest, evidence.capture_sha256)):
        raise QualityProfileError(
            "SELF_DECLARED_QUALITY_ARTIFACT", "artifact metadata must be external"
        )
    if (
        not artifact_id.isdigit()
        or re.fullmatch(r"[0-9a-f]{64}", artifact_digest) is None
        or _FULL_SHA.fullmatch(capture_sha256) is None
        or len(capture_sha256) != 64
        or _sha256(content) != capture_sha256
    ):
        raise QualityProfileError("UNAUTHENTICATED_QUALITY_ARTIFACT", "artifact proof mismatch")
    return replace(
        evidence,
        artifact_id=artifact_id,
        artifact_digest=artifact_digest,
        capture_sha256=capture_sha256,
    )


def _changed_production_paths(
    assessments: tuple[ChangedFileAssessment, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                path
                for item in assessments
                for path, production in (
                    (item.change.old_path, item.base_production),
                    (item.change.new_path, item.head_production),
                )
                if path and production
            }
        )
    )


def _moved_outside_scope(
    assessments: tuple[ChangedFileAssessment, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.change.new_path
            for item in assessments
            if item.base_production
            and not item.head_production
            and item.change.new_path is not None
        )
    )


def _changed_head_production_paths(
    assessments: tuple[ChangedFileAssessment, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.change.new_path
            for item in assessments
            if item.head_production and item.change.new_path is not None
        )
    )


def _verify_evidence_identity(
    evidence: QualityEvidence,
    policy: contract.Contract,
    identity: git_changes.RepositoryIdentity,
    workflow_sha: str,
) -> None:
    if _FULL_SHA.fullmatch(workflow_sha) is None:
        raise QualityProfileError("INVALID_WORKFLOW_SHA", "workflow SHA must be immutable")
    expected_identity = (
        identity.remote,
        identity.base_sha,
        identity.head_sha,
        policy.language,
        workflow_sha,
    )
    actual_identity = (
        evidence.repository_remote,
        evidence.base_sha,
        evidence.head_sha,
        evidence.language,
        evidence.workflow_sha,
    )
    if actual_identity != expected_identity:
        raise QualityProfileError("QUALITY_EVIDENCE_BINDING_MISMATCH", "quality identity mismatch")
    if evidence.schema_version != SCHEMA_VERSION or evidence.runner_environment != "github-hosted":
        raise QualityProfileError("UNTRUSTED_QUALITY_ENVIRONMENT", "untrusted quality runtime")
    if (
        not evidence.artifact_id.isdigit()
        or re.fullmatch(r"[0-9a-f]{64}", evidence.artifact_digest) is None
        or len(evidence.capture_sha256) != 64
        or _FULL_SHA.fullmatch(evidence.capture_sha256) is None
    ):
        raise QualityProfileError("UNAUTHENTICATED_QUALITY_ARTIFACT", "artifact proof missing")


def _covers(result: GateResult, path: str) -> bool:
    return any(path == root or path.startswith(f"{root}/") for root in result.covered_paths)


def _command_blocks(
    evidence: QualityEvidence,
    policy: contract.Contract,
    changed_paths: tuple[str, ...],
) -> list[str]:
    expected = dict(command_templates(policy.language))
    commands = {item.adapter: item for item in evidence.commands}
    if len(commands) != len(evidence.commands):
        raise QualityProfileError("DUPLICATE_QUALITY_COMMAND", "duplicate quality command")
    blocks = [
        f"MISSING_QUALITY_COMMAND:{adapter}" for adapter in expected if adapter not in commands
    ]
    blocks.extend(
        f"UNAPPROVED_QUALITY_COMMAND:{adapter}" for adapter in sorted(set(commands) - set(expected))
    )
    for adapter, arguments in expected.items():
        result = commands.get(adapter)
        if result is None:
            continue
        if result.arguments != arguments:
            blocks.append(f"QUALITY_COMMAND_VECTOR_MISMATCH:{adapter}")
        if not result.executed:
            blocks.append(f"DECLARED_TOOL_NOT_EXECUTED:{adapter}")
        elif result.exit_code:
            blocks.append(f"QUALITY_GATE_FAILED:{adapter}")
        blocks.extend(
            f"QUALITY_CHANGED_FILE_COVERAGE:{adapter}:{path}"
            for path in changed_paths
            if not _covers(result, path)
        )
        blocks.extend(
            f"QUALITY_HIGH_RISK_FILE_COVERAGE:{adapter}:{path}"
            for path in policy.high_risk_paths
            if not _covers(result, path)
        )
    return blocks


def _policy_blocks(
    evidence: QualityEvidence,
    policy: contract.Contract,
    assessments: tuple[ChangedFileAssessment, ...],
) -> list[str]:
    blocks: list[str] = []
    changed_paths = _changed_production_paths(assessments)
    if evidence.production_paths != policy.production_paths:
        blocks.append("QUALITY_SCOPE_NARROWING")
    if evidence.maximum_complexity > policy.maximum:
        blocks.append("QUALITY_THRESHOLD_WEAKENING")
    elif evidence.maximum_complexity != policy.maximum:
        blocks.append("QUALITY_THRESHOLD_MISMATCH")
    blocks.extend(f"QUALITY_EXCLUSION_ADDED:{path}" for path in evidence.exclusions)
    blocks.extend(f"UNTESTED_AREA:{path}" for path in evidence.untested_areas)
    blocks.extend(
        f"PRODUCTION_PATH_MOVED_OUTSIDE_SCOPE:{path}" for path in _moved_outside_scope(assessments)
    )
    head_paths = _changed_head_production_paths(assessments)
    blocks.extend(
        f"QUALITY_CHANGED_FILE_NOT_ATTESTED:{path}"
        for path in head_paths
        if path.endswith(_SOURCE_SUFFIXES[policy.language])
        and path not in evidence.production_files
    )
    blocks.extend(
        f"QUALITY_HIGH_RISK_FILE_NOT_ATTESTED:{path}"
        for path in policy.high_risk_paths
        if path.endswith(_SOURCE_SUFFIXES[policy.language])
        and path not in evidence.production_files
    )
    if (
        evidence.changed_paths != changed_paths
        or evidence.high_risk_paths != policy.high_risk_paths
    ):
        blocks.append("QUALITY_COVERAGE_MAPPING_MISMATCH")
    return blocks


def evidence_blocks(
    evidence: QualityEvidence,
    policy: contract.Contract,
    identity: git_changes.RepositoryIdentity,
    assessments: tuple[ChangedFileAssessment, ...],
    workflow_sha: str,
) -> tuple[str, ...]:
    """Bind exact attestation identity and return deterministic quality blocks."""
    _verify_evidence_identity(evidence, policy, identity, workflow_sha)
    changed_paths = _changed_production_paths(assessments)
    blocks = _command_blocks(evidence, policy, changed_paths)
    blocks.extend(_policy_blocks(evidence, policy, assessments))
    return tuple(sorted(set(blocks)))


def _fixed_environment(output: Path, repository: Path) -> dict[str, str]:
    keys = ("HOME", "PATH", "SystemRoot", "WINDIR")
    environment = {key: os.environ[key] for key in keys if key in os.environ}
    environment.update(
        {
            "CI": "true",
            "NO_COLOR": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(repository / "src"),
            "PYTHONPYCACHEPREFIX": str(output / "pycache"),
        }
    )
    return environment


def _replace_tokens(arguments: tuple[str, ...], values: dict[str, str]) -> tuple[str, ...]:
    replaced: list[str] = []
    for argument in arguments:
        if argument == "$TEST_FILES":
            replaced.extend(values[argument].split("\0") if values[argument] else ())
        else:
            value = argument
            for token, replacement in values.items():
                value = value.replace(token, replacement)
            replaced.append(value)
    return tuple(replaced)


def _write_typescript_configs(tools: Path, output: Path, source_files: tuple[str, ...]) -> None:
    tools.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    parser_url = (
        tools / "node_modules" / "@typescript-eslint" / "parser" / "dist" / "index.js"
    ).as_uri()
    (tools / "eslint.config.mjs").write_text(
        "import parser from " + json.dumps(parser_url) + ";\n"
        "export default [{ files: ['**/*.{js,jsx,ts,tsx,mjs,cjs,mts,cts}'], "
        "languageOptions: { parser, parserOptions: { ecmaVersion: 'latest', sourceType: 'module' } }, "
        "rules: { 'no-constant-condition': 'error', 'no-debugger': 'error', "
        "'no-duplicate-case': 'error', 'no-unreachable': 'error' } }];\n",
        encoding="utf-8",
        newline="\n",
    )
    (tools / "prettier.json").write_text("{}\n", encoding="utf-8", newline="\n")
    (tools / "prettier.ignore").write_text("\n", encoding="utf-8", newline="\n")
    cruiser = {
        "forbidden": [
            {"name": "no-circular", "severity": "error", "from": {}, "to": {"circular": True}},
            {
                "name": "domain-stays-inward",
                "severity": "error",
                "from": {"path": "^src/domain"},
                "to": {"path": "^src/(application|infrastructure|presentation)"},
            },
            {
                "name": "application-stays-inward",
                "severity": "error",
                "from": {"path": "^src/application"},
                "to": {"path": "^src/(infrastructure|presentation)"},
            },
        ],
        "options": {"doNotFollow": {"path": "node_modules"}},
    }
    (tools / "dependency-cruiser.json").write_text(
        json.dumps(cruiser, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    check = {
        "compilerOptions": {
            "allowImportingTsExtensions": True,
            "module": "NodeNext",
            "moduleResolution": "NodeNext",
            "noEmit": True,
            "skipLibCheck": True,
            "strict": True,
            "target": "ES2022",
        },
        "files": list(source_files),
    }
    build = json.loads(json.dumps(check))
    build["compilerOptions"].update(
        {
            "allowImportingTsExtensions": False,
            "declaration": True,
            "noEmit": False,
            "outDir": str(output / "build"),
        }
    )
    (output / "tsconfig-check.json").write_text(
        json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (output / "tsconfig-build.json").write_text(
        json.dumps(build, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _write_python_configs(output: Path, source_files: tuple[str, ...]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "mypy.ini").write_text(
        "[mypy]\npython_version = 3.12\nstrict = True\nmypy_path = src\n",
        encoding="utf-8",
        newline="\n",
    )
    (output / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\npythonpath = src\naddopts = -p no:cacheprovider\n",
        encoding="utf-8",
        newline="\n",
    )
    roots = tuple(
        sorted(
            {
                Path(path).parts[1]
                for path in source_files
                if len(Path(path).parts) > 2 and Path(path).name == "__init__.py"
            }
        )
    )
    packages = roots or ("missing_quality_root",)
    indented = "\n".join(f"    {item}" for item in packages)
    (output / "importlinter.ini").write_text(
        "[importlinter]\nroot_packages =\n"
        + indented
        + "\n\n[importlinter:contract:quality-acyclic]\n"
        + "name = Fixed quality profile has no sibling cycles\n"
        + "type = acyclic_siblings\nancestors =\n"
        + indented
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _command_plans(
    language: str,
    repository: Path,
    output: Path,
    test_files: tuple[str, ...],
    source_files: tuple[str, ...],
) -> tuple[_CommandPlan, ...]:
    tools = output / "quality-tools"
    if language == "typescript":
        _write_typescript_configs(
            tools,
            output,
            tuple(str((repository / path).resolve()) for path in source_files),
        )
    else:
        _write_python_configs(output, source_files)
    lint_imports = shutil.which("lint-imports") or str(
        Path(sys.executable).with_name("lint-imports")
    )
    values = {
        "$LINT_IMPORTS": lint_imports,
        "$NODE": shutil.which("node") or "node",
        "$NPM": shutil.which("npm") or "npm",
        "$OUTPUT": str(output),
        "$PYTHON": sys.executable,
        "$TEST_FILES": "\0".join(str((repository / path).resolve()) for path in test_files),
        "$TOOLS": str(tools),
    }
    return tuple(
        _CommandPlan(adapter, _replace_tokens(arguments, values), arguments, ("src",))
        for adapter, arguments in command_templates(language)
    )


def _profile_files(
    repository: Path,
    head_sha: str,
    policy: contract.Contract,
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    production = git_changes.list_regular_blobs(
        repository, head_sha, policy.production_paths, records
    )
    tests = git_changes.list_regular_blobs(repository, head_sha, ("tests",), records)
    suffixes = _SOURCE_SUFFIXES[policy.language]
    source_files = tuple(item.path for item in production if item.path.endswith(suffixes))
    test_files = tuple(
        item.path
        for item in tests
        if item.path.endswith(
            (".test.js", ".test.mjs", ".test.cjs", ".test.ts", ".test.mts", ".test.cts")
        )
    )
    return source_files, test_files


def write_evidence(evidence: QualityEvidence, output: Path) -> bytes:
    """Write byte-stable attestation JSON."""
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(asdict(evidence), indent=2, sort_keys=True) + "\n").encode()
    output.write_bytes(payload)
    return payload
