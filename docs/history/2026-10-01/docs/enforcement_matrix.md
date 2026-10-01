# Enforcement Matrix

This matrix maps current deterministic responsibilities and assurance boundaries. Current product
status, authorized work, and historical evidence are recorded in the
[Product Completion Contract](product_completion_contract.md).

| Current responsibility | Owner | Evidence boundary |
|---|---|---|
| Immutable Standard, roadmap, and canonical 218-clause registry | Source Integrity plus Source Validation | Independently pinned bytes, trusted schema/provenance validation, and sandboxed conformance |
| Touched-function complexity and progressive tightening | Supportability 1 | Measured facts plus deterministic decisions |
| Separation-of-concerns records | Supportability 2 | Author declarations; qualitative truth is not machine judged |
| Static dependency direction and cycles | Supportability 3 | Deterministic decisions over the supported static graph |
| Domain/module placement and coverage | Supportability 4 | Deterministic path/coverage rules plus author declarations for ownership meaning |
| Base/head characterization | Supportability 5 | Authenticated measured artifacts and deterministic compatibility rules |
| Focused incremental refactor scope and runnability | Supportability 6 | Deterministic decisions; exact broad scope requires authenticated owner attestation |
| Fixed quality commands, coverage, and anti-weakening | Supportability 7 | Authenticated measurements and deterministic decisions |
| Review handoff and remaining-risk records | Supportability 8 | Process evidence and author declarations; no prose-quality judgment |
| Qualification bundle construction and offline restoration | Deterministic evidence job | Existing canonical validators, exact file digests, bounded archive members, and optional release context |

| Supportability requirement | Enforcement class | Historical origin |
|---|---|---:|
| Immutable owner-authored standard integrity | DETERMINISTIC | 1 |
| Git base/head changed-file identity | DETERMINISTIC | 1 |
| Touched Python function and method binding | DETERMINISTIC | 1 |
| McCabe/C901 maximum 10 for new and non-legacy touched functions | DETERMINISTIC | 1 |
| Progressive tightening for touched legacy functions | DETERMINISTIC | 1 |
| Deterministic JSON and derived Markdown evidence | DETERMINISTIC | 1 |
| Product source lint, format, type, complexity, test, package, and import-boundary gates | DETERMINISTIC | 1 |
| Approved external gate adapters | DETERMINISTIC | 2 |
| Changed-file gate coverage proof | DETERMINISTIC | 2 |
| Highest-risk-file gate coverage proof | DETERMINISTIC | 2 |
| Threshold anti-weakening | DETERMINISTIC | 2 |
| Gate-scope anti-narrowing | DETERMINISTIC | 2 |
| Behavior and characterization artifacts | MEASURED_FACT / DETERMINISTIC_DECISION | 5 |
| Separation-of-concerns evidence | AUTHOR_DECLARATION | 2 |
| Static architecture and dependency direction | DETERMINISTIC_DECISION | 3 |
| Responsibility-boundary reporting | AUTHOR_DECLARATION | 2 |
| Incremental refactor runnability and scope | DETERMINISTIC_DECISION / AUTHENTICATED_OWNER_ATTESTATION | 6 |
| Review handoff and remaining-risk evidence | AUTHOR_DECLARATION / PROCESS_EVIDENCE | 8 |
| Naming, cohesion, design quality, and exhaustive intended behavior | ACCEPTED_BOUNDARY | Not independently judged |
| Outside-profile code and unclassified/dynamic architecture | ACCEPTED_BOUNDARY | Not certified |
| Organization required-workflow enforcement proof | DETERMINISTIC | Protected workflow |
| Source workflow, immutable inputs, tool/action pins, and validator conformance | DETERMINISTIC | Independently pinned Source Integrity workflow |
| Protected clean, policy-BLOCK, and technical-failure canaries | DETERMINISTIC | Never merge |
| Repository-retained qualification bundle restoration | DETERMINISTIC | Revalidates the recorded decision and identity without the original Actions artifact |
| Actions retention and moving hosted runtime labels | OPERATIONAL_LIMITATION | Recorded explicitly; never converted to PASS |

## Source Integrity promotion and rollback

The source-only organization rule targets only `mbh-solutions/supportability-gate`. It pins
`.github/workflows/source-integrity.yml` to an exact normally merged commit and requires the
`Source Integrity` context from GitHub Actions App `15368`, in addition to Source Validation and
all eight existing contexts. The pinned guard checks candidate bytes and runs its own conformance
fixture against candidate validator code inside the S02 read-only, no-network container boundary.
Candidate workflows and tests never supply that fixture.

Guard changes use the normal protected path: qualify and merge the source PR under the current
pin, run the newly merged guard against never-merge positive, BLOCK, and TECHNICAL_FAILURE
canaries, then change only the source-integrity workflow SHA to that qualified merge. Rollback
changes only that SHA to the previously qualified revision; it never removes a rule, context,
strictness, review-thread requirement, or zero-bypass protection.

| Candidate state | Source Integrity result |
|---|---|
| Current clean source upgrade retaining required controls | PASS |
| Source Validation reduced or disabled | BLOCK (`SOURCE_VALIDATION_*`) |
| Standard, roadmap, or canonical inventory tamper | BLOCK (`IMMUTABLE_SOURCE_MISMATCH` or `INVENTORY:*`) |
| Candidate validator accepts a trusted negative case | BLOCK (`CONFORMANCE_CASE:*`) |
| Missing runtime, result, identity, or artifact | TECHNICAL_FAILURE |
