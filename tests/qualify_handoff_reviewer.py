"""Qualify candidate model/effort pairs with the exact M10 packet shape."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from supportability_gate.responses_transport import ENDPOINT, LOCAL_OPENER
from supportability_gate.semantic_contract import EvidencePacket, request_payload
from supportability_gate.semantic_review import parse_response
from tests.test_semantic_review import _m10_packet

CANDIDATES = (("gpt-5.6-terra", "medium"), ("gpt-5.6-sol", "medium"))
REQUIRED = ("gpt-5.6-sol", "medium")


@dataclass(frozen=True)
class Case:
    name: str
    expected: str
    evidence: dict[str, Any]
    unsupported_claim: bool = False


@dataclass(frozen=True)
class Observation:
    case: str
    duration_seconds: float
    error: str | None
    expected: str
    model: str
    packet_sha256: str
    parser_result: str | None
    reasoning_effort: str
    request_id: str | None
    response_model: str | None
    response_sha256: str | None
    status: str | None
    unsupported_claim_detected: bool


def _evidence() -> dict[str, Any]:
    return copy.deepcopy(_m10_packet().evidence)


def _cases() -> tuple[Case, ...]:
    clean = _evidence()
    unsupported = _evidence()
    unsupported["completion_report"]["claims"][0]["text"] = (
        "parse_input encrypts secrets before storage."
    )
    invented = _evidence()
    invented["completion_report"]["validation_results"] = [
        {"adapter": "python.magic.v1", "arguments": ["magic"], "exit_code": 0}
    ]
    stale = _evidence()
    stale["completion_report"]["base_sha"] = "c" * 40
    unresolved = _evidence()
    unresolved["completion_report"]["claims"][0]["citations"] = ["src/missing.py:9-10"]
    contradicted = _evidence()
    contradicted["completion_report"]["overall_result"] = "BLOCK"
    hidden = _evidence()
    hidden["authoritative_result"]["quality_profile"]["commands"].append(
        {
            "adapter": "python.pytest.v1",
            "arguments": ["-m", "pytest", "-q"],
            "executed": True,
            "exit_code": 1,
        }
    )
    missing = _evidence()
    del missing["completion_report"]["boundary_rationale"]
    no_risk = _evidence()
    no_risk["completion_report"]["remaining_risks"] = ["No remaining risk."]
    return (
        Case("clean", "PASS", clean),
        Case("unsupported_claim", "BLOCK", unsupported, True),
        Case("invented_command", "BLOCK", invented),
        Case("stale_sha", "BLOCK", stale),
        Case("unresolved_citation", "BLOCK", unresolved),
        Case("contradicted_claim", "BLOCK", contradicted),
        Case("hidden_failure", "BLOCK", hidden),
        Case("missing_section", "BLOCK", missing),
        Case("false_no_risk", "BLOCK", no_risk),
    )


def _packet(case: Case, model: str, effort: str) -> EvidencePacket:
    return EvidencePacket(
        "mbh-solutions/supportability-gate",
        "a" * 40,
        "b" * 40,
        42,
        case.evidence,
        model=model,
        reasoning_effort=effort,
    )


def _response(packet: EvidencePacket) -> tuple[dict[str, Any], str | None]:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(request_payload(packet), separators=(",", ":")).encode(),
        headers={"Authorization": "Bearer sk-dummy", "Content-Type": "application/json"},
        method="POST",
    )
    with LOCAL_OPENER(request, timeout=480.0) as result:
        request_id = result.headers.get("x-request-id")
        response = json.loads(result.read())
    if not isinstance(response, dict):
        raise ValueError("response is not an object")
    return response, request_id


def _observe(case: Case, model: str, effort: str) -> Observation:
    packet = _packet(case, model, effort)
    started = time.monotonic()
    response: dict[str, Any] | None = None
    request_id: str | None = None
    try:
        response, request_id = _response(packet)
        verdict = parse_response(packet, response)
        detected = any(not review.supported for review in verdict.claim_reviews)
        error = None
        parser_result = verdict.parser_result
    except Exception as caught:  # preserve exact bounded qualification failure
        detected = False
        error = f"{type(caught).__name__}:{caught}"
        parser_result = None
    raw = json.dumps(response, separators=(",", ":"), sort_keys=True).encode() if response else b""
    return Observation(
        case=case.name,
        duration_seconds=round(time.monotonic() - started, 3),
        error=error,
        expected=case.expected,
        model=model,
        packet_sha256=packet.sha256,
        parser_result=parser_result,
        reasoning_effort=effort,
        request_id=request_id
        or (str(response.get("id")) if response and response.get("id") else None),
        response_model=str(response.get("model")) if response and response.get("model") else None,
        response_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
        status=str(response.get("status")) if response and response.get("status") else None,
        unsupported_claim_detected=detected,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    observations = [
        _observe(case, model, effort) for model, effort in CANDIDATES for case in _cases()
    ]
    arguments.output.write_text(
        json.dumps([asdict(item) for item in observations], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    qualified = all(
        item.error is None
        and item.response_model == item.model
        and item.status == "completed"
        and item.parser_result == item.expected
        and (item.case != "unsupported_claim" or item.unsupported_claim_detected)
        for item in observations
        if (item.model, item.reasoning_effort) == REQUIRED
    )
    return 0 if qualified and len(observations) == len(CANDIDATES) * len(_cases()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
