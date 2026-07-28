# Product Completion Contract

## Authority

Product requirements come only from:

1. `docs/supportability_standard.md`
2. `docs/fixed_roadmap.md`
3. the owner-authorized active milestone execution directive

Global skills, memory, prior chats, former Governance repositories, unrelated repositories, and
agent assumptions are not product requirement sources.

Successor execution authority is
[Supportability Standard Enforcement](https://github.com/orgs/mbh-solutions/projects/3). Historical
Project #2 remains unchanged and is not authority for successor enforcement work.

## Final product objective

`supportability-gate` must centrally enforce every applicable normative Supportability Standard
clause on Python and frontend/component pull-request changes across repositories in the
`mbh-solutions` organization.

Full Supportability Standard runtime is not complete until all eleven successor milestones have
direct runtime proof.

## Successor enforcement ledger

- Enforcement Milestone 1: `COMPLETE`; Evidence `Complete`; Scope `On scope`; Stop confirmed `Yes`.
- Enforcement Milestone 1 evidence:
  - Inventory commit `b271efa0f42cc85fff1132d439fd8021a35883ba` maps 218 normative source
    statements with stable IDs, explicit applicability, owners, evidence, and executable test
    linkage. Immutable standard SHA-256 remained
    `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.
  - Pull request #27 protected-merged base `4e27c287783c8c6e4c4a59527633ddf866be6091`
    and head `b271efa0f42cc85fff1132d439fd8021a35883ba` as
    `7dfd6046571533feb2a3c4620218a2b743304188`.
  - Source Validation run `30396069668`, job/check `90399155198`, and organization-required
    Supportability Gate run `30396069559`, job/check `90399156619`, succeeded through GitHub
    Actions App ID `15368`. Active source-protection ruleset: `19767613`.
  - Python `3.12.13` exact-lock proof passed: Ruff lint/format, C901 maximum 10, strict mypy,
    Import Linter, 59 pytest tests, compileall, wheel build, fresh exact-lock install, installed CLI
    help, immutable-standard tamper test, and exact-range whitespace validation.
  - Merged-main direct canaries proved a 218-clause mapping passes while omitted clause, missing
    owner, unsupported not-applicable result, missing evidence, absent blocking test, and standard
    hash mismatch each block with its dedicated deterministic code.
- Enforcement Milestone 1 remaining work: None.
- Enforcement Milestone 2: `IN_PROGRESS`; Evidence `Missing`; Scope `On scope`; Stop confirmed
  `Not reached`.
- Enforcement Milestones 3–11: `NOT_STARTED`; not authorized.
- Current directive: `docs/enforcement_milestone_2.md`.
- Current inventory: `docs/normative_clause_inventory.json`.

## Historical delivery ledger

The five entries below preserve direct evidence from historical Project #2. They do not establish
completion of the eleven-milestone successor enforcement project or full current Standard runtime.

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
- Current status: `COMPLETE`
- Verified evidence:
  - Implementation commit: `d45e3335effc65a79087145aebfe167291dd8347`.
  - Python `3.12.13` exact-lock proof passed on 2026-07-28: Ruff lint and format; C901 at
    maximum 10; strict mypy; Import Linter; 52 pytest tests; compileall; wheel build; fresh
    environment wheel install; installed `supportability-gate --help`; immutable-standard tamper
    test; and exact-range whitespace validation excluding only the immutable standard.
  - Twenty-six direct Milestone 3 evaluations passed: complete valid evidence passes and reports
    the required judgment; behavior, characterization, separation-of-concerns, architecture and
    dependency direction, responsibility boundary, incremental refactor, review handoff and
    remaining risk, and human-review evidence each block when missing, malformed, or insufficient;
    repeated identical inputs emit byte-identical JSON.
  - Exact implementation-head self-evaluation passed twice with byte-identical JSON, all three
    changed production files covered by all five approved adapters, and no policy block.
  - Pull request #12 workflow run `30376335471` succeeded through workflow ID `320787297`,
    job/check-run ID `90332853177`, required context `Source Validation`, and GitHub Actions App ID
    `15368`.
  - Active repository ruleset `19767613` targets `refs/heads/main`, requires strict
    `Source Validation` from App ID `15368`, requires pull requests, and blocks deletion and
    non-fast-forward updates.
  - Approved gate scopes, maximum complexity 10, and the four recorded highest-risk trust-boundary
    files remain unchanged. All changed production files remain inside every approved `src` gate
    scope; no production file is excluded.
- Remaining work: None for Milestone 3. Stop; Milestone 4 is not authorized.

### 4. Organization required workflow proof in one temporary target repository.

- Capability: Apply the organization required workflow to one temporary target repository.
- Required runtime proof: Direct GitHub proof that the required workflow runs on pull-request
  changes and blocks merge when its required result fails.
- Current status: `COMPLETE`
- Verified evidence:
  - Native organization ruleset capability returned HTTP `200` from
    `GET /orgs/mbh-solutions/rulesets`; existing organization workflow rule `19746254` confirmed
    rule type `workflows` before activation.
  - Final workflow implementation commit: `5a8c5d161b5abd383cc2df7b038bf66fabe8d1e6`;
    protected merge commit: `e72d7a1e62a21278d68ce92f6b657ddaa51e0faa`; pull request #13.
    Exact implementation-head runs `30381201862` and `30381201873` succeeded through workflow IDs
    `320787297` and `322361049`, job/check-run IDs `90349205989` and `90349205908`, contexts
    `Source Validation` and `Supportability Gate`, and GitHub Actions App ID `15368`.
  - Python `3.12.13` exact-lock proof passed at the implementation head: Ruff lint and format;
    C901 at maximum 10; strict mypy; Import Linter; 52 pytest tests; compileall; wheel build; fresh
    environment wheel install; installed `supportability-gate --help`; immutable-standard tamper
    test; and exact-range whitespace validation excluding only the immutable standard.
  - Temporary target repository `mbh-solutions/supportability-gate-m4-proof-20260728`, repository ID
    `1315235523`, was initialized at `3f0d32b35e4268a2981a5a70728b94eee0b9954d` and retained for
    independently verifiable evidence.
  - Active organization ruleset `19913103` targets only that repository's `main`, has no bypass
    actors, and pins `.github/workflows/organization-required.yml` from source repository ID
    `1312412529` at `e72d7a1e62a21278d68ce92f6b657ddaa51e0faa`. Repository-only rulesets
    were empty and legacy branch protection returned HTTP `404`, so enforcement is the native
    organization workflow rule rather than a repository lookalike.
  - Passing target pull request #1 ran the native workflow at exact head
    `68ae2117d0a330a54c187727e65f0e419193e861`; run `30381513391`, workflow ID `322364809`, and
    job/check-run `90350223702` succeeded as context `Supportability Gate` from GitHub Actions App
    ID `15368`. The protected merge then succeeded as `0798dadd8e115ccbd69a16cacc69cb2e55c0bbfe`.
  - Failing target pull request #2 ran the same native workflow at exact head
    `b268d9ae7f06506b5d193b4fc09b89dbb81afe05`; run `30381622786`, workflow ID `322364809`, and
    job/check-run `90350587650` failed as context `Supportability Gate` from GitHub Actions App ID
    `15368`. Authoritative evidence reported complexity 11, decision `BLOCK`, and overall `BLOCK`.
    A normal merge attempt exited 1 because base-branch policy prohibited the merge. The pull
    request was closed unmerged and its branch deleted; only remote `main` remains.
  - No production file, threshold, approved adapter, gate scope, or highest-risk-file coverage
    changed. The workflow is directly covered by successful source and target GitHub execution;
    all existing Python production and highest-risk files remain inside every approved source gate.
- Remaining work: None for Milestone 4. Stop; Milestone 5 is not authorized.

### 5. TWMN adoption with clean, defect, gate-weakening, and scope-narrowing canaries.

- Capability: Adopt the completed gate in TWMN and exercise all frozen canaries.
- Required runtime proof: Direct TWMN pull-request and GitHub evidence that the clean canary passes
  and the defect, gate-weakening, and scope-narrowing canaries block.
- Current status: `COMPLETE`
- Verified evidence:
  - TWMN adoption pull request #21 changed only `.supportability.toml` and
    `.supportability-review.toml`; base `11b6eb668852b65c4e0d51d22d8f64c34f3c73ac`, head
    `0882007bd53d1969ef2ff92671fc2b13904f05ad`, protected repository-supported squash merge
    `9fad14040a9760e9490370365744b364967409e9`. The final contract covers production root `src`
    with all five approved adapters, maximum complexity 10, and the recorded external-I/O,
    persistence, citation, and financial-decision trust boundaries.
  - Active organization ruleset `19913103`, `supportability-gate-m4-proof`, targets both TWMN
    repository ID `1296846001` and the retained Milestone 4 proof repository ID `1315235523`.
    It has no bypass actors and preserves required workflow `.github/workflows/organization-required.yml`
    from source repository ID `1312412529` at pinned SHA
    `e72d7a1e62a21278d68ce92f6b657ddaa51e0faa`. Effective TWMN branch rules report the
    organization workflow rule directly; no repository ruleset or duplicate status check was added.
  - Clean canary pull request #22, base `9fad14040a9760e9490370365744b364967409e9`, head
    `b45066d4cdaaa89dc2dbab01a5e1294912f40a97`, passed with complexity 1 and overall `PASS`.
    Workflow run `30390778523`, workflow ID `322447435`, and job/check-run `90381509165`
    produced context `Supportability Gate` through GitHub Actions App ID `15368`; the open pull
    request was merge-eligible before it was closed unmerged and its branch deleted.
  - Complexity-defect canary pull request #23, head
    `7f1fd6eb0eab3c8009b502ef26c0b74a18752292`, failed through workflow run `30390911030`,
    workflow ID `322447435`, and job/check-run `90381956708`. Authoritative JSON reported
    `supportability_complexity_canary` at complexity 11, function decision `BLOCK`, no technical
    error, and overall `BLOCK`. A normal squash merge attempt exited 1 because base-branch policy
    prohibited the merge; the pull request was closed unmerged and its branch deleted.
  - Threshold-weakening canary pull request #24, head
    `064970aa3b7de3ec0de91454635597d3ff7ea296`, failed through workflow run `30391031210`,
    workflow ID `322447435`, and job/check-run `90382363750`. Authoritative JSON reported
    `CANDIDATE_CONTRACT_CHANGE`, `THRESHOLD_WEAKENING`, no technical error, and overall `BLOCK`.
    A normal squash merge attempt exited 1 because base-branch policy prohibited the merge; the
    pull request was closed unmerged and its branch deleted.
  - Gate-scope-narrowing canary pull request #25, head
    `b273b58d36a6611711c14e86898cdd9195474117`, failed through workflow run `30391139336`,
    workflow ID `322447435`, and job/check-run `90382725193`. Authoritative JSON reported
    `CANDIDATE_CONTRACT_CHANGE`, `GATE_SCOPE_NARROWING`, no technical error, and overall `BLOCK`.
    A normal squash merge attempt exited 1 because base-branch policy prohibited the merge; the
    pull request was closed unmerged and its branch deleted.
  - Every canary used base `9fad14040a9760e9490370365744b364967409e9`, exact head checkout,
    check context `Supportability Gate`, workflow ID `322447435`, and GitHub Actions App ID `15368`.
    All four canaries were closed unmerged; all remote canary branches were deleted; TWMN remote
    retained only `main`; local and remote `main` were clean and equal at the adoption merge.
  - Final Python `3.12.13` exact-lock source proof passed: Ruff lint and format; C901 at maximum 10;
    strict mypy; Import Linter; 52 pytest tests; compileall; wheel build; fresh-environment exact-lock
    install; installed `supportability-gate --help`; immutable-standard tamper test; and exact-range
    whitespace validation excluding only the separately hash-protected standard. Immutable standard
    SHA-256 remained `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.
  - Final TWMN validation passed with 97 tests, 3 subtests, strict mypy across 12 source files,
    structural quality, SQL supportability and behavior proof, and `VALIDATION=PASS`. Native
    Dependency Graph workflow ID `311554933` remained active and independent.
- Remaining work: None. All five frozen milestones are complete; stop and await owner authorization.

## Product status

```text
Historical deterministic gate deployable to target repositories: YES
Full Supportability Standard enforcement deployable to target repositories: NO
Full Supportability Standard runtime: NO
Current authorized work: Enforcement Milestone 2 — Trusted AI judgment channel
Next milestone authorized: NO — Enforcement Milestones 3–11 remain Not started
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

The product may claim full Supportability Standard runtime only when Enforcement Milestones 1–11
in Project #3 are `Complete`, their Evidence is `Complete`, Scope is `On scope`, Stop confirmed is
`Yes`, and their required direct proof is recorded.

Do not create a new terminal label not authorized by an active milestone directive.
