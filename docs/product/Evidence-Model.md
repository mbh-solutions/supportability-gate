# Evidence Model

The Gate distinguishes the strength of an assertion from its completeness and identity. A
well-formed current statement is not automatically a measured fact or an owner attestation.

## Evidence classes

| Class | What it establishes | What it does not establish |
|---|---|---|
| `measured_fact` | An independently derived or authenticated observation bound to immutable inputs. | Raw target reports are not automatically trusted; an observation is not exhaustive correctness. |
| `deterministic_decision` | A reproducible defined rule result over authenticated inputs. | Semantic judgment outside the rule. |
| `author_declaration` | Complete, current prose bound to the actual source change. | Independent truth, quality, renewed thought, or owner authorship. |
| `authenticated_owner_attestation` | A declaration authenticated through the existing trusted-owner control. | Permission to waive a deterministic violation or certification of prose quality. |

The canonical 218-clause registry records evidence and retained test-proof classes. Shared
family-level tests establish traceability and named rule behavior; they are not 218 independent
semantic proofs.

## Canonical artifacts

| Artifact | Responsibility |
|---|---|
| `complexity-result.json` | Authoritative static evaluation, changed identities, contract policy, and source-bound review evidence. |
| `complexity-result.md` | Human-readable display derived from the JSON. |
| `quality-provenance.json` | Validated hosted commands, outcomes, receipts, coverage observations, and provenance. |
| `characterization-result.json` | Authenticated repeatability, behavior identity, obligation coverage, and compatibility results. |
| `refactor-policy-result.json` | Scope, owner authorization, sequence, and runnability decisions. |
| `standard-results.json` | Canonical `standard-results.v3` composition: applicability, eight owned rows, source outcomes, dependencies, and derived handoff. |
| `qualification-bundle.zip` | Compact canonical evidence, diagnostics, file digests, and restore metadata; optional qualified release context. |

The quality-profile artifact also retains `quality-timings.json`: bounded elapsed-time diagnostics
for setup phases and each executed fixed adapter, including successful commands. Characterization
artifacts retain `characterization-timings.json` for preparation and each executed scenario. Both
emit concise progress logs. These operational measurements are separate from authoritative
evidence and never affect a PASS/BLOCK decision.
The runner materializes exact regular Git blobs in a validated batch and copies that verified
source into a separate writable wheel-build directory; timings do not change command scope,
timeouts, or the isolated execution boundary.

The fixed Python test adapter runs the complete suite with two isolated workers and merged branch
coverage. The read-only collector verifies matching collections, complete outcomes, isolated worker
startup, and each worker's coverage acknowledgement. Missing completion or coverage, worker errors,
and attempts to disable coverage fail the command. A sanitized completion receipt is retained as a
diagnostic; authoritative coverage still comes through the existing source-bound coverage parser.
Native pytest subtests are counted separately from their parent test phases. Any failed subtest
fails the parent outcome, while duplicate ordinary phases remain a completion error.
The fixed 600-second command limit and container resource limits remain unchanged.

The collector stops after the first failed mandatory command and retains its executed prefix.
Dependency receipts cover only completed installations, so staged but uninstalled dependencies
cannot mask the original failure. A valid failed prefix remains BLOCK in the composed result;
omitted mandatory commands are never treated as passed. An unexplained incomplete sequence still
fails evidence validation.

Evidence binds repository name and numeric ID, full base/head SHAs, relevant Git blobs, pinned
workflow SHA, run and attempt, job/artifact identity, digests, capture hashes, and source hashes.
The full-process handoff also binds the base/head review blobs. Required missing, stale, malformed,
or mismatched evidence fails closed according to its defined policy/technical ownership.

For a schema-5 correction, characterization evidence additionally retains the raw behavior-change
blocks, frozen oracle file identities, exact declared scenario/obligation/target sets, the complete
canonical observed delta and its SHA-256. Refactor evidence records the authenticated correction
join. The authoritative result classifies the outcome as `PASS_NO_BEHAVIOR_CHANGE` or
`PASS_AUTHORIZED_BEHAVIOR_CORRECTION`; focused correction failures remain named blocks. This
classification does not replace any of the eight rows or convert technical failures into PASS.

For schema-3 observed Python APIs, the collector records actual call arguments and ordinary
returns with a closed tagged value codec. Static compilation binds the traced function's code
to the exact source blob and span. Git-derived whole-file absence is a measured fact rather
than a claim that an absent API executed. The sealed review artifact is a source-bound
declaration of intended behavior; the existing trusted-owner grant authenticates its exact
feature scope and independently reviewed oracle assertion. This does not certify the reviewer's
identity or qualitative independence through a new service, or prove exhaustive correctness.

## Offline restoration and retention

The workflow must restore its bundle inside the immutable no-network container before authoritative
upload succeeds. With the matching trusted Gate code and Python environment already available:

```text
python -P -m supportability_gate.qualification_bundle restore --bundle qualification-bundle.zip
```

Restore validates member bounds, file digests, canonical production validators, bound identities,
and the recorded decision. Successful restore exits 0 even when the faithfully restored decision
is BLOCK or TECHNICAL_FAILURE: it means the bundle is valid, not that the original change passed.
Invalid restoration exits 2.

Download and retain required bundles before Actions retention expires. Restore does not fetch
GitHub artifacts or need GitHub network access. A retained bundle does not reconstruct absent
external logs, rerun the original tests, replace fresh current-head qualification, or turn a
historical failure into PASS. Host runner image identity and external retention remain explicit
operational boundaries where their exact data was not recorded.

Optional release context can bind delivery, protection, runtime, lock, source-test, expiry, and
limitation records. Do not label an ordinary bundle as release-qualified without that validated
context.

Implementation references: [clause registry](../../src/supportability_gate/clause_inventory.py),
[results](../../src/supportability_gate/standard_results.py),
[bundle validator](../../src/supportability_gate/qualification_bundle.py), and
[workflow restore boundary](../../.github/workflows/organization-required.yml).
