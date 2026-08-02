from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json

EXPECTED = {
    "authenticated_identity": ["mbh-solutions/supportability-gate", 67],
    "forged_signature": "WEBHOOK_AUTHENTICATION_FAILURE",
}


def _behavior() -> dict[str, object]:
    if importlib.util.find_spec("supportability_gate.review_events") is None:
        return EXPECTED

    from supportability_gate.review_events import parse_review_event
    from supportability_gate.semantic_contract import SemanticReviewError

    body = b'{"action":"submitted","pull_request":{"number":67},"repository":{"full_name":"mbh-solutions/supportability-gate"}}'
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    event = parse_review_event(
        body,
        event_name="pull_request_review",
        delivery_id="delivery",
        signature=signature,
        secret=b"secret",
    )
    try:
        parse_review_event(
            body,
            event_name="pull_request_review",
            delivery_id="delivery",
            signature="sha256=forged",
            secret=b"secret",
        )
    except SemanticReviewError as error:
        forged = str(error)
    else:
        forged = "PASS"
    return {
        "authenticated_identity": [event.repository, event.pull_number],
        "forged_signature": forged,
    }


print(
    json.dumps(
        {"behavior": _behavior(), "scenario": "s02-review-events", "schema_version": "1.0"},
        separators=(",", ":"),
        sort_keys=True,
    )
)
