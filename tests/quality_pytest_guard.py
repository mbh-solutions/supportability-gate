"""Fail closed unless both isolated workers finish the same complete collection."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from _pytest.subtests import SubtestReport

_WORKERS = ("gw0", "gw1")
_CONTROLLER: CompletionGuard | None = None


def require_trusted_startup(isolated: int, helper_file: str) -> None:
    expected = Path("/collector/quality_pytest_guard.py").resolve()
    if isolated != 1 or Path(helper_file).resolve() != expected:
        raise pytest.UsageError("QUALITY_PYTEST_UNTRUSTED_WORKER")


def _branch_coverage(config: Any) -> bool:
    plugin = config.pluginmanager.getplugin("_cov")
    if plugin is None or plugin.cov_controller is None:
        return False
    coverage = plugin.cov_controller.cov
    if coverage is None:
        return False
    data = coverage.get_data()
    return bool(
        coverage.config.branch
        and coverage.config.source == ["src"]
        and Path(coverage.config.data_file).resolve() == Path("/work/.coverage").resolve()
        and (data.has_arcs() or not data)
    )


def _phase_outcome(phases: dict[str, str], subtest_failed: bool = False) -> str:
    if subtest_failed or "failed" in phases.values():
        return "failed"
    return "skipped" if "skipped" in phases.values() else "passed"


def _complete_phases(phases: dict[str, str]) -> bool:
    expected = {"setup", "teardown"}
    if phases.get("setup") == "passed":
        expected.add("call")
    return set(phases) == expected and all(
        outcome in {"passed", "failed", "skipped"} for outcome in phases.values()
    )


def _reject_coverage_suppression(item: Any) -> None:
    if item.get_closest_marker("no_cover") or "no_cover" in item.fixturenames:
        raise pytest.UsageError("QUALITY_PYTEST_COVERAGE_SUPPRESSION")


class CompletionGuard:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.collections: dict[str, tuple[str, ...]] = {}
        self.workers: dict[str, dict[str, Any]] = {}
        self.reports: dict[str, dict[str, str]] = {}
        self.subtests: dict[str, Counter[str]] = {}
        self.errors: set[str] = set()

    @pytest.hookimpl(optionalhook=True)
    def pytest_xdist_node_collection_finished(self, node: Any, ids: list[str]) -> None:
        identity = node.gateway.id
        if identity in self.collections:
            self.errors.add("DUPLICATE_COLLECTION")
        self.collections[identity] = tuple(ids)

    @pytest.hookimpl(optionalhook=True, trylast=True)
    def pytest_testnodedown(self, node: Any, error: object) -> None:
        identity = node.gateway.id
        if identity in self.workers:
            self.errors.add("DUPLICATE_WORKER_COMPLETION")
        output = getattr(node, "workeroutput", {})
        guard = output.get("quality_pytest_guard", {})
        self.workers[identity] = {
            "id": identity,
            "completed": error is None,
            "coverage_ack": output.get("cov_worker_node_id") == identity,
            "isolated": guard.get("isolated") is True,
            "trusted_helper": guard.get("trusted_helper") is True,
            "branch_coverage": guard.get("branch_coverage") is True,
        }

    def pytest_collection_modifyitems(self, items: list[Any]) -> None:
        for item in items:
            _reject_coverage_suppression(item)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_call(self, item: Any) -> None:
        _reject_coverage_suppression(item)

    @pytest.hookimpl(tryfirst=True)
    def pytest_fixture_setup(self, fixturedef: pytest.FixtureDef[Any], request: Any) -> None:
        if fixturedef.argname == "no_cover":
            raise pytest.UsageError("QUALITY_PYTEST_COVERAGE_SUPPRESSION")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if hasattr(self.config, "workerinput"):
            return
        # Pinned pytest preserves this concrete type across xdist serialization.
        # Nested reports share the parent's call phase but cannot complete that phase.
        if isinstance(report, SubtestReport):
            self._record_subtest(report)
            return
        phases = self.reports.setdefault(report.nodeid, {})
        if report.when in phases:
            self.errors.add("DUPLICATE_TEST_PHASE")
        phases[report.when] = report.outcome

    def _record_subtest(self, report: SubtestReport) -> None:
        if report.when != "call" or report.outcome not in {"passed", "skipped", "failed"}:
            self.errors.add("INVALID_SUBTEST_REPORT")
            return
        self.subtests.setdefault(report.nodeid, Counter())[report.outcome] += 1

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if hasattr(self.config, "workerinput"):
            self.config.workeroutput["quality_pytest_guard"] = {
                "isolated": sys.flags.isolated == 1,
                "trusted_helper": Path(__file__).resolve()
                == Path("/collector/quality_pytest_guard.py").resolve(),
                "branch_coverage": _branch_coverage(self.config),
            }
        elif not self.receipt()["guard_passed"] and exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def _collection(self) -> tuple[str, ...]:
        if set(self.collections) != set(_WORKERS):
            self.errors.add("MISSING_WORKER_COLLECTION")
        collection = self.collections.get("gw0", ())
        if not collection or len(set(collection)) != len(collection):
            self.errors.add("EMPTY_OR_DUPLICATE_COLLECTION")
        if self.collections.get("gw1") != collection:
            self.errors.add("WORKER_COLLECTION_MISMATCH")
        return collection

    def _validate_workers(self) -> None:
        if set(self.workers) != set(_WORKERS):
            self.errors.add("MISSING_WORKER_COMPLETION")
        for worker in self.workers.values():
            if not worker["completed"]:
                self.errors.add("WORKER_FAILED")
            if not worker["coverage_ack"] or not worker["branch_coverage"]:
                self.errors.add("MISSING_WORKER_BRANCH_COVERAGE")
            if not worker["isolated"] or not worker["trusted_helper"]:
                self.errors.add("UNTRUSTED_WORKER")

    def receipt(self) -> dict[str, Any]:
        collection = self._collection()
        self._validate_workers()
        if set(self.reports) != set(collection) or not self.subtests.keys() <= self.reports.keys():
            self.errors.add("INCOMPLETE_TEST_EXECUTION")
        if not all(_complete_phases(phases) for phases in self.reports.values()):
            self.errors.add("INCOMPLETE_TEST_PHASES")
        outcomes = Counter(
            _phase_outcome(phases, self.subtests.get(nodeid, Counter())["failed"] > 0)
            for nodeid, phases in self.reports.items()
        )
        subtest_outcomes: Counter[str] = Counter()
        for nested in self.subtests.values():
            subtest_outcomes.update(nested)
        return {
            "schema_version": "quality-pytest-completion.v1",
            "guard_passed": not self.errors,
            "collected": len(collection),
            "completed": sum("teardown" in phases for phases in self.reports.values()),
            "collection_sha256": hashlib.sha256(json.dumps(collection).encode()).hexdigest(),
            "outcomes": {name: outcomes[name] for name in ("passed", "skipped", "failed")},
            "subtests": {name: subtest_outcomes[name] for name in ("passed", "skipped", "failed")},
            "workers": [self.workers[name] for name in _WORKERS if name in self.workers],
            "errors": sorted(self.errors),
        }


def pytest_configure(config: Any) -> None:
    global _CONTROLLER
    require_trusted_startup(sys.flags.isolated, __file__)
    guard = CompletionGuard(config)
    config.pluginmanager.register(guard, "quality-completion-guard")
    if not hasattr(config, "workerinput"):
        _CONTROLLER = guard


def finish_controller(exit_code: int) -> int:
    if _CONTROLLER is None:
        print("QUALITY_PYTEST_MISSING_COMPLETION_GUARD", file=sys.stderr)
        return exit_code or 1
    receipt = _CONTROLLER.receipt()
    raw = json.dumps(receipt, sort_keys=True).encode() + b"\n"
    if len(raw) >= 4096:
        raise RuntimeError("QUALITY_PYTEST_COMPLETION_RECEIPT_TOO_LARGE")
    Path("/work/pytest-completion.json").write_bytes(raw)
    if not receipt["guard_passed"]:
        print("QUALITY_PYTEST_INCOMPLETE_EXECUTION", file=sys.stderr)
        return exit_code or 1
    return exit_code or int(receipt["outcomes"]["failed"] > 0)
