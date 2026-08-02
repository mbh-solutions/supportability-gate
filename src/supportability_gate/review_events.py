"""Authenticate and normalize GitHub review-state webhook deliveries."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

from supportability_gate.semantic_contract import REPOSITORY_PATTERN, SemanticReviewError

EVENT_ACTIONS = {
    "pull_request_review": frozenset({"submitted", "edited", "dismissed"}),
    "pull_request_review_comment": frozenset({"created", "edited", "deleted"}),
    "pull_request_review_thread": frozenset({"resolved", "unresolved"}),
}


@dataclass(frozen=True)
class ReviewEvent:
    """Authenticated pointer to a pull request requiring fresh reconciliation."""

    repository: str
    pull_number: int
    delivery_id: str


def _authenticated(body: bytes, signature: str, secret: bytes) -> None:
    if not secret or not isinstance(signature, str):
        raise SemanticReviewError("WEBHOOK_AUTHENTICATION_FAILURE")
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SemanticReviewError("WEBHOOK_AUTHENTICATION_FAILURE")


def _payload(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SemanticReviewError("MALFORMED_REVIEW_EVENT") from error
    if not isinstance(value, dict):
        raise SemanticReviewError("MALFORMED_REVIEW_EVENT")
    return value


def parse_review_event(
    body: bytes,
    *,
    event_name: str,
    delivery_id: str,
    signature: str,
    secret: bytes,
) -> ReviewEvent:
    """Verify one GitHub delivery and return only its reconciliation identity."""
    _authenticated(body, signature, secret)
    payload = _payload(body)
    repository = payload.get("repository")
    pull = payload.get("pull_request")
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    pull_number = pull.get("number") if isinstance(pull, dict) else None
    if (
        payload.get("action") not in EVENT_ACTIONS.get(event_name, ())
        or not isinstance(delivery_id, str)
        or not delivery_id
        or len(delivery_id) > 200
        or not isinstance(full_name, str)
        or REPOSITORY_PATTERN.fullmatch(full_name) is None
        or not isinstance(pull_number, int)
        or isinstance(pull_number, bool)
        or pull_number <= 0
    ):
        raise SemanticReviewError("MALFORMED_REVIEW_EVENT")
    return ReviewEvent(full_name, pull_number, delivery_id)
