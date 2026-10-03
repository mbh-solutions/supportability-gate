"""Fixed same-file body witness collector beside the unchanged API observer.

Only synthetic source may run outside the fixed hosted sandbox. This collector is
selected only by schema-4 module roots. Driver output remains discarded. Helpers'
return/exception markers are execution evidence, never invented semantic outputs.
"""

from __future__ import annotations

import argparse
import dis
import enum
import inspect
import sys
from pathlib import Path
from types import CodeType, FrameType, GetSetDescriptorType, ModuleType
from typing import Any

import characterization_observer as ordinary

from supportability_gate import characterization as module_observation


class _ModuleCodec(ordinary._Codec):
    def __init__(self, root: Path, path: str, declarations: list[dict[str, Any]]) -> None:
        super().__init__(root)
        self.path = path
        self.declarations = declarations

    def _container(self, value: Any, depth: int) -> dict[str, object]:
        if type(type(value)) is enum.EnumType:
            return self._enum(value, depth)
        if type(value) is not dict or all(type(key) is str for key in value):
            return super()._container(value, depth)
        if len(value) > ordinary.MAX_ITEMS or any(
            type(key) not in {bool, int, str} for key in value
        ):
            raise ordinary.ObserverError("OBSERVER_UNSUPPORTED_VALUE")
        items = [
            [self.encode(key, depth + 1), self.encode(item, depth + 1)]
            for key, item in value.items()
        ]
        items.sort(key=lambda pair: ordinary.canonical_bytes(pair[0]))
        return {"type": "dict-keyed", "items": items}

    def _enum_class(self, value: object) -> tuple[type, dict[str, Any], dict[str, Any]]:
        cls = type(value)
        namespace = vars(cls)
        if any(type(key) is not str for key in namespace):
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        name, module_name = type.__getattribute__(cls, "__qualname__"), namespace.get("__module__")
        if type(name) is not str:
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        module = sys.modules.get(module_name) if type(module_name) is str else None
        module_namespace = vars(module) if type(module) is ModuleType else {}
        if any(type(key) is not str for key in module_namespace):
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        filename = module_namespace.get("__file__")
        declared = next((row for row in self.declarations if row["name"] == name), None)
        if (
            declared is None
            or type(filename) is not str
            or Path(filename).resolve() != (self.root / self.path).resolve()
            or module_namespace.get(name) is not cls
            or cls.__bases__ != ({"Enum": enum.Enum, "StrEnum": enum.StrEnum}[declared["base"]],)
        ):
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        _enum_namespace(namespace, declared)
        _enum_hooks(namespace)
        return cls, declared, namespace

    def _enum(self, value: object, depth: int) -> dict[str, object]:
        cls, declared, namespace = self._enum_class(value)
        descriptor = next(
            (vars(base)["__dict__"] for base in cls.__mro__ if "__dict__" in vars(base)), None
        )
        if type(descriptor) is not GetSetDescriptorType:
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        storage = object.__getattribute__(value, "__dict__")
        members = namespace.get("_member_map_")
        names = [row["name"] for row in declared["members"]]
        if (
            type(storage) is not dict
            or any(type(key) is not str for key in storage)
            or type(members) is not dict
            or any(type(key) is not str for key in members)
            or list(members) != names
        ):
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        if (
            set(storage) != {"_value_", "_name_", "__objclass__", "_sort_order_"}
            or storage["__objclass__"] is not cls
        ):
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        name = storage.get("_name_")
        if type(name) is not str:
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        literal = next((row["value"] for row in declared["members"] if row["name"] == name), None)
        actual = storage.get("_value_")
        if members.get(name) is not value or type(actual) is not type(literal) or actual != literal:
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
        return {
            "type": "enum",
            "source_path": self.path,
            "module": namespace["__module__"],
            "name": type.__getattribute__(cls, "__qualname__"),
            "member": name,
            "value": self.encode(actual, depth + 1),
        }


def _enum_hooks(namespace: Any) -> None:
    trusted = (enum.Enum, enum.StrEnum, str, object)
    for name in (
        "__new__",
        "__str__",
        "__repr__",
        "__format__",
        "__getattribute__",
        "__getattr__",
        "__init__",
        "_generate_next_value_",
        "_new_member_",
    ):
        base_name = "__new__" if name == "_new_member_" else name
        if name in namespace and not any(
            _raw_hook(namespace[name]) is _raw_hook(vars(base).get(candidate))
            for base in trusted
            for candidate in {name, base_name}
        ):
            raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")


def _enum_namespace(namespace: Any, declared: dict[str, Any]) -> None:
    if any(type(key) is not str for key in namespace):
        raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
    generated = {
        "_generate_next_value_",
        "__module__",
        "_new_member_",
        "_use_args_",
        "_member_names_",
        "_member_map_",
        "_value2member_map_",
        "_unhashable_values_",
        "_member_type_",
        "_value_repr_",
        "__doc__",
        "__new__",
        "__str__",
        "__format__",
        "__dict__",
        "__weakref__",
    }
    names = [member["name"] for member in declared["members"]]
    if set(namespace) != generated.intersection(namespace) | set(names):
        raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
    stored_names = namespace.get("_member_names_")
    if (
        type(stored_names) is not list
        or any(type(name) is not str for name in stored_names)
        or stored_names != names
    ):
        raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
    value_members = namespace.get("_value2member_map_")
    if type(value_members) is not dict or any(
        type(key) not in {bool, int, str} for key in value_members
    ):
        raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
    if namespace.get("_member_type_") is not (str if declared["base"] == "StrEnum" else object):
        raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")
    if namespace.get("_value_repr_") is not (
        str.__repr__ if declared["base"] == "StrEnum" else None
    ):
        raise ordinary.ObserverError("OBSERVER_ENUM_INVALID")


def _raw_hook(value: object) -> object:
    return object.__getattribute__(value, "__func__") if type(value) is staticmethod else value


def _encode(
    value: object, root: Path, path: str, declarations: list[dict[str, Any]]
) -> dict[str, object]:
    encoded = _ModuleCodec(root, path, declarations).encode(value)
    if len(ordinary.canonical_bytes(encoded)) > ordinary.MAX_BYTES:
        raise ordinary.ObserverError("OBSERVER_VALUE_LIMIT")
    return encoded


def _arguments(frame: FrameType, observer: _ModuleObserver) -> list[dict[str, object]]:
    code = frame.f_code
    count = code.co_argcount + code.co_kwonlyargcount
    count += bool(code.co_flags & inspect.CO_VARARGS) + bool(code.co_flags & inspect.CO_VARKEYWORDS)
    return [
        {"name": name, "value": observer.encode(frame.f_locals[name])}
        for name in code.co_varnames[:count]
    ]


class _ModuleObserver(ordinary._CallObserver):
    def __init__(self, root: Path, api: str, roots: list[str]) -> None:
        super().__init__(root, api)
        self.path = api.split("::", 1)[0]
        self.source = ordinary._read_source(root / self.path)
        self.inventory = module_observation.module_source_inventory(self.source, self.path)
        compiled = compile(
            self.source, str(root / self.path), "exec", dont_inherit=True, optimize=0
        )
        self.expected_codes = {
            code.co_qualname: ordinary.code_sha256(code) for code in ordinary._codes(compiled)
        }
        self.functions = {row["name"]: row for row in self.inventory["functions"]}
        self.root_names = module_observation.module_public_roots(roots, api, self.functions)
        self.roots = roots
        self.executions: list[dict[str, Any]] = []
        self.root_cases: list[dict[str, Any]] = []
        self.witnesses: list[dict[str, Any]] = []
        self.frames: dict[int, dict[str, Any]] = {}
        self.current: int | None = None
        self.dataclass_factory = ordinary.dataclasses._create_fn.__code__
        self.dataclass_processor = ordinary.dataclasses._process_class.__code__
        self.generated_codes: set[CodeType] = set()

    def encode(self, value: object) -> dict[str, object]:
        return _encode(value, self.root, self.path, self.inventory["enum_declarations"])

    def _owns_frame(self, frame: FrameType) -> bool:
        filename = frame.f_globals.get("__file__")
        return (
            frame.f_code.co_filename == self.expected.co_filename
            or type(filename) is str
            and filename == self.expected.co_filename
        )

    def _unaccounted_code(self) -> None:
        self.failures.append("OBSERVER_UNACCOUNTED_CODE")
        raise ordinary.ObserverError("OBSERVER_UNACCOUNTED_CODE")

    def _declared_dataclass_factory(self, frame: FrameType) -> bool:
        namespace = frame.f_locals.get("globals")
        if type(namespace) is not dict or namespace.get("__file__") != self.expected.co_filename:
            return False
        caller = frame.f_back
        while caller is not None and caller.f_code is not self.dataclass_processor:
            caller = caller.f_back
        cls = caller.f_locals.get("cls") if caller is not None else None
        if type(cls) is not type:
            return False
        try:
            ordinary._dataclass_field_names(self.source, type.__getattribute__(cls, "__qualname__"))
        except ordinary.ObserverError:
            return False
        return True

    def audit(self, event: str, arguments: tuple[object, ...]) -> None:
        super().audit(event, arguments)
        if not self.active or event not in {"compile", "exec", "code.__new__", "function.__new__"}:
            return
        caller = sys._getframe(1)
        if (
            event == "exec"
            and caller.f_code is self.dataclass_factory
            and self._declared_dataclass_factory(caller)
            and type(arguments[0]) is CodeType
        ):
            self.generated_codes.update(ordinary._codes(arguments[0]))
        if self._owns_frame(caller):
            # Runtime target compilation is outside the source inventory, even
            # with a foreign filename or an alias for the creation primitive.
            # Standard-library dataclass generation has its own caller frame.
            self._unaccounted_code()

    def trace(self, frame: FrameType, event: str, value: object) -> Any:
        if not self._owns_frame(frame):
            return None
        if frame.f_code in self.generated_codes:
            return None
        name = frame.f_code.co_qualname
        if name not in self.functions:
            # Only the exact compiled module/class scaffolding may be skipped.
            # A precreated body assigned to the target namespace is also owned,
            # including code whose filename differs from the target source.
            if frame.f_code.co_flags & inspect.CO_NEWLOCALS or ordinary.code_sha256(
                frame.f_code
            ) != self.expected_codes.get(name):
                self._unaccounted_code()
            return None
        if ordinary.code_sha256(frame.f_code) != self.expected_codes[name]:
            self.failures.append("OBSERVER_CODE_MISMATCH")
            raise ordinary.ObserverError("OBSERVER_CODE_MISMATCH")
        try:
            self._event(frame, event, value, name)
        except ordinary.ObserverError as error:
            self.failures.append(error.code)
            raise
        return self.hook

    def _event(self, frame: FrameType, event: str, value: object, name: str) -> None:
        if event == "call":
            self._called(frame, name)
        elif event == "line" and id(frame) in self.frames:
            if frame.f_lineno in self.functions[name]["lines"]:
                self.frames[id(frame)]["lines"].add(frame.f_lineno)
        elif event == "return" and id(frame) in self.frames:
            self._returned(frame, value)
        elif event == "exception" and id(frame) in self.frames:
            self.frames[id(frame)]["exception"] = (
                type(value[1]).__module__ + "." + type(value[1]).__qualname__
            )

    def _called(self, frame: FrameType, name: str) -> None:
        if self.current is None and name in self.root_names:
            if len(self.executions) >= module_observation.MODULE_MAX_EXECUTIONS:
                raise ordinary.ObserverError("OBSERVER_CASE_LIMIT")
            self.current = len(self.executions)
            self.executions.append(
                {
                    "id": self.current,
                    "root": name,
                    "outcome": None,
                    "case": self.current,
                    "primary_case": None,
                }
            )
            self.calls[id(frame)] = _arguments(frame, self)
        if self.current is not None:
            self.frames[id(frame)] = {
                "execution": self.current,
                "function": name,
                "lines": set(),
                "outcome": None,
                "root_frame": id(frame) in self.calls,
                "exception": None,
            }

    def _returned(self, frame: FrameType, value: object) -> None:
        witness = self.frames.pop(id(frame))
        root_frame = witness.pop("root_frame")
        exception = witness.pop("exception")
        returned = dis.opname[frame.f_code.co_code[frame.f_lasti]] in {
            "RETURN_VALUE",
            "RETURN_CONST",
        }
        witness["outcome"] = "RETURN" if returned else "EXCEPTION"
        witness["lines"] = sorted(witness["lines"])
        if len(self.witnesses) >= module_observation.MODULE_MAX_CALLS:
            raise ordinary.ObserverError("OBSERVER_MODULE_CALL_LIMIT")
        self.witnesses.append(witness)
        if root_frame:
            execution = self.executions[witness["execution"]]
            execution["outcome"] = witness["outcome"]
            self.root_cases.append(
                {
                    "root": witness["function"],
                    "input": self.calls[id(frame)],
                    "arguments_after": _arguments(frame, self),
                    "outcome": witness["outcome"],
                    "output": self.encode(value) if returned else None,
                    "exception": None if returned else exception,
                }
            )
            if returned and witness["function"] == self.expected.co_qualname:
                execution["primary_case"] = len(self.cases)
                self.cases.append(
                    {"input": self.calls.pop(id(frame)), "output": self.encode(value)}
                )
            else:
                self.calls.pop(id(frame), None)
            self.current = None


def observe_module(
    root: Path, api: str, driver: Path, roots: list[str] | None = None
) -> dict[str, object]:
    """Measure actual public calls and all same-file bodies reached within them."""
    observer = _ModuleObserver(root, api, roots or [api])
    observer.run(driver)
    if observer.frames or observer.current is not None:
        raise ordinary.ObserverError("OBSERVER_MODULE_INCOMPLETE")
    if not observer.cases:
        raise ordinary.ObserverError("OBSERVER_NO_NORMAL_RETURNS")
    primary = {
        "schema_version": "1.0",
        "codec": module_observation.MODULE_CODEC,
        **observer.identity,
        "cases": observer.cases,
    }
    # Source inventory always uses the fixed hosted /target identity. In synthetic
    # fixture tests only, the observer also checks the actual temporary filename
    # fingerprint on every call. Hosted roots already equal /target.
    primary["function_code_sha256"] = observer.functions[observer.expected.co_qualname][
        "code_sha256"
    ]
    result = {
        "schema_version": module_observation.MODULE_WITNESS_SCHEMA,
        "path": observer.path,
        "api": api,
        **observer.inventory,
        "primary": primary,
        "roots": observer.roots,
        "root_cases": observer.root_cases,
        "executions": observer.executions,
        "calls": observer.witnesses,
        "body_metric": module_observation.MODULE_BODY_METRIC,
        "body_coverage": module_observation.module_body_report(
            observer.inventory, observer.witnesses
        ),
    }
    if len(ordinary.canonical_bytes(result)) + 1 > module_observation.MODULE_MAX_JSON_BYTES:
        raise ordinary.ObserverError("OBSERVER_VALUE_LIMIT")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", required=True)
    parser.add_argument("--driver", required=True)
    parser.add_argument("--module-roots", required=True)
    arguments = parser.parse_args()
    try:
        driver = Path(arguments.driver)
        if driver.parent != Path("/driver") or driver.is_symlink() or not driver.is_file():
            raise ordinary.ObserverError("OBSERVER_DRIVER_INVALID")
        roots = ordinary.json.loads(arguments.module_roots)
        result = observe_module(Path("/target"), arguments.api, driver, roots)
    except module_observation.ModuleObservationError as error:
        print(ordinary.canonical_bytes({"schema_version": "1.0", "error": str(error)}).decode())
        return 2
    except Exception as error:
        print(
            ordinary.canonical_bytes(
                {"schema_version": "1.0", "error": getattr(error, "code", "OBSERVER_FAILED")}
            ).decode()
        )
        return 2
    print(ordinary.canonical_bytes(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
