# Repository Agent Contract

## Immutable policy

- Do not edit, rename, reformat, regenerate, or replace `docs/supportability_standard.md`.
- Required SHA-256:
  `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.
- Source whitespace validation excludes only this immutable file; its exact SHA-256 check remains
  mandatory.
- Milestone order and wording in `docs/fixed_roadmap.md` are frozen.

## Product boundary

- Execute only the single slice marked `In Progress` in the
  [Supportability Gate Deterministic Assurance Remediation](https://github.com/orgs/mbh-solutions/projects/25)
  project under its repository issue.
- Never import or execute target repository code.
- Git and Ruff use fixed argument vectors, finite timeouts, captured output, and no shell.
- Do not add commands, executable paths, environment controls, exclusions, waivers, or threshold
  overrides to repository contract.
- Keep every production function at McCabe/C901 complexity 10 or lower.

## Required reading before mutation

Before planning or editing, read:

1. `AGENTS.md`
2. `docs/supportability_standard.md`
3. `docs/fixed_roadmap.md`
4. `docs/product_completion_contract.md`
5. the complete issue for the single active Project #25 slice and the Project #25 README

Before editing, report:

```text
Final product objective:
Active milestone:
Completed milestones:
Current milestone definition of done:
Remaining milestones:
Authorized files or responsibilities:
Prohibited work:
Required runtime proof:
Stop condition:
```

## Scope and architecture control

### Global skill boundary

- Caveman and Ponytail are approved.
- Caveman may control communication brevity and clarity.
- Ponytail may control implementation minimalism: prefer the smallest correct change, avoid
  overengineering, and use one clear line or small focused change when that fully satisfies the
  requirement.
- Global skills may influence communication style, planning discipline, execution structure,
  verification habits, and implementation minimalism.
- Global skills must not add, remove, reinterpret, replace, or override product requirements,
  milestone scope, architecture requirements, evidence requirements, completion conditions,
  terminal labels, authorized files, or prohibited work.
- When a global skill conflicts with the authoritative repository documents or owner-authorized
  active milestone directive, the repository documents and active directive control.

- The active milestone directive and authoritative repository documents are the only product
  requirement sources.
- Do not import product requirements, milestone scope, architecture requirements, evidence
  requirements, completion conditions, terminal labels, authorized files, or prohibited work from
  memory, global skills, prior chats, former Governance repositories, or unrelated repositories.
- Do not begin later-milestone work until explicitly authorized by the owner.
- Do not add speculative infrastructure, generalized configuration, reusable abstractions,
  future-proofing, or optional mechanisms not required by the active milestone.
- Do not create `utils`, `helpers`, `common`, `misc`, `stuff`, generic manager, generic engine, or
  generic processor modules.
- Every new production module must have one named responsibility, explicit dependency direction,
  direct test coverage, and a requirement traceable to the active milestone.
- Do not reorganize or replace proven interfaces unless required by a failing acceptance test or
  explicit owner instruction.
- Planning, documentation, local tests, and summaries are not completion when runtime or GitHub
  evidence is required.
- Stop after the active milestone reaches its exact definition of done.
- Do not continue into cleanup, hardening, remediation, or the next milestone without owner
  authorization.

## Completion ledger control

- `docs/product_completion_contract.md` is the persistent product-status ledger.
- At slice completion, update only the applicable slice status, evidence, remaining work,
  product status, and next authorized milestone fields.
- Do not rewrite the frozen roadmap or silently add requirements.
- Do not mark a milestone complete without direct proof.
- After updating the ledger, stop.

## Completion claims

- Treat agent statements as claims until independently verified.
- Separate verified facts, unsupported claims, assumptions, and unknowns.
- Do not claim `FOUNDATION VERTICAL SLICE: GREEN` or `SOURCE PROTECTION: ACTIVE` without the exact
  evidence required by the Milestone 1 directive.
- Do not claim full Supportability Standard runtime while any required milestone remains incomplete.

## Codex review

Codex review is owner opt-in only. An agent may post `@codex review` only when the owner explicitly
requests it in the current task. A push, check failure, timeout, missing acknowledgement, or earlier
review request never authorizes an automatic request, retry, or blocking wait. Codex review is
advisory and is never required for qualification, merge, or completion. If an owner-requested
review creates an inline conversation, resolve that conversation through GitHub's native
review-thread resolution before merge.

## Active milestone control

Before editing:

1. Identify the single slice marked `In Progress` in the
   [Supportability Gate Deterministic Assurance Remediation](https://github.com/orgs/mbh-solutions/projects/25)
   project. Projects #2, #3, #6, #8, #9, and #10 are historical and must not be changed.
2. Read its milestone issue completely.
3. State:
   - terminal capability;
   - direct evidence requirement;
   - approved scope;
   - excluded scope;
   - single next action;
   - stopping condition.
4. Do not work on another milestone.
5. Do not add packages, tooling, cleanup, documentation, architecture, or abstractions not required
   by the active milestone.
6. Stop when the terminal capability and required evidence are achieved.
7. Do not mark Evidence complete without direct proof.
8. Do not close the slice until:
   - Evidence is Proven;
   - Scope is On scope;
   - Stop confirmed is Yes.
9. Never activate a Project #25 successor from an implementation task. Leave delivery evidence for
   independent controller verification and stop; Ready still requires separate owner execution
   authorization.

## Required source proof

Run all commands with Python 3.12 and exact lock:

1. `python -m ruff check src tests --no-cache`
2. `python -m ruff format --check src tests --no-cache`
3. `python -m ruff check src --select C901 --config "lint.mccabe.max-complexity = 10" --no-cache`
4. `python -m mypy --strict src`
5. `lint-imports --no-cache`
6. `python -m pytest -q`
7. `python -m compileall -q src tests`
8. `python -m build --wheel --no-isolation --outdir <temporary-directory>`
9. Install wheel in a fresh environment.
10. Run installed `supportability-gate --help`.
11. `python -m pytest -q tests/test_evaluate_complexity.py::test_standard_hash_change_fails_source_validation`
12. `git diff --check <base-sha> <head-sha> -- . ":(exclude)docs/supportability_standard.md"`

Remove caches, build output, wheels, and egg-info before completion.
