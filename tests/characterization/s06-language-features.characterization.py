from __future__ import annotations

import json

from supportability_gate import complexity_metrics, function_changes


def _case(value: object, result: object) -> dict[str, object]:
    return {"input": value, "output": result}


def _python_names(source: bytes) -> list[str]:
    parsed = function_changes.parse_python_file("src/sample.py", source)
    return [item.span.qualified_name for item in parsed.functions]


def _typescript_names(source: bytes) -> list[str]:
    parsed = function_changes.parse_typescript_file("src/sample.ts", source)
    return [item.span.qualified_name for item in parsed.functions]


def _python_complexity(source: bytes) -> int:
    parsed = function_changes.parse_python_file("src/sample.py", source)
    return complexity_metrics.measure_definitions(parsed.functions)[0].complexity


def main() -> None:
    property_source = b"""\
class Reading:
    @property
    def value(self) -> int:
        return 1

    @value.setter
    def value(self, next_value: int) -> None:
        self.current = next_value
"""
    overload_source = b"""\
from typing import overload

@overload
def normalize(value: int) -> int: ...

@overload
def normalize(value: str) -> str: ...

def normalize(value: int | str) -> int | str:
    return value
"""
    typescript_source = b"""\
export class Reading {
  get value(): number { return 1; }
  set value(next: number) { this.current = next; }
  private current = 0;
}
"""
    low_match = b"""\
def classify(value: int) -> int:
    match value:
        case 0:
            return 0
        case 1 if value > 0:
            return 1
        case _:
            return -1
"""
    high_cases = b"".join(f"        case {index}: return {index}\n".encode() for index in range(12))
    high_match = b"def classify(value: int) -> int:\n    match value:\n" + high_cases
    high_match += b"        case _: return -1\n"
    behavior = {
        "language-construct-identities": [
            _case("python-property", _python_names(property_source)),
            _case("python-property-after-line-movement", _python_names(b"\n\n" + property_source)),
            _case("python-overloads", _python_names(overload_source)),
            _case("typescript-accessors", _typescript_names(typescript_source)),
        ],
        "modern-match-complexity": [
            _case("guarded-low-complexity", _python_complexity(low_match)),
            _case("twelve-cases", _python_complexity(high_match)),
        ],
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "s06-language-features",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
