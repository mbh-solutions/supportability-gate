"""Compose one independently owned result for each Supportability Standard."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from supportability_gate import clause_inventory, contract, standard_block_ownership

CHECK_CONTEXTS = (
    "Supportability 1 - Cyclomatic Complexity",
    "Supportability 2 - Separation of Concerns",
    "Supportability 3 - Dependency Direction",
    "Supportability 4 - Domain Modularity",
    "Supportability 5 - Characterization",
    "Supportability 6 - Incremental Refactor",
    "Supportability 7 - Quality Gates",
    "Supportability 8 - Review Handoff",
)


class StandardResultsError(ValueError):
    """One fail-closed standard-result contract error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RunIdentity:
    """Exact producer and pull-request identity."""

    repository: str
    repository_id: int
    base_sha: str
    head_sha: str
    workflow_sha: str
    run_id: int
    run_attempt: int


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _string_list(value: object, code: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StandardResultsError(code)
    if len(value) != len(set(value)):
        raise StandardResultsError(code)
    return value


# Deterministic S02 result contract.
SCHEMA_VERSION = "standard-results.v2"
RESULTS = frozenset({"PASS", "BLOCK", "TECHNICAL_FAILURE", "NOT_APPLICABLE_SHORT_TASK"})
SOURCE_OUTCOMES = frozenset({"success", "failure", "cancelled", "skipped"})
SOURCE_KEYS = ("install", "complexity", "characterization", "refactor", "quality")
EVIDENCE_SOURCES = (
    (
        "complexity-result.json:functions",
        "complexity-result.json:ruff_diagnostics",
        "complexity-result.json:review_evidence.human_review",
    ),
    ("complexity-result.json:review_evidence.separation_of_concerns",),
    (
        "complexity-result.json:architecture",
        "complexity-result.json:dependency_direction_explanation",
        "complexity-result.json:review_evidence.architecture",
    ),
    (
        "complexity-result.json:modularity",
        "complexity-result.json:review_evidence.module_boundaries",
        "complexity-result.json:review_evidence.responsibility_boundary",
    ),
    (
        "characterization-result.json",
        "complexity-result.json:review_evidence.behavior",
        "complexity-result.json:review_evidence.characterization",
    ),
    (
        "refactor-policy-result.json",
        "complexity-result.json:review_evidence.incremental_refactor",
    ),
    (
        "complexity-result.json:changed_files",
        "complexity-result.json:gate_coverage",
        "complexity-result.json:quality_profile",
        "quality-provenance.json",
    ),
    (
        "complexity-result.json:changed_files",
        "complexity-result.json:review_evidence.review_handoff",
    ),
)
_S02_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_S02_SHA64 = re.compile(r"[0-9a-f]{64}\Z")
_S02_SHORT_EXCLUSIONS = {
    "docs/fixed_roadmap.md",
    "docs/product_completion_contract.md",
    "docs/supportability_standard.md",
}
_S02_SOURCE_CODES = {
    "gate_install": {"GATE_INSTALL_FAILURE"},
    "complexity": {"MISSING_COMPLEXITY_RESULT", "MALFORMED_COMPLEXITY_RESULT"},
    "characterization": {
        "MISSING_CHARACTERIZATION_RESULT",
        "MALFORMED_CHARACTERIZATION_RESULT",
    },
    "refactor": {"MISSING_REFACTOR_RESULT", "MALFORMED_REFACTOR_RESULT"},
    "quality_provenance": {
        "MISSING_QUALITY_PROVENANCE",
        "MALFORMED_QUALITY_PROVENANCE",
    },
}
_S02_COMPLEXITY_KEYS = {
    "architecture",
    "base_contract_blob_sha",
    "base_sha",
    "base_tree_sha",
    "changed_files",
    "commands",
    "contract_path",
    "contract_sha256",
    "dependency_direction_explanation",
    "functions",
    "gate_coverage",
    "head_sha",
    "head_tree_sha",
    "high_risk_paths",
    "language",
    "modularity",
    "overall_result",
    "policy_blocks",
    "production_paths",
    "quality_profile",
    "rename_bindings",
    "repository_remote",
    "review_evidence",
    "review_evidence_path",
    "ruff_diagnostics",
    "schema_version",
    "standard_sha256",
    "technical_errors",
    "tool_versions",
    "touched_qualified_functions",
}
_S02_CHANGED_KEYS = {
    "base_production",
    "changed_head_lines",
    "complexity_assessed",
    "head_production",
    "new_path",
    "old_path",
    "status",
}
_S02_CHARACTERIZATION_KEYS = {
    "artifacts",
    "base_sha",
    "behavior_fingerprint",
    "coverage",
    "head_sha",
    "manifest_blob_sha",
    "manifest_sha256",
    "overall_result",
    "policy_blocks",
    "repository",
    "scenarios",
    "schema_version",
    "workflow_sha",
}
_S02_REFACTOR_KEYS = {
    "applicable",
    "authorization",
    "authorization_comment_id",
    "base_sha",
    "characterization_sha256",
    "changed_paths",
    "head_sha",
    "other_standard_clauses_waived",
    "overall_result",
    "policy_blocks",
    "repository",
    "schema_version",
    "targets",
    "unbounded_paths",
}
_S02_QUALITY_KEYS = {
    "artifact_digest",
    "artifact_id",
    "capture_sha256",
    "commands",
    "job",
    "repository",
    "repository_id",
    "run_attempt",
    "run_id",
    "runner_environment",
}
_S02_PROFILE_KEYS = {
    "base_sha",
    "changed_paths",
    "commands",
    "exclusions",
    "head_sha",
    "high_risk_paths",
    "language",
    "maximum_complexity",
    "production_files",
    "production_paths",
    "repository_remote",
    "schema_version",
    "workflow_sha",
}

_S02_FUNCTION_KEYS = {
    "base",
    "decision",
    "ending_complexity",
    "head",
    "next_target",
    "remaining_debt",
    "remaining_gap",
    "starting_complexity",
    "state",
}
_S02_METRIC_KEYS = {"complexity", "end_line", "path", "qualified_name", "start_line"}
_S02_EDGE_KEYS = {"internal", "line", "source", "specifier", "target"}
_S02_REVIEW_SECTIONS = {
    "architecture": ({"dependency_direction"}, {"reviewed_paths"}),
    "behavior": ({"intended_behavior", "proof"}, set()),
    "characterization": ({"captured_behavior", "proof"}, set()),
    "human_review": (
        {"cohesion", "intended_behavior", "naming", "reviewability"},
        set(),
    ),
    "incremental_refactor": ({"completed_step", "target"}, set()),
    "responsibility_boundary": ({"does_not_own", "owns", "path"}, set()),
    "review_handoff": ({"summary"}, {"remaining_risks"}),
    "separation_of_concerns": ({"after", "before"}, set()),
}


@dataclass(frozen=True)
class _S02Complexity:
    blocks: tuple[str, ...]
    technical: tuple[str, ...]
    changed_files: tuple[dict[str, Any], ...]
    quality_adapters: tuple[str, ...]
    quality_result: str | None
    result: str
    source_sha256: str


class _S02State:
    def __init__(self, applicable: frozenset[int]) -> None:
        self.applicable = applicable
        self.blocks: dict[int, set[str]] = {standard: set() for standard in range(1, 9)}
        self.errors: dict[int, set[str]] = {standard: set() for standard in range(1, 9)}
        self.shared: set[tuple[str, str, str, tuple[int, ...]]] = set()

    def policy(self, code: str, dependency: str, affected: frozenset[int]) -> None:
        active = affected & self.applicable
        for standard in active:
            self.blocks[standard].add(code)
        if len(active) > 1:
            self.shared.add(("POLICY_BLOCK", code, dependency, tuple(sorted(active))))

    def technical(self, code: str, dependency: str, affected: frozenset[int]) -> None:
        for standard in affected:
            self.errors[standard].add(code)
        if len(affected) > 1:
            self.shared.add(("TECHNICAL_ERROR", code, dependency, tuple(sorted(affected))))


def _s02_exact(value: object, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise StandardResultsError(code)
    return value


def _s02_sha(value: object, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _s02_identity(identity: RunIdentity) -> None:
    if (
        not isinstance(identity.repository, str)
        or identity.repository.count("/") != 1
        or any(not part for part in identity.repository.split("/"))
        or type(identity.repository_id) is not int
        or identity.repository_id < 1
        or not _s02_sha(identity.base_sha, _S02_SHA40)
        or not _s02_sha(identity.head_sha, _S02_SHA40)
        or not _s02_sha(identity.workflow_sha, _S02_SHA40)
        or type(identity.run_id) is not int
        or identity.run_id < 1
        or type(identity.run_attempt) is not int
        or identity.run_attempt < 1
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_IDENTITY")


def _s02_dict_list(value: object, code: str, required: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise StandardResultsError(code)
    if required and not value:
        raise StandardResultsError(code)
    return value


def _s02_strings(value: object, code: str, required: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or (required and not value)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise StandardResultsError(code)
    return value


def _s02_rows(
    value: object, keys: set[str], code: str, required: bool = False
) -> list[dict[str, Any]]:
    rows = _s02_dict_list(value, code, required)
    if any(set(row) != keys for row in rows):
        raise StandardResultsError(code)
    return rows


def _s02_changed(value: object, code: str, required: bool = False) -> tuple[dict[str, Any], ...]:
    rows = _s02_dict_list(value, code, required)
    for row in rows:
        lines = row.get("changed_head_lines")
        if (
            set(row) != _S02_CHANGED_KEYS
            or row.get("status") not in {"ADDED", "DELETED", "MODIFIED", "RENAMED"}
            or any(
                path is not None and not isinstance(path, str)
                for path in (row.get("old_path"), row.get("new_path"))
            )
            or any(
                type(row.get(name)) is not bool
                for name in ("base_production", "complexity_assessed", "head_production")
            )
            or not isinstance(lines, list)
            or any(type(line) is not int or line < 1 for line in lines)
        ):
            raise StandardResultsError(code)
    return tuple(rows)


def _s02_short(changed: tuple[dict[str, Any], ...]) -> bool:
    if len(changed) != 1:
        return False
    row = changed[0]
    path = row["new_path"]
    allowed = path == "README.md" or bool(
        isinstance(path, str) and path.startswith("docs/") and path.endswith(".md")
    )
    return bool(
        row["status"] == "ADDED"
        and row["old_path"] is None
        and row["base_production"] is False
        and row["head_production"] is False
        and allowed
        and path not in _S02_SHORT_EXCLUSIONS
        and len(row["changed_head_lines"]) == 1
    )


def _s02_commands(value: object, code: str, required: bool) -> None:
    keys = {"arguments", "exit_code", "stderr_sha256", "stdout_sha256", "tool"}
    rows = _s02_rows(value, keys, code, required)
    for row in rows:
        if (
            row["tool"] not in {"git", "ruff"}
            or not isinstance(row["arguments"], list)
            or any(not isinstance(item, str) for item in row["arguments"])
            or type(row["exit_code"]) is not int
            or not _s02_sha(row["stderr_sha256"], _S02_SHA64)
            or not _s02_sha(row["stdout_sha256"], _S02_SHA64)
        ):
            raise StandardResultsError(code)


def _s02_profile_command(row: dict[str, Any], code: str) -> str:
    adapter = row["adapter"]
    if (
        not isinstance(adapter, str)
        or not adapter
        or not isinstance(row["proof_kind"], str)
        or not row["proof_kind"]
        or type(row["executed"]) is not bool
        or type(row["exit_code"]) is not int
    ):
        raise StandardResultsError(code)
    _s02_strings(row["arguments"], code, True)
    _s02_strings(row["observed_paths"], code)
    _s02_strings(row["zero_statement_paths"], code)
    return adapter


def _s02_profile(
    value: object, identity: RunIdentity, language: str, code: str
) -> tuple[tuple[str, ...], str, tuple[str, ...]]:
    row = _s02_exact(value, _S02_PROFILE_KEYS, code)
    actual = tuple(
        row[name] for name in ("base_sha", "head_sha", "repository_remote", "workflow_sha")
    )
    expected = (
        identity.base_sha,
        identity.head_sha,
        f"github.com/{identity.repository}",
        identity.workflow_sha,
    )
    if row["language"] != language:
        raise StandardResultsError(code)
    command_keys = {
        "adapter",
        "arguments",
        "executed",
        "exit_code",
        "observed_paths",
        "proof_kind",
        "zero_statement_paths",
    }
    commands = _s02_rows(row["commands"], command_keys, code, True)
    adapters = tuple(_s02_profile_command(command, code) for command in commands)
    if (
        actual != expected
        or row["schema_version"] != "quality-gates.v3"
        or row["maximum_complexity"] != 10
        or len(adapters) != len(set(adapters))
    ):
        raise StandardResultsError(code)
    for name in (
        "changed_paths",
        "exclusions",
        "high_risk_paths",
        "production_files",
        "production_paths",
    ):
        _s02_strings(row[name], code)
    result = (
        "BLOCK"
        if any(
            contract.command_failed(
                row["language"],
                command["adapter"],
                command["executed"],
                command["exit_code"],
            )
            for command in commands
        )
        else "PASS"
    )
    failed = tuple(
        command["adapter"]
        for command in commands
        if command["executed"]
        and contract.command_failed(
            row["language"],
            command["adapter"],
            command["executed"],
            command["exit_code"],
        )
    )
    return adapters, result, failed


def _s02_review_section(
    value: object, text_fields: set[str], list_fields: set[str], code: str
) -> None:
    row = _s02_exact(value, text_fields | list_fields, code)
    if any(not isinstance(row[name], str) or not row[name].strip() for name in text_fields):
        raise StandardResultsError(code)
    for name in list_fields:
        values = _s02_strings(row[name], code, True)
        if any(not item.strip() for item in values):
            raise StandardResultsError(code)


def _s02_review_boundaries(value: object, code: str) -> None:
    keys = {"basis", "justification", "owner_path", "path"}
    rows = _s02_rows(value, keys, code)
    paths: list[str] = []
    for row in rows:
        if row["basis"] not in {"domain", "responsibility"} or any(
            not isinstance(row[name], str) or not row[name].strip() for name in keys
        ):
            raise StandardResultsError(code)
        paths.append(row["path"])
    if len(paths) != len(set(paths)):
        raise StandardResultsError(code)


def _s02_separation_boundaries(value: object, code: str) -> None:
    rows = _s02_rows(value, {"after", "before", "kind", "path", "symbol"}, code)
    identities: set[tuple[str, str, str]] = set()
    for row in rows:
        if (
            row["kind"] not in {"function", "component", "module"}
            or any(not isinstance(row[name], str) or not row[name].strip() for name in row)
        ):
            raise StandardResultsError(code)
        identity = (row["path"], row["kind"], row["symbol"])
        if identity in identities:
            raise StandardResultsError(code)
        identities.add(identity)


def _s02_review(value: object, blocks: list[str], code: str) -> None:
    owners = frozenset().union(*(standard_block_ownership.review_owners(block) for block in blocks))
    if value is None:
        if owners:
            return
        raise StandardResultsError(code)
    if owners:
        raise StandardResultsError(code)
    keys = {"module_boundaries", "schema_version", *_S02_REVIEW_SECTIONS}
    row = _s02_exact(value, keys, code)
    if row["schema_version"] != "1.0":
        raise StandardResultsError(code)
    for name, (text_fields, list_fields) in _S02_REVIEW_SECTIONS.items():
        if name == "separation_of_concerns":
            separation = _s02_exact(row[name], text_fields | {"boundaries"}, code)
            _s02_review_section(separation, text_fields, list_fields, code)
            _s02_separation_boundaries(separation["boundaries"], code)
        else:
            _s02_review_section(row[name], text_fields, list_fields, code)
    _s02_review_boundaries(row["module_boundaries"], code)


def _s02_metric(value: object, code: str) -> dict[str, Any] | None:
    if value is None:
        return None
    row = _s02_exact(value, _S02_METRIC_KEYS, code)
    if (
        any(
            type(row[name]) is not int or row[name] < 1
            for name in ("complexity", "start_line", "end_line")
        )
        or row["end_line"] < row["start_line"]
        or any(
            not isinstance(row[name], str) or not row[name] for name in ("path", "qualified_name")
        )
    ):
        raise StandardResultsError(code)
    return row


def _s02_function_expected(
    base: dict[str, Any] | None, head: dict[str, Any] | None
) -> tuple[str, str, int | None, int | None]:
    if head is None:
        return "DELETED", "DELETED", None, None
    if base is None:
        return "NEW", "PASS" if head["complexity"] <= 10 else "BLOCK", None, None
    if base["complexity"] <= 10:
        return "EXISTING", "PASS" if head["complexity"] <= 10 else "BLOCK", None, None
    debt = max(0, head["complexity"] - 10)
    if head["complexity"] <= 10:
        return "EXISTING_LEGACY", "PASS", debt, 10
    decision = "PASS_PROGRESSIVE" if head["complexity"] < base["complexity"] else "BLOCK"
    return "EXISTING_LEGACY", decision, debt, 10


def _s02_function_blocks(value: object, code: str) -> list[str]:
    rows = _s02_rows(value, _S02_FUNCTION_KEYS, code)
    blocks: list[str] = []
    for row in rows:
        base = _s02_metric(row["base"], code)
        head = _s02_metric(row["head"], code)
        if base is None and head is None:
            raise StandardResultsError(code)
        expected = _s02_function_expected(base, head)
        actual = (row["state"], row["decision"], row["remaining_debt"], row["next_target"])
        if (
            actual != expected
            or row["remaining_gap"] != row["remaining_debt"]
            or row["starting_complexity"] != (base["complexity"] if base else None)
            or row["ending_complexity"] != (head["complexity"] if head else None)
        ):
            raise StandardResultsError(code)
        if row["decision"] == "BLOCK":
            metric = head if head is not None else base
            if metric is None:
                raise StandardResultsError(code)
            blocks.append(f"FUNCTION_COMPLEXITY:{metric['qualified_name']}")
    return blocks


def _s02_edges(value: object, code: str) -> None:
    for row in _s02_rows(value, _S02_EDGE_KEYS, code):
        if (
            any(
                not isinstance(row[name], str) or not row[name]
                for name in ("source", "specifier", "target")
            )
            or type(row["line"]) is not int
            or row["line"] < 1
            or type(row["internal"]) is not bool
        ):
            raise StandardResultsError(code)


def _s02_architecture(value: object, policy_blocks: list[str], code: str) -> None:
    keys = {"adapter", "blocks", "covered_paths", "edges", "executed", "nodes"}
    row = _s02_exact(value, keys, code)
    blocks = _string_list(row["blocks"], code)
    if (
        not isinstance(row["adapter"], str)
        or not row["adapter"]
        or type(row["executed"]) is not bool
        or not set(blocks).issubset(policy_blocks)
    ):
        raise StandardResultsError(code)
    _s02_strings(row["covered_paths"], code)
    _s02_strings(row["nodes"], code)
    _s02_edges(row["edges"], code)


def _s02_modularity(value: object, policy_blocks: list[str], code: str) -> None:
    keys = {"blocks", "changed_paths", "coupling_edges", "coverage", "justifications", "new_paths"}
    row = _s02_exact(value, keys, code)
    blocks = _string_list(row["blocks"], code)
    if not set(blocks).issubset(policy_blocks):
        raise StandardResultsError(code)
    _s02_strings(row["changed_paths"], code)
    _s02_strings(row["new_paths"], code)
    _s02_edges(row["coupling_edges"], code)
    for coverage in _s02_rows(row["coverage"], {"adapters", "architecture", "path"}, code):
        if not isinstance(coverage["path"], str) or type(coverage["architecture"]) is not bool:
            raise StandardResultsError(code)
        _s02_strings(coverage["adapters"], code)
    justification_keys = {"basis", "justification", "owner_path", "path"}
    for item in _s02_rows(row["justifications"], justification_keys, code):
        if item["basis"] not in {"domain", "responsibility"} or any(
            not isinstance(item[name], str) or not item[name] for name in justification_keys
        ):
            raise StandardResultsError(code)


def _s02_complexity_lists(row: dict[str, Any], code: str) -> None:
    for name in ("high_risk_paths", "production_paths", "touched_qualified_functions"):
        _s02_strings(row[name], code)
    if not isinstance(row["tool_versions"], dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in row["tool_versions"].items()
    ):
        raise StandardResultsError(code)
    for item in _s02_rows(row["rename_bindings"], {"new_path", "old_path"}, code):
        if any(not isinstance(item[name], str) or not item[name] for name in item):
            raise StandardResultsError(code)


def _s02_ruff(value: object, code: str) -> list[dict[str, Any]]:
    keys = {"code", "complexity", "line", "message", "path", "qualified_name"}
    rows = _s02_rows(value, keys, code)
    for row in rows:
        if any(
            not isinstance(row[name], str) or not row[name]
            for name in ("code", "message", "path", "qualified_name")
        ) or any(type(row[name]) is not int or row[name] < 1 for name in ("complexity", "line")):
            raise StandardResultsError(code)
    return rows


def _s02_change_bindings(
    row: dict[str, Any],
    changed: tuple[dict[str, Any], ...],
    code: str,
) -> tuple[set[str], dict[str, set[int]], set[tuple[str, str]]]:
    rename_rows = _s02_rows(row["rename_bindings"], {"new_path", "old_path"}, code)
    renames = {(item["old_path"], item["new_path"]) for item in rename_rows}
    expected = {
        (item["old_path"], item["new_path"]) for item in changed if item["status"] == "RENAMED"
    }
    if len(renames) != len(rename_rows) or renames != expected:
        raise StandardResultsError(code)
    base_paths = {
        item["old_path"] for item in changed if item["base_production"] and item["old_path"]
    }
    head_lines = {
        item["new_path"]: set(item["changed_head_lines"])
        for item in changed
        if item["head_production"] and item["new_path"]
    }
    return base_paths, head_lines, renames


def _s02_function_bindings(
    value: object,
    base_paths: set[str],
    head_lines: dict[str, set[int]],
    renames: set[tuple[str, str]],
    touched: object,
    language: str,
    code: str,
) -> tuple[dict[str, Any], ...]:
    suffixes = (".py", ".pyi") if language == "python" else (".cts", ".mts", ".ts", ".tsx")
    base_identities: set[tuple[str, str]] = set()
    head_identities: set[tuple[str, str]] = set()
    heads: list[dict[str, Any]] = []
    for row in _s02_rows(value, _S02_FUNCTION_KEYS, code):
        base = _s02_metric(row["base"], code)
        head = _s02_metric(row["head"], code)
        if base is not None:
            identity = (base["path"], base["qualified_name"])
            if (
                base["path"] not in base_paths
                or not base["path"].endswith(suffixes)
                or identity in base_identities
            ):
                raise StandardResultsError(code)
            base_identities.add(identity)
        if head is not None:
            identity = (head["path"], head["qualified_name"])
            changed_lines = head_lines.get(head["path"])
            if (
                changed_lines is None
                or not head["path"].endswith(suffixes)
                or not changed_lines.intersection(range(head["start_line"], head["end_line"] + 1))
                or identity in head_identities
            ):
                raise StandardResultsError(code)
            head_identities.add(identity)
            heads.append(head)
        if (
            base is not None
            and head is not None
            and (
                base["qualified_name"] != head["qualified_name"]
                or (base["path"] != head["path"] and (base["path"], head["path"]) not in renames)
            )
        ):
            raise StandardResultsError(code)
    if touched != sorted(item["qualified_name"] for item in heads):
        raise StandardResultsError(code)
    return tuple(heads)


def _s02_ruff_bindings(
    rows: list[dict[str, Any]],
    language: str,
    heads: tuple[dict[str, Any], ...],
    head_paths: set[str],
    code: str,
) -> None:
    if language == "typescript":
        if rows:
            raise StandardResultsError(code)
        return
    touched = {(item["path"], item["qualified_name"]): item for item in heads}
    matched: dict[tuple[str, str], int] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        identity = (row["path"], row["qualified_name"])
        metric = touched.get(identity)
        message = re.search(r"\((\d+) > 10\)$", row["message"])
        if (
            row["code"] != "C901"
            or row["path"] not in head_paths
            or identity in seen
            or metric is None
            or not metric["start_line"] <= row["line"] <= metric["end_line"]
            or message is None
            or int(message.group(1)) != row["complexity"]
        ):
            raise StandardResultsError(code)
        seen.add(identity)
        matched[identity] = row["complexity"]
    required = {
        identity: metric["complexity"]
        for identity, metric in touched.items()
        if metric["complexity"] > 10
    }
    if matched != required:
        raise StandardResultsError(code)


def _s02_gate_coverage(value: object, language: str, code: str, required: bool) -> None:
    rows = _s02_rows(value, {"adapter", "paths"}, code, required)
    adapters: list[str] = []
    for row in rows:
        if not isinstance(row["adapter"], str) or not row["adapter"]:
            raise StandardResultsError(code)
        adapters.append(row["adapter"])
        _s02_strings(row["paths"], code, True)
    expected = contract.COMPLEXITY_ADAPTERS[language]
    other = set(contract.COMPLEXITY_ADAPTERS.values()) - {expected}
    if (
        len(adapters) != len(set(adapters))
        or any(adapter in other for adapter in adapters)
        or (required and expected not in adapters)
    ):
        raise StandardResultsError(code)


def _s02_complexity_technical_standards(technical: list[str]) -> frozenset[int]:
    return frozenset().union(
        *(
            standard_block_ownership.technical_owners(f"COMPLEXITY_RESULT:{item}")
            or standard_block_ownership.COMPLEXITY_TECHNICAL_STANDARDS
            for item in technical
        )
    )


def _s02_complexity_components(
    row: dict[str, Any],
    blocks: list[str],
    technical: list[str],
    identity: RunIdentity,
    code: str,
) -> tuple[tuple[str, ...], str | None]:
    affected = _s02_complexity_technical_standards(technical)
    review_owners = frozenset().union(
        *(standard_block_ownership.review_owners(block) for block in blocks)
    )
    if (
        (row["architecture"] is None and 3 not in affected)
        or (row["modularity"] is None and 4 not in affected)
        or (row["quality_profile"] is None and 7 not in affected)
        or (
            row["review_evidence"] is None
            and not review_owners
            and not standard_block_ownership.REVIEW_STANDARDS.issubset(affected)
        )
    ):
        raise StandardResultsError(code)
    if row["architecture"] is not None:
        _s02_architecture(row["architecture"], blocks, code)
    if row["modularity"] is not None:
        _s02_modularity(row["modularity"], blocks, code)
    if row["review_evidence"] is not None or review_owners:
        _s02_review(row["review_evidence"], blocks, code)
    profile = (
        _s02_profile(row["quality_profile"], identity, row["language"], code)
        if row["quality_profile"] is not None
        else ((), None, ())
    )
    failed = {
        block.removeprefix("QUALITY_GATE_FAILED:")
        for block in blocks
        if block.startswith("QUALITY_GATE_FAILED:")
    }
    if failed != set(profile[2]):
        raise StandardResultsError(code)
    if profile[1] == "BLOCK" and not any(
        7 in standard_block_ownership.owners(block) for block in blocks
    ):
        raise StandardResultsError(code)
    return profile[0], profile[1]


def _s02_complexity(value: object, identity: RunIdentity) -> _S02Complexity:
    code = "MALFORMED_COMPLEXITY_RESULT"
    row = _s02_exact(value, _S02_COMPLEXITY_KEYS, code)
    actual = (row["base_sha"], row["head_sha"], row["repository_remote"])
    expected = (identity.base_sha, identity.head_sha, f"github.com/{identity.repository}")
    if actual != expected:
        raise StandardResultsError("COMPLEXITY_RESULT_BINDING_MISMATCH")
    if row["schema_version"] != "1.0" or row["standard_sha256"] != clause_inventory.STANDARD_SHA256:
        raise StandardResultsError(code)
    blocks = _string_list(row["policy_blocks"], code)
    function_blocks = _s02_function_blocks(row["functions"], code)
    technical_rows = _s02_rows(row["technical_errors"], {"code", "message"}, code)
    technical = [item["code"] for item in technical_rows]
    if any(
        not isinstance(item["code"], str)
        or not item["code"]
        or not isinstance(item["message"], str)
        or not item["message"]
        for item in technical_rows
    ):
        raise StandardResultsError(code)
    affected = _s02_complexity_technical_standards(technical)
    common_required = not technical or affected != standard_block_ownership.ALL_STANDARDS
    changed = _s02_changed(row["changed_files"], code, common_required)
    result = "TECHNICAL_FAILURE" if technical else "BLOCK" if blocks or function_blocks else "PASS"
    if row["overall_result"] != result:
        raise StandardResultsError(code)
    if (
        not _s02_sha(row["base_contract_blob_sha"], _S02_SHA40)
        or not _s02_sha(row["base_tree_sha"], _S02_SHA40)
        or not _s02_sha(row["head_tree_sha"], _S02_SHA40)
        or not _s02_sha(row["contract_sha256"], _S02_SHA64)
        or row["review_evidence_path"] != ".supportability-review.toml"
        or row["language"] not in {"python", "typescript"}
        or any(
            not isinstance(row[name], str) or not row[name]
            for name in ("contract_path", "dependency_direction_explanation")
        )
    ):
        raise StandardResultsError(code)
    _s02_complexity_lists(row, code)
    base_paths, head_lines, renames = _s02_change_bindings(row, changed, code)
    heads = _s02_function_bindings(
        row["functions"],
        base_paths,
        head_lines,
        renames,
        row["touched_qualified_functions"],
        row["language"],
        code,
    )
    ruff = _s02_ruff(row["ruff_diagnostics"], code)
    _s02_ruff_bindings(ruff, row["language"], heads, set(head_lines), code)
    _s02_commands(row["commands"], code, True)
    _s02_gate_coverage(row["gate_coverage"], row["language"], code, common_required)
    quality_adapters, quality_result = _s02_complexity_components(
        row, blocks, technical, identity, code
    )
    return _S02Complexity(
        tuple((*blocks, *function_blocks)),
        tuple(technical),
        changed,
        quality_adapters,
        quality_result,
        result,
        hashlib.sha256(_canonical(row)).hexdigest(),
    )


def _s02_apply_block(
    state: _S02State,
    block: str,
    source: str,
) -> None:
    owners = standard_block_ownership.owners(block)
    shared = standard_block_ownership.shared_dependency(block)
    if not owners:
        state.technical(
            f"UNKNOWN_STANDARD_BLOCK_OWNER:{block}",
            "standard-block-ownership",
            standard_block_ownership.ALL_STANDARDS,
        )
    elif len(owners) > 1 and shared is None:
        state.technical(
            f"AMBIGUOUS_STANDARD_BLOCK_OWNER:{block}",
            "standard-block-ownership",
            standard_block_ownership.ALL_STANDARDS,
        )
    elif expected := standard_block_ownership.expected_technical_dependency(
        f"STANDARD_BLOCK_SOURCE_MISMATCH:{block}", f"{source}:policy-blocks"
    ):
        state.technical(f"STANDARD_BLOCK_SOURCE_MISMATCH:{block}", expected[0], expected[1])
    elif shared:
        state.policy(block, shared[0], shared[1])
    else:
        state.policy(block, f"{source}:policy-blocks", owners)


def _s02_characterization_artifact(value: object, passing: bool, code: str) -> None:
    row = _s02_exact(value, {"capture_sha256", "digest", "id"}, code)
    capture = row["capture_sha256"]
    if (
        not isinstance(row["id"], str)
        or not row["id"].isdecimal()
        or int(row["id"]) < 1
        or not _s02_sha(row["digest"], _S02_SHA64)
        or (capture is not None and not _s02_sha(capture, _S02_SHA64))
        or (passing and capture is None)
    ):
        raise StandardResultsError(code)


def _s02_characterization_scenario(value: object, passing: bool, code: str) -> None:
    keys = {
        "base_behavior_sha256",
        "command",
        "compatibility",
        "covers",
        "golden_behavior_sha256",
        "head_behavior_sha256",
        "id",
        "kind",
    }
    row = _s02_exact(value, keys, code)
    hashes = tuple(row[name] for name in keys if name.endswith("_sha256"))
    command = row["command"]
    if (
        not isinstance(row["id"], str)
        or not row["id"]
        or row["kind"] not in {"test", "sample_io", "snapshot", "golden", "cli", "regression"}
        or row["compatibility"] not in {"PASS", "BLOCK"}
        or any(item is not None and not _s02_sha(item, _S02_SHA64) for item in hashes)
        or (command is not None and not isinstance(command, list))
    ):
        raise StandardResultsError(code)
    _s02_strings(row["covers"], code, True)
    if command is not None:
        _s02_strings(command, code, True)
    if passing and (
        row["compatibility"] != "PASS" or command is None or any(item is None for item in hashes)
    ):
        raise StandardResultsError(code)


def _s02_characterization(value: object, identity: RunIdentity) -> list[str]:
    code = "MALFORMED_CHARACTERIZATION_RESULT"
    row = _s02_exact(value, _S02_CHARACTERIZATION_KEYS, code)
    actual = tuple(row[name] for name in ("repository", "base_sha", "head_sha", "workflow_sha"))
    expected = (
        f"github.com/{identity.repository}",
        identity.base_sha,
        identity.head_sha,
        identity.workflow_sha,
    )
    if actual != expected:
        raise StandardResultsError("CHARACTERIZATION_RESULT_BINDING_MISMATCH")
    blocks = _string_list(row["policy_blocks"], code)
    if row["schema_version"] != "characterization-result.v1":
        raise StandardResultsError(code)
    if row["overall_result"] != ("BLOCK" if blocks else "PASS"):
        raise StandardResultsError(code)
    passing = not blocks
    if (
        not _s02_sha(row["behavior_fingerprint"], _S02_SHA64)
        or not _s02_sha(row["manifest_blob_sha"], _S02_SHA40)
        or not _s02_sha(row["manifest_sha256"], _S02_SHA64)
    ):
        raise StandardResultsError(code)
    artifacts = _s02_exact(row["artifacts"], {"base", "head"}, code)
    for side in ("base", "head"):
        _s02_characterization_artifact(artifacts[side], passing, code)
    coverage = _s02_exact(row["coverage"], {"covered_paths", "required_paths"}, code)
    _s02_strings(coverage["covered_paths"], code)
    _s02_strings(coverage["required_paths"], code)
    for scenario in _s02_dict_list(row["scenarios"], code, True):
        _s02_characterization_scenario(scenario, passing, code)
    return blocks


def _s02_refactor_authorization(value: object, comment_id: object, code: str) -> None:
    if value is None:
        if comment_id is not None:
            raise StandardResultsError(code)
        return
    keys = {"base_sha", "broad", "head_sha", "repository", "scope", "sequence", "targets"}
    row = _s02_exact(value, keys, code)
    sequence = _s02_exact(row["sequence"], {"predecessor_sha", "step"}, code)
    if (
        not _s02_sha(row["base_sha"], _S02_SHA40)
        or not _s02_sha(row["head_sha"], _S02_SHA40)
        or not isinstance(row["repository"], str)
        or type(row["broad"]) is not bool
        or type(comment_id) is not int
        or comment_id < 1
        or not _s02_sha(sequence["predecessor_sha"], _S02_SHA40)
        or type(sequence["step"]) is not int
        or sequence["step"] < 1
    ):
        raise StandardResultsError(code)
    _s02_strings(row["scope"], code, True)
    _s02_strings(row["targets"], code, True)


def _s02_refactor(
    value: object,
    characterization: object,
    identity: RunIdentity,
) -> list[str]:
    code = "MALFORMED_REFACTOR_RESULT"
    row = _s02_exact(value, _S02_REFACTOR_KEYS, code)
    if tuple(row[name] for name in ("repository", "base_sha", "head_sha")) != (
        identity.repository,
        identity.base_sha,
        identity.head_sha,
    ):
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    blocks = _string_list(row["policy_blocks"], code)
    if (
        row["schema_version"] != "refactor-policy-result.v1"
        or type(row["applicable"]) is not bool
        or row["other_standard_clauses_waived"] is not False
        or row["overall_result"] != ("BLOCK" if blocks else "PASS")
        or not _s02_sha(row["characterization_sha256"], _S02_SHA64)
    ):
        raise StandardResultsError(code)
    for name in ("changed_paths", "targets", "unbounded_paths"):
        _s02_strings(row[name], code)
    _s02_refactor_authorization(row["authorization"], row["authorization_comment_id"], code)
    expected = hashlib.sha256(_canonical(characterization)).hexdigest()
    if row["characterization_sha256"] != expected:
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    return blocks


def _s02_provenance_adapters(value: object, code: str) -> tuple[str, ...]:
    keys = {"adapter", "raw_proof_sha256", "stderr_sha256", "stdout_sha256"}
    commands = _s02_rows(value, keys, code, True)
    adapters: list[str] = []
    for command in commands:
        if (
            not isinstance(command["adapter"], str)
            or not command["adapter"]
            or any(
                not _s02_sha(command[name], _S02_SHA64)
                for name in ("raw_proof_sha256", "stderr_sha256", "stdout_sha256")
            )
        ):
            raise StandardResultsError(code)
        adapters.append(command["adapter"])
    if len(adapters) != len(set(adapters)):
        raise StandardResultsError(code)
    return tuple(adapters)


def _s02_quality_artifact(
    provenance: object,
    expected_artifact: object,
    profile_adapters: tuple[str, ...],
    identity: RunIdentity,
) -> dict[str, object]:
    code = "MALFORMED_QUALITY_RESULT_BINDING"
    row = _s02_exact(provenance, _S02_QUALITY_KEYS, code)
    if (
        tuple(row[name] for name in ("repository", "repository_id", "run_id", "run_attempt", "job"))
        != (
            identity.repository,
            str(identity.repository_id),
            str(identity.run_id),
            str(identity.run_attempt),
            "quality-profile",
        )
        or row["runner_environment"] != "github-hosted"
    ):
        raise StandardResultsError("QUALITY_RESULT_BINDING_MISMATCH")
    if (
        not isinstance(row["artifact_id"], str)
        or not row["artifact_id"].isdecimal()
        or int(row["artifact_id"]) < 1
        or not _s02_sha(row["artifact_digest"], _S02_SHA64)
        or not _s02_sha(row["capture_sha256"], _S02_SHA64)
    ):
        raise StandardResultsError(code)
    adapters = _s02_provenance_adapters(row["commands"], code)
    if adapters != profile_adapters:
        raise StandardResultsError(code)
    if expected_artifact is None:
        raise StandardResultsError("MISSING_EXTERNAL_QUALITY_ARTIFACT")
    trusted = _s02_exact(
        expected_artifact,
        {"capture_sha256", "digest", "id"},
        "MALFORMED_EXTERNAL_QUALITY_ARTIFACT",
    )
    if (
        not isinstance(trusted["id"], str)
        or not trusted["id"].isdecimal()
        or int(trusted["id"]) < 1
        or not _s02_sha(trusted["digest"], _S02_SHA64)
        or not _s02_sha(trusted["capture_sha256"], _S02_SHA64)
    ):
        raise StandardResultsError("MALFORMED_EXTERNAL_QUALITY_ARTIFACT")
    if (row["artifact_id"], row["artifact_digest"], row["capture_sha256"]) != (
        trusted["id"],
        trusted["digest"],
        trusted["capture_sha256"],
    ):
        raise StandardResultsError("QUALITY_ARTIFACT_IDENTITY_MISMATCH")
    return {
        "capture_sha256": trusted["capture_sha256"],
        "digest": trusted["digest"],
        "id": int(trusted["id"]),
    }


def _s02_outcomes(value: object) -> dict[str, str]:
    if value is None:
        return dict.fromkeys(SOURCE_KEYS, "success")
    if not isinstance(value, dict):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_SOURCE_OUTCOMES")
    if set(value) != set(SOURCE_KEYS) or any(
        not isinstance(item, str) or item not in SOURCE_OUTCOMES for item in value.values()
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_SOURCE_OUTCOMES")
    return dict(value)


def _s02_outcome_matches(outcome: str, result: str) -> bool:
    return outcome == ("success" if result == "PASS" else "failure")


def _s02_errors(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_SOURCE_ERRORS")
    errors = dict(value)
    if set(errors) - set(_S02_SOURCE_CODES):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_SOURCE_ERRORS")
    if any(code not in _S02_SOURCE_CODES[source] for source, code in errors.items()):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_SOURCE_ERRORS")
    if "gate_install" in errors:
        return {"gate_install": errors["gate_install"]}
    if "complexity" in errors:
        return {"complexity": errors["complexity"]}
    if "characterization" in errors:
        errors.pop("refactor", None)
    return errors


def _s02_source_failure(state: _S02State, source: str, code: str) -> None:
    dependency = {
        "gate_install": "gate-install",
        "complexity": "complexity-result",
        "complexity_technical": "complexity-result:technical-errors",
        "characterization": "characterization-result",
        "refactor": "refactor-policy-result",
        "quality_provenance": "quality-profile:artifact-binding",
    }[source]
    expected = standard_block_ownership.expected_technical_dependency(code, dependency)
    if expected is None:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_SOURCE_ERRORS")
    state.technical(code, expected[0], expected[1])


def _s02_entries(state: _S02State) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for standard, context in enumerate(CHECK_CONTEXTS, start=1):
        applicable = standard in state.applicable
        blocks = sorted(state.blocks[standard])
        errors = sorted(state.errors[standard])
        result = (
            "NOT_APPLICABLE_SHORT_TASK"
            if not applicable
            else "TECHNICAL_FAILURE"
            if errors
            else "BLOCK"
            if blocks
            else "PASS"
        )
        rows.append(
            {
                "applicable": applicable,
                "check_context": context,
                "evidence_sources": list(EVIDENCE_SOURCES[standard - 1]),
                "policy_blocks": blocks,
                "result": result,
                "standard": standard,
                "technical_errors": errors,
            }
        )
    return rows


def _s02_load_complexity(
    value: object,
    identity: RunIdentity,
    errors: dict[str, str],
    outcomes: dict[str, str],
) -> tuple[_S02Complexity | None, str | None]:
    if "gate_install" in errors:
        return None, None
    source_error = errors.get("complexity")
    if source_error is not None:
        return None, source_error
    try:
        data = _s02_complexity(value, identity)
        if not _s02_outcome_matches(outcomes["complexity"], data.result):
            return None, "MALFORMED_COMPLEXITY_RESULT"
        return data, None
    except StandardResultsError as error:
        return None, error.code


def _s02_authenticated_short(data: _S02Complexity | None) -> bool:
    return bool(
        data is not None
        and not data.blocks
        and not data.technical
        and _s02_short(data.changed_files)
    )


def _s02_add_complexity(
    state: _S02State,
    data: _S02Complexity | None,
    source_error: str | None,
    errors: dict[str, str],
) -> None:
    if "gate_install" in errors:
        _s02_source_failure(state, "gate_install", errors["gate_install"])
        return
    if source_error is not None:
        _s02_source_failure(state, "complexity", source_error)
        return
    if data is None:
        return
    for block in data.blocks:
        _s02_apply_block(state, block, "complexity-result")
    for code in data.technical:
        rendered = f"COMPLEXITY_RESULT:{code}"
        _s02_source_failure(state, "complexity_technical", rendered)


def _s02_add_behavior(
    state: _S02State,
    characterization: object,
    refactor: object,
    identity: RunIdentity,
    errors: dict[str, str],
    outcomes: dict[str, str],
    short: bool,
) -> None:
    if "gate_install" in errors or short:
        return
    if not _s02_add_characterization(state, characterization, identity, errors, outcomes):
        return
    _s02_add_refactor(state, refactor, characterization, identity, errors, outcomes)


def _s02_add_characterization(
    state: _S02State,
    characterization: object,
    identity: RunIdentity,
    errors: dict[str, str],
    outcomes: dict[str, str],
) -> bool:
    char_error = errors.get("characterization")
    if char_error is not None:
        _s02_source_failure(state, "characterization", char_error)
        return False
    try:
        blocks = _s02_characterization(characterization, identity)
    except StandardResultsError as error:
        _s02_source_failure(state, "characterization", error.code)
        return False
    if not _s02_outcome_matches(outcomes["characterization"], "BLOCK" if blocks else "PASS"):
        _s02_source_failure(state, "characterization", "MALFORMED_CHARACTERIZATION_RESULT")
        return False
    for block in blocks:
        _s02_apply_block(state, block, "characterization-result")
    return True


def _s02_add_refactor(
    state: _S02State,
    refactor: object,
    characterization: object,
    identity: RunIdentity,
    errors: dict[str, str],
    outcomes: dict[str, str],
) -> None:
    if "refactor" in errors:
        _s02_source_failure(state, "refactor", errors["refactor"])
        return
    try:
        refactor_blocks = _s02_refactor(refactor, characterization, identity)
    except StandardResultsError as error:
        _s02_source_failure(state, "refactor", error.code)
        return
    if not _s02_outcome_matches(outcomes["refactor"], "BLOCK" if refactor_blocks else "PASS"):
        _s02_source_failure(state, "refactor", "MALFORMED_REFACTOR_RESULT")
        return
    for block in refactor_blocks:
        _s02_apply_block(state, block, "refactor-policy-result")


def _s02_add_quality(
    state: _S02State,
    provenance: object,
    expected_artifact: object,
    data: _S02Complexity | None,
    identity: RunIdentity,
    errors: dict[str, str],
    outcomes: dict[str, str],
    complexity_error: str | None,
) -> dict[str, object] | None:
    if "gate_install" in errors or complexity_error is not None:
        return None
    if "quality_provenance" in errors:
        _s02_source_failure(state, "quality_provenance", errors["quality_provenance"])
        return None
    if data is not None and data.quality_result is None and data.technical:
        if not state.errors[7]:
            _s02_source_failure(state, "quality_provenance", "MALFORMED_QUALITY_PROVENANCE")
        return None
    if (
        data is not None
        and data.quality_result is not None
        and not _s02_outcome_matches(outcomes["quality"], data.quality_result)
    ):
        _s02_source_failure(state, "quality_provenance", "MALFORMED_QUALITY_PROVENANCE")
        return None
    try:
        return _s02_quality_artifact(
            provenance,
            expected_artifact,
            data.quality_adapters if data is not None else (),
            identity,
        )
    except StandardResultsError as error:
        _s02_source_failure(state, "quality_provenance", error.code)
        return None


def compose_results(
    complexity: dict[str, Any] | None,
    characterization: dict[str, Any] | None,
    refactor: dict[str, Any] | None,
    quality_provenance: dict[str, Any] | None,
    identity: RunIdentity,
    *,
    expected_quality_artifact: dict[str, object] | None,
    source_outcomes: dict[str, str] | None = None,
    source_errors: dict[str, str] | None = None,
) -> dict[str, object]:
    """Compose eight deterministic rows without target execution or advisory judgment."""
    _s02_identity(identity)
    outcomes = _s02_outcomes(source_outcomes)
    errors = _s02_errors(source_errors)
    if outcomes["install"] != "success":
        errors = {"gate_install": "GATE_INSTALL_FAILURE"}
    data, complexity_error = _s02_load_complexity(complexity, identity, errors, outcomes)
    short = _s02_authenticated_short(data)
    state = _S02State(frozenset({7}) if short else standard_block_ownership.ALL_STANDARDS)
    _s02_add_complexity(state, data, complexity_error, errors)
    _s02_add_behavior(state, characterization, refactor, identity, errors, outcomes, short)
    artifact = _s02_add_quality(
        state,
        quality_provenance,
        expected_quality_artifact,
        data,
        identity,
        errors,
        outcomes,
        complexity_error,
    )
    source_validated = bool(data and not data.technical)
    payload: dict[str, object] = {
        "applicability_evidence": {
            "changed_files": list(data.changed_files) if data else [],
            "classification": "SHORT_TASK" if short else "FULL_PROCESS",
            "source_sha256": data.source_sha256 if data else None,
            "source_validated": source_validated,
        },
        "base_sha": identity.base_sha,
        "entries": _s02_entries(state),
        "head_sha": identity.head_sha,
        "quality_artifact": artifact,
        "repository": identity.repository,
        "repository_id": identity.repository_id,
        "run_attempt": identity.run_attempt,
        "run_id": identity.run_id,
        "schema_version": SCHEMA_VERSION,
        "shared_failures": [
            {
                "affected_standards": list(affected),
                "code": code,
                "dependency": dependency,
                "kind": kind,
            }
            for kind, code, dependency, affected in sorted(state.shared)
        ],
        "short_task": short,
        "source_outcomes": outcomes,
        "standard_sha256": clause_inventory.STANDARD_SHA256,
        "workflow_sha": identity.workflow_sha,
    }
    validate_payload(payload, identity)
    return payload


def _s02_applicability(value: object, short_task: bool) -> bool:
    row = _s02_exact(
        value,
        {"changed_files", "classification", "source_sha256", "source_validated"},
        "MALFORMED_STANDARD_RESULTS_APPLICABILITY",
    )
    changed = _s02_changed(row["changed_files"], "MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    if type(row["source_validated"]) is not bool:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    if row["source_sha256"] is not None and not _s02_sha(row["source_sha256"], _S02_SHA64):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    eligible = bool(row["source_validated"] and _s02_short(changed))
    classification = "SHORT_TASK" if short_task else "FULL_PROCESS"
    if (short_task and not eligible) or row["classification"] != classification:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    if row["source_validated"] and row["source_sha256"] is None:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    return eligible


def _s02_source_uncertain(entries: list[dict[str, Any]]) -> bool:
    if any(row["policy_blocks"] for row in entries):
        return True
    codes = {code for row in entries for code in row["technical_errors"]}
    return any(
        standard_block_ownership.expected_technical_dependency(
            code, "quality-profile:artifact-binding"
        )
        != ("quality-profile:artifact-binding", frozenset({7}))
        for code in codes
    )


def _s02_entry(value: object, standard: int, short_task: bool) -> dict[str, Any]:
    keys = {
        "applicable",
        "check_context",
        "evidence_sources",
        "policy_blocks",
        "result",
        "standard",
        "technical_errors",
    }
    row = _s02_exact(value, keys, "MALFORMED_STANDARD_RESULT_ENTRY")
    if (
        type(row["standard"]) is not int
        or row["standard"] != standard
        or type(row["applicable"]) is not bool
        or row["check_context"] != CHECK_CONTEXTS[standard - 1]
        or row["evidence_sources"] != list(EVIDENCE_SOURCES[standard - 1])
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    blocks = _string_list(row["policy_blocks"], "MALFORMED_STANDARD_RESULT_ENTRY")
    errors = _string_list(row["technical_errors"], "MALFORMED_STANDARD_RESULT_ENTRY")
    expected_applicable = not short_task or standard == 7
    if row["applicable"] is not expected_applicable or (
        not expected_applicable and (blocks or errors)
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    result = (
        "NOT_APPLICABLE_SHORT_TASK"
        if not expected_applicable
        else "TECHNICAL_FAILURE"
        if errors
        else "BLOCK"
        if blocks
        else "PASS"
    )
    if row["result"] != result or row["result"] not in RESULTS:
        raise StandardResultsError("MALFORMED_STANDARD_RESULT_ENTRY")
    return row


def _s02_shared(value: object) -> dict[tuple[str, str], tuple[str, frozenset[int]]]:
    rows = _s02_dict_list(value, "MALFORMED_SHARED_FAILURE")
    shared: dict[tuple[str, str], tuple[str, frozenset[int]]] = {}
    for row in rows:
        if set(row) != {"affected_standards", "code", "dependency", "kind"}:
            raise StandardResultsError("MALFORMED_SHARED_FAILURE")
        affected = row["affected_standards"]
        if (
            row["kind"] not in {"POLICY_BLOCK", "TECHNICAL_ERROR"}
            or not isinstance(row["code"], str)
            or not isinstance(row["dependency"], str)
            or not isinstance(affected, list)
            or affected != sorted(set(affected))
            or len(affected) < 2
            or any(type(item) is not int or item not in range(1, 9) for item in affected)
        ):
            raise StandardResultsError("MALFORMED_SHARED_FAILURE")
        owners = frozenset(affected)
        expected = (
            standard_block_ownership.shared_dependency(row["code"])
            if row["kind"] == "POLICY_BLOCK"
            else standard_block_ownership.expected_technical_dependency(
                row["code"], row["dependency"]
            )
        )
        if row["kind"] == "TECHNICAL_ERROR" and expected is None:
            raise StandardResultsError("MALFORMED_STANDARD_TECHNICAL_OWNERSHIP")
        if expected != (row["dependency"], owners):
            raise StandardResultsError("MALFORMED_SHARED_FAILURE")
        key = (row["kind"], row["code"])
        if key in shared:
            raise StandardResultsError("MALFORMED_SHARED_FAILURE")
        shared[key] = (row["dependency"], owners)
    return shared


def _s02_claims(
    entries: list[dict[str, Any]],
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    policies: dict[str, set[int]] = {}
    technical: dict[str, set[int]] = {}
    for standard, row in enumerate(entries, start=1):
        for code in row["policy_blocks"]:
            policies.setdefault(code, set()).add(standard)
        for code in row["technical_errors"]:
            technical.setdefault(code, set()).add(standard)
    return policies, technical


def _s02_policy_ownership(
    policies: dict[str, set[int]],
    shared: dict[tuple[str, str], tuple[str, frozenset[int]]],
) -> None:
    for code, actual in policies.items():
        intended = standard_block_ownership.shared_dependency(code)
        if intended:
            if shared.get(("POLICY_BLOCK", code)) != intended or frozenset(actual) != intended[1]:
                raise StandardResultsError("MALFORMED_STANDARD_BLOCK_OWNERSHIP")
        elif standard_block_ownership.owners(code) != frozenset(actual):
            raise StandardResultsError("MALFORMED_STANDARD_BLOCK_OWNERSHIP")


def _s02_technical_ownership(
    technical: dict[str, set[int]],
    shared: dict[tuple[str, str], tuple[str, frozenset[int]]],
) -> None:
    for code, actual in technical.items():
        bound = shared.get(("TECHNICAL_ERROR", code))
        expected = standard_block_ownership.expected_technical_dependency(
            code, bound[0] if bound else ""
        )
        if expected is None or expected[1] != frozenset(actual):
            raise StandardResultsError("MALFORMED_STANDARD_TECHNICAL_OWNERSHIP")
        if len(actual) > 1 and bound != expected:
            raise StandardResultsError("MALFORMED_SHARED_FAILURE")
        if len(actual) == 1 and bound is not None:
            raise StandardResultsError("MALFORMED_SHARED_FAILURE")


def _s02_ownership(
    entries: list[dict[str, Any]],
    shared: dict[tuple[str, str], tuple[str, frozenset[int]]],
) -> None:
    policies, technical = _s02_claims(entries)
    _s02_policy_ownership(policies, shared)
    _s02_technical_ownership(technical, shared)
    used = {("POLICY_BLOCK", code) for code in policies} | {
        ("TECHNICAL_ERROR", code) for code in technical
    }
    if set(shared) - used:
        raise StandardResultsError("MALFORMED_SHARED_FAILURE")


def validate_payload(value: object, identity: RunIdentity | None = None) -> None:
    """Validate exact identity, applicability, ownership, and provenance bindings."""
    keys = {
        "applicability_evidence",
        "base_sha",
        "entries",
        "head_sha",
        "quality_artifact",
        "repository",
        "repository_id",
        "run_attempt",
        "run_id",
        "schema_version",
        "shared_failures",
        "short_task",
        "source_outcomes",
        "standard_sha256",
        "workflow_sha",
    }
    row = _s02_exact(value, keys, "MALFORMED_STANDARD_RESULTS_ARTIFACT")
    actual = RunIdentity(
        row["repository"],
        row["repository_id"],
        row["base_sha"],
        row["head_sha"],
        row["workflow_sha"],
        row["run_id"],
        row["run_attempt"],
    )
    _s02_identity(actual)
    if identity is not None and actual != identity:
        raise StandardResultsError("STANDARD_RESULTS_IDENTITY_MISMATCH")
    if (
        row["schema_version"] != SCHEMA_VERSION
        or row["standard_sha256"] != clause_inventory.STANDARD_SHA256
        or type(row["short_task"]) is not bool
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_ARTIFACT")
    outcomes = _s02_outcomes(row["source_outcomes"])
    eligible_short = _s02_applicability(row["applicability_evidence"], row["short_task"])
    if not isinstance(row["entries"], list) or len(row["entries"]) != 8:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_ARTIFACT")
    entries = [
        _s02_entry(item, standard, row["short_task"])
        for standard, item in enumerate(row["entries"], start=1)
    ]
    required_success = {"complexity", "install", "quality"}
    if not row["short_task"]:
        required_success.update({"characterization", "refactor"})
    if all(entry["result"] in {"PASS", "NOT_APPLICABLE_SHORT_TASK"} for entry in entries) and any(
        outcomes[source] != "success" for source in required_success
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_SOURCE_OUTCOMES")
    shared = _s02_shared(row["shared_failures"])
    _s02_ownership(entries, shared)
    if row["short_task"] is not (eligible_short and not _s02_source_uncertain(entries)):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    artifact = row["quality_artifact"]
    if artifact is None:
        if entries[6]["result"] != "TECHNICAL_FAILURE":
            raise StandardResultsError("MALFORMED_STANDARD_RESULTS_ARTIFACT")
    else:
        artifact = _s02_exact(
            artifact,
            {"capture_sha256", "digest", "id"},
            "MALFORMED_STANDARD_RESULTS_ARTIFACT",
        )
        if (
            type(artifact["id"]) is not int
            or artifact["id"] < 1
            or not _s02_sha(artifact["digest"], _S02_SHA64)
            or not _s02_sha(artifact["capture_sha256"], _S02_SHA64)
        ):
            raise StandardResultsError("MALFORMED_STANDARD_RESULTS_ARTIFACT")


def review_required(payload: object) -> bool:
    """Require advisory review only after all non-short deterministic rows pass."""
    validate_payload(payload)
    assert isinstance(payload, dict)
    return not payload["short_task"] and all(row["result"] == "PASS" for row in payload["entries"])
