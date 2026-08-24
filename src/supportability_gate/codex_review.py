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
FOCUS_PREFIX = "Codex-Review-Focus: "
CLEAN_PREFIX = "Codex Review: Didn't find any major issues."
OBSERVER_JOB = "Observe Codex Review"
OBSERVER_MARKER = "CODEX_REVIEW_ACKNOWLEDGED:"
FOCUSED_OBSERVER_MARKER = "CODEX_FOCUSED_REVIEW_ACKNOWLEDGED:"
WORKFLOW_NAME = "Organization Required Supportability Gate"
SHA = re.compile(r"[0-9a-f]{40}\Z")
REVIEWED_COMMIT = re.compile(r"(?m)^\*\*Reviewed commit:\*\* `([0-9a-f]{10})`$")
MAX_PAGES = 10
POLL_ATTEMPTS = 30
POLL_SECONDS = 15
FOCUSED_POLL_ATTEMPTS = 120
FOCUSED_REVIEWS = (
    (
        "2",
        "@codex review for mixed responsibilities or unclear single ownership in changed code only",
    ),
    (
        "4",
        "@codex review for weak domain ownership, low cohesion, avoidable coupling, "
        "or unjustified module boundaries only",
    ),
    (
        "8",
        "@codex review for unsupported, contradictory, stale, incomplete, or misleading "
        "handoff claims about change, boundaries, validation, risk, or gate coverage only",
    ),
)
FOCUSES = tuple(item[0] for item in FOCUSED_REVIEWS)


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


@dataclass(frozen=True)
class CompletionArtifact:
    """One trusted connector completion artifact."""

    kind: str
    artifact_id: int
    completed_at: datetime


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


def _validate_focused_order(requests: dict[str, FocusedReviewRequest]) -> None:
    present = tuple(focus for focus in FOCUSES if focus in requests)
    if set(requests) != set(present) or present != FOCUSES[: len(present)]:
        raise CodexReviewError("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_REQUEST")
    ordered = tuple(requests[focus] for focus in present)
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.created_at >= current.created_at or previous.comment_id >= current.comment_id:
            raise CodexReviewError("OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_REQUEST")


def _focused_review_requests(
    comments: tuple[dict[str, Any], ...], head_sha: str, run_id: int
) -> tuple[dict[str, FocusedReviewRequest], bool]:
    requests: dict[str, FocusedReviewRequest] = {}
    stale = False
    for comment in comments:
        if not _trusted_owner(comment):
            continue
        values = _focused_request_values(comment.get("body"))
        if values is None:
            continue
        focus, requested_head, requested_run = values
        if (requested_head, requested_run) != (head_sha, run_id):
            stale = True
            continue
        if focus not in FOCUSES:
            raise CodexReviewError("UNFOCUSED_CODEX_REVIEW_REQUEST")
        created_at = _timestamp(comment.get("created_at"))
        if created_at != _timestamp(comment.get("updated_at")) or focus in requests:
            raise CodexReviewError("MALFORMED_FOCUSED_CODEX_REVIEW_REQUEST")
        requests[focus] = FocusedReviewRequest(focus, _positive_id(comment), created_at)
    _validate_focused_order(requests)
    return requests, stale


def _required_focused_requests(
    comments: tuple[dict[str, Any], ...], head_sha: str, run_id: int
) -> tuple[FocusedReviewRequest, ...]:
    requests, stale = _focused_review_requests(comments, head_sha, run_id)
    missing = next((focus for focus in FOCUSES if focus not in requests), None)
    if missing is not None:
        prefix = "STALE" if stale and not requests else "MISSING"
        raise CodexReviewError(f"{prefix}_FOCUSED_CODEX_REVIEW_REQUEST_{missing}")
    return tuple(requests[focus] for focus in FOCUSES)


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
) -> str | None:
    root = f"https://api.github.com/repos/{repository}"
    comments = _pages(f"{root}/issues/{pull_number}/comments", token, opener)
    requests = _required_focused_requests(comments, head_sha, run_id)
    acknowledged = _focused_observer_comment_ids(repository, run_id, head_sha, token, opener)
    if acknowledged is None:
        return "FOCUSED_CODEX_REVIEW_PENDING_2"
    if acknowledged != tuple(item.comment_id for item in requests):
        raise CodexReviewError("GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE")
    reviews = _pages(f"{root}/pulls/{pull_number}/reviews", token, opener)
    artifacts: list[CompletionArtifact] = []
    for index, request in enumerate(requests):
        reactions = _pages(f"{root}/issues/comments/{request.comment_id}/reactions", token, opener)
        completed_before = requests[index + 1].created_at if index + 1 < len(requests) else None
        artifact = _focused_completion_artifact(
            request,
            head_sha,
            comments,
            reactions,
            reviews,
            True,
            completed_before,
        )
        if artifact is None:
            return f"FOCUSED_CODEX_REVIEW_PENDING_{request.focus}"
        artifacts.append(artifact)
    if len({(item.kind, item.artifact_id) for item in artifacts}) != len(FOCUSES):
        raise CodexReviewError("REUSED_FOCUSED_CODEX_REVIEW_EVIDENCE")
    return None


def _observed_eyes(reactions: tuple[dict[str, Any], ...], request: FocusedReviewRequest) -> bool:
    return any(
        _trusted_user(reaction)
        and reaction.get("content") == "eyes"
        and _timestamp(reaction.get("created_at")) >= request.created_at
        for reaction in reactions
    )


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
    root = f"https://api.github.com/repos/{repository}"
    acknowledged: dict[str, int] = {}
    last_code = "MISSING_FOCUSED_CODEX_REVIEW_REQUEST_2"
    for attempt in range(attempts):
        try:
            durable = _focused_observer_comment_ids(repository, run_id, head_sha, token, opener)
            if durable is not None:
                return durable
            comments = _pages(f"{root}/issues/{pull_number}/comments", token, opener)
            requests, stale = _focused_review_requests(comments, head_sha, run_id)
            for focus, request in requests.items():
                reactions = _pages(
                    f"{root}/issues/comments/{request.comment_id}/reactions", token, opener
                )
                if _observed_eyes(reactions, request) or any(
                    _reaction_artifact(reaction, request, None) is not None
                    for reaction in reactions
                ):
                    acknowledged[focus] = request.comment_id
            missing = next((focus for focus in FOCUSES if focus not in requests), None)
            if missing is not None:
                prefix = "STALE" if stale and not requests else "MISSING"
                last_code = f"{prefix}_FOCUSED_CODEX_REVIEW_REQUEST_{missing}"
            else:
                pending = next(
                    (
                        focus
                        for focus in FOCUSES
                        if acknowledged.get(focus) != requests[focus].comment_id
                    ),
                    None,
                )
                if pending is None:
                    return tuple(requests[focus].comment_id for focus in FOCUSES)
                last_code = f"FOCUSED_CODEX_REVIEW_PENDING_{pending}"
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
) -> None:
    """Require three distinct observer-bound focused connector completions."""
    last_code = "FOCUSED_CODEX_REVIEW_PENDING_2"
    for attempt in range(attempts):
        try:
            block = _focused_state(
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
