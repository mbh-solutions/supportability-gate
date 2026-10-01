# Getting Started

This guide separates the organization administrator's assignment from the repository maintainer's
preparation. Use a fictional repository such as `example-org/order-service` when trying examples.

## 1. Check compatibility and authority

- Select the supported Python, TypeScript, or mixed profile for the declared production roots.
- Confirm the organization has GitHub's native organization required-workflow capability and
  permission to select repositories and branches. The Gate does not emulate an unavailable feature.
- Identify the already qualified Gate workflow revision. Enrollment must use a full trusted commit
  SHA, not a moving branch or an unqualified candidate.
- Confirm who can provide the existing trusted-owner authorization when a change requires it.
  Repository admin status alone does not confer that identity; see [Refactor Authorization](Refactor-Authorization.md).

## 2. Prepare the repository baseline

Commit these files through the repository's existing authorized process **before** assigning the
Gate, so the first evaluated pull request has a valid contract at its base commit:

| Input | Preparation |
|---|---|
| `.supportability.toml` | Select fixed adapters, declare production and high-risk paths, and retain maximum complexity 10. |
| `.supportability-review.toml` | Describe the actual change and responsibilities; use the required derived handoff markers. |
| `.supportability-characterization.json` | Declare real executable scenarios and their covered paths and obligations. |

Also commit the scenario drivers and golden files, meaningful tests, and the profile's dependency
metadata. Hosted Python target provisioning reads static `project.dependencies` from
`pyproject.toml`; it rejects dynamic dependency declarations and direct URL requirements.
The trusted Gate tools use their own exact `requirements-dev.lock`. TypeScript target setup
requires `package.json` and `package-lock.json`. Dependencies must authenticate under the fixed
hosted profile. The mixed profile needs both language preparations and separate language-specific
characterization scenarios.

Use [Repository Configuration](Repository-Configuration.md) for complete contract examples and
the characterization output protocol. Parsing an example is not protected qualification.

## 3. Assign the organization ruleset

An administrator selects the intended repositories and branches in the existing native ruleset,
references the qualified `.github/workflows/organization-required.yml` from the Gate source
repository, and pins it to the trusted full SHA. The deployed source is
`mbh-solutions/supportability-gate`; application repository names remain arbitrary.

Require all eight exact [Supportability contexts](Eight-Gates.md) from GitHub Actions App `15368`,
strict current-head checks, pull-request merge, native review-thread resolution, and no bypass
actors. Read back that the ruleset is effective on the selected branch. A workflow file or a local
command by itself does not enroll a repository or block merge.

The Gate-source repository has additional protections for its own implementation. Do not add
consumer requirements named Source Validation or Source Integrity merely by copying that source
repository's rule; see [Validation and Protection](Validation-and-Protection.md).

## 4. Make the first protected change

1. Make a small, real change and refresh the review evidence for its exact changed boundaries.
2. Open a pull request. Supply exact-change owner authorization if the applicable refactor policy
   requires it; it must bind the current base and head.
3. Inspect all eight check results and the attached canonical evidence. A local green result or
   aggregate summary cannot replace a required lane.
4. Resolve policy defects or evidence/runtime failures using [Troubleshooting](Troubleshooting.md).
   Every new push needs fresh evidence and checks; refresh stale authorization when necessary.
5. Merge normally only after required checks and native thread resolution pass on the final head.

Follow the [Pull Request Lifecycle](Pull-Request-Lifecycle.md) for the full process. No repository
contract can add a waiver, arbitrary command, environment control, or report-only PASS conversion.

Implementation references: [workflow](../../.github/workflows/organization-required.yml),
[contract parser](../../src/supportability_gate/contract.py), and
[hosted dependency setup](../../tests/hosted_quality_profile.py).
