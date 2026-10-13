# Refactor Authorization

## Work authorization and technical proof

The owner authorizes the work in the task or project process. The Gate does not ask the owner
to review code, approve function ranges, certify test expectations, or post a specially formatted
GitHub comment. Acting through the owner's GitHub account is not independent human review.

Every enrolled repository uses the same source-owned policy. There is no consumer switch to
turn technical checks off. No authorization receiver, owner identity configuration, or approval
comment is required for new runs using this policy. A new source revision still needs fresh tests.

## Scope and related tests

Gate 6 derives the complete changed scope, production responsibilities and unbounded paths
from the actual base and head. It verifies that the increment has meaningful behavior coverage
and is runnable. Gate 7 independently checks observed runtime coverage and fixed quality checks.
Multiple functions or files do not trigger an extra owner approval; their technical obligations
remain in force. The author explains the intended work and responsibility boundaries through
source-bound review evidence. That explanation is a declaration, not an independently judged fact.

## Series and freshness

Git supplies the actual base/head relationship. Synthetic refactor series numbers and owner
comments are no longer required. Every published result binds to repository, exact source
revisions, workflow, run and attempt. A changed head cannot reuse an earlier head's test proof.
Scope measurements cannot establish semantic smallness, good naming or product correctness.

## Introduced Python API scope

New observed APIs still require genuine Git absence, admissible source inventory, meaningful
sealed expected cases, actual named-body execution, deterministic replay and applicable coverage.
The existing oracle review artifact remains an author declaration with validated structure and
source binding; it is not a claim that the product owner inspected code. The Gate does not ask
for a duplicate owner grant repeating the same digests. Fixed profile and codec limits remain.

## Schema-5 correction authorization

A deliberate behavior correction still has two Git stages: commit the exact declaration and
expected cases, goldens, review artifact and source receipts before changing production code.
Then implement the correction without editing those frozen bytes. The Gate derives the oracle
commit from the earliest matching manifest on the actual comparison's ancestry path and checks
all intervening history, including merged branches. Editing and restoring an oracle still blocks.

Technical evidence binds the correction ID, oracle commit and manifest, exact final comparison,
and complete observed behavior delta. Only the precisely declared compatibility differences
can be reconciled. Unexpected changes, mismatched goldens, missing execution, failed quality
checks and unrelated violations remain blocking. No post-capture owner comment is needed.

### Content-only corrections

Production content keeps whole-file target identities, exact-byte snapshots, format validation
and the same frozen-expectation history. Source responsibilities still require execution proof.
Neither asset deletion nor relocation is automatically admissible. Domain truth remains outside
what structural checks and finite scenarios can prove.

## Existing schema

Historical `refactor-policy-result.v1` evidence and authorization schemas 2–5 remain readable
under their recorded policy. Their authentication is not fabricated, erased or converted to a new
PASS. Current runs emit `refactor-policy-result.v2` with measured scope, runnability and correction
evidence, and no owner-attestation fields. Historical comment failures identify an older release;
use a fresh run on the deployed release rather than creating another approval ritual.

The original page name and section anchors remain for compatibility.
Implementation: [refactor policy](../../src/supportability_gate/refactor_policy.py) and
[result reconciliation](../../src/supportability_gate/standard_results.py).
