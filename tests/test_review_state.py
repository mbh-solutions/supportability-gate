from __future__ import annotations

import copy
import hashlib
from typing import Any

import pytest

from supportability_gate import semantic_cli
from supportability_gate.github_app import GitHubApp
from supportability_gate.review_state import normalize_review_state
from supportability_gate.semantic_contract import EvidencePacket, SemanticReviewError

P1 = """**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Require a source for schema 2 builds

When callers select `schema_version="2.0"` but omit `source_id`, this passes `None` into `_build_database`, which iterates every transcript source and performs the full-corpus expansion that `.supportability-review.toml` explicitly says remains prohibited and unproved. Reject this combination before building so the new schema cannot silently exceed the authorized representative-source scope.

Useful? React with 👍 / 👎."""


def _user(
    identifier: int = 199175422, login: str = "chatgpt-codex-connector[bot]"
) -> dict[str, object]:
    return {"id": identifier, "login": login, "node_id": f"actor-{identifier}", "type": "Bot"}


def _review(identifier: int = 4836889742) -> dict[str, object]:
    return {
        "author_association": "NONE",
        "body": "",
        "commit_id": "e805c68850c7a669e9b385cb6dbfe41ca11f94a5",
        "id": identifier,
        "node_id": f"review-{identifier}",
        "performed_via_github_app": None,
        "state": "COMMENTED",
        "submitted_at": "2026-08-02T03:20:41Z",
        "user": _user(),
    }


def _inline(identifier: int = 3697594742, body: str = P1) -> dict[str, object]:
    return {
        "author_association": "NONE",
        "body": body,
        "commit_id": "e805c68850c7a669e9b385cb6dbfe41ca11f94a5",
        "created_at": "2026-08-02T03:20:41Z",
        "id": identifier,
        "in_reply_to_id": None,
        "line": 53,
        "node_id": f"comment-{identifier}",
        "original_commit_id": "e805c68850c7a669e9b385cb6dbfe41ca11f94a5",
        "original_line": 53,
        "original_start_line": None,
        "path": "src/twmn_corpus/contextual_index_store.py",
        "performed_via_github_app": None,
        "pull_request_review_id": 4836889742,
        "side": "RIGHT",
        "start_line": None,
        "start_side": None,
        "subject_type": "line",
        "updated_at": "2026-08-02T03:20:41Z",
        "user": _user(),
    }


def _top(body: str = "ordinary comment") -> dict[str, object]:
    return {
        "author_association": "NONE",
        "body": body,
        "created_at": "2026-08-02T03:19:00Z",
        "id": 10,
        "node_id": "top-10",
        "performed_via_github_app": None,
        "updated_at": "2026-08-02T03:19:00Z",
        "user": _user(),
    }


def _state(*, resolved: bool = False, body: str = P1) -> dict[str, object]:
    return normalize_review_state(
        (_review(),),
        (
            {
                "comments": [{"databaseId": 3697594742, "id": "comment-3697594742"}],
                "id": "PRRT_kwDOTUxMsc6VtT-J",
                "isOutdated": False,
                "isResolved": resolved,
            },
        ),
        (_inline(body=body),),
        (_top(),),
    )


def _packet(state: dict[str, object]) -> EvidencePacket:
    return EvidencePacket(
        "mbh-solutions/twmn",
        "9e0c490f1fac9a46eca52f9fdfcc9a14e684bed4",
        "e805c68850c7a669e9b385cb6dbfe41ca11f94a5",
        4418989,
        {"pull_request": 52, "review_state": state, "reviewed_sources": []},
    )


def test_pr52_omission_is_bound_and_blocks_before_model(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _packet({"threads": []})
    packet = _packet(_state())
    assert old.sha256 != packet.sha256
    assert (
        packet.evidence["review_state"]["inline_comments"][0]["body_sha256"]
        == hashlib.sha256(P1.encode()).hexdigest()
    )

    class App:
        published: list[tuple[object, ...]] = []

        def m10_evidence_packet(self, *args: object) -> EvidencePacket:
            return packet

        def assert_current(self, *args: object) -> None:
            return None

        def replay_result(self, *args: object) -> bool:
            pytest.fail("unresolved state must block before replay")

        def publish_check(self, *args: object) -> None:
            self.published.append(args)

    app = App()
    monkeypatch.setattr(
        semantic_cli, "request_response", lambda *args: pytest.fail("model transport called")
    )
    assert not semantic_cli._review(app, "mbh-solutions/twmn", "token", {})  # type: ignore[arg-type]
    assert "UNRESOLVED_REVIEW_THREAD:PRRT_kwDOTUxMsc6VtT-J" in str(app.published[0][3])


def test_review_state_changes_digest_and_unchanged_state_is_deterministic() -> None:
    base = _state()
    assert _packet(base).canonical_bytes() == _packet(copy.deepcopy(base)).canonical_bytes()
    variants = [_state(resolved=True), _state(body=P1 + " edited")]
    added = copy.deepcopy(base)
    added["top_level_comments"].append({**added["top_level_comments"][0], "id": 11})  # type: ignore[index,union-attr]
    deleted = copy.deepcopy(base)
    deleted["top_level_comments"] = []
    reopened = _state(resolved=False)
    for state in (*variants, added, deleted):
        assert _packet(state).sha256 != _packet(base).sha256
    assert _packet(reopened).sha256 != _packet(_state(resolved=True)).sha256


def test_structured_app_identity_cannot_be_spoofed_by_text() -> None:
    spoof = _top('performed_via_github_app: {"id": 15368}')
    state = normalize_review_state((_review(),), (), (), (spoof,))
    assert state["top_level_comments"][0]["app"] is None  # type: ignore[index]
    trusted = copy.deepcopy(spoof)
    trusted["performed_via_github_app"] = {
        "id": 15368,
        "node_id": "app-15368",
        "owner": {"id": 229662739},
        "slug": "supportability-gate",
    }
    assert normalize_review_state((_review(),), (), (), (trusted,))["top_level_comments"][0][  # type: ignore[index]
        "app"
    ] == {"id": 15368, "node_id": "app-15368", "owner_id": 229662739, "slug": "supportability-gate"}


def test_conflicting_comment_identity_and_malformed_payload_fail_closed() -> None:
    thread = {
        "comments": [{"databaseId": 3697594742, "id": "wrong-node"}],
        "id": "thread",
        "isOutdated": False,
        "isResolved": False,
    }
    with pytest.raises(SemanticReviewError, match="CONFLICTING_REVIEW_IDENTITY"):
        normalize_review_state((_review(),), (thread,), (_inline(),), ())
    malformed = _inline()
    malformed["user"] = "forged"
    with pytest.raises(SemanticReviewError, match="MALFORMED_REVIEW_STATE"):
        normalize_review_state((_review(),), (), (malformed,), ())


def test_rest_and_graphql_pagination_complete_beyond_100() -> None:
    calls: list[dict[str, Any] | None] = []

    def request(method: str, path: str, token: str, payload: object = None) -> object:
        variables = payload.get("variables") if isinstance(payload, dict) else None
        calls.append(variables)
        if method == "GET":
            return [{}] * 100 if path.endswith("&page=1") else [{}]
        if variables and "thread" in variables:
            return {
                "data": {
                    "node": {
                        "comments": {
                            "nodes": [{"id": "c101", "databaseId": 101}],
                            "pageInfo": {"hasNextPage": False, "endCursor": "done"},
                        }
                    }
                }
            }
        first = variables.get("cursor") is None if variables else False
        nodes = [
            {
                "comments": {
                    "nodes": [{"id": f"c{i}", "databaseId": i} for i in range(1, 101)],
                    "pageInfo": {
                        "hasNextPage": i == 1,
                        "endCursor": "comments-100" if i == 1 else None,
                    },
                },
                "id": f"thread-{i}",
                "isOutdated": False,
                "isResolved": True,
            }
            for i in (range(1, 101) if first else [101])
        ]
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": nodes,
                            "pageInfo": {
                                "hasNextPage": first,
                                "endCursor": "threads-100" if first else "done",
                            },
                        }
                    }
                }
            }
        }

    app = GitHubApp(42, 7, b"unused")
    app._request = request  # type: ignore[method-assign]
    assert len(app._rest_pages("/items?per_page=100", "token")) == 101
    threads = app._review_threads("owner/repo", 52, "token")
    assert len(threads) == 101
    assert len(threads[0]["comments"]) == 101


def test_incomplete_pagination_and_api_failure_fail_closed() -> None:
    app = GitHubApp(42, 7, b"unused")
    app._request = lambda *args, **kwargs: {  # type: ignore[method-assign]
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": True, "endCursor": None},
                    }
                }
            }
        }
    }
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_REVIEW_STATE"):
        app._review_threads("owner/repo", 52, "token")

    def outage(*args: object, **kwargs: object) -> object:
        raise SemanticReviewError("GITHUB_TRANSPORT_FAILURE")

    app._request = outage  # type: ignore[method-assign]
    with pytest.raises(SemanticReviewError, match="GITHUB_TRANSPORT_FAILURE"):
        app._rest_pages("/items?per_page=100", "token")
