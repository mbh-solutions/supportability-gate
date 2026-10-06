# Troubleshooting

Start with the failed check's canonical row and named cause. A red check can mean either a
policy defect or an evidence/runtime failure; the remedy depends on which was actually observed.

| Result | First action |
|---|---|
| `BLOCK` | Correct the owning policy violation and regenerate exact-change evidence. |
| `TECHNICAL_FAILURE` | Restore the named missing or untrusted runtime, artifact, identity, or evidence dependency. |
| `PASS` | Verify it belongs to the current head; still satisfy other required checks and native threads. |

## Find the authoritative cause

1. Open the failed lane and its matching workflow run/attempt. Confirm its base, head, and workflow
   SHA are the ones you intend to assess.
2. Read that lane's blocks, technical errors, evidence sources, and dependency list in
   `standard-results.json`. Read retained `stage-diagnostic.v1` diagnostics and the originating job
   output when the named cause is technical.
3. Check the relevant input or command without weakening scope or thresholds.
4. Make the correction, refresh review evidence and authorization as necessary, and obtain fresh
   complete checks on the new head. A rerun cannot make an earlier authorization current.

## Representative failures

| Symptom or code | Meaning and remedy |
|---|---|
| A touched function exceeds its permitted metric | Simplify the actual responsibility. Legacy debt must decrease and remain visible; new functions must meet maximum 10. |
| `INSUFFICIENT_REVIEW_EVIDENCE:*` | A required declaration or exact changed-boundary record is absent or incomplete. Supply the matching record for the actual diff. |
| `GATE_SCOPE_NARROWING` | The candidate removes required coverage. Restore scope; use only a supported exact deletion/profile transition when its conditions truly apply. |
| `QUALITY_BLANKET_SUPPRESSION:*` | A supported suppression undermines evidence. Remove the weakening and repair the underlying lint/type defect. |
| `UNTESTED_AREA:*` or `QUALITY_HIGH_RISK_FILE_COVERAGE:*` | Required responsibilities were not meaningfully observed. Add executable tests/assertions that exercise the actual spans. Passing unrelated tests does not establish coverage. |
| `GOLDEN_BEHAVIOR_MISMATCH:*` | The captured assertion differs or is not meaningful. Inspect the real behavior and retained baseline; do not blindly regenerate a golden to obtain PASS. |
| `CHARACTERIZATION_ENVIRONMENT_DRIFT` | The compared capture environments differ. Verify executable and dependency identities and restore the intended matched environment. |
| `MISSING_OWNER_AUTHORIZATION` / `STALE_OWNER_AUTHORIZATION` | Required trusted-owner evidence is absent or bound to older commits. Use the existing format and the exact current scope/base/head. |
| `STALE_CORRECTION_ORACLE` / `MODIFIED_CORRECTION_ORACLE` | The declared oracle is outside the exact base-to-head ancestry or one of its frozen bytes changed after the oracle commit. Restore the two-stage history and authorize the final head. |
| `UNDECLARED_CORRECTION_DELTA` / `ABSENT_DECLARED_CORRECTION_DELTA` | The observed scenario, obligation or target delta does not exactly equal the correction declaration. Correct the declaration before implementation, or remove the unintended change. |
| `CORRECTION_ORACLE_MISMATCH` / `MALFORMED_CORRECTION` | The oracle commit changed production or undeclared paths, lacks a required generic oracle role, or its identities are malformed. Rebuild the oracle commit from the exact evidence files; do not regenerate snapshots after implementation. |
| `UNAUTHORIZED_CORRECTION` | The schema-5 trusted-owner join is missing or does not bind the correction ID, oracle, final head and canonical delta. Post one fresh exact authorization after the final head exists. |
| `UNVERIFIABLE_DEPENDENCY_IDENTITY` | Locked and installed dependency evidence cannot authenticate. Inspect the retained receipts and supported optional/local dependency rules; do not simply skip missing packages. |
| `TARGET_SANDBOX_WRITE_DENIED` | A fixed target command could not write within its permitted boundary. Inspect diagnostics and supported writable areas. Do not disable isolation. |
| Missing/malformed canonical artifact or identity mismatch | Evidence cannot be trusted. Restore the correct artifact and run binding; diagnose its producer rather than substituting authored JSON. |

Some technical failures legitimately affect several lanes through one explicitly modeled shared
dependency. Read the original cause once and confirm each affected row's dependency. Unrelated
policy failures must remain in their owning lanes. Field ownership comes from the canonical map,
not the evidence filename: for example, `human_review.naming` belongs to Gate 1, `human_review.cohesion`
to Gate 4, and `human_review.intended_behavior` to Gate 5.

## Check effective protection

If checks pass but merge remains blocked, inspect strict current-head/base requirements, other
repository-native checks, and unresolved review threads. If the Gate did not run, inspect effective
organization assignment and the pinned required workflow. Editing a repository contract cannot
repair missing organization enrollment.

If the cause is a confirmed Gate defect, retain the exact source/run/attempt/artifact identities
and diagnostics for a bounded Gate maintenance change. Neither a waiver nor a report-only PASS
conversion is a supported remedy.

Implementation references: [block ownership](../../src/supportability_gate/standard_block_ownership.py),
[quality validation](../../src/supportability_gate/quality_profile.py), and
[canonical validation](../../src/supportability_gate/standard_results.py).
