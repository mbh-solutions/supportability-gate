# Refactor Authorization

`Supportability-Refactor-Authorization` schema `2.0` is the current owner-comment format. The
comment is accepted only from numeric owner ID `229662739` and is bound to the exact repository,
base, head, complete changed scope, and derived production targets.

```text
Supportability-Refactor-Authorization: {"base_sha":"<40 lowercase hex>","broad":false,"head_sha":"<40 lowercase hex>","related_tests":[{"path":"tests/test_cli.py","targets":["src/supportability_gate/cli.py::function:main:1-20"]}],"repository":"owner/repository","schema_version":"2.0","scope":["src/supportability_gate/cli.py","tests/test_cli.py"],"sequence":{"predecessor_sha":"<40 lowercase hex>","series_id":"cli-refactor","step":1},"targets":["src/supportability_gate/cli.py::function:main:1-20"]}
```

`related_tests` is an exact, sorted declaration. A narrow Python link must use
`tests/test_<source-stem>.py`; a narrow TypeScript link must use
`tests/<source-stem>.test.{js,mjs,cjs,ts,mts,cts}`. Each declared test and target must be present in
the authenticated changed scope and derived target inventory. Characterization proof paths retain
their existing fixed treatment. Other tests, docs, hidden production, stale links, or invented
targets still require exact broad authorization.

`series_id` identifies one refactor series. Step 1 starts a new series even when the base is an
unrelated refactor merge. Later steps must name the same series, cite the exact immediate merge as
`predecessor_sha`, increment the step by one, and retain the same target identity apart from its
line span. Missing lookup evidence fails closed; wrong-series, skipped, replayed, or substituted
predecessors block.

Changed paths, target kinds, names, and line spans are footprint evidence only. One target or a
large module span—including a thousand-line span—is not machine certification of semantic
smallness, cohesion, or quality.
