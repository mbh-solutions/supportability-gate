from __future__ import annotations

import json
from pathlib import Path

from supportability_gate import contract, function_changes, gate_policy, git_changes


def _case(language: str, lint_scope: str) -> dict[str, object]:
    languages = ("python", "typescript") if language == "mixed" else ("python",)
    policy = contract.Contract(
        schema_version="1.1" if language == "mixed" else "1.0",
        language=language,
        languages=languages,
        production_paths=("src",),
        high_risk_paths=(),
        adapter="python.c901-touched.v1" if language == "python" else "mixed",
        maximum=10,
        gates=tuple(
            contract.GateAdapter(
                adapter, (lint_scope,) if adapter == "python.ruff-lint.v1" else ("src",)
            )
            for adapter in contract.FIXED_ADAPTERS_BY_LANGUAGE[language]
        ),
        sha256="a" * 64,
    )
    change = function_changes.ChangedFileAssessment(
        git_changes.ChangedPath("MODIFIED", "src/sample.py", "src/sample.py"),
        True,
        True,
        True,
        (1,),
    )
    return {
        "input": {"language": language, "lint_scope": lint_scope, "path": "src/sample.py"},
        "output": list(gate_policy.evaluate_contract(policy, (change,))),
    }


def main() -> None:
    if (
        Path(gate_policy.__file__).resolve()
        != (Path.cwd() / "src/supportability_gate/gate_policy.py").resolve()
    ):
        raise RuntimeError("coverage policy was not loaded from the target source")
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "gate-policy-retained-coverage",
                "behavior": [
                    _case("python", "src"),
                    _case("python", "other"),
                    _case("mixed", "src"),
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
