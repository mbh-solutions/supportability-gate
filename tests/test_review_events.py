from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from supportability_gate import semantic_cli
from supportability_gate.review_events import ReviewEvent, parse_review_event
from supportability_gate.semantic_contract import EvidencePacket, SemanticReviewError


def _body(action: str = "submitted") -> bytes:
    return json.dumps(
        {
            "action": action,
            "pull_request": {"number": 67},
            "repository": {"full_name": "mbh-solutions/supportability-gate"},
        },
        separators=(",", ":"),
    ).encode()


def _signature(body: bytes, secret: bytes = b"secret") -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _packet(state: str = "current", *, unresolved: bool = False) -> EvidencePacket:
    return EvidencePacket(
        "mbh-solutions/supportability-gate",
        "a" * 40,
        "b" * 40,
        42,
        {
            "pull_request": 67,
            "review_state": {
                "threads": ([{"id": "thread-1", "is_resolved": False}] if unresolved else []),
                "state": state,
            },
        },
    )


def _pass_verdict() -> object:
    return type(
        "Verdict",
        (),
        {
            "verdict": "PASS",
            "boundaries": (),
            "dependency_direction": "preserved",
            "architecture_citations": (),
            "findings": (),
            "returned_model": "model",
            "reasoning_effort": "medium",
            "response_sha256": "c" * 64,
            "terminal_status": "completed",
            "parser_result": "PASS",
            "reviewed_paths": (),
        },
    )()


def test_authenticated_review_events_accept_only_supported_delivery() -> None:
    body = _body()
    event = parse_review_event(
        body,
        event_name="pull_request_review",
        delivery_id="delivery-1",
        signature=_signature(body),
        secret=b"secret",
    )
    assert (event.repository, event.pull_number) == (
        "mbh-solutions/supportability-gate",
        67,
    )

    with pytest.raises(SemanticReviewError, match="WEBHOOK_AUTHENTICATION_FAILURE"):
        parse_review_event(
            body,
            event_name="pull_request_review",
            delivery_id="delivery-1",
            signature="sha256=forged",
            secret=b"secret",
        )
    with pytest.raises(SemanticReviewError, match="MALFORMED_REVIEW_EVENT"):
        malformed = _body("opened")
        parse_review_event(
            malformed,
            event_name="pull_request_review",
            delivery_id="delivery-1",
            signature=_signature(malformed),
            secret=b"secret",
        )


@pytest.mark.parametrize(
    ("event_name", "action"),
    [
        ("pull_request_review", "submitted"),
        ("pull_request_review", "edited"),
        ("pull_request_review", "dismissed"),
        ("pull_request_review_comment", "created"),
        ("pull_request_review_comment", "edited"),
        ("pull_request_review_comment", "deleted"),
        ("pull_request_review_thread", "resolved"),
        ("pull_request_review_thread", "unresolved"),
    ],
)
def test_supported_review_state_event_matrix(event_name: str, action: str) -> None:
    body = _body(action)
    assert (
        parse_review_event(
            body,
            event_name=event_name,
            delivery_id="delivery",
            signature=_signature(body),
            secret=b"secret",
        ).pull_number
        == 67
    )


def test_duplicate_and_out_of_order_events_reconcile_current_state() -> None:
    packet = _packet()

    class App:
        pulls = 0
        replays = 0
        pending = 0

        def pull(self, *args: object) -> dict[str, object]:
            self.pulls += 1
            return {"number": 67}

        def evidence_packet(self, *args: object) -> EvidencePacket:
            return packet

        def m10_evidence_packet(self, *args: object) -> EvidencePacket:
            return packet

        def assert_current(self, *args: object) -> None:
            return None

        def replay_result(self, *args: object) -> bool:
            self.replays += 1
            return True

        def start_check(self, *args: object) -> int:
            self.pending += 1
            return 99

    app = App()
    older = ReviewEvent(packet.repository, 67, "older")
    duplicate = ReviewEvent(packet.repository, 67, "duplicate")
    assert semantic_cli.process_review_event(app, "token", duplicate)  # type: ignore[arg-type]
    assert semantic_cli.process_review_event(app, "token", older)  # type: ignore[arg-type]
    assert (app.pulls, app.replays, app.pending) == (2, 2, 0)


def test_new_event_invalidates_without_concurrent_model_evaluation() -> None:
    packet = _packet("new-event", unresolved=True)

    class App:
        pending: list[str] = []

        def pull(self, *args: object) -> dict[str, object]:
            return {"number": 67}

        def evidence_packet(self, *args: object) -> EvidencePacket:
            return packet

        def m10_evidence_packet(self, *args: object) -> EvidencePacket:
            pytest.fail("unresolved event must invalidate before handoff evidence")

        def assert_current(self, *args: object) -> None:
            return None

        def replay_result(self, *args: object) -> None:
            return None

        def start_check(self, captured: EvidencePacket, *args: object) -> int:
            self.pending.append(captured.sha256)
            return 99

    app = App()
    event = ReviewEvent(packet.repository, 67, "new")
    assert not semantic_cli.process_review_event(app, "token", event)  # type: ignore[arg-type]
    assert app.pending == [packet.sha256]


def test_only_one_exact_pull_worker_can_hold_its_lock(tmp_path: Path) -> None:
    lock = tmp_path / "semantic-review.lock"
    with semantic_cli._evaluation_lock(lock):
        with pytest.raises(SemanticReviewError, match="EVALUATION_IN_PROGRESS"):
            with semantic_cli._evaluation_lock(lock):
                pytest.fail("concurrent evaluator acquired the runtime lock")


def test_pull_lock_path_is_exact_and_repository_isolated(tmp_path: Path) -> None:
    private_key = tmp_path / "key.pem"
    first = semantic_cli._pull_lock_path(private_key, "owner/first", 7)

    assert first == semantic_cli._pull_lock_path(private_key, "owner/first", 7)
    assert first == semantic_cli._pull_lock_path(private_key, "Owner/First", 7)
    assert first != semantic_cli._pull_lock_path(private_key, "owner/second", 7)
    assert first != semantic_cli._pull_lock_path(private_key, "owner/first", 8)
    assert first.parent == tmp_path


def test_main_reviews_only_the_requested_pull(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_key = tmp_path / "key.pem"
    private_key.write_bytes(b"key")
    observed: list[object] = []

    class App:
        def installation_token(self) -> str:
            return "token"

        def pull(self, repository: str, pull_number: int, token: str) -> dict[str, object]:
            observed.append((repository, pull_number, token))
            return {"number": pull_number}

    monkeypatch.setattr(semantic_cli, "GitHubApp", lambda *args: App())

    def review(*args: object) -> bool:
        observed.append(args[3])
        return True

    monkeypatch.setattr(semantic_cli, "_review", review)

    assert (
        semantic_cli.main(
            [
                "--repository",
                "owner/repository",
                "--pull-number",
                "17",
                "--app-id",
                "42",
                "--installation-id",
                "7",
                "--private-key",
                str(private_key),
            ]
        )
        == 0
    )
    assert observed == [("owner/repository", 17, "token"), {"number": 17}]
    assert capsys.readouterr().out == "PASS\n"
    assert semantic_cli._pull_lock_path(private_key, "owner/repository", 17).exists()


def test_same_head_change_becomes_pending_before_fresh_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _packet("changed")

    class App:
        calls: list[str] = []

        def evidence_packet(self, *args: object) -> EvidencePacket:
            self.calls.append("review")
            return packet

        def m10_evidence_packet(self, *args: object) -> EvidencePacket:
            self.calls.append("handoff")
            assert args[3] is packet
            return packet

        def assert_current(self, *args: object) -> None:
            self.calls.append("current")

        def replay_result(self, *args: object) -> None:
            self.calls.append("replay")
            return None

        def start_check(self, *args: object) -> int:
            self.calls.append("pending")
            return 99

        def complete_check(self, *args: object) -> None:
            self.calls.append("complete")

    app = App()
    monkeypatch.setattr(
        semantic_cli,
        "request_response",
        lambda *args: (_ for _ in ()).throw(SemanticReviewError("MODEL_TIMEOUT")),
    )

    with pytest.raises(SemanticReviewError, match="ENSEMBLE_TECHNICAL_FAILURE"):
        semantic_cli._review(  # type: ignore[arg-type]
            app, packet.repository, "token", {}
        )
    assert app.calls == [
        "review",
        "current",
        "handoff",
        "current",
        "replay",
        "pending",
        "current",
        "complete",
    ]


def test_state_change_during_evaluation_never_completes_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _packet()

    class App:
        current_reads = 0
        completed = False

        def evidence_packet(self, *args: object) -> EvidencePacket:
            return packet

        def m10_evidence_packet(self, *args: object) -> EvidencePacket:
            return packet

        def assert_current(self, *args: object) -> None:
            self.current_reads += 1
            if self.current_reads == 2:
                raise SemanticReviewError("STALE_EVIDENCE")

        def replay_result(self, *args: object) -> None:
            return None

        def start_check(self, *args: object) -> int:
            return 99

        def complete_check(self, *args: object) -> None:
            self.completed = True

    app = App()
    monkeypatch.setattr(semantic_cli, "request_response", lambda *args: {"unused": True})
    monkeypatch.setattr(
        semantic_cli,
        "parse_response",
        lambda *args: _pass_verdict(),
    )

    with pytest.raises(SemanticReviewError, match="STALE_EVIDENCE"):
        semantic_cli._review(app, packet.repository, "token", {})  # type: ignore[arg-type]
    assert not app.completed


def test_publication_failure_cannot_return_green(monkeypatch: pytest.MonkeyPatch) -> None:
    packet = _packet("fresh")

    class App:
        def evidence_packet(self, *args: object) -> EvidencePacket:
            return packet

        def m10_evidence_packet(self, *args: object) -> EvidencePacket:
            return packet

        def assert_current(self, *args: object) -> None:
            return None

        def replay_result(self, *args: object) -> None:
            return None

        def start_check(self, *args: object) -> int:
            return 99

        def complete_check(self, *args: object) -> None:
            raise SemanticReviewError("CHECK_PUBLICATION_FAILURE")

    monkeypatch.setattr(semantic_cli, "request_response", lambda *args: {"unused": True})
    monkeypatch.setattr(
        semantic_cli,
        "parse_response",
        lambda *args: _pass_verdict(),
    )

    with pytest.raises(SemanticReviewError, match="CHECK_PUBLICATION_FAILURE"):
        semantic_cli._review(App(), packet.repository, "token", {})  # type: ignore[arg-type]


def test_github_outage_during_event_reconciliation_is_non_green() -> None:
    class App:
        def pull(self, *args: object) -> dict[str, object]:
            raise SemanticReviewError("GITHUB_TRANSPORT_FAILURE")

    event = ReviewEvent("mbh-solutions/supportability-gate", 67, "delivery")
    with pytest.raises(SemanticReviewError, match="GITHUB_TRANSPORT_FAILURE"):
        semantic_cli.process_review_event(App(), "token", event)  # type: ignore[arg-type]


def test_missed_event_is_recovered_with_fresh_digest_bound_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _packet("old-pass")
    fresh = _packet("missed-event-current")

    class App:
        completed: list[tuple[object, ...]] = []

        def open_pulls(self, *args: object) -> tuple[dict[str, object], ...]:
            return ({"number": 67},)

        def evidence_packet(self, *args: object) -> EvidencePacket:
            return fresh

        def m10_evidence_packet(self, *args: object) -> EvidencePacket:
            return fresh

        def assert_current(self, *args: object) -> None:
            return None

        def replay_result(self, packet: EvidencePacket, *args: object) -> None:
            assert packet.sha256 != old.sha256
            return None

        def start_check(self, packet: EvidencePacket, *args: object) -> int:
            assert packet.sha256 == fresh.sha256
            return 99

        def complete_check(self, *args: object) -> None:
            self.completed.append(args)

    app = App()
    monkeypatch.setattr(semantic_cli, "request_response", lambda *args: {"unused": True})
    monkeypatch.setattr(semantic_cli, "parse_response", lambda *args: _pass_verdict())

    assert semantic_cli.reconcile_open_pulls(app, fresh.repository, "token") == (True,)  # type: ignore[arg-type]
    assert app.completed[0][0].sha256 == fresh.sha256  # type: ignore[union-attr]
    assert app.completed[0][3] == "success"
