"""Run the fixed semantic-review.v2 convergence oracle against the live transport."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from supportability_gate.github_app import GitHubApp
from supportability_gate.responses_transport import request_response
from supportability_gate.semantic_contract import (
    PROFILE_IDS,
    ROUNDS,
    EvidencePacket,
    SemanticReviewError,
)
from supportability_gate.semantic_review import parse_response

REPOSITORY = "mbh-solutions/dc_training"
HISTORICAL_CASES = {
    "pr18-1f153c9": (
        18,
        "1f153c9f20b00ebc1c53d4ef9b30108a34a6291f",
        (
            ("home ownership", "home owner", "foundationhome"),
            ("assignment ownership", "assignment owner", "rotationassignment"),
            ("supabase cast", "unchecked cast", "as rotationassignment"),
            ("pending save", "save in progress", "back navigation"),
        ),
    ),
    "pr18-9cc23a": (
        18,
        "9cc23a553f86d0f50b25510f0aada9094388a2e2",
        (("pending", "save", "navigation", "back"),),
    ),
}


@dataclass(frozen=True)
class Observation:
    case: str
    error: str | None
    evidence_sha256: str
    findings: tuple[str, ...]
    profile_id: str
    response_sha256: str | None
    round: int
    verdict: str | None


def _historical_packet(
    app: GitHubApp, token: str, pull_number: int, head_sha: str
) -> EvidencePacket:
    pull = dict(app.pull(REPOSITORY, pull_number, token))
    run = app._handoff_runs(REPOSITORY, head_sha, token)[0]
    artifact = app._handoff_artifact(REPOSITORY, run, token)
    files = app._artifact_json(app._artifact_bytes(REPOSITORY, artifact["id"], token))
    pull["base"] = {**pull["base"], "sha": files["complexity-result.json"]["base_sha"]}
    pull["head"] = {**pull["head"], "sha": head_sha}
    packet = app.evidence_packet(REPOSITORY, pull, token)
    return app.m10_evidence_packet(REPOSITORY, pull, token, packet)


def _authority(body: str) -> dict[str, object]:
    return {
        "closing_issues": [],
        "pull_request": {
            "body": body,
            "number": 999,
            "repository": REPOSITORY,
            "title": "Convergence poison fixture",
            "updated_at": "2026-08-09T00:00:00Z",
            "url": f"https://github.com/{REPOSITORY}/pull/999",
        },
    }


def _fixture_packet(name: str) -> EvidencePacket:
    fixtures = {
        "sql-null": (
            "Acceptance: owner_id is required and every row is owner-bound.",
            "supabase/migrations/poison.sql",
            "create table assignments (owner_id uuid null);",
        ),
        "mjs-fail-open": (
            "Acceptance: failed checks must exit nonzero.",
            "scripts/poison.mjs",
            "try { await check(); } catch (error) { console.log(error); }",
        ),
        "missing-acceptance": ("Closes #999", "README.md", "Adds the requested behavior."),
        "clean": (
            "Acceptance: document the fixed A1 contract; no runtime behavior changes.",
            "README.md",
            "The fixed A1 contract is documented; runtime behavior is unchanged.",
        ),
    }
    body, path, line = fixtures[name]
    diff = f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+{line}"
    return EvidencePacket(
        REPOSITORY,
        "a" * 40,
        "b" * 40,
        4418989,
        {
            "authority": _authority(body),
            "deleted_sources": [],
            "diff": diff,
            "pull_request": 999,
            "refactor_context": {"changed_files": [{"path": path, "status": "added"}]},
            "review_state": {"threads": []},
            "reviewed_sources": [],
        },
    )


def _observe(case: str, packet: EvidencePacket) -> list[Observation]:
    observations: list[Observation] = []
    for round_number in ROUNDS:
        for profile_id in PROFILE_IDS:
            try:
                response = request_response(packet, profile_id, round_number)
                verdict = parse_response(packet, response.decoded(), profile_id, round_number)
                observations.append(
                    Observation(
                        case,
                        None,
                        packet.sha256,
                        verdict.findings,
                        profile_id,
                        verdict.response_sha256,
                        round_number,
                        verdict.verdict,
                    )
                )
            except SemanticReviewError as error:
                observations.append(
                    Observation(
                        case,
                        error.code,
                        packet.sha256,
                        (),
                        profile_id,
                        None,
                        round_number,
                        None,
                    )
                )
    return observations


def _contains_category(text: str, alternatives: tuple[str, ...]) -> bool:
    return any(term in text for term in alternatives)


def _qualified(observations: list[Observation]) -> bool:
    grouped = {
        case: [item for item in observations if item.case == case]
        for case in {item.case for item in observations}
    }
    if any(len(items) != 8 for items in grouped.values()):
        return False
    clean = grouped["clean"]
    if any(item.error or item.verdict != "PASS" or item.findings for item in clean):
        return False
    categories = {
        **{case: value[2] for case, value in HISTORICAL_CASES.items()},
        "sql-null": (("null", "nullable"),),
        "mjs-fail-open": (("fail-open", "nonzero", "failure", "exit"),),
        "missing-acceptance": (("acceptance", "criteria", "guidance", "uncertain"),),
    }
    for case, required in categories.items():
        text = " ".join(
            part for item in grouped[case] for part in (*item.findings, item.error or "")
        ).lower()
        if not all(_contains_category(text, alternatives) for alternatives in required):
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", required=True, type=int)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    app = GitHubApp(args.app_id, args.installation_id, args.private_key.read_bytes())
    token = app.installation_token()
    packets = {
        case: _historical_packet(app, token, pull_number, head_sha)
        for case, (pull_number, head_sha, _) in HISTORICAL_CASES.items()
    }
    packets.update(
        (name, _fixture_packet(name))
        for name in ("sql-null", "mjs-fail-open", "missing-acceptance", "clean")
    )
    observations = [item for case, packet in packets.items() for item in _observe(case, packet)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps([asdict(item) for item in observations], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0 if _qualified(observations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
