from __future__ import annotations

import json
from dataclasses import replace

from supportability_gate import contract, function_changes, gate_policy, git_changes

PYTHON_POLICY = """schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = []

[[gates]]
adapter = "python.c901-touched.v1"
paths = ["src"]

[[gates]]
adapter = "python.import-linter.v1"
paths = ["src"]

[[gates]]
adapter = "python.mypy-strict.v1"
paths = ["src"]

[[gates]]
adapter = "python.pytest.v1"
paths = ["src"]

[[gates]]
adapter = "python.ruff-lint.v1"
paths = ["src"]

[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
"""
MIXED_POLICY = PYTHON_POLICY.replace(
    'schema_version = "1.0"\nlanguage = "python"',
    'schema_version = "1.1"\nlanguages = ["python", "typescript"]',
).replace(
    '[complexity]\nadapter = "python.c901-touched.v1"\nmaximum = 10',
    """[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["src"]

[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["src"]

[complexity]
maximum = 10""",
)


def main() -> None:
    python = contract.parse_contract(PYTHON_POLICY.encode())
    changed = function_changes.ChangedFileAssessment(
        git_changes.ChangedPath("MODIFIED", "src/sample.py", "src/sample.py"),
        True,
        True,
        True,
        (1,),
    )
    if gate_policy.evaluate_contract(python, (changed,)):
        raise RuntimeError("covered Python source was rejected")
    uncovered = replace(
        python,
        gates=tuple(
            replace(gate, paths=("other",)) if gate.adapter == "python.ruff-lint.v1" else gate
            for gate in python.gates
        ),
    )
    if gate_policy.evaluate_contract(uncovered, (changed,)) != (
        "CHANGED_FILE_GATE_COVERAGE:python.ruff-lint.v1:src/sample.py",
    ):
        raise RuntimeError("uncovered Python source was accepted")
    if gate_policy.contract_change_blocks(python, python):
        raise RuntimeError("stable Python profile was narrowed")
    if "mixed" in gate_policy.APPROVED_ADAPTERS_BY_LANGUAGE:
        mixed = contract.parse_contract(MIXED_POLICY.encode())
        if gate_policy.evaluate_contract(mixed, (changed,)):
            raise RuntimeError("covered mixed source was rejected")
        expected = set(gate_policy.APPROVED_ADAPTERS_BY_LANGUAGE["python"]) | set(
            gate_policy.APPROVED_ADAPTERS_BY_LANGUAGE["typescript"]
        )
        if set(gate_policy.APPROVED_ADAPTERS_BY_LANGUAGE["mixed"]) != expected:
            raise RuntimeError("mixed profile does not compose both fixed profiles")
        if "LANGUAGE_PROFILE_NARROWING" not in gate_policy.contract_change_blocks(mixed, python):
            raise RuntimeError("mixed profile narrowing was not blocked")
    payload = {
        "behavior": {
            "fixed_profiles": "preserved",
            "mixed_extension": "compatible",
        },
        "scenario": "gate-policy-mixed-compatibility",
        "schema_version": "1.0",
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
