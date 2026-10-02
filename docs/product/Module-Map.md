# Module Map

Each current production module has a named responsibility. Links point to the source that owns
that responsibility; the declared package dependency direction is enforced in
[pyproject.toml](../../pyproject.toml).

| Module | Primary responsibility |
|---|---|
| [__init__.py](../../src/supportability_gate/__init__.py) | Package identity and version |
| [__main__.py](../../src/supportability_gate/__main__.py) | Module entrypoint |
| [cli.py](../../src/supportability_gate/cli.py) | Public evaluator orchestration and arguments |
| [git_changes.py](../../src/supportability_gate/git_changes.py) | Immutable Git identities, blobs, changes, and command records |
| [contract.py](../../src/supportability_gate/contract.py) | Fixed base-contract parsing and supported profile transitions |
| [function_changes.py](../../src/supportability_gate/function_changes.py) | Changed lines and stable supported function/component identities |
| [complexity_metrics.py](../../src/supportability_gate/complexity_metrics.py) | Python/TypeScript measurements and Ruff parity |
| [complexity_policy.py](../../src/supportability_gate/complexity_policy.py) | Progressive legacy complexity decisions and debt reporting |
| [architecture_policy.py](../../src/supportability_gate/architecture_policy.py) | Supported static import graphs and direction |
| [modularity_policy.py](../../src/supportability_gate/modularity_policy.py) | New production location ownership and coverage records |
| [gate_policy.py](../../src/supportability_gate/gate_policy.py) | Fixed adapter coverage and contract anti-weakening |
| [review_evidence.py](../../src/supportability_gate/review_evidence.py) | Fixed review structure, changed-boundary declarations, and handoff sentinels |
| [characterization.py](../../src/supportability_gate/characterization.py) | Manifest obligations and authenticated behavior compatibility |
| [refactor_targets.py](../../src/supportability_gate/refactor_targets.py) | Derived responsibility target inventory |
| [refactor_policy.py](../../src/supportability_gate/refactor_policy.py) | Trusted-owner scope, sequence, and runnability checks |
| [quality_profile.py](../../src/supportability_gate/quality_profile.py) | Fixed command profiles, source/dependency receipts, suppressions, and observed coverage |
| [quality_runner.py](../../src/supportability_gate/quality_runner.py) | Command construction, supervisor provisioning paths, and isolated target execution |
| [clause_inventory.py](../../src/supportability_gate/clause_inventory.py) | Immutable Standard traceability and canonical evidence/test-proof classifications |
| [reporting.py](../../src/supportability_gate/reporting.py) | Authoritative evaluator JSON, derived Markdown, and hosted summaries |
| [standard_block_ownership.py](../../src/supportability_gate/standard_block_ownership.py) | Policy and technical dependency ownership |
| [standard_results.py](../../src/supportability_gate/standard_results.py) | Eight-row composition, applicability, validation, and derived handoff |
| [standard_results_producer.py](../../src/supportability_gate/standard_results_producer.py) | Canonical evidence assembly from authenticated sources |
| [standard_results_enforcer.py](../../src/supportability_gate/standard_results_enforcer.py) | Exact-identity enforcement of one required row |
| [qualification_bundle.py](../../src/supportability_gate/qualification_bundle.py) | Bounded deterministic archives and offline restoration |

## Hosted and source-protection entrypoints

The [required workflow](../../.github/workflows/organization-required.yml) orchestrates captures,
quality execution, composition, bundle restoration, and lane enforcement. Hosted drivers and
their direct tests live under [tests](../../tests). The independently pinned
[Source Integrity workflow](../../.github/workflows/source-integrity.yml) uses its trusted
[source guard](../../tests/source_integrity_check.py) and [manifest](../source_integrity_manifest.json).

The fixed [hosted characterization collector](../../tests/hosted_characterization.py) runs
[the observed Python API collector](../../tests/characterization_observer.py) inside the existing
isolated target boundary for explicit schema-3 APIs. Static source/birth verification remains
in `characterization`; exact trusted-owner feature scope stays in `refactor_policy`, and the
shared result consumer repeats the join without loading target-analysis dependencies at import.

## Change routing

| Responsibility being changed | Start with |
|---|---|
| Profile/schema/coverage rules | `contract`, `gate_policy`, `quality_profile` |
| Source identity or supported language constructs | `git_changes`, `function_changes`, `complexity_metrics` |
| Architecture or module placement | `architecture_policy`, `modularity_policy` |
| Behavior and refactor evidence | `characterization`, `refactor_targets`, `refactor_policy` |
| Review records or handoff | `review_evidence`, `standard_results` |
| Execution boundary and receipts | `quality_runner`, `quality_profile`, hosted drivers |
| Row ownership or malformed canonical evidence | `standard_block_ownership`, `standard_results`, producer/enforcer |
| Retained bundle validation | `qualification_bundle` |

Use the [maintenance guide](../maintenance.md) before changing implementation or publication.
