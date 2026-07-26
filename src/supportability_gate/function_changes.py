"""Map changed Python lines to qualified functions and methods."""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass

from supportability_gate.git_changes import ChangedPath


class PythonSourceError(ValueError):
    """A changed production source could not be assessed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class FunctionSpan:
    """Stable qualified function identity and AST span."""

    path: str
    qualified_name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class FunctionDefinition:
    """A function span bound to its parsed AST node."""

    span: FunctionSpan
    node: FunctionNode


@dataclass(frozen=True)
class ParsedPythonFile:
    """Parsed source and all nested function identities."""

    path: str
    content: bytes
    functions: tuple[FunctionDefinition, ...]


@dataclass(frozen=True)
class ChangedFileAssessment:
    """Production classification and changed head lines for one Git identity."""

    change: ChangedPath
    base_production: bool
    head_production: bool
    complexity_assessed: bool
    changed_head_lines: tuple[int, ...]


@dataclass(frozen=True)
class FunctionDelta:
    """One touched or deleted function across base and head."""

    base: FunctionDefinition | None
    head: FunctionDefinition | None


@dataclass(frozen=True)
class FileFunctionAnalysis:
    """Parsed file sides plus policy-relevant function deltas."""

    assessment: ChangedFileAssessment
    base: ParsedPythonFile | None
    head: ParsedPythonFile | None
    deltas: tuple[FunctionDelta, ...]


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.context: list[str] = []
        self.functions: list[FunctionDefinition] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.context.append(node.name)
        for child in node.body:
            self.visit(child)
        self.context.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: FunctionNode) -> None:
        qualified_name = ".".join([*self.context, node.name])
        end_line = node.end_lineno
        if end_line is None:
            raise PythonSourceError(
                "MISSING_AST_SPAN", f"missing AST end line: {self.path}:{node.lineno}"
            )
        span = FunctionSpan(self.path, qualified_name, node.lineno, end_line)
        self.functions.append(FunctionDefinition(span, node))
        self.context.append(node.name)
        for child in node.body:
            self.visit(child)
        self.context.pop()


def _decode_python(content: bytes, path: str) -> str:
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(content).readline)
        return content.decode(encoding)
    except (LookupError, SyntaxError, UnicodeError) as error:
        raise PythonSourceError(
            "SOURCE_ENCODING", f"cannot decode changed source: {path}"
        ) from error


def parse_python_file(path: str, content: bytes) -> ParsedPythonFile:
    """Parse source without importing or executing it."""
    try:
        tree = ast.parse(_decode_python(content, path), filename=path, type_comments=True)
    except SyntaxError as error:
        location = f"{path}:{error.lineno or 0}:{error.offset or 0}"
        raise PythonSourceError(
            "SYNTAX_ERROR", f"syntax error in changed production file: {location}"
        ) from error
    collector = _FunctionCollector(path)
    collector.visit(tree)
    functions = tuple(
        sorted(
            collector.functions, key=lambda item: (item.span.start_line, item.span.qualified_name)
        )
    )
    return ParsedPythonFile(path, content, functions)


def _deltas(
    assessment: ChangedFileAssessment,
    base: ParsedPythonFile | None,
    head: ParsedPythonFile | None,
) -> tuple[FunctionDelta, ...]:
    base_functions = {item.span.qualified_name: item for item in base.functions} if base else {}
    head_functions = {item.span.qualified_name: item for item in head.functions} if head else {}
    changed_lines = set(assessment.changed_head_lines)
    touched = (
        set(head_functions)
        if not assessment.base_production
        else {
            name
            for name, item in head_functions.items()
            if changed_lines.intersection(range(item.span.start_line, item.span.end_line + 1))
        }
    )
    deleted = set(base_functions) - set(head_functions)
    deltas = [FunctionDelta(base_functions.get(name), head_functions[name]) for name in touched]
    deltas.extend(FunctionDelta(base_functions[name], None) for name in deleted)

    def identity(delta: FunctionDelta) -> tuple[str, str]:
        definition = delta.head or delta.base
        if definition is None:
            raise PythonSourceError("EMPTY_FUNCTION_DELTA", "function delta has no base or head")
        return definition.span.path, definition.span.qualified_name

    return tuple(sorted(deltas, key=identity))


def analyze_file(
    assessment: ChangedFileAssessment,
    base_content: bytes | None,
    head_content: bytes | None,
) -> FileFunctionAnalysis:
    """Bind base/head functions for one changed production identity."""
    old_path = assessment.change.old_path
    new_path = assessment.change.new_path
    base = (
        parse_python_file(old_path, base_content) if old_path and base_content is not None else None
    )
    head = (
        parse_python_file(new_path, head_content) if new_path and head_content is not None else None
    )
    return FileFunctionAnalysis(assessment, base, head, _deltas(assessment, base, head))
