"""Publish validated Standard rows as GitHub check runs from one trusted job."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

from supportability_gate import standard_results

_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SUMMARY_LIMIT = 60_000


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--run-attempt", required=True, type=int)
    parser.add_argument("--standard", required=True, type=int, choices=range(1, 9))
    parser.add_argument("--result-exit-code", required=True, type=int, choices=(0, 1, 2))
    parser.add_argument("--summary", required=True)
    return parser


def _payload(arguments: argparse.Namespace) -> dict[str, object]:
    if _REPOSITORY.fullmatch(arguments.repository) is None:
        raise ValueError("invalid repository")
    if _FULL_SHA.fullmatch(arguments.head_sha) is None:
        raise ValueError("invalid head SHA")
    if arguments.run_id < 1 or arguments.run_attempt < 1:
        raise ValueError("invalid run identity")
    summary = Path(arguments.summary).read_text(encoding="utf-8")
    if not summary:
        raise ValueError("empty result summary")
    context = standard_results.CHECK_CONTEXTS[arguments.standard - 1]
    return {
        "name": context,
        "head_sha": arguments.head_sha,
        "status": "completed",
        "conclusion": "success" if arguments.result_exit_code == 0 else "failure",
        "external_id": (
            f"supportability:{arguments.run_id}:{arguments.run_attempt}:{arguments.standard}"
        ),
        "details_url": (
            f"https://github.com/{arguments.repository}/actions/runs/{arguments.run_id}"
        ),
        "output": {
            "title": context,
            "summary": summary[:_SUMMARY_LIMIT],
        },
    }


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        token = os.environ["GITHUB_TOKEN"]
        if not token:
            raise ValueError("missing token")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{arguments.repository}/check-runs",
            data=json.dumps(_payload(arguments), separators=(",", ":")).encode(),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 201:
                raise RuntimeError("unexpected GitHub response")
            result = json.load(response)
        print(json.dumps({"check_run_id": result["id"], "standard": arguments.standard}))
        return 0
    except Exception as error:
        print(f"CHECK_RUN_PUBLICATION_FAILED:{type(error).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
