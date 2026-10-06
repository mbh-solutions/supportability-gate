"""Full test partitions cannot hide missing execution, failures, or worker coverage."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from supportability_gate.quality_profile import QualityProfileError


def candidate_module(name: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        f"candidate_partition_{name}", Path(__file__).with_name(f"{name}.py")
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


aggregate = candidate_module("hosted_quality_aggregate")
guard = candidate_module("quality_pytest_guard")


def proof(index: int = 0):
    full = [f"case-{number}" for number in range(6)]
    selected = full[index::4]
    completion = {
        "guard_passed": True,
        "errors": [],
        "collected": len(selected),
        "completed": len(selected),
        "collection_sha256": hashlib.sha256(json.dumps(selected).encode()).hexdigest(),
        "outcomes": {"passed": len(selected), "skipped": 0, "failed": 0},
        "subtests": {"passed": 2, "skipped": 0, "failed": 0},
        "workers": [
            dict(
                id=identity,
                completed=True,
                coverage_ack=True,
                isolated=True,
                trusted_helper=True,
                branch_coverage=True,
            )
            for identity in ("gw0", "gw1")
        ],
    }
    raw = json.dumps(completion).encode()
    partition = {
        "schema_version": "quality-pytest-partition.v1",
        "index": index,
        "count": 4,
        "full_collection": full,
        "selected_collection": selected,
        "completion_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return partition, completion, raw


def test_real_partition_layout_is_disjoint_and_covers_the_whole_collection():
    selected = []
    for index in range(4):
        partition, completion, raw = proof(index)
        assert aggregate.validate_partition(partition, completion, raw, index) == tuple(
            partition["full_collection"]
        )
        selected.extend(partition["selected_collection"])
    assert sorted(selected) == [f"case-{number}" for number in range(6)]


@pytest.mark.parametrize(
    "fault",
    [
        "index",
        "count",
        "missing",
        "duplicate",
        "misplaced",
        "digest",
        "incomplete",
        "parent_failure",
        "subtest_failure",
        "worker_missing",
        "coverage_missing",
        "nonisolated",
        "outcome_count",
    ],
)
def test_partition_join_blocks_actual_prerequisite_failures(fault):
    partition, completion, raw = proof()
    if fault in {"index", "count"}:
        partition[fault] += 1
    elif fault == "missing":
        partition["selected_collection"].pop()
    elif fault == "duplicate":
        partition["full_collection"].append("case-0")
    elif fault == "misplaced":
        partition["selected_collection"] = ["case-1", "case-5"]
    elif fault == "digest":
        partition["completion_sha256"] = "0" * 64
    elif fault == "incomplete":
        completion["completed"] -= 1
    elif fault == "parent_failure":
        completion["outcomes"]["failed"] = 1
    elif fault == "subtest_failure":
        completion["subtests"]["failed"] = 1
    elif fault == "worker_missing":
        completion["workers"].pop()
    elif fault == "coverage_missing":
        completion["workers"][0]["coverage_ack"] = False
    elif fault == "nonisolated":
        completion["workers"][0]["isolated"] = False
    else:
        completion["outcomes"]["passed"] += 1
    if fault not in {"index", "count", "missing", "duplicate", "misplaced", "digest"}:
        raw = json.dumps(completion).encode()
        partition["completion_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(QualityProfileError) as caught:
        aggregate.validate_partition(partition, completion, raw, 0)
    assert caught.value.code == "INVALID_QUALITY_PARTITION"


def test_guard_validates_the_worker_full_collection_before_accepting_a_partition(monkeypatch):
    monkeypatch.setattr(guard, "_SHARD_COUNT", 4)
    monkeypatch.setattr(guard, "_SHARD_INDEX", 0)
    observed = guard.CompletionGuard(SimpleNamespace())
    partition, completion, _raw = proof()
    for worker in completion["workers"]:
        identity = worker["id"]
        node = SimpleNamespace(
            gateway=SimpleNamespace(id=identity),
            workeroutput={
                "cov_worker_node_id": identity,
                "quality_pytest_guard": {
                    "isolated": True,
                    "trusted_helper": True,
                    "branch_coverage": True,
                    "full_collection": partition["full_collection"],
                },
            },
        )
        observed.pytest_xdist_node_collection_finished(node, partition["selected_collection"])
        observed.pytest_testnodedown(node, None)
    for nodeid in partition["selected_collection"]:
        for phase in ("setup", "call", "teardown"):
            observed.pytest_runtest_logreport(
                SimpleNamespace(nodeid=nodeid, when=phase, outcome="passed")
            )
    assert observed.receipt()["guard_passed"]
    changed = copy.deepcopy(observed.full_collections["gw1"])
    observed.full_collections["gw1"] = (*changed, "unexpected-case")
    assert not observed.receipt()["guard_passed"]
    assert "PARTITION_COLLECTION_MISMATCH" in observed.receipt()["errors"]


def test_an_empty_partition_is_allowed_only_when_its_full_collection_is_present(monkeypatch):
    monkeypatch.setattr(guard, "_SHARD_COUNT", 4)
    monkeypatch.setattr(guard, "_SHARD_INDEX", 3)
    observed = guard.CompletionGuard(SimpleNamespace())
    for identity in ("gw0", "gw1"):
        observed.collections[identity] = ()
        observed.full_collections[identity] = ("one", "two")
        observed.workers[identity] = dict(
            id=identity,
            completed=True,
            coverage_ack=True,
            isolated=True,
            trusted_helper=True,
            branch_coverage=True,
        )
    assert observed.receipt()["guard_passed"]
    observed.full_collections["gw0"] = observed.full_collections["gw1"] = ()
    assert not observed.receipt()["guard_passed"]


@pytest.mark.parametrize("fault", ["missing", "order", "after_failure", "policy_exit_prefix"])
def test_join_rejects_omissions_without_a_terminal_required_failure(fault):
    adapters = aggregate.quality_profile.required_adapters("python")
    commands = [SimpleNamespace(adapter=item, executed=True, exit_code=0) for item in adapters]
    if fault == "missing":
        commands.pop()
    elif fault == "order":
        commands[0], commands[1] = commands[1], commands[0]
    elif fault == "after_failure":
        commands[0].exit_code = 2
    else:
        commands = commands[:3]
        commands[-1].exit_code = 1
    with pytest.raises(QualityProfileError):
        aggregate.validate_command_prefix("python", tuple(commands))


def test_join_preserves_a_terminal_failure_and_the_existing_policy_exit_classification():
    adapters = aggregate.quality_profile.required_adapters("python")
    commands = [SimpleNamespace(adapter=item, executed=True, exit_code=0) for item in adapters]
    commands[2].exit_code = 1
    aggregate.validate_command_prefix("python", tuple(commands))
    profile = SimpleNamespace(language="python", commands=tuple(commands), asset_receipts=())
    assert not aggregate.has_failure([profile])
    commands[4].exit_code = 1
    profile.commands = tuple(commands[:5])
    aggregate.validate_command_prefix("python", profile.commands)
    assert aggregate.has_failure([profile])
