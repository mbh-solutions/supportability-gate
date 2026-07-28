# Supportability Gate

`supportability-gate` produces deterministic PASS or BLOCK evidence from immutable Git commits
without importing or executing target repository code. Successor enforcement work is mapping every
normative Supportability Standard clause before adding later clause-specific enforcement.

## Status

- Completed successor scope: Supportability Standard Enforcement Milestones 1–2
- Current authorized work: Enforcement Milestone 3
- Completed capability: normative-clause inventory and trusted semantic-review channel
- Existing complexity adapter: `python.c901-touched.v1`; maximum complexity: `10`
- Full Supportability Standard runtime: **incomplete**

Delivery state and milestone evidence are tracked in the
[Supportability Standard Enforcement](https://github.com/orgs/mbh-solutions/projects/3)
organization project. Project #2 is historical and is not successor execution authority.

## Requirements

- Python `>=3.12,<3.13`
- Git
- Dependencies installed from `requirements-dev.lock`

## Evaluate

```powershell
python -m supportability_gate evaluate-complexity `
  --repository C:\absolute\target-repository `
  --base-ref <full-commit-sha> `
  --head-ref <full-commit-sha> `
  --contract-path .supportability.toml `
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
