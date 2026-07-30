from __future__ import annotations

import json

from supportability_gate.handoff_policy import ClaimReview, evaluate_completion_report

PATH = "src/sample.py"


def _behavior() -> dict[str, list[str]]:
    report = {
        "architecture_judgment": "PASS",
        "boundary_rationale": ["Parsing owns one input transformation."],
        "claims": [
            {
                "citations": [f"{PATH}:1-2"],
                "id": "claim-1",
                "text": "parse_input strips whitespace.",
            }
        ],
        "gate_coverage": [{"adapter": "python.ruff-lint.v1", "paths": [PATH]}],
        "head_sha": "b" * 40,
        "overall_result": "PASS",
        "remaining_risks": ["Semantic review can reject unsupported prose."],
        "responsibility_changes": ["Parsing is isolated from validation."],
        "simplified_functions": ["parse_input"],
        "validation_results": [
            {
                "adapter": "python.ruff-lint.v1",
                "arguments": ["check", "src"],
                "exit_code": 0,
            }
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
                }
            ]
        },
    }
    sources = [
        {
            "path": PATH,
            "lines": [
                {"line": 1, "text": "def parse_input(value: str) -> str:"},
                {"line": 2, "text": "    return value.strip()"},
            ],
        }
    ]
    clean = evaluate_completion_report(
        report,
        authoritative,
        sources,
        (ClaimReview("claim-1", True, (f"{PATH}:1-2",)),),
    )
    unsupported = evaluate_completion_report(
        report,
        authoritative,
        sources,
        (ClaimReview("claim-1", False, (f"{PATH}:1-2",)),),
    )
    return {"clean": list(clean), "unsupported": list(unsupported)}


print(
    json.dumps(
        {"behavior": _behavior(), "scenario": "m10-review-handoff", "schema_version": "1.0"},
        separators=(",", ":"),
        sort_keys=True,
    )
)
