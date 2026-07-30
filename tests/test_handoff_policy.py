from __future__ import annotations

import copy

from supportability_gate.handoff_policy import (
    ClaimReview,
    evaluate_completion_report,
    parse_completion_report,
)

HEAD = "b" * 40
PATH = "src/sample.py"


def _authoritative() -> dict[str, object]:
    return {
        "architecture": {"blocks": [], "executed": True},
        "functions": [{"head": {"qualified_name": "parse_input"}}],
        "gate_coverage": [{"adapter": "python.ruff-lint.v1", "paths": [PATH]}],
        "head_sha": HEAD,
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
        "technical_errors": [],
    }


def _report() -> dict[str, object]:
    return {
        "architecture_judgment": "PASS",
        "boundary_rationale": ["Parsing remains one focused responsibility."],
        "claims": [
            {
                "citations": [f"{PATH}:1-2"],
                "id": "claim-1",
                "text": "parse_input strips surrounding whitespace.",
            }
        ],
        "gate_coverage": [{"adapter": "python.ruff-lint.v1", "paths": [PATH]}],
        "head_sha": HEAD,
        "overall_result": "PASS",
        "remaining_risks": ["Semantic review can still reject an unsupported claim."],
        "responsibility_changes": ["Input parsing is isolated from validation."],
        "simplified_functions": ["parse_input"],
        "validation_results": [
            {
                "adapter": "python.ruff-lint.v1",
                "arguments": ["check", "src"],
                "exit_code": 0,
            }
        ],
    }


def _sources() -> list[dict[str, object]]:
    return [
        {
            "line_count": 2,
            "lines": [
                {"line": 1, "text": "def parse_input(value: str) -> str:"},
                {"line": 2, "text": "    return value.strip()"},
            ],
            "path": PATH,
        }
    ]


def _blocks(
    report: dict[str, object],
    authoritative: dict[str, object] | None = None,
    reviews: tuple[ClaimReview, ...] = (ClaimReview("claim-1", True, (f"{PATH}:1-2",)),),
) -> tuple[str, ...]:
    return evaluate_completion_report(
        report,
        authoritative or _authoritative(),
        _sources(),
        reviews,
    )


def test_complete_source_backed_handoff_passes() -> None:
    assert _blocks(_report()) == ()


def test_completion_report_document_round_trips() -> None:
    assert parse_completion_report(
        b'schema_version = "1.0"\n[completion_report]\noverall_result = "PASS"\n'
    ) == {"overall_result": "PASS"}


def test_plausible_nonempty_but_unsupported_claim_blocks() -> None:
    report = _report()
    report["claims"] = [
        {
            "citations": [f"{PATH}:1-2"],
            "id": "claim-1",
            "text": "parse_input encrypts secrets before storage.",
        }
    ]
    assert _blocks(
        report,
        reviews=(ClaimReview("claim-1", False, (f"{PATH}:1-2",)),),
    ) == ("UNSUPPORTED_COMPLETION_CLAIM:claim-1",)


def test_invented_command_blocks() -> None:
    report = _report()
    report["validation_results"] = [
        {"adapter": "python.magic.v1", "arguments": ["magic"], "exit_code": 0}
    ]
    assert "INVENTED_VALIDATION_COMMAND:python.magic.v1" in _blocks(report)


def test_stale_sha_blocks() -> None:
    report = _report()
    report["head_sha"] = "a" * 40
    assert "STALE_COMPLETION_REPORT_SHA" in _blocks(report)


def test_unresolved_path_line_citation_blocks() -> None:
    report = _report()
    report["claims"] = [{"citations": ["src/missing.py:9-10"], "id": "claim-1", "text": "Claim."}]
    assert "UNRESOLVED_COMPLETION_CITATION:claim-1" in _blocks(report)


def test_contradicted_overall_result_blocks() -> None:
    report = _report()
    report["overall_result"] = "BLOCK"
    assert "CONTRADICTED_COMPLETION_RESULT" in _blocks(report)


def test_missing_simplified_function_blocks() -> None:
    report = _report()
    report["simplified_functions"] = ["other_function"]
    assert "CONTRADICTED_SIMPLIFIED_FUNCTIONS" in _blocks(report)


def test_hidden_failed_command_blocks() -> None:
    authoritative = _authoritative()
    authoritative["quality_profile"] = copy.deepcopy(authoritative["quality_profile"])
    authoritative["quality_profile"]["commands"].append(  # type: ignore[index]
        {
            "adapter": "python.pytest.v1",
            "arguments": ["-m", "pytest", "-q"],
            "executed": True,
            "exit_code": 1,
        }
    )
    assert "HIDDEN_FAILED_COMMAND:python.pytest.v1" in _blocks(_report(), authoritative)


def test_missing_required_report_section_blocks() -> None:
    report = _report()
    del report["boundary_rationale"]
    assert "MISSING_COMPLETION_SECTION:boundary_rationale" in _blocks(report)


def test_false_no_remaining_risk_claim_blocks() -> None:
    report = _report()
    report["remaining_risks"] = ["No remaining risk."]
    assert "FALSE_NO_REMAINING_RISK" in _blocks(report)
