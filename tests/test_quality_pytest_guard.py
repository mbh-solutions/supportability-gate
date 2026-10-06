"""Focused completion controls for the fixed parallel Python quality profile."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _candidate_module(filename: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "candidate_" + filename.removesuffix(".py"), Path(__file__).with_name(filename)
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


entry = _candidate_module("quality_pytest_entry.py")
guard = _candidate_module("quality_pytest_guard.py")


def _completed_guard() -> guard.CompletionGuard:
    observed = guard.CompletionGuard(SimpleNamespace())
    for identity in ("gw0", "gw1"):
        node = SimpleNamespace(
            gateway=SimpleNamespace(id=identity),
            workeroutput={
                "cov_worker_node_id": identity,
                "quality_pytest_guard": {
                    "isolated": True,
                    "trusted_helper": True,
                    "branch_coverage": True,
                },
            },
        )
        observed.pytest_xdist_node_collection_finished(node, ["a", "b"])
        observed.pytest_testnodedown(node, None)
    for nodeid in ("a", "b"):
        for phase in ("setup", "call", "teardown"):
            observed.pytest_runtest_logreport(
                SimpleNamespace(nodeid=nodeid, when=phase, outcome="passed")
            )
    return observed


def test_complete_workers_preserve_pass_skip_and_failure_outcomes() -> None:
    observed = _completed_guard()
    receipt = observed.receipt()
    assert receipt["guard_passed"]
    assert receipt["collected"] == receipt["completed"] == 2
    assert receipt["outcomes"] == {"passed": 2, "skipped": 0, "failed": 0}
    observed.reports["a"] = {"setup": "skipped", "teardown": "passed"}
    observed.reports["b"]["call"] = "failed"
    receipt = observed.receipt()
    assert receipt["guard_passed"]
    assert receipt["outcomes"] == {"passed": 0, "skipped": 1, "failed": 1}


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        ("crash", "WORKER_FAILED"),
        ("coverage", "MISSING_WORKER_BRANCH_COVERAGE"),
        ("collection", "WORKER_COLLECTION_MISMATCH"),
        ("completion", "MISSING_WORKER_COMPLETION"),
        ("missing_test", "INCOMPLETE_TEST_EXECUTION"),
        ("duplicate", "DUPLICATE_TEST_PHASE"),
        ("isolation", "UNTRUSTED_WORKER"),
    ],
)
def test_incomplete_worker_or_test_evidence_fails(failure: str, expected: str) -> None:
    observed = _completed_guard()
    mutations = {
        "crash": lambda: observed.workers["gw1"].update(completed=False),
        "coverage": lambda: observed.workers["gw1"].update(coverage_ack=False),
        "collection": lambda: observed.collections.update(gw1=("a",)),
        "completion": lambda: observed.workers.pop("gw1"),
        "missing_test": lambda: observed.reports.pop("b"),
        "duplicate": lambda: observed.pytest_runtest_logreport(
            SimpleNamespace(nodeid="a", when="call", outcome="passed")
        ),
        "isolation": lambda: observed.workers["gw1"].update(isolated=False),
    }
    mutations[failure]()
    receipt = observed.receipt()
    assert not receipt["guard_passed"]
    assert expected in receipt["errors"]


@pytest.mark.parametrize("fixture", [False, True])
def test_coverage_suppression_is_rejected(fixture: bool) -> None:
    item = SimpleNamespace(
        fixturenames=["no_cover"] if fixture else [],
        get_closest_marker=lambda _name: not fixture,
    )
    with pytest.raises(pytest.UsageError, match="COVERAGE_SUPPRESSION"):
        _completed_guard().pytest_collection_modifyitems([item])


def test_dynamic_coverage_suppression_fixture_is_rejected() -> None:
    with pytest.raises(pytest.UsageError, match="COVERAGE_SUPPRESSION"):
        _completed_guard().pytest_fixture_setup(SimpleNamespace(argname="no_cover"), None)


def test_runtime_coverage_suppression_marker_is_rejected() -> None:
    item = SimpleNamespace(fixturenames=[], get_closest_marker=lambda _name: object())
    with pytest.raises(pytest.UsageError, match="COVERAGE_SUPPRESSION"):
        _completed_guard().pytest_runtest_call(item)


def test_untrusted_entry_worker_or_shadowed_helper_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="UNTRUSTED_ENTRY"):
        entry.trusted_collector("/target/quality_pytest_entry.py", 1)
    with pytest.raises(pytest.UsageError, match="UNTRUSTED_WORKER"):
        guard.require_trusted_startup(0, "/collector/quality_pytest_guard.py")
    specification = importlib.machinery.ModuleSpec(
        "quality_pytest_guard", None, origin="/target/quality_pytest_guard.py"
    )
    monkeypatch.setattr(entry.importlib.util, "find_spec", lambda _name: specification)
    with pytest.raises(RuntimeError, match="SHADOWED_GUARD"):
        entry.load_guard(Path("/collector").resolve())


def test_missing_controller_cannot_report_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guard, "_CONTROLLER", None)
    assert guard.finish_controller(0) == 1
    assert guard.finish_controller(2) == 2


def test_worker_bootstrap_rejects_non_protocol_code() -> None:
    with pytest.raises(RuntimeError, match="INVALID_WORKER_BOOTSTRAP"):
        entry.worker_bootstrap(["--worker", "-u", "-c", "raise RuntimeError('target')"])
