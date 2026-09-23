"""Declared Python target dependencies are provisioned without executing target source."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

from supportability_gate import quality_profile, quality_runner

_RUNNER_PATH = Path(__file__).with_name("hosted_quality_profile.py")
_SPEC = importlib.util.spec_from_file_location("hosted_quality_profile", _RUNNER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def test_static_project_requirements_select_current_platform(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\n'
        'dependencies = ["tzdata==2026.3", "websockets>=15,<17", '
        "\"windows-only==1; sys_platform == 'win32'\"]\n",
        encoding="utf-8",
    )
    selected = runner._python_requirements(tmp_path)
    assert "tzdata==2026.3" in selected
    assert "websockets>=15,<17" in selected
    if __import__("sys").platform != "win32":
        assert not any("windows-only" in item for item in selected)


@pytest.mark.parametrize(
    "manifest",
    [
        '[project]\ndynamic = ["dependencies"]\n',
        '[project]\ndependencies = ["package @ https://example.invalid/pkg.whl"]\n',
        '[project]\ndependencies = ["--index-url=https://example.invalid"]\n',
        '[project]\ndependencies = "tzdata==2026.3"\n',
    ],
)
def test_unverifiable_python_requirements_fail_closed(tmp_path: Path, manifest: str) -> None:
    (tmp_path / "pyproject.toml").write_text(manifest, encoding="utf-8")
    with pytest.raises(quality_profile.QualityProfileError) as caught:
        runner._python_requirements(tmp_path)
    assert caught.value.code == "TARGET_DEPENDENCY_MANIFEST_INVALID"


def test_installer_uses_only_wheel_requirements_and_trusted_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "pyproject.toml").write_text(
        '[project]\ndependencies = ["tzdata==2026.3"]\n', encoding="utf-8"
    )
    output = tmp_path / "output"
    trusted = quality_runner.trusted_directory(output)
    trusted.mkdir(parents=True)
    observed: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def fake_run(
        command: tuple[str, ...], *, cwd: Path, env: dict[str, str], **_: object
    ) -> subprocess.CompletedProcess[bytes]:
        observed.append((command, cwd, env))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_distribution_receipts", lambda *_args, **_kwargs: ("receipt",))
    exposed: list[tuple[Path, Path]] = []
    monkeypatch.setattr(runner, "_expose_python_dependencies", lambda *paths: exposed.append(paths))
    runner._install_python_dependencies(target, output)

    command, cwd, environment = observed[0]
    assert command[-1] == "tzdata==2026.3"
    assert "--only-binary=:all:" in command
    assert "--target" in command
    assert str(target) not in command
    assert cwd == trusted
    assert "PYTHONPATH" not in environment
    assert len(exposed) == 1


def test_target_dependencies_are_visible_to_isolated_and_child_python(tmp_path: Path) -> None:
    runtime = tmp_path / "fixed-runtime"
    executable = runtime / "bin" / "python"
    site_packages = runtime / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    runner._expose_python_dependencies(executable, site_packages)
    assert (site_packages / "supportability-target-dependencies.pth").read_bytes() == (
        b"/trusted/python-dependencies\n"
    )


def test_python_quality_command_keeps_its_isolated_vector(tmp_path: Path) -> None:
    repository = tmp_path / "target"
    repository.mkdir()
    output = tmp_path / "output"
    pytest_command = dict(quality_profile.command_templates("python"))["python.pytest.v1"]
    assert pytest_command[:2] == ("$PYTHON", "-I")
    dependencies = quality_runner.trusted_directory(output) / "python-dependencies"
    dependencies.mkdir(parents=True)
    plan = quality_runner.CommandPlan("python.pytest.v1", ("python", "-I"), (), "runtime-lines", ())
    invocation = quality_runner.sandbox_command(
        plan, repository=repository, output=output, collector=tmp_path
    )
    assert any("dst=/trusted,readonly" in item for item in invocation)
    assert not any("PYTHONPATH=" in item for item in invocation)
