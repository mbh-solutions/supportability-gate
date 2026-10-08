# Refactor Authorization

The Gate authenticates required refactor scope through exactly one trusted-owner PR comment
matching the current head and beginning `Supportability-Refactor-Authorization: `. Repository enrollment and ordinary
admin permissions do not change who is trusted: the current source uses numeric owner ID
`229662739`, fixed in `refactor_policy.py`. The repository contract cannot override that identity.

## Existing schema

Schema `2.0` binds the exact repository, full base/head commits, complete sorted changed scope,
derived production targets, related test associations, and refactor sequence. This fictional
example illustrates a narrow Python change; replace every identity with the actual current facts:

```json
{
  "repository": "example-org/order-service",
  "schema_version": "2.0",
  "base_sha": "1111111111111111111111111111111111111111",
  "head_sha": "2222222222222222222222222222222222222222",
  "broad": false,
  "scope": ["src/application/pricing.py", "tests/test_pricing.py"],
  "targets": ["src/application/pricing.py::function:calculate_total:1-20"],
  "related_tests": [{"path": "tests/test_pricing.py", "targets": ["src/application/pricing.py::function:calculate_total:1-20"]}],
  "sequence": {"predecessor_sha": "1111111111111111111111111111111111111111", "series_id": "pricing-refactor", "step": 1}
}
```

The actual comment is the exact prefix followed by the JSON object. The pretty-printed object
above is reference structure, not evidence for any real pull request. Use the Gate-derived target
symbols and line spans, not guessed function identities. All required keys are fixed and unknown
keys do not add authority.

Changed production assets have whole-file identities, for example
`content/catalog.json::asset:content/catalog.json:whole-file`. These are derived from actual
Git changes within the production roots. Additions, modifications and deletions retain their
asset path; renames retain each production asset path on either side. Nonregular files remain
unbounded and blocking. Source files retain their source responsibility identities.

## Scope and related tests

For a narrow Python association use `tests/test_<source-stem>.py`. A narrow TypeScript association
uses `tests/<source-stem>.test.{js,mjs,cjs,ts,mts,cts}`. Every associated test and target must exist
in the authenticated changed scope and derived inventory. Existing characterization proof paths
retain their fixed treatment. Other tests, documentation, multiple production targets, and
unrelated files require the defined exact broad authorization when that policy applies.

An authenticated broad declaration records actual approved scope; it does not waive complexity,
coverage, characterization, or any other deterministic rule. Non-production-only changes use their
defined applicability and need not invent a production target to satisfy this example.

## Series and freshness

Step 1 starts a new named series even if the base is an unrelated refactor merge. Later steps
must use the same series ID, name the exact immediate predecessor merge, increment by one, and
preserve target identity apart from its line span. Missing lookup evidence fails closed; skipped,
replayed, substituted, or wrong-series predecessors block.

After a push, earlier base/head authorization is stale. Supply exact current evidence, verify its
author and content, and obtain fresh checks. Earlier-head comments may remain as history; multiple
matching current-head authorizations are malformed and cannot compete for authority.

Scope and spans are footprint evidence, not proof that a refactor is semantically small or good.
See [Eight Gates](Eight-Gates.md#6-incremental-refactor) and [Boundaries and Limitations](Boundaries-and-Limitations.md).

## Introduced Python API scope

For a schema-4 module responsibility, an introduction grant also includes
`module_roots`, `module_inventory_sha256` and `module_oracle_sha256`. They must exactly match the
source-derived inventory and independently reviewed root oracle. Authorization schema `4.0`
admits at most 100 globally unique actual roots only when joined to characterization result `v4`.
Its version is retained in measured authorization and checked again by the shared consumer.
Older authorization versions retain their 50-root limits and previous serialized behavior.
A grant cannot waive missing named-body executions, false hit/missing-line reports, changed normal outputs, absent
independent review, copied source, retired targets or stale repository/base/head identities. Existing
function-only grants retain their exact previous shape. The module inventory fingerprint also binds
the fixed codec-2 literal enum declarations to the exact source hash, outside semantic oracle cases.
Typed dictionary keys and literal enum capture add no owner option, grant field or authorization
waiver; ordinary observations retain codec 1.

The source-owned `named-body-execution.v1` metric in `module-witness.v3` requires a real linked
call and nonempty body-line hits for every independently inventoried named body. Exact executable,
hit and missing lines are all reported and independently recomputed; neither an owner grant nor
the metric claims every line/path executed or semantic truth. Module witness/oracle JSON is
bounded to 2,000,000 bytes and schema-4 capture/result transport to 32,000,000 bytes. Individual
value, case, source, sandbox and replay bounds remain fixed; larger evidence fails closed.

Schema `3.0` retains every schema-2 field and adds `introductions`. This array is required for
schema 3 and contains one exact grant for each Git-derived API birth in a schema-3 characterization
manifest. A grant has exactly `api`, `scenario`, `source_sha256`, `driver_sha256`, `oracle_sha256`,
`review_sha256`, `intended_feature`, and `independent_oracle_reviewed`. The last value must be
`true`. The API must be an authenticated derived production target; each digest and the precise
feature description must match the measured characterization facts and sealed review artifact.

The same fixed numeric owner and exact current base/head comment authenticate these grants.
The grant records the owner's approved intended feature and independently reviewed oracle; it
does not waive actual Git absence, real head execution, meaningful cases, deterministic replay,
or retained legacy compatibility. Missing, extra, stale, or mismatched grants block Gate 6.
The shared result consumer repeats the exact grant join before accepting an aggregate result.
An already present API continues to require compatible base/head behavior; introduction scope
cannot relabel an existing scenario or obligation.

Implementation references: [authorization parser and policy](../../src/supportability_gate/refactor_policy.py)
and [derived targets](../../src/supportability_gate/refactor_targets.py).

## Schema-5 correction authorization

A behavior correction uses the same one-comment trusted-owner boundary and one review cycle. In
addition to the complete existing scope, targets, related tests and sequence, schema `5.0` binds:

- `correction_id` to the exact manifest declaration;
- `oracle_commit_sha` to the non-production oracle commit;
- `oracle_manifest_blob_sha` and `oracle_manifest_sha256` to the frozen manifest bytes;
- `base_sha` and `head_sha` to the pull request's exact final comparison; and
- `behavior_delta_sha256` to the canonical complete observed base-to-head delta.

Authorization authenticates the transaction the owner chose; it does not certify the semantic
truth of expected cases or source receipts. A stale head or oracle, modified oracle bytes,
undeclared or absent delta, missing technical proof, or unrelated raw block still fails closed.
No wildcard correction scope, waiver, administrator bypass or second reviewer loop exists.

### Content-only corrections

A change to a production content file can use this same correction transaction without changing
source code. Declare the exact derived whole-file asset targets, the affected scenarios and
obligations, and the complete expected change. Commit expected cases, new goldens, review and
source receipts before changing the production content. Then run the captures and obtain the
schema-5 owner authorization for the exact base, oracle, head, scope, targets and observed delta.

An established baseline scenario continues to capture both real revisions. Its asset hashes must
cover every listed file and match the frozen expected bytes. Function execution obligations apply
to source responsibilities; assets still require file coverage and the existing fixed format
validators. Mixed source/content changes require both kinds of target. Empty or invented targets,
missing files, changed oracle bytes, stale approval and invalid assets remain blocking.

The final results cross-check includes each changed production asset even though assets do not
receive a source complexity score. Each asset path on either side of a rename requires its own
target or an explicit unbounded-path failure. This keeps content-only approval applicable through
final reconciliation while preserving source coverage and exact evidence binding.

Whole-file target derivation does not itself make a deletion or rename admissible. The existing
scenario inventory and evidence rules must also support the change. It does not certify the truth
of content or provide a generic existing-application bootstrap.
