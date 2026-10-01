# Repository Configuration

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

Use schema `2.0` for stable obligations in new examples. This minimal Python manifest is valid
structure; its driver, golden output, and observed coverage still have to prove the obligation.

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
results. Observed coverage and the surrounding golden/capture protocol still apply.

Supported scenario kinds are `test`, `sample_io`, `snapshot`, `golden`, `cli`, and `regression`.
Obligation categories are `behavior`, `cli_help`, and `static`. A static obligation binds to
`scenario:<id>`; it does not become behavior proof. `transitions` bind explicit baseline assertions
to supported replacements; replacing an ID alone cannot discard an old requirement. Legacy
schema `1.0` remains admitted by the parser but does not add schema-2 obligation fields.

Implementation references: [contract schema](../../src/supportability_gate/contract.py),
[anti-weakening](../../src/supportability_gate/gate_policy.py),
[review schema](../../src/supportability_gate/review_evidence.py), and
[manifest and assertions](../../src/supportability_gate/characterization.py).
