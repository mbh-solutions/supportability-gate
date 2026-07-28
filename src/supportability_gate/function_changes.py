"""Map changed Python or TypeScript lines to qualified functions and methods."""

from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from supportability_gate.git_changes import ChangedPath


class PythonSourceError(ValueError):
    """A changed production source could not be assessed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
SourceNode = FunctionNode | Node
_TYPESCRIPT_FUNCTIONS = {
    "arrow_function",
    "function_declaration",
    "function_expression",
    "generator_function",
    "generator_function_declaration",
    "method_definition",
}


@dataclass(frozen=True)
class FunctionSpan:
    """Stable qualified function identity and AST span."""

    path: str
    qualified_name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ResponsibilitySpan:
    """One changed function, module, or frontend-component boundary."""

    start_line: int
    end_line: int
    kind: str
    name: str


@dataclass(frozen=True)
class FunctionDefinition:
    """A function span bound to its parsed AST node."""

    span: FunctionSpan
    node: SourceNode


@dataclass(frozen=True)
class ParsedSourceFile:
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
    base: ParsedSourceFile | None
    head: ParsedSourceFile | None
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


def _unique_functions(
    path: str, functions: list[FunctionDefinition]
) -> tuple[FunctionDefinition, ...]:
    ordered = tuple(
        sorted(functions, key=lambda item: (item.span.start_line, item.span.qualified_name))
    )
    names = [item.span.qualified_name for item in ordered]
    if len(names) != len(set(names)):
        raise PythonSourceError(
            "AMBIGUOUS_FUNCTION_IDENTITY", f"duplicate qualified function identity: {path}"
        )
    return ordered


def parse_python_file(path: str, content: bytes) -> ParsedSourceFile:
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
    return ParsedSourceFile(path, content, _unique_functions(path, collector.functions))


def _typescript_text(node: Node, content: bytes) -> str:
    try:
        return content[node.start_byte : node.end_byte].decode("utf-8")
    except UnicodeDecodeError as error:
        raise PythonSourceError("SOURCE_ENCODING", "TypeScript source must be UTF-8") from error


class _TypeScriptFunctionCollector:
    def __init__(self, path: str, content: bytes) -> None:
        self.path = path
        self.content = content
        self.context: list[str] = []
        self.functions: list[FunctionDefinition] = []

    def _name(self, node: Node) -> str:
        name = node.child_by_field_name("name")
        parent = node.parent
        if (
            name is None
            and parent
            and parent.type
            in {
                "pair",
                "public_field_definition",
                "variable_declarator",
            }
        ):
            name = parent.child_by_field_name("name") or parent.child_by_field_name("key")
        if name is None:
            raise PythonSourceError(
                "AMBIGUOUS_FUNCTION_IDENTITY",
                f"unnamed TypeScript function: {self.path}:{node.start_point.row + 1}",
            )
        value = _typescript_text(name, self.content)
        if not value:
            raise PythonSourceError("AMBIGUOUS_FUNCTION_IDENTITY", self.path)
        return value

    def visit(self, node: Node) -> None:
        if node.type in {"class_declaration", "class_expression"}:
            self.context.append(self._name(node))
            for child in node.named_children:
                self.visit(child)
            self.context.pop()
            return
        if node.type in _TYPESCRIPT_FUNCTIONS:
            self._visit_function(node)
            return
        for child in node.named_children:
            self.visit(child)

    def _visit_function(self, node: Node) -> None:
        name = self._name(node)
        qualified_name = ".".join([*self.context, name])
        span = FunctionSpan(
            self.path,
            qualified_name,
            node.start_point.row + 1,
            node.end_point.row + 1,
        )
        self.functions.append(FunctionDefinition(span, node))
        self.context.append(name)
        body = node.child_by_field_name("body")
        if body is not None:
            self.visit(body)
        self.context.pop()


def parse_typescript_file(path: str, content: bytes) -> ParsedSourceFile:
    """Parse TypeScript or TSX without importing or executing it."""
    language = (
        tree_sitter_typescript.language_tsx()
        if path.endswith(".tsx")
        else tree_sitter_typescript.language_typescript()
    )
    tree = Parser(Language(language)).parse(content)
    if tree.root_node.has_error:
        raise PythonSourceError("SYNTAX_ERROR", f"syntax error in changed production file: {path}")
    collector = _TypeScriptFunctionCollector(path, content)
    collector.visit(tree.root_node)
    return ParsedSourceFile(path, content, _unique_functions(path, collector.functions))


def _line_spans(lines: set[int]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for line in sorted(lines):
        if not spans or line > spans[-1][1] + 1:
            spans.append((line, line))
        else:
            spans[-1] = (spans[-1][0], line)
    return spans


def responsibility_spans(
    path: str, content: bytes, changed_lines: set[int]
) -> tuple[ResponsibilitySpan, ...]:
    """Map changed lines to exact parser-derived responsibility spans."""
    parsed = (
        parse_python_file(path, content)
        if path.endswith(".py")
        else parse_typescript_file(path, content)
    )
    touched = tuple(
        item.span
        for item in parsed.functions
        if changed_lines.intersection(range(item.span.start_line, item.span.end_line + 1))
    )
    boundaries = [
        ResponsibilitySpan(
            span.start_line,
            span.end_line,
            "component"
            if not path.endswith(".py") and span.qualified_name.rsplit(".", 1)[-1][:1].isupper()
            else "function",
            span.qualified_name,
        )
        for span in touched
    ]
    covered = {line for span in touched for line in range(span.start_line, span.end_line + 1)}
    module_spans = (
        _line_spans(changed_lines - covered) if changed_lines else [(1, len(content.splitlines()))]
    )
    boundaries.extend(ResponsibilitySpan(start, end, "module", path) for start, end in module_spans)
    return tuple(boundaries)


def _deltas(
    assessment: ChangedFileAssessment,
    base: ParsedSourceFile | None,
    head: ParsedSourceFile | None,
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
    language: str = "python",
) -> FileFunctionAnalysis:
    """Bind base/head functions for one changed production identity."""
    parser = parse_python_file if language == "python" else parse_typescript_file
    old_path = assessment.change.old_path
    new_path = assessment.change.new_path
    base = parser(old_path, base_content) if old_path and base_content is not None else None
    head = parser(new_path, head_content) if new_path and head_content is not None else None
    return FileFunctionAnalysis(assessment, base, head, _deltas(assessment, base, head))
