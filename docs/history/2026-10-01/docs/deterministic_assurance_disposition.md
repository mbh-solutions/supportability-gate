# Deterministic assurance disposition

This is the current Project #25 disposition index as of September 22, 2026. It separates corrected
deterministic defects, accepted product boundaries, and still-unresolved operational risks. A link
to a closed issue is the durable delivery record; the exact protected PR, run, artifact, digest,
canary, and controller readback are recorded there and in the product completion contract.

## Slice state

| Slice | Project state | Direct record |
|---|---|---|
| S00 | Done / Proven / On scope / Yes | [Issue #201](https://github.com/mbh-solutions/supportability-gate/issues/201) |
| S01 | Done / Proven / On scope / Yes | [Issue #202](https://github.com/mbh-solutions/supportability-gate/issues/202) |
| S02 | Done / Proven / On scope / Yes | [Issue #203](https://github.com/mbh-solutions/supportability-gate/issues/203) |
| S03 | Done / Proven / On scope / Yes | [Issue #204](https://github.com/mbh-solutions/supportability-gate/issues/204) |
| S04 | Done / Proven / On scope / Yes | [Issue #205](https://github.com/mbh-solutions/supportability-gate/issues/205) |
| S05 | Done / Proven / On scope / Yes | [Issue #206](https://github.com/mbh-solutions/supportability-gate/issues/206) |
| S06 | Done / Proven / On scope / Yes | [Issue #207](https://github.com/mbh-solutions/supportability-gate/issues/207) |
| S07 | Done / Proven / On scope / Yes | [Issue #208](https://github.com/mbh-solutions/supportability-gate/issues/208) |
| S08 | In Review / Partial / On scope / No | [Issue #209](https://github.com/mbh-solutions/supportability-gate/issues/209) and [durable bundles](qualification/README.md) |

S08 remains an executor handoff until an independent controller verifies the evidence. Project #25
remains open and has no successor slice.

## Audit findings and assumptions

| ID | Current disposition | Direct evidence or boundary |
|---|---|---|
| F1 | Corrected and proven | S02 isolates target execution and binds capture provenance; issue #203 records protected PASS and failure reproductions. |
| F2 | Corrected and proven | S04 detects blanket supported suppressions and anti-weakening violations; issue #205 records protected canaries. |
| F3a | Corrected within the declared profile | S05 requires executable responsibility-span coverage; import-only evidence is not meaningful behavior. |
| F3b | Corrected within the declared profile | S05 retains multi-input/output obligations and rejects help-only or constant-output substitutes. |
| F3c | Corrected and proven | S05 binds stable obligations across scenario replacement and blocks removed obligations. |
| F4 | Corrected and proven | S03 independently pins Source Integrity at `a645c1d3c6fd7f9d9c804b173c5b2ac0a420d786`; candidate source cannot qualify its own guard. |
| F5a | Corrected wording | Current docs define PASS as deterministic evidence satisfaction, not semantic quality. |
| F5b | Accepted product boundary | Prose truth, naming, cohesion, and ownership quality remain human judgments. |
| F5c | Accepted product boundary | Static roles cover the declared parser graph; dynamic imports and unusual or undeclared roots are not certified. |
| F5d | Accepted product boundary | Freshness and exact-change binding prove current attribution, not renewed human thought. |
| F6a | Corrected and proven | S00 binds the normative registry to immutable source text, references, applicability, evidence class, and tests. |
| F6b | Corrected wording | The 218 IDs and shared test references are traceability, not 218 independent semantic proofs. |
| F7a | Corrected and proven | S06 retains stable identities for Python and TypeScript accessors and Python overloads. |
| F7b | Corrected and proven | S06 reconciles supported Python `match` complexity with pinned Ruff/McCabe behavior. |
| F8a | Corrected and proven | S07 permits narrow related-test authorization only through exact source association and authenticated scope. |
| F8b | Accepted product boundary | A path/span footprint is deterministic scope evidence, not semantic proof that a change is small. |
| F8c | Corrected and proven | S07 requires one authenticated series, exact predecessor merge, adjacent step, and stable target identity. |
| F9 | Corrected and proven | S01 gives every Standard row an explicit evidence owner and fails closed for absent review sections. |
| F10a | Corrected and proven | S01 classifies malformed nested evidence as TECHNICAL_FAILURE rather than policy BLOCK. |
| F10b | Corrected and proven | S01 retains bounded stage diagnostics and original-cause identity; S08 bundles preserve nested diagnostics. |
| F11a | Corrected in current surfaces | README, scope, matrix, this registry, and the completion contract now state the live deterministic boundary and Project state. Historical records remain dated. |
| F11b | Corrected for retained S08 proof | Repository-hosted compact bundles survive Actions expiry and restore with existing validators; external artifact expiry stays explicit. |
| F12a | Partially mitigated; still unresolved | The immutable restore image and exact Python/lock identity are retained. Node patch and hosted runner image are moving; fresh TypeScript/mixed consumer dependency identities failed closed. |
| F12b | Corrected and proven | S02 removed only dependencies proven unused; no unrelated dependency refresh is part of S08. |
| F12c | Defect-driven correction only | S01 simplified malformed-input boundaries. Module size and dynamic dictionaries alone were not treated as a rewrite requirement. |
| F12d | Untested risk; explicitly deferred | No retained concurrent-head reproduction established wasted obsolete-head execution, so cancellation was not added. |
| A01 | Accepted product boundary | Only committed Python, TypeScript, and mixed profiles and their declared production paths are governed. |
| A02 | Accepted product boundary | The Gate is not a general security, deployment, or exhaustive acceptance system. |
| A03 | Accepted product boundary | Semantic Review is retired. Codex review is optional and advisory; no model result is required for merge. |
| A04 | Operationally resolved without inference | The owner authorized removal of two dead ruleset selectors during S00; current scope is the five live repository IDs. A 404 alone was never treated as deletion proof. |
| A05 | Accepted product boundary | Adversarial fixtures and bounded protected canaries establish named behavior, not exhaustive assurance. |

## Final S08 boundary

The deployed organization workflow is `2c461f5f8554e1092fe9a6706904578dcbe2fc37`.
The organization Gate ruleset is active, strict, zero-bypass, covers five repositories, and requires
all eight App `15368` contexts. Source Integrity remains independently pinned and zero-bypass.

The protected implementation and directory-fix PRs provide normal merge proof. Fresh Python,
TypeScript, mixed, policy-poison, and provenance canaries exercised the deployed bundle path and
are closed unmerged. Their bundle restore proofs are durable, but the consumer baselines and the
policy canary's independent technical error prevent a complete fresh PASS/BLOCK/TECHNICAL_FAILURE
matrix. That gap is why S08 Evidence is Partial rather than Proven.
