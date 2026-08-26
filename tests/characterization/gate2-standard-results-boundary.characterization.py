from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import inspect
import io
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

LEGACY_DRIVER_SHA256 = "c735557ea56b8c1af081610eef042052a8db18fc863efa1108d39263284ffaf3"
FORGED_BOUNDARIES = (("tests/characterization/forged.py", "function", "forged"),)


def _legacy_driver(definition: Path) -> ModuleType:
    path = definition / "tests/characterization/standard-results-boundary.characterization.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != LEGACY_DRIVER_SHA256:
        raise RuntimeError("changed standard-results characterization driver")
    spec = importlib.util.spec_from_file_location("standard_results_boundary", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("missing standard-results characterization driver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    definition = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"])
    sys.path.insert(0, str(target / "src"))

    from supportability_gate import review_evidence  # noqa: PLC0415

    legacy = _legacy_driver(definition)
    original_review_evidence = legacy._review_evidence

    def review_fixture() -> dict[str, object]:
        value = original_review_evidence()
        if (
            "expected_boundaries"
            in inspect.signature(review_evidence.evaluate_review_evidence).parameters
        ):
            separation = value["separation_of_concerns"]
            if not isinstance(separation, dict):
                raise RuntimeError("invalid legacy review fixture")
            separation["boundaries"] = []
        return value

    legacy._review_evidence = review_fixture
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        legacy.main()
    payload: dict[str, Any] = json.loads(output.getvalue())
    for case in payload["behavior"]["cases"].values():
        case["applicability_evidence"]["source_sha256"] = "normalized"

    content = (target / ".supportability-review.toml").read_bytes()
    if (
        "expected_boundaries"
        in inspect.signature(review_evidence.evaluate_review_evidence).parameters
    ):
        parsed, blocks = review_evidence.evaluate_review_evidence(content, None)
        forged, forged_blocks = review_evidence.evaluate_review_evidence(content, FORGED_BOUNDARIES)
        if forged is not None or forged_blocks != (
            "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries",
        ):
            raise RuntimeError("Gate 2 boundary comparison is not enforced")
    else:
        parsed, blocks = review_evidence.evaluate_review_evidence(content)
    payload["behavior"]["review_evidence"] = {
        "accepted": parsed is not None,
        "blocks": list(blocks),
    }
    payload["scenario"] = "gate2-standard-results-boundary"
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
