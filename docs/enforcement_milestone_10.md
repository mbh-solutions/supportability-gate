# Enforcement Milestone 10 Execution Directive

## Authority

- Owner authorization: 2026-07-29 M10 execution controls prompt.
- Repository: `mbh-solutions/supportability-gate`.
- Successor project: https://github.com/orgs/mbh-solutions/projects/3.
- Active issue: https://github.com/mbh-solutions/supportability-gate/issues/25.
- Historical Project #2 must remain unchanged.
- Required immutable-standard SHA-256:
  `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.

## Terminal capability

Cross-check every completion-report claim against the exact diff, authenticated command results,
AI claim reviews, and resolvable source lines. Unsupported nonempty prose, stale or contradictory
evidence, hidden failures, and false risk claims block.

## Approved scope

- ADR-style completion-report schema and exact claim/evidence linkage.
- Exact-head source resolution and M9 `quality-gates.v2` artifact reuse.
- Machine-result, coverage, architecture, risk, and gap cross-checking.
- M10-specific model/effort and full-packet transport qualification.
- Protected passing and blocking canaries required for direct proof.

## Excluded scope

- Enforcement Milestone 11 or final cross-stack qualification.
- New target commands, executable paths, environment controls, exclusions, waivers, thresholds, or
  scope narrowing.
- Target-code import or workstation execution.
- M9 enforcement redesign without a focused M10 acceptance failure.
- Cleanup, generalized infrastructure, unrelated tooling, or new dependencies.
- Changes to `docs/supportability_standard.md` or `docs/fixed_roadmap.md`.

## Required direct proof

- A complete source-backed report passes; plausible unsupported prose blocks despite valid shape.
- Invented commands, stale SHAs, unresolved citations, contradicted claims, hidden failed commands,
  missing sections, and false no-risk claims each block separately.
- Candidate model/effort pairs run the exact representative M10 packet before production binding.
- Evidence binds model, reasoning effort, packet and response hashes, returned model, terminal
  status, parser result, exact run attempt, artifact ID/digest, report blob, base, and head.
- Technical model or transport failure publishes no trusted semantic check; substantive `BLOCK`
  remains visible.
- Owner authorization bytes pass the installed production parser before posting.
- Source proof in `AGENTS.md`, protected passing/blocking canaries, normal protected merge, and API
  read-backs all pass.

## Protected delivery

- Deliver implementation through `codex/enforcement-milestone-10` and a normally protected pull
  request that references, but does not close, issue #25.
- Reuse the M9 organization workflow, `quality-gates.v2`, and authenticated artifacts.
- Use only fresh complete workflow runs when evidence binds `run_attempt`; never rerun failed jobs.
- After runtime proof, use a ledger-only protected pull request with `Closes #25` and update only M10
  ledger/status/evidence/remaining-work, product status, current directive, and next-authorization
  fields.

## Closure

Record exact commits, runs, checks, Apps, models, efforts, packet/response hashes, artifacts,
authorization, rulesets, workflow pin, canaries, source proof, report truth blocks, cleanup, and
remaining gap. Set M10 `Complete / Complete / On scope / Yes`, close issue #25, verify Project #2
unchanged and M11 untouched, then stop.
