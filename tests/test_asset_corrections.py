from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_baseline_evidence import _capture_envelope, _capture_row, _manifest
from test_characterization import PYTHON_CONTRACT, _commit, _git, _verify, _write, _write_artifacts
from test_refactor_policy import _derived_targets, _event

from supportability_gate import baseline_evidence as baseline
from supportability_gate import (
    characterization,
    contract,
    git_changes,
    refactor_policy,
    standard_results,
)

ASSET = "src/catalog.json"
TARGET = f"{ASSET}::asset:{ASSET}:whole-file"


def _content_correction(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "remote", "add", "origin", "https://github.com/example/fixture.git")
    text = PYTHON_CONTRACT.replace('high_risk_paths = ["src/sample.py"]', "high_risk_paths = []")
    policy = contract.parse_contract(text.encode())
    _write(repository / ".supportability.toml", text)
    before, after = b'{"answer":1}', b'{"answer":2}'
    _write(repository / ASSET, before.decode())
    manifest = _manifest((ASSET,))
    driver_path = "tests/characterization/sample.characterization.py"
    driver = (
        "print("
        + repr(json.dumps({"schema_version": "1.0", "scenario": "sample", "behavior": []}))
        + ")\n"
    )
    _write(repository / driver_path, driver)
    golden_path = "tests/characterization/sample.golden.json"
    _write(repository / golden_path, json.dumps(baseline.behavior({ASSET: before}, [])))
    _write(repository / characterization.MANIFEST_PATH, json.dumps(manifest))
    base = _commit(repository, "existing content with preserved baseline")
    # Freeze the corrected expected bytes before changing production content.
    files = {
        driver_path: ("expected_case", driver),
        golden_path: ("golden", json.dumps(baseline.behavior({ASSET: after}, []))),
        "tests/characterization/sample.review.json": (
            "review",
            '{"evidence_class":"author_declaration"}',
        ),
        "tests/characterization/sample.source.json": (
            "source_receipt",
            json.dumps({ASSET: baseline.digest(after)}),
        ),
    }
    for name, (_, content) in files.items():
        _write(repository / name, content)
    manifest["corrections"] = [
        {
            "id": "content-fix",
            "scenarios": ["sample"],
            "obligations": ["sample-cases"],
            "targets": [TARGET],
            "oracle_files": [
                {"path": name, "kind": kind, "sha256": baseline.digest(content.encode())}
                for name, (kind, content) in sorted(files.items())
            ],
        }
    ]
    _write(repository / characterization.MANIFEST_PATH, json.dumps(manifest))
    oracle = _commit(repository, "freeze corrected content oracle")
    _write(repository / ASSET, after.decode())
    head = _commit(repository, "correct content")
    base_checkout = tmp_path / "base"
    _git(repository, "worktree", "add", "--detach", str(base_checkout), base)
    return repository, base_checkout, base, oracle, head, policy


def test_content_only_correction_requires_complete_evidence_and_exact_owner(tmp_path, monkeypatch):
    repository, base_checkout, base, oracle, head, policy = _content_correction(tmp_path)
    manifest = characterization._manifest(repository, head, [])
    rows = [
        _capture_row(target, repository, head, policy, monkeypatch)
        for target in (base_checkout, repository)
    ]
    assert all(row["error"] is None and row["deterministic"] for row in rows)
    artifacts = [
        _capture_envelope(row, manifest, policy, base, head, side)
        for row, side in zip(rows, ("base", "head"), strict=True)
    ]
    result = _verify(repository, base, head, *_write_artifacts(tmp_path, *artifacts))
    correction = result["correction"]
    assert not correction["verification_blocks"]
    assert correction["targets"] == [TARGET]
    assert result["coverage"]["required_obligations"] == []
    assert result["coverage"]["covered_paths"] == [ASSET]
    assert standard_results._s02_refactor_target_paths([TARGET]) == [ASSET]
    assert (
        characterization.validate_result(
            result,
            repository=result["repository"],
            base_sha=base,
            head_sha=head,
            workflow_sha=result["workflow_sha"],
            required_paths=(ASSET,),
            required_targets=(TARGET,),
        )
        == result["policy_blocks"]
    )
    event = _event(base, head, None)
    denied = refactor_policy.verify_refactor(repository, event, result, ())
    assert "MISSING_OWNER_AUTHORIZATION" in denied["policy_blocks"]
    assert "UNAUTHORIZED_CORRECTION" in denied["policy_blocks"]
    scope = sorted(
        c.new_path or c.old_path for c in git_changes.changed_paths(repository, base, head, [])
    )
    authorization = refactor_policy.Authorization(
        "example/fixture",
        base,
        head,
        True,
        (),
        tuple(scope),
        (TARGET,),
        refactor_policy.Sequence(1, base, "content-fix"),
        (),
        "5.0",
        "content-fix",
        oracle,
        correction["oracle_manifest_blob_sha"],
        correction["oracle_manifest_sha256"],
        correction["behavior_delta_sha256"],
    )
    payload = refactor_policy._authorization_payload(authorization)
    body = refactor_policy.AUTHORIZATION_PREFIX + json.dumps(payload, separators=(",", ":"))
    comment = {"id": 123, "user": {"id": refactor_policy.TRUSTED_OWNER_ID}, "body": body}
    accepted = refactor_policy.verify_refactor(repository, event, result, (comment,))
    assert accepted["policy_blocks"] == []
    assert accepted["result_classification"] == "PASS_AUTHORIZED_BEHAVIOR_CORRECTION"
    identity = standard_results.RunIdentity("example/fixture", 123, base, head, "f" * 40, 456, 1)
    assert standard_results._s02_refactor(accepted, result, identity, None) == []
    assert (
        standard_results._s02_characterization(result, identity, (ASSET,), (TARGET,), None)
        == result["policy_blocks"]
    )
    for field, value in (
        ("head_sha", "f" * 40),
        ("targets", ["src/other.json::asset:src/other.json:whole-file"]),
        ("behavior_delta_sha256", "f" * 64),
        ("scope", [ASSET]),
    ):
        wrong = dict(payload, **{field: value})
        changed = dict(comment, body=refactor_policy.AUTHORIZATION_PREFIX + json.dumps(wrong))
        assert (
            refactor_policy.verify_refactor(repository, event, result, (changed,))["overall_result"]
            == "BLOCK"
        )
    foreign = dict(comment, user={"id": 1})
    assert (
        refactor_policy.verify_refactor(repository, event, result, (foreign,))["overall_result"]
        == "BLOCK"
    )
    for value in ({"cases": [], "assets": {}}, {"cases": [], "assets": {ASSET: "0" * 64}}):
        tampered = copy.deepcopy(artifacts)
        tampered[1]["scenarios"][0]["behavior"] = value
        invalid = _verify(repository, base, head, *_write_artifacts(tmp_path, *tampered))
        assert invalid["overall_result"] == "BLOCK"
        assert (
            refactor_policy.verify_refactor(repository, event, invalid, (comment,))[
                "overall_result"
            ]
            == "BLOCK"
        )
    # Merely editing the content again cannot reuse the frozen golden or approval.
    _write(repository / ASSET, '{"answer":3}')
    changed_head = _commit(repository, "unapproved content drift")
    drift = _capture_row(repository, repository, changed_head, policy, monkeypatch)
    assert drift["behavior"] != rows[1]["behavior"]


@pytest.mark.parametrize("operation", ["modify", "add", "delete", "rename", "mixed"])
def test_asset_targets_preserve_each_changed_path(tmp_path, operation):
    repository, _, base, _, _, _ = _content_correction(tmp_path)
    _git(repository, "reset", "--hard", base)
    paths = [ASSET]
    if operation == "add":
        paths = ["src/second.json"]
        _write(repository / paths[0], "{}")
    elif operation == "delete":
        (repository / ASSET).unlink()
    elif operation == "rename":
        paths.append("src/renamed.json")
        _git(repository, "mv", ASSET, paths[1])
    else:
        _write(repository / ASSET, '{"answer":2}')
    if operation == "mixed":
        _write(repository / "src/calculate.py", "def calculate(value):\n    return value + 1\n")
    head = _commit(repository, "asset operation")
    targets, unbounded = _derived_targets(repository, base, head)
    expected = [f"{path}::asset:{path}:whole-file" for path in paths]
    if operation == "mixed":
        expected.append("src/calculate.py::function:calculate:1-2")
    assert targets == tuple(sorted(expected))
    assert unbounded == ()


@pytest.mark.parametrize(
    "target",
    [
        "src/model.py::asset:src/model.py:whole-file",
        "src/data.json::asset:src/other.json:whole-file",
        "src/data.json::asset:src/data.json:1-2",
        "../data.json::asset:../data.json:whole-file",
    ],
)
def test_serialized_asset_target_rejects_malformed_identity(target):
    with pytest.raises(standard_results.StandardResultsError):
        standard_results._s02_refactor_target_paths([target])


def test_nonregular_asset_remains_unbounded(tmp_path):
    repository, _, base, _, _, _ = _content_correction(tmp_path)
    _git(repository, "reset", "--hard", base)
    blob = _git(repository, "hash-object", "-w", ASSET)
    _git(repository, "update-index", "--cacheinfo", f"120000,{blob},{ASSET}")
    _git(repository, "commit", "-m", "nonregular asset")
    head = _git(repository, "rev-parse", "HEAD")
    targets, unbounded = _derived_targets(repository, base, head)
    assert targets == (TARGET,)
    assert unbounded == (ASSET,)
