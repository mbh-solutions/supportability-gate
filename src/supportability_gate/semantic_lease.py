"""Coordinate cross-process semantic-worker publication leases."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from supportability_gate.semantic_contract import SemanticReviewError


def _lock(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
        else:
            import fcntl

            getattr(fcntl, "flock")(
                handle.fileno(), getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB")
            )
    except OSError as error:
        raise SemanticReviewError("EVALUATION_IN_PROGRESS") from error


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
    else:
        import fcntl

        getattr(fcntl, "flock")(handle.fileno(), getattr(fcntl, "LOCK_UN"))


@contextmanager
def exclusive_lease(path: Path) -> Iterator[None]:
    """Hold one local cross-process lease."""
    with path.open("a+b") as handle:
        _lock(handle)
        try:
            yield
        finally:
            _unlock(handle)


@contextmanager
def publication_lease(path: Path | None) -> Iterator[None]:
    """Hold one validated worker lease through final-check publication."""
    if path is None:
        yield
        return
    with path.open("r+b") as handle:
        _lock(handle)
        try:
            handle.seek(0)
            if path.with_name(path.name + ".revoked").exists() or handle.read() != b"active":
                raise SemanticReviewError("WORKER_LEASE_REVOKED")
            yield
        finally:
            _unlock(handle)


def revoke_publication_lease(path: Path) -> None:
    """Prevent every future publication acquisition by one worker."""
    marker = path.with_name(path.name + ".revoked")
    try:
        marker.touch(exist_ok=False)
    except FileExistsError:
        pass
