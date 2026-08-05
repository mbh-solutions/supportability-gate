from __future__ import annotations

import json
import os
from pathlib import Path

from supportability_gate.clause_inventory import validate_inventory


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    clauses = validate_inventory(
        (target / "docs" / "supportability_standard.md").read_bytes(),
        (target / "docs" / "normative_clause_inventory.json").read_bytes(),
    )
    behavior = {
        "clause_count": len(clauses),
        "profiles": sorted({profile for clause in clauses for profile in clause.profiles}),
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "clause-inventory-baseline",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
