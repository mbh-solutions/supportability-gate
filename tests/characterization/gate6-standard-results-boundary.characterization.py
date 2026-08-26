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
from types import ModuleType, SimpleNamespace
from typing import Any

LEGACY_DRIVER_SHA256 = "c735557ea56b8c1af081610eef042052a8db18fc863efa1108d39263284ffaf3"
LEGACY_STANDARD_RESULTS_SHA256 = "43b1e96099a314aac1f2059589705161b5681157e07a752674aac9748d551f5b"
FORGED_BOUNDARIES = (("tests/characterization/forged.py", "function", "forged"),)
GATE_SIX_EVIDENCE_SOURCES = [
    "refactor-policy-result.json",
    "characterization-result.json:refactor_runnability",
    "complexity-result.json:responsibility_targets",
    "complexity-result.json:unbounded_production_paths",
    "complexity-result.json:review_evidence.incremental_refactor",
]


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


def _gate_six_derivation_probe(module: ModuleType) -> None:
    original_read = module.git_changes.read_regular_blob
    original_lines = module.git_changes.changed_head_lines
    module.git_changes.read_regular_blob = lambda *args: SimpleNamespace(
        content=b"def calculate(value: int) -> int:\n    return value + 1\n"
    )
    module.git_changes.changed_head_lines = lambda *args, **kwargs: [1, 2]
    try:
        python_targets = module.derive(
            Path("."),
            SimpleNamespace(base_sha="a" * 40, head_sha="b" * 40),
            SimpleNamespace(
                language="python", is_production_path=lambda path: path.startswith("src/")
            ),
            (SimpleNamespace(old_path=None, new_path="src/sample.py"),),
            [],
        )
        module.git_changes.read_regular_blob = lambda _, sha, path, __: SimpleNamespace(
            content=(
                b"export function Card(value: number): number {\n  return value + 1;\n}\n"
                if sha == "a" * 40
                else b"export function Card(value: number) {\n"
                b"  return <section>{value + 2}</section>;\n}\n"
            )
        )
        frontend_targets = module.derive(
            Path("."),
            SimpleNamespace(base_sha="a" * 40, head_sha="b" * 40),
            SimpleNamespace(
                language="typescript", is_production_path=lambda path: path.startswith("src/")
            ),
            (SimpleNamespace(old_path="src/sample.ts", new_path="src/sample.tsx"),),
            [],
        )
    finally:
        module.git_changes.read_regular_blob = original_read
        module.git_changes.changed_head_lines = original_lines
    if python_targets != (("src/sample.py::function:calculate:1-2",), ()) or frontend_targets != (
        ("src/sample.tsx::component:Card:1-3",),
        (),
    ):
        raise RuntimeError("Gate 6 target derivation is not exact")


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    definition = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"])
    sys.path.insert(0, str(target / "src"))

    from supportability_gate import review_evidence, standard_results  # noqa: PLC0415

    gate_six_binding = "responsibility_targets" in getattr(
        standard_results, "_S02_COMPLEXITY_KEYS", ()
    )
    if not gate_six_binding:
        legacy_source = target / "src/supportability_gate/standard_results.py"
        definition_source = definition / "src/supportability_gate/standard_results.py"
        if (
            hashlib.sha256(legacy_source.read_bytes()).hexdigest() != LEGACY_STANDARD_RESULTS_SHA256
            or hashlib.sha256(definition_source.read_bytes()).hexdigest()
            == LEGACY_STANDARD_RESULTS_SHA256
        ):
            raise RuntimeError("Gate 6 binding is missing outside the exact pre-S08 baseline")
    if gate_six_binding:
        from supportability_gate import refactor_targets  # noqa: PLC0415

        _gate_six_derivation_probe(refactor_targets)

    legacy = _legacy_driver(definition)
    original_characterization = legacy._characterization
    original_complexity = legacy._complexity
    original_review_evidence = legacy._review_evidence
    original_refactor = legacy._refactor

    def complexity_fixture(
        identity: Any, standard_sha256: str, path: str, status: str
    ) -> dict[str, Any]:
        value = original_complexity(identity, standard_sha256, path, status)
        if not gate_six_binding:
            return value
        targets = [f"{path}::module:{path}:1-1"] if path.startswith("src/") else []
        value["responsibility_targets"] = targets
        value["unbounded_production_paths"] = []
        return value

    def characterization_fixture(identity: Any, path: str) -> dict[str, Any]:
        value = original_characterization(identity, path)
        if not gate_six_binding:
            return value
        targets = [f"{path}::module:{path}:1-1"] if path.startswith("src/") else []
        value["refactor_runnability"] = {
            "base_sha": identity.base_sha,
            "head_sha": identity.head_sha,
            "repository": f"github.com/{identity.repository}",
            "runnable": True,
            "schema_version": "refactor-runnability.v1",
            "targets": targets,
            "unbounded_paths": [],
            "workflow_sha": identity.workflow_sha,
        }
        return value

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

    def refactor_fixture(
        identity: Any, characterization: dict[str, Any], path: str
    ) -> dict[str, Any]:
        value = original_refactor(identity, characterization, path)
        if gate_six_binding:
            value["predecessor"] = {
                "authorization": None,
                "authorization_comment_id": None,
                "base_sha": None,
                "block": None,
                "head_sha": None,
                "merge_sha": None,
                "pull_number": None,
            }
        if gate_six_binding and path.startswith("src/"):
            target_identity = f"{path}::module:{path}:1-1"
            value.update(
                {
                    "applicable": True,
                    "authorization": {
                        "base_sha": identity.base_sha,
                        "broad": False,
                        "head_sha": identity.head_sha,
                        "repository": identity.repository,
                        "scope": [path],
                        "sequence": {"predecessor_sha": identity.base_sha, "step": 1},
                        "targets": [target_identity],
                    },
                    "authorization_comment_id": 11,
                    "targets": [target_identity],
                }
            )
        return value

    legacy._characterization = characterization_fixture
    legacy._complexity = complexity_fixture
    legacy._review_evidence = review_fixture
    legacy._refactor = refactor_fixture
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        legacy.main()
    payload: dict[str, Any] = json.loads(output.getvalue())
    for case in payload["behavior"]["cases"].values():
        case["applicability_evidence"]["source_sha256"] = "normalized"
        evidence_sources = case["rows"][5]["evidence_sources"]
        if gate_six_binding and evidence_sources != GATE_SIX_EVIDENCE_SOURCES:
            raise RuntimeError("Gate 6 evidence sources are incomplete")
        case["rows"][5]["evidence_sources"] = GATE_SIX_EVIDENCE_SOURCES

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
    payload["scenario"] = "gate6-standard-results-boundary"
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
