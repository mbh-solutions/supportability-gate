from __future__ import annotations

import json
import urllib.error
import urllib.parse
from collections.abc import Callable
from typing import Any

import pytest

from supportability_gate import codex_review

HEAD = "a" * 40
OLD_HEAD = "b" * 40
REQUESTED = "2026-08-11T12:00:00Z"
COMPLETED = "2026-08-11T12:01:00Z"
RUN_ID = 12345


class _Reply:
    def __init__(self, value: object) -> None:
        self.value = value

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.value, bytes):
            return self.value
        return json.dumps(self.value).encode()


def _request(
    head: str = HEAD,
    *,
    comment_id: int = 1,
    run_id: int = RUN_ID,
    updated_at: str = REQUESTED,
    user_id: int = codex_review.REQUESTER_ID,
) -> dict[str, object]:
    return {
        "body": (f"@codex review\n\nCodex-Review-Head: {head}\nCodex-Review-Run: {run_id}"),
        "created_at": REQUESTED,
        "id": comment_id,
        "updated_at": updated_at,
        "user": {"id": user_id},
    }


def _reaction(
    *, user_id: int = codex_review.CONNECTOR_ID, content: str = "+1"
) -> dict[str, object]:
    return {
        "content": content,
        "created_at": COMPLETED,
        "user": {"id": user_id},
    }


def _observer(**overrides: object) -> dict[str, object]:
    job: dict[str, object] = {
        "conclusion": "success",
        "head_sha": HEAD,
        "id": 10,
        "name": codex_review.OBSERVER_JOB,
        "run_id": RUN_ID,
        "workflow_name": codex_review.WORKFLOW_NAME,
    }
    job.update(overrides)
    return job


def _jobs(jobs: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "jobs": jobs or [],
        "total_count": len(jobs or []),
    }


def _log(comment_id: int = 1) -> bytes:
    return f"2026-08-11T12:00:00Z {codex_review.OBSERVER_MARKER}{comment_id}\n".encode()


def _legacy_request() -> dict[str, object]:
    request = _request()
    request["body"] = f"@codex review\n\nCodex-Review-Head: {HEAD}"
    return request


def _review(*, user_id: int = codex_review.CONNECTOR_ID, head: str = HEAD) -> dict[str, object]:
    return {
        "commit_id": head,
        "submitted_at": COMPLETED,
        "user": {"id": user_id},
    }


def _summary(*, user_id: int = codex_review.CONNECTOR_ID, head: str = HEAD) -> dict[str, object]:
    return {
        "body": (
            "Codex Review: Didn't find any major issues. Delightful!\n\n"
            f"**Reviewed commit:** `{head[:10]}`"
        ),
        "created_at": COMPLETED,
        "updated_at": COMPLETED,
        "user": {"id": user_id},
    }


def _opener(
    comments: list[dict[str, object]],
    reactions: list[dict[str, object]] | None = None,
    reviews: list[dict[str, object]] | None = None,
    jobs: list[dict[str, object]] | None = None,
    log: bytes | None = None,
) -> Callable[..., _Reply]:
    sources = {
        "comments": comments,
        "reactions": reactions or [],
        "reviews": reviews or [],
    }

    def open_request(request: Any, **kwargs: object) -> _Reply:
        assert kwargs == {"timeout": 30}
        url = urllib.parse.urlparse(request.full_url)
        if url.path.endswith("logs"):
            return _Reply(log or _log())
        page = int(urllib.parse.parse_qs(url.query)["page"][0])
        if url.path.endswith("jobs"):
            start = (page - 1) * 100
            return _Reply(_jobs((jobs or [])[start : start + 100]))
        key = next(name for name in sources if url.path.endswith(name))
        start = (page - 1) * 100
        return _Reply(sources[key][start : start + 100])

    return open_request


def _verify(opener: Callable[..., _Reply]) -> None:
    codex_review.require_completion(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=1,
        delay=0,
        opener=opener,
        sleeper=lambda _: None,
    )


def _handshake_opener(*, clean_summary: bool = False) -> Callable[..., _Reply]:
    reaction_calls = 0

    def open_request(request: Any, **kwargs: object) -> _Reply:
        nonlocal reaction_calls
        assert kwargs == {"timeout": 30}
        path = urllib.parse.urlparse(request.full_url).path
        if path.endswith("comments"):
            comments = [_request()]
            if clean_summary and reaction_calls:
                comments.append(_summary())
            return _Reply(comments)
        if path.endswith("reactions"):
            reaction_calls += 1
            return _Reply([_reaction(content="eyes")] if reaction_calls == 1 else [])
        if path.endswith("jobs"):
            return _Reply(_jobs([_observer()]))
        if path.endswith("logs"):
            return _Reply(_log())
        if path.endswith("reviews"):
            return _Reply([] if reaction_calls == 1 else [_review()])
        raise AssertionError(path)

    return open_request


@pytest.mark.parametrize(
    ("comments", "reactions", "code"),
    [
        ([], [], "MISSING_CODEX_REVIEW_REQUEST"),
        ([_request(OLD_HEAD)], [], "STALE_CODEX_REVIEW_REQUEST"),
        ([_request()], [_reaction(content="eyes")], "CODEX_REVIEW_PENDING"),
        ([_request()], [_reaction(user_id=1)], "CODEX_REVIEW_PENDING"),
        ([_request(run_id=RUN_ID - 1)], [], "STALE_CODEX_REVIEW_REQUEST"),
        ([_request(user_id=1)], [], "MISSING_CODEX_REVIEW_REQUEST"),
        ([_request(updated_at=COMPLETED)], [], "MALFORMED_CODEX_REVIEW_REQUEST"),
        ([_request(), _request(comment_id=2)], [], "MALFORMED_CODEX_REVIEW_REQUEST"),
    ],
)
def test_missing_stale_pending_and_spoofed_evidence_block(
    comments: list[dict[str, object]],
    reactions: list[dict[str, object]],
    code: str,
) -> None:
    with pytest.raises(codex_review.CodexReviewError, match=code):
        _verify(_opener(comments, reactions))


def test_trusted_exact_request_comment_thumbsup_passes() -> None:
    opener = _opener([_request()], [_reaction()])
    urls: list[str] = []

    def record(request: Any, **kwargs: object) -> _Reply:
        urls.append(request.full_url)
        return opener(request, **kwargs)

    _verify(record)

    assert any("/issues/comments/1/reactions?" in url for url in urls)


def test_unacknowledged_exact_head_submitted_review_blocks() -> None:
    with pytest.raises(codex_review.CodexReviewError, match="CODEX_REVIEW_PENDING"):
        _verify(_opener([_request()], reviews=[_review()]))


def test_unacknowledged_clean_summary_blocks() -> None:
    with pytest.raises(codex_review.CodexReviewError, match="CODEX_REVIEW_PENDING"):
        _verify(_opener([_request(), _summary()]))


def test_replacement_request_does_not_reuse_observer_evidence() -> None:
    with pytest.raises(codex_review.CodexReviewError, match="CODEX_REVIEW_PENDING"):
        _verify(
            _opener(
                [_request()],
                reviews=[_review()],
                jobs=[_observer()],
                log=_log(comment_id=2),
            )
        )


def test_acknowledged_exact_request_submitted_review_passes() -> None:
    codex_review.require_completion(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=2,
        delay=0,
        opener=_handshake_opener(),
        sleeper=lambda _: None,
    )


def test_acknowledged_exact_request_clean_summary_passes() -> None:
    codex_review.require_completion(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=2,
        delay=0,
        opener=_handshake_opener(clean_summary=True),
        sleeper=lambda _: None,
    )


def test_connector_eyes_are_observed_without_repository_write() -> None:
    requests: list[Any] = []

    def opener(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        assert kwargs == {"timeout": 30}
        path = urllib.parse.urlparse(request.full_url).path
        if path.endswith("jobs"):
            return _Reply(_jobs())
        if path.endswith("comments"):
            return _Reply([_request()])
        if path.endswith("reactions"):
            return _Reply([_reaction(content="eyes")])
        raise AssertionError(path)

    comment_id = codex_review.require_acknowledgement(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=1,
        delay=0,
        opener=opener,
        sleeper=lambda _: None,
    )

    assert comment_id == 1
    assert all(request.get_method() == "GET" for request in requests)


def test_exact_request_thumbsup_does_not_require_eyes() -> None:
    comment_id = codex_review.require_acknowledgement(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=1,
        delay=0,
        opener=_opener([_request()], [_reaction()]),
        sleeper=lambda _: None,
    )

    assert comment_id == 1


def test_prior_successful_observer_attempt_is_durable() -> None:
    comment_id = codex_review.require_acknowledgement(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=1,
        delay=0,
        opener=_opener([], jobs=[_observer()]),
        sleeper=lambda _: None,
    )

    assert comment_id == 1


def test_log_redirect_drops_github_authorization_and_requires_https() -> None:
    handler = codex_review._NoAuthRedirect()
    request = codex_review.urllib.request.Request(
        "https://api.github.com/example", headers={"Authorization": "Bearer token"}
    )

    redirected = handler.redirect_request(
        request, None, 302, "Found", {}, "https://blob.example/log"
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") is None
    with pytest.raises(codex_review.CodexReviewError, match="GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://blob.example/log")


def test_legacy_head_only_request_does_not_override_exact_run_request() -> None:
    _verify(_opener([_legacy_request(), _request(comment_id=2)], [_reaction()]))


def test_api_failure_blocks() -> None:
    def fail(*args: object, **kwargs: object) -> _Reply:
        raise urllib.error.URLError("offline")

    with pytest.raises(codex_review.CodexReviewError, match="GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE"):
        _verify(fail)


def test_paginated_exact_head_request_is_found() -> None:
    comments = [{"body": "ordinary", "created_at": REQUESTED, "id": item} for item in range(100)]
    comments.append(_request(comment_id=101))

    _verify(_opener(comments, [_reaction()]))
