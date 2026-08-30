"""Define and verify fixed GitHub-hosted stack-native quality profiles."""

from __future__ import annotations

import hashlib
import json
import re
import zlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from supportability_gate import contract, git_changes

if TYPE_CHECKING:
    from supportability_gate.function_changes import ChangedFileAssessment

SCHEMA_VERSION = "quality-gates.v6"
TIMEOUT_SECONDS = 180
_FULL_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SOURCE_SUFFIXES = {
    "python": (".py", ".pyi"),
    "typescript": (".cts", ".js", ".jsx", ".mts", ".ts", ".tsx"),
    "mixed": (".cts", ".js", ".jsx", ".mts", ".py", ".pyi", ".ts", ".tsx"),
}
TEST_SUFFIXES = {
    "python": (".py", ".pyi"),
    "typescript": (".test.js", ".test.mjs", ".test.cjs", ".test.ts", ".test.mts", ".test.cts"),
    "mixed": (
        ".py",
        ".pyi",
        ".test.js",
        ".test.mjs",
        ".test.cjs",
        ".test.ts",
        ".test.mts",
        ".test.cts",
    ),
}
ASSET_VALIDATORS = {
    ".json": ("json", "json.stdlib.v1"),
    ".md": ("markdown", "markdown.utf8.v1"),
    ".png": ("png", "png.crc.v1"),
}
UNSUPPORTED_ASSET_IDENTITY = ("unsupported", "unsupported.v1")
ASSET_RESULTS = frozenset({"PASS", "MALFORMED", "UNSUPPORTED"})
_PNG_BIT_DEPTHS = {
    0: (1, 2, 4, 8, 16),
    2: (8, 16),
    3: (1, 2, 4, 8),
    4: (8, 16),
    6: (8, 16),
}
_PYTHON_COMMANDS = (
    (
        "python.ruff-lint.v1",
        (
            "$PYTHON",
            "-I",
            "-m",
            "ruff",
            "check",
            "$SOURCE_FILES",
            "$TEST_FILES",
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
            "-I",
            "-m",
            "ruff",
            "format",
            "--check",
            "$SOURCE_FILES",
            "$TEST_FILES",
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
            "-I",
            "-m",
            "ruff",
            "check",
            "$SOURCE_FILES",
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
            "-I",
            "-m",
            "mypy",
            "--config-file",
            "$OUTPUT/mypy.ini",
            "--cache-dir",
            "$OUTPUT/mypy-cache",
            "$SOURCE_FILES",
        ),
    ),
    (
        "python.pytest.v1",
        (
            "$PYTHON",
            "-I",
            "-m",
            "coverage",
            "run",
            "--rcfile=$OUTPUT/coverage.ini",
            "--branch",
            "--source=src",
            "--data-file=$OUTPUT/.coverage",
            "-m",
            "pytest",
            "-q",
            "-c",
            "$OUTPUT/pytest.ini",
            "--rootdir",
            "$REPOSITORY",
        ),
    ),
    (
        "python.build-wheel.v1",
        (
            "$PYTHON",
            "-I",
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
        "typescript.target-install.v1",
        ("$NPM", "ci", "--ignore-scripts", "--no-audit", "--no-fund"),
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
            "$SOURCE_FILES",
            "$TEST_FILES",
        ),
    ),
    (
        "typescript.prettier.v1",
        (
            "$TOOLS/node_modules/.bin/prettier",
            "--check",
            "--config",
            "$TOOLS/prettier.json",
            "--no-editorconfig",
            "--ignore-path",
            "$TOOLS/prettier.ignore",
            "$SOURCE_FILES",
            "$TEST_FILES",
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
            "$SOURCE_FILES",
            "$TEST_FILES",
        ),
    ),
    (
        "typescript.typecheck.v1",
        ("$TOOLS/node_modules/.bin/tsc", "--project", "$OUTPUT/tsconfig-check.json"),
    ),
    (
        "typescript.test.v1",
        (
            "$NODE",
            "--test",
            "--experimental-test-coverage",
            "--test-coverage-include=src/**",
            "--test-reporter=spec",
            "--test-reporter=lcov",
            "--test-reporter-destination=stdout",
            "--test-reporter-destination=$OUTPUT/coverage.lcov",
            "$TEST_FILES",
        ),
    ),
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

_PROOF_KINDS = {
    "python.ruff-lint.v1": "explicit-source",
    "python.ruff-format.v1": "explicit-source",
    "python.c901-touched.v1": "explicit-source",
    "python.mypy-strict.v1": "config-source",
    "python.pytest.v1": "runtime-lines",
    "python.build-wheel.v1": "artifact-members",
    "python.import-linter.v1": "parsed-source",
    "typescript.tool-install.v1": "provisioning",
    "typescript.target-install.v1": "provisioning",
    "typescript.eslint.v1": "explicit-source",
    "typescript.prettier.v1": "explicit-source",
    "typescript.c901-equivalent-touched.v1": "explicit-source",
    "typescript.typecheck.v1": "config-source",
    "typescript.test.v1": "runtime-lines",
    "typescript.build.v1": "compiler-output",
    "typescript.import-boundaries.v1": "parsed-source",
}


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
    proof_kind: str
    observed_paths: tuple[str, ...]
    zero_statement_paths: tuple[str, ...]
    executed: bool
    exit_code: int
    stderr_sha256: str
    stdout_sha256: str
    raw_proof_sha256: str
    executed_arguments: tuple[str, ...]


@dataclass(frozen=True)
class AssetReceipt:
    """One immutable production-asset validation result."""

    path: str
    kind: str
    validator: str
    blob_sha256: str
    result: str


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
    asset_receipts: tuple[AssetReceipt, ...]
    production_files: tuple[str, ...]
    source_files: tuple[str, ...]
    test_files: tuple[str, ...]
    production_paths: tuple[str, ...]
    repository: str
    repository_id: str
    repository_remote: str
    run_attempt: str
    run_id: str
    runner_environment: str
    schema_version: str
    workflow_sha: str
    job: str
    artifact_id: str
    artifact_digest: str
    capture_sha256: str


def command_templates(language: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return the immutable approved command templates for one stack."""
    if language == "python":
        return _PYTHON_COMMANDS
    if language == "typescript":
        return _TYPESCRIPT_COMMANDS
    if language == "mixed":
        return (*_PYTHON_COMMANDS, *_TYPESCRIPT_COMMANDS)
    raise QualityProfileError("UNSUPPORTED_LANGUAGE", f"unsupported language: {language}")


def required_adapters(language: str) -> tuple[str, ...]:
    """Return required gate identities, including fixed TypeScript tool installation."""
    return tuple(adapter for adapter, _ in command_templates(language))


def production_files(
    repository: Path,
    head_sha: str,
    policy: contract.Contract,
    records: list[git_changes.CommandRecord],
) -> tuple[str, ...]:
    """Recompute the complete production manifest from the immutable head tree."""
    return tuple(
        item.path
        for item in git_changes.list_regular_blobs(
            repository, head_sha, policy.production_paths, records
        )
    )


def source_files(production: tuple[str, ...], language: str) -> tuple[str, ...]:
    """Return the language-source subset eligible for fixed code commands."""
    return tuple(path for path in production if path.endswith(SOURCE_SUFFIXES[language]))


def _valid_json(content: bytes) -> bool:
    def reject_constant(value: str) -> None:
        raise ValueError(value)

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate object key")
        return value

    try:
        json.loads(
            content.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, ValueError):
        return False
    return True


def _valid_markdown(content: bytes) -> bool:
    try:
        return "\0" not in content.decode("utf-8")
    except UnicodeDecodeError:
        return False


def _valid_ihdr(data: bytes) -> bool:
    return (
        len(data) == 13
        and int.from_bytes(data[:4], "big") > 0
        and int.from_bytes(data[4:8], "big") > 0
        and data[8] in _PNG_BIT_DEPTHS.get(data[9], ())
        and data[10] == 0
        and data[11] == 0
        and data[12] in {0, 1}
    )


def _valid_png(content: bytes) -> bool:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    first = True
    while offset < len(content):
        if len(content) - offset < 12:
            return False
        size = int.from_bytes(content[offset : offset + 4], "big")
        end = offset + 12 + size
        if end > len(content):
            return False
        kind = content[offset + 4 : offset + 8]
        data = content[offset + 8 : offset + 8 + size]
        expected_crc = int.from_bytes(content[offset + 8 + size : end], "big")
        if zlib.crc32(kind + data) != expected_crc:
            return False
        if first and (kind != b"IHDR" or not _valid_ihdr(data)):
            return False
        first = False
        if kind == b"IEND":
            return size == 0 and end == len(content)
        offset = end
    return False


def _asset_result(validator: str, content: bytes) -> str:
    validators = {
        "json.stdlib.v1": _valid_json,
        "markdown.utf8.v1": _valid_markdown,
        "png.crc.v1": _valid_png,
    }
    return "PASS" if validators[validator](content) else "MALFORMED"


def asset_receipts(
    repository: Path,
    head_sha: str,
    production: tuple[str, ...],
    sources: tuple[str, ...],
    records: list[git_changes.CommandRecord],
) -> tuple[AssetReceipt, ...]:
    """Validate every non-source production blob at the immutable head."""
    receipts: list[AssetReceipt] = []
    for path in production:
        if path in sources:
            continue
        blob = git_changes.read_regular_blob(repository, head_sha, path, records)
        identity = ASSET_VALIDATORS.get(Path(path).suffix, UNSUPPORTED_ASSET_IDENTITY)
        kind, validator = identity
        result = (
            "UNSUPPORTED"
            if identity == UNSUPPORTED_ASSET_IDENTITY
            else _asset_result(validator, blob.content)
        )
        receipts.append(AssetReceipt(path, kind, validator, _sha256(blob.content), result))
    return tuple(receipts)


def test_files(
    repository: Path,
    head_sha: str,
    language: str,
    records: list[git_changes.CommandRecord],
) -> tuple[str, ...]:
    """Recompute the exact quality test manifest from the immutable head tree."""
    return tuple(
        item.path
        for item in git_changes.list_regular_blobs(repository, head_sha, ("tests",), records)
        if item.path.endswith(TEST_SUFFIXES[language])
    )


def expected_proof_kind(adapter: str) -> str:
    """Return the fixed observation type for one approved adapter."""
    try:
        return _PROOF_KINDS[adapter]
    except KeyError as error:
        raise QualityProfileError("UNAPPROVED_QUALITY_COMMAND", adapter) from error


def python_coverage_observation(
    report: object, source_files: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return files with executed statements and files with no statements."""
    if not isinstance(report, dict) or not isinstance(report.get("files"), dict):
        raise QualityProfileError("MALFORMED_QUALITY_PROOF", "coverage files missing")
    files = report["files"]
    observed: list[str] = []
    zero_statement: list[str] = []
    for path in source_files:
        item = files.get(path)
        if item is None:
            continue
        if not isinstance(item, dict) or not isinstance(item.get("summary"), dict):
            raise QualityProfileError("MALFORMED_QUALITY_PROOF", path)
        summary = item["summary"]
        statements = summary.get("num_statements")
        covered = summary.get("covered_lines")
        if type(statements) is not int or type(covered) is not int:
            raise QualityProfileError("MALFORMED_QUALITY_PROOF", path)
        if statements == 0:
            zero_statement.append(path)
        elif covered > 0:
            observed.append(path)
    return tuple(observed), tuple(zero_statement)


def _lcov_path(value: str, repository: Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(repository.resolve())
        except ValueError as error:
            raise QualityProfileError("MALFORMED_QUALITY_PROOF", value) from error
    return path.as_posix()


def typescript_lcov_observation(
    report: str, source_files: tuple[str, ...], repository: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return TypeScript files with executed lines and files with no executable lines."""
    records: dict[str, tuple[int, int]] = {}
    current: str | None = None
    found = hits = None
    for line in report.splitlines():
        if line.startswith("SF:"):
            current = _lcov_path(line[3:], repository)
            found = hits = None
        elif line.startswith("LF:"):
            found = int(line[3:])
        elif line.startswith("LH:"):
            hits = int(line[3:])
        elif line == "end_of_record":
            if current is None or found is None or hits is None or current in records:
                raise QualityProfileError("MALFORMED_QUALITY_PROOF", "invalid LCOV record")
            records[current] = (found, hits)
            current = None
    observed = tuple(path for path in source_files if records.get(path, (0, 0))[1] > 0)
    zero_statement = tuple(path for path in source_files if records.get(path) == (0, 0))
    return observed, zero_statement


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
        "proof_kind",
        "observed_paths",
        "zero_statement_paths",
        "executed",
        "exit_code",
        "executed_arguments",
        "stderr_sha256",
        "stdout_sha256",
        "raw_proof_sha256",
    }:
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid command result")
    if (
        not isinstance(value["adapter"], str)
        or not isinstance(value["proof_kind"], str)
        or type(value["executed"]) is not bool
        or type(value["exit_code"]) is not int
        or not isinstance(value["stdout_sha256"], str)
        or not isinstance(value["stderr_sha256"], str)
        or not isinstance(value["raw_proof_sha256"], str)
    ):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid command result types")
    executed_arguments = _string_tuple(value["executed_arguments"], "executed_arguments")
    if not executed_arguments:
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "executed argv missing")
    return GateResult(
        value["adapter"],
        _string_tuple(value["arguments"], "arguments"),
        value["proof_kind"],
        _string_tuple(value["observed_paths"], "observed_paths"),
        _string_tuple(value["zero_statement_paths"], "zero_statement_paths"),
        value["executed"],
        value["exit_code"],
        value["stderr_sha256"],
        value["stdout_sha256"],
        value["raw_proof_sha256"],
        executed_arguments,
    )


def _asset_receipt(value: object) -> AssetReceipt:
    keys = {"blob_sha256", "kind", "path", "result", "validator"}
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or any(not isinstance(value[field], str) for field in keys)
        or not value["path"]
        or re.fullmatch(r"[0-9a-f]{64}", value["blob_sha256"]) is None
        or value["result"] not in ASSET_RESULTS
    ):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid asset receipt")
    identity = ASSET_VALIDATORS.get(Path(value["path"]).suffix, UNSUPPORTED_ASSET_IDENTITY)
    if (value["kind"], value["validator"]) != identity or (
        value["result"] == "UNSUPPORTED"
    ) is not (identity == UNSUPPORTED_ASSET_IDENTITY):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid asset identity")
    return AssetReceipt(
        value["path"],
        value["kind"],
        value["validator"],
        value["blob_sha256"],
        value["result"],
    )


def _parse_evidence(data: object) -> QualityEvidence:
    expected = {
        "asset_receipts",
        "base_sha",
        "changed_paths",
        "commands",
        "exclusions",
        "head_sha",
        "high_risk_paths",
        "language",
        "maximum_complexity",
        "production_files",
        "source_files",
        "test_files",
        "production_paths",
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
    if (
        type(data["maximum_complexity"]) is not int
        or not isinstance(data["commands"], list)
        or not isinstance(data["asset_receipts"], list)
    ):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid quality values")
    receipts = tuple(_asset_receipt(item) for item in data["asset_receipts"])
    if tuple(sorted(receipts, key=lambda item: item.path)) != receipts or len(
        {item.path for item in receipts}
    ) != len(receipts):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "asset receipts not unique/sorted")
    production = _string_tuple(data["production_files"], "production_files")
    sources = _string_tuple(data["source_files"], "source_files")
    tests = _string_tuple(data["test_files"], "test_files")
    commands = tuple(_gate_result(item) for item in data["commands"])
    if (
        data["language"] not in SOURCE_SUFFIXES
        or production != tuple(sorted(set(production)))
        or sources != tuple(sorted(set(sources)))
        or any(not path.endswith(SOURCE_SUFFIXES[data["language"]]) for path in sources)
        or production != tuple(sorted((*sources, *(item.path for item in receipts))))
        or any(
            path not in sources
            for command in commands
            for path in (*command.observed_paths, *command.zero_statement_paths)
        )
    ):
        raise QualityProfileError("MALFORMED_QUALITY_EVIDENCE", "invalid production partition")
    return QualityEvidence(
        data["base_sha"],
        _string_tuple(data["changed_paths"], "changed_paths"),
        commands,
        _string_tuple(data["exclusions"], "exclusions"),
        data["head_sha"],
        _string_tuple(data["high_risk_paths"], "high_risk_paths"),
        data["language"],
        data["maximum_complexity"],
        receipts,
        production,
        sources,
        tests,
        _string_tuple(data["production_paths"], "production_paths"),
        data["repository"],
        data["repository_id"],
        data["repository_remote"],
        data["run_attempt"],
        data["run_id"],
        data["runner_environment"],
        data["schema_version"],
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


def _verify_artifact_metadata(
    path: Path,
    *,
    repository: str,
    repository_id: str,
    run_id: str,
    run_attempt: str,
    artifact_id: str,
    artifact_digest: str,
    head_sha: str,
) -> None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise QualityProfileError("UNAUTHENTICATED_QUALITY_ARTIFACT", str(error)) from error
    workflow = data.get("workflow_run") if isinstance(data, dict) else None
    digest = data.get("digest") if isinstance(data, dict) else None
    if isinstance(digest, str) and digest.startswith("sha256:"):
        digest = digest.removeprefix("sha256:")
    expected = (
        int(artifact_id),
        f"quality-profile-{run_id}-{run_attempt}",
        False,
        artifact_digest,
        f"/repos/{repository}/actions/artifacts/{artifact_id}",
        int(run_id),
        int(repository_id),
        head_sha,
    )
    actual = (
        data.get("id") if isinstance(data, dict) else None,
        data.get("name") if isinstance(data, dict) else None,
        data.get("expired") if isinstance(data, dict) else None,
        digest,
        str(data.get("url", "")).removeprefix("https://api.github.com"),
        workflow.get("id") if isinstance(workflow, dict) else None,
        workflow.get("repository_id") if isinstance(workflow, dict) else None,
        workflow.get("head_sha") if isinstance(workflow, dict) else None,
    )
    expires_at = data.get("expires_at") if isinstance(data, dict) else None
    if actual != expected or not isinstance(expires_at, str) or not expires_at:
        raise QualityProfileError(
            "QUALITY_ARTIFACT_BINDING_MISMATCH", "GitHub artifact metadata mismatch"
        )


def verify_evidence_binding(
    path: Path,
    *,
    metadata_path: Path,
    repository: str,
    repository_id: str,
    run_id: str,
    run_attempt: str,
    job: str,
    artifact_id: str,
    artifact_digest: str,
    capture_sha256: str,
) -> QualityEvidence:
    """Bind one downloaded artifact to expected API-read workflow metadata."""
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
    _verify_artifact_metadata(
        metadata_path,
        repository=repository,
        repository_id=repository_id,
        run_id=run_id,
        run_attempt=run_attempt,
        artifact_id=artifact_id,
        artifact_digest=artifact_digest,
        head_sha=evidence.head_sha,
    )
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
    return path in result.observed_paths or path in result.zero_statement_paths


def _command_exit_block(language: str, result: GateResult) -> str | None:
    profile = result.adapter.split(".", 1)[0] if language == "mixed" else language
    if result.exit_code == 1 and contract.POLICY_EXIT_STANDARDS[profile].get(result.adapter) == 3:
        return f"ARCHITECTURE_GATE_FAILED:{result.adapter}"
    if contract.command_failed(language, result.adapter, result.executed, result.exit_code):
        return f"QUALITY_GATE_FAILED:{result.adapter}"
    return None


def _command_coverage_blocks(
    adapter: str,
    result: GateResult,
    changed_paths: tuple[str, ...],
    high_risk_paths: tuple[str, ...],
) -> list[str]:
    if result.proof_kind == "provisioning":
        return []
    profile = adapter.split(".", 1)[0]
    applicable_changed = tuple(
        path for path in changed_paths if path.endswith(SOURCE_SUFFIXES[profile])
    )
    applicable_high_risk = tuple(
        path for path in high_risk_paths if path.endswith(SOURCE_SUFFIXES[profile])
    )
    return [
        *(
            f"QUALITY_CHANGED_FILE_COVERAGE:{adapter}:{path}"
            for path in applicable_changed
            if not _covers(result, path)
        ),
        *(
            f"QUALITY_HIGH_RISK_FILE_COVERAGE:{adapter}:{path}"
            for path in applicable_high_risk
            if not _covers(result, path)
        ),
    ]


def _test_coverage_blocks(
    policy: contract.Contract,
    commands: dict[str, GateResult],
    changed_paths: tuple[str, ...],
    high_risk_paths: tuple[str, ...],
) -> list[str]:
    blocks: list[str] = []
    for profile, test_adapter in (
        ("python", "python.pytest.v1"),
        ("typescript", "typescript.test.v1"),
    ):
        if profile not in policy.languages or (test_result := commands.get(test_adapter)) is None:
            continue
        blocks.extend(
            f"UNTESTED_AREA:{path}"
            for path in sorted(set((*changed_paths, *high_risk_paths)))
            if path.endswith(SOURCE_SUFFIXES[profile]) and not _covers(test_result, path)
        )
    return blocks


def _command_blocks(
    evidence: QualityEvidence,
    policy: contract.Contract,
    changed_paths: tuple[str, ...],
    high_risk_paths: tuple[str, ...],
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
        if result.proof_kind != expected_proof_kind(adapter):
            blocks.append(f"QUALITY_PROOF_KIND_MISMATCH:{adapter}")
        if not result.executed:
            blocks.append(f"DECLARED_TOOL_NOT_EXECUTED:{adapter}")
        elif exit_block := _command_exit_block(policy.language, result):
            blocks.append(exit_block)
        blocks.extend(_command_coverage_blocks(adapter, result, changed_paths, high_risk_paths))
    blocks.extend(_test_coverage_blocks(policy, commands, changed_paths, high_risk_paths))
    return blocks


def _policy_blocks(
    evidence: QualityEvidence,
    policy: contract.Contract,
    assessments: tuple[ChangedFileAssessment, ...],
    production_files: tuple[str, ...],
    source_files: tuple[str, ...],
    asset_receipts: tuple[AssetReceipt, ...],
    test_files: tuple[str, ...],
) -> list[str]:
    blocks: list[str] = []
    if evidence.production_files != production_files:
        blocks.append("QUALITY_PRODUCTION_MANIFEST_MISMATCH")
    if evidence.test_files != test_files:
        blocks.append("QUALITY_TEST_MANIFEST_MISMATCH")
    if evidence.source_files != source_files:
        blocks.append("QUALITY_SOURCE_MANIFEST_MISMATCH")
    if evidence.asset_receipts != asset_receipts:
        blocks.append("QUALITY_ASSET_RECEIPT_MISMATCH")
    if production_files != tuple(sorted((*source_files, *(item.path for item in asset_receipts)))):
        blocks.append("QUALITY_PRODUCTION_PARTITION_MISMATCH")
    blocks.extend(
        f"MALFORMED_PRODUCTION_ASSET:{item.path}"
        for item in asset_receipts
        if item.result == "MALFORMED"
    )
    blocks.extend(
        f"UNSUPPORTED_PRODUCTION_ASSET:{item.path}"
        for item in asset_receipts
        if item.result == "UNSUPPORTED"
    )
    changed_paths = _changed_production_paths(assessments)
    if evidence.production_paths != policy.production_paths:
        blocks.append("QUALITY_SCOPE_NARROWING")
    if evidence.maximum_complexity > policy.maximum:
        blocks.append("QUALITY_THRESHOLD_WEAKENING")
    elif evidence.maximum_complexity != policy.maximum:
        blocks.append("QUALITY_THRESHOLD_MISMATCH")
    blocks.extend(f"QUALITY_EXCLUSION_ADDED:{path}" for path in evidence.exclusions)
    blocks.extend(
        f"PRODUCTION_PATH_MOVED_OUTSIDE_SCOPE:{path}" for path in _moved_outside_scope(assessments)
    )
    head_paths = _changed_head_production_paths(assessments)
    blocks.extend(
        f"QUALITY_CHANGED_FILE_NOT_ATTESTED:{path}"
        for path in head_paths
        if path not in evidence.production_files
    )
    blocks.extend(
        f"QUALITY_HIGH_RISK_FILE_NOT_ATTESTED:{path}"
        for path in policy.high_risk_paths
        if path not in evidence.production_files
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
    production_files: tuple[str, ...],
    source_files: tuple[str, ...],
    asset_receipts: tuple[AssetReceipt, ...],
    test_files: tuple[str, ...],
    workflow_sha: str,
) -> tuple[str, ...]:
    """Bind exact attestation identity and return deterministic quality blocks."""
    _verify_evidence_identity(evidence, policy, identity, workflow_sha)
    changed_paths = tuple(
        path for path in _changed_head_production_paths(assessments) if path in source_files
    )
    high_risk_paths = tuple(path for path in policy.high_risk_paths if path in source_files)
    blocks = _command_blocks(evidence, policy, changed_paths, high_risk_paths)
    blocks.extend(
        _policy_blocks(
            evidence,
            policy,
            assessments,
            production_files,
            source_files,
            asset_receipts,
            test_files,
        )
    )
    return tuple(sorted(set(blocks)))


def write_evidence(evidence: QualityEvidence, output: Path) -> bytes:
    """Write byte-stable attestation JSON."""
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(asdict(evidence), indent=2, sort_keys=True) + "\n").encode()
    output.write_bytes(payload)
    return payload


def decision_payload(evidence: QualityEvidence) -> dict[str, object]:
    """Return only inputs and observations that define the deterministic decision."""
    commands = [
        {
            "adapter": item.adapter,
            "arguments": list(item.arguments),
            "executed": item.executed,
            "exit_code": item.exit_code,
            "observed_paths": list(item.observed_paths),
            "proof_kind": item.proof_kind,
            "zero_statement_paths": list(item.zero_statement_paths),
        }
        for item in evidence.commands
    ]
    return {
        "base_sha": evidence.base_sha,
        "changed_paths": list(evidence.changed_paths),
        "commands": commands,
        "exclusions": list(evidence.exclusions),
        "head_sha": evidence.head_sha,
        "high_risk_paths": list(evidence.high_risk_paths),
        "language": evidence.language,
        "maximum_complexity": evidence.maximum_complexity,
        "asset_receipts": [asdict(item) for item in evidence.asset_receipts],
        "production_files": list(evidence.production_files),
        "source_files": list(evidence.source_files),
        "test_files": list(evidence.test_files),
        "production_paths": list(evidence.production_paths),
        "repository_remote": evidence.repository_remote,
        "schema_version": evidence.schema_version,
        "workflow_sha": evidence.workflow_sha,
    }


def provenance_payload(evidence: QualityEvidence) -> dict[str, object]:
    """Return run-specific artifact and raw-proof identities."""
    return {
        "artifact_digest": evidence.artifact_digest,
        "artifact_id": evidence.artifact_id,
        "capture_sha256": evidence.capture_sha256,
        "commands": [
            {
                "adapter": item.adapter,
                "executed_arguments": list(item.executed_arguments),
                "raw_proof_sha256": item.raw_proof_sha256,
                "stderr_sha256": item.stderr_sha256,
                "stdout_sha256": item.stdout_sha256,
            }
            for item in evidence.commands
        ],
        "job": evidence.job,
        "repository": evidence.repository,
        "repository_id": evidence.repository_id,
        "run_attempt": evidence.run_attempt,
        "run_id": evidence.run_id,
        "runner_environment": evidence.runner_environment,
    }
