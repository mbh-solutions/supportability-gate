"""Validate and publish one deferred semantic worker result."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from supportability_gate.github_app import GitHubApp
from supportability_gate.semantic_contract import (
    MAX_WORKER_RESULT_BYTES,
    EvidencePacket,
    SemanticReviewError,
)
from supportability_gate.semantic_lease import exclusive_lease


def publish_worker_result(
    app: GitHubApp,
    packet: EvidencePacket | None,
    pull_number: int,
    path: Path | None,
    lock_file: Path | None,
) -> None:
    """Validate exact evidence, reacquire its lease, then publish."""
    if (
        packet is None
        or path is None
        or lock_file is None
        or path.stat().st_size > MAX_WORKER_RESULT_BYTES
    ):
        raise SemanticReviewError("MALFORMED_WORKER_RESULT")
    try:
        result: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SemanticReviewError("MALFORMED_WORKER_RESULT") from error
    if (
        not isinstance(result, dict)
        or set(result) != {"check_id", "conclusion", "evidence_sha256", "summary"}
        or type(result["check_id"]) is not int
        or result["check_id"] <= 0
        or result["conclusion"] not in {"success", "failure", "action_required"}
        or result["evidence_sha256"] != packet.sha256
        or not isinstance(result["summary"], str)
    ):
        raise SemanticReviewError("MALFORMED_WORKER_RESULT")
    with exclusive_lease(lock_file):
        token = app.installation_token()
        app.assert_current(packet, pull_number, token)
        app.complete_check(
            packet, token, result["check_id"], result["conclusion"], result["summary"]
        )
