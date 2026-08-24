from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    definition = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"])
    modules = (
        "focused_review.py",
        "standard_block_ownership.py",
        "standard_results.py",
        "standard_results_enforcer.py",
        "standard_results_producer.py",
    )
    root = (
        target
        if all((target / "src/supportability_gate" / name).is_file() for name in modules)
        else definition
    )
    sys.path.insert(0, str(root / "src"))

    from supportability_gate import (  # noqa: PLC0415
        clause_inventory,
        codex_review,
        standard_results,
        standard_results_enforcer,
        standard_results_producer,
    )

    identity = standard_results.RunIdentity(
        "example/repository", 123, "b" * 40, "a" * 40, "f" * 40, 456, 1
    )
    characterization = {
        "base_sha": identity.base_sha,
        "head_sha": identity.head_sha,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": f"github.com/{identity.repository}",
        "schema_version": "characterization-result.v1",
        "workflow_sha": identity.workflow_sha,
    }
    complexity = {
        "architecture": {"blocks": []},
        "base_sha": identity.base_sha,
        "changed_files": [{"path": "src/sample.py"}],
        "functions": [],
        "head_sha": identity.head_sha,
        "modularity": {"blocks": []},
        "overall_result": "PASS",
        "policy_blocks": [],
        "quality_profile": {
            "base_sha": identity.base_sha,
            "head_sha": identity.head_sha,
            "repository_remote": f"github.com/{identity.repository}",
            "workflow_sha": identity.workflow_sha,
        },
        "repository_remote": f"github.com/{identity.repository}",
        "schema_version": "1.0",
        "standard_blocks": [{"blocks": [], "standard": standard} for standard in range(1, 9)],
        "standard_sha256": clause_inventory.STANDARD_SHA256,
        "technical_errors": [],
    }
    refactor = {
        "applicable": False,
        "base_sha": identity.base_sha,
        "characterization_sha256": hashlib.sha256(_canonical(characterization)).hexdigest(),
        "head_sha": identity.head_sha,
        "overall_result": "PASS",
        "policy_blocks": [],
        "repository": identity.repository,
        "schema_version": "refactor-policy-result.v1",
    }
    quality = {
        "artifact_digest": "d" * 64,
        "artifact_id": "789",
        "capture_sha256": "c" * 64,
        "job": "quality-profile",
        "repository": identity.repository,
        "repository_id": str(identity.repository_id),
        "run_attempt": str(identity.run_attempt),
        "run_id": str(identity.run_id),
    }
    start = datetime(2026, 8, 11, 12, tzinfo=UTC)
    evidence = tuple(
        codex_review.FocusedReviewEvidence(
            focus,
            20 + int(focus),
            start + timedelta(minutes=(int(focus) - 1) * 2),
            codex_review.CompletionArtifact(
                "reaction",
                100 * int(focus) + 1,
                start + timedelta(minutes=(int(focus) - 1) * 2 + 1),
            ),
        )
        for focus in codex_review.FOCUSES
    )
    standard_results_producer.codex_review.require_focused_completion = lambda *args: evidence
    os.environ["GITHUB_TOKEN"] = "characterization-token"

    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        inputs = {
            "complexity": complexity,
            "characterization": characterization,
            "refactor": refactor,
            "quality": quality,
        }
        for name, value in inputs.items():
            _write(directory / f"{name}.json", value)
        output = directory / "standard-results.json"
        common = [
            "--repository",
            identity.repository,
            "--repository-id",
            str(identity.repository_id),
            "--base-sha",
            identity.base_sha,
            "--head-sha",
            identity.head_sha,
            "--workflow-sha",
            identity.workflow_sha,
            "--run-id",
            str(identity.run_id),
            "--run-attempt",
            str(identity.run_attempt),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            produced = standard_results_producer.main(
                [
                    *common,
                    "--pull-number",
                    "7",
                    "--complexity-result",
                    str(directory / "complexity.json"),
                    "--characterization-result",
                    str(directory / "characterization.json"),
                    "--refactor-result",
                    str(directory / "refactor.json"),
                    "--quality-provenance",
                    str(directory / "quality.json"),
                    "--output",
                    str(output),
                ]
            )
            enforced = standard_results_enforcer.main(
                [*common, "--input", str(output), "--standard", "1"]
            )
        payload = json.loads(output.read_bytes())

    behavior = {
        "applicable": [entry["applicable"] for entry in payload["entries"]],
        "contexts": [entry["check_context"] for entry in payload["entries"]],
        "enforcer_exit": enforced,
        "producer_exit": produced,
        "results": [entry["result"] for entry in payload["entries"]],
        "schema_version": payload["schema_version"],
    }
    print(
        json.dumps(
            {
                "behavior": behavior,
                "scenario": "standard-results-boundary",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
