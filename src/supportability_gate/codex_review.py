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
REQUESTER_ID = 229662739
HEAD_PREFIX = "Codex-Review-Head: "
RUN_PREFIX = "Codex-Review-Run: "
CLEAN_PREFIX = "Codex Review: Didn't find any major issues."
OBSERVER_JOB = "Observe Codex Review"
OBSERVER_MARKER = "CODEX_REVIEW_ACKNOWLEDGED:"
WORKFLOW_NAME = "Organization Required Supportability Gate"
SHA = re.compile(r"[0-9a-f]{40}\Z")
REVIEWED_COMMIT = re.compile(r"(?m)^\*\*Reviewed commit:\*\* `([0-9a-f]{10})`$")
MAX_PAGES = 10
POLL_ATTEMPTS = 30
POLL_SECONDS = 15


class CodexReviewError(ValueError):
    """One fail-closed Codex review evidence error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS log redirects without leaking the GitHub bearer token."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        if urllib.parse.urlparse(new_url).scheme != "https":
            raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
        redirected = super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


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


def _job_page(
    repository: str, run_id: int, token: str, page: int, opener: Any
) -> list[dict[str, Any]]:
    root = f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs?filter=all"
    request = urllib.request.Request(
        f"{root}&per_page=100&page={page}",
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
    jobs = value.get("jobs") if isinstance(value, dict) else None
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
    return jobs


def _observer_jobs(
    repository: str, run_id: int, head_sha: str, token: str, opener: Any
) -> tuple[dict[str, Any], ...]:
    matches: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        jobs = _job_page(repository, run_id, token, page, opener)
        matches.extend(
            job
            for job in jobs
            if job.get("run_id") == run_id
            and job.get("head_sha") == head_sha
            and job.get("workflow_name") == WORKFLOW_NAME
            and job.get("name") == OBSERVER_JOB
            and job.get("conclusion") == "success"
        )
        if len(jobs) < 100:
            return tuple(matches)
    raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")


def _observer_log(repository: str, job_id: int, token: str, opener: Any) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/jobs/{job_id}/logs",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    log_opener = (
        urllib.request.build_opener(_NoAuthRedirect()).open
        if opener is urllib.request.urlopen
        else opener
    )
    try:
        with log_opener(request, timeout=30) as response:
            content: bytes = response.read()
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE") from error
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE") from error


def _observer_comment_id(
    repository: str, run_id: int, head_sha: str, token: str, opener: Any
) -> int | None:
    identifiers: set[int] = set()
    pattern = re.compile(rf"(?m)^\S+ {OBSERVER_MARKER}([1-9][0-9]*)\r?$")
    for job in _observer_jobs(repository, run_id, head_sha, token, opener):
        job_id = job.get("id")
        if type(job_id) is not int:
            raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
        matches = pattern.findall(_observer_log(repository, job_id, token, opener))
        if len(matches) != 1:
            raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
        identifiers.add(int(matches[0]))
    if len(identifiers) > 1:
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
    return next(iter(identifiers), None)


def _request_values(body: object) -> tuple[str, int] | None:
    if not isinstance(body, str):
        return None
    commands = [line.strip() for line in body.splitlines() if line.strip() == "@codex review"]
    heads = [
        line.removeprefix(HEAD_PREFIX) for line in body.splitlines() if line.startswith(HEAD_PREFIX)
    ]
    runs = [
        line.removeprefix(RUN_PREFIX) for line in body.splitlines() if line.startswith(RUN_PREFIX)
    ]
    if not heads and not runs:
        return None
    if len(commands) == 1 and len(heads) == 1 and not runs and SHA.fullmatch(heads[0]) is not None:
        return heads[0], 0
    if (
        len(commands) != 1
        or len(heads) != 1
        or len(runs) != 1
        or SHA.fullmatch(heads[0]) is None
        or not runs[0].isdigit()
        or int(runs[0]) < 1
    ):
        raise CodexReviewError("MALFORMED_CODEX_REVIEW_REQUEST")
    return heads[0], int(runs[0])


def _review_request(
    comments: tuple[dict[str, Any], ...], head_sha: str, run_id: int
) -> ReviewRequest:
    candidates: list[ReviewRequest] = []
    stale = False
    for comment in comments:
        user = comment.get("user")
        if not isinstance(user, dict) or user.get("id") != REQUESTER_ID:
            continue
        request_values = _request_values(comment.get("body"))
        if request_values is None:
            continue
        if request_values != (head_sha, run_id):
            stale = True
            continue
        comment_id = comment.get("id")
        if type(comment_id) is not int:
            raise CodexReviewError("MALFORMED_CODEX_REVIEW_EVIDENCE")
        created_at = _timestamp(comment.get("created_at"))
        if created_at != _timestamp(comment.get("updated_at")):
            raise CodexReviewError("MALFORMED_CODEX_REVIEW_REQUEST")
        candidates.append(ReviewRequest(comment_id, created_at))
    if not candidates:
        raise CodexReviewError(
            "STALE_CODEX_REVIEW_REQUEST" if stale else "MISSING_CODEX_REVIEW_REQUEST"
        )
    if len(candidates) != 1:
        raise CodexReviewError("MALFORMED_CODEX_REVIEW_REQUEST")
    return candidates[0]


def _trusted_user(item: dict[str, Any]) -> bool:
    user = item.get("user")
    return isinstance(user, dict) and user.get("id") == CONNECTOR_ID


def _clean_summary(item: dict[str, Any], request: ReviewRequest, head_sha: str) -> bool:
    body = item.get("body")
    match = REVIEWED_COMMIT.search(body) if isinstance(body, str) else None
    return (
        _trusted_user(item)
        and isinstance(body, str)
        and body.startswith(CLEAN_PREFIX)
        and match is not None
        and match.group(1) == head_sha[:10]
        and _timestamp(item.get("created_at")) >= request.created_at
    )


def _completed(
    request: ReviewRequest,
    head_sha: str,
    comments: tuple[dict[str, Any], ...],
    reactions: tuple[dict[str, Any], ...],
    reviews: tuple[dict[str, Any], ...],
    acknowledged: bool,
) -> bool:
    for reaction in reactions:
        if (
            _trusted_user(reaction)
            and reaction.get("content") == "+1"
            and _timestamp(reaction.get("created_at")) >= request.created_at
        ):
            return True
    if not acknowledged or any(
        _trusted_user(reaction) and reaction.get("content") == "eyes" for reaction in reactions
    ):
        return False
    if any(_clean_summary(comment, request, head_sha) for comment in comments):
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


def _state(
    repository: str,
    pull_number: int,
    head_sha: str,
    run_id: int,
    token: str,
    opener: Any,
) -> str | None:
    root = f"https://api.github.com/repos/{repository}"
    comments = _pages(f"{root}/issues/{pull_number}/comments", token, opener)
    request = _review_request(comments, head_sha, run_id)
    reactions = _pages(f"{root}/issues/comments/{request.comment_id}/reactions", token, opener)
    acknowledged_comment = _observer_comment_id(repository, run_id, head_sha, token, opener)
    reviews = _pages(f"{root}/pulls/{pull_number}/reviews", token, opener)
    complete = _completed(
        request,
        head_sha,
        comments,
        reactions,
        reviews,
        acknowledged_comment == request.comment_id,
    )
    return None if complete else "CODEX_REVIEW_PENDING"


def require_acknowledgement(
    repository: str,
    pull_number: int,
    head_sha: str,
    run_id: int,
    token: str,
    *,
    attempts: int = POLL_ATTEMPTS,
    delay: int = POLL_SECONDS,
    opener: Any = urllib.request.urlopen,
    sleeper: Any = time.sleep,
) -> int:
    """Observe trusted connector acknowledgement on the exact request."""
    root = f"https://api.github.com/repos/{repository}"
    last_code = "CODEX_REVIEW_PENDING"
    for attempt in range(attempts):
        try:
            acknowledged_comment = _observer_comment_id(repository, run_id, head_sha, token, opener)
            if acknowledged_comment is not None:
                return acknowledged_comment
            comments = _pages(f"{root}/issues/{pull_number}/comments", token, opener)
            request = _review_request(comments, head_sha, run_id)
            endpoint = f"{root}/issues/comments/{request.comment_id}/reactions"
            reactions = _pages(endpoint, token, opener)
            if any(
                _trusted_user(reaction)
                and reaction.get("content") == "+1"
                and _timestamp(reaction.get("created_at")) >= request.created_at
                for reaction in reactions
            ):
                return request.comment_id
            if any(
                _trusted_user(reaction) and reaction.get("content") == "eyes"
                for reaction in reactions
            ):
                return request.comment_id
        except CodexReviewError as error:
            if error.code == "GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE":
                raise
            last_code = error.code
        if attempt + 1 < attempts:
            sleeper(delay)
    raise CodexReviewError(last_code)


def require_completion(
    repository: str,
    pull_number: int,
    head_sha: str,
    run_id: int,
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
            block = _state(
                repository,
                pull_number,
                head_sha,
                run_id,
                token,
                opener,
            )
        except CodexReviewError as error:
            last_code = error.code
        else:
            if block is None:
                return
            last_code = block
        if attempt + 1 < attempts:
            sleeper(delay)
    raise CodexReviewError(last_code)
