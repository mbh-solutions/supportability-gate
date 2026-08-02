"""Poll GitHub and publish fail-closed semantic review checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from supportability_gate.github_app import GitHubApp
from supportability_gate.handoff_policy import deterministic_completion_blocks
from supportability_gate.responses_transport import request_response
from supportability_gate.review_state import unresolved_review_blocks
from supportability_gate.semantic_contract import SemanticReviewError, SemanticVerdict
from supportability_gate.semantic_review import parse_response


def _verdict_summary(verdict: SemanticVerdict) -> str:
    """Render resolvable ownership evidence for the GitHub check summary."""
    lines = [verdict.verdict]
    for item in verdict.boundaries:
        lines.append(
            f"{item.path}:{item.start_line}-{item.end_line} {item.kind} {item.name} | "
            f"{item.basis} | owns: {item.owns} | does not own: {item.does_not_own} | "
            f"evidence lines: {','.join(str(line) for line in item.evidence_lines)}"
        )
    lines.append(f"dependency direction: {verdict.dependency_direction}")
    lines.extend(
        f"architecture citation: {citation}" for citation in verdict.architecture_citations
    )
    lines.extend(f"finding: {finding}" for finding in verdict.findings)
    lines.extend(
        (
            f"model: {verdict.returned_model} ({verdict.reasoning_effort})",
            f"response SHA-256: {verdict.response_sha256}",
            f"terminal status: {verdict.terminal_status}",
            f"parser result: {verdict.parser_result}",
        )
    )
    if not verdict.reviewed_paths:
        lines.append("No changed Python or frontend boundary.")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-semantic-review")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--app-id", required=True, type=int)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--private-key", required=True, type=Path)
    return parser


def _review(app: GitHubApp, repository: str, token: str, pull: dict[str, object]) -> bool:
    packet = app.m10_evidence_packet(repository, pull, token)
    evidence = packet.evidence
    review_blocks = unresolved_review_blocks(evidence)
    pull_number = evidence.get("pull_request")
    if not isinstance(pull_number, int):
        raise SemanticReviewError("MALFORMED_PULL_REQUEST")
    app.assert_current(packet, pull_number, token)
    if review_blocks:
        app.publish_check(packet, token, "failure", "BLOCK\n" + "\n".join(review_blocks))
        return False
    replay = app.replay_result(packet, token)
    if replay is not None:
        return replay
    preflight = (
        deterministic_completion_blocks(
            evidence.get("completion_report"),
            evidence.get("authoritative_result"),
            evidence.get("completion_sources"),
        )
        if any(
            evidence.get(key)
            for key in ("completion_sources", "reviewed_sources", "deleted_sources")
        )
        else ()
    )
    if preflight:
        app.publish_check(packet, token, "failure", "BLOCK\n" + "\n".join(preflight))
        return False
    verdict = parse_response(packet, request_response(packet))
    app.assert_current(packet, pull_number, token)
    if verdict.verdict == "PASS":
        app.publish_check(packet, token, "success", _verdict_summary(verdict))
        return True
    app.publish_check(packet, token, "failure", _verdict_summary(verdict))
    return False


def main(argv: list[str] | None = None) -> int:
    """Review every currently open pull request once."""
    arguments = _parser().parse_args(argv)
    try:
        private_key = arguments.private_key.read_bytes()
        app = GitHubApp(arguments.app_id, arguments.installation_id, private_key)
        token = app.installation_token()
        results = [
            _review(app, arguments.repository, token, pull)
            for pull in app.open_pulls(arguments.repository, token)
        ]
    except (OSError, SemanticReviewError):
        print("TECHNICAL_FAILURE")
        return 2
    print("PASS" if all(results) else "BLOCK")
    return 0 if all(results) else 1
