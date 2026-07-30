"""Cross-check one completion report against authenticated evidence."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from typing import Any

_REQUIRED = {
    "architecture_judgment",
    "boundary_rationale",
    "claims",
    "gate_coverage",
    "head_sha",
    "overall_result",
    "remaining_risks",
    "responsibility_changes",
    "simplified_functions",
    "validation_results",
}
_CITATION = re.compile(r"(.+):(\d+)-(\d+)\Z")
HANDOFF_REPORT_PATH = ".supportability-handoff.toml"


class CompletionReportError(ValueError):
    """Malformed source completion report."""


def parse_completion_report(content: bytes) -> dict[str, Any]:
    """Parse the only supported M10 report document."""
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise CompletionReportError("MALFORMED_COMPLETION_REPORT") from error
    if set(data) != {"schema_version", "completion_report"} or data["schema_version"] != "1.0":
        raise CompletionReportError("MALFORMED_COMPLETION_REPORT")
    report = data["completion_report"]
    if not isinstance(report, dict):
        raise CompletionReportError("MALFORMED_COMPLETION_REPORT")
    return report


@dataclass(frozen=True)
class ClaimReview:
    """Model judgment for one exact report claim."""

    claim_id: str
    supported: bool
    citations: tuple[str, ...]


def _text_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _source_lines(sources: object) -> dict[str, frozenset[int]]:
    indexed: dict[str, frozenset[int]] = {}
    if not isinstance(sources, list):
        return indexed
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            continue
        lines = source.get("lines")
        if not isinstance(lines, list):
            continue
        numbers = frozenset(
            item["line"]
            for item in lines
            if isinstance(item, dict) and type(item.get("line")) is int
        )
        indexed[source["path"]] = numbers
    return indexed


def _citation_resolves(citation: object, sources: dict[str, frozenset[int]]) -> bool:
    if not isinstance(citation, str) or (match := _CITATION.fullmatch(citation)) is None:
        return False
    path, start_text, end_text = match.groups()
    start, end = int(start_text), int(end_text)
    return start <= end and all(line in sources.get(path, ()) for line in range(start, end + 1))


def _claims(
    value: object, sources: dict[str, frozenset[int]], blocks: list[str]
) -> dict[str, tuple[str, ...]]:
    claims: dict[str, tuple[str, ...]] = {}
    if not isinstance(value, list) or not value:
        blocks.append("MALFORMED_COMPLETION_SECTION:claims")
        return claims
    for item in value:
        if not isinstance(item, dict) or set(item) != {"citations", "id", "text"}:
            blocks.append("MALFORMED_COMPLETION_SECTION:claims")
            continue
        claim_id, text, citations = item["id"], item["text"], item["citations"]
        if (
            not isinstance(claim_id, str)
            or not claim_id.strip()
            or claim_id in claims
            or not isinstance(text, str)
            or not text.strip()
            or not _text_list(citations)
        ):
            blocks.append("MALFORMED_COMPLETION_SECTION:claims")
            continue
        trusted = tuple(citations)
        claims[claim_id] = trusted
        if not all(_citation_resolves(citation, sources) for citation in trusted):
            blocks.append(f"UNRESOLVED_COMPLETION_CITATION:{claim_id}")
    return claims


def _commands(value: object) -> dict[str, tuple[tuple[str, ...], int]]:
    commands: dict[str, tuple[tuple[str, ...], int]] = {}
    if not isinstance(value, list):
        return commands
    for item in value:
        if not isinstance(item, dict):
            continue
        adapter, arguments, exit_code = (
            item.get("adapter"),
            item.get("arguments"),
            item.get("exit_code"),
        )
        if (
            isinstance(adapter, str)
            and isinstance(arguments, list)
            and all(isinstance(argument, str) for argument in arguments)
            and type(exit_code) is int
        ):
            commands[adapter] = (tuple(arguments), exit_code)
    return commands


def _command_blocks(report: dict[str, Any], authoritative: dict[str, Any]) -> list[str]:
    quality = authoritative.get("quality_profile")
    observed = _commands(quality.get("commands") if isinstance(quality, dict) else None)
    reported = _commands(report.get("validation_results"))
    blocks = [
        f"INVENTED_VALIDATION_COMMAND:{adapter}" for adapter in reported if adapter not in observed
    ]
    blocks.extend(
        f"CONTRADICTED_VALIDATION_RESULT:{adapter}"
        for adapter in reported.keys() & observed.keys()
        if reported[adapter] != observed[adapter]
    )
    blocks.extend(
        f"HIDDEN_FAILED_COMMAND:{adapter}"
        for adapter, (_, exit_code) in observed.items()
        if exit_code != 0 and adapter not in reported
    )
    blocks.extend(
        f"MISSING_VALIDATION_RESULT:{adapter}" for adapter in observed if adapter not in reported
    )
    return blocks


def _architecture_result(authoritative: dict[str, Any]) -> str:
    architecture = authoritative.get("architecture")
    if not isinstance(architecture, dict) or not architecture.get("executed"):
        return "MISSING"
    return "BLOCK" if architecture.get("blocks") else "PASS"


def _function_names(authoritative: dict[str, Any]) -> list[str]:
    functions = authoritative.get("functions")
    if not isinstance(functions, list):
        return []
    return sorted(
        item["head"]["qualified_name"]
        for item in functions
        if isinstance(item, dict)
        and isinstance(item.get("head"), dict)
        and isinstance(item["head"].get("qualified_name"), str)
    )


def _risk_blocks(value: object) -> list[str]:
    if not isinstance(value, list) or not _text_list(value):
        return ["MALFORMED_COMPLETION_SECTION:remaining_risks"]
    normalized = {str(item).strip().lower().rstrip(".") for item in value}
    false_none = {"none", "no risk", "no risks", "no known risk", "no remaining risk"}
    return ["FALSE_NO_REMAINING_RISK"] if normalized & false_none else []


def _result_blocks(report: dict[str, Any], authoritative: dict[str, Any]) -> list[str]:
    blocks: list[str] = []
    if report["head_sha"] != authoritative.get("head_sha"):
        blocks.append("STALE_COMPLETION_REPORT_SHA")
    if report["overall_result"] != authoritative.get("overall_result"):
        blocks.append("CONTRADICTED_COMPLETION_RESULT")
    if report["architecture_judgment"] != _architecture_result(authoritative):
        blocks.append("CONTRADICTED_ARCHITECTURE_JUDGMENT")
    if report["gate_coverage"] != authoritative.get("gate_coverage"):
        blocks.append("CONTRADICTED_GATE_COVERAGE")
    if sorted(report["simplified_functions"]) != _function_names(authoritative):
        blocks.append("CONTRADICTED_SIMPLIFIED_FUNCTIONS")
    return blocks


def _review_blocks(
    claims: dict[str, tuple[str, ...]], reviews: tuple[ClaimReview, ...]
) -> list[str]:
    indexed = {review.claim_id: review for review in reviews}
    blocks = [
        f"MISSING_COMPLETION_CLAIM_REVIEW:{claim_id}"
        for claim_id in claims
        if claim_id not in indexed
    ]
    blocks.extend(
        f"UNBOUND_COMPLETION_CLAIM_REVIEW:{claim_id}"
        for claim_id in indexed
        if claim_id not in claims or indexed[claim_id].citations != claims.get(claim_id)
    )
    blocks.extend(
        f"UNSUPPORTED_COMPLETION_CLAIM:{claim_id}"
        for claim_id, review in indexed.items()
        if claim_id in claims and not review.supported
    )
    return blocks


def deterministic_completion_blocks(
    report: object,
    authoritative: object,
    reviewed_sources: object,
) -> tuple[str, ...]:
    """Return machine-resolvable M10 completion-report blocks."""
    if not isinstance(report, dict) or not isinstance(authoritative, dict):
        return ("MALFORMED_COMPLETION_REPORT",)
    missing = sorted(_REQUIRED - set(report))
    if missing:
        return (f"MISSING_COMPLETION_SECTION:{missing[0]}",)
    unknown = sorted(set(report) - _REQUIRED)
    if unknown:
        return (f"MALFORMED_COMPLETION_SECTION:{unknown[0]}",)
    blocks: list[str] = []
    for section in ("boundary_rationale", "responsibility_changes", "simplified_functions"):
        if not _text_list(report[section]):
            blocks.append(f"MALFORMED_COMPLETION_SECTION:{section}")
    blocks.extend(_result_blocks(report, authoritative))
    blocks.extend(_risk_blocks(report["remaining_risks"]))
    blocks.extend(_command_blocks(report, authoritative))
    _claims(report["claims"], _source_lines(reviewed_sources), blocks)
    return tuple(sorted(set(blocks)))


def evaluate_completion_report(
    report: object,
    authoritative: object,
    reviewed_sources: object,
    claim_reviews: tuple[ClaimReview, ...],
) -> tuple[str, ...]:
    """Return deterministic and model-backed M10 report blocks."""
    blocks = list(deterministic_completion_blocks(report, authoritative, reviewed_sources))
    if not isinstance(report, dict):
        return tuple(blocks)
    claims: list[str] = []
    indexed: dict[str, tuple[str, ...]] = {}
    _claims(report.get("claims"), _source_lines(reviewed_sources), claims)
    for item in report.get("claims", []):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            citations = item.get("citations")
            if isinstance(citations, list) and all(isinstance(value, str) for value in citations):
                indexed[item["id"]] = tuple(citations)
    blocks.extend(_review_blocks(indexed, claim_reviews))
    return tuple(sorted(set(blocks)))
