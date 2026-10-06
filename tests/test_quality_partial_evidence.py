"""Failed profiles retain receipts only for dependencies actually provisioned."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from supportability_gate import quality_profile

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
