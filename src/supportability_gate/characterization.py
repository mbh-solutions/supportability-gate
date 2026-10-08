"""Verify authenticated base/head characterization artifacts without executing target code."""

from __future__ import annotations

import argparse
import ast
import dis
import hashlib
import json
import marshal
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import CodeType
from typing import Any, cast

from supportability_gate import baseline_evidence, contract, git_changes

MANIFEST_PATH = ".supportability-characterization.json"
SCENARIO_ROOT = "tests/characterization"
CAPTURE_SCHEMA = "characterization-capture.v2"
PROVENANCE_SCHEMA = "characterization-provenance.v1"
LEGACY_RESULT_SCHEMA = "characterization-result.v1"
RESULT_SCHEMA = "characterization-result.v2"
OBSERVED_RESULT_SCHEMA = "characterization-result.v3"
OBSERVED_CAPTURE_SCHEMA = "characterization-capture.v3"
MODULE_RESULT_SCHEMA = "characterization-result.v4"
MODULE_CAPTURE_SCHEMA = "characterization-capture.v4"
CORRECTION_RESULT_SCHEMA = "characterization-result.v5"
CORRECTION_CAPTURE_SCHEMA = "characterization-capture.v5"
RUNNABILITY_SCHEMA = "refactor-runnability.v1"
KINDS = frozenset({"test", "sample_io", "snapshot", "golden", "cli", "regression", "baseline"})
OBLIGATION_CATEGORIES = frozenset({"behavior", "cli_help", "static", "baseline"})
SCENARIO_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ARTIFACT_ID = re.compile(r"[1-9][0-9]*")
SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_JSON_BYTES = 1_000_000
MAX_SCENARIOS = 64
MAX_OBSERVED_APIS = 50
MODULE_MAX_OBSERVED_APIS = 100
MODULE_MAX_JSON_BYTES = 2_000_000
MODULE_AGGREGATE_JSON_BYTES = 32_000_000


class CharacterizationError(ValueError):
    """One fail-closed characterization defect."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class Scenario:
    """One fixed-convention characterization scenario."""

    id: str
    kind: str
    covers: tuple[str, ...]
    api: str | None = None
    module_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class Obligation:
    """One stable assertion identity independent of scenario packaging."""

    id: str
    category: str
    scenario: str
    selector: str
    target: str


@dataclass(frozen=True)
class Transition:
    """One explicit baseline-obligation migration."""

    source: str
    targets: tuple[str, ...]
    baseline_sha256: str


@dataclass(frozen=True)
class OracleFile:
    """One byte-frozen correction oracle file."""

    path: str
    sha256: str
    kind: str


@dataclass(frozen=True)
class Correction:
    """One repository-agnostic, oracle-first behavior correction."""

    id: str
    scenarios: tuple[str, ...]
    obligations: tuple[str, ...]
    targets: tuple[str, ...]
    oracle_files: tuple[OracleFile, ...]


@dataclass(frozen=True)
class Manifest:
    """Validated scenario manifest at one immutable commit."""

    scenarios: tuple[Scenario, ...]
    blob_sha: str
    sha256: str
    obligations: tuple[Obligation, ...] = ()
    transitions: tuple[Transition, ...] = ()
    corrections: tuple[Correction, ...] = ()
    schema_version: str = "1.0"


def _manifest_payload(manifest: Manifest) -> dict[str, object]:
    payload: dict[str, object] = {
        "blob_sha": manifest.blob_sha,
        "scenarios": [
            {
                "covers": list(item.covers),
                "id": item.id,
                "kind": item.kind,
                **({"api": item.api} if manifest.schema_version in {"3.0", "4.0", "5.0"} else {}),
                **(
                    {"module_roots": list(item.module_roots)}
                    if manifest.schema_version in {"4.0", "5.0"}
                    else {}
                ),
            }
            for item in manifest.scenarios
        ],
        "sha256": manifest.sha256,
    }
    if manifest.schema_version in {"2.0", "3.0", "4.0", "5.0"}:
        payload.update(
            {
                "obligations": [
                    {
                        "category": item.category,
                        "id": item.id,
                        "scenario": item.scenario,
                        "selector": item.selector,
                        "target": item.target,
                    }
                    for item in manifest.obligations
                ],
                "schema_version": manifest.schema_version,
                "transitions": [
                    {
                        "baseline_sha256": item.baseline_sha256,
                        "from": item.source,
                        "to": list(item.targets),
                    }
                    for item in manifest.transitions
                ],
            }
        )
    if manifest.schema_version == "5.0":
        payload["corrections"] = [
            {
                "id": item.id,
                "obligations": list(item.obligations),
                "oracle_files": [
                    {"kind": file.kind, "path": file.path, "sha256": file.sha256}
                    for file in item.oracle_files
                ],
                "scenarios": list(item.scenarios),
                "targets": list(item.targets),
            }
            for item in manifest.corrections
        ]
    return payload


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json_bytes(content: bytes, code: str) -> Any:
    if not content or len(content) > MAX_JSON_BYTES:
        raise CharacterizationError(code)
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CharacterizationError(code) from error


def _read_module_json(content: bytes, code: str) -> Any:
    """Read only a fixed module witness/oracle, leaving ordinary JSON unchanged."""
    if not content or len(content) > MODULE_MAX_JSON_BYTES:
        raise CharacterizationError(code)
    try:
        return json.loads(content, object_pairs_hook=_module_json_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise CharacterizationError(code) from error


def _module_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_MODULE_JSON_KEY")
        result[key] = value
    return result


def _read_module_aggregate(content: bytes, code: str, schema: str) -> dict[str, Any]:
    if not content or len(content) > MODULE_AGGREGATE_JSON_BYTES:
        raise CharacterizationError(code)
    try:
        value = json.loads(content, object_pairs_hook=_module_json_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise CharacterizationError(code) from error
    if schema not in {
        MODULE_CAPTURE_SCHEMA,
        MODULE_RESULT_SCHEMA,
        CORRECTION_CAPTURE_SCHEMA,
        CORRECTION_RESULT_SCHEMA,
    } or not isinstance(value, dict):
        raise CharacterizationError(code)
    if value.get("schema_version") != schema:
        raise CharacterizationError(code)
    return value


def _exact_keys(value: object, expected: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CharacterizationError(code)
    return value


def _path_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST", field)
    paths = tuple(contract.normalize_repository_path(item, field) for item in value)
    if len(paths) != len(set(paths)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST", field)
    return paths


def _scenario_rows(value: object, version: str = "1.0") -> tuple[Scenario, ...]:
    if not isinstance(value, list) or not value:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    parsed: list[Scenario] = []
    for item in value:
        keys = {"covers", "id", "kind"}
        row = _exact_keys(
            item,
            keys
            | ({"api"} if version in {"3.0", "4.0", "5.0"} else set())
            | ({"module_roots"} if version in {"4.0", "5.0"} else set()),
            "MALFORMED_CHARACTERIZATION_MANIFEST",
        )
        identifier, kind = row["id"], row["kind"]
        if (
            not isinstance(identifier, str)
            or SCENARIO_ID.fullmatch(identifier) is None
            or kind not in KINDS
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
        covers = _path_list(row["covers"], "covers")
        api = _parse_api(row.get("api"), covers)
        roots = _module_roots(row.get("module_roots", []), api, covers)
        if kind == "baseline" and (version != "5.0" or api is not None or roots):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
        parsed.append(Scenario(identifier, str(kind), covers, api, roots))
    if (
        len(parsed) != len({item.id for item in parsed})
        or len(parsed) > MAX_SCENARIOS
        or sum(max(1, len(item.module_roots)) for item in parsed if item.api is not None)
        > (MODULE_MAX_OBSERVED_APIS if version in {"4.0", "5.0"} else MAX_OBSERVED_APIS)
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    if version in {"4.0", "5.0"}:
        actual_roots = [
            root
            for item in parsed
            for root in (item.module_roots or ((item.api,) if item.api else ()))
        ]
        if len(actual_roots) != len(set(actual_roots)):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return tuple(sorted(parsed, key=lambda item: item.id))


def _module_roots(value: object, api: str | None, covers: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MODULE_MAX_OBSERVED_APIS:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    if not value:
        return ()
    if api is None or api not in value or any(not isinstance(item, str) for item in value):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    if value != sorted(set(value)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    for root in value:
        _parse_api(root, covers)
        if root.rsplit(".", 1)[-1].split("::function:")[-1].startswith("_"):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return tuple(value)


def _parse_api(value: object, covers: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value.count("::function:") != 1:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    path, name = value.split("::function:")
    if (
        covers != (path,)
        or not path.endswith(".py")
        or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", name)
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return value


def _obligation_rows(value: object, scenarios: tuple[Scenario, ...]) -> tuple[Obligation, ...]:
    if not isinstance(value, list) or not value:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    scenario_ids = {item.id for item in scenarios}
    parsed: list[Obligation] = []
    for item in value:
        row = _exact_keys(
            item,
            {"category", "id", "scenario", "selector", "target"},
            "MALFORMED_CHARACTERIZATION_MANIFEST",
        )
        if (
            not all(isinstance(row[field], str) and row[field] for field in row)
            or SCENARIO_ID.fullmatch(row["id"]) is None
            or row["category"] not in OBLIGATION_CATEGORIES
            or row["scenario"] not in scenario_ids
            or (row["selector"] != "$" and SCENARIO_ID.fullmatch(row["selector"]) is None)
            or not _valid_obligation_target(row, scenarios)
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
        parsed.append(Obligation(**row))
    identifiers = [item.id for item in parsed]
    if len(parsed) > 200 or identifiers != sorted(set(identifiers)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    if scenario_ids != {item.scenario for item in parsed}:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return tuple(parsed)


def _valid_obligation_target(row: dict[str, Any], scenarios: tuple[Scenario, ...]) -> bool:
    scenario = next(item for item in scenarios if item.id == row["scenario"])
    target = str(row["target"])
    if row["category"] == "static":
        return target == f"scenario:{scenario.id}"
    if row["category"] == "baseline":
        return (
            scenario.kind == "baseline"
            and target == f"scenario:{scenario.id}"
            and row["selector"] == "$"
        )
    path = target.split("::", 1)[0]
    try:
        normalized = contract.normalize_repository_path(path, "obligations.target")
    except contract.ContractError:
        return False
    return bool(
        normalized in scenario.covers and (row["category"] != "cli_help" or scenario.kind == "cli")
    )


def _transition_rows(value: object) -> tuple[Transition, ...]:
    if not isinstance(value, list):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    parsed: list[Transition] = []
    for item in value:
        row = _exact_keys(
            item, {"baseline_sha256", "from", "to"}, "MALFORMED_CHARACTERIZATION_MANIFEST"
        )
        targets = row["to"]
        if (
            not isinstance(row["from"], str)
            or SCENARIO_ID.fullmatch(row["from"]) is None
            or not isinstance(row["baseline_sha256"], str)
            or SHA256.fullmatch(row["baseline_sha256"]) is None
            or not isinstance(targets, list)
            or any(
                not isinstance(item, str) or SCENARIO_ID.fullmatch(item) is None for item in targets
            )
            or targets != sorted(set(targets))
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
        parsed.append(Transition(row["from"], tuple(targets), row["baseline_sha256"]))
    sources = [item.source for item in parsed]
    if sources != sorted(set(sources)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return tuple(parsed)


def _correction_ids(value: object, field: str, allowed: set[str]) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or item not in allowed for item in value)
        or value != sorted(set(value))
    ):
        raise CharacterizationError("MALFORMED_CORRECTION", field)
    return tuple(value)


def _correction_targets(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or "::" not in item for item in value)
        or value != sorted(set(value))
    ):
        raise CharacterizationError("MALFORMED_CORRECTION", "targets")
    for item in value:
        contract.normalize_repository_path(item.split("::", 1)[0], "corrections.targets")
    return tuple(value)


def _oracle_files(value: object) -> tuple[OracleFile, ...]:
    kinds = {"expected_case", "golden", "review", "source_receipt"}
    if not isinstance(value, list) or not value:
        raise CharacterizationError("MALFORMED_CORRECTION", "oracle_files")
    rows: list[OracleFile] = []
    for item in value:
        row = _exact_keys(item, {"kind", "path", "sha256"}, "MALFORMED_CORRECTION")
        try:
            path = contract.normalize_repository_path(row["path"], "corrections.oracle_files")
        except contract.ContractError as error:
            raise CharacterizationError("MALFORMED_CORRECTION", "oracle_files") from error
        if (
            row["kind"] not in kinds
            or not isinstance(row["sha256"], str)
            or SHA256.fullmatch(row["sha256"]) is None
        ):
            raise CharacterizationError("MALFORMED_CORRECTION", "oracle_files")
        rows.append(OracleFile(path, row["sha256"], row["kind"]))
    if rows != sorted(rows, key=lambda item: (item.path, item.kind)):
        raise CharacterizationError("MALFORMED_CORRECTION", "oracle_files")
    if len({item.path for item in rows}) != len(rows) or {item.kind for item in rows} != kinds:
        raise CharacterizationError("MALFORMED_CORRECTION", "oracle_files")
    return tuple(rows)


def _correction_rows(
    value: object, scenarios: tuple[Scenario, ...], obligations: tuple[Obligation, ...]
) -> tuple[Correction, ...]:
    if not isinstance(value, list):
        raise CharacterizationError("MALFORMED_CORRECTION")
    scenario_ids = {item.id for item in scenarios}
    obligation_ids = {item.id for item in obligations}
    rows: list[Correction] = []
    for item in value:
        row = _exact_keys(
            item,
            {"id", "obligations", "oracle_files", "scenarios", "targets"},
            "MALFORMED_CORRECTION",
        )
        identifier = row["id"]
        if not isinstance(identifier, str) or SCENARIO_ID.fullmatch(identifier) is None:
            raise CharacterizationError("MALFORMED_CORRECTION", "id")
        rows.append(
            Correction(
                identifier,
                _correction_ids(row["scenarios"], "scenarios", scenario_ids),
                _correction_ids(row["obligations"], "obligations", obligation_ids),
                _correction_targets(row["targets"]),
                _oracle_files(row["oracle_files"]),
            )
        )
    if rows != sorted(rows, key=lambda item: item.id) or len({item.id for item in rows}) != len(
        rows
    ):
        raise CharacterizationError("MALFORMED_CORRECTION")
    return tuple(rows)


def _observed_obligations_valid(scenario: Scenario, obligations: tuple[Obligation, ...]) -> bool:
    behavior = [
        item for item in obligations if item.scenario == scenario.id and item.category == "behavior"
    ]
    allowed = {scenario.api, *scenario.covers} if scenario.module_roots else {scenario.api}
    return (
        bool(behavior)
        and any(item.target == scenario.api for item in behavior)
        and all(item.selector == "$" and item.target in allowed for item in behavior)
    )


def parse_manifest(content: bytes, blob_sha: str) -> Manifest:
    """Parse a legacy scenario manifest or stable-obligation manifest."""
    raw = _read_json_bytes(content, "MALFORMED_CHARACTERIZATION_MANIFEST")
    if not isinstance(raw, dict):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    version = raw.get("schema_version")
    expected = (
        {"schema_version", "scenarios"}
        if version == "1.0"
        else {"schema_version", "scenarios", "obligations", "transitions"}
        | ({"corrections"} if version == "5.0" else set())
    )
    data = _exact_keys(raw, expected, "MALFORMED_CHARACTERIZATION_MANIFEST")
    scenarios = data["scenarios"]
    if version not in {"1.0", "2.0", "3.0", "4.0", "5.0"}:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    parsed = _scenario_rows(scenarios, version)
    obligations = _obligation_rows(data["obligations"], parsed) if version != "1.0" else ()
    transitions = _transition_rows(data["transitions"]) if version != "1.0" else ()
    corrections = (
        _correction_rows(data["corrections"], parsed, obligations) if version == "5.0" else ()
    )
    for scenario in parsed:
        if scenario.kind == "baseline" and [
            item.category for item in obligations if item.scenario == scenario.id
        ] != ["baseline"]:
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
        if scenario.api is not None and not _observed_obligations_valid(scenario, obligations):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return Manifest(
        parsed, blob_sha, _sha256(content), obligations, transitions, corrections, version
    )


def _manifest(
    repository: Path,
    commit_sha: str,
    records: list[git_changes.CommandRecord],
) -> Manifest:
    blob = git_changes.read_regular_blob(repository, commit_sha, MANIFEST_PATH, records)
    return parse_manifest(blob.content, blob.object_sha)


def _scenario_paths(scenario: Scenario, language: str) -> tuple[str, str]:
    extension = "py" if scenario_language(scenario, language) == "python" else "mjs"
    base = f"{SCENARIO_ROOT}/{scenario.id}"
    return f"{base}.characterization.{extension}", f"{base}.golden.json"


def scenario_language(scenario: Scenario, language: str) -> str:
    """Return the one fixed profile exercised by a characterization scenario."""
    if language != "mixed":
        return language
    profiles = {
        "python" if path.endswith((".py", ".pyi")) else "typescript"
        for path in scenario.covers
        if path.endswith(contract.SOURCE_SUFFIXES["mixed"])
    }
    if not profiles:
        return "python"
    if len(profiles) != 1:
        raise CharacterizationError("MIXED_PROFILE_CHARACTERIZATION_SCENARIO")
    return profiles.pop()


def scenario_command(scenario: Scenario, language: str) -> list[str]:
    """Return the fixed recorded command; contracts cannot select executables."""
    driver, _ = _scenario_paths(scenario, language)
    if scenario.kind == "baseline":
        return (
            ["python3.12", "-P", "/collector/baseline_observer.py", driver]
            if scenario_language(scenario, language) == "python"
            else ["node", "/collector/baseline_observer.mjs", driver]
        )
    if scenario.api is not None:
        return [
            "python3.12",
            "-P",
            "/collector/module_characterization_observer.py"
            if scenario.module_roots
            else "/collector/characterization_observer.py",
            "--api",
            scenario.api,
            "--driver",
            driver,
            *(
                ["--module-roots", _canonical(list(scenario.module_roots)).decode()]
                if scenario.module_roots
                else []
            ),
        ]
    return (
        ["python3.12", "-P", driver]
        if scenario_language(scenario, language) == "python"
        else ["node", driver]
    )


def _compiled_functions(code: CodeType) -> list[CodeType]:
    children = [value for value in code.co_consts if type(value) is CodeType]
    return [code, *(item for child in children for item in _compiled_functions(child))]


def api_source_identity(content: bytes, api: str) -> dict[str, Any]:
    """Compile exact source for identity only; never import or execute it."""
    path, name = api.split("::function:")
    try:
        code = compile(content, "/target/" + path, "exec", dont_inherit=True, optimize=0)
    except (SyntaxError, ValueError) as error:
        raise CharacterizationError("INVALID_OBSERVED_API_SOURCE") from error
    matches = [item for item in _compiled_functions(code) if item.co_qualname == name]
    if len(matches) != 1 or matches[0].co_flags & (0x20 | 0x80 | 0x200):
        raise CharacterizationError("INVALID_OBSERVED_API_SOURCE")
    start, end = _api_span(content, matches[0].co_firstlineno)
    return {
        "source_sha256": _sha256(content),
        "function_code_sha256": _sha256(marshal.dumps(matches[0], 2)),
        "start_line": start,
        "end_line": end,
    }


def _api_span(content: bytes, first_line: int) -> tuple[int, int]:
    spans = [
        (min((item.lineno for item in node.decorator_list), default=node.lineno), node.end_lineno)
        for node in ast.walk(ast.parse(content))
        if isinstance(node, ast.FunctionDef)
    ]
    matches = [(start, end) for start, end in spans if start == first_line and end is not None]
    if len(matches) != 1:
        raise CharacterizationError("INVALID_OBSERVED_API_SOURCE")
    return matches[0]


def _api_source(
    repository: Path, commit: str, api: str, records: list[git_changes.CommandRecord]
) -> dict[str, Any] | None:
    try:
        blob = git_changes.read_regular_blob(repository, commit, api.split("::", 1)[0], records)
    except git_changes.GitError as error:
        if error.code == "MISSING_BLOB":
            return None
        raise
    return api_source_identity(blob.content, api)


def _copied_source_paths(
    repository: Path, base_sha: str, head_sha: str, records: list[git_changes.CommandRecord]
) -> set[str]:
    raw = (
        git_changes.run_git(
            repository,
            (
                "diff",
                "--name-status",
                "-z",
                "--no-ext-diff",
                "--find-renames=50%",
                "--find-copies=50%",
                "--find-copies-harder",
                base_sha,
                head_sha,
                "--",
            ),
            records,
        )
        .decode("utf-8")
        .split("\0")
    )
    copied: set[str] = set()
    index = 0
    while index < len(raw) - 1:
        status = raw[index]
        count = 3 if status.startswith(("C", "R")) else 2
        if count == 3:
            copied.add(raw[index + 2])
        index += count
    return copied


def _production_function_names(path: str, content: bytes) -> set[str] | None:
    from supportability_gate import function_changes

    try:
        parsed = (
            function_changes.parse_python_file(path, content)
            if path.endswith((".py", ".pyi"))
            else function_changes.parse_typescript_file(path, content)
        )
    except function_changes.PythonSourceError:
        return None
    return {item.span.qualified_name for item in parsed.functions}


def _retired_production_targets(
    repository: Path,
    base_sha: str,
    head_sha: str,
    policy: contract.Contract,
    changes: tuple[git_changes.ChangedPath, ...],
    records: list[git_changes.CommandRecord],
) -> bool:
    for change in changes:
        path = change.old_path
        if path is None or not policy.is_production_path(path):
            continue
        if change.new_path != path:
            return True
        if not path.endswith(
            (".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
        ):
            continue
        base = _production_function_names(
            path, git_changes.read_regular_blob(repository, base_sha, path, records).content
        )
        head = _production_function_names(
            path, git_changes.read_regular_blob(repository, head_sha, path, records).content
        )
        if base is None or head is None or base - head:
            return True
    return False


def _api_bindings(
    repository: Path,
    base_sha: str,
    head_sha: str,
    policy: contract.Contract,
    manifest: Manifest,
    changes: tuple[git_changes.ChangedPath, ...],
    records: list[git_changes.CommandRecord],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    observed = [item for item in manifest.scenarios if item.api is not None]
    copied = _copied_source_paths(repository, base_sha, head_sha, records) if observed else set()
    added = {item.new_path for item in changes if item.status == "ADDED" and item.old_path is None}
    retired = (
        _retired_production_targets(repository, base_sha, head_sha, policy, changes, records)
        if observed
        else False
    )
    facts: dict[str, dict[str, Any]] = {}
    blocks: list[str] = []
    for item in observed:
        assert item.api is not None
        base = _api_source(repository, base_sha, item.api, records)
        head = _api_source(repository, head_sha, item.api, records)
        path = item.covers[0]
        admissible = head is not None and policy.is_production_path(path)
        if base is None:
            admissible = admissible and path in added and path not in copied and not retired
        driver_path, oracle_path = _scenario_paths(item, policy.language)
        facts[item.id] = {
            "api": item.api,
            "scenario": item.id,
            "base_absent": base is None,
            "base_source": base,
            "head_source": head,
            "driver_sha256": _sha256(
                git_changes.read_regular_blob(repository, head_sha, driver_path, records).content
            ),
            "oracle_sha256": _sha256(
                git_changes.read_regular_blob(repository, head_sha, oracle_path, records).content
            ),
            "admissible": admissible,
        }
        if item.module_roots:
            facts[item.id]["module"] = _module_binding(
                repository, base_sha, head_sha, manifest, item, records
            )
        facts[item.id].update(
            _api_oracle_review(repository, head_sha, item, facts[item.id], records)
        )
        if not admissible:
            blocks.append(f"INVALID_API_INTRODUCTION:{item.id}")
        if not facts[item.id]["oracle_review_valid"]:
            blocks.append(f"INVALID_API_ORACLE_REVIEW:{item.id}")
    return facts, blocks


def _module_binding(
    repository: Path,
    base_sha: str,
    head_sha: str,
    manifest: Manifest,
    scenario: Scenario,
    records: list[git_changes.CommandRecord],
) -> dict[str, Any]:
    path = scenario.covers[0]
    inventories: dict[str, object] = {}
    for side, sha in (("base", base_sha), ("head", head_sha)):
        try:
            source = git_changes.read_regular_blob(repository, sha, path, records).content
        except git_changes.GitError as error:
            if side != "base" or error.code != "MISSING_BLOB":
                raise
            inventories[side + "_inventory"] = None
            continue
        try:
            inventory = module_source_inventory(source, path)
            module_public_roots(
                list(scenario.module_roots),
                str(scenario.api),
                {row["name"]: row for row in inventory["functions"]},
            )
        except ModuleObservationError as error:
            raise CharacterizationError("INVALID_MODULE_SOURCE", str(error)) from error
        inventories[side + "_inventory"] = inventory
    oracle_path = f"{SCENARIO_ROOT}/{scenario.id}.module.golden.json"
    oracle, cases = _module_oracle_cases(repository, head_sha, oracle_path, records)
    if oracle is None or cases is None:
        raise CharacterizationError("INVALID_MODULE_ORACLE")
    correction_base_path = _correction_base_module_oracle_path(manifest, scenario)
    _, base_cases = _module_oracle_cases(
        repository,
        head_sha if correction_base_path is not None else base_sha,
        correction_base_path or oracle_path,
        records,
        missing_allowed=correction_base_path is None,
    )
    return {
        "roots": list(scenario.module_roots),
        "metric": MODULE_BODY_METRIC,
        **inventories,
        "oracle_sha256": _sha256(oracle),
        "oracle_cases": cases,
        # Verification-only input. It is removed before result serialization.
        "_base_oracle_cases": base_cases,
    }


def _correction_base_module_oracle_path(manifest: Manifest, scenario: Scenario) -> str | None:
    path = f"{SCENARIO_ROOT}/{scenario.id}.base.module.golden.json"
    return (
        path
        if any(
            scenario.id in correction.scenarios
            and any(item.path == path and item.kind == "golden" for item in correction.oracle_files)
            for correction in manifest.corrections
        )
        else None
    )


def _module_oracle_cases(
    repository: Path,
    commit_sha: str,
    path: str,
    records: list[git_changes.CommandRecord],
    *,
    missing_allowed: bool = False,
) -> tuple[bytes | None, list[object] | None]:
    try:
        content = git_changes.read_regular_blob(repository, commit_sha, path, records).content
    except git_changes.GitError as error:
        if not missing_allowed or error.code != "MISSING_BLOB":
            raise
        return None, None
    cases = _read_module_json(content, "INVALID_MODULE_ORACLE")
    if not isinstance(cases, list) or not 2 <= len(cases) <= MODULE_MAX_EXECUTIONS:
        raise CharacterizationError("INVALID_MODULE_ORACLE")
    return content, cases


def _api_review_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in pairs:
        if key in row:
            raise CharacterizationError("MALFORMED_API_ORACLE_REVIEW")
        row[key] = value
    return row


def _read_api_review(content: bytes) -> Any:
    if not content or len(content) > MAX_JSON_BYTES:
        raise CharacterizationError("MALFORMED_API_ORACLE_REVIEW")
    try:
        return json.loads(content, object_pairs_hook=_api_review_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CharacterizationError("MALFORMED_API_ORACLE_REVIEW") from error


def _api_oracle_review(
    repository: Path,
    head_sha: str,
    scenario: Scenario,
    fact: dict[str, Any],
    records: list[git_changes.CommandRecord],
) -> dict[str, Any]:
    path = f"{SCENARIO_ROOT}/{scenario.id}.review.json"
    try:
        blob = git_changes.read_regular_blob(repository, head_sha, path, records)
        module = fact.get("module")
        row = _exact_keys(
            _read_api_review(blob.content),
            {
                "schema_version",
                "api",
                "source_sha256",
                "driver_sha256",
                "oracle_sha256",
                "intended_feature",
                "reviewer",
                "verdict",
            }
            | (
                {"module_roots", "module_inventory_sha256", "module_oracle_sha256"}
                if module is not None
                else set()
            ),
            "MALFORMED_API_ORACLE_REVIEW",
        )
    except (git_changes.GitError, CharacterizationError):
        return {"review_sha256": None, "intended_feature": None, "oracle_review_valid": False}
    valid = _valid_api_oracle_review(row, fact) and _module_review_matches(row, fact)
    return {
        "review_sha256": _sha256(blob.content),
        "intended_feature": row["intended_feature"] if valid else None,
        "oracle_review_valid": valid,
    }


def _valid_api_oracle_review(row: dict[str, Any], fact: dict[str, Any]) -> bool:
    source = fact["head_source"]
    return bool(
        row["schema_version"] == ("2.0" if "module" in fact else "1.0")
        and row["api"] == fact["api"]
        and row["verdict"] == "ACCEPTED"
        and isinstance(row["reviewer"], str)
        and row["reviewer"].strip()
        and isinstance(row["intended_feature"], str)
        and 0 < len(row["intended_feature"].strip()) <= 2000
        and isinstance(row["source_sha256"], str)
        and SHA256.fullmatch(row["source_sha256"])
        and row["driver_sha256"] == fact["driver_sha256"]
        and row["oracle_sha256"] == fact["oracle_sha256"]
        and (
            not fact["base_absent"]
            or (isinstance(source, dict) and row["source_sha256"] == source["source_sha256"])
        )
    )


def _module_review_matches(row: dict[str, Any], fact: dict[str, Any]) -> bool:
    module = fact.get("module")
    return module is None or bool(
        row["module_roots"] == module["roots"]
        and row["module_inventory_sha256"] == module["head_inventory"]["inventory_sha256"]
        and row["module_oracle_sha256"] == module["oracle_sha256"]
        and row["source_sha256"] == fact["head_source"]["source_sha256"]
    )


def _api_capture_matches(row: dict[str, Any] | None, fact: dict[str, Any], side: str) -> bool:
    if row is None:
        return False
    observation = row.get("api_observation")
    if side == "base" and fact["base_absent"]:
        return (
            observation == {"api": fact["api"], "state": "ABSENT"}
            and row.get("behavior") == {"api_absent": fact["api"]}
            and row.get("command") is None
        )
    source = fact[f"{side}_source"]
    if not isinstance(source, dict) or not isinstance(observation, dict):
        return False
    module_valid = _module_capture_matches(row, fact, side)
    if "module" in fact:
        observation = {key: value for key, value in observation.items() if key != "module_witness"}
    return bool(
        set(observation)
        == {
            "schema_version",
            "codec",
            "api",
            "source_sha256",
            "function_code_sha256",
            "start_line",
            "end_line",
            "cases",
        }
        and observation["schema_version"] == "1.0"
        and observation["codec"] == (MODULE_CODEC if "module" in fact else "python-values-v1")
        and observation["api"] == fact["api"]
        and all(observation[key] == value for key, value in source.items())
        and type(observation["start_line"]) is int
        and type(observation["end_line"]) is int
        and 1 <= observation["start_line"] <= observation["end_line"]
        and observation["cases"] == row.get("behavior")
        and _meaningful_cases(observation["cases"])
        and row.get("exit_code") == 0
        and row.get("error") is None
        and row.get("deterministic") is True
        and module_valid
    )


def _module_capture_matches(row: dict[str, Any], fact: dict[str, Any], side: str) -> bool:
    module = fact.get("module")
    if module is None:
        return "module_witness" not in row.get("api_observation", {})
    observation = row.get("api_observation", {})
    witness = observation.get("module_witness")
    if not isinstance(witness, dict) or witness.get("roots") != module["roots"]:
        return False
    primary = {key: value for key, value in observation.items() if key != "module_witness"}
    if witness.get("primary") != primary:
        return False
    try:
        expected_root_cases = (
            module.get("_base_oracle_cases")
            if side == "base" and module.get("_base_oracle_cases") is not None
            else module["oracle_cases"]
        )
        verify_module_inventory_observation(
            module[side + "_inventory"],
            fact["api"].split("::", 1)[0],
            fact["api"],
            witness,
            row.get("behavior"),
            expected_root_cases,
        )
    except (ModuleObservationError, TypeError, ValueError, KeyError):
        return False
    return True


def _load_capture(
    path: Path,
    missing_code: str,
    *,
    module: bool = False,
    aggregate_schema: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if module and aggregate_schema is None:
            aggregate_schema = MODULE_CAPTURE_SCHEMA
        if aggregate_schema is not None:
            with path.open("rb") as stream:
                content = stream.read(MODULE_AGGREGATE_JSON_BYTES + 1)
            value = _read_module_aggregate(
                content, "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE", aggregate_schema
            )
        else:
            content = path.read_bytes()
            value = _read_json_bytes(content, "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE")
    except FileNotFoundError:
        return None, missing_code
    except (OSError, CharacterizationError):
        return None, "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"
    if not isinstance(value, dict):
        return None, "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"
    value["_capture_sha256"] = _sha256(content)
    return value, None


def _capture_digest_blocks(
    base: dict[str, Any] | None,
    head: dict[str, Any] | None,
    base_sha256: str,
    head_sha256: str,
) -> list[str]:
    blocks: list[str] = []
    if base is not None and base.get("_capture_sha256") != base_sha256:
        blocks.append("BASE_CAPTURE_DIGEST_MISMATCH")
    if head is not None and head.get("_capture_sha256") != head_sha256:
        blocks.append("HEAD_CAPTURE_DIGEST_MISMATCH")
    return blocks


def _authentication_blocks(
    artifact: dict[str, Any],
    expected: dict[str, str],
) -> list[str]:
    expected_keys = {
        "_capture_sha256",
        "authentication",
        "behavior_fingerprint",
        "definition_sha",
        "language",
        "manifest",
        "scenarios",
        "schema_version",
        "target_sha",
    }
    if set(artifact) != expected_keys or artifact.get("schema_version") not in {
        CAPTURE_SCHEMA,
        OBSERVED_CAPTURE_SCHEMA,
        MODULE_CAPTURE_SCHEMA,
        CORRECTION_CAPTURE_SCHEMA,
    }:
        return ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"]
    authentication = artifact.get("authentication")
    if not isinstance(authentication, dict) or authentication != expected:
        return ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"]
    return []


def _valid_environment(value: object) -> bool:
    keys = {
        "container_digest",
        "container_id",
        "container_image",
        "node_runtime",
        "python_runtime",
        "resolved_dependencies",
    }
    if not isinstance(value, dict) or set(value) != keys:
        return False
    dependencies = value["resolved_dependencies"]
    return bool(
        isinstance(value["container_image"], str)
        and isinstance(value["container_digest"], str)
        and value["container_image"].endswith("@" + value["container_digest"])
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["container_digest"])
        and isinstance(value["container_id"], str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value["container_id"])
        and isinstance(value["python_runtime"], str)
        and re.fullmatch(r".+:sha256:[0-9a-f]{64}", value["python_runtime"])
        and isinstance(value["node_runtime"], str)
        and (
            not value["node_runtime"]
            or re.fullmatch(r".+:sha256:[0-9a-f]{64}", value["node_runtime"])
        )
        and isinstance(dependencies, list)
        and all(isinstance(item, str) and item for item in dependencies)
        and dependencies == sorted(set(dependencies))
    )


def _provenance_path(capture_path: Path) -> Path:
    return capture_path.with_name(f"{capture_path.stem}-provenance.json")


def _load_environment_provenance(
    capture_path: Path,
    capture: dict[str, Any] | None,
    expected: dict[str, str],
) -> tuple[dict[str, Any] | None, list[str]]:
    if capture is None:
        return None, []
    try:
        value = _read_json_bytes(
            _provenance_path(capture_path).read_bytes(),
            "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE",
        )
    except (FileNotFoundError, OSError, CharacterizationError):
        return None, ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"]
    if not isinstance(value, dict) or set(value) != {
        "authentication",
        "capture_sha256",
        "environment",
        "schema_version",
    }:
        return None, ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"]
    environment = value["environment"]
    if (
        value["schema_version"] != PROVENANCE_SCHEMA
        or value["authentication"] != expected
        or value["capture_sha256"] != capture.get("_capture_sha256")
        or not _valid_environment(environment)
    ):
        return None, ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"]
    assert isinstance(environment, dict)
    return environment, []


def _stable_environment(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "container_id"}


def _effective_obligations(manifest: Manifest) -> tuple[Obligation, ...]:
    if manifest.schema_version in {"2.0", "3.0", "4.0", "5.0"}:
        return manifest.obligations
    return tuple(
        Obligation(item.id, "static", item.id, "$", f"scenario:{item.id}")
        for item in manifest.scenarios
    )


def _selected_assertion(behavior: object, selector: str) -> object:
    if selector == "$":
        return behavior
    if not isinstance(behavior, dict) or selector not in behavior:
        raise CharacterizationError("MISSING_CHARACTERIZATION_ASSERTION", selector)
    return behavior[selector]


def _golden_assertions(
    repository: Path,
    commit_sha: str,
    language: str,
    manifest: Manifest,
    records: list[git_changes.CommandRecord],
) -> dict[str, tuple[Obligation, str]]:
    scenarios = {item.id: item for item in manifest.scenarios}
    values: dict[str, tuple[Obligation, str]] = {}
    for obligation in _effective_obligations(manifest):
        _, path = _scenario_paths(scenarios[obligation.scenario], language)
        blob = git_changes.read_regular_blob(repository, commit_sha, path, records)
        behavior = _read_json_bytes(blob.content, "MALFORMED_GOLDEN_OUTPUT")
        selected = _selected_assertion(behavior, obligation.selector)
        values[obligation.id] = (obligation, _sha256(_canonical(selected)))
    return values


def _transition_allows(
    transition: Transition | None,
    obligation: Obligation,
    baseline_sha256: str,
    head_assertions: dict[str, tuple[Obligation, str]],
) -> bool:
    if transition is None or transition.baseline_sha256 != baseline_sha256:
        return False
    if transition.targets:
        return all(
            target in head_assertions
            and head_assertions[target][0].category == obligation.category
            and head_assertions[target][0].target == obligation.target
            for target in transition.targets
        )
    return False


def _removed_obligation_blocks(
    base: Manifest,
    head: Manifest,
    base_assertions: dict[str, tuple[Obligation, str]],
    head_assertions: dict[str, tuple[Obligation, str]],
    deleted_paths: set[str],
) -> list[str]:
    base_scenarios = {item.id: item for item in base.scenarios}
    head_covered = {path for item in head.scenarios for path in item.covers}
    transitions = {item.source: item for item in head.transitions}
    blocks: list[str] = []
    for identifier in sorted(set(base_assertions) - set(head_assertions)):
        obligation, digest = base_assertions[identifier]
        scenario = base_scenarios[obligation.scenario]
        source_deleted = bool(set(scenario.covers) & deleted_paths) and all(
            path in deleted_paths or path in head_covered for path in scenario.covers
        )
        allowed = _transition_allows(
            transitions.get(identifier), obligation, digest, head_assertions
        )
        if source_deleted or allowed:
            continue
        suffix = identifier if head.schema_version == "1.0" else f"obligation:{identifier}"
        blocks.append(f"REMOVED_CHARACTERIZATION_SCENARIO:{suffix}")
    return blocks


def _common_obligation_blocks(
    base: Manifest,
    head: Manifest,
    base_assertions: dict[str, tuple[Obligation, str]],
    head_assertions: dict[str, tuple[Obligation, str]],
) -> list[str]:
    blocks: list[str] = []
    for identifier in sorted(set(base_assertions) & set(head_assertions)):
        base_obligation, base_digest = base_assertions[identifier]
        head_obligation, head_digest = head_assertions[identifier]
        if (base_obligation.category, base_obligation.target) != (
            head_obligation.category,
            head_obligation.target,
        ):
            blocks.append(f"CHANGED_CHARACTERIZATION_DEFINITION:obligation:{identifier}")
        if base_digest != head_digest:
            suffix = identifier if head.schema_version == "1.0" else f"obligation:{identifier}"
            blocks.append(f"CHANGED_GOLDEN_OUTPUT:{suffix}")
    return blocks


def _transition_blocks(
    base_assertions: dict[str, tuple[Obligation, str]],
    head_assertions: dict[str, tuple[Obligation, str]],
    transitions: tuple[Transition, ...],
) -> list[str]:
    removed = set(base_assertions) - set(head_assertions)
    return [
        f"CHANGED_CHARACTERIZATION_DEFINITION:transition:{item.source}"
        for item in transitions
        if item.source not in removed
    ]


def _legacy_driver_blocks(
    repository: Path,
    base_sha: str,
    head_sha: str,
    language: str,
    base: Manifest,
    head: Manifest,
    records: list[git_changes.CommandRecord],
) -> list[str]:
    if base.schema_version != "1.0" or head.schema_version != "1.0":
        return []
    base_by_id = {item.id: item for item in base.scenarios}
    head_by_id = {item.id: item for item in head.scenarios}
    blocks: list[str] = []
    for identifier in sorted(set(base_by_id) & set(head_by_id)):
        if base_by_id[identifier] != head_by_id[identifier]:
            blocks.append(f"CHANGED_CHARACTERIZATION_DEFINITION:{identifier}")
            continue
        driver_path, _ = _scenario_paths(head_by_id[identifier], language)
        base_driver = git_changes.read_regular_blob(repository, base_sha, driver_path, records)
        head_driver = git_changes.read_regular_blob(repository, head_sha, driver_path, records)
        if base_driver.object_sha != head_driver.object_sha:
            blocks.append(f"CHANGED_CHARACTERIZATION_DEFINITION:{identifier}")
    return blocks


def _definition_blocks(
    repository: Path,
    base_sha: str,
    head_sha: str,
    language: str,
    head: Manifest,
    deleted_paths: set[str],
    records: list[git_changes.CommandRecord],
) -> list[str]:
    try:
        base = _manifest(repository, base_sha, records)
    except git_changes.GitError as error:
        if error.code == "MISSING_BLOB":
            return []
        raise
    base_assertions = _golden_assertions(repository, base_sha, language, base, records)
    head_assertions = _golden_assertions(repository, head_sha, language, head, records)
    return [
        *_removed_obligation_blocks(base, head, base_assertions, head_assertions, deleted_paths),
        *_common_obligation_blocks(base, head, base_assertions, head_assertions),
        *_transition_blocks(base_assertions, head_assertions, head.transitions),
        *_legacy_driver_blocks(repository, base_sha, head_sha, language, base, head, records),
        *_observed_definition_blocks(repository, base_sha, head_sha, language, base, head, records),
    ]


def _observed_definition_blocks(
    repository: Path,
    base_sha: str,
    head_sha: str,
    language: str,
    base: Manifest,
    head: Manifest,
    records: list[git_changes.CommandRecord],
) -> list[str]:
    previous = {item.id: item for item in base.scenarios}
    blocks: list[str] = []
    for item in head.scenarios:
        old = previous.get(item.id)
        if old is None:
            continue
        if old.kind == "baseline" and (item.kind != old.kind or item.covers != old.covers):
            blocks.append(f"CHARACTERIZATION_DEFINITION_MISMATCH:{item.id}")
        if old.api != item.api or old.module_roots != item.module_roots:
            blocks.append(f"CHANGED_CHARACTERIZATION_DEFINITION:{item.id}")
        elif item.api is not None:
            path, _ = _scenario_paths(item, language)
            before = git_changes.read_regular_blob(repository, base_sha, path, records)
            after = git_changes.read_regular_blob(repository, head_sha, path, records)
            if before.content != after.content:
                blocks.append(f"CHANGED_CHARACTERIZATION_DEFINITION:{item.id}")
            review_path = f"{SCENARIO_ROOT}/{item.id}.review.json"
            before_review = git_changes.read_regular_blob(
                repository, base_sha, review_path, records
            )
            after_review = git_changes.read_regular_blob(repository, head_sha, review_path, records)
            if before_review.content != after_review.content:
                blocks.append(f"CHANGED_CHARACTERIZATION_DEFINITION:review:{item.id}")
    return blocks


def _capture_blocks(
    artifact: dict[str, Any],
    manifest: Manifest,
    language: str,
    side: str,
    absent_scenarios: frozenset[str] = frozenset(),
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    blocks: list[str] = []
    rows = artifact.get("scenarios")
    if (
        artifact.get("schema_version") != capture_schema(manifest.schema_version)
        or artifact.get("language") != language
        or artifact.get("manifest") != _manifest_payload(manifest)
        or not isinstance(rows, list)
    ):
        return ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"], {}
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            by_id[row["id"]] = row
    if set(by_id) != {item.id for item in manifest.scenarios} or len(by_id) != len(rows):
        return ["INCOMPLETE_CHARACTERIZATION_EVIDENCE"], {}
    for scenario in manifest.scenarios:
        row = by_id[scenario.id]
        blocks.extend(
            _scenario_row_blocks(
                scenario,
                row,
                scenario.id in absent_scenarios,
                correction_base=manifest.schema_version == "5.0" and side == "base",
            )
        )
    blocks.extend(
        _obligation_capture_blocks(
            manifest,
            by_id,
            absent_scenarios,
            correction_base=manifest.schema_version == "5.0" and side == "base",
        )
    )
    expected_fingerprint = _sha256(
        _canonical(
            [[item.id, by_id[item.id].get("behavior_sha256")] for item in manifest.scenarios]
        )
    )
    if artifact.get("behavior_fingerprint") != expected_fingerprint:
        blocks.append("CHARACTERIZATION_FINGERPRINT_MISMATCH")
    return blocks, by_id


def _scenario_row_blocks(
    scenario: Scenario,
    row: dict[str, Any],
    absent: bool = False,
    *,
    correction_base: bool = False,
) -> list[str]:
    blocks: list[str] = []
    if row.get("kind") != scenario.kind or row.get("covers") != list(scenario.covers):
        blocks.append(f"CHARACTERIZATION_DEFINITION_MISMATCH:{scenario.id}")
    if row.get("exit_code") != 0 or row.get("error") is not None:
        blocks.append(f"CHARACTERIZATION_EXECUTION_FAILED:{scenario.id}")
    if row.get("deterministic") is not True:
        blocks.append(f"CHARACTERIZATION_REPLAY_DRIFT:{scenario.id}")
    if (
        not absent
        and not correction_base
        and row.get("behavior_sha256") != row.get("golden_behavior_sha256")
    ):
        blocks.append(f"GOLDEN_BEHAVIOR_MISMATCH:{scenario.id}")
    return blocks


def _meaningful_cases(value: object) -> bool:
    if not isinstance(value, list) or len(value) < 2:
        return False
    if any(not isinstance(item, dict) or set(item) != {"input", "output"} for item in value):
        return False
    inputs = [_canonical(item["input"]) for item in value]
    outputs = [_canonical(item["output"]) for item in value]
    return len(inputs) == len(set(inputs)) and len(set(outputs)) > 1


def _valid_obligation_assertion(category: str, value: object, paths: tuple[str, ...] = ()) -> bool:
    if category == "baseline":
        return baseline_evidence.meaningful(value, paths)
    if category == "behavior":
        return _meaningful_cases(value)
    if category == "cli_help":
        return isinstance(value, dict) and set(value) == {
            "exit_code",
            "stderr_sha256",
            "stdout_sha256",
        }
    return value is not None


def _obligation_capture_blocks(
    manifest: Manifest,
    rows: dict[str, dict[str, Any]],
    absent_scenarios: frozenset[str] = frozenset(),
    *,
    correction_base: bool = False,
) -> list[str]:
    blocks: list[str] = []
    for obligation in manifest.obligations:
        if obligation.scenario in absent_scenarios:
            continue
        row = rows.get(obligation.scenario, {})
        try:
            selected = _selected_assertion(row.get("behavior"), obligation.selector)
        except CharacterizationError:
            blocks.append(f"GOLDEN_BEHAVIOR_MISMATCH:obligation:{obligation.id}")
            continue
        if not correction_base and not _valid_obligation_assertion(
            obligation.category, selected, tuple(row.get("covers", []))
        ):
            blocks.append(f"GOLDEN_BEHAVIOR_MISMATCH:obligation:{obligation.id}")
    return blocks


def _artifact_identity_blocks(
    repository: Path,
    head_sha: str,
    language: str,
    manifest: Manifest,
    rows: dict[str, dict[str, Any]],
    records: list[git_changes.CommandRecord],
) -> list[str]:
    blocks: list[str] = []
    for scenario in manifest.scenarios:
        row = rows.get(scenario.id)
        if row is None:
            continue
        driver_path, golden_path = _scenario_paths(scenario, language)
        driver = git_changes.read_regular_blob(repository, head_sha, driver_path, records)
        golden = git_changes.read_regular_blob(repository, head_sha, golden_path, records)
        if row.get("driver_blob_sha") != driver.object_sha:
            blocks.append(f"CHARACTERIZATION_DRIVER_IDENTITY_MISMATCH:{scenario.id}")
        if row.get("golden_blob_sha") != golden.object_sha:
            blocks.append(f"GOLDEN_ARTIFACT_IDENTITY_MISMATCH:{scenario.id}")
    return blocks


def _coverage_blocks(
    policy: contract.Contract,
    manifest: Manifest,
    changes: tuple[git_changes.ChangedPath, ...],
    deleted_paths: set[str],
    responsibility_targets: tuple[str, ...],
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    changed = {
        item.new_path
        for item in changes
        if item.new_path and policy.is_production_path(item.new_path)
    }
    required = derive_required_paths(changed, set(policy.high_risk_paths), deleted_paths)
    covered = sorted({path for item in manifest.scenarios for path in item.covers})
    blocks = [
        f"MISSING_CHARACTERIZATION_COVERAGE:{path}" for path in required if path not in covered
    ]
    required_obligations = sorted(
        {
            target.rsplit(":", 1)[0]
            for target in responsibility_targets
            if "::" in target and target.split("::", 1)[1].split(":", 1)[0] != "module"
        }
    )
    behavior_targets = {item.target for item in manifest.obligations if item.category == "behavior"}
    behavior_targets.update(
        path for item in manifest.scenarios if item.kind == "baseline" for path in item.covers
    )
    covered_obligations = sorted(
        target
        for target in required_obligations
        if target in behavior_targets or target.split("::", 1)[0] in behavior_targets
    )
    blocks.extend(
        f"MISSING_CHARACTERIZATION_COVERAGE:obligation:{target}"
        for target in required_obligations
        if target not in covered_obligations
    )
    return blocks, required, covered, required_obligations, covered_obligations


def derive_required_paths(
    changed_paths: set[str], high_risk_paths: set[str], deleted_paths: set[str]
) -> list[str]:
    """Return canonical changed and retained high-risk characterization coverage."""
    return sorted(changed_paths | (high_risk_paths - deleted_paths))


def baseline_sources(
    repository: Path, commit: str, scenario: Scenario, records: list[git_changes.CommandRecord]
) -> dict[str, bytes]:
    """Read the exact explicit inventory, never target-selected filesystem paths."""
    entries = git_changes.list_regular_blobs(repository, commit, scenario.covers, records)
    if not entries:
        return {}
    if {item.path for item in entries} != set(scenario.covers):
        raise CharacterizationError("CHARACTERIZATION_DEFINITION_MISMATCH", scenario.id)
    return {
        path: blob.content
        for path, blob in git_changes.read_regular_blobs(
            repository, commit, scenario.covers, records
        )
    }


def _baseline_absence(row: dict[str, Any] | None, obligation: Obligation) -> bool:
    return bool(
        row
        and obligation.category == "baseline"
        and row.get("command") is None
        and row.get("behavior") == {"absent": row.get("covers")}
    )


def _baseline_row_valid(
    scenario: Scenario, row: dict[str, Any], sources: dict[str, bytes], language: str
) -> bool:
    if row.get("behavior_sha256") != _sha256(_canonical(row.get("behavior"))):
        return False
    if not sources:
        return (
            row.get("command") is None
            and row.get("behavior") == {"absent": list(scenario.covers)}
            and row.get("baseline_witness") == {}
        )
    actual = row.get("behavior")
    if not isinstance(actual, dict) or actual != baseline_evidence.behavior(
        sources, actual.get("cases")
    ):
        return False
    return row.get("command") == scenario_command(
        scenario, language
    ) and baseline_evidence.execution_verified(sources, row.get("baseline_witness"))


def _baseline_birth_valid(
    repository: Path,
    base: str,
    head: str,
    policy: contract.Contract,
    manifest: Manifest,
    scenario: Scenario,
    records: list[git_changes.CommandRecord],
) -> bool:
    if git_changes.list_regular_blobs(repository, base, policy.production_paths, records):
        return False
    changes = git_changes.changed_paths(repository, base, head, records)
    if any(change.new_path in scenario.covers and change.status != "ADDED" for change in changes):
        return False
    if set(scenario.covers) & _copied_source_paths(repository, base, head, records):
        return False
    required = set(_scenario_paths(scenario, policy.language))
    return any(
        scenario.id in item.scenarios and required.issubset({f.path for f in item.oracle_files})
        for item in manifest.corrections
    )


def _baseline_capture_blocks(
    repository: Path,
    target: str,
    base: str,
    policy: contract.Contract,
    manifest: Manifest,
    artifact: dict[str, Any],
    records: list[git_changes.CommandRecord],
) -> tuple[list[str], frozenset[str]]:
    blocks: list[str] = []
    absent: set[str] = set()
    baseline_scenarios = [item for item in manifest.scenarios if item.kind == "baseline"]
    if not baseline_scenarios:
        return blocks, frozenset()
    if not isinstance(artifact.get("scenarios"), list):
        return ["UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"], frozenset()
    rows = {row.get("id"): row for row in artifact.get("scenarios", []) if isinstance(row, dict)}
    for scenario in baseline_scenarios:
        try:
            sources = baseline_sources(repository, target, scenario, records)
            valid = all(policy.is_production_path(path) for path in scenario.covers)
            if not sources:
                absent.add(scenario.id)
                valid = (
                    valid
                    and target == base
                    and _baseline_birth_valid(
                        repository,
                        base,
                        str(artifact.get("definition_sha", "")),
                        policy,
                        manifest,
                        scenario,
                        records,
                    )
                )
            valid = valid and _baseline_row_valid(
                scenario, rows.get(scenario.id, {}), sources, policy.language
            )
        except (git_changes.GitError, CharacterizationError):
            valid = False
        if not valid:
            blocks.append(f"CHARACTERIZATION_EXECUTION_FAILED:{scenario.id}")
    return blocks, frozenset(absent)


def _verified_capture_rows(
    artifact: dict[str, Any] | None,
    side: str,
    expected_common: dict[str, str],
    target_sha: str,
    head_sha: str,
    repository: Path,
    policy: contract.Contract,
    manifest: Manifest,
    records: list[git_changes.CommandRecord],
    api_facts: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if artifact is None:
        return [], {}
    expected = {**expected_common, "job": f"characterize-{side}", "side": side}
    blocks = _authentication_blocks(artifact, expected)
    if artifact.get("target_sha") != target_sha or artifact.get("definition_sha") != head_sha:
        blocks.append(f"STALE_{'BASELINE' if side == 'base' else 'POST_CHANGE'}_ARTIFACT")
    facts = api_facts or {}
    absent = frozenset(
        key for key, value in facts.items() if side == "base" and value["base_absent"]
    )
    baseline_blocks, baseline_absent = _baseline_capture_blocks(
        repository, target_sha, expected_common["base_sha"], policy, manifest, artifact, records
    )
    blocks.extend(baseline_blocks)
    absent |= baseline_absent
    capture_blocks, rows = _capture_blocks(artifact, manifest, policy.language, side, absent)
    blocks.extend(capture_blocks)
    blocks.extend(
        _artifact_identity_blocks(repository, head_sha, policy.language, manifest, rows, records)
    )
    blocks.extend(
        f"MISSING_API_EXECUTION:{key}"
        for key, fact in facts.items()
        if not _api_capture_matches(rows.get(key), fact, side)
    )
    return blocks, rows


def _compatibility_evidence(
    manifest: Manifest,
    base_rows: dict[str, dict[str, Any]],
    head_rows: dict[str, dict[str, Any]],
    api_facts: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], list[dict[str, object]]]:
    blocks: list[str] = []
    scenarios: list[dict[str, object]] = []
    for item in manifest.scenarios:
        base_row, head_row = base_rows.get(item.id), head_rows.get(item.id)
        base_behavior = base_row.get("behavior_sha256") if base_row else None
        head_behavior = head_row.get("behavior_sha256") if head_row else None
        compatible = bool(
            base_row and head_row and base_behavior is not None and base_behavior == head_behavior
        )
        fact = (api_facts or {}).get(item.id)
        if fact is not None and fact["base_absent"]:
            compatible = bool(
                fact["admissible"]
                and fact["oracle_review_valid"]
                and _api_capture_matches(base_row, fact, "base")
                and _api_capture_matches(head_row, fact, "head")
            )
        if base_behavior is not None and head_behavior is not None and not compatible:
            blocks.append(f"INCOMPATIBLE_POST_CHANGE_BEHAVIOR:{item.id}")
        scenarios.append(
            {
                "base_behavior_sha256": base_behavior,
                "command": head_row.get("command") if head_row else None,
                "compatibility": "PASS" if compatible else "BLOCK",
                "covers": list(item.covers),
                "golden_behavior_sha256": (
                    head_row.get("golden_behavior_sha256") if head_row else None
                ),
                "head_behavior_sha256": head_behavior,
                "id": item.id,
                "kind": item.kind,
            }
        )
    return blocks, scenarios


def _assertion_sha(row: dict[str, Any] | None, obligation: Obligation) -> str | None:
    if row is None:
        return None
    try:
        selected = _selected_assertion(row.get("behavior"), obligation.selector)
    except CharacterizationError:
        return None
    return _sha256(_canonical(selected))


def _obligation_evidence(
    manifest: Manifest,
    base_rows: dict[str, dict[str, Any]],
    head_rows: dict[str, dict[str, Any]],
    api_facts: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], list[dict[str, object]]]:
    blocks: list[str] = []
    rows: list[dict[str, object]] = []
    for item in manifest.obligations:
        base = _assertion_sha(base_rows.get(item.scenario), item)
        head = _assertion_sha(head_rows.get(item.scenario), item)
        compatible = base is not None and base == head
        fact = (api_facts or {}).get(item.scenario)
        if fact is not None and fact["base_absent"]:
            compatible = bool(
                fact["admissible"]
                and fact["oracle_review_valid"]
                and _api_capture_matches(base_rows.get(item.scenario), fact, "base")
                and _api_capture_matches(head_rows.get(item.scenario), fact, "head")
            )
        if base is not None and head is not None and not compatible:
            blocks.append(f"INCOMPATIBLE_POST_CHANGE_BEHAVIOR:obligation:{item.id}")
        meaningful = _obligation_meaningful(head_rows.get(item.scenario), item)
        if any(item.id in correction.obligations for correction in manifest.corrections):
            meaningful = meaningful and (
                _obligation_meaningful(base_rows.get(item.scenario), item)
                or _baseline_absence(base_rows.get(item.scenario), item)
            )
            if not meaningful:
                blocks.append(f"GOLDEN_BEHAVIOR_MISMATCH:obligation:{item.id}")
        if fact is not None:
            meaningful = meaningful and (
                fact["base_absent"] or _obligation_meaningful(base_rows.get(item.scenario), item)
            )
            if not meaningful:
                blocks.append(f"GOLDEN_BEHAVIOR_MISMATCH:obligation:{item.id}")
        rows.append(
            {
                "base_assertion_sha256": base,
                "category": item.category,
                "compatibility": "PASS" if compatible else "BLOCK",
                "head_assertion_sha256": head,
                "id": item.id,
                "meaningful": meaningful,
                "scenario": item.scenario,
                "target": item.target,
            }
        )
    return blocks, rows


def _obligation_meaningful(row: dict[str, Any] | None, obligation: Obligation) -> bool:
    if row is None:
        return False
    try:
        selected = _selected_assertion(row.get("behavior"), obligation.selector)
    except CharacterizationError:
        return False
    return _valid_obligation_assertion(obligation.category, selected, tuple(row.get("covers", [])))


def _logical_step_runnable(
    manifest: Manifest,
    base_rows: dict[str, dict[str, Any]],
    head_rows: dict[str, dict[str, Any]],
    responsibility_targets: tuple[str, ...],
    language: str,
    api_facts: dict[str, dict[str, Any]] | None = None,
) -> bool:
    target_paths = {target.split("::", 1)[0] for target in responsibility_targets}
    runnable_paths = {
        path
        for scenario in manifest.scenarios
        if (base := base_rows.get(scenario.id)) is not None
        and (head := head_rows.get(scenario.id)) is not None
        and _scenario_runnable(scenario, base, head, language, (api_facts or {}).get(scenario.id))
        for path in scenario.covers
    }
    return target_paths.issubset(runnable_paths)


def _scenario_runnable(
    scenario: Scenario,
    base: dict[str, Any],
    head: dict[str, Any],
    language: str,
    fact: dict[str, Any] | None,
) -> bool:
    command = scenario_command(scenario, language)
    if fact is not None and fact["base_absent"]:
        return (
            fact["admissible"]
            and fact["oracle_review_valid"]
            and _api_capture_matches(base, fact, "base")
            and _api_capture_matches(head, fact, "head")
            and head.get("command") == command
        )
    return bool(base.get("command") == head.get("command") == command)


def _result_paths(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    try:
        paths = tuple(contract.normalize_repository_path(item, field) for item in value)
    except contract.ContractError:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT") from None
    if len(paths) != len(set(paths)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return paths


def _result_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return tuple(value)


def _result_scenario(value: object) -> dict[str, Any]:
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
    row = _exact_keys(value, keys, "MALFORMED_CHARACTERIZATION_RESULT")
    hashes = tuple(row[name] for name in keys if name.endswith("_sha256"))
    command = row["command"]
    if (
        not isinstance(row["id"], str)
        or SCENARIO_ID.fullmatch(row["id"]) is None
        or not isinstance(row["kind"], str)
        or row["kind"] not in KINDS
        or not isinstance(row["compatibility"], str)
        or row["compatibility"] not in {"PASS", "BLOCK"}
        or any(
            item is not None and (not isinstance(item, str) or SHA256.fullmatch(item) is None)
            for item in hashes
        )
        or (
            command is not None
            and (
                not isinstance(command, list)
                or not command
                or any(not isinstance(item, str) or not item for item in command)
            )
        )
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    if not _result_paths(row["covers"], "scenarios.covers"):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return row


def _result_scenarios(
    value: object, api_facts: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_SCENARIOS:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    rows = [_result_scenario(item) for item in value]
    identifiers = [str(item["id"]) for item in rows]
    if identifiers != sorted(set(identifiers)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    for row in rows:
        compatible = bool(
            row["base_behavior_sha256"] is not None
            and row["base_behavior_sha256"] == row["head_behavior_sha256"]
        )
        fact = (api_facts or {}).get(row["id"])
        if fact is not None and fact["base_absent"]:
            compatible = _introduction_compatible(fact)
        if row["compatibility"] != ("PASS" if compatible else "BLOCK"):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return rows


def _result_obligation(
    value: object, scenario_ids: set[str], api_facts: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    keys = {
        "base_assertion_sha256",
        "category",
        "compatibility",
        "head_assertion_sha256",
        "id",
        "meaningful",
        "scenario",
        "target",
    }
    row = _exact_keys(value, keys, "MALFORMED_CHARACTERIZATION_RESULT")
    hashes = (row["base_assertion_sha256"], row["head_assertion_sha256"])
    compatible = hashes[0] is not None and hashes[0] == hashes[1]
    fact = (api_facts or {}).get(row["scenario"])
    if fact is not None and fact["base_absent"]:
        compatible = _introduction_compatible(fact)
    if (
        not isinstance(row["id"], str)
        or SCENARIO_ID.fullmatch(row["id"]) is None
        or row["category"] not in OBLIGATION_CATEGORIES
        or row["scenario"] not in scenario_ids
        or type(row["meaningful"]) is not bool
        or not isinstance(row["target"], str)
        or not row["target"]
        or (
            fact is not None
            and row["category"] == "behavior"
            and row["target"]
            not in (
                {fact["api"], fact["api"].split("::", 1)[0]} if "module" in fact else {fact["api"]}
            )
        )
        or any(
            item is not None and (not isinstance(item, str) or SHA256.fullmatch(item) is None)
            for item in hashes
        )
        or row["compatibility"] != ("PASS" if compatible else "BLOCK")
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return row


def _result_obligations(
    value: object,
    scenarios: list[dict[str, Any]],
    api_facts: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    scenario_ids = {str(item["id"]) for item in scenarios}
    rows = [_result_obligation(item, scenario_ids, api_facts) for item in value]
    for item in rows:
        if item["category"] == "baseline" and (
            item["target"] != f"scenario:{item['scenario']}"
            or next(s for s in scenarios if s["id"] == item["scenario"])["kind"] != "baseline"
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    identifiers = [str(item["id"]) for item in rows]
    if identifiers != sorted(set(identifiers)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return rows


def _introduction_compatible(fact: dict[str, Any]) -> bool:
    return bool(
        fact["admissible"]
        and fact["oracle_review_valid"]
        and fact["base_execution_verified"]
        and fact["head_execution_verified"]
    )


def _serialized_api_facts(
    facts: dict[str, dict[str, Any]],
    base_rows: dict[str, dict[str, Any]],
    head_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            **{
                key: (
                    {
                        module_key: module_value
                        for module_key, module_value in value.items()
                        if not module_key.startswith("_")
                    }
                    if key == "module"
                    else value
                )
                for key, value in fact.items()
            },
            "base_execution_verified": _api_capture_matches(base_rows.get(key), fact, "base"),
            "head_execution_verified": _api_capture_matches(head_rows.get(key), fact, "head"),
            "head_cases_sha256": head_rows.get(key, {}).get("behavior_sha256"),
        }
        for key, fact in sorted(facts.items())
    ]


def _result_api_facts(value: object, *, allow_module: bool = False) -> dict[str, dict[str, Any]]:
    limit = MODULE_MAX_OBSERVED_APIS if allow_module else MAX_OBSERVED_APIS
    if not isinstance(value, list) or len(value) > limit:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    rows = [_result_api_fact(item, allow_module=allow_module) for item in value]
    names = [item["scenario"] for item in rows]
    if (
        names != sorted(set(names))
        or sum(len(row["module"]["roots"]) if "module" in row else 1 for row in rows) > limit
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    if allow_module:
        roots = [
            root
            for row in rows
            for root in (row["module"]["roots"] if "module" in row else [row["api"]])
        ]
        if len(roots) != len(set(roots)):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return {item["scenario"]: item for item in rows}


def _result_api_fact(value: object, *, allow_module: bool = False) -> dict[str, Any]:
    row = _exact_keys(
        value,
        {
            "api",
            "scenario",
            "base_absent",
            "base_source",
            "head_source",
            "driver_sha256",
            "oracle_sha256",
            "admissible",
            "base_execution_verified",
            "head_execution_verified",
            "head_cases_sha256",
            "review_sha256",
            "intended_feature",
            "oracle_review_valid",
        }
        | ({"module"} if allow_module and isinstance(value, dict) and "module" in value else set()),
        "MALFORMED_CHARACTERIZATION_RESULT",
    )
    booleans = (
        "base_absent",
        "admissible",
        "base_execution_verified",
        "head_execution_verified",
        "oracle_review_valid",
    )
    if (
        any(type(row[key]) is not bool for key in booleans)
        or not isinstance(row["scenario"], str)
        or SCENARIO_ID.fullmatch(row["scenario"]) is None
        or not isinstance(row["api"], str)
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    _parse_api(row["api"], (row["api"].split("::", 1)[0],))
    for key in ("driver_sha256", "oracle_sha256"):
        if not isinstance(row[key], str) or SHA256.fullmatch(row[key]) is None:
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    _result_api_sources(row)
    if "module" in row:
        _result_module_fact(row)
    if row["oracle_review_valid"] and (
        not isinstance(row["review_sha256"], str)
        or SHA256.fullmatch(row["review_sha256"]) is None
        or not isinstance(row["intended_feature"], str)
        or not row["intended_feature"].strip()
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return row


def _result_module_fact(fact: dict[str, Any]) -> None:
    row = _exact_keys(
        fact["module"],
        {"roots", "metric", "base_inventory", "head_inventory", "oracle_sha256", "oracle_cases"},
        "MALFORMED_CHARACTERIZATION_RESULT",
    )
    _module_roots(row["roots"], fact["api"], (fact["api"].split("::", 1)[0],))
    if (
        not row["roots"]
        or row["metric"] != MODULE_BODY_METRIC
        or not isinstance(row["oracle_sha256"], str)
        or SHA256.fullmatch(row["oracle_sha256"]) is None
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    try:
        if len(_canonical(row["oracle_cases"])) > MODULE_MAX_JSON_BYTES:
            raise ModuleObservationError("MODULE_ORACLE_VALUE_LIMIT")
        _module_root_cases(row["oracle_cases"], row["oracle_cases"], len(row["oracle_cases"]))
        if not 2 <= len(row["oracle_cases"]) <= MODULE_MAX_EXECUTIONS:
            raise ModuleObservationError("INVALID_MODULE_ORACLE")
        for side in ("base", "head"):
            source, inventory = fact[side + "_source"], row[side + "_inventory"]
            if source is None:
                if inventory is not None:
                    raise ModuleObservationError("INVALID_MODULE_INVENTORY")
                continue
            module_validate_inventory(inventory, source["source_sha256"])
            module_public_roots(
                row["roots"], fact["api"], {item["name"]: item for item in inventory["functions"]}
            )
        _module_case_values(
            row["oracle_cases"],
            fact["api"].split("::", 1)[0],
            row["head_inventory"]["enum_declarations"],
        )
    except (ModuleObservationError, TypeError, ValueError, KeyError) as error:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT") from error


def capture_schema(version: str) -> str:
    return (
        CORRECTION_CAPTURE_SCHEMA
        if version == "5.0"
        else MODULE_CAPTURE_SCHEMA
        if version == "4.0"
        else OBSERVED_CAPTURE_SCHEMA
        if version == "3.0"
        else CAPTURE_SCHEMA
    )


def result_schema(version: str) -> str:
    return (
        CORRECTION_RESULT_SCHEMA
        if version == "5.0"
        else MODULE_RESULT_SCHEMA
        if version == "4.0"
        else OBSERVED_RESULT_SCHEMA
        if version == "3.0"
        else RESULT_SCHEMA
    )


def _result_api_sources(row: dict[str, Any]) -> None:
    for key in ("base_source", "head_source"):
        value = row[key]
        if value is not None:
            source = _exact_keys(
                value,
                {"source_sha256", "function_code_sha256", "start_line", "end_line"},
                "MALFORMED_CHARACTERIZATION_RESULT",
            )
            if any(
                not isinstance(item, str) or SHA256.fullmatch(item) is None
                for item in (source["source_sha256"], source["function_code_sha256"])
            ):
                raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
            if not _valid_source_span(source):
                raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    digest = row["head_cases_sha256"]
    if (
        row["base_absent"] != (row["base_source"] is None)
        or (
            digest is not None and (not isinstance(digest, str) or SHA256.fullmatch(digest) is None)
        )
        or (row["head_execution_verified"] and (row["head_source"] is None or digest is None))
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")


def _valid_source_span(value: dict[str, Any]) -> bool:
    return (
        type(value["start_line"]) is int
        and type(value["end_line"]) is int
        and 1 <= value["start_line"] <= value["end_line"]
    )


def _result_artifact(value: object) -> dict[str, Any]:
    row = _exact_keys(
        value, {"capture_sha256", "digest", "id"}, "MALFORMED_CHARACTERIZATION_RESULT"
    )
    if (
        not isinstance(row["id"], str)
        or not isinstance(row["digest"], str)
        or (
            row["capture_sha256"] is not None
            and (
                not isinstance(row["capture_sha256"], str)
                or SHA256.fullmatch(row["capture_sha256"]) is None
            )
        )
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return row


def _result_artifacts(
    value: object, expected: object
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows = _exact_keys(value, {"base", "head"}, "MALFORMED_CHARACTERIZATION_RESULT")
    trusted = (
        rows
        if expected is None
        else _exact_keys(expected, {"base", "head"}, "MALFORMED_CHARACTERIZATION_RESULT")
    )
    artifacts = {side: _result_artifact(rows[side]) for side in ("base", "head")}
    external = {side: _result_artifact(trusted[side]) for side in ("base", "head")}
    if any(
        (artifacts[side]["id"], artifacts[side]["digest"])
        != (external[side]["id"], external[side]["digest"])
        for side in ("base", "head")
    ):
        raise CharacterizationError("CHARACTERIZATION_RESULT_BINDING_MISMATCH")
    blocks = [
        f"{side.upper()}_CAPTURE_DIGEST_MISMATCH"
        for side in ("base", "head")
        if artifacts[side]["capture_sha256"] is not None
        and artifacts[side]["capture_sha256"] != external[side]["capture_sha256"]
    ]
    if any(
        ARTIFACT_ID.fullmatch(external[side]["id"]) is None
        or SHA256.fullmatch(external[side]["digest"]) is None
        or not isinstance(external[side]["capture_sha256"], str)
        or SHA256.fullmatch(external[side]["capture_sha256"]) is None
        for side in ("base", "head")
    ):
        blocks.append("INVALID_ARTIFACT_IDENTITY")
    if artifacts["base"]["capture_sha256"] is None and artifacts["head"]["capture_sha256"]:
        blocks.append("HEAD_ONLY_CHARACTERIZATION_CLAIM")
    return artifacts, blocks


def _result_policy_blocks(row: dict[str, Any]) -> list[str]:
    value = row["policy_blocks"]
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
        or row["overall_result"] != ("BLOCK" if value else "PASS")
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return value


def _result_coverage(
    value: object,
    scenarios: list[dict[str, Any]],
    obligations: list[dict[str, Any]],
    required_paths: tuple[str, ...] | None,
    required_targets: tuple[str, ...] | None,
    correction_ids: frozenset[str] = frozenset(),
) -> list[str]:
    row = _exact_keys(
        value,
        {"covered_obligations", "covered_paths", "required_obligations", "required_paths"},
        "MALFORMED_CHARACTERIZATION_RESULT",
    )
    covered = _result_paths(row["covered_paths"], "coverage.covered_paths")
    required = _result_paths(row["required_paths"], "coverage.required_paths")
    covered_obligations = _result_strings(row["covered_obligations"])
    required_obligations = _result_strings(row["required_obligations"])
    expected_obligations = (
        tuple(
            sorted(
                {
                    target.rsplit(":", 1)[0]
                    for target in required_targets
                    if "::" in target and target.split("::", 1)[1].split(":", 1)[0] != "module"
                }
            )
        )
        if required_targets is not None
        else required_obligations
    )
    expected_covered = tuple(
        sorted({path for scenario in scenarios for path in scenario["covers"]})
    )
    expected_covered_obligations = tuple(
        _verified_obligation_coverage(
            list(required_obligations), obligations, correction_ids, scenarios
        )
    )
    if (
        covered != expected_covered
        or required != tuple(sorted(required))
        or (required_paths is not None and required != required_paths)
        or required_obligations != expected_obligations
        or covered_obligations != tuple(sorted(set(covered_obligations)))
        or covered_obligations != expected_covered_obligations
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return [
        *(f"MISSING_CHARACTERIZATION_COVERAGE:{path}" for path in required if path not in covered),
        *(
            f"MISSING_CHARACTERIZATION_COVERAGE:obligation:{target}"
            for target in required_obligations
            if target not in covered_obligations
        ),
    ]


def _legacy_result_coverage(
    value: object,
    scenarios: list[dict[str, Any]],
    required_paths: tuple[str, ...] | None,
) -> list[str]:
    row = _exact_keys(
        value, {"covered_paths", "required_paths"}, "MALFORMED_CHARACTERIZATION_RESULT"
    )
    covered = _result_paths(row["covered_paths"], "coverage.covered_paths")
    required = _result_paths(row["required_paths"], "coverage.required_paths")
    expected_covered = tuple(
        sorted({path for scenario in scenarios for path in scenario["covers"]})
    )
    if (
        covered != expected_covered
        or required != tuple(sorted(required))
        or (required_paths is not None and required != required_paths)
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return [f"MISSING_CHARACTERIZATION_COVERAGE:{path}" for path in required if path not in covered]


def _result_scenario_blocks(
    rows: list[dict[str, Any]],
    blocks: list[str],
    api_facts: dict[str, dict[str, Any]] | None = None,
    *,
    correction: bool = False,
) -> list[str]:
    derived: list[str] = []
    incomplete = any(
        block
        in {"INCOMPLETE_CHARACTERIZATION_EVIDENCE", "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"}
        for block in blocks
    )
    for row in rows:
        identifier = row["id"]
        base = row["base_behavior_sha256"]
        head = row["head_behavior_sha256"]
        golden = row["golden_behavior_sha256"]
        execution_failed = f"CHARACTERIZATION_EXECUTION_FAILED:{identifier}" in blocks
        birth = bool((api_facts or {}).get(identifier, {}).get("base_absent"))
        if (
            ((row["command"] is None or golden is None) and not incomplete)
            or (
                base is None
                and not (incomplete or execution_failed or "MISSING_BASELINE" in blocks)
            )
            or (head is None and not (incomplete or execution_failed))
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
        if (
            base is not None
            and head is not None
            and base != head
            and row["compatibility"] == "BLOCK"
        ):
            derived.append(f"INCOMPATIBLE_POST_CHANGE_BEHAVIOR:{identifier}")
        behavior_mismatch = golden is not None and any(
            behavior is not None and behavior != golden
            for behavior in ((head,) if birth or correction else (base, head))
        )
        if golden is not None and execution_failed and (base is None or head is None):
            behavior_mismatch = True
        if behavior_mismatch:
            derived.append(f"GOLDEN_BEHAVIOR_MISMATCH:{identifier}")
    return derived


def _result_shape(value: object) -> dict[str, Any]:
    common_keys = {
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
    if not isinstance(value, dict):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    schema_version = value.get("schema_version")
    modern = schema_version in {
        RESULT_SCHEMA,
        OBSERVED_RESULT_SCHEMA,
        MODULE_RESULT_SCHEMA,
        CORRECTION_RESULT_SCHEMA,
    }
    keys = {*common_keys, "obligations"} if modern else common_keys
    if schema_version in {OBSERVED_RESULT_SCHEMA, MODULE_RESULT_SCHEMA, CORRECTION_RESULT_SCHEMA}:
        keys.add("api_observations")
    allowed_shapes: tuple[set[str], ...] = (keys, {*keys, "refactor_runnability"})
    if schema_version == CORRECTION_RESULT_SCHEMA:
        allowed_shapes = (
            *allowed_shapes,
            {*keys, "correction"},
            {*keys, "correction", "refactor_runnability"},
        )
    if (
        schema_version
        not in {
            LEGACY_RESULT_SCHEMA,
            RESULT_SCHEMA,
            OBSERVED_RESULT_SCHEMA,
            MODULE_RESULT_SCHEMA,
            CORRECTION_RESULT_SCHEMA,
        }
        or set(value) not in allowed_shapes
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return value


def validate_result(
    value: object,
    *,
    repository: str,
    base_sha: str,
    head_sha: str,
    workflow_sha: str,
    required_paths: tuple[str, ...] | None,
    required_targets: tuple[str, ...] | None = None,
    expected_artifacts: object = None,
) -> list[str]:
    """Validate serialized Gate 5 facts without repository or target execution."""
    row = _result_shape(value)
    if (
        row["schema_version"] in {MODULE_RESULT_SCHEMA, CORRECTION_RESULT_SCHEMA}
        and len(_canonical(row)) + 1 > MODULE_AGGREGATE_JSON_BYTES
    ):
        raise CharacterizationError("MODULE_AGGREGATE_VALUE_LIMIT")
    schema_version = row["schema_version"]
    modern = schema_version in {
        RESULT_SCHEMA,
        OBSERVED_RESULT_SCHEMA,
        MODULE_RESULT_SCHEMA,
        CORRECTION_RESULT_SCHEMA,
    }
    if (row["repository"], row["base_sha"], row["head_sha"], row["workflow_sha"]) != (
        repository,
        base_sha,
        head_sha,
        workflow_sha,
    ):
        raise CharacterizationError("CHARACTERIZATION_RESULT_BINDING_MISMATCH")
    if (
        not isinstance(row["manifest_blob_sha"], str)
        or SHA.fullmatch(row["manifest_blob_sha"]) is None
        or not isinstance(row["manifest_sha256"], str)
        or SHA256.fullmatch(row["manifest_sha256"]) is None
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    blocks = _result_policy_blocks(row)
    artifacts, derived = _result_artifacts(row["artifacts"], expected_artifacts)
    if artifacts["base"]["capture_sha256"] is None and not any(
        block in {"MISSING_BASELINE", "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE"}
        for block in blocks
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    if artifacts["head"]["capture_sha256"] is None and not any(
        block
        in {
            "INCOMPLETE_CHARACTERIZATION_EVIDENCE",
            "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE",
        }
        for block in blocks
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    api_facts = (
        _result_api_facts(
            row["api_observations"],
            allow_module=schema_version in {MODULE_RESULT_SCHEMA, CORRECTION_RESULT_SCHEMA},
        )
        if schema_version
        in {OBSERVED_RESULT_SCHEMA, MODULE_RESULT_SCHEMA, CORRECTION_RESULT_SCHEMA}
        else {}
    )
    scenarios = _result_scenarios(row["scenarios"], api_facts)
    derived.extend(_result_api_blocks(api_facts, scenarios))
    obligations = _result_obligations(row["obligations"], scenarios, api_facts) if modern else []
    if schema_version == CORRECTION_RESULT_SCHEMA and "correction" in row:
        _validate_result_correction(
            row["correction"], blocks, scenarios, obligations, required_targets
        )
    fingerprint_payload: object = (
        {
            "obligations": [[item["id"], item["head_assertion_sha256"]] for item in obligations],
            "scenarios": [[item["id"], item["head_behavior_sha256"]] for item in scenarios],
            **(
                {"api_observations": list(api_facts.values())}
                if schema_version
                in {OBSERVED_RESULT_SCHEMA, MODULE_RESULT_SCHEMA, CORRECTION_RESULT_SCHEMA}
                else {}
            ),
        }
        if modern
        else [[item["id"], item["head_behavior_sha256"]] for item in scenarios]
    )
    fingerprint = _sha256(_canonical(fingerprint_payload))
    if row["behavior_fingerprint"] != fingerprint:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    derived.extend(
        _result_scenario_blocks(
            scenarios,
            blocks,
            api_facts,
            correction=schema_version == CORRECTION_RESULT_SCHEMA and "correction" in row,
        )
    )
    derived.extend(
        f"INCOMPATIBLE_POST_CHANGE_BEHAVIOR:obligation:{item['id']}"
        for item in obligations
        if item["base_assertion_sha256"] is not None
        and item["head_assertion_sha256"] is not None
        and item["compatibility"] == "BLOCK"
    )
    derived.extend(
        f"GOLDEN_BEHAVIOR_MISMATCH:obligation:{item['id']}"
        for item in obligations
        if item["meaningful"] is False
    )
    if modern:
        derived.extend(
            _result_coverage(
                row["coverage"],
                scenarios,
                obligations,
                required_paths,
                required_targets,
                _correction_coverage_ids(row.get("correction"), blocks),
            )
        )
    else:
        derived.extend(_legacy_result_coverage(row["coverage"], scenarios, required_paths))
    exact_families = (
        "BASE_CAPTURE_DIGEST_MISMATCH",
        "HEAD_CAPTURE_DIGEST_MISMATCH",
        "HEAD_ONLY_CHARACTERIZATION_CLAIM",
        "GOLDEN_BEHAVIOR_MISMATCH:",
        "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:",
        "INVALID_ARTIFACT_IDENTITY",
        "MISSING_CHARACTERIZATION_COVERAGE:",
        "INVALID_API_INTRODUCTION:",
        "INVALID_API_ORACLE_REVIEW:",
        "MISSING_API_EXECUTION:",
    )
    actual = sorted(
        block for block in blocks if any(block.startswith(family) for family in exact_families)
    )
    if actual != sorted(set(derived)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return blocks


def _result_api_blocks(
    facts: dict[str, dict[str, Any]], scenarios: list[dict[str, Any]]
) -> list[str]:
    by_id = {item["id"]: item for item in scenarios}
    blocks: list[str] = []
    for key, fact in facts.items():
        scenario = by_id.get(key)
        if (
            scenario is None
            or scenario["covers"] != [fact["api"].split("::", 1)[0]]
            or scenario["head_behavior_sha256"] != fact["head_cases_sha256"]
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
        if fact["base_absent"] and scenario["base_behavior_sha256"] is not None:
            if scenario["base_behavior_sha256"] != _sha256(_canonical({"api_absent": fact["api"]})):
                raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
        if not fact["admissible"]:
            blocks.append(f"INVALID_API_INTRODUCTION:{key}")
        if not fact["oracle_review_valid"]:
            blocks.append(f"INVALID_API_ORACLE_REVIEW:{key}")
        if not fact["base_execution_verified"] or not fact["head_execution_verified"]:
            blocks.append(f"MISSING_API_EXECUTION:{key}")
    return blocks


def _delta_ids(rows: list[dict[str, object]], prefix: str) -> set[str]:
    return {
        str(item["id"])
        for item in rows
        if item.get(f"base_{prefix}_sha256") is not None
        and item.get(f"head_{prefix}_sha256") is not None
        and item[f"base_{prefix}_sha256"] != item[f"head_{prefix}_sha256"]
    }


def _correction_delta(
    scenarios: list[dict[str, object]], obligations: list[dict[str, object]]
) -> list[dict[str, object]]:
    rows = [
        {
            "base_sha256": item["base_behavior_sha256"],
            "head_sha256": item["head_behavior_sha256"],
            "id": item["id"],
            "kind": "scenario",
        }
        for item in scenarios
        if item["base_behavior_sha256"] is not None
        and item["head_behavior_sha256"] is not None
        and item["base_behavior_sha256"] != item["head_behavior_sha256"]
    ]
    rows.extend(
        {
            "base_sha256": item["base_assertion_sha256"],
            "head_sha256": item["head_assertion_sha256"],
            "id": item["id"],
            "kind": "obligation",
        }
        for item in obligations
        if item["base_assertion_sha256"] is not None
        and item["head_assertion_sha256"] is not None
        and item["base_assertion_sha256"] != item["head_assertion_sha256"]
    )
    return sorted(rows, key=lambda item: (str(item["kind"]), str(item["id"])))


def _reconcilable_block(block: str, correction: Correction) -> bool:
    allowed = set(correction.scenarios)
    obligation_allowed = set(correction.obligations)
    families = (
        "CHANGED_CHARACTERIZATION_DEFINITION:",
        "CHANGED_GOLDEN_OUTPUT:",
        "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:",
    )
    if not block.startswith(families):
        return False
    suffix = block.split(":", 1)[1]
    if suffix.startswith("obligation:"):
        return suffix.removeprefix("obligation:") in obligation_allowed
    if suffix.startswith("review:"):
        return suffix.removeprefix("review:") in allowed
    return suffix in allowed


def _correction_evidence(
    manifest: Manifest,
    base_manifest: Manifest | None,
    blocks: list[str],
    scenarios: list[dict[str, object]],
    obligations: list[dict[str, object]],
    targets: tuple[str, ...],
) -> dict[str, object]:
    previous = {item.id: item for item in base_manifest.corrections} if base_manifest else {}
    active = [item for item in manifest.corrections if previous.get(item.id) != item]
    if manifest.schema_version != "5.0" or len(active) != 1:
        raise CharacterizationError("MALFORMED_CORRECTION")
    correction = active[0]
    delta = _correction_delta(scenarios, obligations)
    actual_scenarios = _delta_ids(scenarios, "behavior")
    actual_obligations = _delta_ids(obligations, "assertion")
    # A module can expose changed behavior through a changed dependency while
    # its observer-level module hash remains stable. Accept that declared
    # scenario only when its definition changed and both sides executed to the
    # same non-null observer hash. The changed definition remains a reconciled,
    # review-visible policy block; absent or failed execution is never accepted.
    definition_only_scenarios = {
        str(item["id"])
        for item in scenarios
        if item["base_behavior_sha256"] is not None
        and item["base_behavior_sha256"] == item["head_behavior_sha256"]
        and f"CHANGED_CHARACTERIZATION_DEFINITION:{item['id']}" in blocks
    }
    verification_blocks: list[str] = []
    if actual_scenarios - set(correction.scenarios) or actual_obligations - set(
        correction.obligations
    ):
        verification_blocks.append("UNDECLARED_CORRECTION_DELTA")
    if (
        set(correction.scenarios) - actual_scenarios - definition_only_scenarios
        or set(correction.obligations) - actual_obligations
    ):
        verification_blocks.append("ABSENT_DECLARED_CORRECTION_DELTA")
    if correction.targets != targets:
        verification_blocks.append("UNDECLARED_CORRECTION_DELTA")
    behavior_blocks = [
        item
        for item in blocks
        if item.startswith(
            (
                "CHANGED_CHARACTERIZATION_DEFINITION:",
                "CHANGED_GOLDEN_OUTPUT:",
                "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:",
            )
        )
    ]
    reconciled = sorted(item for item in behavior_blocks if _reconcilable_block(item, correction))
    if len(reconciled) != len(behavior_blocks):
        verification_blocks.append("UNDECLARED_CORRECTION_DELTA")
    return {
        "behavior_delta": delta,
        "behavior_delta_sha256": _sha256(_canonical(delta)),
        "id": correction.id,
        "obligations": list(correction.obligations),
        "oracle_files": [
            {"kind": item.kind, "path": item.path, "sha256": item.sha256}
            for item in correction.oracle_files
        ],
        "oracle_manifest_blob_sha": manifest.blob_sha,
        "oracle_manifest_sha256": manifest.sha256,
        "reconcilable_blocks": reconciled,
        "scenarios": list(correction.scenarios),
        "targets": list(correction.targets),
        "verification_blocks": sorted(set(verification_blocks)),
    }


def _validate_result_correction(
    value: object,
    blocks: list[str],
    scenarios: list[dict[str, Any]],
    obligations: list[dict[str, Any]],
    required_targets: tuple[str, ...] | None,
) -> None:
    expected = {
        "behavior_delta",
        "behavior_delta_sha256",
        "id",
        "obligations",
        "oracle_files",
        "oracle_manifest_blob_sha",
        "oracle_manifest_sha256",
        "reconcilable_blocks",
        "scenarios",
        "targets",
        "verification_blocks",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    delta = _correction_delta(scenarios, obligations)
    if value["behavior_delta"] != delta or value["behavior_delta_sha256"] != _sha256(
        _canonical(delta)
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    if (
        not isinstance(value["id"], str)
        or SCENARIO_ID.fullmatch(value["id"]) is None
        or not isinstance(value["oracle_manifest_blob_sha"], str)
        or SHA.fullmatch(value["oracle_manifest_blob_sha"]) is None
        or not isinstance(value["oracle_manifest_sha256"], str)
        or SHA256.fullmatch(value["oracle_manifest_sha256"]) is None
        or value["targets"] != list(required_targets or ())
        or value["reconcilable_blocks"] != sorted(set(value["reconcilable_blocks"]))
        or any(item not in blocks for item in value["reconcilable_blocks"])
        or value["verification_blocks"] != sorted(set(value["verification_blocks"]))
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    for field, allowed in (
        ("scenarios", {item["id"] for item in scenarios}),
        ("obligations", {item["id"] for item in obligations}),
    ):
        if (
            not isinstance(value[field], list)
            or value[field] != sorted(set(value[field]))
            or any(item not in allowed for item in value[field])
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    try:
        _oracle_files(value["oracle_files"])
    except CharacterizationError as error:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT") from error


def _verification_result(
    identity: git_changes.RepositoryIdentity,
    manifest: Manifest,
    base: dict[str, Any] | None,
    head: dict[str, Any] | None,
    scenarios: list[dict[str, object]],
    obligations: list[dict[str, object]],
    blocks: list[str],
    required: list[str],
    covered: list[str],
    required_obligations: list[str],
    covered_obligations: list[str],
    responsibility_targets: tuple[str, ...],
    unbounded_paths: tuple[str, ...],
    runnable: bool,
    workflow_sha: str,
    base_artifact_id: str,
    base_artifact_digest: str,
    head_artifact_id: str,
    head_artifact_digest: str,
    api_observations: list[dict[str, Any]] | None = None,
    correction: dict[str, object] | None = None,
) -> dict[str, object]:
    unique_blocks = sorted(set(blocks))
    return {
        "artifacts": {
            "base": {
                "capture_sha256": base.get("_capture_sha256") if base else None,
                "digest": base_artifact_digest,
                "id": base_artifact_id,
            },
            "head": {
                "capture_sha256": head.get("_capture_sha256") if head else None,
                "digest": head_artifact_digest,
                "id": head_artifact_id,
            },
        },
        "base_sha": identity.base_sha,
        "behavior_fingerprint": _sha256(
            _canonical(
                {
                    "obligations": [
                        [item["id"], item["head_assertion_sha256"]] for item in obligations
                    ],
                    "scenarios": [[item["id"], item["head_behavior_sha256"]] for item in scenarios],
                    **(
                        {"api_observations": api_observations or []}
                        if manifest.schema_version in {"3.0", "4.0", "5.0"}
                        else {}
                    ),
                }
            )
        ),
        "coverage": {
            "covered_obligations": covered_obligations,
            "covered_paths": covered,
            "required_obligations": required_obligations,
            "required_paths": required,
        },
        "head_sha": identity.head_sha,
        "manifest_blob_sha": manifest.blob_sha,
        "manifest_sha256": manifest.sha256,
        "obligations": obligations,
        "overall_result": "BLOCK" if unique_blocks else "PASS",
        "policy_blocks": unique_blocks,
        "repository": identity.remote,
        "refactor_runnability": {
            "base_sha": identity.base_sha,
            "head_sha": identity.head_sha,
            "repository": identity.remote,
            "runnable": runnable,
            "schema_version": RUNNABILITY_SCHEMA,
            "targets": list(responsibility_targets),
            "unbounded_paths": list(unbounded_paths),
            "workflow_sha": workflow_sha,
        },
        "scenarios": scenarios,
        "schema_version": result_schema(manifest.schema_version),
        **(
            {"api_observations": api_observations or []}
            if manifest.schema_version in {"3.0", "4.0", "5.0"}
            else {}
        ),
        "workflow_sha": workflow_sha,
        **({"correction": correction} if correction is not None else {}),
    }


def _effective_policy_and_changes(
    repository: Path,
    base_sha: str,
    head_sha: str,
    records: list[git_changes.CommandRecord],
) -> tuple[contract.Contract, tuple[git_changes.ChangedPath, ...]]:
    policy = contract.parse_contract(
        git_changes.read_regular_blob(repository, base_sha, ".supportability.toml", records).content
    )
    candidate_policy = contract.parse_contract(
        git_changes.read_regular_blob(repository, head_sha, ".supportability.toml", records).content
    )
    changes = git_changes.changed_paths(repository, base_sha, head_sha, records)
    exact_deleted_paths = {
        item.old_path
        for item in changes
        if item.status == "DELETED" and item.old_path is not None and item.new_path is None
    }
    if contract.is_profile_expansion(policy, candidate_policy) or contract.is_profile_retirement(
        policy, candidate_policy, exact_deleted_paths
    ):
        policy = candidate_policy
    return policy, changes


def _responsibility_scope(
    repository: Path,
    identity: git_changes.RepositoryIdentity,
    policy: contract.Contract,
    changes: tuple[git_changes.ChangedPath, ...],
    records: list[git_changes.CommandRecord],
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    from supportability_gate import (
        refactor_targets,
    )  # local: keep result validator dependency-light

    try:
        targets, unbounded = refactor_targets.derive(repository, identity, policy, changes, records)
    except git_changes.GitError:
        return (
            True,
            (),
            tuple(
                sorted(
                    {
                        path
                        for change in changes
                        for path in (change.old_path, change.new_path)
                        if path is not None and policy.is_production_path(path)
                    }
                )
            ),
        )
    return False, targets, unbounded


def _active_correction_evidence(
    manifest: Manifest,
    base_manifest: Manifest | None,
    blocks: list[str],
    scenarios: list[dict[str, object]],
    obligations: list[dict[str, object]],
    responsibility_targets: tuple[str, ...],
) -> dict[str, object] | None:
    if manifest.schema_version != "5.0":
        return None
    previous = {item.id: item for item in base_manifest.corrections} if base_manifest else {}
    active = [item for item in manifest.corrections if previous.get(item.id) != item]
    if not active:
        return None
    return _correction_evidence(
        manifest,
        base_manifest,
        blocks,
        scenarios,
        obligations,
        responsibility_targets,
    )


def verify_evidence(
    repository: Path,
    base_sha: str,
    head_sha: str,
    base_path: Path,
    head_path: Path,
    *,
    repository_name: str,
    repository_id: str,
    workflow_sha: str,
    run_id: str,
    run_attempt: str,
    base_artifact_id: str,
    base_artifact_digest: str,
    base_capture_sha256: str,
    head_artifact_id: str,
    head_artifact_digest: str,
    head_capture_sha256: str,
) -> dict[str, object]:
    """Verify exact-identity captures, compatibility, golden immutability, and coverage."""
    records: list[git_changes.CommandRecord] = []
    repository = git_changes.validate_repository(repository, records)
    identity = git_changes.inspect_repository(repository, base_sha, head_sha, records)
    policy, changes = _effective_policy_and_changes(repository, base_sha, head_sha, records)
    target_derivation_failed, responsibility_targets, unbounded_paths = _responsibility_scope(
        repository, identity, policy, changes, records
    )
    deleted_paths = {
        item.old_path
        for item in changes
        if item.old_path and item.new_path is None and policy.is_production_path(item.old_path)
    }
    manifest = _manifest(repository, head_sha, records)
    try:
        base_manifest = _manifest(repository, base_sha, records)
    except git_changes.GitError as error:
        if error.code != "MISSING_BLOB":
            raise
        base_manifest = None
    api_facts, api_blocks = _api_bindings(
        repository, base_sha, head_sha, policy, manifest, changes, records
    )
    base, base_error = _load_capture(
        base_path,
        "MISSING_BASELINE",
        aggregate_schema=(
            capture_schema(manifest.schema_version)
            if manifest.schema_version in {"4.0", "5.0"}
            else None
        ),
    )
    head, head_error = _load_capture(
        head_path,
        "INCOMPLETE_CHARACTERIZATION_EVIDENCE",
        aggregate_schema=(
            capture_schema(manifest.schema_version)
            if manifest.schema_version in {"4.0", "5.0"}
            else None
        ),
    )
    blocks = [item for item in (base_error, head_error) if item] + api_blocks
    if base is None and head is not None:
        blocks.append("HEAD_ONLY_CHARACTERIZATION_CLAIM")
    blocks.extend(_capture_digest_blocks(base, head, base_capture_sha256, head_capture_sha256))
    expected_common = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "repository": repository_name,
        "repository_id": repository_id,
        "run_attempt": run_attempt,
        "run_id": run_id,
        "workflow_sha": workflow_sha,
    }
    base_blocks, base_rows = _verified_capture_rows(
        base,
        "base",
        expected_common,
        base_sha,
        head_sha,
        repository,
        policy,
        manifest,
        records,
        api_facts,
    )
    head_blocks, head_rows = _verified_capture_rows(
        head,
        "head",
        expected_common,
        head_sha,
        head_sha,
        repository,
        policy,
        manifest,
        records,
        api_facts,
    )
    blocks.extend((*base_blocks, *head_blocks))
    base_environment, base_provenance_blocks = _load_environment_provenance(
        base_path, base, {**expected_common, "job": "characterize-base", "side": "base"}
    )
    head_environment, head_provenance_blocks = _load_environment_provenance(
        head_path, head, {**expected_common, "job": "characterize-head", "side": "head"}
    )
    blocks.extend((*base_provenance_blocks, *head_provenance_blocks))
    if (
        base_environment is not None
        and head_environment is not None
        and _stable_environment(base_environment) != _stable_environment(head_environment)
    ):
        blocks.append("CHARACTERIZATION_ENVIRONMENT_DRIFT")
    blocks.extend(
        _definition_blocks(
            repository,
            base_sha,
            head_sha,
            policy.language,
            manifest,
            deleted_paths,
            records,
        )
    )
    (
        coverage_blocks,
        required,
        covered,
        required_obligations,
        covered_obligations,
    ) = _coverage_blocks(policy, manifest, changes, deleted_paths, responsibility_targets)
    blocks.extend(coverage_blocks)
    if (
        ARTIFACT_ID.fullmatch(base_artifact_id) is None
        or ARTIFACT_ID.fullmatch(head_artifact_id) is None
        or SHA256.fullmatch(base_artifact_digest) is None
        or SHA256.fullmatch(base_capture_sha256) is None
        or SHA256.fullmatch(head_artifact_digest) is None
        or SHA256.fullmatch(head_capture_sha256) is None
    ):
        blocks.append("INVALID_ARTIFACT_IDENTITY")
    compatibility_blocks, scenarios = _compatibility_evidence(
        manifest, base_rows, head_rows, api_facts
    )
    blocks.extend(compatibility_blocks)
    obligation_blocks, obligations = _obligation_evidence(manifest, base_rows, head_rows, api_facts)
    blocks.extend(obligation_blocks)
    correction = _active_correction_evidence(
        manifest,
        base_manifest,
        blocks,
        scenarios,
        obligations,
        responsibility_targets,
    )
    covered_obligations = _verified_obligation_coverage(
        required_obligations, obligations, _correction_coverage_ids(correction, blocks), scenarios
    )
    blocks.extend(
        f"MISSING_CHARACTERIZATION_COVERAGE:obligation:{target}"
        for target in required_obligations
        if target not in covered_obligations
    )
    runnable = not target_derivation_failed and _logical_step_runnable(
        manifest, base_rows, head_rows, responsibility_targets, policy.language, api_facts
    )
    if correction is not None:
        blocks.extend(cast(list[str], correction["verification_blocks"]))
        remaining = set(blocks) - set(cast(list[str], correction["reconcilable_blocks"]))
        runnable = not target_derivation_failed and not remaining
    result = _verification_result(
        identity,
        manifest,
        base,
        head,
        scenarios,
        obligations,
        blocks,
        required,
        covered,
        required_obligations,
        covered_obligations,
        responsibility_targets,
        unbounded_paths,
        runnable,
        workflow_sha,
        base_artifact_id,
        base_artifact_digest,
        head_artifact_id,
        head_artifact_digest,
        _serialized_api_facts(api_facts, base_rows, head_rows),
        correction,
    )
    validate_result(
        result,
        repository=identity.remote,
        base_sha=identity.base_sha,
        head_sha=identity.head_sha,
        workflow_sha=workflow_sha,
        required_paths=tuple(required),
        required_targets=responsibility_targets,
        expected_artifacts={
            "base": {
                "capture_sha256": base_capture_sha256,
                "digest": base_artifact_digest,
                "id": base_artifact_id,
            },
            "head": {
                "capture_sha256": head_capture_sha256,
                "digest": head_artifact_digest,
                "id": head_artifact_id,
            },
        },
    )
    return result


def _correction_coverage_ids(correction: object, blocks: list[str]) -> frozenset[str]:
    if not isinstance(correction, dict) or correction["verification_blocks"]:
        return frozenset()
    if set(blocks) - set(correction["reconcilable_blocks"]):
        return frozenset()
    return frozenset(correction["obligations"])


def _verified_obligation_coverage(
    required: list[str],
    obligations: list[dict[str, object]],
    correction_ids: frozenset[str] = frozenset(),
    scenarios: list[dict[str, Any]] | None = None,
) -> list[str]:
    targets = {
        str(item["target"])
        for item in obligations
        if item["category"] == "behavior"
        and (
            item["compatibility"] == "PASS"
            or (
                item["id"] in correction_ids
                and item["meaningful"] is True
                and item["base_assertion_sha256"] is not None
                and item["head_assertion_sha256"] is not None
            )
        )
    }
    baseline_ids = {
        str(item["scenario"])
        for item in obligations
        if item["category"] == "baseline"
        and item["meaningful"] is True
        and (item["compatibility"] == "PASS" or item["id"] in correction_ids)
    }
    targets.update(
        str(path)
        for scenario in (scenarios or [])
        if scenario.get("kind") == "baseline" and scenario["id"] in baseline_ids
        for path in scenario["covers"]
    )
    return [item for item in required if item in targets or item.split("::", 1)[0] in targets]


def _write_json(path: Path, value: object, *, compact: bool = False) -> bytes:
    module = isinstance(value, dict) and value.get("schema_version") in {
        MODULE_CAPTURE_SCHEMA,
        MODULE_RESULT_SCHEMA,
        CORRECTION_CAPTURE_SCHEMA,
        CORRECTION_RESULT_SCHEMA,
    }
    content = (
        _canonical(value)
        if compact or module
        else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode()
    ) + b"\n"
    if module:
        if len(content) > MODULE_AGGREGATE_JSON_BYTES:
            raise CharacterizationError("MODULE_AGGREGATE_VALUE_LIMIT")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


MODULE_MAX_FUNCTIONS = 128
MODULE_MAX_EXECUTIONS = 128
MODULE_MAX_CALLS = 4096
MODULE_WITNESS_SCHEMA = "module-witness.v3"
MODULE_BODY_METRIC = "named-body-execution.v1"
MODULE_CODEC = "python-values-v2"


class ModuleObservationError(ValueError):
    """One deterministic incomplete or inconsistent module witness."""


def module_validate_inventory(value: object, source_sha256: str) -> dict[str, Any]:
    inventory = _module_exact(
        value, {"source_sha256", "inventory_sha256", "functions", "enum_declarations"}
    )
    rows = inventory["functions"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MODULE_MAX_FUNCTIONS:
        raise ModuleObservationError("INVALID_MODULE_INVENTORY")
    functions = [_module_inventory_row(row) for row in rows]
    names = [row["name"] for row in functions]
    if names != sorted(set(names)) or inventory["source_sha256"] != source_sha256:
        raise ModuleObservationError("INVALID_MODULE_INVENTORY")
    _module_enum_rows(inventory["enum_declarations"])
    binding = {"functions": functions, "enum_declarations": inventory["enum_declarations"]}
    if inventory["inventory_sha256"] != hashlib.sha256(_module_canonical(binding)).hexdigest():
        raise ModuleObservationError("INVALID_MODULE_INVENTORY")
    return inventory


def _module_inventory_row(value: object) -> dict[str, Any]:
    row = _module_exact(value, {"name", "code_sha256", "lines"})
    if (
        not isinstance(row["name"], str)
        or not row["name"]
        or not isinstance(row["code_sha256"], str)
        or SHA256.fullmatch(row["code_sha256"]) is None
    ):
        raise ModuleObservationError("INVALID_MODULE_INVENTORY")
    lines = row["lines"]
    if (
        not isinstance(lines, list)
        or not lines
        or any(type(line) is not int or line < 1 for line in lines)
        or lines != sorted(set(lines))
    ):
        raise ModuleObservationError("INVALID_MODULE_INVENTORY")
    return row


def _module_canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _module_codes(code: CodeType) -> list[CodeType]:
    return [
        code,
        *(
            item
            for child in code.co_consts
            if type(child) is CodeType
            for item in _module_codes(child)
        ),
    ]


def _module_functions(
    nodes: list[ast.stmt], parents: tuple[str, ...] = ()
) -> list[tuple[str, ast.FunctionDef]]:
    found: list[tuple[str, ast.FunctionDef]] = []
    for node in nodes:
        if isinstance(node, ast.AsyncFunctionDef):
            raise ModuleObservationError("UNSUPPORTED_MODULE_FUNCTION")
        if isinstance(node, ast.FunctionDef):
            found.append((".".join((*parents, node.name)), node))
            nested = _module_functions(node.body, (*parents, node.name, "<locals>"))
            found.extend(nested)
        elif isinstance(node, ast.ClassDef):
            found.extend(_module_functions(node.body, (*parents, node.name)))
        elif any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for child in ast.walk(node)
        ):
            raise ModuleObservationError("UNSUPPORTED_CONDITIONAL_DECLARATION")
    return found


def module_source_inventory(source: bytes, path: str) -> dict[str, Any]:
    """Bind every declared function, its code and executable body lines to source."""
    if (
        not path.endswith(".py")
        or "\\" in path
        or ":" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ModuleObservationError("INVALID_MODULE_PATH")
    try:
        module = compile(source, "/target/" + path, "exec", dont_inherit=True, optimize=0)
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as error:
        raise ModuleObservationError("INVALID_MODULE_SOURCE") from error
    if any(isinstance(node, ast.GeneratorExp) for node in ast.walk(tree)):
        raise ModuleObservationError("UNSUPPORTED_MODULE_GENERATOR_EXPRESSION")
    if any(isinstance(node, ast.Lambda) for node in ast.walk(tree)):
        raise ModuleObservationError("UNSUPPORTED_MODULE_LAMBDA")
    declared = _module_functions(tree.body)
    codes = _module_codes(module)
    rows = [_module_function_row(name, node, codes) for name, node in declared]
    _module_accounted_codes(codes, rows)
    rows.sort(key=lambda row: row["name"])
    if (
        not rows
        or len(rows) > MODULE_MAX_FUNCTIONS
        or len({row["name"] for row in rows}) != len(rows)
    ):
        raise ModuleObservationError("INVALID_MODULE_INVENTORY")
    enums = module_enum_declarations(source)
    return {
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "inventory_sha256": hashlib.sha256(
            _module_canonical({"functions": rows, "enum_declarations": enums})
        ).hexdigest(),
        "functions": rows,
        "enum_declarations": enums,
    }


def _module_enum_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 256:
        raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
    rows = [_module_exact(row, {"name", "base", "members"}) for row in value]
    names = [row["name"] for row in rows]
    if any(type(name) is not str or not name.isidentifier() for name in names):
        raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
    if names != sorted(set(names)):
        raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
    for row in rows:
        _module_enum_members(row)
    return rows


def _module_enum_members(row: dict[str, Any]) -> None:
    members = row["members"]
    if row["base"] not in {"Enum", "StrEnum"} or not isinstance(members, list):
        raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
    if not 1 <= len(members) <= 256:
        raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
    names, literals = [], []
    for member in members:
        item = _module_exact(member, {"name", "value"})
        name, literal = item["name"], item["value"]
        if type(name) is not str or not name.isidentifier() or name.startswith("_"):
            raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
        if type(literal) not in {bool, int, str} or literal in literals:
            raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
        if row["base"] == "StrEnum" and type(literal) is not str:
            raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
        if name in names:
            raise ModuleObservationError("INVALID_MODULE_ENUM_DECLARATION")
        names.append(name)
        literals.append(literal)


def _module_enum_imports(tree: ast.Module) -> dict[str, str]:
    imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "enum" and node.level == 0:
            for alias in node.names:
                if alias.name in {"Enum", "StrEnum"}:
                    imports[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "enum":
                    imports[alias.asname or "enum"] = "enum"
    return imports


def _module_enum_base(node: ast.expr, imports: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name) and imports.get(node.id) in {"Enum", "StrEnum"}:
        return imports[node.id]
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and imports.get(node.value.id) == "enum"
        and node.attr in {"Enum", "StrEnum"}
    ):
        return node.attr
    return None


def _module_enum_class(node: ast.ClassDef, base: str) -> dict[str, Any]:
    if len(node.bases) != 1 or node.keywords or node.decorator_list:
        raise ModuleObservationError("UNSUPPORTED_MODULE_ENUM_DECLARATION")
    members = []
    for statement in node.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            if type(statement.value.value) is str:
                continue
        if (
            not isinstance(statement, ast.Assign)
            or len(statement.targets) != 1
            or not isinstance(statement.targets[0], ast.Name)
            or not isinstance(statement.value, ast.Constant)
        ):
            raise ModuleObservationError("UNSUPPORTED_MODULE_ENUM_DECLARATION")
        members.append({"name": statement.targets[0].id, "value": statement.value.value})
    result = {"name": node.name, "base": base, "members": members}
    _module_enum_members(result)
    return result


def module_enum_declarations(source: bytes) -> list[dict[str, Any]]:
    """Read simple literal stdlib enum declarations without evaluating target code."""
    tree = ast.parse(source)
    imports = _module_enum_imports(tree)
    rows = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [_module_enum_base(base, imports) for base in node.bases]
            recognized = [base for base in bases if base is not None]
            if recognized:
                rows.append(_module_enum_class(node, recognized[0]))
    rows.sort(key=lambda row: row["name"])
    return _module_enum_rows(rows)


def _module_case_values(
    cases: list[dict[str, Any]], path: str, enums: list[dict[str, Any]]
) -> None:
    for row in cases:
        for key in ("input", "arguments_after"):
            arguments = row[key]
            if not isinstance(arguments, list) or len(arguments) > 256:
                raise ModuleObservationError("INVALID_MODULE_ARGUMENTS")
            names = []
            for argument in arguments:
                item = _module_exact(argument, {"name", "value"})
                if type(item["name"]) is not str or not item["name"].isidentifier():
                    raise ModuleObservationError("INVALID_MODULE_ARGUMENTS")
                names.append(item["name"])
                module_validate_value(item["value"], path, enums)
            if len(names) != len(set(names)):
                raise ModuleObservationError("INVALID_MODULE_ARGUMENTS")
        if row["outcome"] == "RETURN":
            module_validate_value(row["output"], path, enums)


def module_validate_value(value: object, path: str, enums: list[dict[str, Any]]) -> None:
    """Validate the fixed module codec's bounded wire values without target execution."""
    if len(_canonical(value)) > 262144:
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    _module_value(value, path, enums, 0, [0])


def _module_value(
    value: object, path: str, enums: list[dict[str, Any]], depth: int, nodes: list[int]
) -> None:
    nodes[0] += 1
    if depth > 16 or nodes[0] > 4096 or not isinstance(value, dict):
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    kind = value.get("type")
    if type(kind) is not str:
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    if kind in {"list", "tuple", "dict", "dict-keyed", "dataclass"}:
        children = _module_value_children(value)
        for child in children:
            _module_value(child, path, enums, depth + 1, nodes)
    elif kind == "enum":
        _module_enum_value(value, path, enums)
        _module_value(value["value"], path, enums, depth + 1, nodes)
    else:
        _module_primitive_value(value)


def _module_value_children(row: dict[str, Any]) -> list[Any]:
    kind = row["type"]
    if kind == "dataclass":
        _module_exact(row, {"type", "module", "name", "fields"})
        if any(type(row[key]) is not str or not row[key] for key in ("module", "name")):
            raise ModuleObservationError("INVALID_MODULE_VALUE")
        items = row["fields"]
    else:
        _module_exact(row, {"type", "items"})
        items = row["items"]
    if not isinstance(items, list) or len(items) > 256:
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    if kind in {"list", "tuple"}:
        return items
    if any(not isinstance(pair, list) or len(pair) != 2 for pair in items):
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    keys = [pair[0] for pair in items]
    if kind == "dict-keyed":
        _module_typed_keys(keys)
        return [item for pair in items for item in pair]
    if any(type(key) is not str for key in keys) or len(keys) != len(set(keys)):
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    if kind == "dict" and keys != sorted(keys):
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    return [pair[1] for pair in items]


def _module_typed_keys(keys: list[Any]) -> None:
    literals = []
    encodings = []
    for key in keys:
        row = _module_exact(key, {"type", "value"})
        kinds = {"bool": bool, "int": int, "str": str}
        if row["type"] not in kinds or type(row["value"]) is not kinds[row["type"]]:
            raise ModuleObservationError("INVALID_MODULE_DICT_KEY")
        if row["value"] in literals:
            raise ModuleObservationError("INVALID_MODULE_DICT_KEY")
        literals.append(row["value"])
        encodings.append(_canonical(row))
    if not keys or all(key["type"] == "str" for key in keys) or encodings != sorted(encodings):
        raise ModuleObservationError("INVALID_MODULE_DICT_KEY")


def _module_primitive_value(row: dict[str, Any]) -> None:
    kind = row.get("type")
    if type(kind) is not str:
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    _module_exact(row, {"type", "value", "fold"} if kind == "datetime" else {"type", "value"})
    value: Any = row["value"]
    kinds = {"none": type(None), "bool": bool, "str": str, "int": int}
    if kind in kinds:
        if type(value) is not kinds[kind]:
            raise ModuleObservationError("INVALID_MODULE_VALUE")
        if kind == "int" and isinstance(value, int) and value.bit_length() > 256:
            raise ModuleObservationError("INVALID_MODULE_VALUE")
        return
    if type(value) is not str:
        raise ModuleObservationError("INVALID_MODULE_VALUE")
    _module_text_primitive(kind, value, row)


def _module_text_primitive(kind: str, value: str, row: dict[str, Any]) -> None:
    try:
        if kind == "bytes":
            valid = bytes.fromhex(value).hex() == value and len(value) <= 262144
        elif kind == "float":
            number = float.fromhex(value)
            valid = (
                number.hex() == value
                and number not in {float("inf"), float("-inf")}
                and number == number
            )
        elif kind == "decimal":
            decimal = Decimal(value)
            exponent = decimal.as_tuple().exponent
            valid = (
                decimal.is_finite()
                and len(decimal.as_tuple().digits) <= 256
                and isinstance(exponent, int)
                and abs(exponent) <= 10000
            )
        elif kind == "datetime":
            date = datetime.fromisoformat(value)
            valid = (
                date.tzinfo is not None
                and date.isoformat(timespec="microseconds") == value
                and type(row["fold"]) is int
                and row["fold"] in {0, 1}
            )
        else:
            valid = False
    except (ValueError, InvalidOperation):
        valid = False
    if not valid:
        raise ModuleObservationError("INVALID_MODULE_VALUE")


def _module_enum_value(row: dict[str, Any], path: str, enums: list[dict[str, Any]]) -> None:
    _module_exact(row, {"type", "source_path", "module", "name", "member", "value"})
    declaration = next((item for item in enums if item["name"] == row["name"]), None)
    module_path = (
        path.removeprefix("src/").removesuffix(".py").removesuffix("/__init__").replace("/", ".")
    )
    if declaration is None or row["source_path"] != path or row["module"] != module_path:
        raise ModuleObservationError("INVALID_MODULE_ENUM_VALUE")
    literal = next((item for item in declaration["members"] if item["name"] == row["member"]), None)
    if literal is None:
        raise ModuleObservationError("INVALID_MODULE_ENUM_VALUE")
    kinds = {bool: "bool", int: "int", str: "str"}
    if row["value"] != {"type": kinds[type(literal["value"])], "value": literal["value"]}:
        raise ModuleObservationError("INVALID_MODULE_ENUM_VALUE")


def _module_accounted_codes(codes: list[CodeType], rows: list[dict[str, Any]]) -> None:
    """Reject separately compiled executable function bodies absent from inventory."""
    names = {row["name"] for row in rows}
    # CO_NEWLOCALS distinguishes function bodies from module/class scaffolding.
    if any(code.co_flags & 0x02 and code.co_qualname not in names for code in codes):
        raise ModuleObservationError("UNSUPPORTED_MODULE_COMPILED_FUNCTION")


def _module_function_row(name: str, node: ast.FunctionDef, codes: list[CodeType]) -> dict[str, Any]:
    matching = [code for code in codes if code.co_qualname == name]
    if len(matching) != 1 or matching[0].co_flags & (0x20 | 0x80 | 0x200):
        raise ModuleObservationError("UNSUPPORTED_MODULE_FUNCTION")
    code = matching[0]
    lines = sorted({line for _, line in dis.findlinestarts(code) if line >= node.body[0].lineno})
    if not lines:
        raise ModuleObservationError("INVALID_FUNCTION_LINES")
    return {
        "name": name,
        "code_sha256": hashlib.sha256(marshal.dumps(code, 2)).hexdigest(),
        "lines": lines,
    }


def _module_exact(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ModuleObservationError("INVALID_MODULE_WITNESS_SHAPE")
    return value


def _module_meaningful(cases: object) -> bool:
    if not isinstance(cases, list) or not 2 <= len(cases) <= MODULE_MAX_EXECUTIONS:
        return False
    if any(not isinstance(row, dict) or set(row) != {"input", "output"} for row in cases):
        return False
    return (
        len({_module_canonical(row["input"]) for row in cases}) >= 2
        and len({_module_canonical(row["output"]) for row in cases}) >= 2
    )


def _module_primary(
    source: bytes, path: str, api: str, value: object, expected: object
) -> dict[str, Any]:
    row = _module_exact(
        value,
        {
            "schema_version",
            "codec",
            "api",
            "source_sha256",
            "function_code_sha256",
            "start_line",
            "end_line",
            "cases",
        },
    )
    if not api.startswith(path + "::function:"):
        raise ModuleObservationError("MODULE_PRIMARY_OUTSIDE_SOURCE")
    name = api.split("::function:", 1)[1]
    codes = _module_codes(compile(source, "/target/" + path, "exec", dont_inherit=True, optimize=0))
    selected = [code for code in codes if code.co_qualname == name]
    if len(selected) != 1:
        raise ModuleObservationError("INVALID_MODULE_PRIMARY")
    if row["schema_version"] != "1.0" or row["codec"] != MODULE_CODEC or row["api"] != api:
        raise ModuleObservationError("INVALID_MODULE_PRIMARY")
    if (
        row["source_sha256"] != hashlib.sha256(source).hexdigest()
        or row["function_code_sha256"] != hashlib.sha256(marshal.dumps(selected[0], 2)).hexdigest()
    ):
        raise ModuleObservationError("STALE_MODULE_PRIMARY")
    nodes = [
        node for qualified, node in _module_functions(ast.parse(source).body) if qualified == name
    ]
    if (
        len(nodes) != 1
        or row["start_line"]
        != min((item.lineno for item in nodes[0].decorator_list), default=nodes[0].lineno)
        or row["end_line"] != nodes[0].end_lineno
    ):
        raise ModuleObservationError("STALE_MODULE_PRIMARY")
    if row["cases"] != expected or not _module_meaningful(row["cases"]):
        raise ModuleObservationError("INVALID_MODULE_ORACLE_CASES")
    return row


def module_public_roots(value: object, api: str, functions: dict[str, Any]) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > MODULE_MAX_FUNCTIONS:
        raise ModuleObservationError("INVALID_MODULE_ROOTS")
    path = api.split("::", 1)[0]
    if any(
        not isinstance(root, str) or not root.startswith(path + "::function:") for root in value
    ):
        raise ModuleObservationError("INVALID_MODULE_ROOTS")
    if value != sorted(set(value)) or api not in value:
        raise ModuleObservationError("INVALID_MODULE_ROOTS")
    names = tuple(root.split("::function:", 1)[1] for root in value)
    if any(name not in functions or name.rsplit(".", 1)[-1].startswith("_") for name in names):
        raise ModuleObservationError("INVALID_MODULE_ROOTS")
    return names


def _module_executions(
    value: object, primary: dict[str, Any], root_cases: list[dict[str, Any]], roots: tuple[str, ...]
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 2 <= len(value) <= MODULE_MAX_EXECUTIONS:
        raise ModuleObservationError("INVALID_MODULE_EXECUTIONS")
    rows = [_module_exact(row, {"id", "root", "outcome", "case", "primary_case"}) for row in value]
    expected_case = 0
    for index, row in enumerate(rows):
        if (
            type(row["id"]) is not int
            or row["id"] != index
            or row["outcome"] not in {"RETURN", "EXCEPTION"}
        ):
            raise ModuleObservationError("INVALID_MODULE_EXECUTIONS")
        if row["root"] not in roots or type(row["case"]) is not int or row["case"] != index:
            raise ModuleObservationError("UNLINKED_MODULE_EXECUTION")
        case = root_cases[index]
        if case["root"] != row["root"] or case["outcome"] != row["outcome"]:
            raise ModuleObservationError("UNLINKED_MODULE_EXECUTION")
        expected_case = _module_primary_case_link(row, case, primary, expected_case)
    if expected_case != len(primary["cases"]) or {row["root"] for row in rows} != set(roots):
        raise ModuleObservationError("UNLINKED_MODULE_EXECUTION")
    return rows


def _module_primary_case_link(
    row: dict[str, Any], case: dict[str, Any], primary: dict[str, Any], index: int
) -> int:
    if row["root"] != primary["api"].split("::function:", 1)[1] or row["outcome"] != "RETURN":
        if row["primary_case"] is not None:
            raise ModuleObservationError("UNLINKED_MODULE_EXECUTION")
        return index
    if (
        type(row["primary_case"]) is not int
        or row["primary_case"] != index
        or index >= len(primary["cases"])
    ):
        raise ModuleObservationError("UNLINKED_MODULE_EXECUTION")
    if {"input": case["input"], "output": case["output"]} != primary["cases"][index]:
        raise ModuleObservationError("UNLINKED_MODULE_EXECUTION")
    return index + 1


def _module_root_cases(
    value: object, expected: object, execution_count: int
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != execution_count or value != expected:
        raise ModuleObservationError("INVALID_MODULE_ROOT_ORACLE")
    rows = [
        _module_exact(row, {"root", "input", "arguments_after", "outcome", "output", "exception"})
        for row in value
    ]
    for row in rows:
        if row["outcome"] not in {"RETURN", "EXCEPTION"}:
            raise ModuleObservationError("INVALID_MODULE_ROOT_ORACLE")
        if row["outcome"] == "RETURN" and row["exception"] is not None:
            raise ModuleObservationError("INVALID_MODULE_ROOT_ORACLE")
        if row["outcome"] == "EXCEPTION" and (
            row["output"] is not None
            or not isinstance(row["exception"], str)
            or not row["exception"]
        ):
            raise ModuleObservationError("INVALID_MODULE_ROOT_ORACLE")
    return rows


def _module_call(
    row: object, functions: dict[str, dict[str, Any]], execution_count: int
) -> dict[str, Any]:
    call = _module_exact(row, {"execution", "function", "lines", "outcome"})
    if type(call["execution"]) is not int or not 0 <= call["execution"] < execution_count:
        raise ModuleObservationError("UNLINKED_MODULE_CALL")
    name, lines = call["function"], call["lines"]
    if (
        not isinstance(name, str)
        or name not in functions
        or call["outcome"] not in {"RETURN", "EXCEPTION"}
    ):
        raise ModuleObservationError("INVALID_MODULE_CALL")
    if not isinstance(lines, list) or not lines or any(type(line) is not int for line in lines):
        raise ModuleObservationError("INVALID_MODULE_CALL_LINES")
    if lines != sorted(set(lines)) or not set(lines) <= set(functions[name]["lines"]):
        raise ModuleObservationError("INVALID_MODULE_CALL_LINES")
    return call


def _module_coverage(
    calls: list[dict[str, Any]],
    functions: dict[str, dict[str, Any]],
    executions: list[dict[str, Any]],
) -> None:
    for execution in executions:
        roots = [
            call
            for call in calls
            if call["execution"] == execution["id"] and call["function"] == execution["root"]
        ]
        if len(roots) != 1 or roots[0]["outcome"] != execution["outcome"]:
            raise ModuleObservationError("UNWITNESSED_PRIMARY_EXECUTION")
    for name, function in functions.items():
        seen = {line for call in calls if call["function"] == name for line in call["lines"]}
        if not seen:
            raise ModuleObservationError("UNWITNESSED_FUNCTION_BODY:" + name)


def module_body_report(
    inventory: dict[str, Any], calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Report exact actual hit and missing lines for every independently inventoried body."""
    rows = []
    for function in inventory["functions"]:
        hits = sorted(
            {
                line
                for call in calls
                if call["function"] == function["name"]
                for line in call["lines"]
            }
        )
        rows.append(
            {
                "function": function["name"],
                "executable_lines": function["lines"],
                "hit_lines": hits,
                "missing_lines": sorted(set(function["lines"]) - set(hits)),
            }
        )
    return rows


def verify_module_observation(
    source: bytes,
    path: str,
    api: str,
    value: object,
    expected_cases: object,
    expected_root_cases: object,
) -> None:
    """Reject any incomplete inventory, changed oracle or unlinked body witness."""
    inventory = module_source_inventory(source, path)
    if not isinstance(value, dict):
        raise ModuleObservationError("INVALID_MODULE_WITNESS_SHAPE")
    _module_primary(source, path, api, value.get("primary"), expected_cases)
    verify_module_inventory_observation(
        inventory, path, api, value, expected_cases, expected_root_cases
    )


def verify_module_inventory_observation(
    inventory: dict[str, Any],
    path: str,
    api: str,
    value: object,
    expected_cases: object,
    expected_root_cases: object,
) -> None:
    """Join the independently derived inventory to an authenticated collector row."""
    if len(_canonical(value)) > MODULE_MAX_JSON_BYTES:
        raise ModuleObservationError("MODULE_WITNESS_VALUE_LIMIT")
    row = _module_exact(
        value,
        {
            "schema_version",
            "path",
            "api",
            "source_sha256",
            "inventory_sha256",
            "functions",
            "enum_declarations",
            "primary",
            "roots",
            "root_cases",
            "executions",
            "calls",
            "body_metric",
            "body_coverage",
        },
    )
    if row["schema_version"] != MODULE_WITNESS_SCHEMA or row["path"] != path or row["api"] != api:
        raise ModuleObservationError("INVALID_MODULE_WITNESS_IDENTITY")
    if any(row[key] != item for key, item in inventory.items()):
        raise ModuleObservationError("STALE_MODULE_INVENTORY")
    primary = row["primary"]
    if (
        not isinstance(primary, dict)
        or primary.get("codec") != MODULE_CODEC
        or primary.get("cases") != expected_cases
        or not _module_meaningful(expected_cases)
    ):
        raise ModuleObservationError("INVALID_MODULE_ORACLE_CASES")
    functions = {item["name"]: item for item in inventory["functions"]}
    roots = module_public_roots(row["roots"], api, functions)
    if not isinstance(row["executions"], list):
        raise ModuleObservationError("INVALID_MODULE_EXECUTIONS")
    root_cases = _module_root_cases(row["root_cases"], expected_root_cases, len(row["executions"]))
    _module_case_values(root_cases, path, inventory["enum_declarations"])
    executions = _module_executions(row["executions"], primary, root_cases, roots)
    if not isinstance(row["calls"], list) or not 1 <= len(row["calls"]) <= MODULE_MAX_CALLS:
        raise ModuleObservationError("INVALID_MODULE_CALLS")
    calls = [_module_call(item, functions, len(executions)) for item in row["calls"]]
    _module_coverage(calls, functions, executions)
    if row["body_metric"] != MODULE_BODY_METRIC or row["body_coverage"] != module_body_report(
        inventory, calls
    ):
        raise ModuleObservationError("INVALID_MODULE_BODY_METRIC")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supportability-characterization")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--repository", required=True)
    verify.add_argument("--repository-name", required=True)
    verify.add_argument("--repository-id", required=True)
    verify.add_argument("--base-ref", required=True)
    verify.add_argument("--head-ref", required=True)
    verify.add_argument("--workflow-sha", required=True)
    verify.add_argument("--run-id", required=True)
    verify.add_argument("--run-attempt", required=True)
    verify.add_argument("--base-evidence", required=True)
    verify.add_argument("--head-evidence", required=True)
    verify.add_argument("--base-artifact-id", required=True)
    verify.add_argument("--base-artifact-digest", required=True)
    verify.add_argument("--base-capture-sha256", required=True)
    verify.add_argument("--head-artifact-id", required=True)
    verify.add_argument("--head-artifact-digest", required=True)
    verify.add_argument("--head-capture-sha256", required=True)
    verify.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Verify characterization evidence without executing target code."""
    arguments = _parser().parse_args(argv)
    try:
        result = verify_evidence(
            Path(arguments.repository),
            arguments.base_ref,
            arguments.head_ref,
            Path(arguments.base_evidence),
            Path(arguments.head_evidence),
            repository_name=arguments.repository_name,
            repository_id=arguments.repository_id,
            workflow_sha=arguments.workflow_sha,
            run_id=arguments.run_id,
            run_attempt=arguments.run_attempt,
            base_artifact_id=arguments.base_artifact_id,
            base_artifact_digest=arguments.base_artifact_digest,
            base_capture_sha256=arguments.base_capture_sha256,
            head_artifact_id=arguments.head_artifact_id,
            head_artifact_digest=arguments.head_artifact_digest,
            head_capture_sha256=arguments.head_capture_sha256,
        )
        _write_json(Path(arguments.output), result)
    except Exception as error:  # fail closed at isolated-job boundary
        print(getattr(error, "code", "TECHNICAL_FAILURE"))
        return 2
    overall = str(result.get("overall_result", "PASS"))
    print(overall)
    correction = result.get("correction")
    correction_ready = bool(
        isinstance(correction, dict)
        and not correction["verification_blocks"]
        and set(cast(list[str], result.get("policy_blocks", [])))
        <= set(cast(list[str], correction["reconcilable_blocks"]))
    )
    return 1 if overall == "BLOCK" and not correction_ready else 0


if __name__ == "__main__":
    raise SystemExit(main())
