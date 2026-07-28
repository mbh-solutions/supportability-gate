"""Poll GitHub and publish fail-closed semantic review checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from supportability_gate.github_app import GitHubApp
from supportability_gate.semantic_review import SemanticReviewError, call_responses


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-semantic-review")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--app-id", required=True, type=int)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--private-key", required=True, type=Path)
    return parser


def _review(app: GitHubApp, repository: str, token: str, pull: dict[str, object]) -> bool:
    packet = app.evidence_packet(repository, pull, token)
    try:
        verdict = call_responses(packet)
    except SemanticReviewError as error:
        app.publish_check(packet, token, "failure", f"TECHNICAL_FAILURE: {error.code}")
        return False
    if verdict.verdict == "PASS":
        app.publish_check(packet, token, "success", "PASS")
        return True
    app.publish_check(packet, token, "failure", "BLOCK: " + "; ".join(verdict.findings))
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
