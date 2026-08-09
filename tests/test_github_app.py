from __future__ import annotations

import base64
import hashlib
import io
import json
import urllib.error
import zipfile
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import supportability_gate.github_app as github_app_module
from supportability_gate.function_changes import ResponsibilitySpan, responsibility_spans
from supportability_gate.github_app import (
    CHECK_NAME,
    MAX_CHECK_SUMMARY_BYTES,
    REVIEWED_SUFFIXES,
    GitHubApp,
    app_jwt,
)
from supportability_gate.semantic_contract import EvidencePacket, SemanticReviewError

AUTHORITY = GitHubApp._authority

CONTRACT = b"""schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = []

[[gates]]
adapter = "python.c901-touched.v1"
paths = ["src"]

[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
"""


@pytest.fixture(autouse=True)
def _existing_packet_tests_use_empty_review_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        GitHubApp,
        "_review_state",
        lambda *args: {
            "inline_comments": [],
            "reviews": [],
            "schema_version": "review-state.v1",
            "threads": [],
            "top_level_comments": [],
        },
    )
    monkeypatch.setattr(
        GitHubApp,
        "_authority",
        lambda self, repository, pull_number, pull, token: {
            "closing_issues": [],
            "pull_request": {
                "body": pull.get("body", ""),
                "number": pull_number,
                "repository": repository,
                "title": pull.get("title", "test pull"),
                "updated_at": pull.get("updated_at", "2026-08-09T00:00:00Z"),
                "url": pull.get("html_url", f"https://github.com/{repository}/pull/{pull_number}"),
            },
        },
    )


class _Reply:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class _RawReply(_Reply):
    def read(self) -> bytes:
        return str(self.payload).encode()


class _BytesReply(_Reply):
    def read(self) -> bytes:
        return self.payload  # type: ignore[return-value]


def _private_key() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _decode(segment: str) -> object:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def _blob_sha(content: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(content)}\0".encode() + content, usedforsecurity=False
    ).hexdigest()


def _blob_payload(content: bytes) -> dict[str, str]:
    return {
        "content": base64.b64encode(content).decode(),
        "encoding": "base64",
        "sha": _blob_sha(content),
    }


def _handoff_archive(
    head_sha: str, run_id: int = 123, run_attempt: int = 1, base_sha: str = "a" * 40
) -> bytes:
    target = io.BytesIO()
    result = {
        "base_sha": base_sha,
        "head_sha": head_sha,
    }
    provenance = {"run_attempt": str(run_attempt), "run_id": str(run_id)}
    with zipfile.ZipFile(target, "w") as bundle:
        bundle.writestr("complexity-result.json", json.dumps(result))
        bundle.writestr("quality-provenance.json", json.dumps(provenance))
    return target.getvalue()


def test_m10_packet_binds_fresh_full_run_artifact_and_report() -> None:
    head_sha = "b" * 40
    archive = _handoff_archive(head_sha)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    run = {
        "conclusion": "success",
        "event": "pull_request",
        "head_sha": head_sha,
        "id": 123,
        "path": ".github/workflows/organization-required.yml",
        "run_attempt": 1,
        "status": "completed",
        "updated_at": "2026-08-02T17:00:00Z",
    }
    artifact = {
        "digest": f"sha256:{archive_sha256}",
        "expired": False,
        "id": 789,
        "name": "supportability-evidence-123-1",
    }

    def open_request(request: object, *args: object, **kwargs: object) -> _Reply:
        url = request.full_url  # type: ignore[attr-defined]
        if url.endswith("/zip"):
            return _BytesReply(archive)
        if "/actions/runs?" in url:
            return _Reply({"workflow_runs": [run]})
        return _Reply({"artifacts": [artifact]})

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    evidence = app._handoff_evidence(
        "mbh-solutions/supportability-gate", "a" * 40, head_sha, "token"
    )

    assert evidence["artifact_provenance"] == {
        "artifact_digest": f"sha256:{archive_sha256}",
        "artifact_id": 789,
        "archive_sha256": archive_sha256,
        "run_attempt": 1,
        "run_conclusion": "success",
        "run_id": 123,
        "workflow_path": ".github/workflows/organization-required.yml",
    }


def test_m10_packet_skips_completion_report_for_nonproduction_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = EvidencePacket(
        "mbh-solutions/supportability-gate",
        "a" * 40,
        "b" * 40,
        42,
        {"pull_request": 3, "reviewed_sources": []},
    )
    app = GitHubApp(42, 7, b"unused")
    monkeypatch.setattr(app, "evidence_packet", lambda *args: packet)
    monkeypatch.setattr(
        app,
        "_handoff_evidence",
        lambda *args: pytest.fail("nonproduction diff must not require a handoff artifact"),
    )

    assert app.m10_evidence_packet("mbh-solutions/supportability-gate", {}, "token") is packet


def test_m10_packet_collects_completion_evidence_for_deletion_only_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = EvidencePacket(
        "mbh-solutions/supportability-gate",
        "a" * 40,
        "b" * 40,
        42,
        {
            "deleted_sources": [{"path": "src/a.py"}],
            "pull_request": 3,
            "refactor_context": {"changed_files": [{"path": "src/a.py", "status": "modified"}]},
            "reviewed_sources": [],
        },
    )
    app = GitHubApp(42, 7, b"unused")
    monkeypatch.setattr(app, "evidence_packet", lambda *args: packet)
    monkeypatch.setattr(app, "_handoff_evidence", lambda *args: {"authoritative_result": {}})
    monkeypatch.setattr(
        app,
        "_completion_report",
        lambda *args: {"completion_report": {"claims": []}},
    )
    monkeypatch.setattr(
        app,
        "_completion_sources",
        lambda *args: [{"path": "src/a.py", "lines": []}],
    )

    result = app.m10_evidence_packet("mbh-solutions/supportability-gate", {}, "token")

    assert result.evidence["completion_sources"] == [{"lines": [], "path": "src/a.py"}]


def test_newest_successful_full_rerun_attempt_is_accepted() -> None:
    head_sha = "b" * 40
    archive = _handoff_archive(head_sha, run_attempt=2)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    older = {
        "conclusion": "success",
        "event": "pull_request",
        "head_sha": head_sha,
        "id": 122,
        "path": ".github/workflows/organization-required.yml",
        "run_attempt": 1,
        "status": "completed",
        "updated_at": "2026-08-02T16:00:00Z",
    }
    rerun = {
        **older,
        "id": 123,
        "run_attempt": 2,
        "updated_at": "2026-08-02T17:00:00Z",
    }
    artifact = {
        "digest": f"sha256:{archive_sha256}",
        "expired": False,
        "id": 789,
        "name": "supportability-evidence-123-2",
    }

    def open_request(request: object, *args: object, **kwargs: object) -> _Reply:
        url = request.full_url  # type: ignore[attr-defined]
        if url.endswith("/zip"):
            return _BytesReply(archive)
        if "/actions/runs?" in url:
            return _Reply({"workflow_runs": [older, rerun]})
        return _Reply({"artifacts": [artifact]})

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    evidence = app._handoff_evidence(
        "mbh-solutions/supportability-gate", "a" * 40, head_sha, "token"
    )

    assert evidence["artifact_provenance"]["run_attempt"] == 2  # type: ignore[index]


def test_older_successful_run_is_used_when_newer_run_has_stale_base() -> None:
    head_sha = "b" * 40
    older_archive = _handoff_archive(head_sha, run_id=122)
    newer_archive = _handoff_archive(head_sha, base_sha="c" * 40)
    older = {
        "conclusion": "success",
        "event": "pull_request",
        "head_sha": head_sha,
        "id": 122,
        "path": ".github/workflows/organization-required.yml",
        "run_attempt": 1,
        "status": "completed",
        "updated_at": "2026-08-02T16:00:00Z",
    }
    newer = {**older, "id": 123, "updated_at": "2026-08-02T17:00:00Z"}

    def open_request(request: object, *args: object, **kwargs: object) -> _Reply:
        url = request.full_url  # type: ignore[attr-defined]
        if "/actions/runs?" in url:
            return _Reply({"workflow_runs": [older, newer]})
        if "/actions/runs/123/artifacts" in url:
            return _Reply(
                {
                    "artifacts": [
                        {
                            "digest": f"sha256:{hashlib.sha256(newer_archive).hexdigest()}",
                            "expired": False,
                            "id": 789,
                            "name": "supportability-evidence-123-1",
                        }
                    ]
                }
            )
        if "/actions/runs/122/artifacts" in url:
            return _Reply(
                {
                    "artifacts": [
                        {
                            "digest": f"sha256:{hashlib.sha256(older_archive).hexdigest()}",
                            "expired": False,
                            "id": 788,
                            "name": "supportability-evidence-122-1",
                        }
                    ]
                }
            )
        return _BytesReply(newer_archive if "/artifacts/789/" in url else older_archive)

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    evidence = app._handoff_evidence(
        "mbh-solutions/supportability-gate", "a" * 40, head_sha, "token"
    )

    assert evidence["artifact_provenance"]["run_id"] == 122  # type: ignore[index]


def test_failed_rerun_attempt_is_not_accepted_as_m10_evidence() -> None:
    older = {
        "conclusion": "success",
        "event": "pull_request",
        "head_sha": "b" * 40,
        "id": 122,
        "path": ".github/workflows/organization-required.yml",
        "run_attempt": 1,
        "status": "completed",
        "updated_at": "2026-08-02T16:00:00Z",
    }
    failed = {
        "conclusion": "failure",
        "event": "pull_request",
        "head_sha": "b" * 40,
        "id": 123,
        "path": ".github/workflows/organization-required.yml",
        "run_attempt": 2,
        "status": "completed",
        "updated_at": "2026-08-02T17:00:00Z",
    }
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply({"workflow_runs": [older, failed]}),
    )
    with pytest.raises(SemanticReviewError, match="HANDOFF_EVIDENCE_UNAVAILABLE"):
        app._handoff_evidence("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, "token")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("updated_at", "garbage"),
        ("updated_at", "20260802T170000Z"),
        ("id", True),
        ("id", 0),
        ("run_attempt", True),
    ],
)
def test_malformed_successful_rerun_metadata_is_rejected(field: str, value: object) -> None:
    run = {
        "conclusion": "success",
        "event": "pull_request",
        "head_sha": "b" * 40,
        "id": 123,
        "path": ".github/workflows/organization-required.yml",
        "run_attempt": 2,
        "status": "completed",
        "updated_at": "2026-08-02T17:00:00Z",
    }
    run[field] = value
    app = GitHubApp(
        42, 7, b"unused", opener=lambda *args, **kwargs: _Reply({"workflow_runs": [run]})
    )

    with pytest.raises(SemanticReviewError, match="MALFORMED_GITHUB_RESPONSE"):
        app._handoff_evidence("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, "token")


def test_rerun_attempt_with_mismatched_provenance_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head_sha = "b" * 40
    archive = _handoff_archive(head_sha, run_attempt=1)
    digest = f"sha256:{hashlib.sha256(archive).hexdigest()}"
    run = {
        "conclusion": "success",
        "id": 123,
        "run_attempt": 2,
    }
    app = GitHubApp(42, 7, b"unused")
    monkeypatch.setattr(app, "_handoff_runs", lambda *args: (run,))
    monkeypatch.setattr(
        app,
        "_handoff_artifact",
        lambda *args: {"digest": digest, "id": 789},
    )
    monkeypatch.setattr(app, "_artifact_bytes", lambda *args: archive)

    with pytest.raises(SemanticReviewError, match="STALE_HANDOFF_EVIDENCE"):
        app._handoff_evidence("mbh-solutions/supportability-gate", "a" * 40, head_sha, "token")


def test_handoff_artifact_with_stale_base_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    head_sha = "b" * 40
    archive = _handoff_archive(head_sha, base_sha="c" * 40)
    app = GitHubApp(42, 7, b"unused")
    monkeypatch.setattr(app, "_handoff_runs", lambda *args: ({"id": 123, "run_attempt": 1},))
    monkeypatch.setattr(
        app,
        "_handoff_artifact",
        lambda *args: {"digest": f"sha256:{hashlib.sha256(archive).hexdigest()}", "id": 789},
    )
    monkeypatch.setattr(app, "_artifact_bytes", lambda *args: archive)

    with pytest.raises(SemanticReviewError, match="STALE_HANDOFF_EVIDENCE"):
        app._handoff_evidence("mbh-solutions/supportability-gate", "a" * 40, head_sha, "token")


def test_handoff_archive_rejects_large_expanded_member() -> None:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("complexity-result.json", b"0" * 5_000_001)
        bundle.writestr("quality-provenance.json", b"{}")
    app = GitHubApp(42, 7, b"unused")
    with pytest.raises(SemanticReviewError, match="HANDOFF_EVIDENCE_UNAVAILABLE"):
        app._artifact_json(target.getvalue())


def test_m10_report_is_bound_to_exact_head_blob() -> None:
    content = b'schema_version = "1.0"\n[completion_report]\noverall_result = "PASS"\n'
    blob_sha = _blob_sha(content)

    def open_request(request: object, *args: object, **kwargs: object) -> _Reply:
        if "/contents/" in request.full_url:  # type: ignore[attr-defined]
            return _Reply({"sha": blob_sha})
        return _Reply(_blob_payload(content))

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    evidence = app._completion_report("mbh-solutions/supportability-gate", "b" * 40, "token")

    assert evidence["completion_report"] == {"overall_result": "PASS"}
    assert evidence["completion_report_provenance"] == {
        "blob_sha": blob_sha,
        "parser_result": "PASS",
        "path": ".supportability-handoff.toml",
        "resolved_head_sha": "b" * 40,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_app_jwt_binds_app_and_short_lifetime() -> None:
    token = app_jwt(42, _private_key(), now=1000)
    header, payload, signature = token.split(".")
    assert _decode(header) == {"alg": "RS256", "typ": "JWT"}
    assert _decode(payload) == {"exp": 1540, "iat": 940, "iss": "42"}
    assert signature


def test_invalid_private_key_blocks_with_stable_error() -> None:
    with pytest.raises(SemanticReviewError, match="INVALID_APP_PRIVATE_KEY"):
        app_jwt(42, b"not a key")


def test_installation_auth_verifies_app_identity() -> None:
    replies = iter([{"id": 42}, {"token": "installation-token"}])
    app = GitHubApp(42, 7, _private_key(), opener=lambda *args, **kwargs: _Reply(next(replies)))
    assert app.installation_token() == "installation-token"


def test_installation_repository_discovery_paginates_every_selected_repository() -> None:
    urls: list[str] = []

    def open_request(request: Any, **kwargs: object) -> _Reply:
        urls.append(request.full_url)
        page = 2 if "page=2" in request.full_url else 1
        repositories = (
            [{"full_name": f"owner/repo-{number}", "id": number} for number in range(100)]
            if page == 1
            else [{"full_name": "owner/repo-100", "id": 100}]
        )
        return _Reply({"repositories": repositories, "total_count": 101})

    app = GitHubApp(42, 7, b"unused", opener=open_request)

    assert len(app.installation_repositories("token")) == 101
    assert urls[-1].endswith("/installation/repositories?per_page=100&page=2")


def test_incomplete_installation_repository_page_blocks() -> None:
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply(
            {"repositories": [{"full_name": "owner/repo", "id": 1}], "total_count": 2}
        ),
    )

    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.installation_repositories("token")


def test_duplicate_installation_repository_identity_blocks() -> None:
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply(
            {
                "repositories": [
                    {"full_name": "owner/one", "id": 1},
                    {"full_name": "owner/two", "id": 1},
                ],
                "total_count": 2,
            }
        ),
    )

    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.installation_repositories("token")


def test_handoff_ready_requires_successful_exact_head_artifact() -> None:
    head_sha = "b" * 40
    replies = iter(
        [
            {
                "workflow_runs": [
                    {
                        "conclusion": "success",
                        "event": "pull_request",
                        "head_sha": head_sha,
                        "id": 123,
                        "path": ".github/workflows/organization-required.yml",
                        "run_attempt": 1,
                        "status": "completed",
                        "updated_at": "2026-08-09T17:00:00Z",
                    }
                ]
            },
            {
                "artifacts": [
                    {
                        "digest": f"sha256:{'c' * 64}",
                        "expired": False,
                        "id": 456,
                        "name": "supportability-evidence-123-1",
                    }
                ]
            },
        ]
    )
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(next(replies)))

    assert app.handoff_ready("owner/repo", head_sha, "token") is True


def test_handoff_ready_accepts_older_success_when_newer_attempt_failed() -> None:
    head_sha = "b" * 40
    replies = iter(
        [
            {
                "workflow_runs": [
                    {
                        "conclusion": conclusion,
                        "event": "pull_request",
                        "head_sha": head_sha,
                        "id": run_id,
                        "path": ".github/workflows/organization-required.yml",
                        "run_attempt": attempt,
                        "status": "completed",
                        "updated_at": updated,
                    }
                    for conclusion, run_id, attempt, updated in (
                        ("failure", 124, 2, "2026-08-09T18:00:00Z"),
                        ("success", 123, 1, "2026-08-09T17:00:00Z"),
                    )
                ]
            },
            {
                "artifacts": [
                    {
                        "digest": f"sha256:{'c' * 64}",
                        "expired": False,
                        "id": 456,
                        "name": "supportability-evidence-123-1",
                    }
                ]
            },
        ]
    )
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(next(replies)))

    assert app.handoff_ready("owner/repo", head_sha, "token") is True


def test_wrong_app_identity_blocks() -> None:
    app = GitHubApp(42, 7, _private_key(), opener=lambda *args, **kwargs: _Reply({"id": 41}))
    with pytest.raises(SemanticReviewError, match="APP_IDENTITY_MISMATCH"):
        app.installation_token()


def test_open_pull_truncation_blocks() -> None:
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply([{}] * 100))
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.open_pulls("mbh-solutions/supportability-gate", "token")


def test_closed_exact_pull_blocks_before_evaluation() -> None:
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply({"number": 3, "state": "closed"}),
    )
    with pytest.raises(SemanticReviewError, match="STALE_EVIDENCE"):
        app.pull("mbh-solutions/supportability-gate", 3, "token")


def test_boolean_pull_number_cannot_impersonate_integer_identity() -> None:
    current = {
        "base": {"sha": "a" * 40},
        "head": {"sha": "b" * 40},
        "number": True,
        "state": "open",
    }
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(current))

    with pytest.raises(SemanticReviewError, match="MALFORMED_PULL_REQUEST"):
        app.pull("mbh-solutions/supportability-gate", 1, "token")
    with pytest.raises(SemanticReviewError, match="STALE_EVIDENCE"):
        app.assert_current(packet, 1, "token")


def test_semantic_source_collection_has_no_total_line_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = GitHubApp(42, 7, b"unused")
    files = (
        {"filename": "src/a.py", "status": "modified"},
        {"filename": "src/b.py", "status": "modified"},
    )
    monkeypatch.setattr(app, "_production_paths", lambda *args: ("src",))
    monkeypatch.setattr(
        app,
        "_reviewed_source",
        lambda repository, item, token: {
            "lines": [None] * 2_003,
            "path": item["filename"],
        },
    )
    monkeypatch.setattr(app, "_deletion_evidence", lambda *args: (None, None))

    sources, deleted = app._source_evidence("owner/repo", "a" * 40, files, "token")

    assert sum(len(source["lines"]) for source in sources) == 4_006
    assert deleted == []


def test_stale_pull_evidence_blocks_before_publication() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    current = {
        "base": {"sha": "c" * 40},
        "head": {"sha": "b" * 40},
        "number": 3,
        "state": "open",
    }
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(current))
    with pytest.raises(SemanticReviewError, match="STALE_EVIDENCE"):
        app.assert_current(packet, 3, "token")


def test_wrong_pull_number_blocks_before_publication() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    current = {
        "base": {"sha": "a" * 40},
        "head": {"sha": "b" * 40},
        "number": 4,
        "state": "open",
    }
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(current))
    with pytest.raises(SemanticReviewError, match="STALE_EVIDENCE"):
        app.assert_current(packet, 3, "token")


def test_stale_review_state_blocks_before_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {
        "inline_comments": [],
        "reviews": [],
        "schema_version": "review-state.v1",
        "threads": [],
        "top_level_comments": [],
    }
    packet = EvidencePacket(
        "mbh-solutions/supportability-gate",
        "a" * 40,
        "b" * 40,
        42,
        {"review_state": captured},
    )
    current = {
        "base": {"sha": "a" * 40},
        "head": {"sha": "b" * 40},
        "number": 3,
        "state": "open",
    }
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(current))
    monkeypatch.setattr(app, "issue_comments", lambda *args: ())
    monkeypatch.setattr(app, "_review_state", lambda *args: {**captured, "threads": [{}]})
    with pytest.raises(SemanticReviewError, match="STALE_EVIDENCE"):
        app.assert_current(packet, 3, "token")


def test_authenticated_pr_and_closing_issue_authority_is_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issue = {
        "body": "Acceptance: save is durable.",
        "number": 8,
        "repository": {"nameWithOwner": "mbh-solutions/dc_training"},
        "title": "Save workout",
        "updatedAt": "2026-08-09T01:00:00Z",
        "url": "https://github.com/mbh-solutions/dc_training/issues/8",
    }
    result = {
        "data": {
            "repository": {
                "pullRequest": {
                    "closingIssuesReferences": {
                        "nodes": [issue],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }
    }
    app = GitHubApp(42, 7, b"unused")
    monkeypatch.setattr(app, "_request", lambda *args: result)
    pull = {
        "body": None,
        "html_url": "https://github.com/mbh-solutions/dc_training/pull/18",
        "number": 18,
        "title": "Workout history",
        "updated_at": "2026-08-09T02:00:00Z",
    }

    authority = AUTHORITY(app, "mbh-solutions/dc_training", 18, pull, "token")

    assert authority == {
        "closing_issues": [
            {
                "body": "Acceptance: save is durable.",
                "number": 8,
                "repository": "mbh-solutions/dc_training",
                "title": "Save workout",
                "updated_at": "2026-08-09T01:00:00Z",
                "url": "https://github.com/mbh-solutions/dc_training/issues/8",
            }
        ],
        "pull_request": {
            "body": "",
            "number": 18,
            "repository": "mbh-solutions/dc_training",
            "title": "Workout history",
            "updated_at": "2026-08-09T02:00:00Z",
            "url": "https://github.com/mbh-solutions/dc_training/pull/18",
        },
    }


def test_authority_edit_invalidates_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = {"closing_issues": [], "pull_request": {"body": "original"}}
    packet = EvidencePacket(
        "mbh-solutions/dc_training",
        "a" * 40,
        "b" * 40,
        42,
        {"authority": authority, "review_state": {"threads": []}},
    )
    current = {
        "base": {"sha": "a" * 40},
        "head": {"sha": "b" * 40},
        "number": 18,
        "state": "open",
    }
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(current))
    monkeypatch.setattr(app, "_authority", lambda *args: {**authority, "edited": True})

    with pytest.raises(SemanticReviewError, match="STALE_EVIDENCE"):
        app.assert_current(packet, 18, "token")


def test_validated_full_diff_preserves_sql_mjs_and_markdown() -> None:
    paths = ("supabase/migration.sql", "scripts/check.mjs", "docs/acceptance.md")
    files = [
        {"filename": path, "patch": "@@ -0,0 +1 @@\n+changed", "status": "added"} for path in paths
    ]
    diff = "\n".join(
        f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+changed"
        for path in paths
    )

    def open_request(request: Any, **kwargs: object) -> _Reply:
        return (
            _RawReply(diff)
            if request.get_header("Accept") == "application/vnd.github.v3.diff"
            else _Reply({"files": files})
        )

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    captured, captured_files = app._comparison_evidence(
        "mbh-solutions/dc_training", "a" * 40, "b" * 40, "token"
    )

    assert captured == diff
    assert tuple(item["filename"] for item in captured_files) == paths


def test_exact_evidence_replay_reuses_app_result() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    runs = {
        "total_count": 1,
        "check_runs": [
            {
                "app": {"id": 42},
                "conclusion": "success",
                "external_id": packet.sha256,
                "status": "completed",
            }
        ],
    }
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(runs))
    assert app.replay_result(packet, "token") is True


def test_action_required_technical_result_remains_retryable() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    runs = {
        "total_count": 1,
        "check_runs": [
            {
                "app": {"id": 42},
                "conclusion": "action_required",
                "external_id": packet.sha256,
                "status": "completed",
            }
        ],
    }
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(runs))

    assert app.replay_result(packet, "token") is None


def test_replay_truncation_blocks() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply({"total_count": 100, "check_runs": []}),
    )
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.replay_result(packet, "token")


def test_compare_evidence_and_check_bind_exact_head_app_and_hash() -> None:
    requests: list[Any] = []
    source = b"def safe():\n    return 1\n"
    source_sha = _blob_sha(source)
    contract_sha = _blob_sha(CONTRACT)

    def open_request(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        if "/contents/.supportability.toml" in request.full_url:
            return _Reply({"sha": contract_sha})
        if request.full_url.endswith(f"/git/blobs/{contract_sha}"):
            return _Reply(_blob_payload(CONTRACT))
        if request.full_url.endswith(f"/git/blobs/{source_sha}"):
            return _Reply(_blob_payload(source))
        if "/issues/3/comments" in request.full_url:
            return _Reply(
                [
                    {
                        "body": "Supportability-Refactor-Authorization: {}",
                        "id": 11,
                        "user": {"id": 229662739},
                    }
                ]
            )
        if "/compare/" in request.full_url:
            if request.get_header("Accept") != "application/vnd.github.v3.diff":
                return _Reply(
                    {
                        "files": [
                            {
                                "filename": "src/a.py",
                                "patch": "@@ -1 +1,2 @@\n+def safe():\n+    return 1",
                                "sha": source_sha,
                                "status": "modified",
                            }
                        ]
                    }
                )
            return _RawReply("diff --git a/src/a.py b/src/a.py\n+safe")
        body = json.loads(request.data)
        return _Reply({"app": {"id": 42}, "head_sha": body["head_sha"], "id": 99})

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull: dict[str, Any] = {
        "number": 3,
        "author_association": "MEMBER",
        "base": {"sha": "a" * 40},
        "body": "Supportability-Refactor-Authorization: {}",
        "head": {"sha": "b" * 40},
        "user": {"id": 229662739, "login": "markheck-solutions"},
    }
    packet = app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")
    result = app.publish_check(packet, "token", "success", "PASS")
    payload = json.loads(requests[-1].data)
    assert result["id"] == 99
    assert payload["name"] == CHECK_NAME
    assert payload["head_sha"] == "b" * 40
    assert packet.sha256 in payload["output"]["summary"]
    assert packet.instruction_sha256 in payload["output"]["summary"]
    assert packet.evidence["reviewed_sources"] == [
        {
            "blob_sha": source_sha,
            "boundaries": [{"end_line": 2, "kind": "function", "name": "safe", "start_line": 1}],
            "imports": [],
            "line_count": 2,
            "lines": [
                {"line": 1, "text": "def safe():"},
                {"line": 2, "text": "    return 1"},
            ],
            "path": "src/a.py",
        }
    ]
    assert packet.evidence["refactor_context"] == {
        "author_association": "MEMBER",
        "author_id": 229662739,
        "author_login": "markheck-solutions",
        "authorization_comments": [
            {
                "body": "Supportability-Refactor-Authorization: {}",
                "id": 11,
                "user_id": 229662739,
            }
        ],
        "changed_files": [{"path": "src/a.py", "status": "modified"}],
        "trusted_owner_id": 229662739,
    }


def test_frontend_component_boundary_uses_complete_parser_span() -> None:
    app = GitHubApp(42, 7, b"unused")
    source = "export function SaveButton() {\n  const label = 'Save';\n  return <button>{label}</button>;\n}\n"
    boundaries = app._source_boundaries(
        "src/SaveButton.tsx",
        source,
        "@@ -2 +2 @@\n-  const label = 'Go';\n+  const label = 'Save';",
    )
    assert boundaries == [
        {"end_line": 4, "kind": "component", "name": "SaveButton", "start_line": 1}
    ]
    assert [item["line"] for item in app._source_excerpt(source, boundaries)] == [1, 2, 3, 4]


def test_supported_source_boundaries_include_stubs_decorators_and_react_classes() -> None:
    app = GitHubApp(42, 7, b"unused")
    candidates = app._source_candidates(
        tuple(
            {"filename": f"src/sample{suffix}", "status": "modified"}
            for suffix in (*sorted(REVIEWED_SUFFIXES), ".js", ".jsx")
        )
    )
    assert {item["filename"].rsplit("sample", 1)[1] for item in candidates} == set(
        REVIEWED_SUFFIXES
    )

    decorated = "@route('/x')\ndef handle():\n    return 1\n"
    assert app._source_boundaries("src/routes.pyi", decorated, "@@ -0,0 +1 @@\n+@route('/x')") == [
        {"end_line": 3, "kind": "function", "name": "handle", "start_line": 1}
    ]
    latin = b"# coding: latin-1\nlabel = 'caf\xe9'\n"
    assert responsibility_spans("src/labels.py", latin, {2}) == (
        ResponsibilitySpan(2, 2, "module", "src/labels.py"),
    )

    frontend = (
        "class Panel extends React.PureComponent<Props> {\n"
        "  render() { return <div />; }\n}\n"
        "class Plain {\n  method() {}\n}\n"
    )
    assert app._source_boundaries(
        "src/panel.tsx",
        frontend,
        "@@ -2 +2 @@\n-  render() { return null; }\n"
        "+  render() { return <div />; }\n"
        "@@ -5 +5 @@\n-  old() {}\n+  method() {}",
    ) == [
        {"end_line": 3, "kind": "component", "name": "Panel", "start_line": 1},
        {"end_line": 2, "kind": "function", "name": "Panel.render", "start_line": 2},
        {"end_line": 5, "kind": "function", "name": "Plain.method", "start_line": 5},
    ]


def test_deletion_only_change_maps_surviving_head_and_removed_base_responsibilities() -> None:
    base = b"def keep():\n    removed = 1\n    return 1\n\ndef obsolete():\n    return 2\n"
    head = b"def keep():\n    return 1\n"
    base_blob, head_blob, contract_blob = map(_blob_sha, (base, head, CONTRACT))
    patch = (
        "@@ -1,6 +1,2 @@\n def keep():\n-    removed = 1\n"
        "     return 1\n-\n-def obsolete():\n-    return 2"
    )

    def open_request(request: Any, **kwargs: object) -> _Reply:
        if "/issues/3/comments" in request.full_url:
            return _Reply([])
        if "/contents/.supportability.toml" in request.full_url:
            return _Reply({"sha": contract_blob})
        if "/contents/src/a.py?ref=" in request.full_url:
            return _Reply({"sha": base_blob})
        for content in (CONTRACT, base, head):
            if request.full_url.endswith(f"/git/blobs/{_blob_sha(content)}"):
                return _Reply(_blob_payload(content))
        if request.get_header("Accept") == "application/vnd.github.v3.diff":
            return _RawReply(f"diff --git a/src/a.py b/src/a.py\n{patch}")
        return _Reply(
            {
                "files": [
                    {
                        "filename": "src/a.py",
                        "patch": patch,
                        "sha": head_blob,
                        "status": "modified",
                    }
                ]
            }
        )

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    packet = app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")

    assert packet.evidence["reviewed_sources"] == [
        {
            "blob_sha": head_blob,
            "boundaries": [{"end_line": 2, "kind": "function", "name": "keep", "start_line": 1}],
            "imports": [],
            "line_count": 2,
            "lines": [
                {"line": 1, "text": "def keep():"},
                {"line": 2, "text": "    return 1"},
            ],
            "path": "src/a.py",
        }
    ]
    assert packet.evidence["deleted_sources"] == [
        {
            "blob_sha": base_blob,
            "boundaries": [
                {"end_line": 6, "kind": "function", "name": "obsolete", "start_line": 5},
            ],
            "line_count": 6,
            "path": "src/a.py",
        }
    ]


def test_deletion_only_completion_citation_uses_exact_head_lines_without_boundary() -> None:
    head = b"def keep():\n    return 1\n"
    head_blob = _blob_sha(head)

    def open_request(request: Any, **kwargs: object) -> _Reply:
        if "/contents/src/a.py?ref=" in request.full_url:
            return _Reply({"sha": head_blob})
        return _Reply(_blob_payload(head))

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    sources = app._completion_sources(
        "mbh-solutions/supportability-gate",
        "b" * 40,
        {
            "claims": [
                {
                    "citations": ["src/a.py:1-2"],
                    "id": "retained-behavior",
                    "text": "keep still returns one.",
                }
            ]
        },
        [],
        [{"blob_sha": "a" * 40, "boundaries": [], "path": "src/a.py"}],
        [{"path": "src/a.py", "status": "modified"}],
        "token",
    )

    assert sources == [
        {
            "blob_sha": head_blob,
            "line_count": 2,
            "lines": [
                {"line": 1, "text": "def keep():"},
                {"line": 2, "text": "    return 1"},
            ],
            "path": "src/a.py",
        }
    ]


def test_completion_sources_reject_unbounded_paths_and_ranges() -> None:
    report = {
        "claims": [{"citations": ["src/a.py:1-2"], "id": "claim", "text": "retained behavior"}]
    }
    no_request = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: pytest.fail("unbounded citation must not be fetched"),
    )
    cases = (
        (report, [], [{"path": "src/a.py", "status": "modified"}]),
        (report, [{"path": "src/a.py"}], [{"path": "src/a.py", "status": "removed"}]),
        (
            {"claims": [{"citations": ["src/a.txt:1-2"], "id": "claim", "text": "text"}]},
            [{"path": "src/a.txt"}],
            [{"path": "src/a.txt", "status": "modified"}],
        ),
        (
            {"claims": [{"citations": ["not-a-citation"], "id": "claim", "text": "bad"}]},
            [{"path": "src/a.py"}],
            [{"path": "src/a.py", "status": "modified"}],
        ),
    )
    for candidate, deleted, changed in cases:
        assert (
            no_request._completion_sources(
                "mbh-solutions/supportability-gate",
                "b" * 40,
                candidate,
                [],
                deleted,
                changed,
                "token",
            )
            == []
        )

    head = b"def keep():\n    return 1\n"
    head_blob = _blob_sha(head)
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda request, **kwargs: (
            _Reply({"sha": head_blob})
            if "/contents/" in request.full_url
            else _Reply(_blob_payload(head))
        ),
    )
    assert (
        app._completion_sources(
            "mbh-solutions/supportability-gate",
            "b" * 40,
            {"claims": [{"citations": ["src/a.py:9-10"], "id": "claim", "text": "bad"}]},
            [],
            [{"path": "src/a.py"}],
            [{"path": "src/a.py", "status": "modified"}],
            "token",
        )
        == []
    )


def test_completion_sources_include_large_valid_citation() -> None:
    head = b"value = 1\n" * 2_501
    head_blob = _blob_sha(head)
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda request, **kwargs: (
            _Reply({"sha": head_blob})
            if "/contents/" in request.full_url
            else _Reply(_blob_payload(head))
        ),
    )

    sources = app._completion_sources(
        "mbh-solutions/supportability-gate",
        "b" * 40,
        {"claims": [{"citations": ["src/a.py:1-2501"], "id": "claim", "text": "large"}]},
        [],
        [{"path": "src/a.py"}],
        [{"path": "src/a.py", "status": "modified"}],
        "token",
    )

    assert len(sources) == 1
    assert len(sources[0]["lines"]) == 2_501


def test_completion_source_coalesces_duplicate_ranges_before_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = b"value = 1\n" * 2_501
    head_blob = _blob_sha(head)
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda request, **kwargs: (
            _Reply({"sha": head_blob})
            if "/contents/" in request.full_url
            else _Reply(_blob_payload(head))
        ),
    )
    calls: list[tuple[int, int]] = []
    builtin_range = range

    def counted_range(start: int, end: int) -> range:
        calls.append((start, end))
        return builtin_range(start, end)

    monkeypatch.setattr(github_app_module, "range", counted_range, raising=False)

    source = app._completion_source(
        "mbh-solutions/supportability-gate",
        "b" * 40,
        "src/a.py",
        [(1, 2_500), (2, 2_501)] * 5_000,
        "token",
    )

    assert source is not None
    assert len(source["lines"]) == 2_501
    assert calls == [(1, 2_502)]


def test_mixed_source_change_reviews_head_and_identifies_deleted_base_responsibilities() -> None:
    base = b"def keep():\n    return 1\n\ndef obsolete():\n    return 2\n"
    head = b"def keep():\n    return 3\n"
    base_blob, head_blob, contract_blob = map(_blob_sha, (base, head, CONTRACT))
    patch = "@@ -1,5 +1,2 @@\n def keep():\n-    return 1\n-\n-def obsolete():\n-    return 2\n+    return 3"

    def open_request(request: Any, **kwargs: object) -> _Reply:
        if "/issues/3/comments" in request.full_url:
            return _Reply([])
        if "/contents/.supportability.toml" in request.full_url:
            return _Reply({"sha": contract_blob})
        if "/contents/src/a.py?ref=" in request.full_url:
            return _Reply({"sha": base_blob})
        for content in (CONTRACT, base, head):
            if request.full_url.endswith(f"/git/blobs/{_blob_sha(content)}"):
                return _Reply(_blob_payload(content))
        if request.get_header("Accept") == "application/vnd.github.v3.diff":
            return _RawReply(f"diff --git a/src/a.py b/src/a.py\n{patch}")
        return _Reply(
            {
                "files": [
                    {
                        "filename": "src/a.py",
                        "patch": patch,
                        "sha": head_blob,
                        "status": "modified",
                    }
                ]
            }
        )

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    packet = app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")

    assert packet.evidence["reviewed_sources"][0]["boundaries"] == [
        {"end_line": 2, "kind": "function", "name": "keep", "start_line": 1}
    ]
    deleted = packet.evidence["deleted_sources"][0]
    assert deleted["blob_sha"] == base_blob
    assert deleted["boundaries"] == [
        {"end_line": 5, "kind": "function", "name": "obsolete", "start_line": 4},
    ]
    assert app._completion_sources(
        "mbh-solutions/supportability-gate",
        "b" * 40,
        {"claims": [{"citations": ["src/a.py:1-2"], "id": "claim", "text": "keep returns three"}]},
        packet.evidence["reviewed_sources"],
        packet.evidence["deleted_sources"],
        [{"path": "src/a.py", "status": "modified"}],
        "token",
    ) == [
        {
            "blob_sha": head_blob,
            "line_count": 2,
            "lines": [
                {"line": 1, "text": "def keep():"},
                {"line": 2, "text": "    return 3"},
            ],
            "path": "src/a.py",
        }
    ]


@pytest.mark.parametrize(
    "diff",
    [
        "",
        "Binary files a/image.png and b/image.png differ",
        "GIT binary patch",
    ],
)
def test_incomplete_diff_evidence_blocks(diff: str) -> None:
    def open_request(request: Any, **kwargs: object) -> _Reply:
        if request.get_header("Accept") == "application/vnd.github.v3.diff":
            return _RawReply(diff)
        return _Reply({"files": [{}]})

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")


def test_binary_marker_inside_source_text_is_evidence() -> None:
    source = b'marker = "Binary files a/image.png and b/image.png differ"\n'
    source_sha = _blob_sha(source)
    contract_sha = _blob_sha(CONTRACT)
    patch = '@@ -1 +1 @@\n+marker = "Binary files a/image.png and b/image.png differ"'
    diff = f"diff --git a/src/a.py b/src/a.py\n{patch}"

    def open_request(request: Any, **kwargs: object) -> _Reply:
        if "/issues/3/comments" in request.full_url:
            return _Reply([])
        if "/contents/.supportability.toml" in request.full_url:
            return _Reply({"sha": contract_sha})
        if request.full_url.endswith(f"/git/blobs/{contract_sha}"):
            return _Reply(_blob_payload(CONTRACT))
        if request.full_url.endswith(f"/git/blobs/{source_sha}"):
            return _Reply(_blob_payload(source))
        if request.get_header("Accept") == "application/vnd.github.v3.diff":
            return _RawReply(diff)
        return _Reply(
            {
                "files": [
                    {
                        "filename": "src/a.py",
                        "patch": patch,
                        "sha": source_sha,
                        "status": "modified",
                    }
                ]
            }
        )

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    packet = app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")
    assert packet.evidence["diff"] == diff
    assert packet.evidence["reviewed_sources"][0]["lines"][0]["text"] == source.decode().strip()


def test_compare_file_limit_blocks_before_diff_fetch() -> None:
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply({"files": [{}] * 300}),
    )
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")


def test_invalid_exact_head_source_blob_blocks() -> None:
    contract_sha = _blob_sha(CONTRACT)

    def open_request(request: Any, **kwargs: object) -> _Reply:
        if "/issues/3/comments" in request.full_url:
            return _Reply([])
        if "/contents/.supportability.toml" in request.full_url:
            return _Reply({"sha": contract_sha})
        if request.full_url.endswith(f"/git/blobs/{contract_sha}"):
            return _Reply(_blob_payload(CONTRACT))
        if "/git/blobs/" in request.full_url:
            return _Reply({"content": "not-base64!", "encoding": "base64", "sha": "c" * 40})
        if request.get_header("Accept") == "application/vnd.github.v3.diff":
            return _RawReply("diff --git a/src/a.py b/src/a.py\n+safe")
        return _Reply(
            {
                "files": [
                    {
                        "filename": "src/a.py",
                        "patch": "@@ -1 +1 @@\n+safe",
                        "sha": "c" * 40,
                        "status": "modified",
                    }
                ]
            }
        )

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")


def test_only_base_contract_production_paths_are_reviewed() -> None:
    contract_sha = _blob_sha(CONTRACT)

    def open_request(request: Any, **kwargs: object) -> _Reply:
        if "/issues/3/comments" in request.full_url:
            return _Reply([])
        if "/contents/.supportability.toml" in request.full_url:
            return _Reply({"sha": contract_sha})
        if request.full_url.endswith(f"/git/blobs/{contract_sha}"):
            return _Reply(_blob_payload(CONTRACT))
        if request.get_header("Accept") == "application/vnd.github.v3.diff":
            return _RawReply("diff --git a/tests/test_a.py b/tests/test_a.py\n+test")
        return _Reply(
            {"files": [{"filename": "tests/test_a.py", "sha": "c" * 40, "status": "modified"}]}
        )

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    packet = app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")
    assert packet.evidence["reviewed_sources"] == []


def test_removed_source_needs_no_outside_head_evidence() -> None:
    def open_request(request: Any, **kwargs: object) -> _Reply:
        if "/issues/3/comments" in request.full_url:
            return _Reply([])
        if request.get_header("Accept") == "application/vnd.github.v3.diff":
            return _RawReply("diff --git a/src/a.py b/src/a.py\n-deleted")
        return _Reply({"files": [{"filename": "src/a.py", "sha": None, "status": "removed"}]})

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    packet = app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")
    assert packet.evidence["reviewed_sources"] == []


def test_check_response_from_wrong_app_blocks() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply({"app": {"id": 41}, "head_sha": "b" * 40}),
    )
    with pytest.raises(SemanticReviewError, match="CHECK_APP_IDENTITY_MISMATCH"):
        app.publish_check(packet, "token", "success", "PASS")


def test_pending_check_is_completed_with_same_evidence_binding() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    requests: list[Any] = []

    def open_request(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        if request.method == "GET":
            return _Reply({"check_runs": [], "total_count": 0})
        return _Reply(
            {
                "app": {"id": 42},
                "external_id": packet.sha256,
                "head_sha": packet.head_sha,
                "id": 99,
            }
        )

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    check_id = app.start_check(packet, "token")
    result = app.complete_check(packet, "token", check_id, "success", "PASS")
    assert result["id"] == 99
    pending = json.loads(requests[1].data)
    assert pending["status"] == "in_progress"
    assert packet.instruction_sha256 in pending["output"]["summary"]
    assert requests[2].method == "PATCH"
    assert packet.instruction_sha256 in json.loads(requests[2].data)["output"]["summary"]


def test_oversized_valid_block_summary_is_safe_bounded_and_actionable() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    requests: list[Any] = []
    prefix = "BLOCK\nfinding: src/a.py:1-2 unsupported completion claim \ud800"
    summary = prefix + "x" * (69_197 - len(prefix))
    safe_summary = summary.encode("utf-8", errors="backslashreplace").decode()

    def open_request(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        return _Reply(
            {
                "app": {"id": 42},
                "external_id": packet.sha256,
                "head_sha": packet.head_sha,
                "id": 99,
            }
        )

    GitHubApp(42, 7, b"unused", opener=open_request).complete_check(
        packet, "token", 99, "failure", summary
    )

    published = json.loads(requests[0].data)["output"]["summary"]
    assert len(summary) == 69_197
    assert len(published.encode()) <= MAX_CHECK_SUMMARY_BYTES
    assert published.startswith(prefix.replace("\ud800", "\\ud800"))
    assert "x" * 100 in published
    assert "GitHub output truncated; full summary SHA-256" in published
    assert hashlib.sha256(safe_summary.encode()).hexdigest() in published
    assert packet.sha256 in published


def test_existing_pending_check_is_reused_idempotently() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    runs = {
        "check_runs": [
            {
                "app": {"id": 42},
                "external_id": packet.sha256,
                "id": 99,
                "status": "in_progress",
            },
            {
                "app": {"id": 42},
                "external_id": packet.sha256,
                "id": 100,
                "status": "in_progress",
            },
        ],
        "total_count": 2,
    }
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(runs))
    assert app.start_check(packet, "token") == 100


def test_github_outage_leaves_no_check() -> None:
    calls = 0

    def offline(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise urllib.error.URLError("offline")

    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    app = GitHubApp(42, 7, b"unused", opener=offline)
    with pytest.raises(SemanticReviewError, match="GITHUB_TRANSPORT_FAILURE"):
        app.publish_check(packet, "token", "success", "PASS")
    assert calls == 1
