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


class _Reply:
    def __init__(self, value: object) -> None:
        self.value = value

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.value).encode()


def _request(
    head: str = HEAD, *, comment_id: int = 1, updated_at: str = REQUESTED
) -> dict[str, object]:
    return {
        "body": f"@codex review\n\nCodex-Review-Head: {head}",
        "created_at": REQUESTED,
        "id": comment_id,
        "updated_at": updated_at,
    }


def _reaction(
    *, user_id: int = codex_review.CONNECTOR_ID, content: str = "+1"
) -> dict[str, object]:
    return {
        "content": content,
        "created_at": COMPLETED,
        "user": {"id": user_id},
    }


def _review(*, user_id: int = codex_review.CONNECTOR_ID, head: str = HEAD) -> dict[str, object]:
    return {
        "commit_id": head,
        "submitted_at": COMPLETED,
        "user": {"id": user_id},
    }


def _opener(
    comments: list[dict[str, object]],
    reactions: list[dict[str, object]] | None = None,
    reviews: list[dict[str, object]] | None = None,
) -> Callable[..., _Reply]:
    sources = {
        "comments": comments,
        "reactions": reactions or [],
        "reviews": reviews or [],
    }

    def open_request(request: Any, **kwargs: object) -> _Reply:
        assert kwargs == {"timeout": 30}
        url = urllib.parse.urlparse(request.full_url)
        page = int(urllib.parse.parse_qs(url.query)["page"][0])
        key = next(name for name in sources if url.path.endswith(name))
        start = (page - 1) * 100
        return _Reply(sources[key][start : start + 100])

    return open_request


def _verify(opener: Callable[..., _Reply]) -> None:
    codex_review.require_completion(
        "example/repository",
        7,
        HEAD,
        "token",
        attempts=1,
        delay=0,
        opener=opener,
        sleeper=lambda _: None,
    )


@pytest.mark.parametrize(
    ("comments", "reactions", "code"),
    [
        ([], [], "MISSING_CODEX_REVIEW_REQUEST"),
        ([_request(OLD_HEAD)], [], "STALE_CODEX_REVIEW_REQUEST"),
        ([_request()], [_reaction(content="eyes")], "CODEX_REVIEW_PENDING"),
        ([_request()], [_reaction(user_id=1)], "CODEX_REVIEW_PENDING"),
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


def test_trusted_exact_head_submitted_review_passes() -> None:
    _verify(_opener([_request()], reviews=[_review()]))


def test_api_failure_blocks() -> None:
    def fail(*args: object, **kwargs: object) -> _Reply:
        raise urllib.error.URLError("offline")

    with pytest.raises(codex_review.CodexReviewError, match="GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE"):
        _verify(fail)


def test_paginated_exact_head_request_is_found() -> None:
    comments = [{"body": "ordinary", "created_at": REQUESTED, "id": item} for item in range(100)]
    comments.append(_request(comment_id=101))

    _verify(_opener(comments, [_reaction()]))
