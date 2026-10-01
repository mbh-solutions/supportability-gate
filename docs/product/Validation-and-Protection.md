# Validation and Protection

There are two distinct protection subjects: a repository receiving the Gate, and the repository
maintaining the Gate's own implementation. Assignment is an organization operation; language
compatibility is a separate fixed-profile requirement.

## An enrolled application repository

The native organization ruleset selects intended repository identities and branch references. It
requires the qualified, SHA-pinned Gate workflow and all eight exact [Supportability contexts](Eight-Gates.md)
from GitHub Actions App `15368`, with strict current-head policy and no bypass actors.

The workflow reads the repository's base contract and source-bound evidence, runs fixed admitted
profiles, and publishes independent results. Preserve pull-request merge and native unresolved
review-thread protection. Other repository-native required checks can coexist with the Gate.

Read back effective rules and the source workflow revision after enrollment. An administrator can
assign the Gate to another eligible repository; the current selection is a deployment choice,
not a product limit or automatic organization-wide enrollment.

## The Gate source repository

In addition to the eight contexts, the source repository requires:

| Control | What it protects |
|---|---|
| `Source Validation` | Exact-lock lint/format/C901, strict types, dependency direction, tests, compilation, packaging/install smoke, Standard integrity, and whitespace checks. |
| `Source Integrity` | Independently pinned immutable Standard/roadmap bytes, canonical clause registry, trusted workflow/tool pins, and sandboxed validator conformance. |

Source Integrity's required workflow is pinned independently of the candidate. Its trusted guard
owns the conformance fixture and checks candidate behavior inside the isolation boundary. This
prevents candidate workflows/tests from qualifying their own weakening of the essential controls.

These source-only contexts are not generic extra check requirements for each consumer repository.
The consumer's fixed quality profile remains part of its eight-lane evaluation.

## Source upgrades and rollback

Executable Gate upgrades follow normal protection, qualification of the exact merged candidate,
and explicit authorized pin promotion. Preserve strictness, context/App identities, thread
resolution, enrolled scope, and zero bypass. Roll back through a protected revert or previously
qualified pin; never remove protection to obtain a green result.

Documentation-only changes do not require deployment pin churn when executable and workflow blobs
remain identical to the deployed revision. Wiki publication is a separate documentation action.

## Final-head verification

- Confirm the current base/head, all required successful contexts, and their App identity.
- Confirm effective organization assignment and the qualified workflow pin.
- Resolve actual native inline conversations.
- Merge normally and verify the resulting default-branch commit.

Local checks and an aggregate summary cannot substitute for these controls. Contributor commands
and documentation publication requirements are in the [maintenance guide](../maintenance.md).

Implementation references: [consumer workflow](../../.github/workflows/organization-required.yml),
[source validation](../../.github/workflows/source-validation.yml),
[source integrity](../../.github/workflows/source-integrity.yml), and
[trusted manifest](../source_integrity_manifest.json).
