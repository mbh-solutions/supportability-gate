"""Validate first-baseline file inventories and fixed-runtime execution witnesses."""

from __future__ import annotations

import hashlib
import json
import marshal
from types import CodeType

from supportability_gate import contract


def digest(value: bytes) -> str:
    """Bind evidence to exact bytes."""
    return hashlib.sha256(value).hexdigest()


def source_path(path: str) -> bool:
    """Separate admitted source from data without changing production scope."""
    return path.endswith(contract.SOURCE_SUFFIXES["mixed"])


def behavior(sources: dict[str, bytes], cases: object) -> dict[str, object]:
    """Bind observed cases and every non-source asset's exact bytes."""
    return {
        "cases": cases,
        "assets": {
            path: digest(data) for path, data in sorted(sources.items()) if not source_path(path)
        },
    }


def meaningful(value: object, paths: tuple[str, ...]) -> bool:
    """Require varied observed outputs for code and complete asset inventories."""
    if not isinstance(value, dict) or set(value) != {"cases", "assets"}:
        return False
    assets = value["assets"]
    if not isinstance(assets, dict) or set(assets) != {p for p in paths if not source_path(p)}:
        return False
    if any(
        not isinstance(v, str) or len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
        for v in assets.values()
    ):
        return False
    cases = value["cases"]
    if not any(source_path(p) for p in paths):
        return bool(cases == [])
    if not isinstance(cases, list) or len(cases) < 2:
        return False
    if any(not isinstance(row, dict) or set(row) != {"input", "output"} for row in cases):
        return False
    inputs = [json.dumps(row["input"], sort_keys=True) for row in cases]
    outputs = [json.dumps(row["output"], sort_keys=True) for row in cases]
    return len(set(inputs)) == len(inputs) and len(set(outputs)) > 1


def _codes(code: CodeType) -> list[CodeType]:
    return [
        code,
        *(child for item in code.co_consts if isinstance(item, CodeType) for child in _codes(item)),
    ]


def _python_witness(path: str, source: bytes, witness: object) -> bool:
    if not isinstance(witness, dict):
        return False
    codes = _codes(compile(source, "/target/" + path, "exec", dont_inherit=True, optimize=0))
    # Include anonymous/generator bodies as well as named functions. Import alone is insufficient.
    required = {digest(marshal.dumps(code, 2)) for code in codes if code.co_name != "<module>"}
    if not required:
        required = {digest(marshal.dumps(codes[0], 2))}
    return required.issubset(witness) and all(
        type(witness[key]) is int and witness[key] > 0 for key in required
    )


def _typescript_witness(path: str, source: bytes, witness: object) -> bool:
    from tree_sitter import Node

    from supportability_gate import function_changes

    if not isinstance(witness, list) or not witness:
        return False
    if any(
        not isinstance(row, list)
        or len(row) != 3
        or any(type(v) is not int for v in row)
        or row[0] < 0
        or row[1] <= row[0]
        or row[2] <= 0
        for row in witness
    ):
        return False
    parsed = function_changes.parse_typescript_file(path, source)
    for function in parsed.functions:
        node = function.node
        if not isinstance(node, Node):
            return False
        start = len(source[: node.start_byte].decode().encode("utf-16-le")) // 2
        end = len(source[: node.end_byte].decode().encode("utf-16-le")) // 2
        # A V8 function range must identify this function, never an enclosing script/body.
        if not any(left == start and right == end for left, right, _ in witness):
            return False
    return True


def execution_verified(sources: dict[str, bytes], witness: object) -> bool:
    """Recompute source identities and require execution of all static function bodies."""
    if not isinstance(witness, dict) or set(witness) != {p for p in sources if source_path(p)}:
        return False
    for path, row in witness.items():
        if (
            not isinstance(row, dict)
            or set(row) != {"sha256", "execution"}
            or row["sha256"] != digest(sources[path])
        ):
            return False
        validator = _python_witness if path.endswith((".py", ".pyi")) else _typescript_witness
        try:
            if not validator(path, sources[path], row["execution"]):
                return False
        except (SyntaxError, ValueError):
            return False
    return True
