from __future__ import annotations

import argparse
import io
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

import supportability_gate.semantic_dispatch as dispatch
from supportability_gate.semantic_contract import EvidencePacket, SemanticReviewError
from supportability_gate.semantic_dispatch import ActiveWorker, Candidate


def _candidate(repository_id: int, pull: int, created_at: str) -> Candidate:
    return Candidate(
        repository_id,
        f"owner/repo-{repository_id}",
        pull,
        "a" * 40,
        datetime.fromisoformat(created_at.replace("Z", "+00:00")),
    )


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

        def evidence_packet(self, repository: str, pull: dict[str, Any], token: str) -> object:
            return pull

        def m10_evidence_packet(self, *args: object) -> object:
            return args[3]

        def replay_result(self, packet: object, token: str) -> bool | None:
            return None

    first = dispatch.discover_candidates(App(), "token")  # type: ignore[arg-type]
    restarted = dispatch.discover_candidates(App(), "token")  # type: ignore[arg-type]

    assert [(item.repository, item.pull_number) for item in first] == [
        ("owner/source", 11),
        ("owner/dc", 23),
    ]
    assert restarted == first


def test_candidate_rejects_noncanonical_github_timestamp() -> None:
    with pytest.raises(SemanticReviewError, match="MALFORMED_PULL_REQUEST"):
        dispatch._candidate(
            {"full_name": "owner/repo", "id": 1},
            {"created_at": "tomorrow", "head": {"sha": "a" * 40}, "number": 1},
        )


@pytest.mark.parametrize(
    ("repository", "pull"),
    [
        ({"full_name": "../repo", "id": 1}, {"number": 1}),
        ({"full_name": "owner/repo", "id": 0}, {"number": 1}),
        ({"full_name": "owner/repo", "id": 1}, {"number": 0}),
    ],
)
def test_candidate_rejects_invalid_external_identity(
    repository: dict[str, Any], pull: dict[str, Any]
) -> None:
    pull.update(created_at="2026-08-09T00:00:00Z", head={"sha": "a" * 40})
    with pytest.raises(SemanticReviewError, match="MALFORMED_PULL_REQUEST"):
        dispatch._candidate(repository, pull)


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
        return ActiveWorker(candidate, object(), 0.0, io.StringIO(), io.StringIO())  # type: ignore[arg-type]

    monkeypatch.setattr(dispatch, "launch_worker", launch)
    active = {
        running.key: ActiveWorker(running, object(), 0.0, io.StringIO(), io.StringIO())  # type: ignore[arg-type]
    }

    after_pulls: dict[int, int] = {}
    assert dispatch.fill_slots(active, candidates, 42, 7, Path("key.pem"), after_pulls) == 2
    assert launched == [candidates[1]]
    assert len(active) == dispatch.MAX_WORKERS
    assert after_pulls == {2: 2}


def test_fill_slots_deduplicates_same_scan_candidates(monkeypatch: Any) -> None:
    candidate = _candidate(1, 1, "2026-08-09T00:00:00Z")
    launched: list[Candidate] = []

    def launch(item: Candidate, *args: object) -> ActiveWorker:
        launched.append(item)
        return ActiveWorker(item, object(), 0.0, io.StringIO(), io.StringIO())  # type: ignore[arg-type]

    monkeypatch.setattr(dispatch, "launch_worker", launch)
    active: dict[tuple[int, int], ActiveWorker] = {}

    dispatch.fill_slots(active, (candidate, candidate), 42, 7, Path("key.pem"), {})

    assert launched == [candidate]


def test_worker_launch_uses_fixed_shell_free_captured_command(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    class Process:
        returncode = None

    def popen(arguments: list[str], **kwargs: object) -> Process:
        captured.update(arguments=arguments, **kwargs)
        return Process()

    monkeypatch.setattr(dispatch.subprocess, "Popen", popen)
    monkeypatch.setattr(dispatch.time, "monotonic", lambda: 12.0)
    candidate = _candidate(9, 24, "2026-08-09T00:00:00Z")

    worker = dispatch.launch_worker(candidate, 42, 7, Path("key.pem"))

    assert worker.started_at == 12.0
    assert captured["shell"] is False
    assert captured["cwd"] == Path(dispatch.sys.executable).resolve().parent
    assert captured["stdout"] is not subprocess.PIPE
    assert captured["stderr"] is not subprocess.PIPE
    assert captured["arguments"][1:] == [
        "-I",
        "-m",
        "supportability_gate.semantic_cli",
        "--repository",
        "owner/repo-9",
        "--pull-number",
        "24",
        "--head-sha",
        "a" * 40,
        "--app-id",
        "42",
        "--installation-id",
        "7",
        "--private-key",
        str(Path("key.pem").resolve()),
        "--result-file",
        str(worker.result_file),
    ]
    assert worker.result_file is not None
    assert worker.result_file.parent == Path("key.pem").resolve().parent
    assert worker.result_file.read_text(encoding="utf-8") == ""
    worker.stdout.close()
    worker.stderr.close()
    worker.result_file.unlink()


def test_discovery_skips_completed_exact_generation() -> None:
    class App:
        def installation_repositories(self, token: str) -> tuple[dict[str, Any], ...]:
            return ({"full_name": "owner/repo", "id": 1},)

        def open_pulls(self, repository: str, token: str) -> tuple[dict[str, Any], ...]:
            return (
                {
                    "created_at": "2026-08-09T00:00:00Z",
                    "head": {"sha": "a" * 40},
                    "number": 1,
                },
            )

        def handoff_ready(self, *args: object) -> bool:
            return True

        def evidence_packet(self, *args: object) -> object:
            return "base"

        def m10_evidence_packet(self, *args: object) -> object:
            return "exact-generation"

        def replay_result(self, packet: object, token: str) -> bool | None:
            assert packet == "exact-generation"
            return True

    assert dispatch.discover_candidates(App(), "token") == ()  # type: ignore[arg-type]


def test_reaper_kills_and_removes_timed_out_worker(capsys: Any) -> None:
    class Process:
        returncode = -9
        killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            assert timeout == 30
            return self.returncode

    process = Process()
    candidate = _candidate(1, 1, "2026-08-09T00:00:00Z")
    active = {
        candidate.key: ActiveWorker(
            candidate, process, 0.0, io.StringIO("partial output"), io.StringIO("timeout")
        )  # type: ignore[arg-type]
    }

    dispatch.reap_workers(active, dispatch.WORKER_TIMEOUT_SECONDS + 1)

    assert process.killed is True
    assert active == {}
    assert json.loads(capsys.readouterr().out)["timed_out"] is True


def test_reaper_tolerates_kill_after_worker_exit(capsys: Any) -> None:
    class Process:
        returncode = 0

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            raise ProcessLookupError

        def wait(self, timeout: int) -> int:
            return self.returncode

    candidate = _candidate(1, 1, "2026-08-09T00:00:00Z")
    active = {
        candidate.key: ActiveWorker(candidate, Process(), 0.0, io.StringIO(), io.StringIO())  # type: ignore[arg-type]
    }

    dispatch.reap_workers(active, dispatch.WORKER_TIMEOUT_SECONDS + 1)

    assert active == {}
    assert json.loads(capsys.readouterr().out)["returncode"] == 0


def test_dispatcher_alone_publishes_confirmed_worker_result(tmp_path: Path) -> None:
    packet = EvidencePacket("owner/repo-1", "b" * 40, "a" * 40, 42, {"pull_request": 1})
    candidate = Candidate(
        1,
        packet.repository,
        1,
        packet.head_sha,
        datetime.fromisoformat("2026-08-09T00:00:00+00:00"),
        packet,
    )
    result = tmp_path / "result"
    result.write_text(
        json.dumps(
            {
                "check_id": 9,
                "conclusion": "success",
                "evidence_sha256": packet.sha256,
                "summary": "PASS",
            }
        ),
        encoding="utf-8",
    )

    class Process:
        returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: int) -> int:
            return 0

    class App:
        completed: list[tuple[object, ...]] = []

        def installation_token(self) -> str:
            return "token"

        def assert_current(self, *args: object) -> None:
            return None

        def complete_check(self, *args: object) -> None:
            self.completed.append(args)

    app = App()
    active = {
        candidate.key: ActiveWorker(candidate, Process(), 0.0, io.StringIO(), io.StringIO(), result)
    }  # type: ignore[arg-type]

    dispatch.reap_workers(active, now=0.0, app=app)  # type: ignore[arg-type]

    assert active == {}
    assert app.completed == [(packet, "token", 9, "success", "PASS")]
    assert not result.exists()


def test_poll_failure_force_reaps_active_worker(monkeypatch: Any, capsys: Any) -> None:
    class Process:
        returncode = -9
        killed = False
        wait_calls = 0

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            self.wait_calls += 1
            if self.wait_calls < 2:
                raise subprocess.TimeoutExpired("worker", timeout)
            return self.returncode

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
        lambda *args: ActiveWorker(candidate, process, 0.0, io.StringIO(), io.StringIO()),
    )
    monkeypatch.setattr(dispatch.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(dispatch.time, "sleep", lambda seconds: None)
    arguments = argparse.Namespace(
        app_id=42, installation_id=7, private_key=Path("key.pem"), shadow=False
    )

    with pytest.raises(SemanticReviewError, match="GITHUB_TRANSPORT_FAILURE"):
        dispatch.run_dispatch_loop(arguments, App())  # type: ignore[arg-type]

    assert process.killed is True
    assert process.wait_calls == 2
    assert json.loads(capsys.readouterr().out)["timed_out"] is True


def test_second_wait_timeout_retains_every_worker(monkeypatch: Any) -> None:
    class Process:
        returncode = None

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: int) -> int:
            raise subprocess.TimeoutExpired("worker", timeout)

    first = _candidate(1, 1, "2026-08-09T00:00:00Z")
    second = _candidate(2, 2, "2026-08-09T00:00:00Z")
    active = {
        item.key: ActiveWorker(item, Process(), 0.0, io.StringIO(), io.StringIO())
        for item in (first, second)
    }  # type: ignore[arg-type]
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

    with pytest.raises(subprocess.SubprocessError, match="WORKER_TERMINATION_FAILURE"):
        dispatch.reap_workers(active, dispatch.WORKER_TIMEOUT_SECONDS + 1)

    assert set(active) == {first.key, second.key}
    assert all(not worker.stdout.closed and not worker.stderr.closed for worker in active.values())
    for worker in active.values():
        worker.stdout.close()
        worker.stderr.close()


def test_shutdown_fails_after_two_bounded_waits(monkeypatch: Any, tmp_path: Path) -> None:
    class Process:
        returncode = None
        wait_calls = 0

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: int) -> int:
            self.wait_calls += 1
            raise subprocess.TimeoutExpired("worker", timeout)

    class App:
        def installation_token(self) -> str:
            return "token"

        def installation_repositories(self, token: str) -> tuple[dict[str, Any], ...]:
            return ({"full_name": "owner/repo-1", "id": 1},)

    candidate = _candidate(1, 1, "2026-08-09T00:00:00Z")
    process = Process()
    result = tmp_path / "result"
    result.write_text("", encoding="utf-8")
    worker = ActiveWorker(candidate, process, 0.0, io.StringIO(), io.StringIO(), result)
    monkeypatch.setattr(dispatch, "discover_candidates", lambda *args: (candidate,))
    monkeypatch.setattr(dispatch, "launch_worker", lambda *args: worker)
    monkeypatch.setattr(dispatch.time, "monotonic", lambda: dispatch.WORKER_TIMEOUT_SECONDS + 1)
    monkeypatch.setattr(dispatch.time, "sleep", lambda seconds: None)
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)
    arguments = argparse.Namespace(
        app_id=42, installation_id=7, private_key=Path("key.pem"), shadow=False
    )

    with pytest.raises(subprocess.SubprocessError, match="WORKER_TERMINATION_FAILURE"):
        dispatch.run_dispatch_loop(arguments, App())  # type: ignore[arg-type]

    assert process.wait_calls == 2
    assert result.exists()
    worker.stdout.close()
    worker.stderr.close()


def test_failed_cleanup_retains_stuck_worker_and_terminates_sibling(
    monkeypatch: Any, tmp_path: Path
) -> None:
    class Stuck:
        returncode = None

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: int) -> int:
            raise subprocess.TimeoutExpired("worker", timeout)

    class Pending:
        returncode = -9
        killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: int) -> int:
            return self.returncode

    first = _candidate(1, 1, "2026-08-09T00:00:00Z")
    second = _candidate(2, 2, "2026-08-09T00:00:00Z")
    first_result, second_result = tmp_path / "first", tmp_path / "second"
    first_result.write_text("", encoding="utf-8")
    second_result.write_text("", encoding="utf-8")
    pending = Pending()
    active = {
        first.key: ActiveWorker(first, Stuck(), 0.0, io.StringIO(), io.StringIO(), first_result),  # type: ignore[arg-type]
        second.key: ActiveWorker(
            second,
            pending,
            dispatch.WORKER_TIMEOUT_SECONDS + 1,
            io.StringIO(),
            io.StringIO(),
            second_result,
        ),  # type: ignore[arg-type]
    }
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

    with pytest.raises(subprocess.SubprocessError, match="WORKER_TERMINATION_FAILURE"):
        dispatch.reap_workers(active, dispatch.WORKER_TIMEOUT_SECONDS + 1)

    assert set(active) == {first.key}
    assert first_result.exists()
    assert pending.killed is True
    assert not second_result.exists()
    active[first.key].stdout.close()
    active[first.key].stderr.close()


def test_termination_failure_cannot_publish_result(tmp_path: Path) -> None:
    class Process:
        returncode = None

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: int) -> int:
            raise subprocess.TimeoutExpired("worker", timeout)

    candidate = _candidate(1, 1, "2026-08-09T00:00:00Z")
    result = tmp_path / "result"
    result.write_text("ready but untrusted", encoding="utf-8")
    worker = ActiveWorker(candidate, Process(), 0.0, io.StringIO(), io.StringIO(), result)  # type: ignore[arg-type]
    active = {candidate.key: worker}

    assert not dispatch._finish_worker(active, candidate.key, worker, True)
    assert candidate.key in active
    assert result.exists()
    worker.stdout.close()
    worker.stderr.close()


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
