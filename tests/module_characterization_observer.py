"""Fixed same-file body witness collector beside the unchanged API observer.

Only synthetic source may run outside the fixed hosted sandbox. This collector is
selected only by schema-4 module roots. Driver output remains discarded. Helpers'
return/exception markers are execution evidence, never invented semantic outputs.
"""

from __future__ import annotations

import argparse
import dis
from pathlib import Path
from types import FrameType
from typing import Any

import characterization_observer as ordinary

from supportability_gate import characterization as module_observation


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

    def trace(self, frame: FrameType, event: str, value: object) -> Any:
        if frame.f_code.co_filename != self.expected.co_filename:
            return None
        name = frame.f_code.co_qualname
        if name not in self.functions:
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
            self.calls[id(frame)] = ordinary._arguments(frame, self.root)
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
                    "arguments_after": ordinary._arguments(frame, self.root),
                    "outcome": witness["outcome"],
                    "output": ordinary.encode_value(value, self.root) if returned else None,
                    "exception": None if returned else exception,
                }
            )
            if returned and witness["function"] == self.expected.co_qualname:
                execution["primary_case"] = len(self.cases)
                self.returned(frame, value)
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
        "codec": ordinary.CODEC,
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
    }
    if len(ordinary.canonical_bytes(result)) > ordinary.MAX_BYTES:
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
