# Enforcement Milestone 9 Execution Directive

## Authority

- Owner authorization: 2026-07-29 owner execution prompt.
- Repository: `mbh-solutions/supportability-gate`.
- Successor project: https://github.com/orgs/mbh-solutions/projects/3.
- Active issue: https://github.com/mbh-solutions/supportability-gate/issues/24.
- Historical Project #2 must remain unchanged.
- Required immutable-standard SHA-256:
  `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.

## Terminal capability

Run approved stack-native lint, format, complexity, type, test, build/package, and architecture
gates. Prove changed-file and highest-risk-file coverage plus exclusions, thresholds, and scope
anti-weakening. No quality claim passes unless every required gate ran successfully and covered
every changed and highest-risk production path.

## Approved scope

- Fixed approved Python and frontend/component validation profiles.
- Isolated GitHub-hosted execution and exact gate-result attestation.
- Changed/high-risk path coverage mapping and anti-weakening.
- Minimum integration with the existing evaluator, reporting, characterization, refactor policy,
  semantic review, organization workflow, and GitHub enforcement.
- Focused tests and protected Python/TypeScript canaries required for direct proof.

## Excluded scope

- Arbitrary repository-controlled shell commands, executable paths, environment controls, command
  substitution, or shell execution.
- New exclusions, waivers, threshold overrides, or scope narrowing.
- Target-code import or execution on the owner workstation.
- Semantic review-handoff truth assigned to Milestone 10.
- Enforcement Milestones 10–11.
- Cleanup, redesign, generalized frameworks, speculative infrastructure, optional configuration,
  or new dependencies not strictly required.
- Changes to `docs/supportability_standard.md` or `docs/fixed_roadmap.md`.

## Required direct proof

- Complete Python profile runs every approved lint, format, C901/complexity, strict type, test,
  build/package, and architecture gate.
- Complete frontend/component profile runs every approved lint, format, complexity-equivalent,
  typecheck, test, build, and architecture/import-boundary gate.
- Authoritative evidence binds exact repository, base SHA, head SHA, workflow SHA, commands,
  results, files, scopes, thresholds, exclusions, changed paths, high-risk paths, and untested areas.
- Passing Python and TypeScript protected canaries merge normally.
- Declared-but-unexecuted tool, failed or missing command, uncovered changed or high-risk production
  file, added exclusion, threshold weakening, gate-scope narrowing, and production movement outside
  governed scope block deterministically.
- Each required protected failing canary fails the required check, rejects a normal merge, closes
  unmerged, and deletes its branch.
- Every source-proof command in `AGENTS.md` passes with Python 3.12 and the exact lock; direct M9
  evaluation is byte-identical across at least two runs.

Approved target commands run only through fixed argument vectors, finite timeouts, captured output,
and isolated GitHub-hosted jobs. Never execute target code on the owner workstation.

## Protected delivery

- Deliver implementation through branch `codex/enforcement-milestone-9` and one normally protected
  implementation pull request that references, but does not close, issue #24.
- Bind exact authenticated owner authorization to repository, base, head, scope, targets, sequence,
  and GitHub user ID `229662739` when Milestone 8 policy applies.
- Require exact-head Source Validation, characterization base/head, Supportability Gate, and
  Supportability Semantic Review evidence before normal merge.
- Pin organization ruleset `19929500` to the exact merged M9 workflow SHA without changing active
  enforcement, repository scope, workflow identity, or zero-bypass state.
- Reuse the retained Python and TypeScript proof repositories unless direct evidence proves them
  insufficient.
- After runtime proof, use a separate ledger-only evidence branch and protected pull request with
  `Closes #24`; update only Milestone 9 ledger/status/evidence/remaining-work, product status,
  current directive, and next-authorized-milestone fields.

## Closure

Record exact commits, pull requests, runs, jobs/checks, Apps, artifacts, digests, authorization,
rulesets, workflow pin, canaries, commands/results, coverage, exclusions, thresholds, untested
areas, deterministic evidence hashes, cleanup, and remaining product gap in issue #24. Then verify
both M9 pull requests are linked; set M9 Status `Complete`, Evidence `Complete`, Scope `On scope`,
Stop confirmed `Yes`; close issue #24; prove Project #2 byte-identical and Milestones 10–11
unchanged; verify clean local/origin/GitHub main and proof environments; stop before Milestone 10.
