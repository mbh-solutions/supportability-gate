# Supportability Gate

Supportability Gate is a shared set of automated checks for GitHub pull requests. It checks
whether a proposed change has the required complexity, architecture, behavior, test coverage,
and handoff evidence. GitHub uses eight separately named results to control merging.

The owner authorizes the task. The implementer supplies the code, tests, and explanations.
The owner is not asked to review code, certify test results, or post an approval comment.

## Use the Gate

1. [Getting Started](Getting-Started.md) — inspect a pull request or arrange repository setup.
2. [Eight Gates](Eight-Gates.md) — understand what each result means.
3. [Pull Request Lifecycle](Pull-Request-Lifecycle.md) — follow a change through checking and merge.
4. [Troubleshooting](Troubleshooting.md) — find the cause of a red check or a long-running job.

If your repository is enrolled, the checks start automatically for pull requests. There is
nothing to install on the owner's computer. If it is not enrolled, an organization administrator
and an implementer must prepare and assign the fixed workflow; see Getting Started.

## Configure and maintain it

| Responsibility | Reference |
|---|---|
| Production paths, language profiles, review declarations | [Repository Configuration](Repository-Configuration.md) |
| Behavior scenarios, expected outputs, observed APIs, corrections | [Characterization Reference](Characterization-Reference.md) |
| Work authorization and intentional behavior changes | [Refactor Authorization](Refactor-Authorization.md) |
| Isolation, data flow, component ownership | [Architecture](Architecture.md) and [Module Map](Module-Map.md) |
| Artifacts, runtime coverage, verification, retention | [Evidence Model](Evidence-Model.md) |
| Enrollment and protected Gate maintenance | [Validation and Protection](Validation-and-Protection.md) |
| Local installation, evaluator arguments, bundle restoration | [CLI Reference](CLI-Reference.md) |

## What a pass establishes

The supported profiles cover Python, TypeScript/JavaScript, and mixed repositories. Organization
assignment selects where the Gate runs; committed production paths and supported parsers determine
what it can assess. Repository names do not select different policy.

PASS means the applicable defined rules and required evidence are satisfied. Finite scenarios
cannot prove every possible behavior, and complete author explanations are not independent
judgments of naming or design quality. See [Boundaries and Limitations](Boundaries-and-Limitations.md).

The [Supportability Standard](../supportability_standard.md) is the immutable policy source.
