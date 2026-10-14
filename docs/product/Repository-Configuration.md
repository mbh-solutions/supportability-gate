# Repository Configuration

## Authorization event receiver

The organization workflow evaluates technical evidence directly. Repository setup does not
require an authorization event receiver or approval-comment configuration.
See [Refactor Authorization](Refactor-Authorization.md) for the owner's role and retained proof.

Each enrolled repository commits three fixed inputs. The contract establishes supported source
scope; review evidence describes the actual change; characterization declares executable proof.
Neither evidence file is a waiver.

## Contract examples

These fictional examples use production root `src` and real high-risk source files that a prepared
application would contain. Change the paths to your repository's actual governed responsibilities.
The maximum is fixed at 10 and adapter identifiers are closed, not user-defined command names.

### Python contract

```toml
schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = ["src/application/pricing.py"]

[[gates]]
adapter = "python.c901-touched.v1"
paths = ["src"]

[[gates]]
adapter = "python.import-linter.v1"
paths = ["src"]

[[gates]]
adapter = "python.mypy-strict.v1"
paths = ["src"]

[[gates]]
adapter = "python.pytest.v1"
paths = ["src"]

[[gates]]
adapter = "python.ruff-lint.v1"
paths = ["src"]

[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
```

### TypeScript contract

```toml
schema_version = "1.0"
language = "typescript"
production_paths = ["src"]
high_risk_paths = ["src/domain/pricing.ts"]

[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["src"]

[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["src"]

[complexity]
adapter = "typescript.c901-equivalent-touched.v1"
maximum = 10
```

The TypeScript contract lists two policy adapters; the hosted profile also provides its fixed
format, lint, type, test, build, and asset-validation responsibilities.

The same profile admits `.js` and `.jsx` source alongside `.ts`, `.tsx`, `.mts` and `.cts`.
JavaScript remains subject to the fixed strict type checker (`allowJs` and `checkJs`), lint,
complexity, import boundaries, runtime test coverage, build evidence and behavior obligations.
A separate TypeScript rewrite or application build step is not required by source admission;
the Gate still performs its own fixed quality commands. Keep browser source in production scope.
Explicit JavaScript imports resolve to a committed matching file first; the existing TypeScript
extension rewrite applies when that file is absent.

HTML assets must be UTF-8 with explicit balanced element tags. Executable code belongs in external
scripts that resolve to assessed production source in the same repository. Inline scripts, event
handlers, executable URLs, embedded documents, foreign markup and base-URL redirection fail the
bounded asset validator. This is an admission boundary, not full browser rendering or security
certification. ICO admission validates a contiguous directory of PNG-backed images, their bounds,
dimensions and PNG checksums; other ICO encodings remain unsupported. Asset receipts bind exact
committed bytes and do not replace behavior evidence.

### Mixed contract

```toml
schema_version = "1.1"
languages = ["python", "typescript"]
production_paths = ["src"]
high_risk_paths = ["src/application/pricing.py", "src/domain/pricing.ts"]

[[gates]]
adapter = "python.c901-touched.v1"
paths = ["src"]

[[gates]]
adapter = "python.import-linter.v1"
paths = ["src"]

[[gates]]
adapter = "python.mypy-strict.v1"
paths = ["src"]

[[gates]]
adapter = "python.pytest.v1"
paths = ["src"]

[[gates]]
adapter = "python.ruff-lint.v1"
paths = ["src"]

[[gates]]
adapter = "typescript.c901-equivalent-touched.v1"
paths = ["src"]

[[gates]]
adapter = "typescript.import-boundaries.v1"
paths = ["src"]

[complexity]
maximum = 10
```

Schema `1.1` uses `languages` and a `complexity` table containing only `maximum`; the language
order and complete adapter sets must match the fixed parser. It does not permit arbitrary
combinations of languages.

Mixed adapter scope follows the admitted source language: Python adapters must cover changed
and high-risk `.py`/`.pyi` files; TypeScript adapters must cover `.ts`, `.tsx`, `.mts`, and `.cts`
files. Separate roots, such as Python `src` and TypeScript `web`, may therefore use corresponding
adapter paths. Both old and new paths remain checked for renames and deletions. Files outside
these admitted language suffixes retain the full declared coverage obligations; this does not
exempt unsupported source or production assets. Candidate scope narrowing still blocks.

The fixed TypeScript test manifest admits files under `tests` ending in `.test.js`, `.test.mjs`,
`.test.cjs`, `.test.ts`, `.test.mts`, or `.test.cts`; Python admits `.py` and `.pyi` tests.
Mixed command arguments select each language's exact source and test manifests. Evidence must
match the executed arguments, including module tests; omitted, substituted, extra, or
other-language test arguments fail binding validation.

## Base authority and supported transitions

The evaluator reads the contract from the immutable **base** commit. Reading a head contract to
detect weakening does not let a pull request redefine its own authority. The fixed policy permits:

- supported expansion to the complete mixed profile with preserved coverage and thresholds;
- removal of an exact high-risk path only when its tracked file is deleted in that diff;
- retirement of one mixed language profile when the corresponding language product is deleted,
  the surviving fixed adapter set is complete, and production roots remain unchanged.

Other threshold relaxation, adapter loss, scope narrowing, exclusions, command overrides, executable
paths, environment controls, and unknown keys fail closed. Moving code outside coverage is not a
way to obtain PASS.

## Review evidence

`.supportability-review.toml` uses schema `1.0`. All fixed sections are required:

| Section | Required content |
|---|---|
| `behavior` | Intended behavior and proof |
| `characterization` | Captured behavior and proof |
| `separation_of_concerns` | Before/after explanation and exact changed-boundary records |
| `architecture` | Dependency-direction explanation and reviewed paths |
| `responsibility_boundary` | Path, what it owns, and what it does not own |
| `incremental_refactor` | Target and completed step |
| `review_handoff` | Derived summary and remaining-risk markers |
| `human_review` | Naming, cohesion, intended behavior, and reviewability declarations |
| `module_boundaries` | Records for relevant new production locations; an empty array when none apply |

Refresh the evidence for each full-process change. Boundary records use exact `path`, `kind`, and
`symbol` identities from the changed inventory, with `before` and `after` text. Missing, duplicate,
invented, or incomplete records block. Nonblank text is structural evidence, not independently
judged truth or automatically authenticated owner authorship.

The handoff fields use the fixed sentinel:

```toml
[review_handoff]
summary = "DERIVED_FROM_AUTHENTICATED_EVIDENCE"
remaining_risks = ["DERIVED_FROM_AUTHENTICATED_EVIDENCE"]
```

The result composer supplies the actual handoff from authenticated sources; do not replace these
markers with an authored claim of completion.

## Characterization manifest and executable proof

See [Characterization Reference](Characterization-Reference.md#characterization-manifest-and-executable-proof).

## Observed Python APIs and genuine introduction

See [Characterization Reference](Characterization-Reference.md#observed-python-apis-and-genuine-introduction).

### Module responsibility witnesses (schema 4)

See [Characterization Reference](Characterization-Reference.md#module-responsibility-witnesses-schema-4).

### Function-only witnesses (schema 3)

See [Characterization Reference](Characterization-Reference.md#function-only-witnesses-schema-3).

## Oracle-first behavior corrections (schema 5)

See [Characterization Reference](Characterization-Reference.md#oracle-first-behavior-corrections-schema-5).

### First application baseline

See [Characterization Reference](Characterization-Reference.md#first-application-baseline).
