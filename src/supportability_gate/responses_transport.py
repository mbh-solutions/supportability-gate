"""Call the fixed localhost Responses transport and return a validated verdict."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from supportability_gate.semantic_contract import (
    EvidencePacket,
    SemanticReviewError,
    request_payload,
)

ENDPOINT = "http://127.0.0.1:8317/v1/responses"
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
SEMANTIC_TIMEOUT_SECONDS = 240.0


def _request(packet: EvidencePacket) -> urllib.request.Request:
    return urllib.request.Request(
        ENDPOINT,
        data=json.dumps(request_payload(packet), separators=(",", ":")).encode(),
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
    *,
    timeout_seconds: float = SEMANTIC_TIMEOUT_SECONDS,
    opener: Callable[..., Any] = LOCAL_OPENER,
) -> object:
    """Return one decoded response from the fixed localhost transport."""
    return _decoded_response(_response_bytes(_request(packet), timeout_seconds, opener))
