# Refactor Authorization

## Work authorization and technical proof

The owner authorizes the work in the task or project process. The Gate does not ask the owner
to review code, approve function ranges, certify test expectations, or post a specially formatted
GitHub comment. Acting through the owner's GitHub account is not independent human review.

Every enrolled repository uses the same source-owned policy. There is no consumer switch to
turn technical checks off. No authorization receiver, owner identity configuration, or approval
comment is required. Every proposed source commit needs matching test evidence.

## Scope and related tests

Gate 6 derives the complete changed scope, production responsibilities and unbounded paths
from the actual base and head. It verifies that the increment has meaningful behavior coverage
and is runnable. Gate 7 independently checks observed runtime coverage and fixed quality checks.
Multiple functions or files do not trigger an extra owner approval; their technical obligations
remain in force. The author explains the intended work and responsibility boundaries through
source-bound review evidence. That explanation is a declaration, not an independently judged fact.

## Series and freshness

Git supplies the actual base/head relationship. Every published result binds to repository,
exact source commits, workflow, run and attempt. A changed head cannot reuse an earlier head's test proof.
Scope measurements cannot establish semantic smallness, good naming or product correctness.

## Introduced Python API scope

New observed APIs require genuine Git absence, admissible source inventory, meaningful
sealed expected cases, actual named-body execution, deterministic replay and applicable coverage.
The existing oracle review artifact is an author declaration with validated structure and
source binding; it is not a claim that the product owner inspected code. The Gate does not ask
for a duplicate owner grant repeating the same digests. Fixed profile and codec limits remain.

## Schema-5 correction authorization

A deliberate behavior correction has two Git stages: commit the exact declaration and
expected cases, goldens, review artifact and source receipts before changing production code.
Then implement the correction without editing those frozen bytes. The Gate derives the oracle
commit from the earliest matching manifest on the actual comparison's ancestry path and checks
all intervening history, including merged branches. Editing and restoring an oracle blocks.

Technical evidence binds the correction ID, oracle commit and manifest, exact final comparison,
and complete observed behavior delta. Only the precisely declared compatibility differences
can be reconciled. Unexpected changes, mismatched goldens, missing execution, failed quality
checks and unrelated violations remain blocking. No post-capture owner comment is needed.

### Content-only corrections

Production content keeps whole-file target identities, exact-byte snapshots, format validation
and the same frozen-expectation history. Source responsibilities require execution proof.
Neither asset deletion nor relocation is automatically admissible. Domain truth is outside
what structural checks and finite scenarios can prove.

## Existing schema

The result `refactor-policy-result.v2` contains measured scope, runnability, and correction
evidence. It contains no owner-attestation fields. Evidence must be interpreted with the exact
trusted producer and validator that created it; authored comments cannot substitute for proof.

Implementation: [refactor policy](../../src/supportability_gate/refactor_policy.py) and
[result reconciliation](../../src/supportability_gate/standard_results.py).
