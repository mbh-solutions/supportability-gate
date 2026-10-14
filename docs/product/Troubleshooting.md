# Troubleshooting

Start with the failed check's canonical row and named cause. A red check can mean either a
policy defect or an evidence/runtime failure; the remedy depends on which was actually observed.

| Result | First action |
|---|---|
| `BLOCK` | Correct the owning policy violation and regenerate exact-change evidence. |
| `TECHNICAL_FAILURE` | Restore the named missing or untrusted runtime, artifact, identity, or evidence dependency. |
| `PASS` | Verify it belongs to the current head; still satisfy other required checks and native threads. |

## Find the authoritative cause

1. In the pull request's **Checks** view, open the failed Supportability check and its workflow
   run. Confirm the proposed commit matches the PR. An implementer can also compare the recorded
   base, head, workflow commit, and attempt.
2. On the workflow run's **Summary** page, find **Artifacts** and download
   `supportability-evidence-<run-id>-<attempt>`. Sign in with repository access if needed.
   Unzip it and open `standard-results.json` in a text editor. Find the failed check's
   `check_context`, `policy_blocks`, and `technical_errors`. An implementer should inspect its
   evidence sources and retained stage diagnostics. Missing final evidence requires inspection
   of the failed producer job's log.
3. Check the relevant input or command without weakening scope or thresholds.
4. Make the correction, refresh source-bound review evidence as necessary, and obtain fresh
   complete checks on the new head. Evidence for a different commit cannot qualify the proposed head.

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
| An authorization comment is requested | No approval comment is part of the documented workflow. Ask the maintainer to verify the effective organization workflow and the run's identity. Report the PR/run links; do not manufacture a technical-review attestation. |
| `STALE_CORRECTION_ORACLE` / `MODIFIED_CORRECTION_ORACLE` | The declared oracle is outside the exact base-to-head ancestry or one of its frozen bytes changed after the oracle commit. Prepare a fresh oracle-before-implementation transaction; a later byte restoration or approval comment cannot waive the history violation. |
| `UNDECLARED_CORRECTION_DELTA` / `ABSENT_DECLARED_CORRECTION_DELTA` | The observed scenario, obligation or target delta does not exactly equal the correction declaration. Correct the declaration before implementation, or remove the unintended change. |
| `CORRECTION_ORACLE_MISMATCH` / `MALFORMED_CORRECTION` | The oracle commit changed production or undeclared paths, lacks a required generic oracle role, or its identities are malformed. Rebuild the oracle commit from the exact evidence files; do not regenerate snapshots after implementation. |
| `UNVERIFIABLE_DEPENDENCY_IDENTITY` | Locked and installed dependency evidence cannot authenticate. Inspect the retained receipts and supported optional/local dependency rules; do not simply skip missing packages. |
| `TARGET_SANDBOX_WRITE_DENIED` | A fixed target command could not write within its permitted boundary. Inspect diagnostics and supported writable areas. Do not disable isolation. |
| Missing/malformed canonical artifact or identity mismatch | Evidence cannot be trusted. Restore the correct artifact and run binding; diagnose its producer rather than substituting authored JSON. |

Some technical failures legitimately affect several lanes through one explicitly modeled shared
dependency. Read the original cause once and confirm each affected row's dependency. Unrelated
policy failures must remain in their owning lanes. Field ownership comes from the canonical map,
not the evidence filename: for example, `human_review.naming` belongs to Gate 1, `human_review.cohesion`
to Gate 4, and `human_review.intended_behavior` to Gate 5.

## A check is taking a long time

Open the running job's log to distinguish dependency preparation, behavior capture, test execution,
and evidence verification. **Characterize Base** and **Characterize Head** run independently;
each repeats successful scenarios to check repeatability. Four quality partitions divide the
test suite. Independent jobs can continue after a failure to retain useful diagnostics.

Use the characterization and quality timing files described in [Evidence Model](Evidence-Model.md)
to identify expensive scenarios or commands. A blocked result does not justify dropping tests,
weakening isolation, or treating missing evidence as passed. Report a repeated infrastructure delay
with the run link, job name, timings, and diagnostics. There is no guaranteed two-minute completion.

## Dependency setup or coverage fails

Scenario drivers must use the dependencies provisioned by the hosted supervisor. They cannot run
`npm ci`, fetch packages, or write into the read-only source trees during isolated execution.
Verify committed package manifests and lockfiles against the
[dependency contract](Characterization-Reference.md#characterization-manifest-and-executable-proof).

For coverage failures, compare the reported responsibility with the proposed source commit.
A surviving changed function needs body execution at its head line range. A deleted function is
not an executable head target; retain a diagnostic that claims otherwise for Gate investigation.
Add meaningful application tests when the target exists and is untested.

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
