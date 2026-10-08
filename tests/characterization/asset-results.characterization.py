"""Compose all eight results with explicit asset approval and source evidence."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from supportability_gate import standard_results


def compose(fixture: dict) -> dict:
    return standard_results.compose_results(
        *fixture["inputs"],
        standard_results.RunIdentity(**fixture["identity"]),
        expected_quality_artifact=fixture["expected_quality_artifact"],
        expected_characterization_artifacts=fixture["expected_characterization_artifacts"],
        source_outcomes=fixture["source_outcomes"],
    )


def cases() -> list[dict]:
    definition = Path(os.environ.get("SUPPORTABILITY_CHARACTERIZATION_DEFINITION", "."))
    path = definition / "tests/characterization/asset-results.inputs.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for name in (
        "approved-content",
        "missing-target",
        "stale-approval",
        "wrong-scope",
        "source-control",
    ):
        fixture = copy.deepcopy(
            original["source_control"] if name == "source-control" else original
        )
        refactor = fixture["inputs"][2]
        if name == "missing-target":
            refactor["targets"] = []
        elif name == "stale-approval":
            refactor["authorization"]["head_sha"] = "0" * 40
        elif name == "wrong-scope":
            refactor["authorization"]["scope"] = ["src/other.json"]
        result = compose(fixture)
        rows.append(
            {
                "input": name,
                "output": [
                    {
                        key: entry[key]
                        for key in ("standard", "result", "policy_blocks", "technical_errors")
                    }
                    for entry in result["entries"]
                ],
            }
        )
    return rows


def main() -> None:
    print(json.dumps({"schema_version": "1.0", "scenario": "asset-results", "behavior": cases()}))


if __name__ == "__main__":
    main()
