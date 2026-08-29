"""Derive exact bounded responsibility targets without executing target code."""

from __future__ import annotations

from pathlib import Path

from supportability_gate import contract, function_changes, git_changes

TargetSpan = tuple[str, function_changes.ResponsibilitySpan]


def _profile_source(path: str, language: str) -> bool:
    suffixes = (".py", ".pyi") if language == "python" else (".cts", ".mts", ".ts", ".tsx")
    return path.endswith(suffixes)


def _production_change_path(
    change: git_changes.ChangedPath, policy: contract.Contract
) -> str | None:
    if change.new_path and policy.is_production_path(change.new_path):
        return change.new_path
    if change.old_path and policy.is_production_path(change.old_path):
        return change.old_path
    return None


def _change_spans(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    change: git_changes.ChangedPath,
    path: str,
    records: list[git_changes.CommandRecord],
) -> tuple[TargetSpan, ...]:
    if change.new_path is None:
        content = git_changes.read_regular_blob(
            repository, identity.base_sha, path, records
        ).content
        return _bind(
            path,
            function_changes.responsibility_spans(
                path, content, set(range(1, len(content.splitlines()) + 1))
            ),
        )
    head = git_changes.read_regular_blob(repository, identity.head_sha, path, records).content
    head_lines = set(
        git_changes.changed_head_lines(
            repository,
            identity.base_sha,
            identity.head_sha,
            path,
            records,
            include_deletion_anchor=False,
        )
    )
    if change.old_path is None:
        return _bind(path, function_changes.responsibility_spans(path, head, head_lines))
    base = git_changes.read_regular_blob(
        repository, identity.base_sha, change.old_path, records
    ).content
    base_lines = set(
        git_changes.changed_base_lines(
            repository, identity.base_sha, identity.head_sha, path, records
        )
    )
    surviving, deleted = function_changes.changed_responsibility_spans(
        path, base, head, base_lines, head_lines
    )
    return _bind(path, (*surviving, *deleted))


def _bind(
    path: str, spans: tuple[function_changes.ResponsibilitySpan, ...]
) -> tuple[TargetSpan, ...]:
    return tuple((path, span) for span in spans)


def _profiled_side(
    repository: Path,
    commit_sha: str,
    path: str,
    records: list[git_changes.CommandRecord],
) -> tuple[bytes, tuple[function_changes.ResponsibilitySpan, ...]] | None:
    try:
        content = git_changes.read_regular_blob(repository, commit_sha, path, records).content
        spans = function_changes.responsibility_spans(
            path, content, set(range(1, len(content.splitlines()) + 1))
        )
    except function_changes.PythonSourceError:
        return None
    except git_changes.GitError as error:
        if error.code != "SYMLINK_OR_NONFILE":
            raise
        return None
    return content, spans


def _renamed_spans(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    change: git_changes.ChangedPath,
    policy: contract.Contract,
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[TargetSpan, ...], tuple[str, ...]]:
    old_path, new_path = change.old_path, change.new_path
    if old_path is None or new_path is None:
        return (), ()
    base_profiled = policy.is_production_path(old_path) and _profile_source(
        old_path, policy.language
    )
    head_profiled = policy.is_production_path(new_path) and _profile_source(
        new_path, policy.language
    )
    base = (
        _profiled_side(repository, identity.base_sha, old_path, records) if base_profiled else None
    )
    head = (
        _profiled_side(repository, identity.head_sha, new_path, records) if head_profiled else None
    )
    unbounded = tuple(
        path
        for path, production, profiled, side in (
            (old_path, policy.is_production_path(old_path), base_profiled, base),
            (new_path, policy.is_production_path(new_path), head_profiled, head),
        )
        if production and profiled and side is None
    )
    if base is not None and head is not None:
        try:
            base_lines = set(
                git_changes.changed_base_lines(
                    repository,
                    identity.base_sha,
                    identity.head_sha,
                    new_path,
                    records,
                    old_path=old_path,
                )
            )
        except git_changes.GitError as error:
            if error.code != "GIT_TIMEOUT":
                raise
            spans = _bind(new_path, head[1])
            unbounded = (*unbounded, old_path)
        else:
            moved, deleted = function_changes.renamed_responsibility_spans(
                old_path, new_path, base[0], head[0], base_lines
            )
            spans = (*_bind(new_path, moved), *_bind(old_path, deleted))
    elif head is not None:
        spans = _bind(new_path, head[1])
    elif base is not None:
        spans = _bind(old_path, base[1])
    else:
        spans = ()
    return spans, unbounded


def _spans_or_unbounded(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    change: git_changes.ChangedPath,
    path: str,
    policy: contract.Contract,
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[TargetSpan, ...], tuple[str, ...]]:
    if (
        change.old_path is not None
        and change.new_path is not None
        and change.old_path != change.new_path
    ):
        return _renamed_spans(repository, identity, change, policy, records)
    if not _profile_source(path, policy.language):
        return (), (path,)
    try:
        return _change_spans(repository, identity, change, path, records), ()
    except function_changes.PythonSourceError:
        return (), (path,)
    except git_changes.GitError as error:
        if error.code != "SYMLINK_OR_NONFILE":
            raise
        return (), (path,)


def derive(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    changes: tuple[git_changes.ChangedPath, ...],
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return exact source targets and unbounded production paths."""
    targets: list[str] = []
    unbounded: list[str] = []
    for change in changes:
        profiled_paths = tuple(
            path
            for path in (change.old_path, change.new_path)
            if path and policy.is_production_path(path) and _profile_source(path, policy.language)
        )
        if not profiled_paths:
            continue
        path = _production_change_path(change, policy)
        if path is None:
            continue
        spans, newly_unbounded = _spans_or_unbounded(
            repository, identity, change, path, policy, records
        )
        unbounded.extend(newly_unbounded)
        if not spans:
            unbounded.extend(profiled_paths)
            continue
        if (
            change.old_path is not None
            and change.new_path is not None
            and change.old_path != change.new_path
            and policy.is_production_path(change.new_path)
            and _profile_source(change.new_path, policy.language)
            and all(target_path != change.new_path for target_path, _ in spans)
        ):
            unbounded.append(change.new_path)
        targets.extend(
            f"{target_path}::{item.kind}:{item.name}:{item.start_line}-{item.end_line}"
            for target_path, item in spans
        )
    return tuple(sorted(set(targets))), tuple(sorted(set(unbounded)))
