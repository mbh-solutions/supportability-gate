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
- Current status: `IN_PROGRESS`
- Verified evidence: Local implementation and local source proof exist. The staged repository tree
  directly contains the package, contract, tests, documentation, and source-validation workflow.
  No live publication or source-protection evidence is recorded here.
- Remaining work: Live GitHub publication, workflow proof, required-check identity, active ruleset,
  probe verification, cleanup, and final evidence.

### 2. Approved gate adapters, changed-file gate coverage, high-risk-file gate coverage, threshold and scope anti-weakening.

- Capability: Enforce approved adapters, required gate coverage, and threshold and scope
  anti-weakening.
- Required runtime proof: Direct evaluations proving changed-file and high-risk-file coverage,
  approved adapter enforcement, and deterministic blocking of threshold or scope weakening.
- Current status: `NOT_STARTED`
- Verified evidence: None recorded.
- Remaining work: Entire milestone; no owner-authorized execution directive.

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
Current authorized work: Complete Milestone 1 publication and source protection
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
