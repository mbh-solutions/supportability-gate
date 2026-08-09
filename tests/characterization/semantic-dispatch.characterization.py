from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _behavior() -> dict[str, object]:
    expected = {
        "fair_order": [[1, 1], [2, 2], [1, 3]],
        "max_workers": 2,
        "poll_seconds": 60,
        "worker_timeout_seconds": 5400,
    }
    if importlib.util.find_spec("supportability_gate.semantic_dispatch") is None:
        return expected

    from supportability_gate.semantic_dispatch import (
        MAX_WORKERS,
        POLL_SECONDS,
        WORKER_TIMEOUT_SECONDS,
        Candidate,
        _worker_arguments,
        fair_order,
    )

    candidates = (
        Candidate(1, "owner/one", 3, "a" * 40, "2026-08-09T03:00:00Z"),
        Candidate(1, "owner/one", 1, "b" * 40, "2026-08-09T01:00:00Z"),
        Candidate(2, "owner/two", 2, "c" * 40, "2026-08-09T02:00:00Z"),
    )
    ordered = fair_order(candidates)
    arguments = _worker_arguments(ordered[0], 42, 7, Path("key.pem"))
    assert arguments[1:] == [
        "-m",
        "supportability_gate.semantic_cli",
        "--repository",
        "owner/one",
        "--pull-number",
        "1",
        "--app-id",
        "42",
        "--installation-id",
        "7",
        "--private-key",
        "key.pem",
    ]
    return {
        "fair_order": [[item.repository_id, item.pull_number] for item in ordered],
        "max_workers": MAX_WORKERS,
        "poll_seconds": POLL_SECONDS,
        "worker_timeout_seconds": WORKER_TIMEOUT_SECONDS,
    }


print(
    json.dumps(
        {"behavior": _behavior(), "scenario": "semantic-dispatch", "schema_version": "1.0"},
        separators=(",", ":"),
        sort_keys=True,
    )
)
