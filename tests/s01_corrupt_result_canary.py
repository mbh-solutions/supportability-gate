"""Never-merge hosted canary for corrupt canonical Standard results."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

EXPECTED = "MALFORMED_STANDARD_RESULTS_APPLICABILITY"


def _fixtures(repository: Path) -> ModuleType:
    path = repository / "tests" / "test_standard_results.py"
    spec = importlib.util.spec_from_file_location("s01_standard_results_fixtures", path)
    if spec is None or spec.loader is None:
        raise SystemExit(99)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    repository = Path(os.environ["TARGET_REPOSITORY"])
    gate_source = Path(os.environ["DEPLOYED_GATE_SOURCE"])
    sys.path.insert(0, str(gate_source))
    fixtures = _fixtures(repository)
    payload = fixtures._compose(fixtures._inputs())
    payload["applicability_evidence"]["changed_files"][0]["status"] = {}
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "standard-results.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        command = [
            sys.executable,
            "-P",
            "-m",
            "supportability_gate.standard_results_enforcer",
            *fixtures._enforcer_arguments(path, 1),
            "--github-summary",
        ]
        completed = subprocess.run(
            command,
            cwd=repository,
            env={**os.environ, "PYTHONPATH": str(gate_source)},
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    print(completed.stdout, end="")
    if completed.returncode != 2 or completed.stdout.strip() != EXPECTED or completed.stderr:
        print("CANARY_RESULT_MISMATCH", file=sys.stderr)
        return 99
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        summary = Path(summary_path).read_text(encoding="utf-8")
        print(summary, end="")
        if EXPECTED not in summary or "Diagnosis unavailable." not in summary:
            print("CANARY_SUMMARY_MISMATCH", file=sys.stderr)
            return 99
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
