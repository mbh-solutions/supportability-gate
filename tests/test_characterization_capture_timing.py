"""Capture timings retain useful failures without changing deterministic evidence."""

from __future__ import annotations

import importlib.util
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from supportability_gate import characterization

_SPEC = importlib.util.spec_from_file_location(
    "timed_hosted_characterization", Path(__file__).with_name("hosted_characterization.py")
)
assert _SPEC is not None and _SPEC.loader is not None
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def _capture_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, str], list[str]]:
    head = "a" * 40
    scenarios = tuple(SimpleNamespace(id=name) for name in ("first", "second"))
    manifest = SimpleNamespace(scenarios=scenarios, schema_version="5.0")
    monkeypatch.setattr(runner, "_require_hosted_runner", lambda: None)
    monkeypatch.setattr(runner.git_changes, "validate_repository", lambda path, records: path)
    monkeypatch.setattr(runner.git_changes, "run_git", lambda *args: head.encode())
    monkeypatch.setattr(
        runner.git_changes, "read_regular_blob", lambda *args: SimpleNamespace(content=b"")
    )
    monkeypatch.setattr(
        runner.contract, "parse_contract", lambda content: SimpleNamespace(language="python")
    )
    monkeypatch.setattr(runner.characterization, "_manifest", lambda *args: manifest)
    monkeypatch.setattr(
        runner.characterization, "_manifest_payload", lambda value: {"fixture": True}
    )
    monkeypatch.setattr(runner, "_prepare_container", lambda: "sha256:" + "b" * 64)
    monkeypatch.setattr(runner, "_install_python_dependencies", lambda *args: None)
    monkeypatch.setattr(runner, "_runtime_probe", lambda *args: "fixed-runtime")
    monkeypatch.setattr(runner, "_dependency_receipts", lambda path: ())
    calls: list[str] = []

    def observe(*args: object) -> dict[str, object]:
        scenario = args[3]
        name = scenario.id
        calls.append(name)
        return {
            "id": name,
            "behavior_sha256": "c" * 64,
            "deterministic": name != "second",
            "exit_code": 0,
            "error": None,
        }

    monkeypatch.setattr(runner, "_scenario_capture", observe)
    return {
        "base_sha": "d" * 40,
        "head_sha": head,
        "side": "head",
        "repository": "example/fixture",
        "repository_id": "1",
        "workflow_sha": "e" * 40,
        "run_id": "2",
        "run_attempt": "1",
        "job": "characterize-head",
    }, calls


def test_capture_timings_preserve_authoritative_bytes_and_serial_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments, calls = _capture_fixture(monkeypatch)
    clock = itertools.count(10.0, 1.25)
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    first = runner.capture_evidence(tmp_path, tmp_path, **arguments)
    clock = itertools.count(100.0, 2.5)
    second = runner.capture_evidence(tmp_path, tmp_path, diagnostics=tmp_path, **arguments)

    assert characterization._canonical(first) == characterization._canonical(second)
    assert calls == ["first", "second", "first", "second"]
    payload = json.loads((tmp_path / "characterization-timings.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "characterization-capture-timings.v1"
    records = payload["records"]
    assert [row["name"] for row in records] == [
        "checkout-and-container",
        "dependencies-and-runtimes",
        "first",
        "second",
    ]
    assert [row["kind"] for row in records] == [
        "preparation",
        "preparation",
        "scenario",
        "scenario",
    ]
    assert [row["status"] for row in records] == ["completed", "completed", "completed", "failed"]
    assert all(row["wall_seconds"] == 2.5 for row in records)
    assert second[0]["scenarios"][1]["deterministic"] is False
    assert list(tmp_path.iterdir()) == [tmp_path / "characterization-timings.json"]
    streams = capsys.readouterr()
    assert streams.out == ""
    output = streams.err
    assert "CHARACTERIZATION_TIMING START scenario first" in output
    assert "CHARACTERIZATION_TIMING END scenario second failed 2.500s" in output


@pytest.mark.parametrize("failure", ["preparation", "scenario"])
def test_capture_timings_retain_failures_without_exception_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    arguments, _ = _capture_fixture(monkeypatch)
    error = RuntimeError("token=private-value /private/target/path")

    def fail(*args: object) -> None:
        raise error

    name = "_prepare_container" if failure == "preparation" else "_scenario_capture"
    monkeypatch.setattr(runner, name, fail)
    with pytest.raises(RuntimeError) as raised:
        runner.capture_evidence(tmp_path, tmp_path, diagnostics=tmp_path, **arguments)

    assert raised.value is error
    text = (tmp_path / "characterization-timings.json").read_text(encoding="utf-8")
    records = json.loads(text)["records"]
    assert records[-1]["kind"] == failure
    assert records[-1]["status"] == "failed"
    assert len(records) == (1 if failure == "preparation" else 3)
    streams = capsys.readouterr()
    assert streams.out == ""
    output = streams.err
    assert "private-value" not in text + output
    assert "/private/target/path" not in text + output


def test_capture_timing_is_bounded_and_unwritable_diagnostics_do_not_change_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "_MAX_TIMING_RECORDS", 2)
    blocked = tmp_path / "file-not-directory"
    blocked.write_text("occupied", encoding="utf-8")
    timing = runner._CharacterizationTiming(blocked)
    completed = []
    for name in ("first", "second", "third"):
        with timing.measure("scenario", name):
            completed.append(name)

    assert completed == ["first", "second", "third"]
    assert len(timing.records) == 2
    assert blocked.read_text(encoding="utf-8") == "occupied"
