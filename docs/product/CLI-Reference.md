# CLI Reference

The installed entrypoint is `supportability-gate`; `python -m supportability_gate` exposes the
same evaluator parser. The public evaluation subcommand is `evaluate-complexity`, although
its result also includes the supported static policy and review evidence responsibilities.

## Install the exact source environment

Use Python `>=3.12,<3.13`, Git, an already selected trusted Gate source commit, and its exact
`requirements-dev.lock`. The organization administrator or maintainer supplies the qualified commit ID. Clone
`https://github.com/mbh-solutions/supportability-gate.git` with Git and check out that exact commit.
Open a terminal in that checkout, where `requirements-dev.lock` and `pyproject.toml` are present.

On Windows, enter these commands in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps .
.\.venv\Scripts\supportability-gate.exe --help
.\.venv\Scripts\supportability-gate.exe --version
```

On Linux or macOS, enter these commands in a terminal with Python 3.12 available:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps .
.venv/bin/supportability-gate --help
.venv/bin/supportability-gate --version
```

The help output lists `evaluate-complexity`; the version command prints the installed package
version. Use the executable inside this environment for the commands below, or activate the
environment first. Local installation supplies tools, not authenticated hosted evidence or
organization merge protection. Ordinary PR users do not need this installation.

## Evaluate immutable commits

All listed evaluation arguments are required. The following POSIX-shell example uses placeholders
that must be replaced with actual matching identities and files from the hosted quality run:

```sh
supportability-gate evaluate-complexity \
  --repository /absolute/application-checkout \
  --base-ref <full-base-sha> \
  --head-ref <full-head-sha> \
  --contract-path .supportability.toml \
  --quality-evidence /absolute/quality-gates.json \
  --quality-repository example-org/order-service \
  --quality-repository-id <repository-id> \
  --quality-run-id <run-id> \
  --quality-run-attempt <attempt> \
  --quality-job quality-profile \
  --quality-artifact-id <artifact-id> \
  --quality-artifact-digest <artifact-digest> \
  --quality-artifact-metadata /absolute/artifact-metadata.json \
  --quality-capture-sha256 <capture-sha256> \
  --workflow-sha <trusted-workflow-sha> \
  --output-directory /absolute/evidence
```

PowerShell uses a backtick for line continuation instead of `\`. Supply full immutable SHAs,
repository/run/attempt identity, job/artifact identity, trusted workflow SHA, and capture digests
from the actual run. Do not invent a local run ID or substitute unauthenticated authored quality
JSON. Static evaluation does not run arbitrary repository commands.

## Outputs and exits

The evaluator writes authoritative `complexity-result.json`, derived `complexity-result.md`, and
validated `quality-provenance.json`. The required hosted workflow separately combines these with
characterization/refactor evidence into `standard-results.v3` and enforces eight rows.

| Evaluation exit | Meaning |
|---|---|
| `0` | PASS |
| `1` | BLOCK |
| `2` | TECHNICAL_FAILURE |

## Restore retained evidence

```text
python -P -m supportability_gate.qualification_bundle restore --bundle qualification-bundle.zip
```

This separate module command exits 0 when restoration validates, including a faithfully restored
BLOCK or TECHNICAL_FAILURE decision, and 2 when restoration fails. See [Evidence Model](Evidence-Model.md)
for retention and offline boundaries. Bundle construction is normally owned by the hosted evidence
job; module help documents its `build` arguments.

Implementation references: [CLI parser](../../src/supportability_gate/cli.py),
[reporting](../../src/supportability_gate/reporting.py), and
[bundle commands](../../src/supportability_gate/qualification_bundle.py).
