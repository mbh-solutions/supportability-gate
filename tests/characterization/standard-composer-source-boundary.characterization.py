"""Present real retained Standard composition as ordinary input/output rows."""

from __future__ import annotations

import contextlib
import io
import json
import os
import runpy
from pathlib import Path


def main() -> None:
    definition = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"])
    retained = (
        definition / "tests/characterization/gate8-standard-results-boundary-v3.characterization.py"
    )
    namespace = runpy.run_path(str(retained))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        namespace["main"]()
    actual = json.loads(output.getvalue())
    cases = actual["behavior"]["cases"]
    if actual["scenario"] != "gate8-standard-results-boundary-v3" or not isinstance(cases, dict):
        raise RuntimeError("Unexpected retained Standard composition protocol")
    rows = [{"input": name, "output": cases[name]} for name in sorted(cases)]
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "standard-composer-source-boundary",
                "behavior": {"cases": rows},
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
