# Supportability Gate

Supportability Gate is a repository-agnostic product assigned through GitHub organization
rulesets. It evaluates pull-request changes against the
[Supportability Standard](docs/supportability_standard.md) and produces
independently enforced checks using authenticated evidence from the exact base and head commits.

It helps maintainers keep changes testable and reviewable by controlling complexity, preserving
known behavior, checking supported dependency boundaries, authenticating quality coverage, and
requiring a current review handoff. GitHub enforces the protected merge decision through eight
independent required checks.

## What it provides

- Fixed complexity rules, including progressive improvement of touched legacy functions.
- Static source and architecture analysis without importing target modules in the evaluator.
- Repeatable base/head behavior captures and meaningful coverage obligations.
- Fixed language-native quality checks with isolated execution and authenticated provenance.
- Anti-weakening rules for thresholds, coverage, scope, and supported suppressions.
- Independent PASS, BLOCK, or TECHNICAL_FAILURE results and offline-restorable evidence bundles.

## Supported scope and assignment

The committed profiles support **Python, TypeScript, and mixed Python/TypeScript repositories**.
Organization rulesets select the repositories and branches that receive the Gate; repository
names and business domains do not select different policy. Declared production paths and admitted
parser/command boundaries determine what is assessed.

PASS establishes satisfaction of the applicable defined rules and required evidence. Naming,
cohesion, design quality, and exhaustive intended behavior remain human responsibilities.

## Start here

| Goal | Documentation |
|---|---|
| Prepare and enroll a repository | [Getting Started](docs/product/Getting-Started.md) |
| Understand the features and checks | [Eight Gates](docs/product/Eight-Gates.md) |
| Configure repository inputs | [Repository Configuration](docs/product/Repository-Configuration.md) |
| Resolve a failed check | [Troubleshooting](docs/product/Troubleshooting.md) |
| Understand evidence and assurance limits | [Evidence Model](docs/product/Evidence-Model.md) and [Boundaries](docs/product/Boundaries-and-Limitations.md) |
| Browse the full product guide | [Documentation home](docs/product/Home.md) or [published wiki](https://github.com/mbh-solutions/supportability-gate/wiki) |

The [Supportability Standard](docs/supportability_standard.md) is the immutable policy source.
Contributors use the [maintenance guide](docs/maintenance.md). Dated development and adoption
records are retained separately in [history](docs/history/README.md).
