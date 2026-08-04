"""Build and enforce fixed Python and TypeScript import graphs."""

from __future__ import annotations

import ast
import posixpath
from dataclasses import dataclass
from pathlib import PurePosixPath

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from supportability_gate.contract import Contract, GateAdapter
from supportability_gate.function_changes import PythonSourceError, _decode_python

ARCHITECTURE_ADAPTERS = {
    "python": "python.import-linter.v1",
    "typescript": "typescript.import-boundaries.v1",
}
_LAYERS = {
    "application": "application",
    "workflow": "application",
    "domain": "domain",
    "infrastructure": "infrastructure",
    "persistence": "infrastructure",
    "database": "infrastructure",
    "external": "infrastructure",
    "framework": "infrastructure",
    "package": "infrastructure",
    "presentation": "presentation",
    "cli": "presentation",
    "ui": "presentation",
    "api": "presentation",
}
_TYPESCRIPT_SUFFIXES = (".cts", ".mts", ".ts", ".tsx")
_DOMAIN_PYTHON_IMPORTS = frozenset(
    {
        "__future__",
        "abc",
        "collections",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "heapq",
        "itertools",
        "math",
        "numbers",
        "operator",
        "re",
        "statistics",
        "string",
        "types",
        "typing",
    }
)


@dataclass(frozen=True)
class ImportEdge:
    """One source-backed import edge."""

    source: str
    target: str
    line: int
    specifier: str
    internal: bool


@dataclass(frozen=True)
class ArchitectureResult:
    """Deterministic executed architecture evidence."""

    adapter: str
    executed: bool
    covered_paths: tuple[str, ...]
    nodes: tuple[str, ...]
    edges: tuple[ImportEdge, ...]
    blocks: tuple[str, ...]


def source_imports(path: str, content: bytes) -> tuple[tuple[int, str], ...]:
    """Return parser-derived import locations for one source blob."""
    if path.endswith((".py", ".pyi")):
        try:
            ast_tree = ast.parse(_decode_python(content, path), filename=path, type_comments=True)
        except SyntaxError as error:
            raise PythonSourceError(
                "SYNTAX_ERROR", f"syntax error in production file: {path}"
            ) from error
        imports = [
            (node.lineno, alias.name)
            for node in ast.walk(ast_tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        imports.extend(
            (node.lineno, "." * node.level + (node.module or ""))
            for node in ast.walk(ast_tree)
            if isinstance(node, ast.ImportFrom)
        )
        return tuple(sorted(imports))
    language = (
        tree_sitter_typescript.language_tsx()
        if path.endswith(".tsx")
        else tree_sitter_typescript.language_typescript()
    )
    syntax_tree = Parser(Language(language)).parse(content)
    if syntax_tree.root_node.has_error:
        raise PythonSourceError("SYNTAX_ERROR", f"syntax error in production file: {path}")
    return tuple(sorted(_typescript_specifiers(syntax_tree.root_node, content)))


def _production_root(path: str, roots: tuple[str, ...]) -> str:
    matches = tuple(root for root in roots if path == root or path.startswith(f"{root}/"))
    if not matches:
        raise PythonSourceError("ARCHITECTURE_PATH_OUTSIDE_PRODUCTION", path)
    return max(matches, key=len)


def _layer(path: str, roots: tuple[str, ...]) -> str | None:
    root = _production_root(path, roots)
    root_layer = _LAYERS.get(PurePosixPath(root).name)
    relative = PurePosixPath(path).relative_to(root)
    return root_layer or next(
        (_LAYERS[part] for part in relative.parts[:-1] if part in _LAYERS), None
    )


def _python_modules(paths: tuple[str, ...], roots: tuple[str, ...]) -> dict[str, str]:
    modules: dict[str, str] = {}
    for path in paths:
        root = _production_root(path, roots)
        relative = PurePosixPath(path).relative_to(root).with_suffix("")
        parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
        module = ".".join(parts)
        if module:
            modules[module] = path
    return modules


def _python_target(module: str, alias: str | None, modules: dict[str, str]) -> tuple[str, bool]:
    candidates = (f"{module}.{alias}" if module and alias else "", module)
    target = next((modules[item] for item in candidates if item in modules), None)
    return (target, True) if target else ((module.split(".", 1)[0] or alias or ""), False)


def _python_edges(path: str, content: bytes, modules: dict[str, str]) -> list[ImportEdge]:
    try:
        tree = ast.parse(_decode_python(content, path), filename=path, type_comments=True)
    except SyntaxError as error:
        raise PythonSourceError(
            "SYNTAX_ERROR", f"syntax error in production file: {path}"
        ) from error
    current = next((name for name, source in modules.items() if source == path), "")
    package = current if path.endswith("/__init__.py") else current.rpartition(".")[0]
    edges: list[ImportEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target, internal = _python_target(alias.name, None, modules)
                edges.append(ImportEdge(path, target, node.lineno, alias.name, internal))
        elif isinstance(node, ast.ImportFrom):
            prefix: list[str] = []
            if node.level:
                prefix = package.split(".") if package else []
                prefix = prefix[: max(0, len(prefix) - node.level + 1)]
            module = ".".join([*prefix, *(node.module or "").split(".")]).strip(".")
            for alias in node.names:
                target, internal = _python_target(module, alias.name, modules)
                edges.append(ImportEdge(path, target, node.lineno, module or alias.name, internal))
    return edges


def _node_text(node: Node, content: bytes) -> str:
    try:
        return content[node.start_byte : node.end_byte].decode("utf-8")
    except UnicodeDecodeError as error:
        raise PythonSourceError("SOURCE_ENCODING", "TypeScript source must be UTF-8") from error


def _typescript_specifiers(node: Node, content: bytes) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    if node.type in {"import_statement", "export_statement"}:
        strings = [child for child in node.named_children if child.type == "string"]
        if strings:
            found.append((strings[-1].start_point.row + 1, _node_text(strings[-1], content)[1:-1]))
    if node.type == "call_expression":
        function = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if function and arguments and _node_text(function, content) in {"import", "require"}:
            strings = [child for child in arguments.named_children if child.type == "string"]
            if len(strings) == 1:
                found.append(
                    (strings[0].start_point.row + 1, _node_text(strings[0], content)[1:-1])
                )
    for child in node.named_children:
        found.extend(_typescript_specifiers(child, content))
    return found


def _typescript_target(source: str, specifier: str, paths: set[str]) -> tuple[str, bool]:
    if specifier.startswith("."):
        stem = posixpath.normpath(posixpath.join(posixpath.dirname(source), specifier))
        suffix = PurePosixPath(stem).suffix
        rewrites = {".js": (".ts", ".tsx"), ".mjs": (".mts",), ".cjs": (".cts",)}
        base, candidates = (stem[: -len(suffix)], []) if suffix in rewrites else (stem, [stem])
        candidates.extend(f"{base}{item}" for item in rewrites.get(suffix, _TYPESCRIPT_SUFFIXES))
        candidates.extend(
            f"{base}/index{item}" for item in rewrites.get(suffix, _TYPESCRIPT_SUFFIXES)
        )
        target = next((item for item in candidates if item in paths), None)
        return (target, True) if target else (specifier, False)
    target = next(
        (path for path in sorted(paths) if path.rsplit(".", 1)[0].endswith(f"/{specifier}")),
        None,
    )
    return (target, True) if target else (specifier.split("/", 1)[0], False)


def _typescript_edges(path: str, content: bytes, paths: set[str]) -> list[ImportEdge]:
    language = (
        tree_sitter_typescript.language_tsx()
        if path.endswith(".tsx")
        else tree_sitter_typescript.language_typescript()
    )
    tree = Parser(Language(language)).parse(content)
    if tree.root_node.has_error:
        raise PythonSourceError("SYNTAX_ERROR", f"syntax error in production file: {path}")
    edges = []
    for line, specifier in _typescript_specifiers(tree.root_node, content):
        target, internal = _typescript_target(path, specifier, paths)
        edges.append(ImportEdge(path, target, line, specifier, internal))
    return edges


def _reachable(start: str, goal: str, graph: dict[str, set[str]]) -> bool:
    # ponytail: linear search per edge; replace with SCC only if repository graph size makes it slow.
    pending, seen = [start], set()
    while pending:
        node = pending.pop()
        if node == goal:
            return True
        if node not in seen:
            seen.add(node)
            pending.extend(sorted(graph.get(node, set()) - seen))
    return False


def _direction_block(source: str, target: str, roots: tuple[str, ...]) -> bool:
    outer = {"infrastructure", "presentation"}
    source_layer, target_layer = _layer(source, roots), _layer(target, roots)
    if source_layer == "domain":
        return target_layer not in {None, "domain"}
    if source_layer == "application":
        return target_layer in outer
    return bool(source_layer in outer and target_layer in outer and source_layer != target_layer)


def _blocks(edges: tuple[ImportEdge, ...], roots: tuple[str, ...]) -> tuple[str, ...]:
    graph: dict[str, set[str]] = {}
    for edge in edges:
        if edge.internal:
            graph.setdefault(edge.source, set()).add(edge.target)
    blocks: set[str] = set()
    for edge in edges:
        location = f"{edge.source}:{edge.line}:{edge.specifier}"
        if edge.internal and _reachable(edge.target, edge.source, graph):
            blocks.add(f"IMPORT_CYCLE:{location}")
        if edge.internal and _direction_block(edge.source, edge.target, roots):
            blocks.add(f"DEPENDENCY_INVERSION:{location}")
        if _layer(edge.source, roots) == "domain":
            target_layer = _layer(edge.target, roots) if edge.internal else None
            root = edge.target.split(".", 1)[0]
            if (edge.internal and target_layer != "domain") or (
                not edge.internal
                and (not edge.source.endswith((".py", ".pyi")) or root not in _DOMAIN_PYTHON_IMPORTS)
            ):
                blocks.add(f"FORBIDDEN_DOMAIN_DEPENDENCY:{location}")
    return tuple(sorted(blocks))


def evaluate_architecture(
    policy: Contract,
    sources: dict[str, bytes],
    gate: GateAdapter | None,
) -> ArchitectureResult:
    """Execute fixed static import analysis without importing target code."""
    adapter = ARCHITECTURE_ADAPTERS[policy.language]
    paths = tuple(sorted(sources))
    if gate is None or gate.adapter != adapter:
        return ArchitectureResult(
            adapter, False, (), paths, (), ("ARCHITECTURE_GATE_NOT_EXECUTED",)
        )
    coverage = tuple(path for path in paths if gate.covers(path))
    coverage_blocks = tuple(
        f"ARCHITECTURE_PRODUCTION_COVERAGE:{path}" for path in paths if path not in coverage
    )
    if policy.language == "python":
        modules = _python_modules(paths, policy.production_paths)
        raw_edges = [edge for path in paths for edge in _python_edges(path, sources[path], modules)]
    else:
        path_set = set(paths)
        raw_edges = [
            edge for path in paths for edge in _typescript_edges(path, sources[path], path_set)
        ]
    edges = tuple(
        sorted(
            set(raw_edges),
            key=lambda item: (
                item.source,
                item.line,
                item.specifier,
                item.target,
                item.internal,
            ),
        )
    )
    return ArchitectureResult(
        adapter,
        True,
        coverage,
        paths,
        edges,
        tuple(sorted((*coverage_blocks, *_blocks(edges, policy.production_paths)))),
    )
