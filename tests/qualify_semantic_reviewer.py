"""Run the bounded live M9 reviewer qualification matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from supportability_gate.architecture_policy import source_imports
from supportability_gate.function_changes import responsibility_spans
from supportability_gate.responses_transport import ENDPOINT, LOCAL_OPENER
from supportability_gate.semantic_contract import EvidencePacket, request_payload
from supportability_gate.semantic_review import _output_text, parse_response

MODELS = ("gpt-5.6-terra", "gpt-5.6-sol")
REQUIRED_MODEL = "gpt-5.6-sol"
EFFORT = "medium"
TIMEOUT_SECONDS = 480.0


@dataclass(frozen=True)
class Case:
    name: str
    path: str
    source: str
    verdict: str
    category_terms: tuple[str, ...]
    extra_evidence: dict[str, object]


@dataclass(frozen=True)
class Observation:
    case: str
    category_ok: bool
    duration_seconds: float
    error: str | None
    expected_verdict: str
    findings: tuple[str, ...]
    model: str
    output_text: str | None
    packet_sha256: str
    parsed_verdict: str | None
    reasoning_effort: str
    request_id: str | None
    response_model: str | None
    response_sha256: str | None
    round: int
    status: str | None


def _cases() -> tuple[Case, ...]:
    clean_quality = {
        "quality_decision": {
            "schema_version": "quality-gates.v2",
            "commands": [
                {
                    "adapter": "python.pytest.v1",
                    "proof_kind": "runtime-lines",
                    "observed_paths": ["src/order.py"],
                    "zero_statement_paths": [],
                    "executed": True,
                    "exit_code": 0,
                }
            ],
        },
        "quality_provenance": {
            "artifact_id": "789",
            "artifact_digest": "d" * 64,
            "raw_proof_sha256": "e" * 64,
        },
    }
    return (
        Case(
            "clean",
            "src/order.py",
            "def calculate_total(price: int, quantity: int) -> int:\n    return price * quantity\n",
            "PASS",
            (),
            clean_quality,
        ),
        Case(
            "security",
            "src/report.py",
            "def run_report(command: str) -> str:\n    import subprocess\n    return subprocess.check_output(command, shell=True, text=True)\n",
            "BLOCK",
            ("security", "shell", "command"),
            {},
        ),
        Case(
            "feasibility",
            "src/total.py",
            "def calculate_total(items: list[int]) -> int:\n    return sum(item.price for value in items)\n",
            "BLOCK",
            ("feasib", "undefined", "nameerror", "cannot"),
            {},
        ),
        Case(
            "architecture",
            "src/domain/order.py",
            "import requests\n\ndef save_order(order_id: str) -> None:\n    requests.post('https://example.invalid/orders', json={'id': order_id})\n",
            "BLOCK",
            ("architect", "domain", "dependency", "external"),
            {},
        ),
        Case(
            "complexity_gaming",
            "src/order.py",
            "def process_part_1(value: int) -> int:\n    return value + 1\n",
            "BLOCK",
            ("complex", "vague", "part", "gaming"),
            {},
        ),
        Case(
            "responsibility_boundaries",
            "src/order.py",
            "def process_order(raw: str) -> str:\n    import json\n    import sqlite3\n    order = json.loads(raw)\n    if not order.get('id'):\n        raise ValueError('id required')\n    sqlite3.connect('orders.db').execute('insert into orders values (?)', (order['id'],))\n    print('saved', order['id'])\n    return f\"<p>{order['id']}</p>\"\n",
            "BLOCK",
            ("responsib", "concern", "cohesion", "persistence"),
            {},
        ),
        Case(
            "cohesive_input_boundary",
            "src/customer.py",
            "def parse_customer_id(raw: str) -> int:\n    value = raw.strip()\n    if not value.isdigit():\n        raise ValueError('customer id must be numeric')\n    return int(value)\n",
            "PASS",
            (),
            {},
        ),
        Case(
            "clear_responsibility_extraction",
            "src/order.py",
            "def calculate_order_total(lines: list[tuple[int, int]]) -> int:\n    return sum(calculate_line_total(price, quantity) for price, quantity in lines)\ndef calculate_line_total(price: int, quantity: int) -> int:\n    return price * quantity\n",
            "PASS",
            (),
            {},
        ),
    )


def _reviewed_source(case: Case) -> dict[str, object]:
    content = case.source.encode()
    lines = case.source.splitlines()
    spans = responsibility_spans(case.path, content, set(range(1, len(lines) + 1)))
    return {
        "blob_sha": hashlib.sha1(
            f"blob {len(content)}\0".encode() + content, usedforsecurity=False
        ).hexdigest(),
        "boundaries": [
            {
                "end_line": span.end_line,
                "kind": span.kind,
                "name": span.name,
                "start_line": span.start_line,
            }
            for span in spans
        ],
        "imports": [
            {"line": line, "specifier": specifier}
            for line, specifier in source_imports(case.path, content)
        ],
        "line_count": len(lines),
        "lines": [{"line": number, "text": text} for number, text in enumerate(lines, start=1)],
        "path": case.path,
    }


def _packet(case: Case, model: str) -> EvidencePacket:
    evidence = {
        "diff": "\n".join(f"+{line}" for line in case.source.splitlines()),
        "reviewed_sources": [_reviewed_source(case)],
        **case.extra_evidence,
    }
    return EvidencePacket(
        "mbh-solutions/supportability-gate",
        "a" * 40,
        "b" * 40,
        42,
        evidence,
        model=model,
        reasoning_effort=EFFORT,
    )


def _response(packet: EvidencePacket) -> tuple[dict[str, Any], str | None]:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(request_payload(packet), separators=(",", ":")).encode(),
        headers={"Authorization": "Bearer sk-dummy", "Content-Type": "application/json"},
        method="POST",
    )
    with LOCAL_OPENER(request, timeout=TIMEOUT_SECONDS) as result:
        request_id = result.headers.get("x-request-id")
        data = json.loads(result.read())
    if not isinstance(data, dict):
        raise ValueError("response is not an object")
    return data, request_id


def _observe(case: Case, model: str, round_number: int) -> Observation:
    packet = _packet(case, model)
    started = time.monotonic()
    response: dict[str, Any] | None = None
    request_id: str | None = None
    output_text: str | None = None
    try:
        response, request_id = _response(packet)
        output_text = _output_text(response)
        verdict = parse_response(packet, response)
        text = " ".join(verdict.findings).lower()
        category_ok = not case.category_terms or any(term in text for term in case.category_terms)
        error = None
        parsed_verdict = verdict.verdict
        findings = verdict.findings
    except Exception as caught:  # preserve every bounded qualification failure
        category_ok = False
        error = type(caught).__name__ + ":" + str(caught)
        parsed_verdict = None
        findings = ()
        if isinstance(caught, urllib.error.HTTPError):
            request_id = caught.headers.get("x-request-id")
    raw = json.dumps(response, separators=(",", ":"), sort_keys=True).encode() if response else b""
    return Observation(
        case=case.name,
        category_ok=category_ok,
        duration_seconds=round(time.monotonic() - started, 3),
        error=error,
        expected_verdict=case.verdict,
        findings=findings,
        model=model,
        output_text=output_text,
        packet_sha256=packet.sha256,
        parsed_verdict=parsed_verdict,
        reasoning_effort=EFFORT,
        request_id=request_id
        or (str(response.get("id")) if response and response.get("id") else None),
        response_model=str(response.get("model")) if response and response.get("model") else None,
        response_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
        round=round_number,
        status=str(response.get("status")) if response and response.get("status") else None,
    )


def _write(path: Path, observations: list[Observation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(item) for item in observations], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    observations: list[Observation] = []
    for model in MODELS:
        for round_number in (1, 2):
            for case in _cases():
                observations.append(_observe(case, model, round_number))
                _write(arguments.output, observations)
    qualified = all(
        item.error is None
        and item.response_model == item.model
        and item.status == "completed"
        and item.parsed_verdict == item.expected_verdict
        and item.category_ok
        for item in observations
        if item.model == REQUIRED_MODEL
    )
    return 0 if qualified and len(observations) == 32 else 1


if __name__ == "__main__":
    raise SystemExit(main())
