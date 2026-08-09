"""Call the fixed localhost Responses transport and return a validated verdict."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supportability_gate.semantic_contract import (
    PROFILE_IDS,
    EvidencePacket,
    SemanticReviewError,
    request_payload,
)
from supportability_gate.semantic_diagnostics import (
    QuarantinedResponse,
    complete_attempt,
    quarantine_response,
    start_attempt,
)

ENDPOINT = "http://127.0.0.1:8317/v1/responses"
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


@dataclass(frozen=True)
class TransportResponse:
    """Exact received bytes plus their safe diagnostic identity."""

    body: bytes
    diagnostic: QuarantinedResponse | None

    def decoded(self) -> object:
        """Decode the quarantined transport body for semantic parsing."""
        return _decoded_response(self.body)

    def __getitem__(self, key: str) -> object:
        """Keep existing decoded field access while retaining exact bytes."""
        decoded = self.decoded()
        if not isinstance(decoded, dict):
            raise TypeError("response is not an object")
        return decoded[key]


def _request(packet: EvidencePacket, profile_id: str, round_number: int) -> urllib.request.Request:
    return urllib.request.Request(
        ENDPOINT,
        data=json.dumps(
            request_payload(packet, profile_id, round_number), separators=(",", ":")
        ).encode(),
        headers={"Authorization": "Bearer sk-dummy", "Content-Type": "application/json"},
        method="POST",
    )


def _response_bytes(
    request: urllib.request.Request, timeout_seconds: float, opener: Callable[..., Any]
) -> bytes:
    try:
        with opener(request, timeout=timeout_seconds) as result:
            return bytes(result.read())
    except urllib.error.HTTPError as error:
        code = "AUTHENTICATION_FAILURE" if error.code in {401, 403} else "TRANSPORT_FAILURE"
        raise SemanticReviewError(code) from error
    except (urllib.error.URLError, TimeoutError) as error:
        code = "TIMEOUT" if isinstance(error, (TimeoutError, socket.timeout)) else "PROXY_OUTAGE"
        raise SemanticReviewError(code) from error


def _decoded_response(body: bytes) -> object:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SemanticReviewError("MALFORMED_RESPONSE") from error


def request_response(
    packet: EvidencePacket,
    profile_id: str = PROFILE_IDS[0],
    round_number: int = 1,
    *,
    timeout_seconds: float = 480.0,
    opener: Callable[..., Any] = LOCAL_OPENER,
    diagnostics_root: Path | None = None,
    check_id: int | None = None,
) -> TransportResponse:
    """Return exact response bytes after recording the transport attempt."""
    if (diagnostics_root is None) != (check_id is None):
        raise ValueError("diagnostics_root and check_id must be supplied together")
    attempt = (
        start_attempt(diagnostics_root, packet.sha256, check_id, profile_id, round_number)
        if diagnostics_root is not None and check_id is not None
        else None
    )
    try:
        body = _response_bytes(_request(packet, profile_id, round_number), timeout_seconds, opener)
    except SemanticReviewError as error:
        if attempt is not None:
            result = {
                "TIMEOUT": "TIMEOUT",
                "PROXY_OUTAGE": "PROXY_OUTAGE",
            }.get(error.code, "HTTP_FAILURE")
            complete_attempt(attempt, result, error_code=error.code)
        raise
    try:
        diagnostic = (
            quarantine_response(diagnostics_root, packet.sha256, body)
            if diagnostics_root is not None
            else None
        )
    except OSError as error:
        if attempt is not None:
            complete_attempt(
                attempt,
                "RESPONSE_RECEIVED",
                error_code="DIAGNOSTIC_PERSISTENCE_FAILURE",
            )
        raise SemanticReviewError("DIAGNOSTIC_PERSISTENCE_FAILURE") from error
    if attempt is not None:
        complete_attempt(attempt, "RESPONSE_RECEIVED", response=diagnostic)
    return TransportResponse(body, diagnostic)
