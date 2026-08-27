# Supportability Gate

`supportability-gate` produces deterministic PASS or BLOCK evidence from immutable Git commits.
Static evaluation never imports target code; fixed stack-native quality commands run only in
isolated GitHub-hosted jobs.

## Status

Current product status, authorized work, milestone evidence, and remaining work are recorded only
in the [Product Completion Contract](docs/product_completion_contract.md).

The [Supportability Gate Qualification and Advisory Review](https://github.com/orgs/mbh-solutions/projects/10)
organization project is successor execution authority. Projects #2, #3, #6, and #8 are historical;
Project #9 becomes historical through the S00 retirement transaction.

## Requirements

- Python `>=3.12,<3.13`
- Git
- Dependencies installed from `requirements-dev.lock`

## Evaluate

Complexity evaluation uses adapter `python.c901-touched.v1` with maximum complexity `10`.

```powershell
python -m supportability_gate evaluate-complexity `
  --repository C:\absolute\target-repository `
  --base-ref <full-commit-sha> `
  --head-ref <full-commit-sha> `
  --contract-path .supportability.toml `
  --quality-evidence C:\absolute\quality-gates.json `
  --quality-repository owner/repository `
  --quality-repository-id <github-repository-id> `
  --quality-run-id <github-actions-run-id> `
  --quality-run-attempt <github-actions-run-attempt> `
  --quality-job quality-profile `
  --quality-artifact-id <github-artifact-id> `
  --quality-artifact-digest <github-artifact-digest> `
  --quality-capture-sha256 <quality-evidence-sha256> `
  --workflow-sha <full-workflow-commit-sha> `
  --output-directory C:\absolute\evidence-directory
```

Exit `0` means PASS, `1` means BLOCK, and `2` means TECHNICAL_FAILURE. JSON is
authoritative; Markdown is derived from that JSON.

## Source validation

GitHub Actions runs lint, format, C901, strict typing, import boundaries, all tests, compile,
wheel build, fresh-environment wheel install, installed CLI smoke, and these owner-required source
controls:

1. `docs/supportability_standard.md` must have SHA-256
   `81653c5057c1555f8b6d41c6e5999d0b54caa178a2ca97a07216147ec16133e2`.
2. All other changed files must pass:

   ```powershell
   git diff --check <base-sha> <head-sha> -- . ":(exclude)docs/supportability_standard.md"
   ```

The path exclusion is limited to the immutable owner-authored standard. It does not waive its
integrity check or exclude any other file.

<!-- S11 ruleset migration canary: consolidated retry -->
