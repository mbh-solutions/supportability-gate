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

## Deterministic assurance evidence

The current evidence classes are:

- `measured_fact` — an independently derived or authenticated observation bound to immutable
  inputs; raw target reports are not automatically measured facts;
- `deterministic_decision` — a reproducible rule result over authenticated inputs;
- `author_declaration` — complete, current, exact-change-bound author prose whose qualitative truth
  and quality are not machine judged;
- `authenticated_owner_attestation` — an author declaration authenticated by an existing
  repository-owner control. This label is unavailable without that authentication.

The canonical 218-clause registry records one class and one retained test-proof class per clause.
Family-level tests intentionally share rule behavior; they are not 218 independent behavior tests.

## Human review

People remain responsible for whether names express responsibilities, boundaries are cohesive,
behavior remains intended, and a change is reviewable. The Gate may validate the presence,
identity, freshness, completeness, and exact-change binding of those declarations, but it does not
judge their qualitative truth. Human approval cannot waive deterministic BLOCK, reduce violations,
or replace required evidence.

## Fixed V1 exclusions

V1 excludes custom GitHub Apps, external verifiers, timers, polling, webhooks, repository dispatch,
hosted services, queues, runtime attestation, release proof chains, target packs, repository-name
logic, SQLite or database logic, security scanners, AI review orchestration, automatic repair,
periodic audits, arbitrary command configuration, waivers, allowlists, known-debt approval, and
report-only pass conversion.

Later historical milestones added the current Python, TypeScript, and mixed-profile deterministic
engine. The former semantic-review service is retired and is not part of current enforcement. No AI
reviewer, probabilistic grader, model-backed required check, semantic approval, replacement
semantic-review service, or second policy engine is authorized.

Certification is limited to the committed Python, TypeScript, or mixed profile and its declared
production paths. Outside-profile code, unclassified or dynamic architecture, subjective cohesion,
and exhaustive intended behavior are not certified. Static architecture roles are inferred within
the supported contract and parser boundary; they are not a proof of every runtime dependency.

## Package dependency direction

The authoritative current layer list is the `Supportability gate dependency direction` Import
Linter contract in `pyproject.toml`. It orders result production/enforcement above result
composition, CLI/reporting, the eight policy responsibilities, parsing/change analysis, and the
bottom-layer contract, Git, review-evidence, clause-registry, and block-ownership modules. Import
Linter enforces that declared static graph. Dynamic imports and architecture outside declared
production paths remain outside this certification.
