# Product Scope

## Purpose

`supportability-gate` centrally interprets Supportability Standard for pull-request
changes across repositories in `mbh-solutions` organization. It emits deterministic
PASS, BLOCK, or TECHNICAL_FAILURE evidence from immutable base and head commits.

## Responsibility split

GitHub owns pull-request events, organization rulesets, required workflows, protected merge paths,
and merge blocking.

Existing tools own linting, formatting, complexity calculation, typing, tests, package builds, and
import-boundary checks.

`supportability-gate` owns policy interpretation, base/head comparison, gate-coverage
decisions, anti-weakening decisions, behavior-evidence requirements, and deterministic evidence.

## Deterministic rules

- Inputs are full immutable Git commit SHAs.
- Contract is loaded only from base commit.
- Candidate contract change blocks.
- Target source is parsed statically and never imported or executed.
- McCabe values come from pinned `mccabe` package.
- Ruff runs isolated with C901, Python 3.12, and maximum complexity 10.
- JSON contains no timestamps, random values, machine paths, or temporary paths.
- Collections are sorted; identical inputs and tools produce byte-identical JSON.
- Missing, malformed, unresolved, or parity-conflicting evidence fails closed.

## Structured review evidence

Machine-checkable review evidence will identify gate coverage, behavior proof, characterization
proof, architecture review, and responsibility boundaries. These requirements remain
NOT_IMPLEMENTED until their roadmap milestone is delivered; metadata alone cannot create PASS.

## Human review

Human review judges whether names express responsibilities, boundaries are cohesive, behavior
remains intended, and a change is reviewable. Human approval cannot waive deterministic BLOCK,
reduce violations, or replace required evidence.

## Fixed V1 exclusions

V1 excludes custom GitHub Apps, external verifiers, timers, polling, webhooks, repository dispatch,
hosted services, queues, runtime attestation, release proof chains, target packs, repository-name
logic, SQLite or database logic, security scanners, AI review orchestration, automatic repair,
periodic audits, arbitrary command configuration, waivers, allowlists, known-debt approval, and
report-only pass conversion.

The historical V1 exclusions remain intact. Owner-authorized successor Enforcement Milestone 2
adds only its outbound GitHub App semantic verifier, localhost CLIProxyAPI call, polling, and
user-scoped scheduled task under `docs/enforcement_milestone_2.md`; no other exclusion is relaxed.

## Package dependency direction

```text
__main__
  -> cli
    -> reporting
    -> complexity_policy
      -> contract
      -> function_changes
      -> complexity_metrics
        -> function_changes
          -> git_changes
```

`contract` and `git_changes` import no internal modules. Import Linter enforces
an acyclic layered graph with those modules as independent bottom-layer siblings.
