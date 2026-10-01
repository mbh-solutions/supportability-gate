# Enforcement Milestone 6 Execution Directive

## Authority

- Owner authorization: 2026-07-28 owner execution prompt.
- Repository: `mbh-solutions/supportability-gate`.
- Successor project: https://github.com/orgs/mbh-solutions/projects/3.
- Active issue: https://github.com/mbh-solutions/supportability-gate/issues/21.
- Historical Project #2 must remain unchanged.
- Required immutable-standard SHA-256:
  `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.

## Terminal capability

Block vague or parallel production locations, unjustified package or module boundaries, weak
cohesion, unjustified or excessive coupling, and new production locations without complete quality
and architecture coverage.

## Approved scope

- Package and module ownership and exact-path new-location justification.
- Cohesion and coupling judgment, vague-location prohibition, and parallel-package detection.
- Complete quality and architecture coverage for every new production location.
- Source-backed evidence cross-checking and minimum integration with existing evaluator, reporting,
  semantic-review, workflow, and GitHub enforcement paths.

## Excluded scope

- General refactor-size policy or Milestone 7 characterization and golden-master sequencing.
- Enforcement Milestones 7–11.
- Changes to `docs/supportability_standard.md` or `docs/fixed_roadmap.md`.
- Target-code import or execution, arbitrary repository commands, waivers, exclusions, threshold
  overrides, cleanup, redesign, generalized frameworks, speculative infrastructure, or new
  dependencies.

## Required direct proof

- Cohesive responsibility-based and domain-based fixtures pass.
- New `utils`, `helpers`, `common`, `misc`, and `stuff` locations block.
- Unjustified parallel packages, uncovered new production locations, weak cohesion, and
  unjustified or excessive coupling block.
- Every new-location justification resolves to an exact immutable path; ownership and
  domain/responsibility claims resolve to source-backed evidence.
- Every required gate claim resolves to an executed result; every applicable changed and high-risk
  production path remains covered.
- Identical immutable inputs produce deterministic, independently verifiable evidence.
- Passing Python and TypeScript cases merge normally through protection; required failing cases
  cannot merge normally.
- Every source-proof command in `AGENTS.md` passes with Python 3.12 and the exact lock.

## Closure

Record exact commits, pull requests, runs, checks, Apps, rulesets, canaries, rejected and successful
normal merges, ownership, cohesion, coupling, path justification, coverage, validation, and gaps in
issue #21. Then update only Milestone 6 ledger fields; set Status `Complete`, Evidence `Complete`,
Scope `On scope`, Stop confirmed `Yes`; close issue #21; prove Project #2 and Milestones 7–11
unchanged; stop.
