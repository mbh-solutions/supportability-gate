from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import hosted_authorization_resume as hosted
import pytest
import test_standard_results as fixtures
import verify_authorization_pin
from test_authorization_wait import _waiting_evidence


def _context(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "GITHUB_REPOSITORY": fixtures.IDENTITY.repository,
        "GITHUB_REPOSITORY_ID": str(fixtures.IDENTITY.repository_id),
        "RESUME_WORKFLOW_SHA": fixtures.IDENTITY.workflow_sha,
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_RUN_ID": "900",
        "GITHUB_RUN_ATTEMPT": "1",
        "GH_TOKEN": "fixture-token",
    }.items():
        monkeypatch.setenv(key, value)


def _pull() -> dict[str, Any]:
    return {
        "number": 25,
        "state": "open",
        "head": {"sha": fixtures.IDENTITY.head_sha},
        "base": {"sha": fixtures.IDENTITY.base_sha},
    }


def _run() -> dict[str, Any]:
    return {
        "id": 456,
        "run_attempt": 1,
        "status": "completed",
        "repository": {"full_name": fixtures.IDENTITY.repository},
    }


def _record() -> dict[str, Any]:
    return {
        "capture_identity": vars(fixtures.IDENTITY),
        "pull_number": 25,
        "resume_workflow_sha": fixtures.IDENTITY.workflow_sha,
        "resume_run_id": 900,
        "resume_run_attempt": 1,
        "authorization_comments_sha256": hosted._authorization_snapshot(()),
    }


@pytest.mark.parametrize(
    "defect",
    [None, "head", "base", "closed", "new_run", "new_attempt", "running", "edited_authorization"],
)
def test_publication_requires_current_comparison_and_current_authorization(
    monkeypatch: pytest.MonkeyPatch, defect: str | None
) -> None:
    _context(monkeypatch)
    pull, run = _pull(), _run()
    comments: tuple[dict[str, Any], ...] = ()
    if defect in {"head", "base"}:
        pull[defect]["sha"] = "9" * 40
    elif defect == "closed":
        pull["state"] = "closed"
    elif defect == "new_run":
        run["id"] += 1
    elif defect == "new_attempt":
        run["run_attempt"] += 1
    elif defect == "running":
        run["status"] = "in_progress"
    elif defect == "edited_authorization":
        comments = (
            {
                "id": 1,
                "user": {"id": 1},
                "body": hosted.refactor_policy.AUTHORIZATION_PREFIX + "{}",
            },
        )
    monkeypatch.setattr(hosted, "_api", lambda *_args: pull)
    monkeypatch.setattr(hosted, "_latest_run", lambda *_args: run)
    monkeypatch.setattr(hosted.refactor_policy, "_github_comments", lambda *_args: comments)
    assert hosted._current(_record()) is (defect is None)


@pytest.mark.parametrize(
    "field,value", [("repository", "other/repo"), ("repository_id", 99), ("workflow_sha", "9" * 40)]
)
def test_continuation_cannot_move_evidence_to_another_trust_context(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    _context(monkeypatch)
    record = copy.deepcopy(_record())
    record["capture_identity"][field] = value
    with pytest.raises(ValueError, match="RESUME_IDENTITY_MISMATCH"):
        hosted._current(record)


def test_capture_bundle_is_validated_against_live_base_head_and_source_revision(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "evidence"
    _waiting_evidence(directory)
    payload = hosted._validate_capture(directory, 123, "f" * 40, _pull(), _run())
    assert payload["head_sha"] == fixtures.IDENTITY.head_sha
    with pytest.raises(ValueError):
        hosted._validate_capture(directory, 123, "9" * 40, _pull(), _run())


@pytest.mark.parametrize("defect", ["digest", "traversal", "duplicate"])
def test_artifact_download_rejects_substitution_and_unsafe_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        name = "../escape.json" if defect == "traversal" else "evidence.json"
        archive.writestr(name, b"{}")
        if defect == "duplicate":
            with pytest.warns(UserWarning):
                archive.writestr(name, b"{}")
    raw = stream.getvalue()
    digest = "0" * 64 if defect == "digest" else hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(
        hosted.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, raw, b""),
    )
    with pytest.raises(ValueError):
        hosted._download(
            "example/repository", {"id": 1, "digest": "sha256:" + digest}, tmp_path / "download"
        )
    assert not (tmp_path / "escape.json").exists()


def test_consumer_named_workflow_cannot_impersonate_organization_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pull = _pull()
    source = {
        **_run(),
        "workflow_id": 42,
        "workflow_url": "https://api.github.com/repos/example/repository/actions/required_workflows/42",
        "path": hosted.SOURCE_PATH,
        "pull_requests": [{"number": 25}],
    }
    impostor = {
        **source,
        "id": 999,
        "workflow_id": 999,
        "workflow_url": "https://api.github.com/repos/example/repository/actions/workflows/999",
    }
    monkeypatch.setattr(hosted, "_require_deployed_source", lambda *_args: None)
    monkeypatch.setattr(hosted, "_pages", lambda *_args: [impostor, source])
    assert hosted._latest_run("example/repository", pull) == source


def test_comment_before_capture_waits_for_completion_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context(monkeypatch)
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "issue": {"number": 25, "pull_request": {}},
                "comment": {"body": hosted.refactor_policy.AUTHORIZATION_PREFIX + "{}"},
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setattr(hosted, "_api", lambda *_args: _pull())
    monkeypatch.setattr(hosted, "_latest_run", lambda *_args: {**_run(), "status": "in_progress"})
    monkeypatch.setattr(hosted, "_download", lambda *_args: pytest.fail("capture is not complete"))
    hosted.prepare(tmp_path / "resume")
    assert not (tmp_path / "resume").exists()


def test_publisher_updates_original_checks_without_creating_new_successes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "id": 100 + index,
            "app": {"id": 15368},
            "name": name,
            "external_id": f"supportability:456:1:{index}",
        }
        for index, name in enumerate(hosted.standard_results.CHECK_CONTEXTS, 1)
    ]
    monkeypatch.setattr(hosted, "_pages", lambda *_args: rows)
    assert hosted._check_ids("example/repository", fixtures.IDENTITY) == {
        index: 100 + index for index in range(1, 9)
    }
    rows[0]["app"]["id"] = 999
    with pytest.raises(ValueError, match="ORIGINAL_CHECK"):
        hosted._check_ids("example/repository", fixtures.IDENTITY)


def test_changed_authorization_withholds_previous_success_before_artifact_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context(monkeypatch)
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "issue": {"number": 25, "pull_request": {}},
                "comment": {"body": hosted.refactor_policy.AUTHORIZATION_PREFIX + "{}"},
            }
        )
    )
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    updates = []

    def api(path: str, method: str = "GET", value: object = None) -> object:
        if method == "PATCH":
            updates.append((path, value))
            return {}
        return _pull()

    monkeypatch.setattr(hosted, "_api", api)
    monkeypatch.setattr(hosted, "_latest_run", lambda *_args: _run())
    monkeypatch.setattr(
        hosted,
        "_pages",
        lambda *_args: [
            {
                "id": 106,
                "external_id": "supportability:456:1:6",
                "name": hosted.standard_results.CHECK_CONTEXTS[5],
                "app": {"id": 15368},
                "conclusion": "success",
            }
        ],
    )
    monkeypatch.setattr(
        hosted,
        "_artifact",
        lambda *_args: pytest.fail("must not need artifact availability to withhold authorization"),
    )
    hosted.invalidate()
    assert len(updates) == 1
    assert updates[0][1]["status"] == "in_progress"
    assert "conclusion" not in updates[0][1]


def test_resume_preserves_capture_bytes_and_records_separate_decision_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context(monkeypatch)
    directory = tmp_path / "resume"
    directory.mkdir()
    _waiting_evidence(directory / "original")
    (directory / "resume.json").write_text(json.dumps(_record()))
    (directory / "event.json").write_text(
        json.dumps(
            {"repository": {"full_name": fixtures.IDENTITY.repository}, "pull_request": _pull()}
        )
    )
    authorized = fixtures._inputs()[2]
    monkeypatch.setattr(hosted.refactor_policy, "_github_comments", lambda *_args: ())

    def verify(
        target: Path,
        event: dict[str, Any],
        characterization: dict[str, Any],
        comments: object,
        predecessor: object,
    ) -> dict[str, Any]:
        assert event["pull_request"] == _pull()
        assert characterization == fixtures._inputs()[1]
        return authorized

    monkeypatch.setattr(hosted.refactor_policy, "verify_refactor", verify)
    hosted.evaluate(directory, tmp_path / "static-target")
    for name in (
        hosted.evidence.SOURCE_FILES[0],
        hosted.evidence.SOURCE_FILES[1],
        hosted.evidence.SOURCE_FILES[3],
    ):
        assert (directory / "evaluated" / name).read_bytes() == (
            directory / "original" / name
        ).read_bytes()
    decision = json.loads((directory / "evaluated" / "standard-results.json").read_bytes())
    record = json.loads((directory / "evaluated" / "authorization-resume.json").read_bytes())
    assert all(row["result"] == "PASS" for row in decision["entries"])
    assert decision["run_id"] == 456
    assert record["resume_run_id"] == 900
    assert (
        hosted.qualification_bundle.restore_bundle(
            directory / "evaluated" / "qualification-bundle.zip"
        ).decision
        == "PASS"
    )


def test_literal_supervisor_pin_rejects_runtime_and_inventory_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    workflow = tmp_path / ".github/workflows/authorization-resume.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("\n".join(["  ref: " + "a" * 40] * 3))
    source = tmp_path / "src/sample.py"
    source.parent.mkdir()
    source.write_bytes(b"original")
    for name in verify_authorization_pin.RUNTIME_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original")
    monkeypatch.setattr(
        verify_authorization_pin.git_changes, "run_git", lambda *_args: b"src/sample.py\n"
    )
    monkeypatch.setattr(
        verify_authorization_pin.git_changes,
        "read_regular_blob",
        lambda *_args: SimpleNamespace(content=b"original"),
    )
    verify_authorization_pin.verify(tmp_path)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="PIN_DRIFT"):
        verify_authorization_pin.verify(tmp_path)
    source.unlink()
    with pytest.raises(ValueError, match="INVENTORY_DRIFT"):
        verify_authorization_pin.verify(tmp_path)


@pytest.mark.parametrize(
    "mutation", ["unchanged", "changed", "deleted", "other-comment", "missing-artifact"]
)
def test_completion_does_not_reopen_current_approval_but_changes_cannot_skip(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    _context(monkeypatch)
    original = {
        "overall_result": "PASS",
        "authorization": {"exact": "grant"},
        "authorization_comment_id": 123,
    }

    def artifact(*args: object) -> dict[str, object]:
        if mutation == "missing-artifact":
            raise ValueError("expired")
        return {}

    def download(repository: str, artifact: object, directory: Path) -> None:
        (directory / "refactor-policy-result.json").write_text(json.dumps(original))

    def owner(*args: object) -> tuple[object, int]:
        if mutation == "deleted":
            raise hosted.refactor_policy.RefactorPolicyError("MISSING_OWNER_AUTHORIZATION")
        return object(), 124 if mutation == "other-comment" else 123

    monkeypatch.setattr(hosted, "_artifact", artifact)
    monkeypatch.setattr(hosted, "_download", download)
    monkeypatch.setattr(hosted, "_validate_capture", lambda *args: {})
    monkeypatch.setattr(hosted.refactor_policy, "_github_comments", lambda *args: ())
    monkeypatch.setattr(hosted.refactor_policy, "_owner_authorization", owner)
    monkeypatch.setattr(
        hosted.refactor_policy,
        "_authorization_payload",
        lambda value: {"exact": "changed" if mutation == "changed" else "grant"},
    )
    result = hosted._unchanged_completed_authorization(
        fixtures.IDENTITY.repository,
        fixtures.IDENTITY.repository_id,
        fixtures.IDENTITY.workflow_sha,
        _pull(),
        _run(),
    )
    assert result is (mutation == "unchanged")


def test_completion_preserves_green_check_and_skips_unnecessary_continuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context(monkeypatch)
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"workflow_run": {"event": "pull_request", "pull_requests": [{"number": 25}]}})
    )
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")

    def api(path: str, method: str = "GET", value: object = None) -> object:
        assert method == "GET", "unchanged approval must stay green"
        return _pull()

    monkeypatch.setattr(hosted, "_api", api)
    monkeypatch.setattr(hosted, "_latest_run", lambda *args: _run())
    monkeypatch.setattr(
        hosted,
        "_pages",
        lambda *args: [
            {
                "id": 106,
                "external_id": "supportability:456:1:6",
                "name": hosted.standard_results.CHECK_CONTEXTS[5],
                "app": {"id": 15368},
                "conclusion": "success",
            }
        ],
    )
    monkeypatch.setattr(hosted, "_unchanged_completed_authorization", lambda *args: True)
    hosted.invalidate()
    assert output.read_text() == "continue=false\n"


@pytest.mark.parametrize("defect", [None, "revision", "source", "repository-rule", "ambiguous"])
def test_required_workflow_identity_is_bound_to_effective_organization_rule(
    monkeypatch: pytest.MonkeyPatch, defect: str | None
) -> None:
    _context(monkeypatch)
    rule = {
        "type": "workflows",
        "ruleset_source_type": "Organization",
        "ruleset_source": "mbh-solutions",
        "parameters": {
            "workflows": [
                {
                    "repository_id": 1234,
                    "path": hosted.SOURCE_PATH,
                    "sha": fixtures.IDENTITY.workflow_sha,
                }
            ]
        },
    }
    if defect == "revision":
        rule["parameters"]["workflows"][0]["sha"] = "0" * 40
    if defect == "source":
        rule["parameters"]["workflows"][0]["repository_id"] = 5678
    if defect == "repository-rule":
        rule["ruleset_source_type"] = "Repository"

    def api(path: str) -> object:
        if path == f"repos/{hosted.SOURCE_REPOSITORY}":
            return {"id": 1234}
        assert path.endswith("/rules/branches/qualification%2Ftypescript")
        return [rule, rule] if defect == "ambiguous" else [rule]

    monkeypatch.setattr(hosted, "_api", api)
    pull = _pull()
    pull["base"]["ref"] = "qualification/typescript"
    if defect is None:
        hosted._require_deployed_source("example/repository", pull)
    else:
        with pytest.raises(ValueError, match="REQUIRED_WORKFLOW_SOURCE"):
            hosted._require_deployed_source("example/repository", pull)
