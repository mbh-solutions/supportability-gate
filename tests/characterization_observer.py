"""Fixed hosted Python API observer; never treats driver output as behavior.

The collector is read-only in the hosted sandbox. This measures bounded ordinary
calls in its thread, not hostile in-process memory integrity or complete semantics.
Only synthetic temporary fixtures may use ``observe`` outside the hosted sandbox.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import dataclasses
import dis
import hashlib
import inspect
import io
import json
import marshal
import math
import os
import runpy
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import CodeType, FrameType, GetSetDescriptorType, MemberDescriptorType, ModuleType
from typing import Any

MAX_DEPTH = 16
MAX_NODES = 4096
MAX_ITEMS = 256
MAX_BYTES = 262144
MAX_CASES = 128
MAX_SOURCE_BYTES = 2097152
CODEC = "python-values-v1"


class ObserverError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ObserverError("OBSERVER_UNSUPPORTED_VALUE") from error


def code_sha256(code: CodeType) -> str:
    # Version 2 has no identity-sharing references: compiled and pyc-loaded
    # equivalent code must bind to the same fingerprint across real replays.
    return hashlib.sha256(marshal.dumps(code, 2)).hexdigest()


def _codes(code: CodeType) -> list[CodeType]:
    found = [code]
    for constant in code.co_consts:
        if type(constant) is CodeType:
            found.extend(_codes(constant))
    return found


def _read_source(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ObserverError("OBSERVER_SOURCE_INVALID")
    return path.read_bytes()


def _function_span(source: bytes, qualname: str) -> tuple[int, int]:
    found: list[tuple[int, int]] = []

    def visit(nodes: list[ast.stmt], parents: tuple[str, ...]) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = (*parents, node.name)
                if isinstance(node, ast.FunctionDef) and ".".join(names) == qualname:
                    start = min((item.lineno for item in node.decorator_list), default=node.lineno)
                    found.append((start, node.end_lineno or node.lineno))
                visit(node.body, names)

    visit(ast.parse(source).body, ())
    if len(found) != 1:
        raise ObserverError("OBSERVER_FUNCTION_INVALID")
    return found[0]


def _selection(root: Path, api: str) -> tuple[CodeType, dict[str, object]]:
    path, separator, qualname = api.partition("::function:")
    parts = path.split("/")
    if (
        not separator
        or not path.endswith(".py")
        or any(part in {"", ".", ".."} for part in parts)
        or "\\" in path
        or ":" in path
        or not all(part.isidentifier() for part in qualname.split("."))
    ):
        raise ObserverError("OBSERVER_API_INVALID")
    selected = root.joinpath(*parts)
    if not selected.resolve().is_relative_to(root.resolve()):
        raise ObserverError("OBSERVER_SOURCE_INVALID")
    source = _read_source(selected)
    try:
        module = compile(source, str(selected), "exec", dont_inherit=True, optimize=0)
        matches = [code for code in _codes(module) if code.co_qualname == qualname]
        start, end = _function_span(source, qualname)
    except ObserverError:
        raise
    except (SyntaxError, ValueError) as error:
        raise ObserverError("OBSERVER_SOURCE_INVALID") from error
    if len(matches) != 1 or matches[0].co_flags & (
        inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
    ):
        raise ObserverError("OBSERVER_FUNCTION_INVALID")
    code = matches[0]
    return code, {
        "api": api,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "function_code_sha256": code_sha256(code),
        "start_line": start,
        "end_line": end,
    }


def _dataclass_field_names(source: bytes, qualname: str) -> tuple[str, ...]:
    nodes = ast.parse(source).body
    selected: ast.ClassDef | None = None
    for name in qualname.split("."):
        matches = [node for node in nodes if isinstance(node, ast.ClassDef) and node.name == name]
        if len(matches) != 1:
            raise ObserverError("OBSERVER_DATACLASS_INVALID")
        selected = matches[0]
        nodes = selected.body
    assert selected is not None
    decorators = [
        item.func if isinstance(item, ast.Call) else item for item in selected.decorator_list
    ]
    if not any(
        (isinstance(item, ast.Name) and item.id == "dataclass")
        or (
            isinstance(item, ast.Attribute)
            and isinstance(item.value, ast.Name)
            and item.value.id == "dataclasses"
            and item.attr == "dataclass"
        )
        for item in decorators
    ):
        raise ObserverError("OBSERVER_DATACLASS_INVALID")
    names = tuple(
        node.target.id
        for node in nodes
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    )
    if not names or len(names) > MAX_ITEMS or len(set(names)) != len(names):
        raise ObserverError("OBSERVER_DATACLASS_INVALID")
    return names


def _stored_fields(value: object, names: tuple[str, ...]) -> list[tuple[str, object]]:
    cls = type(value)
    dictionary = next(
        (vars(base)["__dict__"] for base in cls.__mro__ if "__dict__" in vars(base)), None
    )
    if dictionary is not None and type(dictionary) is not GetSetDescriptorType:
        raise ObserverError("OBSERVER_DATACLASS_INVALID")
    storage = object.__getattribute__(value, "__dict__") if dictionary is not None else None
    values: list[tuple[str, object]] = []
    for name in names:
        descriptor = next((vars(base)[name] for base in cls.__mro__ if name in vars(base)), None)
        if type(descriptor) is MemberDescriptorType:
            item = object.__getattribute__(value, name)
        elif descriptor is not None and any(
            "__get__" in vars(base) for base in type(descriptor).__mro__
        ):
            raise ObserverError("OBSERVER_DATACLASS_INVALID")
        elif type(storage) is dict and name in storage:
            item = storage[name]
        else:
            raise ObserverError("OBSERVER_DATACLASS_INVALID")
        values.append((name, item))
    return values


def _dataclass_values(value: object, root: Path) -> tuple[str, str, list[tuple[str, object]]]:
    cls = type(value)
    if type(cls) is not type:
        raise ObserverError("OBSERVER_UNSUPPORTED_VALUE")
    metadata = vars(cls).get("__dataclass_fields__")
    if type(metadata) is not dict or any(
        type(field) is not dataclasses.Field for field in metadata.values()
    ):
        raise ObserverError("OBSERVER_UNSUPPORTED_VALUE")
    module_name, qualname = cls.__module__, cls.__qualname__
    if type(module_name) is not str or type(qualname) is not str:
        raise ObserverError("OBSERVER_DATACLASS_INVALID")
    module = sys.modules.get(module_name)
    filename = vars(module).get("__file__") if type(module) is ModuleType else None
    if type(filename) is not str:
        raise ObserverError("OBSERVER_DATACLASS_INVALID")
    source_path = Path(filename)
    if not source_path.resolve().is_relative_to(root.resolve()):
        raise ObserverError("OBSERVER_DATACLASS_INVALID")
    declared: object = module
    for name in qualname.split("."):
        if type(declared) is not ModuleType and type(declared) is not type:
            raise ObserverError("OBSERVER_DATACLASS_INVALID")
        declared = vars(declared).get(name)
    if declared is not cls:
        raise ObserverError("OBSERVER_DATACLASS_INVALID")
    names = _dataclass_field_names(_read_source(source_path), qualname)
    if tuple(metadata) != names or any(metadata[name].name != name for name in names):
        raise ObserverError("OBSERVER_DATACLASS_INVALID")
    return module_name, qualname, _stored_fields(value, names)


class _Codec:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.nodes = 0
        self.active: set[int] = set()

    def encode(self, value: object, depth: int = 0) -> dict[str, object]:
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise ObserverError("OBSERVER_VALUE_LIMIT")
        primitive = self._primitive(value)
        if primitive is not None:
            return primitive
        if id(value) in self.active:
            raise ObserverError("OBSERVER_VALUE_CYCLE")
        self.active.add(id(value))
        try:
            return self._container(value, depth)
        finally:
            self.active.remove(id(value))

    def _primitive(self, value: Any) -> dict[str, object] | None:
        kind = type(value)
        if kind is type(None) or kind is bool or kind is str:
            if kind is str and len(value) > MAX_BYTES:
                raise ObserverError("OBSERVER_VALUE_LIMIT")
            return {"type": {type(None): "none", bool: "bool", str: "str"}[kind], "value": value}
        if kind is bytes:
            if len(value) > MAX_BYTES // 2:
                raise ObserverError("OBSERVER_VALUE_LIMIT")
            return {"type": "bytes", "value": value.hex()}
        number = self._finite_number(value)
        if number is not None:
            return number
        if kind is datetime:
            if type(value.tzinfo) is not timezone:
                raise ObserverError("OBSERVER_UNSUPPORTED_VALUE")
            return {
                "type": "datetime",
                "value": value.isoformat(timespec="microseconds"),
                "fold": value.fold,
            }
        return None

    def _finite_number(self, value: Any) -> dict[str, object] | None:
        kind = type(value)
        if kind is int:
            if value.bit_length() > 256:
                raise ObserverError("OBSERVER_VALUE_LIMIT")
            return {"type": "int", "value": value}
        if kind is float:
            if not math.isfinite(value):
                raise ObserverError("OBSERVER_UNSUPPORTED_VALUE")
            return {"type": "float", "value": value.hex()}
        if kind is Decimal:
            decimal = value.as_tuple()
            if not value.is_finite() or len(decimal.digits) > 256 or abs(decimal.exponent) > 10000:
                raise ObserverError("OBSERVER_UNSUPPORTED_VALUE")
            return {"type": "decimal", "value": str(value)}
        return None

    def _container(self, value: Any, depth: int) -> dict[str, object]:
        kind = type(value)
        if kind is list or kind is tuple:
            if len(value) > MAX_ITEMS:
                raise ObserverError("OBSERVER_VALUE_LIMIT")
            return {
                "type": "list" if kind is list else "tuple",
                "items": [self.encode(item, depth + 1) for item in value],
            }
        if kind is dict:
            if len(value) > MAX_ITEMS or any(type(key) is not str for key in value):
                raise ObserverError("OBSERVER_UNSUPPORTED_VALUE")
            return {
                "type": "dict",
                "items": [[key, self.encode(value[key], depth + 1)] for key in sorted(value)],
            }
        module, qualname, items = _dataclass_values(value, self.root)
        return {
            "type": "dataclass",
            "module": module,
            "name": qualname,
            "fields": [[name, self.encode(item, depth + 1)] for name, item in items],
        }


def encode_value(value: object, root: Path) -> dict[str, object]:
    encoded = _Codec(root).encode(value)
    if len(canonical_bytes(encoded)) > MAX_BYTES:
        raise ObserverError("OBSERVER_VALUE_LIMIT")
    return encoded


def _arguments(frame: FrameType, root: Path) -> list[dict[str, object]]:
    code = frame.f_code
    count = code.co_argcount + code.co_kwonlyargcount
    count += bool(code.co_flags & inspect.CO_VARARGS) + bool(code.co_flags & inspect.CO_VARKEYWORDS)
    return [
        {"name": name, "value": encode_value(frame.f_locals[name], root)}
        for name in code.co_varnames[:count]
    ]


class _Discard(io.TextIOBase):
    def write(self, value: str) -> int:
        return len(value)


@contextlib.contextmanager
def _discard_driver_stdout() -> Iterator[None]:
    # Suppress descriptor writes as well as Python prints. The saved pipe is
    # restored before the collector emits its own receipt; target output is
    # never parsed into observations. Deliberate descriptor discovery, like
    # arbitrary collector-memory modification, is outside this bounded proxy.
    sys.stdout.flush()
    descriptor = sys.stdout.fileno()
    saved = os.dup(descriptor)
    discarded = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(discarded, descriptor)
        with contextlib.redirect_stdout(_Discard()):
            yield
    finally:
        os.dup2(saved, descriptor)
        os.close(discarded)
        os.close(saved)


class _CallObserver:
    def __init__(self, root: Path, api: str) -> None:
        self.root = root
        self.expected, self.identity = _selection(root, api)
        self.cases: list[dict[str, object]] = []
        self.calls: dict[int, list[dict[str, object]]] = {}
        self.failures: list[str] = []
        self.active = False
        self.hook = self.trace

    def audit(self, event: str, arguments: tuple[object, ...]) -> None:
        if self.active and event in {"sys.settrace", "sys.setprofile"}:
            self.failures.append("OBSERVER_HOOK_TAMPERED")
            raise ObserverError("OBSERVER_HOOK_TAMPERED")

    def trace(self, frame: FrameType, event: str, value: object) -> Any:
        if (
            frame.f_code.co_filename != self.expected.co_filename
            or frame.f_code.co_qualname != self.expected.co_qualname
        ):
            return None
        if code_sha256(frame.f_code) != self.identity["function_code_sha256"]:
            self.failures.append("OBSERVER_CODE_MISMATCH")
            raise ObserverError("OBSERVER_CODE_MISMATCH")
        try:
            if event == "call":
                self.calls[id(frame)] = _arguments(frame, self.root)
            elif event == "return":
                self.returned(frame, value)
        except ObserverError as error:
            self.failures.append(error.code)
            raise
        return self.hook

    def returned(self, frame: FrameType, value: object) -> None:
        arguments = self.calls.pop(id(frame), None)
        opcode = frame.f_code.co_code[frame.f_lasti]
        if dis.opname[opcode] not in {"RETURN_VALUE", "RETURN_CONST"} or arguments is None:
            return
        if len(self.cases) >= MAX_CASES:
            raise ObserverError("OBSERVER_CASE_LIMIT")
        self.cases.append({"input": arguments, "output": encode_value(value, self.root)})

    def run(self, driver: Path) -> None:
        settrace, gettrace = sys.settrace, sys.gettrace
        setprofile, getprofile = sys.setprofile, sys.getprofile
        previous = gettrace()
        if previous is not None or getprofile() is not None:
            raise ObserverError("OBSERVER_HOOK_INVALID")
        sys.addaudithook(self.audit)
        try:
            settrace(self.hook)
            self.active = True
            with _discard_driver_stdout():
                runpy.run_path(str(driver), run_name="__main__")
            identities = (sys.settrace, sys.gettrace, sys.setprofile, sys.getprofile)
            if any(
                actual is not expected
                for actual, expected in zip(
                    identities, (settrace, gettrace, setprofile, getprofile), strict=True
                )
            ):
                self.failures.append("OBSERVER_HOOK_TAMPERED")
            if gettrace() is not self.hook or getprofile() is not None or self.calls:
                self.failures.append("OBSERVER_HOOK_TAMPERED")
        except BaseException as error:
            self.failures.append(
                error.code if isinstance(error, ObserverError) else "OBSERVER_DRIVER_FAILED"
            )
        finally:
            self.active = False
            settrace(previous)
        if self.failures:
            raise ObserverError(self.failures[0])


def observe(target_root: Path, api: str, driver: Path) -> dict[str, object]:
    """Observe synthetic fixtures locally or fixed hosted targets in the sandbox."""
    observer = _CallObserver(target_root, api)
    observer.run(driver)
    if not observer.cases:
        raise ObserverError("OBSERVER_NO_NORMAL_RETURNS")
    result = {"schema_version": "1.0", "codec": CODEC, **observer.identity, "cases": observer.cases}
    if len(canonical_bytes(result)) > MAX_BYTES:
        raise ObserverError("OBSERVER_VALUE_LIMIT")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--driver", required=True)
    arguments = parser.parse_args(argv)
    driver = Path(arguments.driver)
    try:
        if driver.parent != Path("/driver") or driver.is_symlink() or not driver.is_file():
            raise ObserverError("OBSERVER_DRIVER_INVALID")
        result = observe(Path("/target"), arguments.api, driver)
    except Exception as error:
        result = {"schema_version": "1.0", "error": getattr(error, "code", "OBSERVER_FAILED")}
        print(canonical_bytes(result).decode())
        return 2
    print(canonical_bytes(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
