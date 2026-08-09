"""Poll GitHub and publish fail-closed semantic review checks."""

from __future__ import annotations

import argparse
import hashlib
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from supportability_gate.github_app import GitHubApp
from supportability_gate.handoff_policy import deterministic_completion_blocks
from supportability_gate.responses_transport import TransportResponse, request_response
from supportability_gate.review_events import ReviewEvent, parse_review_event
from supportability_gate.semantic_contract import (
    PROFILE_IDS,
    ROUNDS,
    EvidencePacket,
    SemanticReviewError,
    SemanticVerdict,
)
from supportability_gate.semantic_review import parse_response


def _ensemble_summary(
    passed: bool,
    verdicts: list[SemanticVerdict],
    errors: list[tuple[str, int, str]],
    findings: tuple[str, ...],
) -> str:
    """Render bounded aggregate identities and the unsuppressed finding union."""
    lines = ["PASS" if passed else "BLOCK"]
    lines.extend(f"finding: {finding}" for finding in findings)
    profile_order = {profile_id: index for index, profile_id in enumerate(PROFILE_IDS)}
    attempts = [
        (
            (item.round, profile_order[item.profile_id]),
            f"response: {item.profile_id} round {item.round} | {item.response_sha256} | "
            f"{item.returned_model} {item.reasoning_effort} | {item.parser_result}",
        )
        for item in verdicts
    ]
    attempts.extend(
        (
            (round_number, profile_order[profile_id]),
            f"attempt failure: {profile_id} round {round_number} | {code}",
        )
        for profile_id, round_number, code in errors
    )
    lines.extend(summary for _, summary in sorted(attempts))
    return "\n".join(lines)


def _verdict_summary(verdict: SemanticVerdict) -> str:
    """Keep the focused one-response diagnostic used by qualification tests."""
    lines = [
        verdict.verdict,
        f"dependency direction: {verdict.dependency_direction}",
        f"model: {verdict.returned_model} ({verdict.reasoning_effort})",
        f"response SHA-256: {verdict.response_sha256}",
        f"terminal status: {verdict.terminal_status}",
        f"parser result: {verdict.parser_result}",
    ]
    lines[1:1] = (f"finding: {finding}" for finding in verdict.findings)
    if not verdict.reviewed_paths:
        lines.append("No changed Python or frontend boundary.")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-semantic-review")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pull-number", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--app-id", required=True, type=int)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--lease-file", type=Path)
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


def _require_lease(lease_file: Path | None) -> None:
    if lease_file is not None and lease_file.read_text(encoding="utf-8") != "active":
        raise SemanticReviewError("WORKER_LEASE_REVOKED")


def _complete_current_check(
    app: GitHubApp,
    packet: EvidencePacket,
    pull_number: int,
    token: str,
    check_id: int,
    conclusion: str,
    summary: str,
    lease_file: Path | None = None,
) -> None:
    _require_lease(lease_file)
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
    """Give one pull-request worker exclusive verdict ownership."""
    with path.open("a+b") as handle:
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)


def _pull_lock_path(private_key: Path, repository: str, pull_number: int) -> Path:
    """Return one stable, filesystem-safe lock path for an exact pull request."""
    identity = hashlib.sha256(f"{repository.lower()}#{pull_number}".encode()).hexdigest()
    return private_key.with_name(f"semantic-review-{identity}.lock")


def _review_attempt(
    packet: EvidencePacket,
    profile_id: str,
    round_number: int,
    diagnostics_root: Path | None,
    check_id: int,
) -> SemanticVerdict:
    """Run and parse one specialist attempt."""
    response = (
        request_response(
            packet,
            profile_id,
            round_number,
            diagnostics_root=diagnostics_root,
            check_id=check_id,
        )
        if diagnostics_root is not None
        else request_response(packet, profile_id, round_number)
    )
    return parse_response(
        packet,
        response.decoded() if isinstance(response, TransportResponse) else response,
        profile_id,
        round_number,
    )


def _review_round(
    packet: EvidencePacket,
    round_number: int,
    diagnostics_root: Path | None,
    check_id: int,
) -> tuple[list[SemanticVerdict], list[tuple[str, int, str]]]:
    """Run four specialists concurrently and retain deterministic profile order."""
    with ThreadPoolExecutor(max_workers=len(PROFILE_IDS)) as executor:
        attempts = [
            (
                profile_id,
                executor.submit(
                    _review_attempt,
                    packet,
                    profile_id,
                    round_number,
                    diagnostics_root,
                    check_id,
                ),
            )
            for profile_id in PROFILE_IDS
        ]
    verdicts: list[SemanticVerdict] = []
    errors: list[tuple[str, int, str]] = []
    for profile_id, attempt in attempts:
        try:
            verdicts.append(attempt.result())
        except SemanticReviewError as error:
            errors.append((profile_id, round_number, error.code))
        except Exception:
            errors.append((profile_id, round_number, "UNEXPECTED_ATTEMPT_FAILURE"))
    return verdicts, errors


def _review(
    app: GitHubApp,
    repository: str,
    token: str,
    pull: dict[str, object],
    diagnostics_root: Path | None = None,
    lease_file: Path | None = None,
) -> bool:
    packet = app.evidence_packet(repository, pull, token)
    evidence = packet.evidence
    review_blocks = unresolved_review_blocks(evidence)
    pull_number = evidence.get("pull_request")
    if not isinstance(pull_number, int):
        raise SemanticReviewError("MALFORMED_PULL_REQUEST")
    app.assert_current(packet, pull_number, token)
    if review_blocks:
        _require_lease(lease_file)
        check_id = app.start_check(packet, token)
        _complete_current_check(
            app,
            packet,
            pull_number,
            token,
            check_id,
            "failure",
            "BLOCK\n" + "\n".join(review_blocks),
            lease_file,
        )
        return False
    packet = app.m10_evidence_packet(repository, pull, token, packet)
    evidence = packet.evidence
    app.assert_current(packet, pull_number, token)
    replay = app.replay_result(packet, token)
    if replay is not None:
        return replay
    _require_lease(lease_file)
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
            "action_required",
            "PREFLIGHT_BLOCK\n" + "\n".join(preflight),
            lease_file,
        )
        return False
    verdicts: list[SemanticVerdict] = []
    errors: list[tuple[str, int, str]] = []
    for round_number in ROUNDS:
        round_verdicts, round_errors = _review_round(
            packet, round_number, diagnostics_root, check_id
        )
        verdicts.extend(round_verdicts)
        errors.extend(round_errors)
    findings = tuple(
        dict.fromkeys((*preflight, *(finding for item in verdicts for finding in item.findings)))
    )
    if errors:
        _complete_current_check(
            app,
            packet,
            pull_number,
            token,
            check_id,
            "action_required",
            _ensemble_summary(False, verdicts, errors, findings),
            lease_file,
        )
        raise SemanticReviewError("ENSEMBLE_TECHNICAL_FAILURE")
    passed = (
        len(verdicts) == len(PROFILE_IDS) * len(ROUNDS)
        and not errors
        and not findings
        and all(item.verdict == "PASS" for item in verdicts)
    )
    _complete_current_check(
        app,
        packet,
        pull_number,
        token,
        check_id,
        "success" if passed else "failure",
        _ensemble_summary(passed, verdicts, errors, findings),
        lease_file,
    )
    return passed


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
    """Review one exact pull request once."""
    arguments = _parser().parse_args(argv)
    try:
        private_key = arguments.private_key.read_bytes()
        app = GitHubApp(arguments.app_id, arguments.installation_id, private_key)
        token = app.installation_token()
        pull = app.pull(arguments.repository, arguments.pull_number, token)
        head = pull.get("head")
        if not isinstance(head, dict) or head.get("sha") != arguments.head_sha:
            raise SemanticReviewError("STALE_EVIDENCE")
        lock_path = _pull_lock_path(
            arguments.private_key, arguments.repository, arguments.pull_number
        )
        diagnostics_root = arguments.private_key.with_name("semantic-review-diagnostics")
        with _evaluation_lock(lock_path):
            review_arguments = (app, arguments.repository, token, pull, diagnostics_root)
            result = (
                _review(*review_arguments, arguments.lease_file)
                if arguments.lease_file is not None
                else _review(*review_arguments)
            )
    except (OSError, SemanticReviewError):
        print("TECHNICAL_FAILURE")
        return 2
    print("PASS" if result else "BLOCK")
    return 0 if result else 1
