from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

from supportability_gate import semantic_contract, semantic_review


def _quality_runner_contract(target: Path) -> bool:
    if importlib.util.find_spec("supportability_gate.quality_runner") is None:
        return True
    from supportability_gate import quality_runner

    environment = quality_runner.fixed_environment(Path("output"), target)
    return all(
        environment.get(name) == value
        for name, value in {"CI": "true", "NO_COLOR": "1", "PYTHONHASHSEED": "0"}.items()
    )


def _semantic_verdict() -> str:
    packet = semantic_contract.EvidencePacket(
        "owner/repository", "a" * 40, "b" * 40, 42, {"diff": "", "reviewed_sources": []}
    )
    content: dict[str, object] = {
        "verdict": "PASS",
        "findings": [],
        "reviewed_paths": [],
        "boundaries": [],
        "dependency_direction": "No source dependencies.",
        "architecture_citations": [],
        "claim_reviews": [],
        "app_id": packet.app_id,
        "repository": packet.repository,
        "base_sha": packet.base_sha,
        "head_sha": packet.head_sha,
        "evidence_sha256": packet.sha256,
        "rubric_version": semantic_contract.RUBRIC_VERSION,
        "schema_version": semantic_contract.SCHEMA_VERSION,
        "standard_sha256": semantic_contract.STANDARD_SHA256,
    }
    if "model" in semantic_contract.result_schema()["properties"]:
        content["model"] = packet.model
        content["reasoning_effort"] = packet.reasoning_effort
    response = {
        "model": getattr(packet, "model", semantic_contract.MODEL),
        "status": "completed",
        "error": None,
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(content)}],
            }
        ],
    }
    return semantic_review.parse_response(packet, response).verdict


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    behavior = {
        "quality_runner_contract": _quality_runner_contract(target),
        "semantic_verdict": _semantic_verdict(),
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "m9-reviewer-quality-boundary",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
