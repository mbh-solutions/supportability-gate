from __future__ import annotations

import json
import os
from pathlib import Path

from supportability_gate import source_integrity


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    manifest, _ = source_integrity._manifest(target)
    source_integrity._verify_candidate_workflow(target, manifest)
    source_integrity._verify_inventory(target)
    print(
        json.dumps(
            {
                "behavior": {
                    "manifest_schema": manifest["schema_version"],
                    "source_validation_controls": True,
                    "trusted_inventory": True,
                },
                "scenario": "source-integrity-boundary",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
