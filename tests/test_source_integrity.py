from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def _source_integrity_module():
    path = ROOT / "tests/source_integrity_check.py"
    spec = importlib.util.spec_from_file_location("source_integrity_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


source_integrity = _source_integrity_module()


def _candidate(tmp_path: Path) -> Path:
    candidate = tmp_path / "candidate"
    for name in ("docs", ".github", "src", "tests"):
        (candidate / name).mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT / "src/supportability_gate", candidate / "src/supportability_gate", dirs_exist_ok=True
    )
    shutil.copytree(ROOT / "docs", candidate / "docs", dirs_exist_ok=True)
    shutil.copytree(ROOT / ".github/workflows", candidate / ".github/workflows", dirs_exist_ok=True)
    shutil.copy2(
        ROOT / "tests/test_clause_inventory.py", candidate / "tests/test_clause_inventory.py"
    )
    shutil.copy2(
        ROOT / "tests/test_evaluate_complexity.py", candidate / "tests/test_evaluate_complexity.py"
    )
    return candidate


def test_current_static_controls_pass() -> None:
    manifest, manifest_sha = source_integrity._manifest(ROOT)

    assert len(manifest_sha) == 64
    source_integrity._verify_trusted_pins(ROOT, manifest)
    source_integrity._verify_immutable(ROOT, manifest)
    source_integrity._verify_inventory(ROOT)
    source_integrity._verify_candidate_workflow(ROOT, manifest)
    assert len(source_integrity._receipts(ROOT, manifest)) == 7


def test_source_validation_success_stub_blocks(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    (candidate / ".github/workflows/source-validation.yml").write_text(
        "jobs:\n  source-validation:\n    name: Source Validation\n    steps:\n      - run: true\n",
        encoding="utf-8",
    )
    manifest, _ = source_integrity._manifest(ROOT)

    with pytest.raises(source_integrity.IntegrityFailure) as caught:
        source_integrity._verify_candidate_workflow(candidate, manifest)

    assert caught.value.decision == "BLOCK"
    assert caught.value.code == "SOURCE_VALIDATION_CONTROL_MISSING"


def test_standard_tamper_blocks(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    standard = candidate / "docs/supportability_standard.md"
    standard.write_bytes(standard.read_bytes() + b"\nchanged\n")
    manifest, _ = source_integrity._manifest(ROOT)

    with pytest.raises(source_integrity.IntegrityFailure) as caught:
        source_integrity._verify_immutable(candidate, manifest)

    assert caught.value.decision == "BLOCK"
    assert caught.value.code.startswith("IMMUTABLE_SOURCE_MISMATCH:")


def _conformance(candidate: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(ROOT / "tests/source_integrity_conformance.py"),
            "--repository",
            str(candidate),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_trusted_conformance_passes_current_validator(tmp_path: Path) -> None:
    output = tmp_path / "result.json"

    completed = _conformance(ROOT, output)

    assert completed.returncode == 0
    assert (
        json.loads(output.read_text(encoding="utf-8"))["code"]
        == "SOURCE_INTEGRITY_CONFORMANCE_PASS"
    )


def test_trusted_conformance_blocks_weakened_candidate(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    module = candidate / "src/supportability_gate/clause_inventory.py"
    content = module.read_text(encoding="utf-8")
    module.write_text(
        content.replace(
            '    """Return validated clauses or fail closed on any incomplete mapping."""\n',
            '    """Return validated clauses or fail closed on any incomplete mapping."""\n    return ()\n',
            1,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "result.json"

    completed = _conformance(candidate, output)
    result = json.loads(output.read_text(encoding="utf-8"))

    assert completed.returncode == 1
    assert result["status"] == "BLOCK"
    assert result["code"] == "CONFORMANCE_CASE:clean:CLAUSE_COUNT:0"
