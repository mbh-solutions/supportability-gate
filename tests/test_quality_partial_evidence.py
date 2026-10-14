"""Failed profiles retain receipts only for dependencies actually provisioned."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from supportability_gate import quality_profile, quality_runner

_SPEC = importlib.util.spec_from_file_location(
    "partial_hosted_quality_profile", Path(__file__).with_name("hosted_quality_profile.py")
)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def _result(adapter: str, exit_code: int) -> quality_profile.GateResult:
    return quality_profile.GateResult(
        adapter, (), "none", (), (), True, exit_code, "a" * 64, "b" * 64, "c" * 64, ()
    )


def _locked_package(root: Path, *, installed: bool) -> None:
    root.mkdir(parents=True)
    (root / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "fixture", "dependencies": {"fixture-package": "1.0.0"}},
                    "node_modules/fixture-package": {
                        "version": "1.0.0",
                        "resolved": "https://registry.npmjs.org/fixture-package/-/fixture-package-1.0.0.tgz",
                        "integrity": "sha512-" + "a" * 88,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    if installed:
        package = root / "node_modules" / "fixture-package"
        package.mkdir(parents=True)
        (package / "package.json").write_text(
            json.dumps({"name": "fixture-package", "version": "1.0.0"}), encoding="utf-8"
        )


def test_failed_python_prefix_does_not_claim_staged_node_dependencies(tmp_path: Path) -> None:
    _locked_package(tmp_path / "target-dependencies", installed=False)

    assert runner._provisioned_node_receipts(tmp_path, (_result("python.pytest.v1", 1),)) == ()


def test_failed_target_install_retains_only_verified_tool_install(tmp_path: Path) -> None:
    _locked_package(tmp_path / "quality-tools", installed=True)
    _locked_package(tmp_path / "target-dependencies", installed=False)

    receipts = runner._provisioned_node_receipts(
        tmp_path,
        (
            _result("typescript.tool-install.v1", 0),
            _result("typescript.target-install.v1", 1),
        ),
    )

    assert len(receipts) == 3
    assert all(value.startswith("npm-tool-") for value in receipts)
    assert any(value.startswith("npm-tool-installed:") for value in receipts)


@pytest.mark.parametrize("lock_present", [False, True])
def test_successful_provisioner_still_requires_verifiable_install(
    tmp_path: Path, lock_present: bool
) -> None:
    if lock_present:
        _locked_package(tmp_path / "target-dependencies", installed=False)

    with pytest.raises(quality_profile.QualityProfileError) as caught:
        runner._provisioned_node_receipts(tmp_path, (_result("typescript.target-install.v1", 0),))

    assert caught.value.code == "UNVERIFIABLE_DEPENDENCY_IDENTITY"


def test_complete_node_profile_retains_both_installed_receipts(tmp_path: Path) -> None:
    for directory in ("quality-tools", "target-dependencies"):
        _locked_package(tmp_path / directory, installed=True)

    receipts = runner._provisioned_node_receipts(
        tmp_path,
        (
            _result("typescript.tool-install.v1", 0),
            _result("typescript.target-install.v1", 0),
        ),
    )

    assert len(receipts) == 6
    for kind in ("tool", "target"):
        assert any(value.startswith(f"npm-{kind}-installed:") for value in receipts)


def test_malformed_lcov_retained_before_parser_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "target"
    repository.mkdir()
    output = tmp_path / "output"
    plan = quality_runner.CommandPlan(
        "typescript.test.v1",
        ("node", "--test"),
        ("node", "--test"),
        "coverage",
        ("src/sample.ts",),
    )
    work = quality_runner.command_work_directory(output, plan.adapter)
    work.mkdir(parents=True)
    raw = b"SF:src/sample.ts\nDA:1,1\nLF:2\nLH:1\nend_of_record\n"
    (work / "coverage.lcov").write_bytes(raw)
    monkeypatch.setattr(runner.quality_runner, "sandbox_command", lambda *a, **k: ("unused",))
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b"")
    )
    with pytest.raises(quality_profile.QualityProfileError, match="LF=2 LH=1 DA=1 DA_hits=1"):
        runner._run_command(plan, repository, output)
    name = "quality-runtime-proof--typescript.test.v1"
    assert (output / "diagnostics" / f"{name}.stdout.log").read_bytes() == raw
    metadata = json.loads((output / "diagnostics" / f"{name}.stderr.log").read_text())
    assert metadata["report_sha256"] == quality_profile._sha256(raw)
    assert metadata["report_bytes"] == len(raw)
