"""Parse and validate the immutable base-ref repository contract."""

from __future__ import annotations

import hashlib
import posixpath
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

COMPLEXITY_ADAPTERS = {
    "python": "python.c901-touched.v1",
    "typescript": "typescript.c901-equivalent-touched.v1",
}
POLICY_EXIT_STANDARDS = {
    "python": {COMPLEXITY_ADAPTERS["python"]: 1, "python.import-linter.v1": 3},
    "typescript": {
        COMPLEXITY_ADAPTERS["typescript"]: 1,
        "typescript.import-boundaries.v1": 3,
    },
}
SUPPORTED_LANGUAGES = ("python", "typescript")
FIXED_ADAPTERS_BY_LANGUAGE = {
    "python": (
        "python.c901-touched.v1",
        "python.import-linter.v1",
        "python.mypy-strict.v1",
        "python.pytest.v1",
        "python.ruff-lint.v1",
    ),
    "typescript": (
        "typescript.c901-equivalent-touched.v1",
        "typescript.import-boundaries.v1",
    ),
}
FIXED_ADAPTERS_BY_LANGUAGE["mixed"] = (
    *FIXED_ADAPTERS_BY_LANGUAGE["python"],
    *FIXED_ADAPTERS_BY_LANGUAGE["typescript"],
)


def command_failed(language: str, adapter: str, executed: bool, exit_code: int) -> bool:
    """Return whether a command failed outside ordinary Gate 1 or 3 policy evidence."""
    profile = adapter.split(".", 1)[0] if language == "mixed" else language
    return not executed or (
        exit_code != 0 and (adapter not in POLICY_EXIT_STANDARDS[profile] or exit_code != 1)
    )


class ContractError(ValueError):
    """A fail-closed base contract error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GateAdapter:
    """One approved external gate identity and declared repository scope."""

    adapter: str
    paths: tuple[str, ...]

    def covers(self, path: str) -> bool:
        """Return whether the declared scope covers a repository path."""
        return any(path == root or path.startswith(f"{root}/") for root in self.paths)


@dataclass(frozen=True)
class Contract:
    """Validated base-ref policy contract."""

    schema_version: str
    language: str
    languages: tuple[str, ...]
    production_paths: tuple[str, ...]
    high_risk_paths: tuple[str, ...]
    adapter: str
    maximum: int
    gates: tuple[GateAdapter, ...]
    sha256: str

    def is_production_path(self, path: str) -> bool:
        """Return whether a repository path is inside configured production scope."""
        return any(path == root or path.startswith(f"{root}/") for root in self.production_paths)


def is_profile_expansion(base: Contract, head: Contract | None) -> bool:
    """Return whether a contract safely adds the fixed mixed profile or covered paths."""
    if (
        head is None
        or base.schema_version not in {"1.0", "1.1"}
        or head.schema_version != "1.1"
        or not set(base.languages).issubset(head.languages)
        or not set(base.production_paths).issubset(head.production_paths)
        or not set(base.high_risk_paths).issubset(head.high_risk_paths)
        or base.maximum != head.maximum
    ):
        return False
    base_gates = {gate.adapter: gate for gate in base.gates}
    head_gates = {gate.adapter: gate for gate in head.gates}
    if any(
        (head_gate := head_gates.get(adapter)) is None
        or any(not head_gate.covers(path) for path in gate.paths)
        for adapter, gate in base_gates.items()
    ):
        return False
    return set(head_gates) == set(FIXED_ADAPTERS_BY_LANGUAGE["mixed"]) and all(
        gate.paths == head.production_paths for gate in head_gates.values()
    )


def is_profile_retirement(
    base: Contract,
    head: Contract | None,
    deleted_paths: set[str],
) -> bool:
    """Allow one fixed mixed profile to retire without shrinking production scope."""
    if (
        head is None
        or base.schema_version != "1.1"
        or head.schema_version != "1.0"
        or head.languages[0] not in base.languages
        or head.production_paths != base.production_paths
        or head.maximum != base.maximum
    ):
        return False
    expected_gates = tuple(
        GateAdapter(adapter, head.production_paths)
        for adapter in FIXED_ADAPTERS_BY_LANGUAGE[head.language]
    )
    remaining_high_risk = tuple(path for path in base.high_risk_paths if path not in deleted_paths)
    return head.gates == expected_gates and head.high_risk_paths == remaining_high_risk


def normalize_repository_path(value: object, field: str) -> str:
    """Validate one normalized repository-relative POSIX path."""
    if not isinstance(value, str) or not value:
        raise ContractError("INVALID_PATH", f"{field} must contain non-empty strings")
    if "\\" in value or value != value.strip():
        raise ContractError("INVALID_PATH", f"{field} path is not normalized: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise ContractError("ABSOLUTE_PATH", f"{field} path must be repository-relative: {value!r}")
    if value == "." or ".." in path.parts or posixpath.normpath(value) != value:
        raise ContractError("INVALID_PATH", f"{field} path is not normalized: {value!r}")
    return value


def validate_contract_path(value: str) -> str:
    """Validate the fixed contract path CLI argument."""
    return normalize_repository_path(value, "contract_path")


def content_sha256(content: bytes) -> str:
    """Return the contract's exact byte hash."""
    return hashlib.sha256(content).hexdigest()


def _require_keys(data: dict[str, Any], expected: set[str], location: str) -> None:
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ContractError(
            "INVALID_SCHEMA_KEYS",
            f"{location} keys mismatch; missing={missing}, unknown={unknown}",
        )


def _path_list(value: object, field: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ContractError("INVALID_PATH_LIST", f"{field} must be a list")
    paths = tuple(normalize_repository_path(item, field) for item in value)
    if len(paths) != len(set(paths)):
        raise ContractError("DUPLICATE_PATH", f"{field} contains duplicate paths")
    return paths


def _gate_adapters(value: object) -> tuple[GateAdapter, ...]:
    if not isinstance(value, list) or not value:
        raise ContractError("INVALID_GATES", "gates must be a non-empty array of tables")
    gates: list[GateAdapter] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ContractError("INVALID_GATE", f"gates[{index}] must be a table")
        _require_keys(item, {"adapter", "paths"}, f"gates[{index}]")
        adapter = item["adapter"]
        if not isinstance(adapter, str) or not adapter:
            raise ContractError("INVALID_ADAPTER", f"gates[{index}].adapter must be non-empty")
        gates.append(
            GateAdapter(
                adapter,
                _path_list(item["paths"], f"gates[{index}].paths", allow_empty=False),
            )
        )
    if len(gates) != len({item.adapter for item in gates}):
        raise ContractError("DUPLICATE_ADAPTER", "gates contains duplicate adapters")
    return tuple(sorted(gates, key=lambda item: item.adapter))


def parse_contract(content: bytes) -> Contract:
    """Parse the only supported contract schema."""
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ContractError("MALFORMED_CONTRACT", str(error)) from error
    version = data.get("schema_version")
    language_key = "language" if version == "1.0" else "languages"
    _require_keys(
        data,
        {
            "schema_version",
            language_key,
            "production_paths",
            "high_risk_paths",
            "complexity",
            "gates",
        },
        "contract",
    )
    complexity = data["complexity"]
    if not isinstance(complexity, dict):
        raise ContractError("INVALID_COMPLEXITY", "complexity must be a table")
    language: str
    languages: tuple[str, ...]
    if version == "1.0":
        _require_keys(complexity, {"adapter", "maximum"}, "complexity")
        language = data["language"]
        if language not in COMPLEXITY_ADAPTERS:
            raise ContractError(
                "UNSUPPORTED_LANGUAGE", "language must equal 'python' or 'typescript'"
            )
        languages = (language,)
        expected_adapter = COMPLEXITY_ADAPTERS[language]
        if complexity["adapter"] != expected_adapter:
            raise ContractError("UNSUPPORTED_ADAPTER", f"adapter must equal {expected_adapter!r}")
    elif version == "1.1":
        _require_keys(complexity, {"maximum"}, "complexity")
        raw_languages = data["languages"]
        if raw_languages != list(SUPPORTED_LANGUAGES):
            raise ContractError(
                "UNSUPPORTED_LANGUAGES",
                "languages must equal ['python', 'typescript']",
            )
        languages = SUPPORTED_LANGUAGES
        language = "mixed"
        expected_adapter = "mixed"
    else:
        raise ContractError("UNSUPPORTED_SCHEMA", "schema_version must equal '1.0' or '1.1'")
    if type(complexity["maximum"]) is not int or complexity["maximum"] < 1:
        raise ContractError("INVALID_MAXIMUM", "complexity.maximum must be a positive integer")
    return Contract(
        schema_version=version,
        language=language,
        languages=languages,
        production_paths=_path_list(
            data["production_paths"], "production_paths", allow_empty=False
        ),
        high_risk_paths=_path_list(data["high_risk_paths"], "high_risk_paths", allow_empty=True),
        adapter=expected_adapter,
        maximum=complexity["maximum"],
        gates=_gate_adapters(data["gates"]),
        sha256=content_sha256(content),
    )
