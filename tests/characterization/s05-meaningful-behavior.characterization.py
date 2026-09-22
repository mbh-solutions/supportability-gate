from __future__ import annotations

import json

from supportability_gate import characterization, quality_profile, standard_block_ownership
from supportability_gate.standard_results import RunIdentity


def _case(value: object, result: object) -> dict[str, object]:
    return {"input": value, "output": result}


def main() -> None:
    behavior = {
        "behavioral-return-value": [
            _case(
                {"changed": ["src/a.py"], "deleted": [], "high_risk": []},
                characterization.derive_required_paths({"src/a.py"}, set(), set()),
            ),
            _case(
                {
                    "changed": [],
                    "deleted": ["src/a.py"],
                    "high_risk": ["src/a.py", "src/b.py"],
                },
                characterization.derive_required_paths(
                    set(), {"src/a.py", "src/b.py"}, {"src/a.py"}
                ),
            ),
        ],
        "boundary-ownership": [
            _case(
                "MISSING_CHARACTERIZATION_COVERAGE:src/sample.py",
                sorted(
                    standard_block_ownership.owners(
                        "MISSING_CHARACTERIZATION_COVERAGE:src/sample.py"
                    )
                ),
            ),
            _case(
                "UNTESTED_AREA:src/sample.py",
                sorted(standard_block_ownership.owners("UNTESTED_AREA:src/sample.py")),
            ),
        ],
        "quality-suppression": [
            _case([], list(quality_profile.suppression_policy_blocks(()))),
            _case(
                ["unstructured"],
                list(quality_profile.suppression_policy_blocks(("unstructured",))),
            ),
        ],
        "result-identity": [
            _case(
                "repository",
                RunIdentity("example/one", 1, "a" * 40, "b" * 40, "c" * 40, 2, 1).repository,
            ),
            _case(
                "head_sha",
                RunIdentity("example/two", 1, "d" * 40, "e" * 40, "f" * 40, 3, 1).head_sha,
            ),
        ],
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "s05-meaningful-behavior",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )[:-1]
    )


if __name__ == "__main__":
    main()
