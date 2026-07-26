# Supportability Gate

`supportability-gate` produces deterministic PASS or BLOCK evidence for Python functions
touched between immutable Git commits. Milestone 1 enforces progressive McCabe/C901 complexity
without importing or executing target repository code.

## Status

- Foundation scope: Milestone 1
- Complexity adapter: `python.c901-touched.v1`
- Maximum complexity: `10`
- Full Supportability Standard implementation: incomplete

Delivery state and milestone evidence are tracked in the
[Supportability Gate Delivery](https://github.com/orgs/mbh-solutions/projects/2) organization
project.

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
