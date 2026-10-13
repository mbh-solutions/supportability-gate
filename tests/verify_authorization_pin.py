"""Verify that the literal authorization supervisor pin contains the candidate runtime."""

from __future__ import annotations

import re
from pathlib import Path

from supportability_gate import git_changes

RUNTIME_FILES = (
    "requirements-dev.lock",
    "tests/hosted_authorization_evidence.py",
    "tests/hosted_authorization_resume.py",
)


def verify(repository: Path) -> None:
    workflow = (repository / ".github/workflows/authorization-resume.yml").read_text()
    pins = re.findall(r"^\s+ref: ([0-9a-f]{40})$", workflow, re.MULTILINE)
    if len(pins) != 3 or len(set(pins)) != 1:
        raise ValueError("AUTHORIZATION_SUPERVISOR_MUST_HAVE_ONE_LITERAL_PIN")
    records: list[git_changes.CommandRecord] = []
    source_paths = tuple(
        path.relative_to(repository).as_posix()
        for path in sorted((repository / "src").rglob("*.py"))
    )
    pinned_paths = (
        git_changes.run_git(
            repository, ("ls-tree", "-r", "--name-only", pins[0], "--", "src"), records
        )
        .decode()
        .splitlines()
    )
    if sorted(path for path in pinned_paths if path.endswith(".py")) != list(source_paths):
        raise ValueError("AUTHORIZATION_SUPERVISOR_SOURCE_INVENTORY_DRIFT")
    paths = (*RUNTIME_FILES, *source_paths)
    for path in paths:
        pinned = git_changes.read_regular_blob(repository, pins[0], path, records).content
        if pinned != (repository / path).read_bytes():
            raise ValueError(f"AUTHORIZATION_SUPERVISOR_PIN_DRIFT:{path}")
    print(f"Authorization supervisor {pins[0]} matches the candidate runtime.")


if __name__ == "__main__":
    verify(Path(__file__).resolve().parents[1])
