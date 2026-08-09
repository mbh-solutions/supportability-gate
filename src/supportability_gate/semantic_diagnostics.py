"""Persist restricted semantic-review transport diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from supportability_gate.semantic_contract import PROFILE_IDS


@dataclass(frozen=True)
class Attempt:
    """One model transport attempt awaiting its terminal result."""

    path: Path
    evidence_sha256: str
    check_id: int
    profile_id: str
    round: int
    started_at: str
    started_monotonic_ns: int


@dataclass(frozen=True)
class QuarantinedResponse:
    """Safe identity of an exact quarantined response."""

    sha256: str
    filename: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _restricted_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def _atomic_write(path: Path, content: bytes) -> None:
    _restricted_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _attempt_content(
    attempt: Attempt,
    *,
    result: str,
    ended_at: str | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    response: QuarantinedResponse | None = None,
) -> bytes:
    return json.dumps(
        {
            "check_id": attempt.check_id,
            "duration_ms": duration_ms,
            "ended_at": ended_at,
            "error_code": error_code,
            "evidence_sha256": attempt.evidence_sha256,
            "profile_id": attempt.profile_id,
            "response_file": response.filename if response else None,
            "response_sha256": response.sha256 if response else None,
            "result": result,
            "round": attempt.round,
            "started_at": attempt.started_at,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def start_attempt(
    root: Path,
    evidence_sha256: str,
    check_id: int,
    profile_id: str = PROFILE_IDS[0],
    round_number: int = 1,
) -> Attempt:
    """Persist an in-progress record before starting model transport."""
    _restricted_directory(root)
    start_ns = time.time_ns()
    attempt = Attempt(
        root
        / "attempts"
        / f"{evidence_sha256}-{check_id}-r{round_number}-{profile_id}-{start_ns}-{os.getpid()}.json",
        evidence_sha256,
        check_id,
        profile_id,
        round_number,
        _utc_now(),
        time.monotonic_ns(),
    )
    _atomic_write(attempt.path, _attempt_content(attempt, result="IN_PROGRESS"))
    return attempt


def complete_attempt(
    attempt: Attempt,
    result: str,
    *,
    error_code: str | None = None,
    response: QuarantinedResponse | None = None,
) -> None:
    """Atomically replace an attempt with its terminal transport result."""
    duration_ms = (time.monotonic_ns() - attempt.started_monotonic_ns) // 1_000_000
    _atomic_write(
        attempt.path,
        _attempt_content(
            attempt,
            result=result,
            ended_at=_utc_now(),
            duration_ms=duration_ms,
            error_code=error_code,
            response=response,
        ),
    )


def quarantine_response(root: Path, evidence_sha256: str, body: bytes) -> QuarantinedResponse:
    """Atomically preserve exact response bytes before any decoding or parsing."""
    _restricted_directory(root)
    response_sha256 = hashlib.sha256(body).hexdigest()
    filename = f"responses/{evidence_sha256}-{response_sha256}.response"
    _atomic_write(root / filename, body)
    return QuarantinedResponse(response_sha256, filename)
