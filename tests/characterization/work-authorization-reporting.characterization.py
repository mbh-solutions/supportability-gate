from __future__ import annotations

import json
from pathlib import Path

from supportability_gate import reporting


def main() -> None:
    if (
        Path(reporting.__file__).resolve()
        != (Path.cwd() / "src/supportability_gate/reporting.py").resolve()
    ):
        raise RuntimeError("Reporting must execute from the exact target revision")
    cases = []
    inputs = (
        ("PASS", 6, ["refactor-policy-result.json"]),
        ("PASS", 8, ["complexity-result.json:review_evidence.behavior", "quality-provenance.json"]),
        ("PASS", 1, ["complexity-result.json"]),
        ("BLOCK", 6, []),
    )
    for result, standard, sources in inputs:
        entry = {"result": result, "standard": standard}
        cases.append(
            {
                "input": {**entry, "sources": sources},
                "output": reporting._evidence_classes(entry, sources),
            }
        )
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "work-authorization-reporting",
                "behavior": cases,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
