# Supportability Gate

Supportability Gate checks GitHub pull requests against the [Supportability Standard](docs/supportability_standard.md).
It helps keep code testable, maintainable, and safe to change by checking complexity, dependency
boundaries, behavior, and test coverage. GitHub blocks merging when required evidence fails or
cannot be verified.

The owner authorizes the work. The implementer prepares the code, tests, and explanations;
the Gate does not require the owner to review code or post an approval comment.

## What it provides

- Fixed complexity rules, including progressive improvement of touched legacy functions.
- Static source and architecture analysis without importing target modules in the evaluator.
- Repeatable base/head behavior captures and meaningful coverage obligations.
- Fixed language-native quality checks with isolated execution and authenticated provenance.
- Anti-weakening rules for thresholds, coverage, scope, and supported suppressions.
- Independent PASS, BLOCK, or TECHNICAL_FAILURE results and offline-restorable evidence bundles.

## Supported scope and assignment

The committed profiles support **Python, TypeScript/JavaScript, and mixed Python/frontend repositories**.
Organization rulesets select the repositories and branches that receive the Gate; repository
names and business domains do not select different policy. Declared production paths and admitted
parser/command boundaries determine what is assessed.

PASS establishes satisfaction of the applicable defined rules and required evidence. It does not
prove exhaustive correctness, design quality, or security. Test duration and hosted runner
availability determine waiting time; the Gate does not promise a fixed merge time.

For an enrolled repository, checks start automatically on pull requests. There is no desktop
application to launch or owner approval-comment setup. An implementer prepares the repository's
committed inputs; an organization administrator assigns the workflow.

## Start here

| Goal | Documentation |
|---|---|
| Inspect a pull request or prepare a repository | [Getting Started](https://github.com/mbh-solutions/supportability-gate/wiki/Getting-Started) |
| Understand the features and checks | [Eight Gates](https://github.com/mbh-solutions/supportability-gate/wiki/Eight-Gates) |
| Configure repository inputs | [Repository Configuration](https://github.com/mbh-solutions/supportability-gate/wiki/Repository-Configuration) |
| Resolve a failed check | [Troubleshooting](https://github.com/mbh-solutions/supportability-gate/wiki/Troubleshooting) |
| Understand evidence and assurance limits | [Evidence Model](https://github.com/mbh-solutions/supportability-gate/wiki/Evidence-Model) and [Boundaries](https://github.com/mbh-solutions/supportability-gate/wiki/Boundaries-and-Limitations) |
| Browse the full product guide | [Documentation home](docs/product/Home.md) or [published wiki](https://github.com/mbh-solutions/supportability-gate/wiki) |

The [Supportability Standard](docs/supportability_standard.md) is the immutable policy source.
Contributors use the [maintenance guide](docs/maintenance.md). Dated development and adoption
records are retained separately in [history](docs/history/README.md).
