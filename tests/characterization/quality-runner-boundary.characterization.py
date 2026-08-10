from __future__ import annotations

import json
import os
from pathlib import Path

from supportability_gate import quality_runner


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    environment = quality_runner.fixed_environment(Path("output"), target)
    behavior = {
        "quality_runner_contract": all(
            environment.get(name) == value
            for name, value in {"CI": "true", "NO_COLOR": "1", "PYTHONHASHSEED": "0"}.items()
        )
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "quality-runner-boundary",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
