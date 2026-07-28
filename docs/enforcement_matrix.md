# Enforcement Matrix

Every required `NOT_IMPLEMENTED` row keeps full product incomplete.

| Supportability requirement | Enforcement class | Milestone | Current state |
|---|---|---:|---|
| Immutable owner-authored standard integrity | DETERMINISTIC | 1 | IMPLEMENTED |
| Git base/head changed-file identity | DETERMINISTIC | 1 | IMPLEMENTED |
| Touched Python function and method binding | DETERMINISTIC | 1 | IMPLEMENTED |
| McCabe/C901 maximum 10 for new and non-legacy touched functions | DETERMINISTIC | 1 | IMPLEMENTED |
| Progressive tightening for touched legacy functions | DETERMINISTIC | 1 | IMPLEMENTED |
| Deterministic JSON and derived Markdown evidence | DETERMINISTIC | 1 | IMPLEMENTED |
| Product source lint, format, type, complexity, test, package, and import-boundary gates | DETERMINISTIC | 1 | IMPLEMENTED |
| Approved external gate adapters | DETERMINISTIC | 2 | IMPLEMENTED |
| Changed-file gate coverage proof | DETERMINISTIC | 2 | IMPLEMENTED |
| Highest-risk-file gate coverage proof | DETERMINISTIC | 2 | IMPLEMENTED |
| Threshold anti-weakening | DETERMINISTIC | 2 | IMPLEMENTED |
| Gate-scope anti-narrowing | DETERMINISTIC | 2 | IMPLEMENTED |
| Behavior and characterization proof | STRUCTURED_REVIEW_EVIDENCE | 3 | IMPLEMENTED |
| Separation-of-concerns evidence | STRUCTURED_REVIEW_EVIDENCE | 3 | IMPLEMENTED |
| Architecture and dependency-direction review evidence | STRUCTURED_REVIEW_EVIDENCE | 3 | IMPLEMENTED |
| Responsibility-boundary reporting | STRUCTURED_REVIEW_EVIDENCE | 3 | IMPLEMENTED |
| Incremental refactor evidence | STRUCTURED_REVIEW_EVIDENCE | 3 | IMPLEMENTED |
| Review handoff and remaining-risk evidence | STRUCTURED_REVIEW_EVIDENCE | 3 | IMPLEMENTED |
| Naming, cohesion, intended behavior, and reviewability judgment | HUMAN_REVIEW | 3 | IMPLEMENTED |
| Organization required-workflow enforcement proof | DETERMINISTIC | 4 | IMPLEMENTED |
| Temporary target-repository protected merge proof | DETERMINISTIC | 4 | IMPLEMENTED |
| TWMN clean and defect canaries | DETERMINISTIC | 5 | IMPLEMENTED |
| TWMN gate-weakening and scope-narrowing canaries | DETERMINISTIC | 5 | IMPLEMENTED |
| Frontend framework gate execution in Python-only V1 | DETERMINISTIC | 1 | NOT_APPLICABLE_TO_PRODUCT |
