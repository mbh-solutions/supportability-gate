"""Discover eligible App repositories and supervise ephemeral pull-request workers."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from supportability_gate.github_app import GitHubApp
from supportability_gate.semantic_contract import SHA_PATTERN, SemanticReviewError

POLL_SECONDS = 60
MAX_WORKERS = 2
WORKER_TIMEOUT_SECONDS = 90 * 60
CREATED_AT_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z")


@dataclass(frozen=True)
class Candidate:
    """One exact pull request ready for semantic review."""

    repository_id: int
    repository: str
    pull_number: int
    head_sha: str
    created_at: datetime

    @property
    def key(self) -> tuple[int, int]:
        return self.repository_id, self.pull_number


@dataclass
class ActiveWorker:
    """One launched worker plus its finite runtime boundary."""

    candidate: Candidate
    process: subprocess.Popen[str]
    started_at: float
    stdout: IO[str]
    stderr: IO[str]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-semantic-dispatch")
    parser.add_argument("--app-id", required=True, type=int)
    parser.add_argument("--installation-id", required=True, type=int)
    parser.add_argument("--private-key", required=True, type=Path)
    parser.add_argument("--shadow", action="store_true")
    return parser


def _candidate(repository: dict[str, Any], pull: dict[str, Any]) -> Candidate:
    repository_id, name = repository.get("id"), repository.get("full_name")
    pull_number, created_at = pull.get("number"), pull.get("created_at")
    head = pull.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if (
        type(repository_id) is not int
        or not isinstance(name, str)
        or type(pull_number) is not int
        or not isinstance(created_at, str)
        or CREATED_AT_PATTERN.fullmatch(created_at) is None
        or not isinstance(head_sha, str)
        or SHA_PATTERN.fullmatch(head_sha) is None
    ):
        raise SemanticReviewError("MALFORMED_PULL_REQUEST")
    try:
        created = datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as error:
        raise SemanticReviewError("MALFORMED_PULL_REQUEST") from error
    return Candidate(repository_id, name, pull_number, head_sha, created)


def discover_candidates(
    app: GitHubApp,
    token: str,
    repositories: tuple[dict[str, Any], ...] | None = None,
) -> tuple[Candidate, ...]:
    """Discover exact-head PRs whose prerequisite artifact already exists."""
    candidates = []
    for repository in (
        app.installation_repositories(token) if repositories is None else repositories
    ):
        name = repository["full_name"]
        for pull in app.open_pulls(name, token):
            candidate = _candidate(repository, pull)
            if app.handoff_ready(name, candidate.head_sha, token):
                candidates.append(candidate)
    return tuple(candidates)


def fair_order(
    candidates: tuple[Candidate, ...],
    after_repository: int | None = None,
    after_pulls: dict[int, int] | None = None,
) -> tuple[Candidate, ...]:
    """Round-robin repositories while preserving oldest-PR order within each."""
    grouped: dict[int, list[Candidate]] = {}
    for candidate in sorted(candidates, key=lambda item: (item.created_at, item.pull_number)):
        grouped.setdefault(candidate.repository_id, []).append(candidate)
    for repository_id, pull_number in (after_pulls or {}).items():
        pulls = [item.pull_number for item in grouped.get(repository_id, [])]
        if pull_number in pulls:
            offset = pulls.index(pull_number) + 1
            grouped[repository_id] = (
                grouped[repository_id][offset:] + grouped[repository_id][:offset]
            )
    repositories = list(grouped)
    if after_repository in repositories:
        offset = repositories.index(after_repository) + 1
        repositories = repositories[offset:] + repositories[:offset]
    ordered: list[Candidate] = []
    while any(grouped.values()):
        for repository_id in repositories:
            if grouped[repository_id]:
                ordered.append(grouped[repository_id].pop(0))
    return tuple(ordered)


def _worker_arguments(
    candidate: Candidate, app_id: int, installation_id: int, private_key: Path
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "supportability_gate.semantic_cli",
        "--repository",
        candidate.repository,
        "--pull-number",
        str(candidate.pull_number),
        "--head-sha",
        candidate.head_sha,
        "--app-id",
        str(app_id),
        "--installation-id",
        str(installation_id),
        "--private-key",
        str(private_key),
    ]


def launch_worker(
    candidate: Candidate, app_id: int, installation_id: int, private_key: Path
) -> ActiveWorker:
    """Launch one fixed worker command without a shell."""
    stdout = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    stderr = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    try:
        process = subprocess.Popen(
            _worker_arguments(candidate, app_id, installation_id, private_key),
            stdout=stdout,
            stderr=stderr,
            text=True,
            shell=False,
        )
    except Exception:
        stdout.close()
        stderr.close()
        raise
    return ActiveWorker(candidate, process, time.monotonic(), stdout, stderr)


def fill_slots(
    active: dict[tuple[int, int], ActiveWorker],
    candidates: tuple[Candidate, ...],
    app_id: int,
    installation_id: int,
    private_key: Path,
    after_pulls: dict[int, int],
) -> int | None:
    """Launch only nonduplicate candidates up to the fixed machine cap."""
    last_repository = None
    waiting: dict[tuple[int, int], Candidate] = {}
    for item in candidates:
        if item.key not in active:
            waiting.setdefault(item.key, item)
    for candidate in tuple(waiting.values())[: MAX_WORKERS - len(active)]:
        active[candidate.key] = launch_worker(candidate, app_id, installation_id, private_key)
        last_repository = candidate.repository_id
        after_pulls[candidate.repository_id] = candidate.pull_number
    return last_repository


def _terminate_worker(worker: ActiveWorker, timed_out: bool) -> bool:
    """Return only after one worker terminates or two bounded kill waits fail."""
    if timed_out:
        worker.process.kill()
    try:
        worker.process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        worker.process.kill()
        try:
            worker.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            return False
    return True


def _report_worker(worker: ActiveWorker, timed_out: bool) -> None:
    """Emit bounded worker output after confirmed process termination."""
    worker.stdout.seek(0)
    worker.stderr.seek(0)
    print(
        json.dumps(
            {
                "pull": worker.candidate.pull_number,
                "repository": worker.candidate.repository,
                "returncode": worker.process.returncode,
                "stderr": worker.stderr.read()[-2000:],
                "stdout": worker.stdout.read()[-2000:],
                "timed_out": timed_out,
            },
            sort_keys=True,
        ),
        flush=True,
    )


def reap_workers(active: dict[tuple[int, int], ActiveWorker], now: float | None = None) -> None:
    """Capture completed output and terminate workers beyond the fixed timeout."""
    current = time.monotonic() if now is None else now
    cleanup_failed = False
    for key, worker in tuple(active.items()):
        timed_out = current - worker.started_at > WORKER_TIMEOUT_SECONDS
        if worker.process.poll() is None and not timed_out:
            continue
        terminated = _terminate_worker(worker, timed_out)
        if not terminated:
            cleanup_failed = True
            continue
        try:
            _report_worker(worker, timed_out)
        finally:
            worker.stdout.close()
            worker.stderr.close()
            del active[key]
    if cleanup_failed:
        raise subprocess.SubprocessError("WORKER_TERMINATION_FAILURE")


def _shadow(repositories: tuple[dict[str, Any], ...], candidates: tuple[Candidate, ...]) -> None:
    print(
        json.dumps(
            {
                "candidates": [
                    {
                        "head_sha": item.head_sha,
                        "pull": item.pull_number,
                        "repository": item.repository,
                        "repository_id": item.repository_id,
                    }
                    for item in candidates
                ],
                "selected_repositories": [item["full_name"] for item in repositories],
            },
            sort_keys=True,
        )
    )


def run_dispatch_loop(arguments: argparse.Namespace, app: GitHubApp) -> int:
    active: dict[tuple[int, int], ActiveWorker] = {}
    last_repository: int | None = None
    after_pulls: dict[int, int] = {}
    try:
        while True:
            reap_workers(active)
            token = app.installation_token()
            repositories = app.installation_repositories(token)
            candidates = fair_order(
                discover_candidates(app, token, repositories), last_repository, after_pulls
            )
            if arguments.shadow:
                _shadow(repositories, candidates)
                return 0
            launched_repository = fill_slots(
                active,
                candidates,
                arguments.app_id,
                arguments.installation_id,
                arguments.private_key,
                after_pulls,
            )
            if launched_repository is not None:
                last_repository = launched_repository
            time.sleep(POLL_SECONDS)
    finally:
        if active:
            reap_workers(active, float("inf"))


def main(argv: list[str] | None = None) -> int:
    """Run one shadow scan or the production polling dispatcher."""
    arguments = _parser().parse_args(argv)
    try:
        private_key = arguments.private_key.read_bytes()
        return run_dispatch_loop(
            arguments,
            GitHubApp(arguments.app_id, arguments.installation_id, private_key),
        )
    except (OSError, SemanticReviewError, subprocess.SubprocessError):
        print("TECHNICAL_FAILURE")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
