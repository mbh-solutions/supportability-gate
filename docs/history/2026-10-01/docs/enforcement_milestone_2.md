# Enforcement Milestone 2 Execution Directive

## Authority

- Owner authorization: 2026-07-28 owner execution prompt.
- Repository: `mbh-solutions/supportability-gate`.
- Successor project: https://github.com/orgs/mbh-solutions/projects/3.
- Active issue: https://github.com/mbh-solutions/supportability-gate/issues/17.
- Historical Project #2 must remain unchanged.
- Required immutable-standard SHA-256:
  `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.

## Terminal capability

A local outbound GitHub App verifier consumes immutable evidence, calls `gpt-5.6-sol` through
`http://127.0.0.1:8317/v1/responses` using CLIProxyAPI subscription OAuth, and posts an exact-head
`Supportability Semantic Review` check. Missing or untrusted evidence, transport, identity, model,
schema, or verdict fails closed.

## Approved scope

- Outbound verifier, GitHub App identity, immutable evidence, strict request/result schema.
- Exact-head result binding, polling, and one user-scoped scheduled task.
- CLIProxyAPI localhost subscription-OAuth integration; no OpenAI API-key billing.
- Direct passing, blocking, technical-failure, prompt-injection, replay, and offline tests.
- Project #3 linkage and truthful Milestone 2 status/evidence changes.

## Excluded scope

- Changes to `docs/supportability_standard.md` or `docs/fixed_roadmap.md`.
- Proxy network exposure, personal-PC GitHub Actions runner, or OpenAI API keys.
- Codex cloud review as enforcement evidence.
- Principle-specific rubrics beyond feasibility/security fixtures.
- Enforcement Milestones 3–11, target-code execution, arbitrary repository commands, waivers,
  cleanup, redesign, generalized frameworks, and speculative infrastructure.

## Required direct proof

- Clean live App/proxy/model fixture passes.
- Authentication failure, proxy outage, model drift, timeout, malformed schema, refusal,
  uncertainty, conflicting verdict, and evidence-hash mismatch block.
- Prompt injection cannot grant tools, execute target code, access network resources, or escape
  schema.
- Machine offline leaves check absent or pending and normal protected merge blocked.
- Verdict binds repository, base SHA, head SHA, evidence hash, rubric/schema versions, App identity,
  and returned model; required replay behavior is deterministic.
- Protected merge uses normal merge without bypass, and Codex cloud review remains advisory.
- Every source-proof command in `AGENTS.md` passes with Python 3.12 and exact lock.

## Closure

Record exact commits, pull requests, runs/checks/Apps, model identity, schemas, evidence hashes,
passing and blocking canaries, offline proof, validation, coverage, and gaps in issue #17. Then set
Status `Complete`, Evidence `Complete`, Scope `On scope`, Stop confirmed `Yes`; close issue #17;
leave issues #18–26 `Not started`; prove Project #2 unchanged; stop.
