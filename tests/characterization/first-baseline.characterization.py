"""Measure first-baseline admission and retained rejection of incomplete evidence."""

from __future__ import annotations

import importlib.util
import json
import marshal

from supportability_gate import characterization


def admission() -> str:
    manifest = {
        "schema_version": "5.0",
        "scenarios": [
            {
                "id": "sample",
                "kind": "baseline",
                "covers": ["web/model.js"],
                "api": None,
                "module_roots": [],
            }
        ],
        "obligations": [
            {
                "id": "sample-cases",
                "category": "baseline",
                "scenario": "sample",
                "selector": "$",
                "target": "scenario:sample",
            }
        ],
        "transitions": [],
        "corrections": [],
    }
    try:
        characterization.parse_manifest(json.dumps(manifest).encode(), "a" * 40)
    except characterization.CharacterizationError as error:
        return error.code
    return "ADMITTED"


def witnesses() -> dict[str, object]:
    if importlib.util.find_spec("supportability_gate.baseline_evidence") is None:
        return {
            key: "UNSUPPORTED"
            for key in (
                "asset-inventory",
                "missing-asset",
                "python-executed",
                "python-unexecuted",
                "source-substitution",
                "constant-output",
            )
        }
    from supportability_gate import baseline_evidence as baseline

    assets = {f"content/{index}.json": str(index).encode() for index in range(400)}
    snapshot = baseline.behavior(assets, [])
    source = b"def grade(value):\n    return value * 2\n"
    code = compile(source, "/target/src/model.py", "exec", dont_inherit=True, optimize=0)
    function = next(item for item in code.co_consts if hasattr(item, "co_code"))
    witness = {
        "src/model.py": {
            "sha256": baseline.digest(source),
            "execution": {baseline.digest(marshal.dumps(function, 2)): 1},
        }
    }
    return {
        "asset-inventory": baseline.meaningful(snapshot, tuple(assets)),
        "missing-asset": baseline.meaningful(snapshot, (*assets, "content/missing.json")),
        "python-executed": baseline.execution_verified({"src/model.py": source}, witness),
        "python-unexecuted": baseline.execution_verified({"src/model.py": source}, {}),
        "source-substitution": baseline.execution_verified(
            {"src/model.py": source + b"\n"}, witness
        ),
        "constant-output": baseline.meaningful(
            baseline.behavior(
                {"src/model.py": source}, [{"input": 1, "output": 0}, {"input": 2, "output": 0}]
            ),
            ("src/model.py",),
        ),
    }


def main() -> None:
    cases = {
        "baseline-admission": admission(),
        "ordinary-command": characterization.scenario_command(
            characterization.Scenario("ordinary", "regression", ("src/model.py",)), "python"
        ),
        **witnesses(),
    }
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "first-baseline",
                "behavior": [
                    {"input": key, "output": value} for key, value in sorted(cases.items())
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
