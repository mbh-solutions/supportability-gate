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
_POLICY_EXIT_ADAPTERS = {
    "python": frozenset((COMPLEXITY_ADAPTERS["python"], "python.import-linter.v1")),
    "typescript": frozenset((COMPLEXITY_ADAPTERS["typescript"], "typescript.import-boundaries.v1")),
}


def command_failed(language: str, adapter: str, executed: bool, exit_code: int) -> bool:
    """Return whether a command failed outside ordinary Gate 1 or 3 policy evidence."""
    return not executed or (
        exit_code != 0 and (adapter not in _POLICY_EXIT_ADAPTERS[language] or exit_code != 1)
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
    production_paths: tuple[str, ...]
    high_risk_paths: tuple[str, ...]
    adapter: str
    maximum: int
    gates: tuple[GateAdapter, ...]
    sha256: str

    def is_production_path(self, path: str) -> bool:
        """Return whether a repository path is inside configured production scope."""
        return any(path == root or path.startswith(f"{root}/") for root in self.production_paths)


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
    _require_keys(
        data,
        {
            "schema_version",
            "language",
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
    _require_keys(complexity, {"adapter", "maximum"}, "complexity")
    if data["schema_version"] != "1.0":
        raise ContractError("UNSUPPORTED_SCHEMA", "schema_version must equal '1.0'")
    language = data["language"]
    if language not in COMPLEXITY_ADAPTERS:
        raise ContractError("UNSUPPORTED_LANGUAGE", "language must equal 'python' or 'typescript'")
    expected_adapter = COMPLEXITY_ADAPTERS[language]
    if complexity["adapter"] != expected_adapter:
        raise ContractError("UNSUPPORTED_ADAPTER", f"adapter must equal {expected_adapter!r}")
    if type(complexity["maximum"]) is not int or complexity["maximum"] < 1:
        raise ContractError("INVALID_MAXIMUM", "complexity.maximum must be a positive integer")
    return Contract(
        schema_version="1.0",
        language=language,
        production_paths=_path_list(
            data["production_paths"], "production_paths", allow_empty=False
        ),
        high_risk_paths=_path_list(data["high_risk_paths"], "high_risk_paths", allow_empty=True),
        adapter=expected_adapter,
        maximum=complexity["maximum"],
        gates=_gate_adapters(data["gates"]),
        sha256=content_sha256(content),
    )
