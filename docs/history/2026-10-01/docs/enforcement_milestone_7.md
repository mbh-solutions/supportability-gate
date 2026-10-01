# Enforcement Milestone 7 Execution Directive

## Authority

- Owner authorization: 2026-07-28 owner execution prompt.
- Repository: `mbh-solutions/supportability-gate`.
- Successor project: https://github.com/orgs/mbh-solutions/projects/3.
- Active issue: https://github.com/mbh-solutions/supportability-gate/issues/22.
- Historical Project #2 must remain unchanged.
- Required immutable-standard SHA-256:
  `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.

## Terminal capability

Execute base/head characterization in isolated GitHub-hosted jobs and require authenticated
baseline behavior plus compatible post-change proof tied to the same immutable commits.

## Approved scope

- Safe isolated base/head characterization execution using fixed Python and TypeScript profiles.
- Behavior fingerprints, exact commit and artifact identity, deterministic replay, and compatibility
  decisions.
- One authenticated format for tests, sample I/O, snapshots, golden files, CLI captures, and focused
  regression proofs.
- Minimum integration with existing evaluator, reporting, semantic-review, workflow, and GitHub
  enforcement paths.

## Excluded scope

- Refactor-size or strangler sequencing assigned to Milestone 8.
- General stack-native quality-gate execution assigned to Milestone 9.
- Enforcement Milestones 8–11.
- Changes to `docs/supportability_standard.md` or `docs/fixed_roadmap.md`.
- Target-code import or execution outside isolated GitHub-hosted jobs; arbitrary repository commands,
  executable paths, environment controls, waivers, exclusions, threshold overrides, cleanup,
  redesign, generalized frameworks, speculative infrastructure, or new dependencies.

## Required direct proof

- Existing and newly added characterization scenarios pass with exact base/head identity.
- Missing baseline, changed golden output, unauthenticated proof text, head-only test claims, stale
  artifacts, incompatible behavior, and deterministic replay drift block.
- Every artifact resolves to exact immutable commits and records independently verifiable identity,
  behavior fingerprints, fixed commands, results, and compatibility decisions.
- Every applicable changed and high-risk production path remains covered.
- Passing Python and TypeScript canaries merge normally; required failing canaries cannot merge
  normally.
- Every source-proof command in `AGENTS.md` passes with Python 3.12 and the exact lock.

## Closure

Record exact commits, pull requests, runs, jobs/checks, Apps, rulesets, workflow pins, artifacts,
canaries, merge decisions, fingerprints, compatibility, validation, and gaps in issue #22. Then
update only Milestone 7 ledger fields; verify every Milestone 7 pull request is linked; set Status
`Complete`, Evidence `Complete`, Scope `On scope`, Stop confirmed `Yes`; close issue #22; prove
Project #2 and Milestones 8–11 unchanged; stop.
