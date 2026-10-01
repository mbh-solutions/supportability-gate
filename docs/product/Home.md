# Supportability Gate

Supportability Gate is a repository-agnostic product assigned through GitHub organization
rulesets. It evaluates pull-request changes against the Supportability Standard and produces
independently enforced checks using authenticated evidence from the exact base and head commits.

It helps maintainers keep changes testable and reviewable: control function complexity, preserve
known behavior, maintain declared dependency boundaries, demonstrate test coverage, and retain a
review handoff. GitHub uses the required check results to enforce the protected merge decision.

## Principal capabilities

| Capability | What the product does |
|---|---|
| Eight separate checks | Reports each Standard responsibility independently, with its own evidence and failure ownership. |
| Complexity control | Measures touched functions, holds new functions to maximum 10, and requires touched legacy functions above the limit to improve. |
| Source and architecture analysis | Reads immutable Git blobs and evaluates supported import graphs, changed responsibilities, and new production locations. |
| Behavior evidence | Compares repeatable base/head captures and preserves stable behavioral obligations across supported scenario migrations. |
| Fixed quality profiles | Runs the supported language's checks with authenticated dependency, command, coverage, and outcome evidence. |
| Anti-weakening | Detects narrowed scope, reduced coverage, relaxed thresholds, and supported blanket suppressions. |
| Protected evidence | Binds results to commits, workflow/run identity, and artifact digests; rejects required evidence it cannot trust. |
| Durable evidence | Packages canonical results and diagnostics for offline validation after hosted artifacts expire. |

## Supported repositories

An organization assigns the Gate to repositories and branches through a native required-workflow
ruleset. Repository names and business domains do not select different policy. Each repository
commits its production scope and evidence using a fixed Python, TypeScript, or mixed profile.
Other languages and code outside the admitted profile are not certified.

Assignment and compatibility are separate: enrollment selects **where it runs**; the committed
profile and supported parser/command boundaries determine **what it can assess**.

## Read in this order

| Your goal | Guide |
|---|---|
| Assign the Gate and make a first protected change | [Getting Started](Getting-Started.md) |
| Understand the features and eight checks | [Eight Gates](Eight-Gates.md) |
| Prepare the committed inputs | [Repository Configuration](Repository-Configuration.md) |
| Understand a pull request's path to merge | [Pull Request Lifecycle](Pull-Request-Lifecycle.md) |
| Resolve a failed check | [Troubleshooting](Troubleshooting.md) |
| Understand what PASS establishes | [Evidence Model](Evidence-Model.md) and [Boundaries and Limitations](Boundaries-and-Limitations.md) |
| Administer protection | [Validation and Protection](Validation-and-Protection.md) |
| Inspect the design and implementation | [Architecture](Architecture.md) and [Module Map](Module-Map.md) |
| Use the evaluator or authorize a refactor | [CLI Reference](CLI-Reference.md) and [Refactor Authorization](Refactor-Authorization.md) |

The [Supportability Standard](../supportability_standard.md) is the immutable policy source.
PASS means the applicable defined rules and required evidence are satisfied. Human declarations
are validated for structure, identity, and binding; their qualitative truth is not independently
machine judged.
