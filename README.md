# Supportability Gate

`supportability-gate` turns immutable pull-request base and head commits into deterministic
PASS, BLOCK, or TECHNICAL_FAILURE evidence. Static evaluation never imports target code; fixed
stack-native quality commands run only in isolated GitHub-hosted jobs.

**[Read the Supportability Gate wiki](https://github.com/mbh-solutions/supportability-gate/wiki)**
for architecture, configuration, evidence, lifecycle, and module details.

## Status

Integrated qualification and organization-wide activation completed on 2026-08-27. Project #10 is
temporarily reopened only for the bounded
[S14 maintenance issue](https://github.com/mbh-solutions/supportability-gate/issues/192), which
permits an exact high-risk contract entry to retire with its deleted file. Durable product status
and historical evidence remain in the [Product Completion Contract](docs/product_completion_contract.md).

Protected `main` requires Source Validation plus eight strict, independently owned deterministic
contexts:

1. `Supportability 1 - Cyclomatic Complexity`
2. `Supportability 2 - Separation of Concerns`
3. `Supportability 3 - Dependency Direction`
4. `Supportability 4 - Domain Modularity`
5. `Supportability 5 - Characterization`
6. `Supportability 6 - Incremental Refactor`
7. `Supportability 7 - Quality Gates`
8. `Supportability 8 - Review Handoff`

One lane's policy failure does not become another lane's failure. Shared technical failure is
allowed only for an explicitly named identity or artifact dependency. Codex review is optional,
advisory, and non-blocking; GitHub's native review-thread rule still blocks unresolved inline
conversations.

## Repository inputs

Each participating repository commits three fixed inputs:

- `.supportability.toml` — one language or the fixed Python-plus-TypeScript profile, production
  and high-risk paths, approved adapters, and complexity maximum. The Gate loads this contract
  from the base commit; weakening or narrowing it in the pull request blocks. The only removal
  exception is an exact high-risk path whose tracked file is deleted in the same diff.
- `.supportability-review.toml` — structured behavior, architecture, responsibility, refactor,
  and handoff evidence bound to exact base/head Git blobs.
- `.supportability-characterization.json` — fixed hosted scenarios and their covered source
  paths for authenticated base/head behavior capture.

The contract cannot add arbitrary commands, executable paths, environment controls, exclusions,
waivers, or threshold overrides.

## Requirements

- Python `>=3.12,<3.13`
- Git
- Dependencies pinned by `requirements-dev.lock`

## CLI

The public command is `evaluate-complexity`. It applies each contract-selected fixed complexity
adapter with maximum `10`.

```powershell
python -m supportability_gate evaluate-complexity `
  --repository C:/absolute/target-repository `
  --base-ref <full-commit-sha> `
  --head-ref <full-commit-sha> `
  --contract-path .supportability.toml `
  --quality-evidence C:/absolute/quality-gates.json `
  --quality-repository owner/repository `
  --quality-repository-id <github-repository-id> `
  --quality-run-id <github-actions-run-id> `
  --quality-run-attempt <github-actions-run-attempt> `
  --quality-job quality-profile `
  --quality-artifact-id <github-artifact-id> `
  --quality-artifact-digest <github-artifact-digest> `
  --quality-artifact-metadata C:/absolute/artifact-metadata.json `
  --quality-capture-sha256 <quality-evidence-sha256> `
  --workflow-sha <full-workflow-commit-sha> `
  --output-directory C:/absolute/evidence-directory
```

`supportability-gate --version` prints the package version.

A normal authenticated evaluation writes:

- `complexity-result.json` — authoritative evaluator result;
- `complexity-result.md` — Markdown derived from that JSON;
- `quality-provenance.json` — validated hosted quality-command provenance.

The required workflow combines those sources with characterization and refactor evidence into
`standard-results.json`, schema `standard-results.v3`, then enforces its eight rows separately.

Exit `0` means PASS, `1` means BLOCK, and `2` means TECHNICAL_FAILURE.

## Enforcement boundary

- Full commit SHAs, exact Git blobs, artifact digests, workflow/run identity, and source hashes are
  bound into evidence.
- Missing, malformed, stale, mismatched, or unresolved required evidence fails closed.
- Target source is parsed statically by the Gate; target-native commands execute only in isolated
  GitHub-hosted jobs.
- An eligible one-line added `README.md` or `docs/*.md` file can classify as SHORT_TASK. Gate 7
  still runs; irrelevant lanes emit authenticated `NOT_APPLICABLE_SHORT_TASK`. Uncertain or
  broader changes use the full process.

## Source validation

GitHub Actions runs Ruff lint and format, C901 at maximum 10, strict mypy, Import Linter, all
tests, compileall, wheel build, fresh-environment wheel install, installed CLI help, and these
owner-required controls:

1. `docs/supportability_standard.md` must retain SHA-256
   `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.
2. Every other changed file must pass:

   ```powershell
   git diff --check <base-sha> <head-sha> -- . ":(exclude)docs/supportability_standard.md"
   ```

That exclusion applies only to whitespace checking. The immutable Standard remains hash-protected.
