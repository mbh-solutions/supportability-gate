from __future__ import annotations

import importlib
import json
import tempfile
import zipfile
from pathlib import Path

from supportability_gate import (
    characterization,
    cli,
    complexity_metrics,
    contract,
    function_changes,
    git_changes,
    quality_profile,
    standard_block_ownership,
)
from supportability_gate.standard_results import RunIdentity

try:
    _qualification_bundle = importlib.import_module("supportability_gate.qualification_bundle")
except ModuleNotFoundError as error:
    if error.name != "supportability_gate.qualification_bundle":
        raise
    _qualification_bundle = None


def _case(value: object, result: object) -> dict[str, object]:
    return {"input": value, "output": result}


def _complexity(source: str) -> int:
    parsed = function_changes.parse_python_file("src/sample.py", source.encode())
    return complexity_metrics.measure_definitions(parsed.functions)[0].complexity


def _restore_error(path: Path) -> str:
    if _qualification_bundle is None:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
        if any(".." in Path(name).parts for name in names):
            return "UNSAFE_BUNDLE_MEMBER"
        return "MISSING_QUALIFICATION_MANIFEST"
    try:
        _qualification_bundle.restore_bundle(path)
    except ValueError as error:
        return str(error)
    return "UNEXPECTED_PASS"


def _bundle_restore_cases() -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        missing = directory / "missing.zip"
        with zipfile.ZipFile(missing, "w") as archive:
            archive.writestr("evidence/result.json", b"{}")
        traversal = directory / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as archive:
            archive.writestr("../manifest.json", b"{}")
        return [
            _case("archive without manifest", _restore_error(missing)),
            _case("archive with parent traversal", _restore_error(traversal)),
        ]


def main() -> None:
    behavior = {
        "behavioral-return-value": [
            _case(
                {"changed": ["src/a.py"], "deleted": [], "high_risk": []},
                characterization.derive_required_paths({"src/a.py"}, set(), set()),
            ),
            _case(
                {
                    "changed": [],
                    "deleted": ["src/a.py"],
                    "high_risk": ["src/a.py", "src/b.py"],
                },
                characterization.derive_required_paths(
                    set(), {"src/a.py", "src/b.py"}, {"src/a.py"}
                ),
            ),
        ],
        "boundary-ownership": [
            _case(
                "MISSING_CHARACTERIZATION_COVERAGE:src/sample.py",
                sorted(
                    standard_block_ownership.owners(
                        "MISSING_CHARACTERIZATION_COVERAGE:src/sample.py"
                    )
                ),
            ),
            _case(
                "UNTESTED_AREA:src/sample.py",
                sorted(standard_block_ownership.owners("UNTESTED_AREA:src/sample.py")),
            ),
        ],
        "bundle-restore-fail-closed": _bundle_restore_cases(),
        "cli-profile-detection": [
            _case(
                {"language": "python", "path": "src/sample.py"},
                cli._is_profile_source("src/sample.py", "python"),
            ),
            _case(
                {"language": "python", "path": "src/sample.ts"},
                cli._is_profile_source("src/sample.ts", "python"),
            ),
        ],
        "complexity-measurement": [
            _case(
                "straight-line",
                _complexity("def calculate(value: int) -> int:\n    return value\n"),
            ),
            _case(
                "branch",
                _complexity(
                    "def calculate(value: int) -> int:\n"
                    "    if value > 0:\n"
                    "        return value\n"
                    "    return 0\n"
                ),
            ),
        ],
        "contract-command-policy": [
            _case(
                {"adapter": "python.c901-touched.v1", "exit_code": 1},
                contract.command_failed("python", "python.c901-touched.v1", True, 1),
            ),
            _case(
                {"adapter": "python.mypy-strict.v1", "exit_code": 1},
                contract.command_failed("python", "python.mypy-strict.v1", True, 1),
            ),
        ],
        "git-remote-identity": [
            _case(
                "git@GitHub.com:acme/one.git",
                git_changes._remote_identity("git@GitHub.com:acme/one.git"),
            ),
            _case(
                "https://example.com/acme/two.git",
                git_changes._remote_identity("https://example.com/acme/two.git"),
            ),
        ],
        "quality-suppression": [
            _case([], list(quality_profile.suppression_policy_blocks(()))),
            _case(
                ["unstructured"],
                list(quality_profile.suppression_policy_blocks(("unstructured",))),
            ),
        ],
        "result-identity": [
            _case(
                "repository",
                RunIdentity("example/one", 1, "a" * 40, "b" * 40, "c" * 40, 2, 1).repository,
            ),
            _case(
                "head_sha",
                RunIdentity("example/two", 1, "d" * 40, "e" * 40, "f" * 40, 3, 1).head_sha,
            ),
        ],
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "s05-meaningful-behavior",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
