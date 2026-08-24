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
FOCUS_REQUEST_TIMES = {
    str(focus): f"2026-08-11T12:{(focus - 1) * 2:02d}:00Z" for focus in range(1, 9)
}
FOCUS_COMPLETION_TIMES = {
    str(focus): f"2026-08-11T12:{(focus - 1) * 2 + 1:02d}:00Z" for focus in range(1, 9)
}
FOCUS_REQUEST_IDS = {str(focus): 20 + focus for focus in range(1, 9)}
FOCUS_ARTIFACT_IDS = {str(focus): 100 * focus + 1 for focus in range(1, 9)}


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


def _focused_request(
    focus: str,
    *,
    comment_id: int | None = None,
    created_at: str | None = None,
    head: str = HEAD,
    run_id: int = RUN_ID,
    updated_at: str | None = None,
    user_id: int = codex_review.REQUESTER_ID,
) -> dict[str, object]:
    requested_at = created_at or FOCUS_REQUEST_TIMES[focus]
    return {
        "body": (
            f"{dict(codex_review.FOCUSED_REVIEWS)[focus]}\n\n"
            f"Codex-Review-Focus: {focus}\n"
            f"Codex-Review-Head: {head}\n"
            f"Codex-Review-Run: {run_id}"
        ),
        "created_at": requested_at,
        "id": comment_id or FOCUS_REQUEST_IDS[focus],
        "updated_at": updated_at or requested_at,
        "user": {"id": user_id},
    }


def _focused_reaction(
    focus: str,
    *,
    artifact_id: int | None = None,
    content: str = "+1",
) -> dict[str, object]:
    return {
        "content": content,
        "created_at": FOCUS_COMPLETION_TIMES[focus],
        "id": artifact_id or FOCUS_ARTIFACT_IDS[focus],
        "user": {"id": codex_review.CONNECTOR_ID},
    }


def _focused_summary(
    focus: str,
    *,
    updated_at: str | None = None,
) -> dict[str, object]:
    completed_at = FOCUS_COMPLETION_TIMES[focus]
    return {
        "body": (
            "Codex Review: Didn't find any major issues. Delightful!\n\n"
            f"**Reviewed commit:** `{HEAD[:10]}`"
        ),
        "created_at": completed_at,
        "id": FOCUS_ARTIFACT_IDS[focus],
        "updated_at": updated_at or completed_at,
        "user": {"id": codex_review.CONNECTOR_ID},
    }


def _focused_review(focus: str, *, state: str = "COMMENTED") -> dict[str, object]:
    return {
        "commit_id": HEAD,
        "id": FOCUS_ARTIFACT_IDS[focus],
        "state": state,
        "submitted_at": FOCUS_COMPLETION_TIMES[focus],
        "user": {"id": codex_review.CONNECTOR_ID},
    }


def _focused_log(
    bindings: tuple[tuple[str, int], ...] | None = None,
) -> bytes:
    bound = bindings or tuple((focus, FOCUS_REQUEST_IDS[focus]) for focus in codex_review.FOCUSES)
    return "".join(
        f"2026-08-11T12:06:00Z {codex_review.FOCUSED_OBSERVER_MARKER}{focus}:{request_id}\n"
        for focus, request_id in bound
    ).encode()


def _focused_opener(
    comments: list[dict[str, object]],
    *,
    reactions: dict[int, list[dict[str, object]]] | None = None,
    reviews: list[dict[str, object]] | None = None,
    jobs: list[dict[str, object]] | None = None,
    log: bytes | None = None,
) -> Callable[..., _Reply]:
    reaction_rows = reactions or {}
    job_rows = [_observer()] if jobs is None else jobs

    def open_request(request: Any, **kwargs: object) -> _Reply:
        assert kwargs == {"timeout": 30}
        url = urllib.parse.urlparse(request.full_url)
        page = int(urllib.parse.parse_qs(url.query).get("page", ["1"])[0])
        start = (page - 1) * 100
        if url.path.endswith("logs"):
            return _Reply(log or _focused_log())
        if url.path.endswith("jobs"):
            return _Reply(_jobs(job_rows[start : start + 100]))
        if url.path.endswith("comments"):
            return _Reply(comments[start : start + 100])
        if url.path.endswith("reviews"):
            return _Reply((reviews or [])[start : start + 100])
        if url.path.endswith("reactions"):
            comment_id = int(url.path.split("/")[-2])
            return _Reply(reaction_rows.get(comment_id, [])[start : start + 100])
        raise AssertionError(url.path)

    return open_request


def _verify_focused(
    opener: Callable[..., _Reply],
) -> tuple[codex_review.FocusedReviewEvidence, ...]:
    return codex_review.require_focused_completion(
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


@pytest.mark.parametrize("verify", [_verify, _verify_focused], ids=("single", "focused"))
def test_api_failure_blocks(
    verify: Callable[[Callable[..., _Reply]], None],
) -> None:
    def fail(*args: object, **kwargs: object) -> _Reply:
        raise urllib.error.URLError("offline")

    with pytest.raises(codex_review.CodexReviewError, match="GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE"):
        verify(fail)


def test_paginated_exact_head_request_is_found() -> None:
    comments = [{"body": "ordinary", "created_at": REQUESTED, "id": item} for item in range(100)]
    comments.append(_request(comment_id=101))

    _verify(_opener(comments, [_reaction()]))


def test_clean_reaction_and_finding_review_completions_pass() -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]
    comments = [*requests, _focused_summary("1")]
    reactions = {
        FOCUS_REQUEST_IDS[focus]: [_focused_reaction(focus)] for focus in codex_review.FOCUSES[1:-1]
    }

    evidence = _verify_focused(
        _focused_opener(comments, reactions=reactions, reviews=[_focused_review("8")])
    )

    assert tuple(item.focus for item in evidence) == codex_review.FOCUSES
    assert tuple(item.request_id for item in evidence) == tuple(
        FOCUS_REQUEST_IDS[focus] for focus in codex_review.FOCUSES
    )
    assert len({(item.completion.kind, item.completion.artifact_id) for item in evidence}) == 8  # type: ignore[union-attr]


def test_eight_focus_commands_and_order_are_exact() -> None:
    assert codex_review.FOCUSED_REVIEWS == (
        (
            "1",
            "@codex review for maze-like control flow, misleading extraction, or helpers that "
            "lower measured complexity without improving readability, testability, or naming in "
            "changed code only",
        ),
        (
            "2",
            "@codex review for mixed responsibilities or unclear single ownership in changed "
            "code only",
        ),
        (
            "3",
            "@codex review for unclear, inverted, cyclic, or unjustified dependency direction "
            "across changed boundaries only",
        ),
        (
            "4",
            "@codex review for weak domain ownership, low cohesion, avoidable coupling, or "
            "unjustified module boundaries only",
        ),
        (
            "5",
            "@codex review for missing, weak, misleading, or nondeterministic characterization "
            "of behavior at risk from this change only",
        ),
        (
            "6",
            "@codex review for oversized, non-runnable, big-bang, or insufficiently bounded "
            "refactor steps in this change only",
        ),
        (
            "7",
            "@codex review for validation evidence that omits changed or high-risk behavior, "
            "weakens scope or thresholds, hides failures, or overstates what ran only",
        ),
        (
            "8",
            "@codex review for unsupported, contradictory, stale, incomplete, or misleading "
            "handoff claims about change, boundaries, validation, risk, or gate coverage only",
        ),
    )


@pytest.mark.parametrize(
    ("head", "run_id"),
    [(OLD_HEAD, RUN_ID), (HEAD, RUN_ID - 1)],
    ids=("head", "run"),
)
def test_stale_focused_requests_block(head: str, run_id: int) -> None:
    requests = [_focused_request(focus, head=head, run_id=run_id) for focus in codex_review.FOCUSES]

    with pytest.raises(
        codex_review.CodexReviewError,
        match="STALE_FOCUSED_CODEX_REVIEW_REQUEST_1",
    ):
        _verify_focused(_focused_opener(requests))


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("mutable", "MALFORMED_FOCUSED_CODEX_REVIEW_REQUEST"),
        ("spoofed", "MISSING_FOCUSED_CODEX_REVIEW_REQUEST_1"),
        ("malformed", "MALFORMED_FOCUSED_CODEX_REVIEW_REQUEST"),
    ],
)
def test_untrusted_or_mutable_focused_requests_block(case: str, code: str) -> None:
    request = _focused_request("1")
    if case == "mutable":
        request["updated_at"] = COMPLETED
    elif case == "spoofed":
        request["user"] = {"id": 1}
    else:
        request["body"] = str(request["body"]).replace(
            dict(codex_review.FOCUSED_REVIEWS)["1"],
            "@codex review for an unrecognized focus",
        )

    with pytest.raises(codex_review.CodexReviewError, match=code):
        _verify_focused(_focused_opener([request]))


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("missing", "MISSING_FOCUSED_CODEX_REVIEW_REQUEST_8"),
        ("duplicate", "MALFORMED_FOCUSED_CODEX_REVIEW_REQUEST"),
        ("unfocused", "UNFOCUSED_CODEX_REVIEW_REQUEST"),
        ("out_of_order", "OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_REQUEST"),
        ("same_second", "OUT_OF_ORDER_FOCUSED_CODEX_REVIEW_REQUEST"),
    ],
)
def test_invalid_focused_request_sequences_block(case: str, code: str) -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]
    if case == "missing":
        requests.pop()
    elif case == "duplicate":
        requests.append(_focused_request("1", comment_id=99))
    elif case == "unfocused":
        requests.append(_request(comment_id=24))
    elif case == "out_of_order":
        requests[1] = _focused_request("2", created_at="2026-08-11T11:59:00Z")
    else:
        requests[1] = _focused_request("2", created_at=FOCUS_REQUEST_TIMES["1"])

    with pytest.raises(codex_review.CodexReviewError, match=code):
        _verify_focused(_focused_opener(requests))


def test_snapshot_preserves_completed_prefix_when_next_request_is_missing() -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES[:7]]
    reactions = {
        FOCUS_REQUEST_IDS[focus]: [_focused_reaction(focus)] for focus in codex_review.FOCUSES[:7]
    }

    block, evidence = codex_review.focused_completion_snapshot(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        opener=_focused_opener(requests, reactions=reactions, jobs=[]),
    )

    assert block == "MISSING_FOCUSED_CODEX_REVIEW_REQUEST_8"
    assert tuple(item.focus for item in evidence) == codex_review.FOCUSES[:7]
    assert all(item.completion is not None for item in evidence)


def test_one_artifact_cannot_satisfy_multiple_focuses() -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]
    reactions = {
        FOCUS_REQUEST_IDS[focus]: [_focused_reaction(focus, artifact_id=999)]
        for focus in codex_review.FOCUSES
    }
    with pytest.raises(
        codex_review.CodexReviewError,
        match="REUSED_FOCUSED_CODEX_REVIEW_EVIDENCE",
    ):
        _verify_focused(_focused_opener(requests, reactions=reactions))


def test_multiple_valid_focused_artifacts_are_ambiguous() -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]
    comments = [*requests, _focused_summary("1")]
    reactions = {
        FOCUS_REQUEST_IDS[focus]: [_focused_reaction(focus)] for focus in codex_review.FOCUSES[1:]
    }

    with pytest.raises(
        codex_review.CodexReviewError,
        match="AMBIGUOUS_FOCUSED_CODEX_REVIEW_COMPLETION",
    ):
        _verify_focused(
            _focused_opener(comments, reactions=reactions, reviews=[_focused_review("1")])
        )


def test_paginated_focused_requests_are_found() -> None:
    comments: list[dict[str, object]] = [{"body": "ordinary", "id": item} for item in range(100)]
    comments.extend(_focused_request(focus) for focus in codex_review.FOCUSES)
    reactions = {
        FOCUS_REQUEST_IDS[focus]: [_focused_reaction(focus)] for focus in codex_review.FOCUSES
    }

    _verify_focused(_focused_opener(comments, reactions=reactions))


def test_focused_completion_timeout_blocks() -> None:
    delays: list[int] = []
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]

    with pytest.raises(
        codex_review.CodexReviewError,
        match="FOCUSED_CODEX_REVIEW_PENDING_1",
    ):
        codex_review.require_focused_completion(
            "example/repository",
            7,
            HEAD,
            RUN_ID,
            "token",
            attempts=2,
            delay=3,
            opener=_focused_opener(requests),
            sleeper=delays.append,
        )

    assert delays == [3]


@pytest.mark.parametrize(
    "bindings",
    [
        (("2", 22), ("1", 21), ("3", 23), ("4", 24), ("5", 25), ("6", 26), ("7", 27), ("8", 28)),
        (("1", 99), ("2", 22), ("3", 23), ("4", 24), ("5", 25), ("6", 26), ("7", 27), ("8", 28)),
    ],
)
def test_observer_markers_bind_focus_and_request(
    bindings: tuple[tuple[str, int], ...],
) -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]
    reactions = {
        FOCUS_REQUEST_IDS[focus]: [_focused_reaction(focus)] for focus in codex_review.FOCUSES
    }

    with pytest.raises(
        codex_review.CodexReviewError,
        match="GITHUB_CODEX_REVIEW_EVIDENCE_FAILURE",
    ):
        _verify_focused(_focused_opener(requests, reactions=reactions, log=_focused_log(bindings)))


def test_edited_focused_summary_blocks() -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]
    comments = [
        *requests,
        _focused_summary("1", updated_at="2026-08-11T12:01:30Z"),
    ]
    reactions = {
        FOCUS_REQUEST_IDS[focus]: [_focused_reaction(focus)] for focus in codex_review.FOCUSES[1:]
    }
    with pytest.raises(
        codex_review.CodexReviewError,
        match="MALFORMED_CODEX_REVIEW_EVIDENCE",
    ):
        _verify_focused(_focused_opener(comments, reactions=reactions))


def test_dismissed_focused_review_blocks() -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]
    reactions = {
        FOCUS_REQUEST_IDS[focus]: [_focused_reaction(focus)] for focus in codex_review.FOCUSES[1:]
    }
    with pytest.raises(
        codex_review.CodexReviewError,
        match="FOCUSED_CODEX_REVIEW_PENDING_1",
    ):
        _verify_focused(
            _focused_opener(
                requests,
                reactions=reactions,
                reviews=[_focused_review("1", state="DISMISSED")],
            )
        )


def test_focused_observer_is_get_only_and_tracks_serial_acknowledgements() -> None:
    requests: list[Any] = []
    comment_poll = 0

    def opener(request: Any, **kwargs: object) -> _Reply:
        nonlocal comment_poll
        requests.append(request)
        assert kwargs == {"timeout": 30}
        path = urllib.parse.urlparse(request.full_url).path
        if path.endswith("jobs"):
            return _Reply(_jobs())
        if path.endswith("comments"):
            comment_poll += 1
            visible = min((comment_poll + 1) // 2, len(codex_review.FOCUSES))
            return _Reply([_focused_request(focus) for focus in codex_review.FOCUSES[:visible]])
        if path.endswith("reviews"):
            return _Reply([])
        if path.endswith("reactions"):
            comment_id = int(path.split("/")[-2])
            focus = next(
                item for item, identifier in FOCUS_REQUEST_IDS.items() if identifier == comment_id
            )
            first_seen = {focus: index * 2 + 1 for index, focus in enumerate(codex_review.FOCUSES)}
            if comment_poll == first_seen[focus]:
                return _Reply([_focused_reaction(focus, content="eyes")])
            if comment_poll > first_seen[focus]:
                return _Reply([_focused_reaction(focus)])
            return _Reply([])
        raise AssertionError(path)

    comment_ids = codex_review.require_focused_acknowledgements(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=15,
        delay=0,
        opener=opener,
        sleeper=lambda _: None,
    )

    assert comment_ids == tuple(FOCUS_REQUEST_IDS[focus] for focus in codex_review.FOCUSES)
    assert comment_poll == 15
    assert all(request.get_method() == "GET" for request in requests)


def test_focused_completion_waits_for_final_eyes_to_clear() -> None:
    requests = [_focused_request(focus) for focus in codex_review.FOCUSES]
    polls = 0

    def opener(request: Any, **kwargs: object) -> _Reply:
        nonlocal polls
        assert kwargs == {"timeout": 30}
        path = urllib.parse.urlparse(request.full_url).path
        if path.endswith("comments"):
            polls += 1
            return _Reply(requests)
        if path.endswith("jobs"):
            return _Reply(_jobs([_observer()]))
        if path.endswith("logs"):
            return _Reply(_focused_log())
        if path.endswith("reviews"):
            return _Reply([])
        if path.endswith("reactions"):
            comment_id = int(path.split("/")[-2])
            focus = next(
                item for item, identifier in FOCUS_REQUEST_IDS.items() if identifier == comment_id
            )
            content = "eyes" if focus == "8" and polls == 1 else "+1"
            return _Reply([_focused_reaction(focus, content=content)])
        raise AssertionError(path)

    codex_review.require_focused_completion(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=2,
        delay=0,
        opener=opener,
        sleeper=lambda _: None,
    )

    assert polls == 2
