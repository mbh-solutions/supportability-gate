"""Poll GitHub and publish fail-closed semantic review checks."""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from supportability_gate.github_app import GitHubApp
from supportability_gate.handoff_policy import deterministic_completion_blocks
from supportability_gate.responses_transport import TransportResponse, request_response
from supportability_gate.review_events import ReviewEvent, parse_review_event
from supportability_gate.semantic_contract import (
    EvidencePacket,
    SemanticReviewError,
    SemanticVerdict,
)
from supportability_gate.semantic_review import parse_response


def _verdict_summary(verdict: SemanticVerdict) -> str:
    """Render resolvable ownership evidence for the GitHub check summary."""
    lines = [verdict.verdict]
    lines.extend(f"finding: {finding}" for finding in verdict.findings)
    lines.extend(
        (
            f"dependency direction: {verdict.dependency_direction}",
            f"model: {verdict.returned_model} ({verdict.reasoning_effort})",
            f"response SHA-256: {verdict.response_sha256}",
            f"terminal status: {verdict.terminal_status}",
            f"parser result: {verdict.parser_result}",
        )
    )
    for item in verdict.boundaries:
        lines.append(
            f"{item.path}:{item.start_line}-{item.end_line} {item.kind} {item.name} | "
            f"{item.basis} | owns: {item.owns} | does not own: {item.does_not_own} | "
            f"evidence lines: {','.join(str(line) for line in item.evidence_lines)}"
        )
    lines.extend(
        f"architecture citation: {citation}" for citation in verdict.architecture_citations
    )
    if not verdict.reviewed_paths:
        lines.append("No changed Python or frontend boundary.")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-semantic-review")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--app-id", required=True, type=int)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--private-key", required=True, type=Path)
    return parser


def unresolved_review_blocks(evidence: dict[str, object]) -> tuple[str, ...]:
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


def _complete_current_check(
    app: GitHubApp,
    packet: EvidencePacket,
    pull_number: int,
    token: str,
    check_id: int,
    conclusion: str,
    summary: str,
) -> None:
    app.assert_current(packet, pull_number, token)
    app.complete_check(packet, token, check_id, conclusion, summary)


def _lock(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
        else:
            import fcntl

            getattr(fcntl, "flock")(
                handle.fileno(), getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB")
            )
    except OSError as error:
        raise SemanticReviewError("EVALUATION_IN_PROGRESS") from error


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
    else:
        import fcntl

        getattr(fcntl, "flock")(handle.fileno(), getattr(fcntl, "LOCK_UN"))


@contextmanager
def _evaluation_lock(path: Path) -> Iterator[None]:
    """Give one scheduled process exclusive model and verdict ownership."""
    with path.open("a+b") as handle:
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)


def _review(
    app: GitHubApp,
    repository: str,
    token: str,
    pull: dict[str, object],
    diagnostics_root: Path | None = None,
) -> bool:
    packet = app.evidence_packet(repository, pull, token)
    evidence = packet.evidence
    review_blocks = unresolved_review_blocks(evidence)
    pull_number = evidence.get("pull_request")
    if not isinstance(pull_number, int):
        raise SemanticReviewError("MALFORMED_PULL_REQUEST")
    app.assert_current(packet, pull_number, token)
    if review_blocks:
        check_id = app.start_check(packet, token)
        _complete_current_check(
            app,
            packet,
            pull_number,
            token,
            check_id,
            "failure",
            "BLOCK\n" + "\n".join(review_blocks),
        )
        return False
    packet = app.m10_evidence_packet(repository, pull, token, packet)
    evidence = packet.evidence
    app.assert_current(packet, pull_number, token)
    replay = app.replay_result(packet, token)
    if replay is not None:
        return replay
    check_id = app.start_check(packet, token)
    preflight = (
        deterministic_completion_blocks(
            evidence.get("completion_report"),
            evidence.get("authoritative_result"),
            evidence.get("completion_sources"),
        )
        if any(
            evidence.get(key)
            for key in ("completion_sources", "reviewed_sources", "deleted_sources")
        )
        else ()
    )
    if preflight:
        _complete_current_check(
            app,
            packet,
            pull_number,
            token,
            check_id,
            "failure",
            "BLOCK\n" + "\n".join(preflight),
        )
        return False
    response = (
        request_response(packet, diagnostics_root=diagnostics_root, check_id=check_id)
        if diagnostics_root is not None
        else request_response(packet)
    )
    try:
        verdict = parse_response(
            packet, response.decoded() if isinstance(response, TransportResponse) else response
        )
    except SemanticReviewError as error:
        if error.code in {"INCOMPLETE_RESPONSE", "MODEL_DRIFT", "REFUSAL"}:
            raise
        diagnostic = response.diagnostic if isinstance(response, TransportResponse) else None
        details = f"TECHNICAL_FAILURE\n{error.code}"
        if diagnostic is not None:
            details += (
                f"\nresponse SHA-256: {diagnostic.sha256}\ndiagnostic file: {diagnostic.filename}"
            )
        _complete_current_check(
            app,
            packet,
            pull_number,
            token,
            check_id,
            "failure",
            details,
        )
        return False
    if verdict.verdict == "PASS":
        _complete_current_check(
            app,
            packet,
            pull_number,
            token,
            check_id,
            "success",
            _verdict_summary(verdict),
        )
        return True
    _complete_current_check(
        app,
        packet,
        pull_number,
        token,
        check_id,
        "failure",
        _verdict_summary(verdict),
    )
    return False


def process_review_event(app: GitHubApp, token: str, event: ReviewEvent) -> bool:
    """Invalidate changed current state; scheduled reconciliation alone evaluates it."""
    pull = app.pull(event.repository, event.pull_number, token)
    packet = app.evidence_packet(event.repository, pull, token)
    app.assert_current(packet, event.pull_number, token)
    if not unresolved_review_blocks(packet.evidence):
        packet = app.m10_evidence_packet(event.repository, pull, token, packet)
        app.assert_current(packet, event.pull_number, token)
    replay = app.replay_result(packet, token)
    if replay is not None:
        return replay
    app.start_check(packet, token)
    return False


def handle_review_event(
    app: GitHubApp,
    token: str,
    body: bytes,
    *,
    event_name: str,
    delivery_id: str,
    signature: str,
    secret: bytes,
) -> bool:
    """Authenticate one delivery, then reconcile current review state."""
    event = parse_review_event(
        body,
        event_name=event_name,
        delivery_id=delivery_id,
        signature=signature,
        secret=secret,
    )
    return process_review_event(app, token, event)


def reconcile_open_pulls(
    app: GitHubApp,
    repository: str,
    token: str,
    diagnostics_root: Path | None = None,
) -> tuple[bool, ...]:
    """Re-evaluate every current open pull request as lost-event recovery."""
    return tuple(
        _review(app, repository, token, pull, diagnostics_root)
        for pull in app.open_pulls(repository, token)
    )


def main(argv: list[str] | None = None) -> int:
    """Review every currently open pull request once."""
    arguments = _parser().parse_args(argv)
    try:
        private_key = arguments.private_key.read_bytes()
        app = GitHubApp(arguments.app_id, arguments.installation_id, private_key)
        token = app.installation_token()
        lock_path = arguments.private_key.with_name("semantic-review.lock")
        diagnostics_root = arguments.private_key.with_name("semantic-review-diagnostics")
        with _evaluation_lock(lock_path):
            results = reconcile_open_pulls(app, arguments.repository, token, diagnostics_root)
    except (OSError, SemanticReviewError):
        print("TECHNICAL_FAILURE")
        return 2
    print("PASS" if all(results) else "BLOCK")
    return 0 if all(results) else 1
