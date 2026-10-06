from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from supportability_gate import git_changes, quality_runner

_SPEC = importlib.util.spec_from_file_location(
    "materialization_quality_runner", Path(__file__).with_name("hosted_quality_profile.py")
)
assert _SPEC and _SPEC.loader
hosted_quality = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(hosted_quality)


def _git(repository: Path, *arguments: str, content: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        input=content,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout


@pytest.fixture
def committed_tree(tmp_path: Path) -> tuple[Path, str, dict[str, bytes]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    contents = {
        "assets/binary.dat": b"\0\xff\r\nblob\nheader\0\n",
        "assets/empty.dat": b"",
        "assets/spaces and caf\u00e9.txt": b"first\nsecond\n\n",
        "scripts/check.sh": b"#!/bin/sh\nprintf 'committed\\n'\n",
        **{f"src/file_{index:02}.txt": f"committed {index}\n".encode() for index in range(12)},
    }
    for name, value in contents.items():
        destination = repository / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)
    _git(repository, "add", "--all")
    _git(repository, "update-index", "--chmod=+x", "scripts/check.sh")
    _git(repository, "commit", "-m", "Immutable source fixture")
    commit = _git(repository, "rev-parse", "HEAD").decode().strip()
    (repository / "assets/binary.dat").write_bytes(b"dirty worktree bytes")
    (repository / "untracked.txt").write_bytes(b"must not enter materialized source")
    return repository, commit, contents


def test_materialization_is_byte_exact_and_two_git_processes(
    committed_tree: tuple[Path, str, dict[str, bytes]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, commit, expected = committed_tree
    destination = tmp_path / "materialized"
    records: list[git_changes.CommandRecord] = []
    invocations: list[list[str]] = []
    chmods: dict[str, int] = {}
    real_run, real_chmod = subprocess.run, Path.chmod

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        invocations.append(arguments)
        return real_run(arguments, **kwargs)

    def chmod(path: Path, mode: int, **kwargs: object) -> None:
        chmods[path.relative_to(destination).as_posix()] = mode
        real_chmod(path, mode, **kwargs)

    monkeypatch.setattr(git_changes.subprocess, "run", run)
    monkeypatch.setattr(Path, "chmod", chmod)

    paths = hosted_quality._materialize_git_tree(repository, commit, destination, records)

    assert paths == tuple(sorted(expected))
    assert {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    } == set(expected)
    assert {name: (destination / name).read_bytes() for name in paths} == expected
    assert len(invocations) == len(records) == 2
    assert [arguments[1] for arguments in invocations] == ["ls-tree", "cat-file"]
    assert "--batch" in invocations[1]
    assert chmods == {name: 0o755 if name == "scripts/check.sh" else 0o644 for name in expected}
    if os.name != "nt":
        assert (destination / "scripts/check.sh").stat().st_mode & 0o777 == 0o755
        assert (destination / "assets/binary.dat").stat().st_mode & 0o777 == 0o644


def test_batch_keeps_executable_identity_and_respects_roots(
    committed_tree: tuple[Path, str, dict[str, bytes]],
) -> None:
    repository, commit, expected = committed_tree

    blobs = dict(git_changes.read_regular_blobs(repository, commit, ("scripts",), []))

    assert set(blobs) == {"scripts/check.sh"}
    executable = blobs["scripts/check.sh"]
    assert executable.mode == "100755"
    assert executable.content == expected["scripts/check.sh"]
    expected_object = hashlib.sha1(
        f"blob {len(executable.content)}\0".encode() + executable.content
    ).hexdigest()
    assert executable.object_sha == expected_object


def test_empty_file_batches_bound_header_and_record_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_changes, "_BLOB_BATCH_FILES", 2)
    entries = tuple(
        (git_changes.TreeBlob(f"empty-{index}", "a" * 40, "100644"), 0) for index in range(5)
    )

    batches = tuple(git_changes._blob_batches(entries))

    assert tuple(len(batch) for batch in batches) == (2, 2, 1)
    assert tuple(item for batch in batches for item in batch) == entries


def test_blob_batches_are_bounded_and_oversize_files_are_read_separately(
    committed_tree: tuple[Path, str, dict[str, bytes]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, commit, expected = committed_tree
    limit = 16
    real_run = subprocess.run
    requests: list[tuple[list[str], bytes | None]] = []
    sizes = {
        hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest(): len(content)
        for content in expected.values()
    }

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if arguments[1] == "cat-file":
            requests.append((arguments, kwargs.get("input")))
        return real_run(arguments, **kwargs)

    monkeypatch.setattr(git_changes, "_BLOB_BATCH_BYTES", limit)
    monkeypatch.setattr(git_changes.subprocess, "run", run)

    blobs = dict(git_changes.read_regular_blobs(repository, commit, (".",), []))

    assert {name: blob.content for name, blob in blobs.items()} == expected
    batches = [content for arguments, content in requests if "--batch" in arguments]
    assert len(batches) > 1
    assert all(
        content is not None and sum(sizes[sha.decode()] for sha in content.splitlines()) <= limit
        for content in batches
    )
    oversized = [arguments for arguments, _ in requests if "--batch" not in arguments]
    assert {arguments[3] for arguments in oversized} == {
        sha for sha, size in sizes.items() if size > limit
    }
    assert all(arguments[2] == "blob" for arguments in oversized)
    assert oversized


@pytest.mark.parametrize(
    "corruption", ["object", "type", "size", "content", "truncated", "trailing"]
)
def test_corrupt_batch_is_rejected_before_materialization(
    committed_tree: tuple[Path, str, dict[str, bytes]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    repository, commit, _ = committed_tree
    destination = tmp_path / "rejected"
    real_run = subprocess.run

    def run(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        completed = real_run(arguments, **kwargs)
        if arguments[1] != "cat-file":
            return completed
        header, body = completed.stdout.split(b"\n", 1)
        object_sha, object_type, size = header.split(b" ")
        substitutions = {
            "object": b"0" * len(object_sha) + b" " + object_type + b" " + size + b"\n" + body,
            "type": object_sha + b" tree " + size + b"\n" + body,
            "size": object_sha + b" " + object_type + b" 99999999\n" + body,
            "content": header + b"\n" + bytes([body[0] ^ 1]) + body[1:],
            "truncated": completed.stdout[:-1],
            "trailing": completed.stdout + b"unexpected trailing bytes",
        }
        return subprocess.CompletedProcess(
            completed.args, completed.returncode, substitutions[corruption], completed.stderr
        )

    monkeypatch.setattr(git_changes.subprocess, "run", run)

    with pytest.raises(git_changes.GitError):
        hosted_quality._materialize_git_tree(repository, commit, destination, [])

    assert not destination.exists()


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:\\escape"])
def test_unsafe_tree_path_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    raw = b"100644 blob " + b"a" * 40 + b" 1\t" + path.encode() + b"\0"
    monkeypatch.setattr(git_changes, "run_git", lambda *args, **kwargs: raw)

    with pytest.raises(git_changes.GitError) as caught:
        dict(git_changes.read_regular_blobs(tmp_path, "b" * 40, (".",), []))

    assert caught.value.code == "INVALID_TREE_PATH"


def test_committed_symlink_is_rejected_without_following_it(
    committed_tree: tuple[Path, str, dict[str, bytes]], tmp_path: Path
) -> None:
    repository, _, _ = committed_tree
    symlink_object = _git(repository, "hash-object", "-w", "--stdin", content=b"../outside")
    _git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{symlink_object.decode().strip()},link",
    )
    _git(repository, "commit", "-m", "Symlink negative control")
    commit = _git(repository, "rev-parse", "HEAD").decode().strip()
    destination = tmp_path / "rejected"

    with pytest.raises(git_changes.GitError) as caught:
        hosted_quality._materialize_git_tree(repository, commit, destination, [])

    assert caught.value.code == "SYMLINK_OR_NONFILE"
    assert not destination.exists()


def test_wheel_build_copy_is_mutable_without_changing_execution_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    execution = tmp_path / "execution"
    output = tmp_path / "output"
    repository.mkdir()
    execution.mkdir()
    (repository / "untracked.txt").write_bytes(b"not immutable source")
    (execution / "module.py").write_bytes(b"VALUE = 1\n")
    plan = quality_runner.CommandPlan(
        "python.build-wheel.v1", ("python", "-m", "build", "--wheel"), (), "wheel-members", ()
    )
    build_source = quality_runner.command_work_directory(output, plan.adapter) / "source"

    def sandbox(*args: object, **kwargs: object) -> tuple[str, ...]:
        assert kwargs["repository"] == execution
        assert kwargs["workdir"] == "/work/source"
        return ("fixture-wheel",)

    def run(arguments: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert arguments == ("fixture-wheel",)
        assert (build_source / "module.py").read_bytes() == b"VALUE = 1\n"
        assert not (build_source / "untracked.txt").exists()
        (build_source / "module.py").write_bytes(b"VALUE = 2\n")
        (build_source / "build-generated.txt").write_bytes(b"wheel build output")
        return subprocess.CompletedProcess(arguments, 0, b"built", b"")

    monkeypatch.setattr(quality_runner, "sandbox_command", sandbox)
    monkeypatch.setattr(hosted_quality.subprocess, "run", run)
    monkeypatch.setattr(hosted_quality, "_proof", lambda *args: ((), (), "a" * 64, 0))
    monkeypatch.setattr(
        hosted_quality,
        "_materialize_git_tree",
        lambda *args: pytest.fail("wheel source must reuse the immutable materialization"),
    )

    result = hosted_quality._run_command(plan, repository, output, execution_target=execution)

    assert result.exit_code == 0
    assert (execution / "module.py").read_bytes() == b"VALUE = 1\n"
    assert not (execution / "build-generated.txt").exists()
