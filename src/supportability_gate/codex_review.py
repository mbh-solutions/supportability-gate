"""Require trusted Codex review completion for the exact pull-request head."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CONNECTOR_ID = 199175422
HEAD_PREFIX = "Codex-Review-Head: "
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_PAGES = 10
POLL_ATTEMPTS = 30
POLL_SECONDS = 15


class CodexReviewError(ValueError):
    """One fail-closed Codex review evidence error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReviewRequest:
    """One exact-head Codex review request."""

    comment_id: int
    created_at: datetime


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise CodexReviewError("MALFORMED_CODEX_REVIEW_EVIDENCE")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CodexReviewError("MALFORMED_CODEX_REVIEW_EVIDENCE") from error
    if parsed.tzinfo is None:
        raise CodexReviewError("MALFORMED_CODEX_REVIEW_EVIDENCE")
    return parsed


def _page(
    endpoint: str,
    token: str,
    page: int,
    opener: Any,
) -> list[dict[str, Any]]:
    separator = "&" if "?" in endpoint else "?"
    request = urllib.request.Request(
        f"{endpoint}{separator}per_page=100&page={page}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request, timeout=30) as response:
            value: object = json.loads(response.read())
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE") from error
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
    return value


def _pages(endpoint: str, token: str, opener: Any) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        current = _page(endpoint, token, page, opener)
        rows.extend(current)
        if len(current) < 100:
            return tuple(rows)
    raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")


def _request_head(body: object) -> str | None:
    if not isinstance(body, str):
        return None
    commands = [line.strip() for line in body.splitlines() if line.strip() == "@codex review"]
    markers = [
        line.removeprefix(HEAD_PREFIX) for line in body.splitlines() if line.startswith(HEAD_PREFIX)
    ]
    if not markers:
        return None
    if len(commands) != 1 or len(markers) != 1 or SHA.fullmatch(markers[0]) is None:
        raise CodexReviewError("MALFORMED_CODEX_REVIEW_REQUEST")
    return markers[0]


def _review_request(comments: tuple[dict[str, Any], ...], head_sha: str) -> ReviewRequest:
    candidates: list[ReviewRequest] = []
    stale = False
    for comment in comments:
        request_head = _request_head(comment.get("body"))
        if request_head is None:
            continue
        if request_head != head_sha:
            stale = True
            continue
        comment_id = comment.get("id")
        if type(comment_id) is not int:
            raise CodexReviewError("MALFORMED_CODEX_REVIEW_EVIDENCE")
        candidates.append(ReviewRequest(comment_id, _timestamp(comment.get("created_at"))))
    if not candidates:
        raise CodexReviewError(
            "STALE_CODEX_REVIEW_REQUEST" if stale else "MISSING_CODEX_REVIEW_REQUEST"
        )
    return max(candidates, key=lambda item: (item.created_at, item.comment_id))


def _trusted_user(item: dict[str, Any]) -> bool:
    user = item.get("user")
    return isinstance(user, dict) and user.get("id") == CONNECTOR_ID


def _completed(
    request: ReviewRequest,
    head_sha: str,
    reactions: tuple[dict[str, Any], ...],
    reviews: tuple[dict[str, Any], ...],
) -> bool:
    for reaction in reactions:
        if (
            _trusted_user(reaction)
            and reaction.get("content") == "+1"
            and _timestamp(reaction.get("created_at")) >= request.created_at
        ):
            return True
    for review in reviews:
        if (
            _trusted_user(review)
            and review.get("commit_id") == head_sha
            and review.get("submitted_at") is not None
            and _timestamp(review.get("submitted_at")) >= request.created_at
        ):
            return True
    return False


def _state(repository: str, pull_number: int, head_sha: str, token: str, opener: Any) -> str | None:
    root = f"https://api.github.com/repos/{repository}"
    comments = _pages(f"{root}/issues/{pull_number}/comments", token, opener)
    request = _review_request(comments, head_sha)
    reactions = _pages(
        f"{root}/issues/comments/{request.comment_id}/reactions?"
        f"{urllib.parse.urlencode({'content': '+1'})}",
        token,
        opener,
    )
    reviews = _pages(f"{root}/pulls/{pull_number}/reviews", token, opener)
    return None if _completed(request, head_sha, reactions, reviews) else "CODEX_REVIEW_PENDING"


def require_completion(
    repository: str,
    pull_number: int,
    head_sha: str,
    token: str,
    *,
    attempts: int = POLL_ATTEMPTS,
    delay: int = POLL_SECONDS,
    opener: Any = urllib.request.urlopen,
    sleeper: Any = time.sleep,
) -> None:
    """Wait a bounded time for trusted exact-head Codex review completion."""
    last_code = "CODEX_REVIEW_PENDING"
    for attempt in range(attempts):
        try:
            block = _state(repository, pull_number, head_sha, token, opener)
        except CodexReviewError as error:
            last_code = error.code
        else:
            if block is None:
                return
            last_code = block
        if attempt + 1 < attempts:
            sleeper(delay)
    raise CodexReviewError(last_code)
