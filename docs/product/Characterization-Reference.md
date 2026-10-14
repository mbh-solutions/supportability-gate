# Characterization Reference

Characterization records observable application behavior and checks it against committed expected
results. This reference is for implementers writing the scenarios. Prepare the repository contract
and author declarations using [Repository Configuration](Repository-Configuration.md).

## Choose the required protocol

| Manifest schema | Evidence shape |
|---|---|
| `1.0` | File-scoped scenarios |
| `2.0` | Scenarios with stable obligations and explicit transitions |
| `3.0` | Obligations plus direct observation of named Python APIs |
| `4.0` | Observed Python APIs with module-root witnesses |
| `5.0` | Frozen-oracle correction declarations and first-application baseline scenarios |

The schema selects a fixed protocol, limits, and validator. It does not enable arbitrary commands
or waive existing obligations. A golden file holds expected results; an oracle is the complete
set of frozen expected cases and supporting files used to judge behavior.

## Characterization manifest and executable proof

Schema `2.0` supports stable obligations. This minimal Python manifest is valid
structure; its driver, golden output, and observed coverage have to prove the obligation.

```json
{
  "schema_version": "2.0",
  "scenarios": [{"id": "pricing", "kind": "regression", "covers": ["src/application/pricing.py"]}],
  "obligations": [{"id": "pricing-total", "category": "behavior", "scenario": "pricing", "selector": "pricing-total", "target": "src/application/pricing.py"}],
  "transitions": []
}
```

For scenario `pricing`, commit `tests/characterization/pricing.characterization.py` and
`tests/characterization/pricing.golden.json`. TypeScript uses a `.characterization.mjs` driver.
In a mixed profile, each scenario's covered language must be unambiguous; use separate Python
and TypeScript scenarios rather than one scenario declaring both languages.

For TypeScript and mixed profiles, commit `package.json` and an npm lockfile version 2 or 3
at the repository root. Characterization provisions the exact head manifests once per capture
with `npm ci --ignore-scripts --no-audit --no-fund --include=dev --include=optional`. Both base
and head source run against that same head-defined test environment, including development
dependencies needed by the head's drivers. This is source compatibility under a common test
environment, not proof of each commit's own deployment environment.

Provisioning accepts integrity-bound packages from `https://registry.npmjs.org/`; local links,
workspaces, Git fetches, other registries, missing locks, and unverifiable installed packages
fail closed. Repository npm configuration and lifecycle scripts are not executed. Installed
package content hashes and lock identities are retained in capture provenance. Drivers resolve
packages from the target or definition root; both receive the same read-only `node_modules`.
Drivers must not install dependencies, fetch packages, or write into either source tree during
capture. Execution remains network-disabled and writable output belongs under `/work`.

Prerequisite installation failure is a technical failure, not a behavior mismatch. Collection jobs retain failed diagnostics and report capture failure. The published Gate 5
result is authoritative for scenario success, deterministic replay, coverage, and golden compatibility.

Drivers exercise the real responsibility and emit a deterministic JSON object with exactly
`schema_version`, `scenario`, and `behavior`. The driver protocol uses schema `1.0`, and `scenario`
must match its manifest ID. The `pricing-total` selector chooses that field inside `behavior`.
A behavior assertion must supply distinct inputs with different outputs, not a constant or
help-only substitute. The golden file contains only the expected `behavior` value, not the driver
protocol wrapper. Captured driver/golden blobs bind to the exact source commit.

For example, the driver can emit these actual observed cases:

```json
{"schema_version": "1.0", "scenario": "pricing", "behavior": {"pricing-total": [{"input": {"quantity": 1, "unit_price": 10}, "output": 10}, {"input": {"quantity": 3, "unit_price": 10}, "output": 30}]}}
```

The driver must calculate these outputs from the real application, rather than print invented
results. Observed coverage and the surrounding golden/capture protocol apply.

Supported scenario kinds are `test`, `sample_io`, `snapshot`, `golden`, `cli`, and `regression`.
Obligation categories are `behavior`, `cli_help`, and `static`. A static obligation binds to
`scenario:<id>`; it does not become behavior proof. `transitions` bind explicit baseline assertions
to supported replacements; replacing an ID alone cannot discard an old requirement. Schema `1.0` accepts file-scoped scenarios without schema-2 obligation fields.

For changed functions or components, a successful file-scoped schema-1 scenario alone does not
supply a behavioral obligation. Add schema-2 behavior obligations selecting meaningful observed
input/output cases. A whole-file behavior target can cover that file's changed responsibility
identities; a function target uses its stable identity without a line-range suffix. Preserve
existing scenario IDs and golden values. Additional regression scenarios can run against both
real source commits to supply missing proof without relabelling existing expectations.

Compatibility obligations can include a removed or renamed base responsibility: demonstrate
preserved external behavior through the surviving interface. They do not require executing a
deleted function at head. Gate 7 runtime targets are independently derived from surviving head
source and current line ranges. An intentional behavior change requires the supported
oracle-first correction transaction; changing test expectations is not a refactor shortcut.

Implementation references: [contract schema](../../src/supportability_gate/contract.py),
[anti-weakening](../../src/supportability_gate/gate_policy.py),
[review schema](../../src/supportability_gate/review_evidence.py), and
[manifest and assertions](../../src/supportability_gate/characterization.py).

## Observed Python APIs and genuine introduction

### Module responsibility witnesses (schema 4)

Schema `4.0` includes the schema-3 fields and a `module_roots` field for each scenario. Use an
empty array for ordinary or function-only rows. A module row supplies a sorted, unique list of
public synchronous APIs from the same covered Python file, including its primary `api`. Each
declared root counts toward schema 4's fixed global limit of 100 observed APIs, including
ordinary/function-only rows. Roots must be globally unique and each declared root must execute
as an actual root. Schemas 1-3 retain their 50-API limit; total scenarios remain limited to 64.

Retain a `$` behavior obligation targeting the primary API. A second `$` behavior obligation may
target that exact source filepath. File coverage requires a complete source/function/code inventory
and actual executable-body line witnesses linked to declared public-root executions. Calls outside
those executions cannot supply coverage. Every independently inventoried named body must have
a real linked call with nonempty canonical body-line hits. The fixed `named-body-execution.v1`
metric records every body's exact executable, hit and missing lines and recomputes those sets
from the authenticated traces. It does not require every line to execute or claim complete paths
or semantic truth. Missing bodies, false reports and old witness relabelling fail closed. Unsupported
async, generator, generator-expression, lambda, conditional function definitions or unaccounted
separately compiled function bodies fail closed. The collector rejects target-origin runtime
compilation, evaluation, execution and explicit code/function creation, including aliases and
generated code with a different filename. A precreated function executing with the target's
filename or module globals must match the source inventory. Exact compiled module/class
scaffolding and standard-library dataclass generation remain supported; separately imported
dependency bodies remain outside this same-file witness. Move runtime-generated application
behavior into statically declared source before requesting module coverage. A caught observer
error invalidates capture. Body-line coverage is a
bounded proxy, not proof of every branch combination or semantic fidelity.

The ordinary `.golden.json` is the primary normal-return oracle, requiring at least two
distinct inputs and outputs. Add `<id>.module.golden.json` containing independently established root
records with exact `root`, `input`, `arguments_after`, `outcome`, `output` and `exception` fields.
Returned `None` and an exception are distinct outcomes. Void mutations use actual arguments before
and after the call; exception-only roots cannot replace meaningful primary returns. Include
independently reviewed normal and relevant failure cases: a normal-only oracle does not establish
an unexercised guard's exception behavior, even when its named body has line hits. The fixed
`python-values-v1` codec applies for ordinary and function-only observations, including
exact bytes and source-bound dataclasses. Module rows always use fixed `python-values-v2` and
`module-witness.v3`; repositories cannot choose a codec. Existing supported values retain their
exact codec-1 encoding. Module-only `dict-keyed` values admit exact boolean, integer and string keys
when at least one key is not a string; canonical encoded-key ordering, Python key-equality collision
rejection and the existing depth/node/item/byte limits are mandatory. String-only dictionaries
retain their ordinary encoding. Enum dictionary keys remain unsupported.

Module value capture additionally admits same-file, top-level, simple literal direct standard-library
`Enum` or `StrEnum` members. Exact source SHA and static class/member declarations bind outside
semantic cases and are included in the reviewed inventory fingerprint. Value tags preserve source
path, module, class, member and typed literal identity. Only raw registered member storage is read;
target constructors, serializer hooks, properties, `str` and `repr` are never used. Aliases, `auto`,
factories, decorators, mixed bases, custom metaclasses and custom methods are unsupported. Forged,
stale or relabelled codec/declaration evidence fails closed. This capture capability lets real
invalid-input public calls reach retained guards; it does not relax those guards or body coverage.
Opaque receivers, callbacks and unsupported objects remain rejected. A plain public persistence
API can witness its actual class helpers without serializing their receivers.

Module review records use schema `2.0`, retaining all ordinary review fields and adding
`module_roots`, `module_inventory_sha256` and `module_oracle_sha256`. These bind the exact root
panel, complete function/enum inventory and raw module-oracle bytes at admission. A newly added
module scenario must bind the exact head source and inventory, including when its source file
already exists. For subsequent behavior-preserving changes, keep the review file byte-identical:
the same scenario and byte-identical review must already exist in the comparison base. Its source
and inventory fingerprints record the original admission, rather than requiring review rewrites
after every implementation change. Both source commits' inventories and actual execution
witnesses are independently verified; admission review fingerprints never replace that proof.
The driver, root panel, ordinary golden and module golden remain protected. A changed review is
still rejected; restore its original bytes for a preserving change. Deliberate behavior changes
must use the separate correction contract and cannot be hidden by updating review fingerprints.
Whole-file birth, anti-copy, no-retirement, sealed-review, exact-head evidence,
golden preservation, resource, and quality protections are mandatory.

The fixed producer returns capture/result schema `v4` for schema-4 manifests. Each manifest
schema selects its matching producer and result format.

Module witnesses and module oracles each have a fixed 2,000,000-byte bound. Schema-4 captures
and results have a fixed 32,000,000-byte bound through their matching producers/readers, including
the emitted final newline. Individual encoded values remain limited to 262,144 UTF-8 canonical
wire bytes, with depth 16, 4,096 nodes and 256 items. The 128 cases/executions, 4,096 calls and
128 named bodies per module remain fixed. Wire sizing and typed-key ordering use UTF-8 canonical
bytes; source inventory hashing retains its established canonical bytes. Ordinary observations,
ordinary goldens, manifests, reviews, events, and other JSON use their schema-specific bounds. Larger
module evidence fails closed; limits cannot be selected by a consumer repository.

### Function-only witnesses (schema 3)

The fixed manifest capacity is 64 total scenarios, including at most 50 observed Python API
scenarios. The Gate rejects larger manifests before target execution. Serialized results retain
the same 64-scenario limit; observed API facts remain limited to
50 each. A manifest supports at most 200 obligations. These are resource bounds, not permission
to omit required behavior or combine distinct API identities into one proof.

Machine captures use canonical compact JSON plus a final newline. All observations remain in
the artifact, and capture digests/provenance bind its actual emitted bytes. The existing
1,000,000-byte JSON input limit, case/codec limits, replay timeouts and hosted job limits remain
in force; larger evidence needs a supported scope design rather than dropped observations.

Manifest schema `3.0` includes schema-2 obligations/transitions and an `api` field in every
scenario row. Ordinary rows use `"api": null`. An observed row names one ordinary synchronous
Python function, covers exactly its file, and supplies a behavior obligation with selector `$`
whose target is exactly that API. Observed cases credit only the selected function; sibling
functions require their own meaningful proof:

```json
{"id":"new-total","kind":"regression","covers":["src/application/total.py"],"api":"src/application/total.py::function:calculate_total"}
```

Its fixed driver remains `tests/characterization/new-total.characterization.py`. The driver calls
the actual API; its printed output is discarded. A Gate-owned observer captures actual signature
arguments before mutation and completed normal return values. The golden is the resulting list
of cases, using codec `python-values-v1`. For example, one integer observation is:

```json
{"input":[{"name":"quantity","value":{"type":"int","value":2}}],"output":{"type":"int","value":20}}
```

Provide at least two distinct actual inputs and outputs. The codec preserves separate type tags
for plain scalars, lists, tuples, string-keyed dictionaries, bytes, finite Decimal values,
fixed-offset aware datetimes, and source-bound declared dataclasses. Unknown objects, serializer
hooks, cycles, excessive size/depth, nonfinite numbers, generators, coroutines and raised-error
claims fail closed. The observer is fixed Gate code; contracts cannot select another command.

Commit a separately reviewed intended-oracle record at
`tests/characterization/new-total.review.json` with exactly `schema_version: "1.0"`, `api`,
`source_sha256`, `driver_sha256`, `oracle_sha256`, `intended_feature`, `reviewer` and
`verdict: "ACCEPTED"`. SHA-256 fields bind the actual source/driver/golden file bytes. The review
record is an author-declared review artifact with validated source binding. Neither the
record nor an owner credential establishes independent human review.

The Gate derives introduction from exact Git absence of the whole file at base and exact source
presence at head. Only genuinely added files qualify; copied or renamed source, existing paths,
and changes simultaneously retiring/moving production paths or function identities cannot use
the initial birth route. Existing function identities in modified production files must remain
present, so padding a moved function below Git's copy threshold cannot make it introduced.
The authenticated base capture records absence and no execution command. The head runs the
observer twice and must match the sealed intended oracle. No duplicate owner grant is needed; see [Refactor Authorization](Refactor-Authorization.md).

After merge, the same observed scenario runs twice at both base and head and must preserve its
observations. Existing scenario/obligation identities cannot be relabeled, and admitted driver,
golden and review bytes remain protected. Birth proof requires preservation of existing coverage, source integrity, quality commands,
thresholds, and native protections.

## Oracle-first behavior corrections (schema 5)

### First application baseline

Schema 5 also admits `kind: "baseline"` scenarios with `api: null` and
`module_roots: []`. This bounded introduction path requires an empty production tree at
the exact PR base. Existing applications continue to use preservation or the existing
explicit correction paths. Copies, renames, partial absence, missing source, and files
outside the production roots cannot use first-baseline absence evidence.

Use one `category: "baseline"` obligation per scenario, with `selector: "$"` and
`target: "scenario:<scenario-id>"`. Explicit `covers` lists may group related files;
no wildcard or directory coverage is accepted. Existing limits of 64 scenarios, 200
obligations and bounded JSON transports remain fixed. A content collection can
therefore cover hundreds of individually listed files without hundreds of obligations.
Separate Python and frontend scenarios in mixed profiles. Asset-only mixed scenarios
use Python. For example:

```json
{"id":"catalog","kind":"baseline","covers":["content/a.json","content/b.json"],"api":null,"module_roots":[]}
```

The ordinary fixed driver emits schema 1, its scenario ID, and `behavior` containing
actual observed input/output cases. Source scenarios need at least two distinct inputs
with different outputs. Asset-only drivers emit an empty list. The fixed collector
constructs the golden value as `{"cases": [...], "assets": {"content/a.json": "<sha256>"}}`:
`assets` contains the exact SHA-256 of every covered non-source file, and is empty when
the scenario covers only source. Asset validation and all source quality checks remain
mandatory through Gate 7. Asset hashes establish byte identity, not semantic correctness.

Fixed Python and V8 observers independently collect execution. Every statically compiled
Python function body, including anonymous and generator bodies, must execute a line;
exact code identities and source hashes are checked. Frontend evidence requires actual
V8 execution of every parser-inventoried function at its exact UTF-16 source span.
Importing a module or printing an expected answer does not establish execution coverage.
Drivers must await asynchronous work before returning. No executable or command option
is supplied by the manifest. Code runs only in the existing isolated hosted boundary.

Commit the schema-5 correction manifest, drivers, goldens, review and source receipts
before production implementation. Both the driver and golden must be listed among the
frozen oracle files. The base collector records Git-derived absence explicitly; it does
not execute the candidate as a substitute base. The head runs twice and must match the
frozen golden and source-bound execution witnesses. Frozen oracle history and exact observed-delta evidence are required. A false absence, missing execution, replay drift or mismatch cannot
be reconciled by a correction declaration.

After introduction, both sides execute against their real source commits and the same
goldens protect later behavior. A baseline group's file inventory cannot be narrowed or
relabelled. The initial release path does not introduce a generic exception for additions
to an already populated production tree.

Later corrections to existing production content use the same schema-5 correction transaction
with [whole-file asset targets](Refactor-Authorization.md#content-only-corrections). Both real
source commits are captured, and the expected content and exact technical evidence are mandatory.

Schema `5.0` includes the schema-4 fields and a closed `corrections` collection. A correction has one
stable ID and exact sorted scenario, obligation and production-target identities. Its
`oracle_files` list contains every expected-case, golden, review and source-receipt path with the
SHA-256 of its raw bytes. These four generic roles are required; repository- or domain-specific
interpretation fields are not supported.

When a corrected module scenario adds or changes the base-side execution cases, the oracle commit
may freeze them in `tests/characterization/<scenario-id>.base.module.golden.json`. The file must be
declared with the generic `golden` role; otherwise the ordinary protected-base module golden
remains authoritative.

The correction declaration and all oracle files are committed before production implementation.
The later implementation commit may change production code but cannot modify any frozen oracle
byte. Every descendant commit on a path from the oracle to the final head must retain those
bytes, including merged implementation branches; editing and later restoring an oracle still
blocks. The Gate verifies that the oracle commit descends from the pull-request base, is an ancestor
of the final head, changes no production path, and contains no undeclared file. The actual changed
scenario, obligation and derived target sets must exactly equal the declaration. Head execution
must match the frozen goldens and retain deterministic replay, API execution, coverage, quality
and handoff evidence.

A declared changed behavior obligation can prove coverage when both sides execute meaningful
cases and the corrected head matches its frozen golden. Its raw compatibility result remains
`BLOCK` in the raw capture; only verified declared corrections are reconciled in the final result. Missing execution, replay drift,
golden mismatch and every other technical block prevent correction coverage credit.

The raw characterization blocks remain in retained evidence. Only the exact declared instances of
`CHANGED_CHARACTERIZATION_DEFINITION`, `CHANGED_GOLDEN_OUTPUT` and
`INCOMPATIBLE_POST_CHANGE_BEHAVIOR` can be reconciled after the technical verifier joins the
oracle commit, manifest blob/hash, final head and canonical observed-delta digest. Golden mismatch,
execution failure, missing execution or coverage, quality failure, malformed evidence, stale
source identities and missing handoff evidence are never reconciled.

An fixed schema-5 manifest has no active correction and follows the ordinary preservation
path. Existing schema 1-4 manifests keep their existing decisions and wire formats.

The refactor and final-result readers select the schema-4 or schema-5 aggregate transport from
the exact-head Git manifest. The existing 32,000,000-byte limit and duplicate-key rejection apply
to both; a payload label cannot select a different schema or enlarge the transport limit.
