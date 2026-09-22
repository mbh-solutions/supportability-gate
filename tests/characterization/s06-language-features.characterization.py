from __future__ import annotations

import json

from supportability_gate import function_changes


def _case(value: object, result: object) -> dict[str, object]:
    return {"input": value, "output": result}


def _python_names(source: bytes) -> list[str]:
    parsed = function_changes.parse_python_file("src/sample.py", source)
    return [item.span.qualified_name for item in parsed.functions]


def _typescript_names(source: bytes) -> list[str]:
    parsed = function_changes.parse_typescript_file("src/sample.ts", source)
    return [item.span.qualified_name for item in parsed.functions]


def main() -> None:
    python_source = b"""\
def trace(function: object) -> object:
    return function

@trace
async def fetch(value: int) -> int:
    def normalize(inner: int) -> int:
        return inner
    return normalize(value)

class Reading:
    def value(self) -> int:
        return 1
"""
    typescript_source = b"""\
export const callback = (value: number) => value > 0 ? value : 0;
"""
    behavior = {
        "ordinary-function-identities": [
            _case("python", _python_names(python_source)),
            _case("python-after-line-movement", _python_names(b"\n\n" + python_source)),
            _case("typescript-callback", _typescript_names(typescript_source)),
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
