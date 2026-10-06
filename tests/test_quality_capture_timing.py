"""Operational timing remains separate from authoritative quality outcomes."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from supportability_gate import quality_profile

_SPEC = importlib.util.spec_from_file_location(
    "timed_hosted_quality_profile", Path(__file__).with_name("hosted_quality_profile.py")
)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def test_command_timing_preserves_result_and_retains_separate_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = iter((10.0, 12.125))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    timing = runner._QualityTiming(tmp_path)
    result = quality_profile.GateResult(
        "python.pytest.v1", (), "coverage", (), (), True, 0, "a" * 64, "b" * 64, "c" * 64, ()
    )
    original = asdict(result)

    observed = runner._timed_command(timing, result.adapter, lambda: result)

    assert observed is result
    assert asdict(observed) == original
    payload = json.loads((tmp_path / "quality-timings.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "quality-capture-timings.v1"
    assert len(payload["records"]) == 1
    record = payload["records"][0]
    assert record == {
        "kind": "command",
        "name": "python.pytest.v1",
        "status": "completed",
        "started_at": record["started_at"],
        "wall_seconds": 2.125,
        "executed": True,
        "exit_code": 0,
    }
    captured = capsys.readouterr()
    assert captured.out == ""
    lines = captured.err.splitlines()
    assert len(lines) == 2
    assert "QUALITY_TIMING START command python.pytest.v1" in lines[0]
    assert "QUALITY_TIMING END command python.pytest.v1 completed 2.125s" in lines[1]
    assert "a" * 64 not in "\n".join(lines)
    assert list(tmp_path.iterdir()) == [tmp_path / "quality-timings.json"]


def test_setup_timing_retains_failure_without_exposing_error_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = iter((20.0, 23.5))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    timing = runner._QualityTiming(tmp_path)
    error = RuntimeError("token=private-value /private/target/path")

    with pytest.raises(RuntimeError) as raised:
        with timing.measure("setup", "prepare-container"):
            raise error

    assert raised.value is error
    text = (tmp_path / "quality-timings.json").read_text(encoding="utf-8")
    record = json.loads(text)["records"][0]
    assert record["status"] == "failed"
    assert record["wall_seconds"] == 3.5
    assert record["kind"] == "setup"
    assert record["name"] == "prepare-container"
    captured = capsys.readouterr()
    assert captured.out == ""
    output = captured.err
    assert "QUALITY_TIMING END setup prepare-container failed 3.500s" in output
    assert "private-value" not in text + output
    assert "/private/target/path" not in text + output
