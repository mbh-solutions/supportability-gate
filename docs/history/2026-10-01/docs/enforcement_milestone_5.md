# Enforcement Milestone 5 Execution Directive

## Authority

- Owner authorization: 2026-07-28 owner execution prompt.
- Repository: `mbh-solutions/supportability-gate`.
- Successor project: https://github.com/orgs/mbh-solutions/projects/3.
- Active issue: https://github.com/mbh-solutions/supportability-gate/issues/20.
- Historical Project #2 must remain unchanged.
- Required immutable-standard SHA-256:
  `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.

## Terminal capability

Execute approved Python and TypeScript import-boundary checks and block import cycles,
cross-layer inversions, and forbidden domain dependencies on presentation, framework, package,
database, or external-service glue.

## Approved scope

- Fixed Python and TypeScript architecture profiles.
- Acyclic import-graph proof and dependency-direction policy.
- Evidence cross-checking that proves the architecture gate executed.
- Minimum integration with existing evaluator, evidence, reporting, semantic-review, workflow, and
  GitHub enforcement paths.

## Excluded scope

- Broader cohesion or domain-modularization judgments reserved for Milestone 6.
- Enforcement Milestones 6–11.
- Changes to `docs/supportability_standard.md` or `docs/fixed_roadmap.md`.
- Target-code import or execution, arbitrary repository commands, waivers, exclusions, threshold
  overrides, cleanup, redesign, generalized frameworks, and speculative infrastructure.

## Required direct proof

- Valid layered Python and TypeScript fixtures pass.
- Python and TypeScript circular imports block.
- Cross-layer inversions and forbidden domain-to-infrastructure or presentation imports block.
- Declared-but-unexecuted architecture gates and incomplete production-path coverage block.
- Dependency-direction explanation cites the verified import graph and changed production paths.
- Every applicable changed and high-risk production path is covered by an executed architecture
  check.
- Identical immutable inputs produce deterministic, independently verifiable evidence.
- Required checks run on exact pull-request heads; representative required failures reject normal
  merge; passing Python and TypeScript heads merge normally without admin, bypass, or policy
  weakening.
- Every source-proof command in `AGENTS.md` passes with Python 3.12 and the exact lock.

## Closure

Record exact commits, pull requests, runs, checks, Apps, rulesets, canaries, rejected and successful
normal merges, verified graphs, changed-path coverage, validation, and gaps in issue #20. Then update
only Milestone 5 ledger fields; set Status `Complete`, Evidence `Complete`, Scope `On scope`, Stop
confirmed `Yes`; close issue #20; prove Project #2 and Milestones 6–11 unchanged; stop.
