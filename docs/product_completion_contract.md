# Product Completion Contract

## Authority

Product requirements come only from:

1. `docs/supportability_standard.md`
2. `docs/fixed_roadmap.md`
3. the owner-authorized active milestone execution directive

Global skills, memory, prior chats, former Governance repositories, unrelated repositories, and
agent assumptions are not product requirement sources.

## Final product objective

`supportability-gate` must centrally enforce the Supportability Standard on pull-request changes
across repositories in the `mbh-solutions` organization.

The product is not complete or deployable to target repositories until all five frozen milestones
have direct runtime proof.

## Frozen milestone ledger

Allowed status values are `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED_EXTERNAL`, and `COMPLETE`.

Do not record Codex claims as verified evidence unless direct repository, command, artifact,
workflow, check-run, ruleset, or GitHub state supports them.

### 1. Changed files, touched functions, progressive C901 complexity, deterministic evidence.

- Capability: Evaluate changed Python production functions between immutable Git commits, apply the
  progressive C901 policy, and emit deterministic evidence.
- Required runtime proof: All Milestone 1 source gates; published GitHub workflow proof; observed
  required-check context, check-run, workflow run, job, and producing App identities; active `main`
  ruleset bound to that check and App; successful harmless probe; probe cleanup; remote `main` equal
  to the published Milestone 1 commit; clean local worktree; and final recorded evidence.
- Current status: `COMPLETE`
- Verified evidence:
  - Published source implementation commit: `f7fe1daea040e6901b07ebde8d24be0184ce3958`.
  - Python `3.12.13` exact-lock proof passed on 2026-07-28: Ruff lint and format; C901 at
    maximum 10; strict mypy; Import Linter; 20 pytest tests; compileall; wheel build; fresh
    environment wheel install; installed `supportability-gate --help`; immutable-standard tamper
    test; and whitespace validation from `f13b2a8e79b2cad7c8b5b1e8fbdaadac237e4b09` through
    `f0fc89106c21bfc71560fb8b3943bc0df1687400` excluding only the immutable standard.
  - Immutable standard SHA-256:
    `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.
  - GitHub workflow run `30206119346` succeeded for harmless probe pull request #1 at
    `99a5dd2a3cc6316a911cf5297376b4d672514899`; workflow ID `320787297` and job/check-run ID
    `89804372250` produced required context `Source Validation` through GitHub Actions App ID
    `15368`.
  - Active repository ruleset `19767613` targets `refs/heads/main`, requires strict
    `Source Validation` from App ID `15368`, requires pull requests, and blocks deletion and
    non-fast-forward updates.
  - Probe pull request #1 closed unmerged after success; its branch was deleted; only `main`
    remained remotely. Remote `main` and clean local `HEAD` both resolved to
    `f0fc89106c21bfc71560fb8b3943bc0df1687400` before this evidence-only completion change.
  - Source gate coverage includes all production and test Python files for lint and format; all
    production Python files for C901 and strict typing; the complete package graph for import
    boundaries; runtime tests; compile; wheel build/install; CLI smoke; immutable-standard
    integrity; and complete changed-file whitespace except the separately hash-protected standard.
    No threshold or gate scope changed, and no production file was excluded.
- Remaining work: None for Milestone 1. Stop; Milestone 2 is not authorized.

### 2. Approved gate adapters, changed-file gate coverage, high-risk-file gate coverage, threshold and scope anti-weakening.

- Capability: Enforce approved adapters, required gate coverage, and threshold and scope
  anti-weakening.
- Required runtime proof: Direct evaluations proving changed-file and high-risk-file coverage,
  approved adapter enforcement, and deterministic blocking of threshold or scope weakening.
- Current status: `COMPLETE`
- Verified evidence:
  - Implementation commit: `47e31397b06a1375d007ca2f7f83dd8eca35d4ce`.
  - Python `3.12.13` exact-lock proof passed on 2026-07-28: Ruff lint and format; C901 at
    maximum 10; strict mypy; Import Linter; 26 pytest tests; compileall; wheel build; fresh
    environment wheel install; installed `supportability-gate --help`; immutable-standard tamper
    test; and whitespace validation from `ec71c5671ae05a5e02915a4d6e9a531547bf1f86` through
    `47e31397b06a1375d007ca2f7f83dd8eca35d4ce` excluding only the immutable standard.
  - Seven direct Milestone 2 evaluations passed: approved adapters pass; unapproved adapters,
    changed-file coverage gaps, highest-risk-file coverage gaps, threshold weakening, and gate-scope
    narrowing block; repeated blocking evidence is byte-identical.
  - Pull request #11 workflow run `30373526163` succeeded at the implementation commit through
    workflow ID `320787297`, job/check-run ID `90323233603`, required context
    `Source Validation`, and GitHub Actions App ID `15368`.
  - Active repository ruleset `19767613` targets `refs/heads/main`, requires strict
    `Source Validation` from App ID `15368`, requires pull requests, and blocks deletion and
    non-fast-forward updates.
  - Approved adapters are fixed to C901 touched-function, Import Linter, strict mypy, pytest, and
    Ruff lint adapters. Every adapter covers the complete `src` production scope, including all
    changed production files and the four recorded highest-risk trust-boundary files. Complexity
    maximum remained 10; production scope was not narrowed; no production file was excluded.
- Remaining work: None for Milestone 2. Stop; Milestone 3 is not authorized.

### 3. Behavior proof, characterization evidence, architecture-review evidence, responsibility-boundary reporting.

- Capability: Require and report behavior, characterization, architecture-review, and
  responsibility-boundary evidence.
- Required runtime proof: Direct evaluations proving required evidence is accepted when valid and
  blocked when missing, malformed, or insufficient.
- Current status: `NOT_STARTED`
- Verified evidence: None recorded.
- Remaining work: Entire milestone; no owner-authorized execution directive.

### 4. Organization required workflow proof in one temporary target repository.

- Capability: Apply the organization required workflow to one temporary target repository.
- Required runtime proof: Direct GitHub proof that the required workflow runs on pull-request
  changes and blocks merge when its required result fails.
- Current status: `NOT_STARTED`
- Verified evidence: None recorded.
- Remaining work: Entire milestone; no owner-authorized execution directive.

### 5. TWMN adoption with clean, defect, gate-weakening, and scope-narrowing canaries.

- Capability: Adopt the completed gate in TWMN and exercise all frozen canaries.
- Required runtime proof: Direct TWMN pull-request and GitHub evidence that the clean canary passes
  and the defect, gate-weakening, and scope-narrowing canaries block.
- Current status: `NOT_STARTED`
- Verified evidence: None recorded.
- Remaining work: Entire milestone; no owner-authorized execution directive.

## Product status

```text
Deployable to target repositories: NO
Full Supportability Standard runtime: NO
Current authorized work: None — Milestone 2 complete; stop
Next milestone authorized: NO
```

## Milestone transition rules

- Only one milestone may be active at a time.
- No future milestone work may be implemented during the active milestone.
- A milestone is not complete based only on plans, documentation, source code, local tests, or
  narrative summaries when runtime or GitHub proof is required.
- A milestone status changes to `COMPLETE` only after direct evidence satisfies its active execution
  directive.
- Completion evidence must be recorded in this contract before the milestone is considered closed.
- After a milestone reaches `COMPLETE`, stop.
- The next milestone may begin only after the owner provides and authorizes its execution directive.
- Do not add cleanup, hardening, future-proofing, abstractions, adapters, infrastructure, or
  follow-up work outside the active directive.

## Final completion rule

The product may claim full Supportability Standard runtime only when all five milestones are
`COMPLETE` and their required evidence is recorded.

Do not create a new terminal label not authorized by an active milestone directive.
