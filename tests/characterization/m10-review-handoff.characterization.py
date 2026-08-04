from __future__ import annotations

import importlib.util
import json


def _behavior() -> dict[str, list[str]]:
    """Define M10 truth-check behavior before production implementation."""
    if importlib.util.find_spec("supportability_gate.handoff_policy") is None:
        return {"clean": [], "unsupported": ["UNSUPPORTED_COMPLETION_CLAIM:claim-1"]}

    from supportability_gate.handoff_policy import ClaimReview, evaluate_completion_report

    path = "src/sample.py"
    report = {
        "architecture_judgment": "PASS",
        "boundary_rationale": ["Parsing owns one input transformation."],
        "claims": [
            {
                "citations": [f"{path}:1-2"],
                "id": "claim-1",
                "text": "parse_input strips whitespace.",
            }
        ],
        "gate_coverage": [{"adapter": "python.ruff-lint.v1", "paths": [path]}],
        "head_sha": "b" * 40,
        "overall_result": "PASS",
        "remaining_risks": ["Semantic review can reject unsupported prose."],
        "responsibility_changes": ["Parsing is isolated from validation."],
        "simplified_functions": ["parse_input"],
        "validation_results": [
            {"adapter": "python.ruff-lint.v1", "arguments": ["check", "src"], "exit_code": 0}
        ],
    }
    authoritative = {
        "architecture": {"blocks": [], "executed": True},
        "functions": [{"head": {"qualified_name": "parse_input"}}],
        "gate_coverage": report["gate_coverage"],
        "head_sha": "b" * 40,
        "overall_result": "PASS",
        "quality_profile": {
            "commands": [
                {
                    "adapter": "python.ruff-lint.v1",
                    "arguments": ["check", "src"],
                    "executed": True,
                    "exit_code": 0,
                    "observed_paths": [path],
                    "proof_kind": "explicit-source",
                    "zero_statement_paths": [],
                }
            ]
        },
    }
    sources = [
        {
            "path": path,
            "lines": [
                {"line": 1, "text": "def parse_input(value: str) -> str:"},
                {"line": 2, "text": "    return value.strip()"},
            ],
        }
    ]
    clean = evaluate_completion_report(
        report, authoritative, sources, (ClaimReview("claim-1", True, (f"{path}:1-2",)),)
    )
    unsupported = evaluate_completion_report(
        report, authoritative, sources, (ClaimReview("claim-1", False, (f"{path}:1-2",)),)
    )
    return {"clean": list(clean), "unsupported": list(unsupported)}


print(
    json.dumps(
        {"behavior": _behavior(), "scenario": "m10-review-handoff", "schema_version": "1.0"},
        separators=(",", ":"),
        sort_keys=True,
    )
)
