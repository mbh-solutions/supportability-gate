# Repository Agent Contract

## Authority and task scope

- The current explicit owner-authorized task defines the work and stopping condition.
- Read this contract, the immutable Standard, the maintenance guide, and the relevant product
  documentation before editing. Read any issue explicitly assigned to the current task completely.
- State the intended outcome, authorized responsibilities, excluded work, required proof, and
  stopping condition before editing. A closed project or historical milestone is not new authority.
- Do not create or reopen a project, execute a successor, or expand the task without owner authority.
- Preserve unrelated local changes. Prefer an isolated checkout from verified current main.
- Historical chats, memory, global skills, and archived delivery records are context, not product
  requirements. Skills may guide execution and communication but may not override owner scope,
  immutable policy, architecture, evidence, or completion requirements.

## Immutable policy

- Do not edit, move, rename, reformat, regenerate, or replace `docs/supportability_standard.md`.
- Required Standard SHA-256:
  `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.
- Do not change the order, wording, bytes, or path of `docs/fixed_roadmap.md`.
- Required roadmap SHA-256:
  `76e80ffbf3006e001798d6e95a0ddd7df6ef1b37fd2b64666c4a4fd20d5ef8b6`.
- The roadmap is a frozen foundation delivery record, not a restriction to named consumer
  repositories or a requirement to reactivate its completed milestones.
- Preserve canonical inventory and source-integrity requirements unless their exact change is
  expressly authorized and independently qualified through existing protection.

## Product and architecture boundaries

- Supportability Gate is repository-agnostic. Organization assignment controls enrollment; fixed
  profile and production scope control admitted analysis. Do not add consumer-name policy logic.
- Static evaluation must not import or execute target modules. Target-native execution belongs
  only to the fixed isolated hosted capture/quality boundary.
- Git and Ruff use fixed argument vectors, finite timeouts, captured output, and no shell.
- Do not add arbitrary commands, executable paths, environment controls, exclusions, waivers,
  threshold overrides, or report-only PASS conversion to repository contracts.
- Keep every production function at McCabe/C901 complexity 10 or lower and preserve declared
  dependency direction. New modules require a named responsibility, relevant direct coverage,
  and traceability to the current authorized task.
- Prefer the smallest correct change. Do not add speculative infrastructure, generic manager or
  engine abstractions, vague dumping-ground modules, unrelated tooling, or broad reorganizations.
- Report evidence at its actual strength. Complete prose is not independently judged truth;
  author declarations are not owner attestations without the existing authentication control.
- Semantic Review remains retired. Do not add an AI/probabilistic/model-backed required check,
  mandatory Codex review, replacement semantic service, or second policy engine.

## Documentation maintenance

- `docs/product/` contains authoritative reader documentation. The wiki is a manually published
  view of those pages, not a separately authored product description.
- Update affected documentation with capability changes. Explain purpose, supported behavior,
  configuration, evidence, failure handling, and limits using neutral application examples.
- Keep consumer incidents, project administration, deployment inventories, and dated qualification
  narratives out of normal product pages. Preserve historical evidence in its dated archive.
- Retain original-path compatibility links and historical anchors when reorganizing editable docs.
- Follow the publication and verification checklist in `docs/maintenance.md`. Never claim wiki
  completion from source merge alone when wiki publication is part of the task.

## Required source proof

Use Python 3.12, the exact `requirements-dev.lock`, and the current final head:

```text
python -m ruff check src tests --no-cache
python -m ruff format --check src tests --no-cache
python -m ruff check src --select C901 --config "lint.mccabe.max-complexity = 10" --no-cache
python -m mypy --strict src
lint-imports --no-cache
python -m pytest -q
python -m compileall -q src tests
python -m build --wheel --no-isolation --outdir <owned-temporary-directory>
```

Install the wheel with the exact lock in a fresh environment, run `pip check`, and run installed
`supportability-gate --help`. Also run:

```text
python -m pytest -q tests/test_evaluate_complexity.py::test_standard_hash_change_fails_source_validation
git diff --check <base-sha> <head-sha> -- . ":(exclude)docs/supportability_standard.md"
```

The Standard exclusion applies only to whitespace validation; its exact hash protection remains
mandatory. Preserve required hosted proof and use existing exact-change review/owner evidence when
the applicable Gate requires it. Do not invent authority or bypass checks to deliver a task.

## Delivery and completion

- Verify the exact final head and all required checks/App identities, resolve actual native inline
  threads, merge normally, and read back default-branch delivery.
- For documentation-only changes, verify executable/workflow blobs and deployed protection stay
  unchanged; do not churn deployment pins.
- For executable changes, use only the explicitly authorized existing qualification and promotion
  path. Local green does not prove protected delivery or deployment.
- Treat agent statements as claims until directly verified. Separate facts, assumptions, unknowns,
  and limits; retain needed evidence before hosted retention expires.
- Close task issues or change tracking state only when the task authorizes that action and its
  actual required proof is complete. No historical project transition is implied by maintenance.
- Remove only owned generated outputs and verify the final isolated checkout state. Preserve the
  original checkout's unrelated files and changes. Stop once the authorized outcome is achieved.

## Advisory Codex review

Post `@codex review` only when the owner explicitly requests it in the current task. A push,
failure, timeout, missing acknowledgement, or previous review request does not authorize a retry.
Codex review is advisory and never required for qualification, merge, or completion. Resolve any
actual inline conversations through GitHub's native thread rule before merge.
