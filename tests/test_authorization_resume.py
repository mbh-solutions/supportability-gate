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
        "path": hosted.SOURCE_PATH,
        "pull_requests": [{"number": 25}],
    }
    impostor = {**source, "id": 999, "workflow_id": 999}
    monkeypatch.setattr(hosted, "_api", lambda *_args: {"id": 42})
    monkeypatch.setattr(hosted, "_pages", lambda *_args: [impostor, source])
    assert hosted._latest_run("example/repository", pull) == source


def test_comment_before_capture_waits_for_completion_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _context(monkeypatch)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"issue": {"number": 25, "pull_request": {}}}))
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
