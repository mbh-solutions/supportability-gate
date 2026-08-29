"""Compose one independently owned result for each Supportability Standard."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supportability_gate import (
    characterization,
    clause_inventory,
    contract,
    modularity_policy,
    quality_profile,
    review_evidence,
    standard_block_ownership,
)

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
SCHEMA_VERSION = "standard-results.v3"
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
        "characterization-result.json:refactor_runnability",
        "complexity-result.json:responsibility_targets",
        "complexity-result.json:unbounded_production_paths",
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
        "complexity-result.json:functions",
        "complexity-result.json:gate_coverage",
        "complexity-result.json:quality_profile",
        "complexity-result.json:review_evidence_binding",
        "complexity-result.json:review_evidence.separation_of_concerns.boundaries",
        "complexity-result.json:review_evidence.review_handoff",
        "characterization-result.json",
        "refactor-policy-result.json",
        "quality-provenance.json",
    ),
)
_HANDOFF_CITATIONS = {
    "change": ("complexity-result.json:changed_files",),
    "coverage": (
        "complexity-result.json:gate_coverage",
        "complexity-result.json:policy_blocks",
        "complexity-result.json:quality_profile",
    ),
    "functions": ("complexity-result.json:functions",),
    "identity": (
        "characterization-result.json",
        "complexity-result.json",
        "quality-provenance.json",
        "refactor-policy-result.json",
        "workflow-run-identity",
    ),
    "responsibilities": (
        "complexity-result.json:responsibility_targets",
        "complexity-result.json:review_evidence.separation_of_concerns",
    ),
    "review_identity": ("complexity-result.json:review_evidence_binding",),
    "validation": (
        "complexity-result.json:quality_profile.commands",
        "quality-provenance.json:commands",
    ),
}
_S02_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_S02_SHA64 = re.compile(r"[0-9a-f]{64}\Z")
_S02_QUALITY_TOKEN = re.compile(r"\$[A-Z_]+")
_S02_QUALITY_TOKENS = frozenset(
    {
        "$LINT_IMPORTS",
        "$NODE",
        "$NPM",
        "$OUTPUT",
        "$PYTHON",
        "$REPOSITORY",
        "$SOURCE_FILES",
        "$TEST_FILES",
        "$TOOLS",
    }
)
_S02_QUALITY_LIST_TOKENS = frozenset({"$SOURCE_FILES", "$TEST_FILES"})
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
    "responsibility_targets",
    "repository_remote",
    "review_evidence",
    "review_evidence_binding",
    "review_evidence_path",
    "ruff_diagnostics",
    "schema_version",
    "standard_sha256",
    "technical_errors",
    "tool_versions",
    "touched_qualified_functions",
    "unbounded_production_paths",
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
    "predecessor",
    "repository",
    "schema_version",
    "targets",
    "unbounded_paths",
}
_S02_REFACTOR_TARGET = re.compile(
    r"(?P<path>.+)::(?:component|function|module):.+:(?P<start>[1-9][0-9]*)-(?P<end>[1-9][0-9]*)\Z"
)
_S02_REFACTOR_AUTHORIZATION_BLOCKS = {
    "AUTHORIZATION_REPOSITORY_MISMATCH",
    "BROAD_AUTHORIZATION_REQUIRED",
    "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
    "INVALID_STRANGLER_SEQUENCE",
    "MALFORMED_OWNER_AUTHORIZATION",
    "MISSING_BOUNDED_PRODUCTION_TARGET",
    "MISSING_OWNER_AUTHORIZATION",
    "STALE_OWNER_AUTHORIZATION",
    "UNAUTHENTICATED_OWNER_AUTHORIZATION",
    "UNFOCUSED_DIFF_SCOPE",
    "UNVERIFIABLE_BOUNDED_TARGET",
}
_S02_REFACTOR_CURRENT_AUTHORIZATION_BLOCKS = {
    "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE",
    "MALFORMED_OWNER_AUTHORIZATION",
    "MISSING_OWNER_AUTHORIZATION",
    "STALE_OWNER_AUTHORIZATION",
    "UNAUTHENTICATED_OWNER_AUTHORIZATION",
}
_S02_REFACTOR_RUNNABILITY_BLOCKS = {
    "MISSING_RUNNABILITY_COVERAGE",
    "NON_RUNNABLE_LOGICAL_STEP",
    "STALE_RUNNABILITY_EVIDENCE",
    "UNAUTHENTICATED_RUNNABILITY_EVIDENCE",
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
    "asset_receipts",
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
    "source_files",
    "test_files",
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
    characterization_paths: tuple[str, ...]
    quality_adapters: tuple[str, ...]
    quality_profile: dict[str, Any] | None
    quality_result: str | None
    responsibility_targets: tuple[str, ...]
    result: str
    source_sha256: str
    unbounded_production_paths: tuple[str, ...]
    source: dict[str, Any]


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
        active = affected & self.applicable
        for standard in active:
            self.errors[standard].add(code)
        if len(active) > 1:
            self.shared.add(("TECHNICAL_ERROR", code, dependency, tuple(sorted(active))))


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


def _s02_asset_receipts(value: object, code: str) -> tuple[dict[str, Any], ...]:
    keys = {"blob_sha256", "kind", "path", "result", "validator"}
    rows = _s02_rows(value, keys, code)
    paths: list[str] = []
    for row in rows:
        path = row["path"]
        identity = (
            quality_profile.ASSET_VALIDATORS.get(Path(path).suffix)
            if isinstance(path, str)
            else None
        ) or quality_profile.UNSUPPORTED_ASSET_IDENTITY
        if (
            not isinstance(path, str)
            or not path
            or (row["kind"], row["validator"]) != identity
            or not _s02_sha(row["blob_sha256"], _S02_SHA64)
            or not isinstance(row["result"], str)
            or row["result"] not in quality_profile.ASSET_RESULTS
            or (row["result"] == "UNSUPPORTED")
            is not (identity == quality_profile.UNSUPPORTED_ASSET_IDENTITY)
        ):
            raise StandardResultsError(code)
        paths.append(path)
    if paths != sorted(set(paths)):
        raise StandardResultsError(code)
    return tuple(rows)


def _s02_asset_blocks(receipts: tuple[dict[str, Any], ...]) -> frozenset[str]:
    families = {
        "MALFORMED": "MALFORMED_PRODUCTION_ASSET",
        "UNSUPPORTED": "UNSUPPORTED_PRODUCTION_ASSET",
    }
    return frozenset(
        f"{families[receipt['result']]}:{receipt['path']}"
        for receipt in receipts
        if receipt["result"] != "PASS"
    )


def _s02_profile(
    value: object, identity: RunIdentity, language: str, code: str
) -> tuple[
    tuple[str, ...],
    str,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    frozenset[str],
]:
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
    receipts = _s02_asset_receipts(row["asset_receipts"], code)
    adapters = tuple(_s02_profile_command(command, code) for command in commands)
    expected_commands = dict(quality_profile.command_templates(language))
    required_adapters = quality_profile.required_adapters(language)
    if (
        actual != expected
        or row["schema_version"] != "quality-gates.v6"
        or row["maximum_complexity"] != 10
        or adapters != tuple(adapter for adapter in required_adapters if adapter in adapters)
        or any(
            command["adapter"] not in expected_commands
            or tuple(command["arguments"]) != expected_commands[command["adapter"]]
            or command["proof_kind"] != quality_profile.expected_proof_kind(command["adapter"])
            for command in commands
        )
    ):
        raise StandardResultsError(code)
    for name in (
        "changed_paths",
        "exclusions",
        "high_risk_paths",
        "production_files",
        "production_paths",
        "source_files",
        "test_files",
    ):
        _s02_strings(row[name], code)
    production = row["production_files"]
    sources = row["source_files"]
    if (
        production != sorted(set(production))
        or sources != sorted(set(sources))
        or any(path not in production for path in sources)
        or any(not path.endswith(quality_profile.SOURCE_SUFFIXES[language]) for path in sources)
        or production != sorted([*sources, *(receipt["path"] for receipt in receipts)])
        or any(
            path not in sources
            for command in commands
            for field in ("observed_paths", "zero_statement_paths")
            for path in command[field]
        )
    ):
        raise StandardResultsError(code)
    result = (
        "BLOCK"
        if any(receipt["result"] != "PASS" for receipt in receipts)
        or any(
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
    architecture_failed = tuple(
        command["adapter"]
        for command in commands
        if command["executed"]
        and command["exit_code"] == 1
        and contract.POLICY_EXIT_STANDARDS[row["language"]].get(command["adapter"]) == 3
    )
    missing = tuple(adapter for adapter in required_adapters if adapter not in adapters)
    return adapters, result, failed, architecture_failed, missing, _s02_asset_blocks(receipts)


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
    section = _s02_exact(value, {"after", "before", "boundaries"}, code)
    if any(
        not isinstance(section[field], str) or not section[field].strip()
        for field in ("after", "before")
    ):
        raise StandardResultsError(code)
    rows = _s02_rows(section["boundaries"], {"after", "before", "kind", "path", "symbol"}, code)
    identities: set[tuple[str, str, str]] = set()
    for row in rows:
        if row["kind"] not in {"function", "component", "module"} or any(
            not isinstance(row[name], str) or not row[name].strip() for name in row
        ):
            raise StandardResultsError(code)
        identity = (row["path"], row["kind"], row["symbol"])
        if identity in identities:
            raise StandardResultsError(code)
        identities.add(identity)


def _s02_review(value: object, blocks: list[str], code: str) -> None:
    owners = frozenset().union(*(standard_block_ownership.review_owners(block) for block in blocks))
    if value is None:
        if 2 in owners:
            return
        raise StandardResultsError(code)
    if owners:
        if 2 in owners:
            raise StandardResultsError(code)
        keys = {"separation_of_concerns"}
        if isinstance(value, dict) and "module_boundaries" in value:
            keys.add("module_boundaries")
        row = _s02_exact(value, keys, code)
        _s02_separation_boundaries(row["separation_of_concerns"], code)
        if "module_boundaries" in row:
            _s02_review_boundaries(row["module_boundaries"], code)
        return
    keys = {"module_boundaries", "schema_version", *_S02_REVIEW_SECTIONS}
    row = _s02_exact(value, keys, code)
    if row["schema_version"] != "1.0":
        raise StandardResultsError(code)
    for name, (text_fields, list_fields) in _S02_REVIEW_SECTIONS.items():
        if name == "separation_of_concerns":
            _s02_separation_boundaries(row[name], code)
        else:
            _s02_review_section(row[name], text_fields, list_fields, code)
    _s02_review_boundaries(row["module_boundaries"], code)


def _s02_handoff_claim_blocks(value: object) -> list[str]:
    if not isinstance(value, dict) or not isinstance(value.get("review_handoff"), dict):
        return []
    handoff = value["review_handoff"]
    blocks = []
    if handoff.get("summary") != review_evidence.HANDOFF_SENTINEL:
        blocks.append("UNSUPPORTED_HANDOFF_CLAIM:review_handoff.summary")
    if handoff.get("remaining_risks") != [review_evidence.HANDOFF_SENTINEL]:
        blocks.append("UNSUPPORTED_HANDOFF_CLAIM:review_handoff.remaining_risks")
    return blocks


def _s02_review_binding(value: object, code: str) -> dict[str, Any]:
    row = _s02_exact(value, {"base", "head"}, code)
    for name in ("base", "head"):
        binding = row[name]
        if binding is None:
            continue
        binding = _s02_exact(binding, {"blob_sha", "sha256"}, code)
        if not _s02_sha(binding["blob_sha"], _S02_SHA40) or not _s02_sha(
            binding["sha256"], _S02_SHA64
        ):
            raise StandardResultsError(code)
    return row


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


def _s02_modularity_paths(
    changed: tuple[dict[str, Any], ...],
) -> tuple[list[str], list[str]]:
    changed_paths = sorted(
        {
            item["new_path"]
            for item in changed
            if item["head_production"] and item["new_path"] and item["complexity_assessed"]
        }
    )
    new_paths = sorted(
        {
            item["new_path"]
            for item in changed
            if item["head_production"]
            and item["new_path"]
            and item["complexity_assessed"]
            and (not item["base_production"] or item["old_path"] != item["new_path"])
        }
    )
    return changed_paths, new_paths


def _s02_modularity_coverage(source: dict[str, Any], new_paths: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "adapters": sorted(
                gate["adapter"]
                for gate in source["gate_coverage"]
                for command in source["quality_profile"]["commands"]
                if gate["adapter"] == command["adapter"]
                and contract.GateAdapter(gate["adapter"], tuple(gate["paths"])).covers(path)
                and not contract.command_failed(
                    source["language"],
                    command["adapter"],
                    command["executed"],
                    command["exit_code"],
                )
                and path in (*command["observed_paths"], *command["zero_statement_paths"])
            ),
            "architecture": path in source["architecture"]["covered_paths"],
            "path": path,
        }
        for path in new_paths
    ]


def _s02_modularity_rows(
    value: object, policy_blocks: list[str], code: str
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
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
    justifications = _s02_rows(row["justifications"], justification_keys, code)
    for item in justifications:
        if item["basis"] not in {"domain", "responsibility"} or any(
            not isinstance(item[name], str) or not item[name] for name in justification_keys
        ):
            raise StandardResultsError(code)
    return row, blocks, justifications


def _s02_modularity(
    value: object,
    source: dict[str, Any],
    changed: tuple[dict[str, Any], ...],
    policy_blocks: list[str],
    code: str,
) -> None:
    row, blocks, justifications = _s02_modularity_rows(value, policy_blocks, code)
    if not isinstance(source["architecture"], dict) or not isinstance(
        source["quality_profile"], dict
    ):
        raise StandardResultsError(code)
    changed_paths, new_paths = _s02_modularity_paths(changed)
    review = source["review_evidence"]
    expected_justifications = (
        review.get("module_boundaries", []) if isinstance(review, dict) else []
    )
    expected_coupling = [
        edge for edge in source["architecture"]["edges"] if edge["source"] in changed_paths
    ]
    expected_coverage = _s02_modularity_coverage(source, new_paths)
    try:
        expected_blocks = list(
            modularity_policy.derive_modularity_blocks(
                tuple(source["production_paths"]),
                tuple(new_paths),
                tuple(
                    modularity_policy.LocationJustification(**item)
                    for item in expected_justifications
                ),
                tuple(source["architecture"]["nodes"]),
                tuple(
                    modularity_policy.LocationCoverage(
                        item["path"], tuple(item["adapters"]), item["architecture"]
                    )
                    for item in expected_coverage
                ),
                len(source["gate_coverage"]),
            )
        )
    except (contract.ContractError, ValueError):
        raise StandardResultsError(code) from None
    gate_four_blocks = sorted(
        block
        for block in policy_blocks
        if any(block.startswith(family) for family in standard_block_ownership.BLOCK_FAMILIES[3])
    )
    actual = (
        row["changed_paths"],
        row["new_paths"],
        row["justifications"],
        row["coupling_edges"],
        row["coverage"],
        blocks,
        gate_four_blocks,
    )
    expected = (
        changed_paths,
        new_paths,
        expected_justifications,
        expected_coupling,
        expected_coverage,
        expected_blocks,
        expected_blocks,
    )
    if actual != expected:
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
    changed: tuple[dict[str, Any], ...],
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
    if row["review_evidence"] is not None or review_owners:
        _s02_review(row["review_evidence"], blocks, code)
    try:
        profile = (
            _s02_profile(row["quality_profile"], identity, row["language"], code)
            if row["quality_profile"] is not None
            else ((), None, (), (), (), frozenset())
        )
    except StandardResultsError:
        technical[:] = list(dict.fromkeys((*technical, "MALFORMED_QUALITY_EVIDENCE")))
        profile = ((), None, (), (), (), frozenset())
    if profile[1] is None:
        return (), None
    if row["modularity"] is not None:
        _s02_modularity(row["modularity"], row, changed, blocks, code)
    failed = {
        block.removeprefix("QUALITY_GATE_FAILED:")
        for block in blocks
        if block.startswith("QUALITY_GATE_FAILED:")
    }
    missing = {
        block.removeprefix("MISSING_QUALITY_COMMAND:")
        for block in blocks
        if block.startswith("MISSING_QUALITY_COMMAND:")
    }
    asset_prefixes = ("MALFORMED_PRODUCTION_ASSET:", "UNSUPPORTED_PRODUCTION_ASSET:")
    asset_blocks = frozenset(block for block in blocks if block.startswith(asset_prefixes))
    quality_invalid = (
        failed != set(profile[2]) or missing != set(profile[4]) or asset_blocks != profile[5]
    )
    architecture_failed = {
        block.removeprefix("ARCHITECTURE_GATE_FAILED:")
        for block in blocks
        if block.startswith("ARCHITECTURE_GATE_FAILED:")
    }
    if architecture_failed != set(profile[3]):
        technical.append("ARCHITECTURE_RESULT_BINDING_MISMATCH")
    quality_invalid = (
        quality_invalid
        or profile[1] == "BLOCK"
        and not any(7 in standard_block_ownership.owners(block) for block in blocks)
    )
    quality_invalid = quality_invalid or (
        row["quality_profile"] is not None
        and row["high_risk_paths"] != row["quality_profile"]["high_risk_paths"]
    )
    if quality_invalid:
        technical.append("MALFORMED_QUALITY_EVIDENCE")
        return (), None
    return profile[0], profile[1]


def _s02_characterization_paths(
    row: dict[str, Any], changed: tuple[dict[str, Any], ...]
) -> tuple[str, ...]:
    head_paths = {
        item["new_path"] for item in changed if item["head_production"] and item["new_path"]
    }
    deleted_paths = {
        item["old_path"]
        for item in changed
        if item["base_production"] and item["old_path"] and item["new_path"] is None
    }
    return tuple(
        characterization.derive_required_paths(
            head_paths, set(row["high_risk_paths"]), deleted_paths
        )
    )


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
    responsibility_targets = _s02_strings(row["responsibility_targets"], code)
    unbounded_production_paths = _s02_strings(row["unbounded_production_paths"], code)
    if responsibility_targets != sorted(
        set(responsibility_targets)
    ) or unbounded_production_paths != sorted(set(unbounded_production_paths)):
        raise StandardResultsError(code)
    _s02_refactor_target_paths(responsibility_targets, code)
    try:
        normalized_unbounded = [
            contract.normalize_repository_path(path, "unbounded_production_paths")
            for path in unbounded_production_paths
        ]
    except contract.ContractError:
        raise StandardResultsError(code) from None
    if normalized_unbounded != unbounded_production_paths:
        raise StandardResultsError(code)
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
    _s02_review_binding(row["review_evidence_binding"], code)
    _s02_gate_coverage(row["gate_coverage"], row["language"], code, common_required)
    quality_adapters, quality_result = _s02_complexity_components(
        row, blocks, technical, changed, identity, code
    )
    blocks = list(dict.fromkeys((*blocks, *_s02_handoff_claim_blocks(row["review_evidence"]))))
    return _S02Complexity(
        tuple((*blocks, *function_blocks)),
        tuple(technical),
        changed,
        _s02_characterization_paths(row, changed),
        quality_adapters,
        row["quality_profile"],
        quality_result,
        tuple(responsibility_targets),
        result,
        hashlib.sha256(_canonical(row)).hexdigest(),
        tuple(unbounded_production_paths),
        row,
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


def _s02_characterization(
    value: object,
    identity: RunIdentity,
    required_paths: tuple[str, ...] | None,
    expected_artifacts: object,
) -> list[str]:
    try:
        return characterization.validate_result(
            value,
            repository=f"github.com/{identity.repository}",
            base_sha=identity.base_sha,
            head_sha=identity.head_sha,
            workflow_sha=identity.workflow_sha,
            required_paths=required_paths,
            expected_artifacts=expected_artifacts,
        )
    except characterization.CharacterizationError as error:
        code = (
            error.code
            if error.code == "CHARACTERIZATION_RESULT_BINDING_MISMATCH"
            else "MALFORMED_CHARACTERIZATION_RESULT"
        )
        raise StandardResultsError(code) from None


def _s02_refactor_authorization(
    value: object, comment_id: object, code: str
) -> dict[str, Any] | None:
    if value is None:
        if comment_id is not None:
            raise StandardResultsError(code)
        return None
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
    scope = _s02_strings(row["scope"], code, True)
    try:
        normalized = [
            contract.normalize_repository_path(path, "authorization.scope") for path in scope
        ]
    except contract.ContractError:
        raise StandardResultsError(code) from None
    targets = _s02_strings(row["targets"], code, True)
    if (
        scope != sorted(set(normalized))
        or targets != sorted(set(targets))
        or any("::" not in target for target in targets)
    ):
        raise StandardResultsError(code)
    return row


def _s02_refactor_predecessor(
    value: object, identity: RunIdentity, code: str
) -> tuple[dict[str, Any] | None, str | None]:
    keys = {
        "authorization",
        "authorization_comment_id",
        "base_sha",
        "block",
        "head_sha",
        "merge_sha",
        "pull_number",
    }
    row = _s02_exact(value, keys, code)
    authorization = _s02_refactor_authorization(
        row["authorization"], row["authorization_comment_id"], code
    )
    block = row["block"]
    identity_values = (row["base_sha"], row["head_sha"], row["merge_sha"])
    if block not in {None, "GITHUB_AUTHORIZATION_EVIDENCE_FAILURE", "INVALID_STRANGLER_SEQUENCE"}:
        raise StandardResultsError(code)
    if authorization is None:
        if any(item is not None for item in (*identity_values, row["pull_number"])):
            raise StandardResultsError(code)
        return None, block
    if (
        block is not None
        or any(not _s02_sha(item, _S02_SHA40) for item in identity_values)
        or type(row["pull_number"]) is not int
        or row["pull_number"] < 1
        or authorization["repository"] != identity.repository
        or authorization["base_sha"] != row["base_sha"]
        or authorization["head_sha"] != row["head_sha"]
        or authorization["sequence"]["predecessor_sha"] != row["base_sha"]
        or row["merge_sha"] != identity.base_sha
    ):
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    return authorization, None


def _s02_refactor_change_paths(
    changed: tuple[dict[str, Any], ...],
) -> tuple[list[str], list[str], list[str]]:
    scope = sorted({path for row in changed for path in (row["old_path"], row["new_path"]) if path})
    required = sorted(
        {
            path
            for row in changed
            if (
                path := row["new_path"]
                if row["head_production"]
                else row["old_path"]
                if row["base_production"]
                else None
            )
        }
    )
    allowed = sorted(
        {
            path
            for row in changed
            for path, production in (
                (row["old_path"], row["base_production"]),
                (row["new_path"], row["head_production"]),
            )
            if path and production
        }
    )
    return scope, required, allowed


def _s02_refactor_target_paths(
    targets: list[str], code: str = "MALFORMED_REFACTOR_RESULT"
) -> list[str]:
    paths: list[str] = []
    for target in targets:
        match = _S02_REFACTOR_TARGET.fullmatch(target)
        if match is None or int(match["start"]) > int(match["end"]):
            raise StandardResultsError(code)
        paths.append(match["path"])
    return paths


def _s02_refactor_proof_path(path: str) -> bool:
    return path in {
        ".supportability-characterization.json",
        ".supportability-review.toml",
    } or path.startswith("tests/characterization/")


def _s02_refactor_authorization_blocks(
    authorization: dict[str, Any], identity: RunIdentity
) -> set[str]:
    blocks: set[str] = set()
    if authorization["repository"] != identity.repository:
        blocks.add("AUTHORIZATION_REPOSITORY_MISMATCH")
    if (
        authorization["base_sha"] != identity.base_sha
        or authorization["head_sha"] != identity.head_sha
    ):
        blocks.add("STALE_OWNER_AUTHORIZATION")
    return blocks


def _s02_refactor_sequence_blocks(
    authorization: dict[str, Any],
    predecessor: dict[str, Any] | None,
    predecessor_block: str | None,
    identity: RunIdentity,
) -> set[str]:
    blocks: set[str] = set()
    if authorization["sequence"]["predecessor_sha"] != identity.base_sha:
        blocks.add("INVALID_STRANGLER_SEQUENCE")
    if predecessor_block is not None:
        blocks.add(predecessor_block)
        return blocks
    step = authorization["sequence"]["step"]
    if step == 1:
        if predecessor is not None:
            blocks.add("INVALID_STRANGLER_SEQUENCE")
        return blocks
    if predecessor is None or predecessor["sequence"]["step"] != step - 1:
        blocks.add("INVALID_STRANGLER_SEQUENCE")
    return blocks


def _s02_refactor_focus_blocks(
    row: dict[str, Any],
    authorization: dict[str, Any],
    scope: list[str],
    production: list[str],
    target_paths: list[str],
) -> set[str]:
    blocks: set[str] = set()
    if authorization["scope"] != scope:
        blocks.add("UNFOCUSED_DIFF_SCOPE")
    if authorization["targets"] != row["targets"] or row["unbounded_paths"]:
        blocks.add("UNVERIFIABLE_BOUNDED_TARGET")
    unrelated = [
        path for path in scope if path not in target_paths and not _s02_refactor_proof_path(path)
    ]
    if (len(row["targets"]) != 1 or len(set(target_paths)) != 1 or unrelated) and not authorization[
        "broad"
    ]:
        blocks.add("BROAD_AUTHORIZATION_REQUIRED")
    if not row["targets"] or any(path not in production for path in target_paths):
        blocks.add("MISSING_BOUNDED_PRODUCTION_TARGET")
    return blocks


def _s02_refactor_runnability_blocks(
    characterization_result: object,
    identity: RunIdentity,
    targets: list[str],
    unbounded_paths: list[str],
) -> set[str]:
    if (
        not isinstance(characterization_result, dict)
        or characterization_result.get("overall_result") != "PASS"
    ):
        return set()
    evidence = characterization_result.get("refactor_runnability")
    keys = {
        "base_sha",
        "head_sha",
        "repository",
        "runnable",
        "schema_version",
        "targets",
        "unbounded_paths",
        "workflow_sha",
    }
    if not isinstance(evidence, dict) or set(evidence) != keys:
        return {"UNAUTHENTICATED_RUNNABILITY_EVIDENCE"}
    evidence_targets = evidence["targets"]
    evidence_unbounded = evidence["unbounded_paths"]
    if (
        evidence["schema_version"] != characterization.RUNNABILITY_SCHEMA
        or type(evidence["runnable"]) is not bool
        or not _s02_sha(evidence["base_sha"], _S02_SHA40)
        or not _s02_sha(evidence["head_sha"], _S02_SHA40)
        or not _s02_sha(evidence["workflow_sha"], _S02_SHA40)
        or not isinstance(evidence["repository"], str)
        or not isinstance(evidence_targets, list)
        or any(not isinstance(item, str) for item in evidence_targets)
        or evidence_targets != sorted(set(evidence_targets))
        or not isinstance(evidence_unbounded, list)
        or any(not isinstance(item, str) for item in evidence_unbounded)
        or evidence_unbounded != sorted(set(evidence_unbounded))
    ):
        return {"UNAUTHENTICATED_RUNNABILITY_EVIDENCE"}
    try:
        _s02_refactor_target_paths(evidence_targets)
        normalized_unbounded = [
            contract.normalize_repository_path(path, "refactor_runnability.unbounded_paths")
            for path in evidence_unbounded
        ]
    except (StandardResultsError, contract.ContractError):
        return {"UNAUTHENTICATED_RUNNABILITY_EVIDENCE"}
    if (
        evidence_targets != targets
        or evidence_unbounded != unbounded_paths
        or evidence_unbounded != normalized_unbounded
    ):
        return {"UNAUTHENTICATED_RUNNABILITY_EVIDENCE"}
    stale = (
        evidence["repository"] != f"github.com/{identity.repository}"
        or evidence["base_sha"] != identity.base_sha
        or evidence["head_sha"] != identity.head_sha
        or evidence["workflow_sha"] != identity.workflow_sha
    )
    blocks = {"STALE_RUNNABILITY_EVIDENCE"} if stale else set()
    target_paths = set(_s02_refactor_target_paths(targets))
    covered = set(characterization_result["coverage"]["covered_paths"])
    if target_paths - covered:
        blocks.add("MISSING_RUNNABILITY_COVERAGE")
    elif not evidence["runnable"]:
        blocks.add("NON_RUNNABLE_LOGICAL_STEP")
    if characterization_result["policy_blocks"]:
        blocks.add("NON_RUNNABLE_LOGICAL_STEP")
    return blocks


def _s02_refactor_shape(
    row: dict[str, Any],
    changed: tuple[dict[str, Any], ...],
    responsibility_targets: tuple[str, ...] | None,
    unbounded_production_paths: tuple[str, ...] | None,
) -> tuple[list[str], list[str], list[str]]:
    scope, required, allowed = _s02_refactor_change_paths(changed)
    target_paths = _s02_refactor_target_paths(row["targets"])
    bounded_paths = set((*target_paths, *row["unbounded_paths"]))
    if (
        any(
            row[name] != sorted(set(row[name]))
            for name in ("changed_paths", "targets", "unbounded_paths")
        )
        or row["changed_paths"] != scope
        or (
            responsibility_targets is not None
            and (
                row["targets"] != list(responsibility_targets)
                or row["unbounded_paths"] != list(unbounded_production_paths or ())
            )
        )
        or row["applicable"] is not bool(required)
        or not set(required).issubset(bounded_paths)
        or not bounded_paths.issubset(allowed)
        or set(target_paths) & set(row["unbounded_paths"])
    ):
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    return scope, allowed, target_paths


def _s02_refactor_absent_authorization(
    row: dict[str, Any],
    predecessor: dict[str, Any] | None,
    predecessor_block: str | None,
) -> None:
    if predecessor is not None or predecessor_block is not None:
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    if not row["applicable"]:
        if row["policy_blocks"]:
            raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
        return
    authorization_blocks = set(row["policy_blocks"]) & _S02_REFACTOR_AUTHORIZATION_BLOCKS
    current = authorization_blocks & _S02_REFACTOR_CURRENT_AUTHORIZATION_BLOCKS
    if len(current) != 1 or authorization_blocks != current:
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")


def _s02_refactor_authenticated_authorization(
    row: dict[str, Any],
    authorization: dict[str, Any],
    identity: RunIdentity,
    scope: list[str],
    allowed: list[str],
    target_paths: list[str],
    predecessor: dict[str, Any] | None,
    predecessor_block: str | None,
) -> None:
    if not row["applicable"]:
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    if set(row["policy_blocks"]) & {
        "MALFORMED_OWNER_AUTHORIZATION",
        "MISSING_OWNER_AUTHORIZATION",
        "UNAUTHENTICATED_OWNER_AUTHORIZATION",
    }:
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    expected = _s02_refactor_authorization_blocks(authorization, identity)
    expected.update(_s02_refactor_focus_blocks(row, authorization, scope, allowed, target_paths))
    expected.update(
        _s02_refactor_sequence_blocks(authorization, predecessor, predecessor_block, identity)
    )
    if set(row["policy_blocks"]) & _S02_REFACTOR_AUTHORIZATION_BLOCKS != expected:
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")


def _s02_refactor_binding(
    row: dict[str, Any],
    authorization: dict[str, Any] | None,
    identity: RunIdentity,
    changed: tuple[dict[str, Any], ...],
    responsibility_targets: tuple[str, ...] | None,
    unbounded_production_paths: tuple[str, ...] | None,
    predecessor: dict[str, Any] | None,
    predecessor_block: str | None,
) -> None:
    scope, allowed, target_paths = _s02_refactor_shape(
        row, changed, responsibility_targets, unbounded_production_paths
    )
    if authorization is None:
        _s02_refactor_absent_authorization(row, predecessor, predecessor_block)
        return
    _s02_refactor_authenticated_authorization(
        row,
        authorization,
        identity,
        scope,
        allowed,
        target_paths,
        predecessor,
        predecessor_block,
    )


def _s02_refactor(
    value: object,
    characterization: object,
    identity: RunIdentity,
    complexity: _S02Complexity | None,
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
        or blocks != sorted(blocks)
        or row["overall_result"] != ("BLOCK" if blocks else "PASS")
        or not _s02_sha(row["characterization_sha256"], _S02_SHA64)
    ):
        raise StandardResultsError(code)
    for name in ("changed_paths", "targets", "unbounded_paths"):
        _s02_strings(row[name], code)
    authorization = _s02_refactor_authorization(
        row["authorization"], row["authorization_comment_id"], code
    )
    predecessor, predecessor_block = _s02_refactor_predecessor(row["predecessor"], identity, code)
    if complexity is not None:
        targets_unavailable = "REFACTOR_TARGET_DERIVATION_FAILURE" in complexity.technical
        _s02_refactor_binding(
            row,
            authorization,
            identity,
            complexity.changed_files,
            None if targets_unavailable else complexity.responsibility_targets,
            None if targets_unavailable else complexity.unbounded_production_paths,
            predecessor,
            predecessor_block,
        )
    expected = hashlib.sha256(_canonical(characterization)).hexdigest()
    expected_runnability = (
        _s02_refactor_runnability_blocks(
            characterization, identity, row["targets"], row["unbounded_paths"]
        )
        if row["applicable"]
        else set()
    )
    if (
        row["characterization_sha256"] != expected
        or set(blocks) & _S02_REFACTOR_RUNNABILITY_BLOCKS != expected_runnability
    ):
        raise StandardResultsError("REFACTOR_RESULT_BINDING_MISMATCH")
    return blocks


def _s02_provenance_adapters(value: object, code: str) -> tuple[str, ...]:
    keys = {
        "adapter",
        "executed_arguments",
        "raw_proof_sha256",
        "stderr_sha256",
        "stdout_sha256",
    }
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
            or not _s02_strings(command["executed_arguments"], code, True)
        ):
            raise StandardResultsError(code)
        adapters.append(command["adapter"])
    if len(adapters) != len(set(adapters)):
        raise StandardResultsError(code)
    return tuple(adapters)


def _s02_quality_value(
    values: dict[str, tuple[str, ...]], token: str, observed: tuple[str, ...], code: str
) -> None:
    if token not in _S02_QUALITY_TOKENS or (
        token not in _S02_QUALITY_LIST_TOKENS and (len(observed) != 1 or not observed[0])
    ):
        raise StandardResultsError(code)
    previous = values.setdefault(token, observed)
    if previous != observed:
        raise StandardResultsError(code)


def _s02_quality_scalar(
    template: str, executed: str, values: dict[str, tuple[str, ...]], code: str
) -> None:
    tokens = _S02_QUALITY_TOKEN.findall(template)
    if not tokens:
        if executed != template:
            raise StandardResultsError(code)
        return
    if len(tokens) != 1 or tokens[0] in _S02_QUALITY_LIST_TOKENS:
        raise StandardResultsError(code)
    token = tokens[0]
    prefix, suffix = template.split(token)
    if (
        not executed.startswith(prefix)
        or not executed.endswith(suffix)
        or len(executed) <= len(prefix) + len(suffix)
    ):
        raise StandardResultsError(code)
    end = len(executed) - len(suffix) if suffix else len(executed)
    _s02_quality_value(values, token, (executed[len(prefix) : end],), code)


def _s02_quality_command(
    template: object,
    executed: object,
    list_counts: dict[str, int],
    values: dict[str, tuple[str, ...]],
    code: str,
) -> None:
    template_arguments = _s02_strings(template, code, True)
    executed_arguments = _s02_strings(executed, code, True)
    index = 0
    for argument in template_arguments:
        if argument not in _S02_QUALITY_LIST_TOKENS:
            if index >= len(executed_arguments):
                raise StandardResultsError(code)
            _s02_quality_scalar(argument, executed_arguments[index], values, code)
            index += 1
            continue
        size = list_counts[argument]
        if size < 0 or index + size > len(executed_arguments):
            raise StandardResultsError(code)
        _s02_quality_value(values, argument, tuple(executed_arguments[index : index + size]), code)
        index += size
    if index != len(executed_arguments):
        raise StandardResultsError(code)


def _s02_quality_paths(
    values: dict[str, tuple[str, ...]], profile: dict[str, Any], code: str
) -> None:
    normalized = {
        token: tuple(item.replace("\\", "/") for item in observed)
        for token, observed in values.items()
    }
    repository = normalized.get("$REPOSITORY", (None,))[0]
    source = normalized.get("$SOURCE_FILES")
    source_files = tuple(profile["source_files"])
    if source is not None and source_files:
        suffix = f"/{source_files[0]}"
        if not source[0].endswith(suffix):
            raise StandardResultsError(code)
        derived = source[0][: -len(suffix)]
        if repository not in {None, derived}:
            raise StandardResultsError(code)
        repository = derived
        if source != tuple(f"{repository}/{path}" for path in source_files):
            raise StandardResultsError(code)
    tests = normalized.get("$TEST_FILES")
    test_files = tuple(profile["test_files"])
    if tests is not None and test_files:
        suffix = f"/{test_files[0]}"
        if not tests[0].endswith(suffix):
            raise StandardResultsError(code)
        derived = tests[0][: -len(suffix)]
        if repository not in {None, derived}:
            raise StandardResultsError(code)
        repository = derived
    if tests is not None and tests != tuple(f"{repository}/{path}" for path in test_files):
        raise StandardResultsError(code)
    output = normalized.get("$OUTPUT")
    tools = normalized.get("$TOOLS")
    if tools is not None and (output is None or tools != (f"{output[0]}/quality-tools",)):
        raise StandardResultsError(code)


def _s02_quality_argv(profile: dict[str, Any], provenance: dict[str, Any], code: str) -> None:
    values: dict[str, tuple[str, ...]] = {}
    list_counts = {
        "$SOURCE_FILES": len(profile["source_files"]),
        "$TEST_FILES": len(profile["test_files"]),
    }
    for decision, proof in zip(profile["commands"], provenance["commands"], strict=True):
        _s02_quality_command(
            decision["arguments"], proof["executed_arguments"], list_counts, values, code
        )
    _s02_quality_paths(values, profile, code)


def _s02_quality_capture(profile: dict[str, Any], provenance: dict[str, Any]) -> str:
    commands = [
        {**decision, **proof}
        for decision, proof in zip(profile["commands"], provenance["commands"], strict=True)
    ]
    original = {
        **profile,
        **{
            name: provenance[name]
            for name in (
                "job",
                "repository",
                "repository_id",
                "run_attempt",
                "run_id",
                "runner_environment",
            )
        },
        "artifact_digest": "",
        "artifact_id": "",
        "capture_sha256": "",
        "commands": commands,
    }
    return hashlib.sha256(
        (json.dumps(original, indent=2, sort_keys=True) + "\n").encode()
    ).hexdigest()


def _s02_quality_artifact(
    provenance: object,
    expected_artifact: object,
    profile_adapters: tuple[str, ...],
    profile: dict[str, Any] | None,
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
    if profile is None:
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
    ) or _s02_quality_capture(profile, row) != trusted["capture_sha256"]:
        raise StandardResultsError("QUALITY_ARTIFACT_IDENTITY_MISMATCH")
    _s02_quality_argv(profile, row, code)
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
    if data is None or data.technical or not _s02_short(data.changed_files):
        return False
    state = _S02State(frozenset({7}))
    for block in data.blocks:
        _s02_apply_block(state, block, "complexity-result")
    return not state.blocks[7] and not any(state.errors.values())


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
    data: _S02Complexity | None,
    expected_artifacts: object,
    errors: dict[str, str],
    outcomes: dict[str, str],
    short: bool,
) -> None:
    if "gate_install" in errors or short:
        return
    if not _s02_add_characterization(
        state, characterization, identity, data, expected_artifacts, errors, outcomes
    ):
        return
    _s02_add_refactor(state, refactor, characterization, identity, data, errors, outcomes)


def _s02_add_characterization(
    state: _S02State,
    characterization: object,
    identity: RunIdentity,
    data: _S02Complexity | None,
    expected_artifacts: object,
    errors: dict[str, str],
    outcomes: dict[str, str],
) -> bool:
    char_error = errors.get("characterization")
    if char_error is not None:
        _s02_source_failure(state, "characterization", char_error)
        return False
    try:
        blocks = _s02_characterization(
            characterization,
            identity,
            data.characterization_paths if data is not None else None,
            expected_artifacts,
        )
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
    data: _S02Complexity | None,
    errors: dict[str, str],
    outcomes: dict[str, str],
) -> None:
    if "refactor" in errors:
        _s02_source_failure(state, "refactor", errors["refactor"])
        return
    try:
        refactor_blocks = _s02_refactor(refactor, characterization, identity, data)
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
            data.quality_profile if data is not None else None,
            identity,
        )
    except StandardResultsError as error:
        _s02_source_failure(state, "quality_provenance", error.code)
        return None


def _s02_handoff_commands(
    data: _S02Complexity,
    provenance: object,
    artifact: dict[str, object] | None,
) -> list[dict[str, object]]:
    profile = data.source["quality_profile"]
    if artifact is None or not isinstance(profile, dict) or not isinstance(provenance, dict):
        return []
    proof_rows = provenance.get("commands")
    if not isinstance(proof_rows, list):
        return []
    proof = {row["adapter"]: row for row in proof_rows if isinstance(row, dict)}
    return [{**row, **proof[row["adapter"]]} for row in profile["commands"]]


def _s02_handoff_boundaries(source: dict[str, Any]) -> list[dict[str, str]] | None:
    review = source["review_evidence"]
    if not isinstance(review, dict):
        return None
    separation = review.get("separation_of_concerns")
    if not isinstance(separation, dict):
        return None
    boundaries = separation.get("boundaries")
    if not isinstance(boundaries, list):
        return None
    return [
        {name: row[name] for name in ("kind", "path", "symbol")}
        for row in boundaries
        if isinstance(row, dict)
    ]


def _s02_handoff_coverage(data: _S02Complexity) -> dict[str, object]:
    source = data.source
    profile = source["quality_profile"]
    profile_verified = data.quality_result is not None and isinstance(profile, dict)
    if not profile_verified:
        profile = {}
    candidate_changed = "CANDIDATE_CONTRACT_CHANGE" in data.blocks
    weakened = bool({"THRESHOLD_WEAKENING", "QUALITY_THRESHOLD_WEAKENING"} & set(data.blocks))
    mismatch = "QUALITY_THRESHOLD_MISMATCH" in data.blocks
    narrowed = bool({"GATE_SCOPE_NARROWING", "QUALITY_SCOPE_NARROWING"} & set(data.blocks))
    zero_statement = {
        path
        for command in profile.get("commands", [])
        for path in command.get("zero_statement_paths", [])
    }
    untested = {
        block.split(":", 2)[-1] for block in data.blocks if block.startswith("UNTESTED_AREA:")
    }
    return {
        "candidate_contract_changed": candidate_changed,
        "changed_paths": profile.get("changed_paths", []),
        "exclusions": profile.get("exclusions", []),
        "gate_coverage": source["gate_coverage"],
        "high_risk_paths": profile.get("high_risk_paths", []),
        "maximum_complexity": profile.get("maximum_complexity"),
        "asset_receipts": profile.get("asset_receipts", []),
        "production_files": profile.get("production_files", []),
        "production_paths": profile.get("production_paths", []),
        "source_files": profile.get("source_files", []),
        "scope_state": (
            "UNVERIFIED"
            if not profile_verified
            else "NARROWED"
            if narrowed
            else "UNVERIFIED_CANDIDATE_CHANGE"
            if candidate_changed
            else "UNCHANGED"
        ),
        "test_files": profile.get("test_files", []),
        "threshold_state": (
            "UNVERIFIED"
            if not profile_verified
            else "WEAKENED"
            if weakened
            else "MISMATCH"
            if mismatch
            else "UNVERIFIED_CANDIDATE_CHANGE"
            if candidate_changed
            else "UNCHANGED"
        ),
        "untested_paths": sorted(zero_statement | untested),
    }


def _s02_handoff_blocks(state: _S02State, data: _S02Complexity) -> None:
    binding = data.source["review_evidence_binding"]
    head = binding["head"]
    base = binding["base"]
    if head is None:
        _s02_apply_block(state, "UNAUTHENTICATED_HANDOFF_EVIDENCE", "complexity-result")
    elif base is not None and base["blob_sha"] == head["blob_sha"]:
        _s02_apply_block(state, "STALE_HANDOFF_EVIDENCE", "complexity-result")


def _s02_handoff_risks(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {"code": code, "kind": kind, "standard": row["standard"]}
        for row in entries
        for kind, codes in (
            ("POLICY_BLOCK", row["policy_blocks"]),
            ("TECHNICAL_ERROR", row["technical_errors"]),
        )
        if isinstance(codes, list)
        for code in codes
    ]


def _s02_handoff_gaps(functions: list[dict[str, Any]]) -> list[dict[str, object]]:
    return [
        {
            "path": (row["head"] or row["base"])["path"],
            "qualified_name": (row["head"] or row["base"])["qualified_name"],
            "remaining_gap": row["remaining_gap"],
        }
        for row in functions
        if row["remaining_gap"]
    ]


def _s02_handoff_follow_up(functions: list[dict[str, Any]]) -> list[dict[str, object]]:
    return [
        {
            "next_target": row["next_target"],
            "path": (row["head"] or row["base"])["path"],
            "qualified_name": (row["head"] or row["base"])["qualified_name"],
        }
        for row in functions
        if row["remaining_gap"]
    ]


def _s02_handoff_source(name: str, value: object) -> dict[str, object]:
    return {
        "citations": list(_HANDOFF_CITATIONS[name]),
        "sha256": hashlib.sha256(_canonical(value)).hexdigest(),
    }


def _s02_handoff(
    state: _S02State,
    data: _S02Complexity | None,
    characterization_result: object,
    refactor_result: object,
    quality_provenance: object,
    quality_artifact: dict[str, object] | None,
    identity: RunIdentity,
    outcomes: dict[str, str],
) -> dict[str, object] | None:
    if data is None:
        return None
    _s02_handoff_blocks(state, data)
    entries = _s02_entries(state)
    functions = data.source["functions"]
    changed_files = json.loads(_canonical(data.changed_files))
    coverage = _s02_handoff_coverage(data)
    boundaries = _s02_handoff_boundaries(data.source)
    targets = list(data.responsibility_targets)
    commands = _s02_handoff_commands(data, quality_provenance, quality_artifact)
    characterization_valid = not state.errors[5]
    refactor_valid = not state.errors[6]
    characterization_artifacts = (
        characterization_result.get("artifacts")
        if isinstance(characterization_result, dict)
        and characterization_valid
        and outcomes["characterization"] == "success"
        else None
    )
    identity_facts = {
        "base_sha": identity.base_sha,
        "characterization_artifacts": characterization_artifacts,
        "characterization_result_sha256": (
            hashlib.sha256(_canonical(characterization_result)).hexdigest()
            if characterization_valid
            else None
        ),
        "complexity_result_sha256": data.source_sha256,
        "head_sha": identity.head_sha,
        "quality_artifact": quality_artifact,
        "quality_provenance_sha256": (
            hashlib.sha256(_canonical(quality_provenance)).hexdigest()
            if quality_artifact is not None
            else None
        ),
        "refactor_result_sha256": (
            hashlib.sha256(_canonical(refactor_result)).hexdigest() if refactor_valid else None
        ),
        "repository": identity.repository,
        "repository_id": identity.repository_id,
        "review_evidence": data.source["review_evidence_binding"],
        "run_attempt": identity.run_attempt,
        "run_id": identity.run_id,
        "workflow_sha": identity.workflow_sha,
    }
    responsibility_facts = {"boundaries": boundaries, "targets": targets}
    return {
        "changed_files": changed_files,
        "coverage": coverage,
        "follow_up": _s02_handoff_follow_up(functions),
        "functions": functions,
        "gaps": _s02_handoff_gaps(functions),
        "identity": identity_facts,
        "responsibility_boundaries": boundaries,
        "responsibility_targets": targets,
        "risks": _s02_handoff_risks(entries),
        "schema_version": "review-handoff.v1",
        "sources": {
            "change": _s02_handoff_source("change", changed_files),
            "coverage": _s02_handoff_source("coverage", coverage),
            "functions": _s02_handoff_source("functions", functions),
            "identity": _s02_handoff_source("identity", identity_facts),
            "responsibilities": _s02_handoff_source("responsibilities", responsibility_facts),
            "review_identity": _s02_handoff_source(
                "review_identity", data.source["review_evidence_binding"]
            ),
            "validation": _s02_handoff_source("validation", commands),
        },
        "validation": {
            "commands": commands,
            "source_outcomes": outcomes,
            "standards": [
                {
                    "policy_blocks": row["policy_blocks"],
                    "result": row["result"],
                    "standard": row["standard"],
                    "technical_errors": row["technical_errors"],
                }
                for row in entries
            ],
        },
    }


def compose_results(
    complexity: dict[str, Any] | None,
    characterization: dict[str, Any] | None,
    refactor: dict[str, Any] | None,
    quality_provenance: dict[str, Any] | None,
    identity: RunIdentity,
    *,
    expected_quality_artifact: dict[str, object] | None,
    expected_characterization_artifacts: dict[str, object] | None = None,
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
    _s02_add_behavior(
        state,
        characterization,
        refactor,
        identity,
        data,
        expected_characterization_artifacts,
        errors,
        outcomes,
        short,
    )
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
    handoff = _s02_handoff(
        state,
        data,
        characterization,
        refactor,
        quality_provenance,
        artifact,
        identity,
        outcomes,
    )
    source_validated = bool(data and not data.technical)
    applicability: dict[str, object] = {
        "changed_files": list(data.changed_files) if data else [],
        "classification": "SHORT_TASK" if short else "FULL_PROCESS",
        "source_sha256": data.source_sha256 if data else None,
        "source_validated": source_validated,
    }
    if short and data and data.blocks and complexity is not None:
        applicability["inapplicable_complexity_result"] = json.loads(_canonical(complexity))
    payload: dict[str, object] = {
        "applicability_evidence": applicability,
        "base_sha": identity.base_sha,
        "entries": _s02_entries(state),
        "head_sha": identity.head_sha,
        "quality_artifact": artifact,
        "repository": identity.repository,
        "repository_id": identity.repository_id,
        "review_handoff": handoff,
        "review_handoff_sha256": (
            hashlib.sha256(_canonical(handoff)).hexdigest() if handoff is not None else None
        ),
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


def _s02_inapplicable_source(
    value: object,
    identity: RunIdentity,
    changed: tuple[dict[str, Any], ...],
    source_sha256: object,
) -> bool:
    if value is None:
        return False
    code = "MALFORMED_STANDARD_RESULTS_APPLICABILITY"
    try:
        data = _s02_complexity(value, identity)
    except StandardResultsError:
        raise StandardResultsError(code) from None
    if (
        data.changed_files != changed
        or data.source_sha256 != source_sha256
        or data.result != "BLOCK"
        or not _s02_authenticated_short(data)
    ):
        raise StandardResultsError(code)
    return True


def _s02_applicability(value: object, short_task: bool, identity: RunIdentity) -> tuple[bool, bool]:
    keys = {"changed_files", "classification", "source_sha256", "source_validated"}
    if not isinstance(value, dict) or set(value) not in (
        keys,
        keys | {"inapplicable_complexity_result"},
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    row = value
    changed = _s02_changed(row["changed_files"], "MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    inapplicable_source = _s02_inapplicable_source(
        row.get("inapplicable_complexity_result"), identity, changed, row["source_sha256"]
    )
    if type(row["source_validated"]) is not bool:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    if row["source_sha256"] is not None and not _s02_sha(row["source_sha256"], _S02_SHA64):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    eligible = bool(row["source_validated"] and _s02_short(changed))
    classification = "SHORT_TASK" if short_task else "FULL_PROCESS"
    if (
        short_task
        and not eligible
        or inapplicable_source
        and not short_task
        or row["classification"] != classification
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    if row["source_validated"] and row["source_sha256"] is None:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_APPLICABILITY")
    return eligible, inapplicable_source


def _s02_source_uncertain(entries: list[dict[str, Any]]) -> bool:
    if any(row["policy_blocks"] for row in entries):
        return True
    codes = {code for row in entries for code in row["technical_errors"]}
    for code in codes:
        binding = standard_block_ownership.expected_technical_dependency(
            code, "quality-profile:artifact-binding"
        )
        if (
            binding is None
            or binding[0] != "quality-profile:artifact-binding"
            or 7 not in binding[1]
        ):
            return True
    return False


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
    applicable: frozenset[int],
) -> None:
    for code, actual in technical.items():
        bound = shared.get(("TECHNICAL_ERROR", code))
        expected = standard_block_ownership.expected_technical_dependency(
            code, bound[0] if bound else ""
        )
        if expected is None or expected[1] & applicable != frozenset(actual):
            raise StandardResultsError("MALFORMED_STANDARD_TECHNICAL_OWNERSHIP")
        active_expected = (expected[0], expected[1] & applicable)
        if len(actual) > 1 and bound != active_expected:
            raise StandardResultsError("MALFORMED_SHARED_FAILURE")
        if len(actual) == 1 and bound is not None:
            raise StandardResultsError("MALFORMED_SHARED_FAILURE")


def _s02_ownership(
    entries: list[dict[str, Any]],
    shared: dict[tuple[str, str], tuple[str, frozenset[int]]],
    applicable: frozenset[int],
) -> None:
    policies, technical = _s02_claims(entries)
    _s02_policy_ownership(policies, shared)
    _s02_technical_ownership(technical, shared, applicable)
    used = {("POLICY_BLOCK", code) for code in policies} | {
        ("TECHNICAL_ERROR", code) for code in technical
    }
    if set(shared) - used:
        raise StandardResultsError("MALFORMED_SHARED_FAILURE")


def _s02_handoff_sources(handoff: dict[str, Any], code: str) -> None:
    responsibility_facts = {
        "boundaries": handoff["responsibility_boundaries"],
        "targets": handoff["responsibility_targets"],
    }
    identity = handoff["identity"]
    validation = handoff["validation"]
    expected = {
        "change": handoff["changed_files"],
        "coverage": handoff["coverage"],
        "functions": handoff["functions"],
        "identity": identity,
        "responsibilities": responsibility_facts,
        "review_identity": identity["review_evidence"],
        "validation": validation["commands"],
    }
    sources = _s02_exact(handoff["sources"], set(expected), code)
    for name, fact in expected.items():
        source = _s02_exact(sources[name], {"citations", "sha256"}, code)
        if (
            source["citations"] != list(_HANDOFF_CITATIONS[name])
            or source["sha256"] != hashlib.sha256(_canonical(fact)).hexdigest()
        ):
            raise StandardResultsError(code)


def _s02_handoff_payload(
    value: object,
    sha256: object,
    row: dict[str, Any],
    entries: list[dict[str, Any]],
) -> None:
    if value is None:
        if sha256 is not None or entries[7]["result"] in {"PASS", "BLOCK"}:
            raise StandardResultsError("HANDOFF_RESULT_BINDING_MISMATCH")
        return
    handoff = _s02_exact(
        value,
        {
            "changed_files",
            "coverage",
            "follow_up",
            "functions",
            "gaps",
            "identity",
            "responsibility_boundaries",
            "responsibility_targets",
            "risks",
            "schema_version",
            "sources",
            "validation",
        },
        "HANDOFF_RESULT_BINDING_MISMATCH",
    )
    if (
        handoff["schema_version"] != "review-handoff.v1"
        or not _s02_sha(sha256, _S02_SHA64)
        or hashlib.sha256(_canonical(handoff)).hexdigest() != sha256
    ):
        raise StandardResultsError("HANDOFF_RESULT_BINDING_MISMATCH")
    identity = _s02_exact(
        handoff["identity"],
        {
            "base_sha",
            "characterization_artifacts",
            "characterization_result_sha256",
            "complexity_result_sha256",
            "head_sha",
            "quality_artifact",
            "quality_provenance_sha256",
            "refactor_result_sha256",
            "repository",
            "repository_id",
            "review_evidence",
            "run_attempt",
            "run_id",
            "workflow_sha",
        },
        "HANDOFF_RESULT_BINDING_MISMATCH",
    )
    actual = tuple(
        identity[name]
        for name in (
            "repository",
            "repository_id",
            "base_sha",
            "head_sha",
            "workflow_sha",
            "run_id",
            "run_attempt",
        )
    )
    expected = tuple(
        row[name]
        for name in (
            "repository",
            "repository_id",
            "base_sha",
            "head_sha",
            "workflow_sha",
            "run_id",
            "run_attempt",
        )
    )
    validation = _s02_exact(
        handoff["validation"],
        {"commands", "source_outcomes", "standards"},
        "HANDOFF_RESULT_BINDING_MISMATCH",
    )
    code = "HANDOFF_RESULT_BINDING_MISMATCH"
    _s02_handoff_sources(handoff, code)
    standards = [
        {
            "policy_blocks": entry["policy_blocks"],
            "result": entry["result"],
            "standard": entry["standard"],
            "technical_errors": entry["technical_errors"],
        }
        for entry in entries
    ]
    try:
        gaps = _s02_handoff_gaps(handoff["functions"])
        follow_up = _s02_handoff_follow_up(handoff["functions"])
    except (KeyError, TypeError):
        raise StandardResultsError(code) from None
    if (
        actual != expected
        or handoff["changed_files"] != row["applicability_evidence"]["changed_files"]
        or identity["complexity_result_sha256"] != row["applicability_evidence"]["source_sha256"]
        or identity["quality_artifact"] != row["quality_artifact"]
        or validation["source_outcomes"] != row["source_outcomes"]
        or validation["standards"] != standards
        or handoff["risks"] != _s02_handoff_risks(entries)
        or handoff["gaps"] != gaps
        or handoff["follow_up"] != follow_up
    ):
        raise StandardResultsError(code)


def _s02_validate_handoff(
    row: dict[str, Any], entries: list[dict[str, Any]], standard: int | None
) -> None:
    if standard in {None, 8}:
        _s02_handoff_payload(row["review_handoff"], row["review_handoff_sha256"], row, entries)


def validate_payload(
    value: object,
    identity: RunIdentity | None = None,
    *,
    standard: int | None = None,
) -> None:
    """Validate exact identity, applicability, ownership, and provenance bindings."""
    keys = {
        "applicability_evidence",
        "base_sha",
        "entries",
        "head_sha",
        "quality_artifact",
        "repository",
        "repository_id",
        "review_handoff",
        "review_handoff_sha256",
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
    eligible_short, inapplicable_source = _s02_applicability(
        row["applicability_evidence"], row["short_task"], actual
    )
    if not isinstance(row["entries"], list) or len(row["entries"]) != 8:
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_ARTIFACT")
    entries = [
        _s02_entry(item, standard, row["short_task"])
        for standard, item in enumerate(row["entries"], start=1)
    ]
    required_outcomes = {
        "complexity": {"failure" if inapplicable_source else "success"},
        "install": {"success"},
        "quality": {"success"},
    }
    if not row["short_task"]:
        required_outcomes.update(
            {
                "characterization": {"success"},
                "complexity": {"success"},
                "refactor": {"success"},
            }
        )
    if all(entry["result"] in {"PASS", "NOT_APPLICABLE_SHORT_TASK"} for entry in entries) and any(
        outcomes[source] not in allowed for source, allowed in required_outcomes.items()
    ):
        raise StandardResultsError("MALFORMED_STANDARD_RESULTS_SOURCE_OUTCOMES")
    shared = _s02_shared(row["shared_failures"])
    applicable = frozenset(entry["standard"] for entry in entries if entry["applicable"])
    _s02_ownership(entries, shared, applicable)
    _s02_validate_handoff(row, entries, standard)
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


def _s02_handoff_quality_source(
    row: dict[str, Any],
    data: _S02Complexity,
    provenance: object,
    identity: RunIdentity,
) -> dict[str, object] | None:
    artifact = row["quality_artifact"]
    if artifact is None:
        return None
    if not isinstance(artifact, dict):
        raise StandardResultsError("HANDOFF_SOURCE_BINDING_MISMATCH")
    return _s02_quality_artifact(
        provenance,
        {
            "capture_sha256": artifact["capture_sha256"],
            "digest": artifact["digest"],
            "id": str(artifact["id"]),
        },
        data.quality_adapters,
        data.quality_profile,
        identity,
    )


def validate_handoff_sources(
    value: object,
    complexity: object,
    quality_provenance: object,
    identity: RunIdentity,
) -> None:
    """Bind Gate 8 facts to independently produced source files."""
    validate_payload(value, identity, standard=8)
    if not isinstance(value, dict):
        raise StandardResultsError("HANDOFF_SOURCE_BINDING_MISMATCH")
    row = value
    data = _s02_complexity(complexity, identity)
    applicability = row["applicability_evidence"]
    handoff = row["review_handoff"]
    if not isinstance(applicability, dict) or data.source_sha256 != applicability["source_sha256"]:
        raise StandardResultsError("HANDOFF_SOURCE_BINDING_MISMATCH")
    if handoff is None:
        return
    if not isinstance(handoff, dict) or not isinstance(handoff.get("identity"), dict):
        raise StandardResultsError("HANDOFF_SOURCE_BINDING_MISMATCH")
    artifact = _s02_handoff_quality_source(row, data, quality_provenance, identity)
    handoff_identity = handoff["identity"]
    expected_quality_sha = (
        hashlib.sha256(_canonical(quality_provenance)).hexdigest() if artifact is not None else None
    )
    expected = (
        json.loads(_canonical(data.changed_files)),
        _s02_handoff_coverage(data),
        data.source["functions"],
        _s02_handoff_boundaries(data.source),
        list(data.responsibility_targets),
        _s02_handoff_commands(data, quality_provenance, artifact),
        data.source_sha256,
        expected_quality_sha,
        data.source["review_evidence_binding"],
    )
    actual = (
        handoff["changed_files"],
        handoff["coverage"],
        handoff["functions"],
        handoff["responsibility_boundaries"],
        handoff["responsibility_targets"],
        handoff["validation"]["commands"],
        handoff_identity["complexity_result_sha256"],
        handoff_identity["quality_provenance_sha256"],
        handoff_identity["review_evidence"],
    )
    if actual != expected:
        raise StandardResultsError("HANDOFF_SOURCE_BINDING_MISMATCH")
