from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from supportability_gate.architecture_policy import evaluate_architecture
from supportability_gate.contract import GateAdapter, parse_contract
from supportability_gate.function_changes import ChangedFileAssessment
from supportability_gate.git_changes import ChangedPath
from supportability_gate.modularity_policy import evaluate_modularity


POLICY = parse_contract(
    b'''schema_version = "1.0"
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
'''
)
PATH = "src/orders/service.py"
OWNER = "src/orders/model.py"


def main() -> None:
    architecture = evaluate_architecture(
        POLICY,
        {
            OWNER: b"OWNER = True\n",
            PATH: b"import typing\nfrom orders.model import OWNER\n",
        },
        GateAdapter("python.import-linter.v1", ("src",)),
    )
    assessment = ChangedFileAssessment(
        ChangedPath("ADDED", None, PATH), False, True, True, (1, 2)
    )
    review = {
        "module_boundaries": [
            {
                "path": PATH,
                "owner_path": OWNER,
                "basis": "responsibility",
                "justification": "Existing orders model owns this cohesive orders service.",
            }
        ]
    }
    arguments = (POLICY, (assessment,), review, architecture)
    if "quality" in inspect.signature(evaluate_modularity).parameters:
        quality = SimpleNamespace(
            commands=tuple(
                SimpleNamespace(
                    adapter=gate.adapter,
                    observed_paths=(PATH,),
                    zero_statement_paths=(),
                    executed=True,
                    exit_code=0,
                )
                for gate in POLICY.gates
            )
        )
        modularity = evaluate_modularity(*arguments, quality)
    else:
        modularity = evaluate_modularity(*arguments)
    behavior = {
        "architecture_blocks": list(architecture.blocks),
        "architecture_edges": len(architecture.edges),
        "modularity_adapters": list(modularity.coverage[0].adapters),
        "modularity_blocks": list(modularity.blocks),
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "architecture-modularity-baseline",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
