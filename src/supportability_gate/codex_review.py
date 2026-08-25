"""Require trusted Codex review completion for the exact pull-request head."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from supportability_gate import contract
from supportability_gate.focused_review import (
    FOCUSED_REVIEWS,
    FOCUSES,
    CompletionArtifact,
    FocusedReviewEvidence,
)

CONNECTOR_ID = 199175422
REQUESTER_ID = 229662739
HEAD_PREFIX = "Codex-Review-Head: "
RUN_PREFIX = "Codex-Review-Run: "
FOCUS_PREFIX = "Codex-Review-Focus: "
CLEAN_PREFIX = "Codex Review: Didn't find any major issues."
OBSERVER_JOB = "Observe Codex Review"
OBSERVER_MARKER = "CODEX_REVIEW_ACKNOWLEDGED:"
FOCUSED_OBSERVER_MARKER = "CODEX_FOCUSED_REVIEW_ACKNOWLEDGED:"
FOCUSED_COMPLETION_MARKER = "CODEX_FOCUSED_REVIEW_COMPLETED:"
REMEDIATION_PREFIX = "Codex-Remediation-Authorization: "
REMEDIATION_SCHEMA = "1.0"
WORKFLOW_NAME = "Organization Required Supportability Gate"
SHA = re.compile(r"[0-9a-f]{40}\Z")
REVIEWED_COMMIT = re.compile(r"(?m)^\*\*Reviewed commit:\*\* `([0-9a-f]{10})`$")
MAX_PAGES = 10
POLL_ATTEMPTS = 30
POLL_SECONDS = 15
FOCUSED_POLL_ATTEMPTS = 240
FOCUSED_RETRY_TIMEOUT = timedelta(seconds=POLL_ATTEMPTS * POLL_SECONDS)


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


@dataclass(frozen=True)
class FocusedReviewRequest:
    """One immutable focused request for the exact head and run."""

    focus: str
    comment_id: int
    created_at: datetime
    head_sha: str
    run_id: int


def _positive_integer(value: object) -> bool:
    return type(value) is int and value > 0


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


def _object(endpoint: str, token: str, opener: Any) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
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
    if not isinstance(value, dict):
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
    return value


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


def _focused_observer_comment_ids(
    repository: str, run_id: int, head_sha: str, token: str, opener: Any
) -> tuple[int, ...] | None:
    results: set[tuple[int, ...]] = set()
    focuses = "|".join(FOCUSES)
    pattern = re.compile(rf"(?m)^\S+ {FOCUSED_OBSERVER_MARKER}({focuses}):([1-9][0-9]*)\r?$")
    for job in _observer_jobs(repository, run_id, head_sha, token, opener):
        job_id = job.get("id")
        if type(job_id) is not int:
            raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
        matches = pattern.findall(_observer_log(repository, job_id, token, opener))
        if len(matches) != len(FOCUSES) or tuple(item[0] for item in matches) != FOCUSES:
            raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
        results.add(tuple(int(item[1]) for item in matches))
    if len(results) > 1:
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
    return next(iter(results), None)


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


def _focused_request_values(body: object) -> tuple[str, str, int] | None:
    if not isinstance(body, str):
        return None
    lines = body.splitlines()
    commands = [line.strip() for line in lines if line.strip().startswith("@codex review")]
    heads = [line.removeprefix(HEAD_PREFIX) for line in lines if line.startswith(HEAD_PREFIX)]
    runs = [line.removeprefix(RUN_PREFIX) for line in lines if line.startswith(RUN_PREFIX)]
    focuses = [line.removeprefix(FOCUS_PREFIX) for line in lines if line.startswith(FOCUS_PREFIX)]
    if not commands and not heads and not runs and not focuses:
        return None
    if (
        len(commands) != 1
        or len(heads) != 1
        or SHA.fullmatch(heads[0]) is None
        or len(runs) > 1
        or (runs and (not runs[0].isdigit() or int(runs[0]) < 1))
    ):
        raise CodexReviewError("MALFORMED_FOCUSED_CODEX_REVIEW_REQUEST")
    if commands[0] == "@codex review" and not focuses:
        return "", heads[0], int(runs[0]) if runs else 0
    if len(runs) != 1 or len(focuses) != 1:
        raise CodexReviewError("MALFORMED_FOCUSED_CODEX_REVIEW_REQUEST")
    commands_by_focus = dict(FOCUSED_REVIEWS)
    if focuses[0] not in commands_by_focus or commands[0] != commands_by_focus[focuses[0]]:
        raise CodexReviewError("MALFORMED_FOCUSED_CODEX_REVIEW_REQUEST")
    return focuses[0], heads[0], int(runs[0])


def _positive_id(item: dict[str, Any]) -> int:
    identifier = item.get("id")
    if type(identifier) is not int or identifier < 1:
        raise CodexReviewError("MALFORMED_CODEX_REVIEW_EVIDENCE")
    return identifier


def _focused_review_requests(
    comments: tuple[dict[str, Any], ...],
) -> tuple[FocusedReviewRequest, ...]:
    requests: list[FocusedReviewRequest] = []
    for comment in comments:
        if not _trusted_owner(comment):
            continue
        values = _focused_request_values(comment.get("body"))
        if values is None:
            continue
        focus, requested_head, requested_run = values
        if focus not in FOCUSES:
            raise CodexReviewError("UNFOCUSED_CODEX_REVIEW_REQUEST")
        created_at = _timestamp(comment.get("created_at"))
        if created_at != _timestamp(comment.get("updated_at")):
            raise CodexReviewError("MALFORMED_FOCUSED_CODEX_REVIEW_REQUEST")
        requests.append(
            FocusedReviewRequest(
                focus,
                _positive_id(comment),
                created_at,
                requested_head,
                requested_run,
            )
        )
    ordered = tuple(sorted(requests, key=lambda item: (item.created_at, item.comment_id)))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.created_at >= current.created_at or previous.comment_id >= current.comment_id:
            raise CodexReviewError("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_REQUEST")
    return ordered


def _lifecycle_requests(
    requests: tuple[FocusedReviewRequest, ...], head_sha: str, run_id: int
) -> tuple[FocusedReviewRequest, ...]:
    return tuple(
        request for request in requests if (request.head_sha, request.run_id) == (head_sha, run_id)
    )


def _trusted_owner(item: dict[str, Any]) -> bool:
    user = item.get("user")
    return isinstance(user, dict) and user.get("id") == REQUESTER_ID


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


def _in_focus_window(
    completed_at: datetime,
    request: FocusedReviewRequest,
    completed_before: datetime | None,
) -> bool:
    return request.created_at < completed_at and (
        completed_before is None or completed_at < completed_before
    )


def _reaction_artifact(
    item: dict[str, Any],
    request: FocusedReviewRequest,
    completed_before: datetime | None,
) -> CompletionArtifact | None:
    if not _trusted_user(item) or item.get("content") != "+1":
        return None
    completed_at = _timestamp(item.get("created_at"))
    if not _in_focus_window(completed_at, request, completed_before):
        return None
    return CompletionArtifact("reaction", _positive_id(item), completed_at)


def _summary_artifact(
    item: dict[str, Any],
    request: FocusedReviewRequest,
    head_sha: str,
    completed_before: datetime | None,
) -> CompletionArtifact | None:
    body = item.get("body")
    match = REVIEWED_COMMIT.search(body) if isinstance(body, str) else None
    if (
        not _trusted_user(item)
        or not isinstance(body, str)
        or not body.startswith(CLEAN_PREFIX)
        or match is None
        or match.group(1) != head_sha[:10]
    ):
        return None
    completed_at = _timestamp(item.get("created_at"))
    if completed_at != _timestamp(item.get("updated_at")):
        raise CodexReviewError("MALFORMED_CODEX_REVIEW_EVIDENCE")
    if not _in_focus_window(completed_at, request, completed_before):
        return None
    return CompletionArtifact("comment", _positive_id(item), completed_at)


def _submitted_review_artifact(
    item: dict[str, Any],
    request: FocusedReviewRequest,
    head_sha: str,
    completed_before: datetime | None,
) -> CompletionArtifact | None:
    if not _trusted_user(item) or item.get("commit_id") != head_sha:
        return None
    if item.get("state") not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}:
        return None
    completed_at = _timestamp(item.get("submitted_at"))
    if not _in_focus_window(completed_at, request, completed_before):
        return None
    return CompletionArtifact("review", _positive_id(item), completed_at)


def _one_artifact(artifacts: list[CompletionArtifact]) -> CompletionArtifact | None:
    if len(artifacts) > 1:
        raise CodexReviewError("AMBIGUOUS_FOCUSED_CODEX_REVIEW_COMPLETION")
    return artifacts[0] if artifacts else None


def _focused_completion_artifact(
    request: FocusedReviewRequest,
    head_sha: str,
    comments: tuple[dict[str, Any], ...],
    reactions: tuple[dict[str, Any], ...],
    reviews: tuple[dict[str, Any], ...],
    acknowledged: bool,
    completed_before: datetime | None,
) -> CompletionArtifact | None:
    direct = [
        artifact
        for item in reactions
        if (artifact := _reaction_artifact(item, request, completed_before)) is not None
    ]
    if direct:
        return _one_artifact(direct)
    if not acknowledged or any(
        _trusted_user(reaction) and reaction.get("content") == "eyes" for reaction in reactions
    ):
        return None
    artifacts = [
        artifact
        for item in comments
        if (artifact := _summary_artifact(item, request, head_sha, completed_before)) is not None
    ]
    artifacts.extend(
        artifact
        for item in reviews
        if (artifact := _submitted_review_artifact(item, request, head_sha, completed_before))
        is not None
    )
    return _one_artifact(artifacts)


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


def _focused_state(
    repository: str,
    pull_number: int,
    head_sha: str,
    run_id: int,
    token: str,
    opener: Any,
) -> tuple[str | None, tuple[FocusedReviewEvidence, ...]]:
    root = f"https://api.github.com/repos/{repository}"
    comments = _pages(f"{root}/issues/{pull_number}/comments", token, opener)
    requests = _focused_review_requests(comments)
    reviews = _pages(f"{root}/pulls/{pull_number}/reviews", token, opener)
    reused = _reusable_lifecycle(
        repository, root, pull_number, head_sha, run_id, requests, comments, reviews, token, opener
    )
    if reused is not None:
        return None, reused
    current = _lifecycle_requests(requests, head_sha, run_id)
    if not current:
        prefix = "STALE" if requests else "MISSING"
        return f"{prefix}_FOCUSED_CODEX_REVIEW_REQUEST_{FOCUSES[0]}", ()
    if len(current) != len(requests):
        raise CodexReviewError("STALE_FOCUSED_CODEX_REVIEW_REQUEST")
    acknowledged = _focused_observer_comment_ids(repository, run_id, head_sha, token, opener)
    if acknowledged is None:
        return f"FOCUSED_CODEX_REVIEW_PENDING_{FOCUSES[0]}", ()
    reactions = _focused_reactions(root, current, token, opener)
    block, evidence, pending = _evaluate_lifecycle(
        current,
        head_sha,
        comments,
        reviews,
        reactions,
        frozenset(acknowledged),
    )
    bound = tuple(item.request_id for item in evidence)
    if pending is not None:
        bound += (pending.comment_id,)
    if acknowledged != bound:
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
    return block, evidence


def _remediation_paths(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION")
    try:
        paths = tuple(
            contract.normalize_repository_path(item, "remediation.scope") for item in value
        )
    except contract.ContractError as error:
        raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION") from error
    if list(paths) != sorted(set(paths)):
        raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION")
    return paths


def _parse_remediation(body: object) -> dict[str, Any] | None:
    if not isinstance(body, str):
        return None
    rows = [
        line.removeprefix(REMEDIATION_PREFIX)
        for line in body.splitlines()
        if line.startswith(REMEDIATION_PREFIX)
    ]
    if not rows:
        return None
    if len(rows) != 1:
        raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION")
    try:
        value: object = json.loads(rows[0])
    except json.JSONDecodeError as error:
        raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION") from error
    keys = {
        "current_head_sha",
        "current_run_id",
        "pull_number",
        "repository",
        "review_head_sha",
        "review_run_id",
        "schema_version",
        "scope",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION")
    valid = all(
        (
            value["schema_version"] == REMEDIATION_SCHEMA,
            isinstance(value["repository"], str),
            _positive_integer(value["pull_number"]),
            _positive_integer(value["review_run_id"]),
            _positive_integer(value["current_run_id"]),
            isinstance(value["review_head_sha"], str),
            SHA.fullmatch(str(value["review_head_sha"])) is not None,
            isinstance(value["current_head_sha"], str),
            SHA.fullmatch(str(value["current_head_sha"])) is not None,
        )
    )
    if not valid:
        raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION")
    value["scope"] = _remediation_paths(value["scope"])
    return value


def _remediation_authorization(
    comments: tuple[dict[str, Any], ...],
    expected: tuple[str, int, str, int, str, int],
    completed_at: datetime,
) -> tuple[str, ...]:
    candidates: list[dict[str, Any]] = []
    seen = False
    for comment in comments:
        authorization = _parse_remediation(comment.get("body"))
        if authorization is None:
            continue
        seen = True
        if not _trusted_owner(comment):
            raise CodexReviewError("UNAUTHENTICATED_CODEX_REMEDIATION_AUTHORIZATION")
        created_at = _timestamp(comment.get("created_at"))
        if (
            created_at != _timestamp(comment.get("updated_at"))
            or created_at <= completed_at
            or not _positive_integer(comment.get("id"))
        ):
            raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION")
        if (authorization["current_head_sha"], authorization["current_run_id"]) == (
            expected[4],
            expected[5],
        ):
            candidates.append(authorization)
    if not candidates:
        code = (
            "STALE_CODEX_REMEDIATION_AUTHORIZATION"
            if seen
            else "MISSING_CODEX_REMEDIATION_AUTHORIZATION"
        )
        raise CodexReviewError(code)
    if len(candidates) != 1:
        raise CodexReviewError("MALFORMED_CODEX_REMEDIATION_AUTHORIZATION")
    authorization = candidates[0]
    if (
        authorization["repository"],
        authorization["pull_number"],
        authorization["review_head_sha"],
        authorization["review_run_id"],
        authorization["current_head_sha"],
        authorization["current_run_id"],
    ) != expected:
        raise CodexReviewError("CODEX_REMEDIATION_AUTHORIZATION_MISMATCH")
    return cast(tuple[str, ...], authorization["scope"])


def _comparison_scope(files: object) -> tuple[str, ...]:
    if not isinstance(files, list) or not files or len(files) >= 300:
        raise CodexReviewError("UNSAFE_CODEX_REMEDIATION_DELTA")
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or item.get("status") not in {
            "added",
            "changed",
            "copied",
            "modified",
            "removed",
            "renamed",
            "unchanged",
        }:
            raise CodexReviewError("UNSAFE_CODEX_REMEDIATION_DELTA")
        try:
            paths.add(contract.normalize_repository_path(item.get("filename"), "compare.filename"))
            if item.get("status") == "renamed":
                paths.add(
                    contract.normalize_repository_path(
                        item.get("previous_filename"), "compare.previous_filename"
                    )
                )
            elif "previous_filename" in item:
                raise CodexReviewError("UNSAFE_CODEX_REMEDIATION_DELTA")
        except contract.ContractError as error:
            raise CodexReviewError("UNSAFE_CODEX_REMEDIATION_DELTA") from error
    return tuple(sorted(paths))


def _remediation_scope(
    root: str, reviewed_head: str, current_head: str, token: str, opener: Any
) -> tuple[str, ...]:
    value = _object(f"{root}/compare/{reviewed_head}...{current_head}", token, opener)
    commits = value.get("commits")
    base = value.get("base_commit")
    merge_base = value.get("merge_base_commit")
    ahead = value.get("ahead_by")
    if not _positive_integer(ahead) or not isinstance(commits, list):
        raise CodexReviewError("UNSAFE_CODEX_REMEDIATION_DELTA")
    valid = all(
        (
            value.get("status") == "ahead",
            type(value.get("behind_by")) is int and value.get("behind_by") == 0,
            value.get("total_commits") == ahead,
            len(commits) == value.get("total_commits"),
            isinstance(base, dict) and base.get("sha") == reviewed_head,
            isinstance(merge_base, dict) and merge_base.get("sha") == reviewed_head,
        )
    )
    if (
        not valid
        or not commits
        or any(
            not isinstance(item, dict) or SHA.fullmatch(str(item.get("sha"))) is None
            for item in commits
        )
    ):
        raise CodexReviewError("UNSAFE_CODEX_REMEDIATION_DELTA")
    if commits[-1].get("sha") != current_head:
        raise CodexReviewError("UNSAFE_CODEX_REMEDIATION_DELTA")
    return _comparison_scope(value.get("files"))


def _reusable_lifecycle(
    repository: str,
    root: str,
    pull_number: int,
    head_sha: str,
    run_id: int,
    requests: tuple[FocusedReviewRequest, ...],
    comments: tuple[dict[str, Any], ...],
    reviews: tuple[dict[str, Any], ...],
    token: str,
    opener: Any,
) -> tuple[FocusedReviewEvidence, ...] | None:
    historical = tuple(
        request for request in requests if (request.head_sha, request.run_id) != (head_sha, run_id)
    )
    completed = _completed_lifecycles(
        repository, root, historical, comments, reviews, token, opener
    )
    if len(completed) > 1:
        raise CodexReviewError("REPEATED_COMPLETED_CODEX_REVIEW_LIFECYCLE")
    if not completed:
        return None
    reviewed_head, reviewed_run, evidence = completed[0]
    if any(
        (request.head_sha, request.run_id) != (reviewed_head, reviewed_run) for request in requests
    ):
        raise CodexReviewError("REPEATED_COMPLETED_CODEX_REVIEW_LIFECYCLE")
    expected = (
        repository,
        pull_number,
        reviewed_head,
        reviewed_run,
        head_sha,
        run_id,
    )
    completed_at = max(item.completion.completed_at for item in evidence)
    authorized_scope = _remediation_authorization(comments, expected, completed_at)
    scope = _remediation_scope(root, reviewed_head, head_sha, token, opener)
    if authorized_scope != scope:
        raise CodexReviewError("CODEX_REMEDIATION_SCOPE_MISMATCH")
    return evidence


def _focused_reactions(
    root: str,
    requests: tuple[FocusedReviewRequest, ...],
    token: str,
    opener: Any,
) -> dict[int, tuple[dict[str, Any], ...]]:
    return {
        request.comment_id: _pages(
            f"{root}/issues/comments/{request.comment_id}/reactions", token, opener
        )
        for request in requests
    }


def _evaluate_lifecycle(
    requests: tuple[FocusedReviewRequest, ...],
    head_sha: str,
    comments: tuple[dict[str, Any], ...],
    reviews: tuple[dict[str, Any], ...],
    reactions: dict[int, tuple[dict[str, Any], ...]],
    acknowledged: frozenset[int],
) -> tuple[str | None, tuple[FocusedReviewEvidence, ...], FocusedReviewRequest | None]:
    evidence: list[FocusedReviewEvidence] = []
    pending: FocusedReviewRequest | None = None
    expected = 0
    for index, request in enumerate(requests):
        focus_index = FOCUSES.index(request.focus)
        if focus_index < expected:
            raise CodexReviewError(f"REPEATED_COMPLETED_FOCUSED_CODEX_REVIEW_{request.focus}")
        if focus_index > expected:
            raise CodexReviewError("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_REQUEST")
        completed_before = requests[index + 1].created_at if index + 1 < len(requests) else None
        artifact = _focused_completion_artifact(
            request,
            head_sha,
            comments,
            reactions[request.comment_id],
            reviews,
            request.comment_id in acknowledged,
            completed_before,
        )
        if artifact is None:
            retry = requests[index + 1] if index + 1 < len(requests) else None
            if (
                retry is not None
                and retry.focus == request.focus
                and retry.created_at - request.created_at < FOCUSED_RETRY_TIMEOUT
            ):
                raise CodexReviewError(f"PREMATURE_FOCUSED_CODEX_REVIEW_RETRY_{request.focus}")
            pending = request
            continue
        evidence.append(
            FocusedReviewEvidence(request.focus, request.comment_id, request.created_at, artifact)
        )
        pending = None
        expected += 1
    identities = [(item.completion.kind, item.completion.artifact_id) for item in evidence]
    if len(identities) != len(set(identities)):
        raise CodexReviewError("REUSED_FOCUSED_CODEX_REVIEW_EVIDENCE")
    if expected == len(FOCUSES):
        return None, tuple(evidence), None
    if pending is not None:
        return f"FOCUSED_CODEX_REVIEW_PENDING_{pending.focus}", tuple(evidence), pending
    return f"MISSING_FOCUSED_CODEX_REVIEW_REQUEST_{FOCUSES[expected]}", tuple(evidence), None


def _completed_lifecycles(
    repository: str,
    root: str,
    requests: tuple[FocusedReviewRequest, ...],
    comments: tuple[dict[str, Any], ...],
    reviews: tuple[dict[str, Any], ...],
    token: str,
    opener: Any,
) -> list[tuple[str, int, tuple[FocusedReviewEvidence, ...]]]:
    results: list[tuple[str, int, tuple[FocusedReviewEvidence, ...]]] = []
    keys = tuple(dict.fromkeys((request.head_sha, request.run_id) for request in requests))
    for lifecycle_head, lifecycle_run in keys:
        acknowledged = _focused_observer_comment_ids(
            repository, lifecycle_run, lifecycle_head, token, opener
        )
        if acknowledged is None:
            continue
        current = _lifecycle_requests(requests, lifecycle_head, lifecycle_run)
        reactions = _focused_reactions(root, current, token, opener)
        block, evidence, pending = _evaluate_lifecycle(
            current,
            lifecycle_head,
            comments,
            reviews,
            reactions,
            frozenset(acknowledged),
        )
        bound = tuple(item.request_id for item in evidence)
        if pending is not None:
            bound += (pending.comment_id,)
        if acknowledged != bound:
            raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
        if block is None:
            results.append((lifecycle_head, lifecycle_run, evidence))
    return results


def _observed_eyes(reactions: tuple[dict[str, Any], ...], request: FocusedReviewRequest) -> bool:
    return any(
        _trusted_user(reaction)
        and reaction.get("content") == "eyes"
        and _timestamp(reaction.get("created_at")) >= request.created_at
        for reaction in reactions
    )


def _focused_acknowledgement_state(
    repository: str,
    pull_number: int,
    head_sha: str,
    run_id: int,
    token: str,
    opener: Any,
    acknowledged: set[int],
) -> tuple[str | None, tuple[int, ...]]:
    root = f"https://api.github.com/repos/{repository}"
    comments = _pages(f"{root}/issues/{pull_number}/comments", token, opener)
    requests = _focused_review_requests(comments)
    reviews = _pages(f"{root}/pulls/{pull_number}/reviews", token, opener)
    reused = _reusable_lifecycle(
        repository, root, pull_number, head_sha, run_id, requests, comments, reviews, token, opener
    )
    if reused is not None:
        return None, tuple(item.request_id for item in reused)
    current = _lifecycle_requests(requests, head_sha, run_id)
    if not current:
        prefix = "STALE" if requests else "MISSING"
        return f"{prefix}_FOCUSED_CODEX_REVIEW_REQUEST_{FOCUSES[0]}", ()
    if len(current) != len(requests):
        raise CodexReviewError("STALE_FOCUSED_CODEX_REVIEW_REQUEST")
    reactions = _focused_reactions(root, current, token, opener)
    for request in current:
        rows = reactions[request.comment_id]
        if _observed_eyes(rows, request) or any(
            _reaction_artifact(reaction, request, None) is not None for reaction in rows
        ):
            acknowledged.add(request.comment_id)
    block, evidence, pending = _evaluate_lifecycle(
        current,
        head_sha,
        comments,
        reviews,
        reactions,
        frozenset(acknowledged),
    )
    bound = tuple(item.request_id for item in evidence)
    if pending is not None:
        bound += (pending.comment_id,)
    if block is None and len(bound) == len(FOCUSES) and all(item in acknowledged for item in bound):
        return None, bound
    return block or f"FOCUSED_CODEX_REVIEW_PENDING_{FOCUSES[-1]}", ()


def require_focused_acknowledgements(
    repository: str,
    pull_number: int,
    head_sha: str,
    run_id: int,
    token: str,
    *,
    attempts: int = FOCUSED_POLL_ATTEMPTS,
    delay: int = POLL_SECONDS,
    opener: Any = urllib.request.urlopen,
    sleeper: Any = time.sleep,
) -> tuple[int, ...]:
    """Observe connector acknowledgement on each serial focused request."""
    acknowledged: set[int] = set()
    last_code = f"MISSING_FOCUSED_CODEX_REVIEW_REQUEST_{FOCUSES[0]}"
    for attempt in range(attempts):
        try:
            durable = _focused_observer_comment_ids(repository, run_id, head_sha, token, opener)
            if durable is not None:
                return durable
            block, bound = _focused_acknowledgement_state(
                repository,
                pull_number,
                head_sha,
                run_id,
                token,
                opener,
                acknowledged,
            )
            if block is None:
                return bound
            last_code = block
        except CodexReviewError as error:
            if error.code == "GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE":
                raise
            last_code = error.code
        if attempt + 1 < attempts:
            sleeper(delay)
    raise CodexReviewError(last_code)


def require_focused_completion(
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
) -> tuple[FocusedReviewEvidence, ...]:
    """Require one eight-focus lifecycle, reusing it after remediation."""
    last_code = f"FOCUSED_CODEX_REVIEW_PENDING_{FOCUSES[0]}"
    for attempt in range(attempts):
        try:
            block, evidence = _focused_state(
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
                return evidence
            last_code = block
        if attempt + 1 < attempts:
            sleeper(delay)
    raise CodexReviewError(last_code)
