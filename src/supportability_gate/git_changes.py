"""Read immutable Git identities, blobs, diffs, and changed paths."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

GIT_TIMEOUT_SECONDS = 30
_FULL_SHA = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class GitError(RuntimeError):
    """A deterministic Git evidence failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CommandRecord:
    """Deterministic record of one fixed Git invocation."""

    tool: str
    arguments: tuple[str, ...]
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True)
class RepositoryIdentity:
    """Immutable repository and commit identities."""

    remote: str
    base_sha: str
    base_tree_sha: str
    head_sha: str
    head_tree_sha: str
    git_version: str


@dataclass(frozen=True)
class GitBlob:
    """One regular Git blob at an immutable commit."""

    object_sha: str
    mode: str
    content: bytes


@dataclass(frozen=True)
class TreeBlob:
    """One regular blob identity from an immutable Git tree."""

    path: str
    object_sha: str
    mode: str


@dataclass(frozen=True)
class ChangedPath:
    """One normalized added, modified, renamed, or deleted identity."""

    status: str
    old_path: str | None
    new_path: str | None


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _record(
    records: list[CommandRecord],
    arguments: tuple[str, ...],
    exit_code: int,
    stdout: bytes,
    stderr: bytes,
) -> None:
    records.append(CommandRecord("git", arguments, exit_code, _digest(stdout), _digest(stderr)))


def run_git(
    repository: Path,
    arguments: tuple[str, ...],
    records: list[CommandRecord],
    *,
    normalized_stdout: bytes | None = None,
) -> bytes:
    """Run one fixed Git argument vector without a shell."""
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=False,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        _record(records, arguments, -1, error.stdout or b"", error.stderr or b"")
        raise GitError("GIT_TIMEOUT", f"git {' '.join(arguments)} timed out") from error
    except OSError as error:
        _record(records, arguments, -127, b"", str(error).encode("utf-8", errors="replace"))
        raise GitError("GIT_UNAVAILABLE", "git executable unavailable") from error
    recorded_stdout = completed.stdout if normalized_stdout is None else normalized_stdout
    _record(records, arguments, completed.returncode, recorded_stdout, completed.stderr)
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        detail = detail.replace(str(repository), "<repository>")
        raise GitError("GIT_COMMAND_FAILED", f"git {' '.join(arguments)}: {detail}")
    return completed.stdout


def validate_repository(path: Path, records: list[CommandRecord]) -> Path:
    """Require an absolute path naming the exact Git worktree root."""
    if not path.is_absolute():
        raise GitError("RELATIVE_REPOSITORY", "repository path must be absolute")
    try:
        repository = path.resolve(strict=True)
    except OSError as error:
        raise GitError("MISSING_REPOSITORY", "repository path does not exist") from error
    top = (
        run_git(
            repository,
            ("rev-parse", "--show-toplevel"),
            records,
            normalized_stdout=b"<repository>\n",
        )
        .decode()
        .strip()
    )
    if Path(top).resolve() != repository:
        raise GitError("NOT_REPOSITORY_ROOT", "repository path must name the Git worktree root")
    return repository


def _resolve_commit(repository: Path, value: str, records: list[CommandRecord]) -> str:
    if _FULL_SHA.fullmatch(value) is None:
        raise GitError("MUTABLE_REF", "base-ref and head-ref must be full immutable commit SHAs")
    resolved = (
        run_git(repository, ("rev-parse", "--verify", f"{value}^{{commit}}"), records)
        .decode()
        .strip()
    )
    if resolved.lower() != value.lower():
        raise GitError("COMMIT_MISMATCH", "Git resolved a different commit identity")
    return resolved.lower()


def _tree_sha(repository: Path, commit: str, records: list[CommandRecord]) -> str:
    return (
        run_git(repository, ("rev-parse", f"{commit}^{{tree}}"), records).decode().strip().lower()
    )


def _remote_identity(value: str) -> str:
    remote = value.strip()
    if remote.startswith("git@") and ":" in remote:
        host, path = remote[4:].split(":", 1)
    else:
        parsed = urlparse(remote)
        if not parsed.hostname:
            raise GitError("INVALID_REMOTE", "origin must identify a network repository")
        host, path = parsed.hostname, parsed.path.lstrip("/")
    path = path.removesuffix(".git").strip("/")
    if not host or not path:
        raise GitError("INVALID_REMOTE", "origin must identify a network repository")
    return f"{host.lower()}/{path}"


def inspect_repository(
    repository: Path,
    base_ref: str,
    head_ref: str,
    records: list[CommandRecord],
) -> RepositoryIdentity:
    """Resolve all immutable identities and exact Git version."""
    base_sha = _resolve_commit(repository, base_ref, records)
    head_sha = _resolve_commit(repository, head_ref, records)
    remote = run_git(repository, ("remote", "get-url", "origin"), records).decode().strip()
    version = run_git(repository, ("--version",), records).decode().strip()
    return RepositoryIdentity(
        remote=_remote_identity(remote),
        base_sha=base_sha,
        base_tree_sha=_tree_sha(repository, base_sha, records),
        head_sha=head_sha,
        head_tree_sha=_tree_sha(repository, head_sha, records),
        git_version=version,
    )


def read_regular_blob(
    repository: Path,
    commit: str,
    path: str,
    records: list[CommandRecord],
) -> GitBlob:
    """Read one path without following worktree symlinks."""
    entry = run_git(repository, ("ls-tree", "-z", commit, "--", path), records)
    if not entry:
        raise GitError("MISSING_BLOB", f"missing path at commit: {path}")
    header, separator, returned_path = entry.rstrip(b"\0").partition(b"\t")
    parts = header.decode("ascii").split()
    if not separator or len(parts) != 3 or returned_path.decode("utf-8") != path:
        raise GitError("INVALID_TREE_ENTRY", f"invalid Git tree entry: {path}")
    mode, object_type, object_sha = parts
    if object_type != "blob" or mode not in {"100644", "100755"}:
        raise GitError("SYMLINK_OR_NONFILE", f"path is not a regular file: {path}")
    content = run_git(repository, ("cat-file", "blob", f"{commit}:{path}"), records)
    return GitBlob(object_sha.lower(), mode, content)


def list_regular_blobs(
    repository: Path,
    commit: str,
    roots: tuple[str, ...],
    records: list[CommandRecord],
) -> tuple[TreeBlob, ...]:
    """List regular blobs below fixed repository-relative roots."""
    raw = run_git(repository, ("ls-tree", "-r", "-z", "--full-tree", commit, "--", *roots), records)
    blobs: list[TreeBlob] = []
    for entry in raw.rstrip(b"\0").split(b"\0") if raw else ():
        header, separator, encoded_path = entry.partition(b"\t")
        parts = header.decode("ascii").split()
        if not separator or len(parts) != 3:
            raise GitError("INVALID_TREE_ENTRY", "invalid recursive Git tree entry")
        mode, object_type, object_sha = parts
        if object_type == "blob" and mode in {"100644", "100755"}:
            blobs.append(TreeBlob(encoded_path.decode("utf-8"), object_sha.lower(), mode))
        elif object_type == "blob":
            raise GitError("SYMLINK_OR_NONFILE", encoded_path.decode("utf-8"))
    return tuple(sorted(blobs, key=lambda item: item.path))


def changed_paths(
    repository: Path,
    base_sha: str,
    head_sha: str,
    records: list[CommandRecord],
) -> tuple[ChangedPath, ...]:
    """Detect normalized added, modified, renamed, and deleted paths."""
    raw = run_git(
        repository,
        ("diff", "--name-status", "-z", "--find-renames=50%", base_sha, head_sha, "--"),
        records,
    )
    tokens = raw.rstrip(b"\0").split(b"\0") if raw else []
    changes: list[ChangedPath] = []
    index = 0
    while index < len(tokens):
        status = tokens[index].decode("ascii")
        index += 1
        kind = status[0]
        if kind == "R":
            old_path, new_path = tokens[index : index + 2]
            index += 2
            changes.append(ChangedPath("RENAMED", old_path.decode(), new_path.decode()))
        elif kind in {"A", "M", "T", "D"}:
            path = tokens[index].decode()
            index += 1
            normalized = {"A": "ADDED", "M": "MODIFIED", "T": "MODIFIED", "D": "DELETED"}[kind]
            changes.append(
                ChangedPath(
                    normalized, path if kind != "A" else None, path if kind != "D" else None
                )
            )
        else:
            raise GitError("UNSUPPORTED_CHANGE", f"unsupported Git change status: {status}")
    return tuple(
        sorted(changes, key=lambda item: (item.new_path or item.old_path or "", item.status))
    )


def changed_head_lines(
    repository: Path,
    base_sha: str,
    head_sha: str,
    path: str,
    records: list[CommandRecord],
) -> tuple[int, ...]:
    """Return exact changed line numbers in the head blob."""
    patch = run_git(
        repository,
        ("diff", "--unified=0", "--no-color", "--no-ext-diff", base_sha, head_sha, "--", path),
        records,
    ).decode("utf-8", errors="strict")
    lines: set[int] = set()
    for line in patch.splitlines():
        match = _HUNK.match(line)
        if match:
            start = int(match.group(1))
            count = int(match.group(2) or "1")
            lines.update(range(start, start + count) if count else (max(1, start),))
    return tuple(sorted(lines))
