# Evidence Model

A complete explanation, an observed test result, and a verified policy decision establish
different things. The Gate records those differences and binds each result to the source tested.

## Evidence classes

| Class | Establishes | Does not establish |
|---|---|---|
| `measured_fact` | An independently derived or authenticated observation tied to immutable inputs. | Exhaustive correctness or trust in unauthenticated target output. |
| `deterministic_decision` | A reproducible rule result over authenticated inputs. | Judgment outside that rule. |
| `author_declaration` | Complete prose tied to the actual source change. | Independent truth, design quality, or owner authorship. |
| `authenticated_owner_attestation` | Owner authorship when required by the evidence's recorded schema. | Technical correctness or permission to waive a violation. The required workflow emits no owner attestations. |

The canonical 218-clause registry records evidence and retained test-proof classes. Shared
family-level tests establish traceability and named rule behavior; they are not 218 independent
semantic proofs.

## Find the results

The workflow's **supportability-evidence** artifact contains the final decision and its supporting
records. Its name includes the run ID and attempt. See [Troubleshooting](Troubleshooting.md) for
download steps and [Eight Gates](Eight-Gates.md) for result meanings.

| Artifact member | Responsibility |
|---|---|
| `complexity-result.json` | Static evaluation, changed identities, contract policy, and source-bound review evidence. |
| `complexity-result.md` | Human-readable display derived from that JSON. |
| `quality-provenance.json` | Validated commands, outcomes, dependency receipts, and coverage observations. |
| `characterization-result.json` | Repeatability, behavior identity, obligation coverage, and compatibility. |
| `refactor-policy-result.json` | Git-derived scope, runnability, and frozen correction history. |
| `standard-results.json` | Canonical `standard-results.v3`: eight owned rows, applicability, source outcomes, dependencies, and derived handoff. |
| `qualification-bundle.zip` | Restorable canonical evidence, diagnostics, file digests, and metadata. |

Evidence binds repository name and numeric ID, full base/head commit IDs, relevant Git blobs,
trusted workflow commit, run and attempt, job/artifact identity, native digests, capture hashes,
and source hashes. The handoff also binds both review files. Missing, stale, malformed, or
mismatched required evidence blocks qualification.

## Complete quality partitions

Four hosted partitions divide the fixed quality profile. Python partitions collect the complete
test list, select fixed round-robin subsets, and use two isolated coverage workers per partition.
They retain full and selected lists, complete test/subtest outcomes, worker acknowledgements,
and raw branch coverage. Node partitions use the fixed native `--test-shard=N/4` option with
the same complete Git-derived test-file list. Each retains its command scope, partition identity,
original LCOV coverage bytes and digest, and required responsibility targets.

Partition zero runs the non-test adapters. Other partitions run their tests and dependency
provisioning. Lint, formatting, typing, build, and import-boundary work therefore executes once
per combined profile. The mixed profile includes all sixteen required adapter results.

The join verifies native artifact identities and SHA-256 digests for the same repository, head,
run, and attempt. It rejects missing or duplicate partitions, mismatched collections or command
scope, failed tests or subtests, missing worker coverage, and inconsistent runtime, dependency,
or sandbox evidence. It combines actual raw coverage and reapplies changed/high-risk responsibility
requirements to the union. A partial capture cannot directly satisfy the complete quality attestation.

The joined capture records `isolated-target-provenance.sharded.v1` and retains original invocation
receipts. Combined stream digests bind explicit manifests of the original stdout/stderr digests.
The fixed 600-second command limit and container resource limits apply to each invocation.

Within a partition, collection stops after a failed mandatory command and retains the executed
prefix. Dependency receipts cover completed installations. A valid failed prefix produces BLOCK;
an unexplained incomplete sequence fails evidence validation. Omitted commands never count as passed.

## Runtime coverage

Targets refer to surviving responsibilities in the proposed head source, using its line ranges.
Deleted base bodies participate in change review and behavior compatibility; they are not executable
head-test targets. Passing a command or merely importing a function does not prove its body ran.

TypeScript tests use source maps. Erased type declarations and non-executable module regions have
no runtime obligation. Positive LCOV `DA` records establish execution; omitted or ignored lines
cannot create credit. The reader reconciles ignored type-line totals in `LF`/`LH` and rejects
duplicate lines, invalid identities, and inconsistent accounting.

Bounded, sanitized coverage diagnostics retain the original report digest and size, including
parse failures. Truncated diagnostic text does not replace the complete original evidence.

## Repeatable behavior

Characterization runs at most two scenarios concurrently. Each successful scenario performs two
fresh disposable executions per applicable source commit. A failed first execution is retained
without repetition and cannot establish determinism. Manifest order determines the result order,
independently of completion order.

The Python baseline observer classifies code identity and path once, traces target-tree line
events, and observes calls back into target code from external libraries. It retains source hashes
and exact per-code execution counts. The separate observed-API protocol captures actual arguments,
returns, and supported module witnesses; see [Characterization Reference](Characterization-Reference.md).

For a schema-5 correction, evidence binds frozen oracle files, the exact declared scenario,
obligation and target sets, and the complete observed delta and its digest. The result classifies
a passing change as `PASS_NO_BEHAVIOR_CHANGE` or `PASS_VERIFIED_BEHAVIOR_CORRECTION`. Classification
does not replace the eight rows or convert a technical failure into PASS.

## Progress and failure reporting

Capture artifacts contain `characterization-timings.json`; quality artifacts contain
`quality-timings.json`. These report preparation, scenario, and adapter elapsed time. Progress
logs and timings help diagnose waiting but do not affect PASS/BLOCK decisions.

A failed scenario is reported when it finishes. Collection jobs upload diagnostics before reporting
failure. Independent work can finish so the publisher can report all eight requirements.
The [lifecycle](Pull-Request-Lifecycle.md#parallel-preparation-with-authenticated-producer-completion)
describes concurrent preparation and authenticated waits. Real test cost, artifact transfer, and
hosted scheduling determine total time; concurrent jobs also consume overlapping compute.

## Offline restoration and retention

Before authoritative upload, the workflow restores its bundle in the fixed no-network container.
For a maintainer with the matching trusted Gate environment installed, open a terminal in the
directory containing the downloaded bundle and run:

```text
python -P -m supportability_gate.qualification_bundle restore --bundle qualification-bundle.zip
```

Restore validates archive bounds, file digests, canonical validators, identities, and the recorded
decision. Exit 0 means the bundle is valid, including when its recorded decision is BLOCK or
TECHNICAL_FAILURE. Exit 2 means restoration failed. See [CLI Reference](CLI-Reference.md) for setup.

Download required bundles before Actions retention expires. Restoration needs no GitHub network
access, but does not fetch missing logs, rerun tests, or qualify another commit. A hosted runner
label does not identify an immutable host image. Optional release context can bind delivery,
protection, runtime, lock, test, expiry, and limitation records; an ordinary bundle does not
establish those extra claims.

Implementation references: [clause registry](../../src/supportability_gate/clause_inventory.py),
[quality join](../../tests/hosted_quality_aggregate.py),
[results](../../src/supportability_gate/standard_results.py), and
[bundle validator](../../src/supportability_gate/qualification_bundle.py).
