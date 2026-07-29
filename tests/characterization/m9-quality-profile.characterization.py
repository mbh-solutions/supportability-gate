from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(target / "src")
    completed = subprocess.run(
        [sys.executable, "-P", "-m", "supportability_gate", "--help"],
        cwd=target,
        env=environment,
        check=False,
        capture_output=True,
        timeout=30,
    )
    behavior = {
        "exit_code": completed.returncode,
        "stderr_sha256": hashlib.sha256(completed.stderr.replace(b"\r\n", b"\n")).hexdigest(),
        "stdout_sha256": hashlib.sha256(completed.stdout.replace(b"\r\n", b"\n")).hexdigest(),
    }
    print(
        json.dumps(
            {"behavior": behavior, "scenario": "m9-quality-profile", "schema_version": "1.0"},
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
