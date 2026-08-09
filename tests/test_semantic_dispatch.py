from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

import supportability_gate.semantic_dispatch as dispatch
from supportability_gate.semantic_contract import SemanticReviewError
from supportability_gate.semantic_dispatch import ActiveWorker, Candidate


def _candidate(repository_id: int, pull: int, created_at: str) -> Candidate:
    return Candidate(repository_id, f"owner/repo-{repository_id}", pull, "a" * 40, created_at)


def test_discovery_is_app_selected_exact_head_and_restart_safe() -> None:
    class App:
        def installation_repositories(self, token: str) -> tuple[dict[str, Any], ...]:
            return (
                {"full_name": "owner/source", "id": 1},
                {"full_name": "owner/dc", "id": 2},
                {"full_name": "owner/twmn", "id": 3},
            )

        def open_pulls(self, repository: str, token: str) -> tuple[dict[str, Any], ...]:
            number = {"owner/source": 11, "owner/dc": 23, "owner/twmn": 65}[repository]
            return (
                {
                    "created_at": "2026-08-09T00:00:00Z",
                    "head": {"sha": f"{number % 10}" * 40},
                    "number": number,
                },
            )

        def handoff_ready(self, repository: str, head_sha: str, token: str) -> bool:
            return repository != "owner/twmn"

    first = dispatch.discover_candidates(App(), "token")  # type: ignore[arg-type]
    restarted = dispatch.discover_candidates(App(), "token")  # type: ignore[arg-type]

    assert [(item.repository, item.pull_number) for item in first] == [
        ("owner/source", 11),
        ("owner/dc", 23),
    ]
    assert restarted == first


def test_fair_order_is_round_robin_oldest_pull_first() -> None:
    candidates = (
        _candidate(1, 3, "2026-08-09T03:00:00Z"),
        _candidate(1, 1, "2026-08-09T01:00:00Z"),
        _candidate(2, 2, "2026-08-09T02:00:00Z"),
    )

    assert [(item.repository_id, item.pull_number) for item in dispatch.fair_order(candidates)] == [
        (1, 1),
        (2, 2),
        (1, 3),
    ]
    assert dispatch.fair_order(candidates, after_repository=1)[0].repository_id == 2
    assert dispatch.fair_order(candidates, after_pulls={1: 1})[-1].pull_number == 1


def test_fill_slots_deduplicates_and_caps_workers(monkeypatch: Any) -> None:
    running = _candidate(1, 1, "2026-08-09T01:00:00Z")
    candidates = (
        running,
        _candidate(2, 2, "2026-08-09T02:00:00Z"),
        _candidate(3, 3, "2026-08-09T03:00:00Z"),
    )
    launched: list[Candidate] = []

    def launch(candidate: Candidate, *args: object) -> ActiveWorker:
        launched.append(candidate)
        return ActiveWorker(candidate, object(), 0.0)  # type: ignore[arg-type]

    monkeypatch.setattr(dispatch, "launch_worker", launch)
    active = {running.key: ActiveWorker(running, object(), 0.0)}  # type: ignore[arg-type]

    after_pulls: dict[int, int] = {}
    assert dispatch.fill_slots(active, candidates, 42, 7, Path("key.pem"), after_pulls) == 2
    assert launched == [candidates[1]]
    assert len(active) == dispatch.MAX_WORKERS
    assert after_pulls == {2: 2}


def test_worker_launch_uses_fixed_shell_free_captured_command(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    class Process:
        pass

    def popen(arguments: list[str], **kwargs: object) -> Process:
        captured.update(arguments=arguments, **kwargs)
        return Process()

    monkeypatch.setattr(dispatch.subprocess, "Popen", popen)
    monkeypatch.setattr(dispatch.time, "monotonic", lambda: 12.0)
    candidate = _candidate(9, 24, "2026-08-09T00:00:00Z")

    worker = dispatch.launch_worker(candidate, 42, 7, Path("key.pem"))

    assert worker.started_at == 12.0
    assert captured["shell"] is False
    assert captured["stdout"] is subprocess.PIPE
    assert captured["stderr"] is subprocess.PIPE
    assert captured["arguments"][-12:] == [
        "-m",
        "supportability_gate.semantic_cli",
        "--repository",
        "owner/repo-9",
        "--pull-number",
        "24",
        "--app-id",
        "42",
        "--installation-id",
        "7",
        "--private-key",
        "key.pem",
    ]


def test_reaper_kills_and_removes_timed_out_worker(capsys: Any) -> None:
    class Process:
        returncode = -9
        killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

        def communicate(self, timeout: int) -> tuple[str, str]:
            assert timeout == 30
            return "partial output", "timeout"

    process = Process()
    candidate = _candidate(1, 1, "2026-08-09T00:00:00Z")
    active = {candidate.key: ActiveWorker(candidate, process, 0.0)}  # type: ignore[arg-type]

    dispatch.reap_workers(active, dispatch.WORKER_TIMEOUT_SECONDS + 1)

    assert process.killed is True
    assert active == {}
    assert json.loads(capsys.readouterr().out)["timed_out"] is True


def test_poll_failure_force_reaps_active_worker(monkeypatch: Any, capsys: Any) -> None:
    class Process:
        returncode = -9
        killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

        def communicate(self, timeout: int) -> tuple[str, str]:
            return "", ""

    class App:
        calls = 0

        def installation_token(self) -> str:
            self.calls += 1
            if self.calls == 2:
                raise SemanticReviewError("GITHUB_TRANSPORT_FAILURE")
            return "token"

        def installation_repositories(self, token: str) -> tuple[dict[str, Any], ...]:
            return ({"full_name": "owner/repo-1", "id": 1},)

    process = Process()
    candidate = _candidate(1, 1, "2026-08-09T00:00:00Z")
    monkeypatch.setattr(dispatch, "discover_candidates", lambda *args: (candidate,))
    monkeypatch.setattr(
        dispatch,
        "launch_worker",
        lambda *args: ActiveWorker(candidate, process, 0.0),
    )
    monkeypatch.setattr(dispatch.time, "sleep", lambda seconds: None)
    arguments = argparse.Namespace(
        app_id=42, installation_id=7, private_key=Path("key.pem"), shadow=False
    )

    with pytest.raises(SemanticReviewError, match="GITHUB_TRANSPORT_FAILURE"):
        dispatch._run(arguments, App())  # type: ignore[arg-type]

    assert process.killed is True
    assert json.loads(capsys.readouterr().out)["timed_out"] is True


def test_shadow_reports_selected_repositories_even_without_candidates(capsys: Any) -> None:
    repositories = (
        {"full_name": "owner/source", "id": 1},
        {"full_name": "owner/dc", "id": 2},
        {"full_name": "owner/twmn", "id": 3},
    )

    dispatch._shadow(repositories, ())

    assert json.loads(capsys.readouterr().out) == {
        "candidates": [],
        "selected_repositories": ["owner/source", "owner/dc", "owner/twmn"],
    }
