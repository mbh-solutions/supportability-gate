"""Measure native browser source admission and fail-closed asset validation."""

from __future__ import annotations

import json
import zlib

from supportability_gate import (
    architecture_policy,
    characterization,
    cli,
    complexity_metrics,
    contract,
    function_changes,
    quality_profile,
    refactor_targets,
)


def _policy() -> contract.Contract:
    return contract.parse_contract(
        b"""schema_version = "1.0"
language = "typescript"
production_paths = ["src"]
high_risk_paths = []
[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["src"]
[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["src"]
[complexity]
adapter = "typescript.c901-equivalent-touched.v1"
maximum = 10
"""
    )


def _command(path: str) -> list[str] | str:
    scenario = characterization.Scenario("probe", "regression", (path,))
    try:
        return characterization.scenario_command(scenario, "mixed")
    except characterization.CharacterizationError as error:
        return error.code


def _asset(suffix: str, content: bytes) -> str:
    validator = quality_profile.ASSET_VALIDATORS.get(suffix)
    return quality_profile._asset_result(validator[1], content) if validator else "UNSUPPORTED"


def _icon() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", b"\0\0\0\1\0\0\0\1\x08\x06\0\0\0")
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0\xff"))
        + chunk(b"IEND", b"")
    )
    return (
        b"\0\0\1\0\1\0\1\1\0\0\1\0\x20\0"
        + len(png).to_bytes(4, "little")
        + (22).to_bytes(4, "little")
        + png
    )


def main() -> None:
    path = "src/grade.js"
    body = "".join(f"if (value === {i}) return {i};" for i in range(10))
    parsed = function_changes.parse_typescript_file(
        path, f"export function grade(value) {{{body} return -1;}}".encode()
    )
    architecture = architecture_policy.evaluate_architecture(
        _policy(),
        {
            "src/domain/grade.js": b"import { render } from '../presentation/view.js'; export const grade = render;",
            "src/presentation/view.js": b"import { grade } from '../domain/grade.js'; export const render = grade;",
        },
        contract.GateAdapter("typescript.import-boundaries.v1", ("src",)),
    )
    cases = {
        "javascript-classification": {
            "complexity": cli._is_profile_source(path, "mixed"),
            "quality": bool(quality_profile.source_files((path,), "mixed")),
            "refactor": refactor_targets._profile_source(path, "mixed"),
        },
        "javascript-complexity": [
            metric.complexity
            for metric in complexity_metrics.measure_definitions(parsed.functions, "typescript")
        ],
        "javascript-dependencies": list(architecture.blocks),
        "javascript-scenario": _command("web/grade.js"),
        "asset-scenario": _command("content/course.json"),
        "html-external": _asset(".html", b'<html><script src="app.js"></script></html>'),
        "html-inline": _asset(".html", b"<html><script>run()</script></html>"),
        "html-event": _asset(".html", b'<html><button onclick="run()">Go</button></html>'),
        "icon-valid": _asset(".ico", _icon()),
        "icon-truncated": _asset(".ico", _icon()[:-1]),
    }
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "browser-coverage",
                "behavior": [
                    {"input": key, "output": value} for key, value in sorted(cases.items())
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
