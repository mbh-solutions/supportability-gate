# PR #288 first attempt: retained failure evidence

Archived October 7, 2026, at the owner's request to remove a historical attempt from
the active branch list. This record preserves a failed candidate; it does not grant
acceptance, qualify a release, or authorize further implementation.

## What happened

[PR #288](https://github.com/mbh-solutions/supportability-gate/pull/288) reduced quality
capture overhead. Its first candidate, `dc80e892d7e504726cdf6def77e245b70fd917c4`, failed
[protected run 37415287708](https://github.com/mbh-solutions/supportability-gate/actions/runs/37415287708)
on October 6, 2026. The PR's account attributes the failure to timing diagnostics in
machine-readable stdout and fixed-command fixture drift. The original artifacts and
logs below are the retained evidence; that account is not a new independent diagnosis.

The corrected candidate `5f598ace6c687b525325ba7eb5da66122e0323a3` merged as
`479aedf94e761c4fb3d5274d1647c69552ab8b33`. The failed candidate was left on
`codex/pr288-first-attempt` for preservation. That archival branch looked like active
work and is replaced by this recoverable record. Delete the remote branch only after
this archive has merged and its bytes have been read back from the default branch.

## Retained evidence

- [Manifest](manifest.json): exact commit and run identities, original artifact IDs,
  file sizes, and SHA-256 digests of every retained evidence file.
- [Candidate Git bundle](candidate.bundle): the exact failed commit and its changed
  objects, with its parent as an explicit prerequisite.
- [Run metadata](run.json): original run identity and its `failure` conclusion.
- [Supportability results](supportability-evidence-37415287708-1.zip): original
  results and compact qualification bundle, without changing failed decisions.
- [Quality profile](quality-profile-37415287708-1.zip).
- [Base characterization](characterization-base-37415287708-1.zip) and
  [head characterization](characterization-head-37415287708-1.zip).
- [Original run logs](run-logs.zip).

The ZIP files are the original downloaded bytes, retained here so GitHub Actions
expiration does not erase the record. These files are historical data, not inputs to
current qualification. No active branch or new release tag is needed to retain them.

## Verify and recover

Use a full clone containing parent commit
`73b9a98412d323b958d9df67932c3bf1ae666209`, which is already an ancestor of `main`.
From the repository root, compare each retained file's SHA-256 and size with the
manifest, then inspect the bundle:

```text
git bundle verify docs/history/2026-10-07/pr288-first-attempt/candidate.bundle
git fetch docs/history/2026-10-07/pr288-first-attempt/candidate.bundle refs/remotes/origin/codex/pr288-first-attempt
git show --stat FETCH_HEAD
```

`FETCH_HEAD` must equal `dc80e892d7e504726cdf6def77e245b70fd917c4`. This fetch imports
the historical commit without creating an active branch or checking out its code.

Before publication, the bundle was verified and restored into a separate bare
repository containing only the parent history; the recovered commit matched exactly
and Git's full object-integrity check passed. Every original artifact ZIP passed its
archive integrity check. Archive delivery changes documentation and retained data
only; production source, workflows, immutable policy, thresholds and deployment pins
remain unchanged.
