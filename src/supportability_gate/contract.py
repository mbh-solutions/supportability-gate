"""Parse and validate the immutable base-ref repository contract."""

from __future__ import annotations

import hashlib
import posixpath
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


class ContractError(ValueError):
    """A fail-closed base contract error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Contract:
    """Validated base-ref policy contract."""

    schema_version: str
    language: str
    production_paths: tuple[str, ...]
    high_risk_paths: tuple[str, ...]
    adapter: str
    maximum: int
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


def parse_contract(content: bytes) -> Contract:
    """Parse the only supported contract schema."""
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ContractError("MALFORMED_CONTRACT", str(error)) from error
    _require_keys(
        data,
        {"schema_version", "language", "production_paths", "high_risk_paths", "complexity"},
        "contract",
    )
    complexity = data["complexity"]
    if not isinstance(complexity, dict):
        raise ContractError("INVALID_COMPLEXITY", "complexity must be a table")
    _require_keys(complexity, {"adapter", "maximum"}, "complexity")
    if data["schema_version"] != "1.0":
        raise ContractError("UNSUPPORTED_SCHEMA", "schema_version must equal '1.0'")
    if data["language"] != "python":
        raise ContractError("UNSUPPORTED_LANGUAGE", "language must equal 'python'")
    if complexity["adapter"] != "python.c901-touched.v1":
        raise ContractError("UNSUPPORTED_ADAPTER", "adapter must equal 'python.c901-touched.v1'")
    if type(complexity["maximum"]) is not int or complexity["maximum"] != 10:
        raise ContractError("INVALID_MAXIMUM", "complexity.maximum must equal 10")
    return Contract(
        schema_version="1.0",
        language="python",
        production_paths=_path_list(
            data["production_paths"], "production_paths", allow_empty=False
        ),
        high_risk_paths=_path_list(data["high_risk_paths"], "high_risk_paths", allow_empty=True),
        adapter="python.c901-touched.v1",
        maximum=10,
        sha256=content_sha256(content),
    )
