"""Normalize authenticated GitHub pull-request review state."""

from __future__ import annotations

import hashlib
from typing import Any, cast

from supportability_gate.semantic_contract import SHA_PATTERN, SemanticReviewError


def _malformed() -> SemanticReviewError:
    return SemanticReviewError("MALFORMED_REVIEW_STATE")


def _text(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise _malformed()
    return value


def _integer(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _malformed()
    return value


def _optional_integer(item: dict[str, Any], key: str) -> int | None:
    value = item.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise _malformed()
    return value


def _optional_text(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is not None and not isinstance(value, str):
        raise _malformed()
    return value


def _body_sha256(item: dict[str, Any]) -> str:
    body = item.get("body")
    if not isinstance(body, str):
        raise _malformed()
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _actor(item: dict[str, Any]) -> dict[str, object]:
    user = item.get("user")
    if not isinstance(user, dict):
        raise _malformed()
    actor_type = _text(user, "type")
    if actor_type not in {"Bot", "User"}:
        raise _malformed()
    return {
        "id": _integer(user, "id"),
        "login": _text(user, "login"),
        "node_id": _text(user, "node_id"),
        "type": actor_type,
    }


def _app(item: dict[str, Any]) -> dict[str, object] | None:
    app = item.get("performed_via_github_app")
    if app is None:
        return None
    if not isinstance(app, dict):
        raise _malformed()
    owner = app.get("owner")
    owner_id = owner.get("id") if isinstance(owner, dict) else None
    if owner_id is not None and (not isinstance(owner_id, int) or isinstance(owner_id, bool)):
        raise _malformed()
    return {
        "id": _integer(app, "id"),
        "node_id": _text(app, "node_id"),
        "owner_id": owner_id,
        "slug": _text(app, "slug"),
    }


def _review(item: dict[str, Any]) -> dict[str, object]:
    commit = _text(item, "commit_id")
    if SHA_PATTERN.fullmatch(commit) is None:
        raise _malformed()
    return {
        "app": _app(item),
        "author": _actor(item),
        "author_association": _text(item, "author_association"),
        "body_sha256": _body_sha256(item),
        "commit_sha": commit,
        "id": _integer(item, "id"),
        "node_id": _text(item, "node_id"),
        "state": _text(item, "state"),
        "submitted_at": _text(item, "submitted_at"),
    }


def _inline_comment(item: dict[str, Any]) -> dict[str, object]:
    commit = _text(item, "commit_id")
    original_commit = _text(item, "original_commit_id")
    if SHA_PATTERN.fullmatch(commit) is None or SHA_PATTERN.fullmatch(original_commit) is None:
        raise _malformed()
    return {
        "app": _app(item),
        "author": _actor(item),
        "author_association": _text(item, "author_association"),
        "body_sha256": _body_sha256(item),
        "commit_sha": commit,
        "created_at": _text(item, "created_at"),
        "id": _integer(item, "id"),
        "in_reply_to_id": _optional_integer(item, "in_reply_to_id"),
        "line": _optional_integer(item, "line"),
        "node_id": _text(item, "node_id"),
        "original_commit_sha": original_commit,
        "original_line": _optional_integer(item, "original_line"),
        "original_start_line": _optional_integer(item, "original_start_line"),
        "path": _text(item, "path"),
        "review_id": _integer(item, "pull_request_review_id"),
        "side": _optional_text(item, "side"),
        "start_line": _optional_integer(item, "start_line"),
        "start_side": _optional_text(item, "start_side"),
        "subject_type": _text(item, "subject_type"),
        "updated_at": _text(item, "updated_at"),
    }


def _top_comment(item: dict[str, Any]) -> dict[str, object]:
    return {
        "app": _app(item),
        "author": _actor(item),
        "author_association": _text(item, "author_association"),
        "body_sha256": _body_sha256(item),
        "created_at": _text(item, "created_at"),
        "id": _integer(item, "id"),
        "node_id": _text(item, "node_id"),
        "updated_at": _text(item, "updated_at"),
    }


def _indexed(rows: list[dict[str, object]]) -> dict[int, dict[str, object]]:
    indexed: dict[int, dict[str, object]] = {}
    for row in rows:
        identifier = row["id"]
        if not isinstance(identifier, int) or identifier in indexed:
            raise SemanticReviewError("CONFLICTING_REVIEW_IDENTITY")
        indexed[identifier] = row
    return indexed


def _threads(
    raw_threads: tuple[dict[str, Any], ...],
    comments: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    used: set[int] = set()
    normalized: list[dict[str, object]] = []
    thread_ids: set[str] = set()
    for thread in raw_threads:
        thread_id = _text(thread, "id")
        resolved, outdated = thread.get("isResolved"), thread.get("isOutdated")
        nodes = thread.get("comments")
        if (
            thread_id in thread_ids
            or not isinstance(resolved, bool)
            or not isinstance(outdated, bool)
        ):
            raise SemanticReviewError("CONFLICTING_REVIEW_IDENTITY")
        if not isinstance(nodes, list) or not nodes:
            raise _malformed()
        member_ids: list[int] = []
        for node in nodes:
            if not isinstance(node, dict):
                raise _malformed()
            database_id = _integer(node, "databaseId")
            comment = comments.get(database_id)
            if comment is None or comment["node_id"] != _text(node, "id") or database_id in used:
                raise SemanticReviewError("CONFLICTING_REVIEW_IDENTITY")
            used.add(database_id)
            member_ids.append(database_id)
        thread_ids.add(thread_id)
        normalized.append(
            {
                "comment_ids": sorted(member_ids),
                "id": thread_id,
                "is_outdated": outdated,
                "is_resolved": resolved,
            }
        )
    if used != set(comments):
        raise SemanticReviewError("INCOMPLETE_REVIEW_STATE")
    return sorted(normalized, key=lambda row: str(row["id"]))


def normalize_review_state(
    reviews: tuple[dict[str, Any], ...],
    raw_threads: tuple[dict[str, Any], ...],
    inline_comments: tuple[dict[str, Any], ...],
    top_comments: tuple[dict[str, Any], ...],
) -> dict[str, object]:
    """Return one byte-stable, cross-checked review-state snapshot."""
    normalized_reviews = [_review(item) for item in reviews]
    normalized_inline = [_inline_comment(item) for item in inline_comments]
    review_index = _indexed(normalized_reviews)
    inline_index = _indexed(normalized_inline)
    if any(comment["review_id"] not in review_index for comment in normalized_inline):
        raise SemanticReviewError("CONFLICTING_REVIEW_IDENTITY")
    if any(
        reply is not None and reply not in inline_index
        for comment in normalized_inline
        if (reply := comment["in_reply_to_id"]) is not None
    ):
        raise SemanticReviewError("CONFLICTING_REVIEW_IDENTITY")
    normalized_top = [_top_comment(item) for item in top_comments]
    _indexed(normalized_top)
    return {
        "inline_comments": sorted(normalized_inline, key=lambda row: cast(int, row["id"])),
        "reviews": sorted(normalized_reviews, key=lambda row: cast(int, row["id"])),
        "schema_version": "review-state.v1",
        "threads": _threads(raw_threads, inline_index),
        "top_level_comments": sorted(normalized_top, key=lambda row: cast(int, row["id"])),
    }


def unresolved_review_blocks(evidence: dict[str, Any]) -> tuple[str, ...]:
    """Return deterministic blocks for current unresolved GitHub threads."""
    state = evidence.get("review_state")
    threads = state.get("threads") if isinstance(state, dict) else None
    if not isinstance(threads, list):
        raise SemanticReviewError("MALFORMED_REVIEW_STATE")
    blocks: list[str] = []
    for thread in threads:
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            raise SemanticReviewError("MALFORMED_REVIEW_STATE")
        if not isinstance(thread.get("is_resolved"), bool):
            raise SemanticReviewError("MALFORMED_REVIEW_STATE")
        if not thread["is_resolved"]:
            blocks.append(f"UNRESOLVED_REVIEW_THREAD:{thread['id']}")
    return tuple(sorted(blocks))
