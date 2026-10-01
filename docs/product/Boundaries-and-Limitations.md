# Boundaries and Limitations

## Supported scope

| Area | Current boundary |
|---|---|
| Assignment | Organization rulesets select repositories and branches; repository names do not select policy variants. |
| Profiles | Fixed Python, TypeScript, and Python-plus-TypeScript mixed contracts. |
| Source | Committed declared production roots and the supported parser/import graph. |
| Complexity | Maximum 10 for new/non-legacy touched functions; documented progressive improvement for touched legacy debt. |
| Execution | Fixed hosted commands with the defined isolated target boundary and authenticated provisioning/receipts. |
| Behavior | Bounded executable scenarios, observed responsibilities, and preserved admitted assertions. |
| Review | Structural and source-bound declarations; trusted-owner authentication where required. |

Outside-profile code, unclassified/dynamic architecture, subjective cohesion, naming quality, and
exhaustive intended behavior are not certified. The Gate is not a general security scanner,
deployment approval system, or replacement for product-specific acceptance testing.

## Short-task classification

The exception requires **exactly one newly added non-production file**, named `README.md` or under
`docs/` with a `.md` suffix, with **exactly one changed head line**. It cannot be the immutable
Standard, frozen roadmap, or product completion contract. Modified existing documents, multiple
lines/files, uncertain paths, and production changes use the full process.

Gate 7 still applies. Irrelevant lanes emit authenticated `NOT_APPLICABLE_SHORT_TASK`; a casual
description of a task as small does not authorize skipping checks.

## Human and configuration limits

People remain responsible for the qualitative truth of names, ownership explanations, cohesion,
intended behavior, and reviewability. Current complete prose is an author declaration unless the
existing owner control authenticates it. Neither human approval nor an owner attestation can waive
a deterministic BLOCK or replace required evidence.

Contracts cannot add arbitrary commands, executable paths, environment controls, exclusions,
waivers, threshold overrides, or report-only PASS conversion. Owner authorization is tied to a
fixed trusted numeric identity in the current implementation; assignment to another repository
does not automatically trust its administrators.

Codex review is optional and advisory. Semantic Review is retired; no model result is required
for qualification or merge. Native review-thread resolution still applies to actual conversations.

## Operational and proof limits

Hosted artifact retention is finite. Retained bundles validate recorded decisions offline but
cannot replace unavailable external logs or fresh current-head qualification. Exact runtimes are
selected by the workflow; a hosted runner label is still not a frozen host-image revision.
Optional dependencies must authenticate through admitted receipts; absence is not automatically
accepted just because a package is marked optional.

Static roles and coverage are bounded by declared roots and parser support. Path/span footprints
and one-target refactors do not prove semantic smallness. Adversarial tests and protected canaries
establish their named cases, not exhaustive assurance or a service-level objective.

Implementation references: [short-task classifier](../../src/supportability_gate/standard_results.py),
[profile limits](../../src/supportability_gate/contract.py),
[owner identity](../../src/supportability_gate/refactor_policy.py), and
[receipt validation](../../src/supportability_gate/quality_profile.py).
