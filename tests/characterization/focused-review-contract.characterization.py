from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _module() -> Any:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    definition = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"])
    relative = Path("src/supportability_gate/focused_review.py")
    source = target / relative
    if not source.is_file():
        source = definition / relative
    spec = importlib.util.spec_from_file_location("characterized_focused_review", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("missing focused-review characterization source")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = _module()
    requested_at = datetime(2026, 8, 11, 12, tzinfo=UTC)
    completion = module.CompletionArtifact(
        "comment", 9001, datetime(2026, 8, 11, 12, 1, tzinfo=UTC)
    )
    evidence = module.FocusedReviewEvidence("8", 8001, requested_at, completion)
    reviews = json.dumps(module.FOCUSED_REVIEWS, ensure_ascii=False, separators=(",", ":"))
    print(
        json.dumps(
            {
                "behavior": {
                    "artifact_id": evidence.completion.artifact_id,
                    "completion_kind": evidence.completion.kind,
                    "focuses": list(module.FOCUSES),
                    "request_focus": evidence.focus,
                    "request_id": evidence.request_id,
                    "reviews_sha256": hashlib.sha256(reviews.encode()).hexdigest(),
                },
                "scenario": "focused-review-contract",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
