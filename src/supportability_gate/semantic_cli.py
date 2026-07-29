"""Poll GitHub and publish fail-closed semantic review checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from supportability_gate.github_app import GitHubApp
from supportability_gate.responses_transport import request_response
from supportability_gate.semantic_contract import SemanticReviewError, SemanticVerdict
from supportability_gate.semantic_review import parse_response


def _verdict_summary(verdict: SemanticVerdict) -> str:
    """Render resolvable ownership evidence for the GitHub check summary."""
    lines = [verdict.verdict]
    for item in verdict.boundaries:
        lines.append(
            f"{item.path}:{item.start_line}-{item.end_line} {item.kind} {item.name} | "
            f"owns: {item.owns} | does not own: {item.does_not_own}"
        )
    lines.append(f"dependency direction: {verdict.dependency_direction}")
    lines.extend(
        f"architecture citation: {citation}" for citation in verdict.architecture_citations
    )
    lines.extend(f"finding: {finding}" for finding in verdict.findings)
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
    packet = app.evidence_packet(repository, pull, token)
    replay = app.replay_result(packet, token)
    if replay is not None:
        return replay
    check_id = app.start_check(packet, token)
    try:
        verdict = parse_response(packet, request_response(packet))
        pull_number = packet.evidence["pull_request"]
        if not isinstance(pull_number, int):
            raise SemanticReviewError("MALFORMED_PULL_REQUEST")
        app.assert_current(packet, pull_number, token)
    except SemanticReviewError as error:
        app.complete_check(packet, token, check_id, "failure", f"TECHNICAL_FAILURE: {error.code}")
        return False
    if verdict.verdict == "PASS":
        app.complete_check(packet, token, check_id, "success", _verdict_summary(verdict))
        return True
    app.complete_check(packet, token, check_id, "failure", _verdict_summary(verdict))
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
