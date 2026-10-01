# Eight Gates

Each required check owns a defined Standard responsibility. The Gate uses measured facts,
deterministic decisions, author declarations, and authenticated owner attestations at different
strengths. [Evidence Model](Evidence-Model.md) defines those terms.

| # | Exact required check context | Purpose |
|---|---|---|
| 1 | `Supportability 1 - Cyclomatic Complexity` | Keep changed functions within the fixed complexity policy. |
| 2 | `Supportability 2 - Separation of Concerns` | Require source-bound records of changed responsibility boundaries. |
| 3 | `Supportability 3 - Dependency Direction` | Check supported static import direction and cycles. |
| 4 | `Supportability 4 - Domain Modularity` | Govern new production locations and their coverage and ownership records. |
| 5 | `Supportability 5 - Characterization` | Preserve known behavior using authenticated repeatable captures. |
| 6 | `Supportability 6 - Incremental Refactor` | Enforce authorized, bounded, runnable refactor steps and series. |
| 7 | `Supportability 7 - Quality Gates` | Authenticate fixed quality commands and changed/high-risk coverage. |
| 8 | `Supportability 8 - Review Handoff` | Require current source-bound review and derived handoff evidence. |

## 1. Cyclomatic Complexity

**Enforcement and evidence:** measured base/head function metrics and deterministic decisions.
New functions and touched functions already within the limit must remain at maximum 10. A touched
legacy function above 10 must decrease; a progressive pass retains remaining debt and next target.
Python metrics are reconciled with pinned Ruff/McCabe behavior; TypeScript uses the fixed adapter.
This lane also owns structural validation of the naming and reviewability author declarations;
valid declarations do not constitute independent naming-quality judgment.

**Example failure:** a new function measures 11, or a legacy function remains at 17 after being
touched. **Limit:** a lower metric does not prove clear naming or a good responsibility boundary.

## 2. Separation of Concerns

**Enforcement and evidence:** deterministic validation of the required changed-boundary inventory,
with author declarations describing before/after responsibilities. Each function, component, or
module record must bind to the actual supported changed source identity.

**Example failure:** a changed function has no matching responsibility record.
**Limit:** complete prose is not independent proof that a function has one coherent job.

## 3. Dependency Direction

**Enforcement and evidence:** deterministic decisions over supported Python/TypeScript import
graphs and fixed architecture rules, plus author declarations explaining reviewed changes.

**Example failure:** a supported static dependency creates a cycle or an unassessed production
module has no graph coverage. **Limit:** dynamic imports and unclassified architecture are outside
the certified graph; explanations are not machine judgments of design quality.

## 4. Domain Modularity

**Enforcement and evidence:** deterministic checks of new production paths and required gate
coverage, with author declarations naming the existing owner, explaining new locations, and
recording the human cohesion assessment.

**Example failure:** a new module has no ownership record or is outside required validation scope.
**Limit:** ownership meaning and cohesion remain human judgments.

## 5. Characterization

**Enforcement and evidence:** authenticated measured base/head captures and deterministic checks
of identity, repeatability, golden behavior, covered responsibilities, and retained obligations.
This lane also owns the intended-behavior author declarations. Each declared scenario runs twice
at base and twice at head. Supported schema-2 obligations keep
assertion identities stable when scenario packaging changes.

**Example failure:** an observable result changes unexpectedly, or a scenario replacement drops
an existing obligation. **Limit:** defined scenarios provide bounded proof, not exhaustive intended
behavior. Help-only, constant-output, or import-only observations cannot substitute for required
meaningful behavior coverage.

## 6. Incremental Refactor

**Enforcement and evidence:** deterministic scope, target, sequencing, and runnability checks.
Where authorization is required, an authenticated owner attestation binds the exact repository,
base/head, changed scope, targets, and refactor series. Related tests have exact source associations.

**Example failure:** an authorization names an earlier head or skips a predecessor step.
**Limit:** path and span bounds do not establish semantic smallness or quality. The format and
trusted-owner boundary are in [Refactor Authorization](Refactor-Authorization.md).

## 7. Quality Gates

**Enforcement and evidence:** authenticated command outcomes, source/dependency receipts, observed
coverage, and deterministic anti-weakening decisions. Python and TypeScript select closed quality
profiles; mixed repositories run both. Required changed and high-risk responsibilities must have
the specified proof. Supported blanket suppressions cannot silently create covered PASS.

**Example failure:** tests pass while a high-risk responsibility remains unobserved, or contract
scope is narrowed around changed code. **Limit:** this is the fixed admitted profile, not arbitrary
repository scripts, comprehensive security scanning, or every possible runtime platform.

## 8. Review Handoff

**Enforcement and evidence:** exact-change review identity and handoff-field validation, plus a
handoff derived from authenticated result sources. Other review fields belong to their named
Standard owners; human-review fields are not all owned by Gate 8. An unchanged review blob is
stale for a full-process change.

**Example failure:** review evidence is stale, a required handoff field is empty, or an authored completion
claim replaces the derived handoff marker. **Limit:** freshness proves current attribution, not
renewed human thought or independent prose-quality judgment.

## Results and lane ownership

| Result | Meaning |
|---|---|
| `PASS` | Applicable defined rules and required evidence are satisfied. |
| `BLOCK` | A deterministic policy violation is owned by this lane. |
| `TECHNICAL_FAILURE` | Required evidence or a named runtime/identity dependency cannot be trusted. |
| `NOT_APPLICABLE_SHORT_TASK` | A lane is irrelevant to an authenticated narrow short task; Gate 7 still applies. |

One lane's policy violation does not automatically fail unrelated lanes. A shared technical
failure must identify its modeled dependency and affected standards. Unknown block ownership
fails closed. Each lane enforces its row in `standard-results.v3`; an aggregate cannot replace it.

Implementation references: [block ownership](../../src/supportability_gate/standard_block_ownership.py),
[result composition](../../src/supportability_gate/standard_results.py), and
[enforcement](../../src/supportability_gate/standard_results_enforcer.py).
