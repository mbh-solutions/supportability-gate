"""Verify authenticated base/head characterization artifacts without executing target code."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import marshal
import re
from dataclasses import dataclass
from pathlib import Path
from types import CodeType
from typing import Any

from supportability_gate import contract, git_changes

MANIFEST_PATH = ".supportability-characterization.json"
SCENARIO_ROOT = "tests/characterization"
CAPTURE_SCHEMA = "characterization-capture.v2"
PROVENANCE_SCHEMA = "characterization-provenance.v1"
LEGACY_RESULT_SCHEMA = "characterization-result.v1"
RESULT_SCHEMA = "characterization-result.v2"
OBSERVED_RESULT_SCHEMA = "characterization-result.v3"
OBSERVED_CAPTURE_SCHEMA = "characterization-capture.v3"
RUNNABILITY_SCHEMA = "refactor-runnability.v1"
KINDS = frozenset({"test", "sample_io", "snapshot", "golden", "cli", "regression"})
OBLIGATION_CATEGORIES = frozenset({"behavior", "cli_help", "static"})
SCENARIO_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ARTIFACT_ID = re.compile(r"[1-9][0-9]*")
SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_JSON_BYTES = 1_000_000


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
class Manifest:
    """Validated scenario manifest at one immutable commit."""

    scenarios: tuple[Scenario, ...]
    blob_sha: str
    sha256: str
    obligations: tuple[Obligation, ...] = ()
    transitions: tuple[Transition, ...] = ()
    schema_version: str = "1.0"


def _manifest_payload(manifest: Manifest) -> dict[str, object]:
    payload: dict[str, object] = {
        "blob_sha": manifest.blob_sha,
        "scenarios": [
            {
                "covers": list(item.covers),
                "id": item.id,
                "kind": item.kind,
                **({"api": item.api} if manifest.schema_version == "3.0" else {}),
            }
            for item in manifest.scenarios
        ],
        "sha256": manifest.sha256,
    }
    if manifest.schema_version in {"2.0", "3.0"}:
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
            keys | ({"api"} if version == "3.0" else set()),
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
        parsed.append(Scenario(identifier, str(kind), covers, api))
    if len(parsed) != len({item.id for item in parsed}) or len(parsed) > 50:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return tuple(sorted(parsed, key=lambda item: item.id))


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
    )
    data = _exact_keys(raw, expected, "MALFORMED_CHARACTERIZATION_MANIFEST")
    scenarios = data["scenarios"]
    if version not in {"1.0", "2.0", "3.0"}:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    parsed = _scenario_rows(scenarios, version)
    obligations = _obligation_rows(data["obligations"], parsed) if version != "1.0" else ()
    transitions = _transition_rows(data["transitions"]) if version != "1.0" else ()
    for scenario in parsed:
        if scenario.api is not None and not any(
            item.scenario == scenario.id
            and item.category == "behavior"
            and item.selector == "$"
            and item.target in {scenario.api, scenario.covers[0]}
            for item in obligations
        ):
            raise CharacterizationError("MALFORMED_CHARACTERIZATION_MANIFEST")
    return Manifest(parsed, blob_sha, _sha256(content), obligations, transitions, version)


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
        if path.endswith((".py", ".pyi", ".cts", ".mts", ".ts", ".tsx"))
    }
    if len(profiles) != 1:
        raise CharacterizationError("MIXED_PROFILE_CHARACTERIZATION_SCENARIO")
    return profiles.pop()


def scenario_command(scenario: Scenario, language: str) -> list[str]:
    """Return the fixed recorded command; contracts cannot select executables."""
    driver, _ = _scenario_paths(scenario, language)
    if scenario.api is not None:
        return [
            "python3.12",
            "-P",
            "/collector/characterization_observer.py",
            "--api",
            scenario.api,
            "--driver",
            driver,
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
    retired = any(
        item.old_path is not None
        and policy.is_production_path(item.old_path)
        and item.new_path != item.old_path
        for item in changes
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
        facts[item.id].update(
            _api_oracle_review(repository, head_sha, item, facts[item.id], records)
        )
        if not admissible:
            blocks.append(f"INVALID_API_INTRODUCTION:{item.id}")
        if not facts[item.id]["oracle_review_valid"]:
            blocks.append(f"INVALID_API_ORACLE_REVIEW:{item.id}")
    return facts, blocks


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
            },
            "MALFORMED_API_ORACLE_REVIEW",
        )
    except (git_changes.GitError, CharacterizationError):
        return {"review_sha256": None, "intended_feature": None, "oracle_review_valid": False}
    valid = _valid_api_oracle_review(row, fact)
    return {
        "review_sha256": _sha256(blob.content),
        "intended_feature": row["intended_feature"] if valid else None,
        "oracle_review_valid": valid,
    }


def _valid_api_oracle_review(row: dict[str, Any], fact: dict[str, Any]) -> bool:
    source = fact["head_source"]
    return bool(
        row["schema_version"] == "1.0"
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
        and observation["codec"] == "python-values-v1"
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
    )


def _load_capture(path: Path, missing_code: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
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
    if manifest.schema_version in {"2.0", "3.0"}:
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
        if old.api != item.api:
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
    absent_scenarios: frozenset[str] = frozenset(),
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    blocks: list[str] = []
    rows = artifact.get("scenarios")
    if (
        artifact.get("schema_version")
        != (OBSERVED_CAPTURE_SCHEMA if manifest.schema_version == "3.0" else CAPTURE_SCHEMA)
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
        blocks.extend(_scenario_row_blocks(scenario, row, scenario.id in absent_scenarios))
    blocks.extend(_obligation_capture_blocks(manifest, by_id, absent_scenarios))
    expected_fingerprint = _sha256(
        _canonical(
            [[item.id, by_id[item.id].get("behavior_sha256")] for item in manifest.scenarios]
        )
    )
    if artifact.get("behavior_fingerprint") != expected_fingerprint:
        blocks.append("CHARACTERIZATION_FINGERPRINT_MISMATCH")
    return blocks, by_id


def _scenario_row_blocks(
    scenario: Scenario, row: dict[str, Any], absent: bool = False
) -> list[str]:
    blocks: list[str] = []
    if row.get("kind") != scenario.kind or row.get("covers") != list(scenario.covers):
        blocks.append(f"CHARACTERIZATION_DEFINITION_MISMATCH:{scenario.id}")
    if row.get("exit_code") != 0 or row.get("error") is not None:
        blocks.append(f"CHARACTERIZATION_EXECUTION_FAILED:{scenario.id}")
    if row.get("deterministic") is not True:
        blocks.append(f"CHARACTERIZATION_REPLAY_DRIFT:{scenario.id}")
    if not absent and row.get("behavior_sha256") != row.get("golden_behavior_sha256"):
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


def _valid_obligation_assertion(category: str, value: object) -> bool:
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
        if not _valid_obligation_assertion(obligation.category, selected):
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
    capture_blocks, rows = _capture_blocks(artifact, manifest, policy.language, absent)
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
    return _valid_obligation_assertion(obligation.category, selected)


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
    if not isinstance(value, list) or not value or len(value) > 50:
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
            **fact,
            "base_execution_verified": _api_capture_matches(base_rows.get(key), fact, "base"),
            "head_execution_verified": _api_capture_matches(head_rows.get(key), fact, "head"),
            "head_cases_sha256": head_rows.get(key, {}).get("behavior_sha256"),
        }
        for key, fact in sorted(facts.items())
    ]


def _result_api_facts(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 50:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    rows = [_result_api_fact(item) for item in value]
    names = [item["scenario"] for item in rows]
    if names != sorted(set(names)):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return {item["scenario"]: item for item in rows}


def _result_api_fact(value: object) -> dict[str, Any]:
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
        },
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
    if row["oracle_review_valid"] and (
        not isinstance(row["review_sha256"], str)
        or SHA256.fullmatch(row["review_sha256"]) is None
        or not isinstance(row["intended_feature"], str)
        or not row["intended_feature"].strip()
    ):
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    return row


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
    behavior_targets = {
        str(item["target"])
        for item in obligations
        if item["category"] == "behavior" and item["compatibility"] == "PASS"
    }
    expected_covered_obligations = tuple(
        target
        for target in required_obligations
        if target in behavior_targets or target.split("::", 1)[0] in behavior_targets
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
            for behavior in ((head,) if birth else (base, head))
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
    modern = schema_version in {RESULT_SCHEMA, OBSERVED_RESULT_SCHEMA}
    keys = {*common_keys, "obligations"} if modern else common_keys
    if schema_version == OBSERVED_RESULT_SCHEMA:
        keys.add("api_observations")
    if schema_version not in {LEGACY_RESULT_SCHEMA, RESULT_SCHEMA, OBSERVED_RESULT_SCHEMA} or set(
        value
    ) not in (
        keys,
        {*keys, "refactor_runnability"},
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
    schema_version = row["schema_version"]
    modern = schema_version in {RESULT_SCHEMA, OBSERVED_RESULT_SCHEMA}
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
        _result_api_facts(row["api_observations"])
        if schema_version == OBSERVED_RESULT_SCHEMA
        else {}
    )
    scenarios = _result_scenarios(row["scenarios"], api_facts)
    derived.extend(_result_api_blocks(api_facts, scenarios))
    obligations = _result_obligations(row["obligations"], scenarios, api_facts) if modern else []
    fingerprint_payload: object = (
        {
            "obligations": [[item["id"], item["head_assertion_sha256"]] for item in obligations],
            "scenarios": [[item["id"], item["head_behavior_sha256"]] for item in scenarios],
            **(
                {"api_observations": list(api_facts.values())}
                if schema_version == OBSERVED_RESULT_SCHEMA
                else {}
            ),
        }
        if modern
        else [[item["id"], item["head_behavior_sha256"]] for item in scenarios]
    )
    fingerprint = _sha256(_canonical(fingerprint_payload))
    if row["behavior_fingerprint"] != fingerprint:
        raise CharacterizationError("MALFORMED_CHARACTERIZATION_RESULT")
    derived.extend(_result_scenario_blocks(scenarios, blocks, api_facts))
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
                row["coverage"], scenarios, obligations, required_paths, required_targets
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
                        if manifest.schema_version == "3.0"
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
        "schema_version": OBSERVED_RESULT_SCHEMA
        if manifest.schema_version == "3.0"
        else RESULT_SCHEMA,
        **(
            {"api_observations": api_observations or []} if manifest.schema_version == "3.0" else {}
        ),
        "workflow_sha": workflow_sha,
    }


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
    policy_blob = git_changes.read_regular_blob(
        repository, base_sha, ".supportability.toml", records
    )
    policy = contract.parse_contract(policy_blob.content)
    candidate_blob = git_changes.read_regular_blob(
        repository, head_sha, ".supportability.toml", records
    )
    candidate_policy = contract.parse_contract(candidate_blob.content)
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
    from supportability_gate import (
        refactor_targets,
    )  # local: keep result validator dependency-light

    target_derivation_failed = False
    try:
        responsibility_targets, unbounded_paths = refactor_targets.derive(
            repository, identity, policy, changes, records
        )
    except git_changes.GitError:
        target_derivation_failed = True
        responsibility_targets = ()
        unbounded_paths = tuple(
            sorted(
                {
                    path
                    for change in changes
                    for path in (change.old_path, change.new_path)
                    if path is not None and policy.is_production_path(path)
                }
            )
        )
    deleted_paths = {
        item.old_path
        for item in changes
        if item.old_path and item.new_path is None and policy.is_production_path(item.old_path)
    }
    manifest = _manifest(repository, head_sha, records)
    api_facts, api_blocks = _api_bindings(
        repository, base_sha, head_sha, policy, manifest, changes, records
    )
    base, base_error = _load_capture(base_path, "MISSING_BASELINE")
    head, head_error = _load_capture(head_path, "INCOMPLETE_CHARACTERIZATION_EVIDENCE")
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
    covered_obligations = _verified_obligation_coverage(required_obligations, obligations)
    blocks.extend(
        f"MISSING_CHARACTERIZATION_COVERAGE:obligation:{target}"
        for target in required_obligations
        if target not in covered_obligations
    )
    runnable = not target_derivation_failed and _logical_step_runnable(
        manifest, base_rows, head_rows, responsibility_targets, policy.language, api_facts
    )
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


def _verified_obligation_coverage(
    required: list[str], obligations: list[dict[str, object]]
) -> list[str]:
    targets = {
        str(item["target"])
        for item in obligations
        if item["category"] == "behavior" and item["compatibility"] == "PASS"
    }
    return [item for item in required if item in targets or item.split("::", 1)[0] in targets]


def _write_json(path: Path, value: object) -> bytes:
    content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


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
    return 1 if overall == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(main())
