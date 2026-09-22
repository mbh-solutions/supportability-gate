from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
import test_standard_results as standard_fixtures

from supportability_gate import qualification_bundle


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _evidence(directory: Path) -> dict[str, Any]:
    inputs = standard_fixtures._inputs()
    payload = standard_fixtures._compose(inputs)
    directory.mkdir()
    _write_json(directory / "complexity-result.json", inputs[0])
    _write_json(directory / "characterization-result.json", inputs[1])
    _write_json(directory / "refactor-policy-result.json", inputs[2])
    _write_json(directory / "quality-provenance.json", inputs[3])
    _write_json(directory / "standard-results.json", payload)
    (directory / "complexity-result.md").write_text("# Evidence\n", encoding="utf-8")
    return payload


def _context(payload: dict[str, Any]) -> dict[str, object]:
    return {
        "schema_version": qualification_bundle.CONTEXT_SCHEMA,
        "case_id": "S08-PYTHON-PASS",
        "profile": "python",
        "classification": "PASS",
        "source_test_references": [
            "tests/test_qualification_bundle.py::test_build_and_offline_restore_are_deterministic"
        ],
        "runtime": {
            "python": "3.12.11",
            "node": "24.8.0",
            "runner_image": "ubuntu-24.04",
            "isolation_image": "ubuntu@sha256:" + "1" * 64,
            "lock_sha256": "2" * 64,
        },
        "delivery": {
            "pull_request": 209,
            "merge_sha": "3" * 40,
            "workflow_run": payload["run_id"],
            "artifact_id": 900,
            "artifact_digest": "4" * 64,
            "artifact_expires_at": "2026-12-21T00:00:00Z",
            "deployed_workflow_sha": "5" * 40,
        },
        "rulesets": [
            {
                "scope": "organization:Supportability Gate",
                "retrieved_at": "2026-09-22T00:00:00Z",
                "payload": {"id": 21664395, "enforcement": "active", "bypass_actors": []},
            }
        ],
        "limitations": ["Artifact retention is bounded by the recorded expiration time."],
        "restore_command": qualification_bundle.RESTORE_COMMAND,
    }


def _rewrite_member(source: Path, output: Path, name: str, content: bytes) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        members = {item: archive.read(item) for item in archive.namelist()}
    members[name] = content
    with zipfile.ZipFile(output, "w") as archive:
        for member, value in sorted(members.items()):
            archive.writestr(member, value)


def test_build_and_offline_restore_are_deterministic(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    payload = _evidence(evidence)
    context = tmp_path / "context.json"
    _write_json(context, _context(payload))
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_digest = qualification_bundle.build_bundle(evidence, first, context)
    second_digest = qualification_bundle.build_bundle(evidence, second, context)
    restored = qualification_bundle.restore_bundle(first)

    assert first.read_bytes() == second.read_bytes()
    assert first_digest == second_digest == hashlib.sha256(first.read_bytes()).hexdigest()
    assert restored.repository == standard_fixtures.IDENTITY.repository
    assert restored.head_sha == standard_fixtures.IDENTITY.head_sha
    assert restored.decision == "PASS"
    assert restored.profile == "python"
    assert restored.case_id == "S08-PYTHON-PASS"


def test_build_without_release_context_restores_workflow_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    _evidence(evidence)
    bundle = evidence / "qualification-bundle.zip"

    qualification_bundle.build_bundle(evidence, bundle)
    restored = qualification_bundle.restore_bundle(bundle)

    assert restored.case_id is None
    assert restored.profile == "python"


def test_technical_failure_bundle_restores_without_missing_raw_source(tmp_path: Path) -> None:
    producer = tmp_path / "producer"
    producer.mkdir()
    arguments, paths, output = standard_fixtures._producer_arguments(producer)
    paths["complexity"].unlink()
    arguments[arguments.index("--complexity-outcome") + 1] = "failure"
    assert standard_fixtures.standard_results_producer.main(arguments) == 0
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "standard-results.json").write_bytes(output.read_bytes())
    diagnostics = evidence / "diagnostics"
    diagnostics.mkdir()
    (diagnostics / "complexity--stage.log").write_bytes(b"MISSING_COMPLEXITY_RESULT\n")
    bundle = tmp_path / "technical.zip"

    qualification_bundle.build_bundle(evidence, bundle)
    restored = qualification_bundle.restore_bundle(bundle)

    assert restored.decision == "TECHNICAL_FAILURE"
    assert restored.profile is None
    with zipfile.ZipFile(bundle, "r") as archive:
        assert archive.read("evidence/diagnostics/complexity--stage.log") == (
            b"MISSING_COMPLEXITY_RESULT\n"
        )


def test_restore_rejects_changed_evidence_bytes(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    _evidence(evidence)
    bundle = tmp_path / "bundle.zip"
    qualification_bundle.build_bundle(evidence, bundle)
    tampered = tmp_path / "tampered.zip"
    _rewrite_member(bundle, tampered, "evidence/complexity-result.md", b"changed\n")

    with pytest.raises(
        qualification_bundle.QualificationBundleError, match="BUNDLE_FILE_DIGEST_MISMATCH"
    ):
        qualification_bundle.restore_bundle(tampered)


def test_restore_rejects_duplicate_or_traversal_members(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr("manifest.json", b"{}")
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../manifest.json", b"{}")

    for bundle in (duplicate, traversal):
        with pytest.raises(
            qualification_bundle.QualificationBundleError, match="UNSAFE_BUNDLE_MEMBER"
        ):
            qualification_bundle.restore_bundle(bundle)


def test_context_must_match_canonical_profile_and_decision(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    payload = _evidence(evidence)
    context = _context(payload)
    context["profile"] = "mixed"
    context_path = tmp_path / "context.json"
    _write_json(context_path, context)

    with pytest.raises(
        qualification_bundle.QualificationBundleError,
        match="QUALIFICATION_CONTEXT_EVIDENCE_MISMATCH",
    ):
        qualification_bundle.build_bundle(evidence, tmp_path / "bundle.zip", context_path)


def test_context_delivery_identity_cannot_be_reassigned(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    payload = _evidence(evidence)
    context = _context(payload)
    delivery = copy.deepcopy(context["delivery"])
    assert isinstance(delivery, dict)
    delivery["workflow_run"] = payload["run_id"] + 1
    context["delivery"] = delivery
    context_path = tmp_path / "context.json"
    _write_json(context_path, context)

    with pytest.raises(
        qualification_bundle.QualificationBundleError,
        match="QUALIFICATION_DELIVERY_IDENTITY_MISMATCH",
    ):
        qualification_bundle.build_bundle(evidence, tmp_path / "bundle.zip", context_path)


def test_duplicate_json_key_fails_closed(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    _evidence(evidence)
    (evidence / "standard-results.json").write_text(
        '{"schema_version":"standard-results.v3","schema_version":"standard-results.v3"}',
        encoding="utf-8",
    )

    with pytest.raises(qualification_bundle.QualificationBundleError, match="DUPLICATE_JSON_KEY"):
        qualification_bundle.build_bundle(evidence, tmp_path / "bundle.zip")


def test_cli_restores_valid_bundle_and_fails_closed_for_tamper(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evidence = tmp_path / "evidence"
    _evidence(evidence)
    bundle = tmp_path / "bundle.zip"
    assert (
        qualification_bundle.main(
            [
                "build",
                "--evidence-directory",
                str(evidence),
                "--output",
                str(bundle),
            ]
        )
        == 0
    )
    assert qualification_bundle.main(["restore", "--bundle", str(bundle)]) == 0
    assert '"decision": "PASS"' in capsys.readouterr().out
    bundle.write_bytes(b"not a zip")
    assert qualification_bundle.main(["restore", "--bundle", str(bundle)]) == 2
    assert "MALFORMED_QUALIFICATION_BUNDLE" in capsys.readouterr().err


def test_required_workflow_retains_and_restores_bundle_in_fixed_isolation() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/organization-required.yml"
    ).read_text(encoding="utf-8")
    image = "ubuntu@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4"

    assert "Build compact qualification bundle" in workflow
    assert "Restore qualification bundle without network access" in workflow
    assert "--network none" in workflow
    assert "--read-only" in workflow
    assert "--cap-drop ALL" in workflow
    assert image in workflow
    assert '"$QUALIFICATION_BUNDLE_OUTCOME"' in workflow
    assert '"$RESTORE_BUNDLE_OUTCOME"' in workflow
