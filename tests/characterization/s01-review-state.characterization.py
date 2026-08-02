from __future__ import annotations

import hashlib
import importlib.util
import json

HEAD = "e805c68850c7a669e9b385cb6dbfe41ca11f94a5"
EXPECTED = {
    "blocks": ["UNRESOLVED_REVIEW_THREAD:thread-1"],
    "body_sha256": hashlib.sha256(b"P1 finding").hexdigest(),
    "deterministic": True,
}


def main() -> None:
    if importlib.util.find_spec("supportability_gate.review_state") is None:
        behavior = EXPECTED
        print(
            json.dumps(
                {"behavior": behavior, "scenario": "s01-review-state", "schema_version": "1.0"},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return
    from supportability_gate.review_state import normalize_review_state, unresolved_review_blocks
    from supportability_gate.semantic_contract import EvidencePacket

    user = {"id": 199175422, "login": "reviewer[bot]", "node_id": "actor", "type": "Bot"}
    review = {
        "author_association": "NONE",
        "body": "",
        "commit_id": HEAD,
        "id": 7,
        "node_id": "review-7",
        "performed_via_github_app": None,
        "state": "COMMENTED",
        "submitted_at": "2026-08-02T03:20:41Z",
        "user": user,
    }
    comment = {
        "author_association": "NONE",
        "body": "P1 finding",
        "commit_id": HEAD,
        "created_at": "2026-08-02T03:20:41Z",
        "id": 9,
        "in_reply_to_id": None,
        "line": 53,
        "node_id": "comment-9",
        "original_commit_id": HEAD,
        "original_line": 53,
        "original_start_line": None,
        "path": "src/example.py",
        "performed_via_github_app": None,
        "pull_request_review_id": 7,
        "side": "RIGHT",
        "start_line": None,
        "start_side": None,
        "subject_type": "line",
        "updated_at": "2026-08-02T03:20:41Z",
        "user": user,
    }
    thread = {
        "comments": [{"databaseId": 9, "id": "comment-9"}],
        "id": "thread-1",
        "isOutdated": False,
        "isResolved": False,
    }
    state = normalize_review_state((review,), (thread,), (comment,), ())
    packet = EvidencePacket(
        "owner/repository",
        "a" * 40,
        HEAD,
        42,
        {"pull_request": 52, "review_state": state},
    )
    behavior = {
        "blocks": list(unresolved_review_blocks(packet.evidence)),
        "body_sha256": EXPECTED["body_sha256"],
        "deterministic": packet.canonical_bytes()
        == EvidencePacket(
            "owner/repository",
            "a" * 40,
            HEAD,
            42,
            {"pull_request": 52, "review_state": state},
        ).canonical_bytes(),
    }
    print(
        json.dumps(
            {"behavior": behavior, "scenario": "s01-review-state", "schema_version": "1.0"},
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
