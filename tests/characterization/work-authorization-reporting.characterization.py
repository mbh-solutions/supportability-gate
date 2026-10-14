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
    for result, standard in (("PASS", 6), ("PASS", 8), ("PASS", 1), ("BLOCK", 6)):
        entry = {"result": result, "standard": standard}
        cases.append(
            {
                "input": entry,
                "output": reporting._evidence_classes(entry, ["refactor-policy-result.json"]),
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
