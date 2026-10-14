"""Optimization preserves observations, native partition scope, and authenticated waits."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_quality_partitions import aggregate, candidate_module

from supportability_gate import baseline_evidence, quality_profile

waiter = candidate_module("hosted_artifact_wait")


def test_filtered_observer_preserves_nested_generator_exception_and_callback_counts(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    source = b"""def recurse(n):
    return 1 if n == 0 else n * recurse(n - 1)
def generate(n):
    for i in range(n):
        yield recurse(i)
def grade(n):
    import bridge
    try:
        raise ValueError("expected")
    except ValueError:
        return bridge.invoke(lambda: sum(generate(n)))
def never_called():
    return 99
"""
    (target / "model.py").write_bytes(source)
    (tmp_path / "bridge.py").write_text("def invoke(callback):\n    return callback()\n")
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import sys,os,json\n"
        "sys.path[:0]=[os.environ['SUPPORTABILITY_CHARACTERIZATION_TARGET'],"
        "os.path.dirname(__file__)]\nimport model\n"
        "print(json.dumps([model.grade(3), model.grade(5)]))\n"
    )
    observer = Path(__file__).with_name("baseline_observer.py")
    original = observer.read_text()
    start = original.index("    locations = {}")
    end = original.index("    output = io.StringIO()", start)
    legacy = """    def trace(frame, event, arg):
        filename = Path(frame.f_code.co_filename)
        if event == "line" and filename.is_absolute() and filename.is_relative_to(root):
            path = filename.relative_to(root).as_posix()
            if frame.f_code not in code_ids:
                code_ids[frame.f_code] = hashlib.sha256(
                    marshal.dumps(canonical_code(frame.f_code, root), 2)
                ).hexdigest()
            key = code_ids[frame.f_code]
            file_hits = hits.setdefault(path, {})
            file_hits[key] = file_hits.get(key, 0) + 1
        return trace

"""
    reference = tmp_path / "reference.py"
    reference.write_text(original[:start] + legacy + original[end:])
    results = []
    for program in (reference, observer):
        result = subprocess.run(
            [sys.executable, "-P", str(program), str(driver)],
            check=True,
            capture_output=True,
            timeout=30,
            env={**os.environ, "SUPPORTABILITY_CHARACTERIZATION_TARGET": str(target)},
        )
        results.append(json.loads(result.stdout))
    assert results[0] == results[1]
    assert set(results[1]["witness"]) == {"model.py"}
    assert not baseline_evidence.execution_verified({"model.py": source}, results[1]["witness"])


def test_partition_union_cannot_drop_checks_or_repeat_non_test_work():
    for language in ("python", "typescript", "mixed"):
        partitions = [aggregate.collector.partition_adapters(language, i) for i in range(4)]
        assert set().union(*map(set, partitions)) == set(
            quality_profile.required_adapters(language)
        )
        for adapter in quality_profile.required_adapters(language):
            repeated = adapter in {
                "python.pytest.v1",
                "typescript.test.v1",
                "typescript.tool-install.v1",
                "typescript.target-install.v1",
            }
            assert sum(adapter in part for part in partitions) == (4 if repeated else 1)


def test_lcov_union_requires_each_responsibility_and_never_credits_ignored_lines():
    first = "SF:src/app.js\nDA:2,1\nDA:5,0\nLF:3\nLH:2\nend_of_record\n"
    second = "SF:src/app.js\nDA:2,0\nDA:5,1\nLF:3\nLH:2\nend_of_record\n"
    targets = ("src/app.js::function:first:2-2", "src/app.js::function:second:5-5")
    assert (
        quality_profile.typescript_lcov_observation(
            first, ("src/app.js",), Path("/target"), targets
        )[0]
        == ()
    )
    merged = aggregate.merge_lcov([first, second])
    assert quality_profile.typescript_lcov_observation(
        merged, ("src/app.js",), Path("/target"), targets
    )[0] == ("src/app.js",)
    assert (
        quality_profile.typescript_lcov_observation(
            merged, ("src/app.js",), Path("/target"), ("src/app.js::function:missing:3-3",)
        )[0]
        == ()
    )
    with pytest.raises(quality_profile.QualityProfileError):
        aggregate.merge_lcov([first.replace("LH:2", "LH:3"), second])


def test_wait_does_not_accept_missing_running_or_duplicate_producers():
    job = {"name": "required", "status": "completed", "conclusion": "failure"}
    assert waiter.completed_jobs([], ("required",)) is None
    assert waiter.completed_jobs([{**job, "status": "in_progress"}], ("required",)) is None
    assert waiter.completed_jobs([job], ("required",)) == {"required": "failure"}
    with pytest.raises(ValueError, match="duplicate"):
        waiter.completed_jobs([job, job], ("required",))


@pytest.mark.parametrize("fault", ["head", "run", "repository", "expired", "digest", "duplicate"])
def test_wait_rejects_stale_or_ambiguous_artifact_identity(fault):
    artifact = {
        "name": "proof-2-1",
        "id": 4,
        "expired": False,
        "digest": "sha256:" + hashlib.sha256(b"proof").hexdigest(),
        "workflow_run": {"head_sha": "a" * 40, "id": 2, "repository_id": 3},
    }
    if fault in ("head", "run", "repository"):
        key = {"head": "head_sha", "run": "id", "repository": "repository_id"}[fault]
        artifact["workflow_run"][key] = "wrong"
    elif fault == "expired":
        artifact["expired"] = True
    elif fault == "digest":
        artifact["digest"] = "not-a-digest"
    items = [artifact, artifact] if fault == "duplicate" else [artifact]
    with pytest.raises(ValueError):
        waiter.artifact_values(items, "proof-2-1", "repos/example/repo", "a" * 40, "2", "3", False)


def test_wait_groups_overlap_only_independent_work():
    assert waiter.producer_names("capture") == ("Characterize Base", "Characterize Head")
    assert waiter.producer_names("quality") == tuple(
        f"Quality Profile (Partition {index})" for index in range(4)
    )
    assert waiter.producer_names("publish") == ("Deterministic Supportability Evidence",)
    with pytest.raises(ValueError):
        waiter.producer_names("untrusted")
    workflow = Path(__file__).parents[1] / ".github/workflows/organization-required.yml"
    content = workflow.read_text()
    quality = content.split("  quality-profile-shards:")[1].split("  deterministic-evidence:")[0]
    assert "id: capture" in quality
    assert "id: await_quality" not in quality
    text = content.split("  deterministic-evidence:")[1].split("  publish-results:")[0]
    assert "id: capture\n" not in text
    assert text.index("id: await_capture") < text.index("id: characterization")
    assert text.index("id: characterization") < text.index("id: await_quality")
    assert text.index("id: await_quality") < text.index("id: aggregate_quality")
    assert text.index("id: aggregate_quality") < text.index("id: evaluate")


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "index",
        "count",
        "command",
        "required_targets",
        "coverage_sha256",
        "schema_version",
        "missing",
        "bytes",
    ],
)
def test_node_join_authenticates_every_partition_before_coverage(tmp_path, monkeypatch, fault):
    targets = ("src/app.js::function:body:2-2",)
    monkeypatch.setattr(aggregate, "required_targets", lambda *_: targets)
    parts, profiles = [], []
    for index in range(4):
        part = tmp_path / str(index)
        shard = part / "typescript-shard"
        shard.mkdir(parents=True)
        raw = b"SF:src/app.js\nDA:2,1\nLF:1\nLH:1\nend_of_record\n"
        digest = hashlib.sha256(raw).hexdigest()
        command = quality_profile.GateResult(
            "typescript.test.v1",
            (),
            "lcov",
            (),
            (),
            True,
            0,
            "a" * 64,
            "b" * 64,
            digest,
            ("node", "--test"),
        )
        receipt = dict(
            schema_version="quality-node-partition.v1",
            index=index,
            count=4,
            required_targets=list(targets),
            command=list(command.executed_arguments),
            coverage_sha256=digest,
        )
        if index == 3 and fault in receipt:
            receipt[fault] = "wrong"
        if index == 3 and fault == "bytes":
            raw = raw.replace(b"DA:2,1", b"DA:2,0")
        (shard / "coverage.lcov").write_bytes(raw)
        if index != 3 or fault != "missing":
            (shard / "partition.json").write_text(json.dumps(receipt))
        parts.append(part)
        profiles.append(SimpleNamespace(commands=(command,), source_files=("src/app.js",)))
    if fault is not None:
        with pytest.raises(quality_profile.QualityProfileError):
            aggregate.combined_typescript(parts, profiles, tmp_path, tmp_path / "joined")
    else:
        result = aggregate.combined_typescript(parts, profiles, tmp_path, tmp_path / "joined")
        assert result.observed_paths == ("src/app.js",)
