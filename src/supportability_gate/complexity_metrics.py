"""Obtain exact mccabe metrics and isolated Ruff C901 diagnostics."""

from __future__ import annotations

import ast
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mccabe import PathGraphingAstVisitor  # type: ignore[import-untyped]
from tree_sitter import Node

from supportability_gate import __version__
from supportability_gate.function_changes import FunctionDefinition, FunctionSpan

RUFF_TIMEOUT_SECONDS = 60
_COMPLEXITY = re.compile(r"\((\d+) > 10\)$")
_TYPESCRIPT_BRANCHES = {
    "catch_clause",
    "do_statement",
    "for_in_statement",
    "for_statement",
    "if_statement",
    "switch_case",
    "ternary_expression",
    "while_statement",
}
_TYPESCRIPT_FUNCTIONS = {
    "arrow_function",
    "function_declaration",
    "function_expression",
    "generator_function",
    "generator_function_declaration",
    "method_definition",
}
_TYPESCRIPT_ASSIGNMENT_PATTERNS = {
    "assignment_pattern",
    "object_assignment_pattern",
}
_TYPESCRIPT_PARAMETERS = {"optional_parameter", "required_parameter"}
_TYPESCRIPT_OPTIONAL_MEMBERS = {"member_expression", "subscript_expression"}


class MetricsError(RuntimeError):
    """A fail-closed metric or Ruff parity error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FunctionMetric:
    """Exact mccabe value for one qualified function."""

    span: FunctionSpan
    complexity: int


@dataclass(frozen=True)
class RuffDiagnostic:
    """Normalized Ruff C901 evidence."""

    path: str
    qualified_name: str
    line: int
    complexity: int
    code: str
    message: str


@dataclass(frozen=True)
class RuffCommandRecord:
    """Deterministic record of the fixed Ruff invocation."""

    tool: str
    arguments: tuple[str, ...]
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True)
class RuffResult:
    """Diagnostics and command evidence from one isolated Ruff run."""

    diagnostics: tuple[RuffDiagnostic, ...]
    command: RuffCommandRecord | None


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def measure_definitions(
    definitions: tuple[FunctionDefinition, ...],
    language: str = "python",
) -> tuple[FunctionMetric, ...]:
    """Measure each function through the selected fixed profile."""
    if language == "typescript":
        return _measure_typescript(definitions)
    metrics: list[FunctionMetric] = []
    for definition in definitions:
        if not isinstance(definition.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            raise MetricsError("PROFILE_NODE_MISMATCH", definition.span.path)
        visitor: Any = PathGraphingAstVisitor()
        visitor.preorder(definition.node, visitor)
        graphs = list(visitor.graphs.values())
        if len(graphs) != 1:
            raise MetricsError(
                "MCCABE_GRAPH_MISMATCH",
                f"expected one mccabe graph: {definition.span.path}:{definition.span.qualified_name}",
            )
        metrics.append(FunctionMetric(definition.span, int(graphs[0].complexity())))
    return tuple(sorted(metrics, key=lambda item: (item.span.path, item.span.qualified_name)))


def _typescript_complexity_increment(node: Node) -> int:
    if node.type in _TYPESCRIPT_BRANCHES | _TYPESCRIPT_ASSIGNMENT_PATTERNS:
        return 1
    if node.type in _TYPESCRIPT_PARAMETERS and node.child_by_field_name("value") is not None:
        return 1
    if node.type == "binary_expression" and any(
        child.type in {"&&", "||", "??"} for child in node.children
    ):
        return 1
    if node.type == "augmented_assignment_expression" and any(
        child.type in {"&&=", "||=", "??="} for child in node.children
    ):
        return 1
    if (
        node.type in _TYPESCRIPT_OPTIONAL_MEMBERS
        and node.child_by_field_name("optional_chain") is not None
    ):
        return 1
    return int(
        node.type == "call_expression" and any(child.type == "?." for child in node.children)
    )


def _typescript_complexity(node: Node) -> int:
    complexity = 1

    def visit(current: Node) -> None:
        nonlocal complexity
        if current is not node and current.type in _TYPESCRIPT_FUNCTIONS:
            return
        if current.type == "class_static_block":
            return
        if current.type == "public_field_definition":
            name = current.child_by_field_name("name")
            if name is not None:
                visit(name)
            return
        complexity += _typescript_complexity_increment(current)
        for child in current.named_children:
            visit(child)

    body = node.child_by_field_name("body")
    if body is None:
        raise MetricsError("MISSING_FUNCTION_BODY", str(node.start_point.row + 1))
    parameters = node.child_by_field_name("parameters") or node.child_by_field_name("parameter")
    if parameters is not None:
        visit(parameters)
    visit(body)
    return complexity


def _measure_typescript(
    definitions: tuple[FunctionDefinition, ...],
) -> tuple[FunctionMetric, ...]:
    metrics: list[FunctionMetric] = []
    for definition in definitions:
        if not isinstance(definition.node, Node):
            raise MetricsError("PROFILE_NODE_MISMATCH", definition.span.path)
        metrics.append(FunctionMetric(definition.span, _typescript_complexity(definition.node)))
    return tuple(sorted(metrics, key=lambda item: (item.span.path, item.span.qualified_name)))


def _normalized_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("RUFF_") and key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    environment["NO_COLOR"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def _write_sources(root: Path, sources: dict[str, bytes]) -> None:
    for path, content in sorted(sources.items()):
        destination = root.joinpath(*PurePosixPath(path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)


def _diagnostic_path(filename: str, root: Path) -> str:
    candidate = Path(filename)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise MetricsError(
            "RUFF_PATH_ESCAPE", "Ruff reported a path outside assessed sources"
        ) from error


def _parse_diagnostics(
    stdout: bytes,
    root: Path,
    definitions: tuple[FunctionDefinition, ...],
) -> tuple[RuffDiagnostic, ...]:
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MetricsError("RUFF_JSON", "Ruff did not return valid JSON") from error
    by_location = {
        (item.span.path, item.node.lineno): item.span.qualified_name
        for item in definitions
        if isinstance(item.node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if len(by_location) != len(definitions):
        raise MetricsError("PROFILE_NODE_MISMATCH", "Ruff requires Python definitions")
    diagnostics: list[RuffDiagnostic] = []
    for item in payload:
        path = _diagnostic_path(str(item["filename"]), root)
        line = int(item["location"]["row"])
        qualified_name = by_location.get((path, line))
        match = _COMPLEXITY.search(str(item["message"]))
        if item.get("code") != "C901" or qualified_name is None or match is None:
            raise MetricsError(
                "RUFF_DIAGNOSTIC_MAPPING", f"unmapped Ruff diagnostic: {path}:{line}"
            )
        diagnostics.append(
            RuffDiagnostic(
                path,
                qualified_name,
                line,
                int(match.group(1)),
                "C901",
                str(item["message"]),
            )
        )
    return tuple(sorted(diagnostics, key=lambda item: (item.path, item.line, item.qualified_name)))


def run_ruff(
    sources: dict[str, bytes],
    definitions: tuple[FunctionDefinition, ...],
) -> RuffResult:
    """Run isolated Ruff over exact head blobs without importing target code."""
    if not sources:
        return RuffResult((), None)
    arguments = (
        "check",
        ".",
        "--isolated",
        "--no-cache",
        "--no-respect-gitignore",
        "--select",
        "C901",
        "--config",
        "lint.mccabe.max-complexity = 10",
        "--target-version",
        "py312",
        "--output-format",
        "json",
    )
    with tempfile.TemporaryDirectory(prefix="supportability-gate-") as temporary:
        root = Path(temporary)
        _write_sources(root, sources)
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "ruff", *arguments],
                cwd=root,
                env=_normalized_environment(),
                check=False,
                capture_output=True,
                timeout=RUFF_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise MetricsError("RUFF_TIMEOUT", "Ruff C901 timed out") from error
        except OSError as error:
            raise MetricsError("RUFF_UNAVAILABLE", "Ruff executable unavailable") from error
        if completed.returncode not in {0, 1}:
            detail = (
                completed.stderr.decode("utf-8", errors="replace")
                .replace(temporary, "<temporary>")
                .strip()
            )
            raise MetricsError("RUFF_FAILED", f"Ruff exited {completed.returncode}: {detail}")
        diagnostics = _parse_diagnostics(completed.stdout, root, definitions)
        normalized = (
            json.dumps([asdict(item) for item in diagnostics], sort_keys=True) + "\n"
        ).encode()
        stderr = completed.stderr.replace(temporary.encode(), b"<temporary>")
        command = RuffCommandRecord(
            "ruff",
            arguments,
            completed.returncode,
            _digest(normalized),
            _digest(stderr),
        )
        return RuffResult(diagnostics, command)


def verify_ruff_parity(
    metrics: tuple[FunctionMetric, ...],
    diagnostics: tuple[RuffDiagnostic, ...],
) -> None:
    """Require bidirectional mccabe/Ruff C901 parity at maximum 10."""
    measured = {(item.span.path, item.span.qualified_name): item.complexity for item in metrics}
    reported = {(item.path, item.qualified_name): item.complexity for item in diagnostics}
    expected = {key: value for key, value in measured.items() if value > 10}
    if expected != reported:
        raise MetricsError(
            "RUFF_PARITY_MISMATCH",
            f"mccabe/Ruff parity mismatch; measured={sorted(expected.items())}, "
            f"reported={sorted(reported.items())}",
        )


def tool_versions() -> dict[str, str]:
    """Return exact deterministic tool versions."""
    return {
        "mccabe": importlib.metadata.version("mccabe"),
        "python": platform.python_version(),
        "ruff": importlib.metadata.version("ruff"),
        "supportability_gate": __version__,
        "tree_sitter": importlib.metadata.version("tree-sitter"),
        "tree_sitter_typescript": importlib.metadata.version("tree-sitter-typescript"),
    }
