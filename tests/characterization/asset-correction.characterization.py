"""Capture whole-asset target admission with retained rejection controls."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from supportability_gate import (
    characterization,
    contract,
    git_changes,
    refactor_targets,
    standard_results,
)

ASSET = "src/catalog.json"
TARGET = f"{ASSET}::asset:{ASSET}:whole-file"
POLICY = b"""schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = []
[[gates]]
adapter = "python.pytest.v1"
paths = ["src"]
[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, timeout=30
    ).stdout.strip()


def commit(root: Path, name: str) -> str:
    git(root, "add", ".")
    git(root, "commit", "-m", name)
    return git(root, "rev-parse", "HEAD")


def measured_targets() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        git(root, "init", "--initial-branch=main")
        git(root, "config", "user.name", "Fixture")
        git(root, "config", "user.email", "fixture@example.invalid")
        git(root, "config", "core.autocrlf", "false")
        git(root, "remote", "add", "origin", "https://github.com/example/fixture.git")
        (root / "src").mkdir()
        (root / ASSET).write_bytes(b'{"answer":1}\n')
        (root / "src/model.py").write_bytes(b"def calculate(value):\n    return value + 1\n")
        base = commit(root, "before")
        (root / ASSET).write_bytes(b'{"answer":2}\n')
        (root / "src/model.py").write_bytes(b"def calculate(value):\n    return value + 2\n")
        head = commit(root, "after")
        policy = contract.parse_contract(POLICY)
        identity = git_changes.inspect_repository(root, base, head, [])
        changes = git_changes.changed_paths(root, base, head, [])
        assets = tuple(c for c in changes if c.new_path == ASSET)
        return {
            "asset-targets": list(refactor_targets.derive(root, identity, policy, assets, [])[0]),
            "mixed-targets": list(refactor_targets.derive(root, identity, policy, changes, [])[0]),
        }


def parse_target(value: str) -> object:
    try:
        return standard_results._s02_refactor_target_paths([value])
    except standard_results.StandardResultsError as error:
        return error.code


def empty_correction() -> str:
    try:
        characterization._correction_targets([])
    except characterization.CharacterizationError as error:
        return error.code
    return "ADMITTED"


def asset_coverage() -> object:
    scenarios = [{"id": "catalog", "covers": [ASSET], "kind": "baseline"}]
    obligations = [
        {
            "id": "catalog-bytes",
            "category": "baseline",
            "scenario": "catalog",
            "meaningful": True,
            "compatibility": "PASS",
        }
    ]
    value = {
        "required_paths": [ASSET],
        "covered_paths": [ASSET],
        "required_obligations": [],
        "covered_obligations": [],
    }
    try:
        return characterization._result_coverage(value, scenarios, obligations, (ASSET,), (TARGET,))
    except characterization.CharacterizationError as error:
        return error.code


def main() -> None:
    cases = {
        **measured_targets(),
        "asset-parser": parse_target(TARGET),
        "asset-path-mismatch": parse_target("src/a.json::asset:src/b.json:whole-file"),
        "source-disguised-as-asset": parse_target("src/model.py::asset:src/model.py:whole-file"),
        "empty-correction": empty_correction(),
        "asset-coverage": asset_coverage(),
    }
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "asset-correction",
                "behavior": [
                    {"input": key, "output": value} for key, value in sorted(cases.items())
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
