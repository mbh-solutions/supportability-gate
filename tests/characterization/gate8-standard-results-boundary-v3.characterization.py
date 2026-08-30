from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import inspect
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

LEGACY_DRIVER_SHA256 = "d8413002ab34794c7cf1b71e3ec893dbf8cea62c9c8bb98d6ccc96952d97f3fc"
LEGACY_STANDARD_RESULTS_SHA256 = "43b1e96099a314aac1f2059589705161b5681157e07a752674aac9748d551f5b"
LEGACY_GATE_EIGHT_STANDARD_RESULTS_SHA256 = (
    "942d2127b636240bb3695ce489fccd7a27736a82b358fc4fe9db9a33d355ede0"
)
FORGED_BOUNDARIES = (("tests/characterization/forged.py", "function", "forged"),)
GATE_SIX_EVIDENCE_SOURCES = [
    "refactor-policy-result.json",
    "characterization-result.json:refactor_runnability",
    "complexity-result.json:responsibility_targets",
    "complexity-result.json:unbounded_production_paths",
    "complexity-result.json:review_evidence.incremental_refactor",
]
GATE_EIGHT_EVIDENCE_SOURCES = [
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
]
HANDOFF_SENTINEL = "DERIVED_FROM_AUTHENTICATED_EVIDENCE"


def _legacy_driver(definition: Path) -> ModuleType:
    path = definition / "tests/characterization/standard-results-boundary.characterization.py"
    if hashlib.sha256(path.read_bytes()).hexdigest() != LEGACY_DRIVER_SHA256:
        raise RuntimeError("changed standard-results characterization driver")
    spec = importlib.util.spec_from_file_location("standard_results_boundary", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("missing standard-results characterization driver")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _gate_six_derivation_probe(module: ModuleType) -> dict[str, str]:
    original_read = module.git_changes.read_regular_blob
    original_lines = module.git_changes.changed_head_lines
    original_base_lines = module.git_changes.changed_base_lines
    module.git_changes.read_regular_blob = lambda *args: SimpleNamespace(
        content=b"def calculate(value: int) -> int:\n    return value + 1\n"
    )
    module.git_changes.changed_head_lines = lambda *args, **kwargs: [1, 2]
    module.git_changes.changed_base_lines = lambda *args, **kwargs: [1, 2, 3]
    try:
        derive = module.derive if hasattr(module, "derive") else module._target_identities
        python_targets = derive(
            Path("."),
            SimpleNamespace(base_sha="a" * 40, head_sha="b" * 40),
            SimpleNamespace(
                language="python", is_production_path=lambda path: path.startswith("src/")
            ),
            (SimpleNamespace(old_path=None, new_path="src/sample.py"),),
            [],
        )
        module.git_changes.read_regular_blob = lambda _, sha, path, __: SimpleNamespace(
            content=(
                b"export function Card(value: number): number {\n  return value + 1;\n}\n"
                if sha == "a" * 40
                else b"export function Card(value: number) {\n"
                b"  return <section>{value + 2}</section>;\n}\n"
            )
        )
        frontend_targets = derive(
            Path("."),
            SimpleNamespace(base_sha="a" * 40, head_sha="b" * 40),
            SimpleNamespace(
                language="typescript", is_production_path=lambda path: path.startswith("src/")
            ),
            (SimpleNamespace(old_path="src/sample.ts", new_path="src/sample.tsx"),),
            [],
        )
    finally:
        module.git_changes.read_regular_blob = original_read
        module.git_changes.changed_head_lines = original_lines
        module.git_changes.changed_base_lines = original_base_lines
    if python_targets != (("src/sample.py::function:calculate:1-2",), ()) or frontend_targets != (
        ("src/sample.tsx::component:Card:1-3",),
        (),
    ):
        raise RuntimeError("Gate 6 target derivation is not exact")
    return {
        "src/sample.py": python_targets[0][0],
        "src/sample.tsx": frontend_targets[0][0],
    }


def _refactor_policy_probe(module: ModuleType, target: str) -> bool:
    base_sha, head_sha, workflow_sha = "a" * 40, "b" * 40, "c" * 40
    path = target.split("::", 1)[0]
    originals = (
        module.git_changes.validate_repository,
        module.git_changes.inspect_repository,
        module.git_changes.read_regular_blob,
        module.git_changes.changed_paths,
        module.contract.parse_contract,
    )
    target_owner = module.refactor_targets if hasattr(module, "refactor_targets") else module
    target_name = "derive" if hasattr(module, "refactor_targets") else "_target_identities"
    original_targets = getattr(target_owner, target_name)
    module.git_changes.validate_repository = lambda repository, records: repository
    module.git_changes.inspect_repository = lambda *args: SimpleNamespace()
    module.git_changes.read_regular_blob = lambda *args: SimpleNamespace(content=b"")
    module.git_changes.changed_paths = lambda *args: (
        SimpleNamespace(old_path=None, new_path=path),
    )
    module.contract.parse_contract = lambda content: SimpleNamespace(
        is_production_path=lambda candidate: candidate.startswith("src/")
    )
    setattr(target_owner, target_name, lambda *args: ((target,), ()))
    try:
        event = {
            "repository": {"full_name": "acme/repo"},
            "pull_request": {
                "base": {"sha": base_sha},
                "head": {"sha": head_sha},
                "number": 7,
            },
        }
        authorization = {
            "base_sha": base_sha,
            "broad": False,
            "head_sha": head_sha,
            "repository": "acme/repo",
            "schema_version": "1.0",
            "scope": [path],
            "sequence": {"predecessor_sha": base_sha, "step": 1},
            "targets": [target],
        }
        characterization = {
            "base_sha": base_sha,
            "coverage": {"covered_paths": [path], "required_paths": [path]},
            "head_sha": head_sha,
            "overall_result": "PASS",
            "policy_blocks": [],
            "refactor_runnability": {
                "base_sha": base_sha,
                "head_sha": head_sha,
                "repository": "github.com/acme/repo",
                "runnable": True,
                "schema_version": "refactor-runnability.v1",
                "targets": [target],
                "unbounded_paths": [],
                "workflow_sha": workflow_sha,
            },
            "repository": "github.com/acme/repo",
            "scenarios": [{"compatibility": "PASS", "covers": [path]}],
            "schema_version": "characterization-result.v1",
            "workflow_sha": workflow_sha,
        }
        result = module.verify_refactor(
            Path("."),
            event,
            characterization,
            (
                {
                    "body": module.AUTHORIZATION_PREFIX
                    + json.dumps(authorization, separators=(",", ":"), sort_keys=True),
                    "id": 11,
                    "user": {"id": module.TRUSTED_OWNER_ID},
                },
            ),
        )
    finally:
        (
            module.git_changes.validate_repository,
            module.git_changes.inspect_repository,
            module.git_changes.read_regular_blob,
            module.git_changes.changed_paths,
            module.contract.parse_contract,
        ) = originals
        setattr(target_owner, target_name, original_targets)
    if (
        result["applicable"] is not True
        or result["authorization_comment_id"] != 11
        or result["changed_paths"] != [path]
        or result["overall_result"] != "PASS"
        or result["policy_blocks"] != []
        or result["targets"] != [target]
        or result["unbounded_paths"] != []
    ):
        raise RuntimeError("Gate 6 producer is not independently runnable")
    return True


def _gate_seven_probe(
    quality_profile: ModuleType,
    quality_runner: ModuleType,
    standard_results: ModuleType,
    target: Path,
) -> None:
    schema = quality_profile.SCHEMA_VERSION
    if schema not in {
        "quality-gates.v3",
        "quality-gates.v4",
        "quality-gates.v5",
        "quality-gates.v6",
        "quality-gates.v7",
    }:
        raise RuntimeError("unsupported Gate 7 characterization schema")
    argv_current = schema in {
        "quality-gates.v4",
        "quality-gates.v5",
        "quality-gates.v6",
        "quality-gates.v7",
    }
    manifest_current = schema in {"quality-gates.v5", "quality-gates.v6", "quality-gates.v7"}
    asset_current = schema in {"quality-gates.v6", "quality-gates.v7"}
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        python_output = directory / "python"
        typescript_output = directory / "typescript"
        python_plans = quality_runner.command_plans(
            "python",
            target,
            python_output,
            ("tests/test_quality_profile.py",),
            ("src/supportability_gate/quality_profile.py",),
        )
        typescript_plans = quality_runner.command_plans(
            "typescript",
            target,
            typescript_output,
            ("tests/quality.test.mjs",),
            ("src/presentation/Card.tsx",),
        )
        pytest_plan = next(item for item in python_plans if item.adapter == "python.pytest.v1")
        ruff_plan = next(item for item in python_plans if item.adapter == "python.ruff-lint.v1")
        prettier_plan = next(
            item for item in typescript_plans if item.adapter == "typescript.prettier.v1"
        )
        config = python_output / "coverage.ini"
        rcfile_bound = any(
            item.startswith("--rcfile=") and Path(item.removeprefix("--rcfile=")) == config
            for item in pytest_plan.actual
        )
        if argv_current:
            config.write_bytes(b"[report]\nexclude_lines =\n    .+\n")
            restored = quality_runner._write_coverage_config(python_output)
            config_bound = (
                restored == config and config.read_bytes() == b"[report]\nexclude_lines =\n"
            )
        else:
            config_bound = not config.exists() and not hasattr(
                quality_runner, "_write_coverage_config"
            )

    command_fields: dict[str, object] = {
        "adapter": ruff_plan.adapter,
        "arguments": ruff_plan.evidence,
        "proof_kind": ruff_plan.proof_kind,
        "observed_paths": ("src/supportability_gate/quality_profile.py",),
        "zero_statement_paths": (),
        "executed": True,
        "exit_code": 0,
        "stderr_sha256": "a" * 64,
        "stdout_sha256": "b" * 64,
        "raw_proof_sha256": "c" * 64,
    }
    if argv_current:
        command_fields["executed_arguments"] = ruff_plan.actual
    command = quality_profile.GateResult(**command_fields)
    evidence_fields: dict[str, object] = dict(
        base_sha="a" * 40,
        changed_paths=("src/supportability_gate/quality_profile.py",),
        commands=(command,),
        exclusions=(),
        head_sha="b" * 40,
        high_risk_paths=("src/supportability_gate/quality_profile.py",),
        language="python",
        maximum_complexity=10,
        production_files=("src/supportability_gate/quality_profile.py",),
        production_paths=("src",),
        repository="acme/repo",
        repository_id="123",
        repository_remote="github.com/acme/repo",
        run_attempt="1",
        run_id="456",
        runner_environment="github-hosted",
        schema_version=schema,
        workflow_sha="d" * 40,
        job="quality-profile",
        artifact_id="789",
        artifact_digest="e" * 64,
        capture_sha256="f" * 64,
    )
    if manifest_current:
        evidence_fields["test_files"] = ("tests/test_quality_profile.py",)
    if asset_current:
        evidence_fields["asset_receipts"] = ()
        evidence_fields["source_files"] = ("src/supportability_gate/quality_profile.py",)
    evidence = quality_profile.QualityEvidence(**evidence_fields)
    decision = quality_profile.decision_payload(evidence)
    provenance = quality_profile.provenance_payload(evidence)
    executed_bound = "executed_arguments" in provenance["commands"][0]
    reconstructed = not hasattr(standard_results, "_s02_quality_capture")
    argv_bound = not manifest_current
    if manifest_current:
        standard_results._s02_quality_argv(decision, provenance, "MALFORMED_QUALITY_RESULT_BINDING")
        forged = json.loads(json.dumps(provenance))
        forged["commands"][0]["executed_arguments"] = ["python", "unsafe.py"]
        try:
            standard_results._s02_quality_argv(decision, forged, "MALFORMED_QUALITY_RESULT_BINDING")
        except standard_results.StandardResultsError as error:
            argv_bound = error.code == "MALFORMED_QUALITY_RESULT_BINDING"
        original = {
            **decision,
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
            "commands": [{**decision["commands"][0], **provenance["commands"][0]}],
        }
        expected = hashlib.sha256(
            (json.dumps(original, indent=2, sort_keys=True) + "\n").encode()
        ).hexdigest()
        reconstructed = standard_results._s02_quality_capture(decision, provenance) == expected
    valid = (
        config_bound
        and rcfile_bound is argv_current
        and ("--no-editorconfig" in prettier_plan.actual) is argv_current
        and executed_bound is argv_current
        and "executed_arguments" not in decision["commands"][0]
        and ("test_files" in decision) is manifest_current
        and ("asset_receipts" in decision) is asset_current
        and ("source_files" in decision) is asset_current
        and argv_bound
        and reconstructed
    )
    if not valid:
        raise RuntimeError(
            "Gate 7 fixed evidence contract is not preserved: "
            f"config={config_bound}, rcfile={rcfile_bound}, "
            f"prettier={'--no-editorconfig' in prettier_plan.actual}, "
            f"executed={executed_bound}, argv={argv_bound}, reconstructed={reconstructed}"
        )


def main() -> None:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    definition = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"])
    sys.path.insert(0, str(target / "src"))

    from supportability_gate import (  # noqa: PLC0415
        quality_profile,
        quality_runner,
        refactor_policy,
        review_evidence,
        standard_results,
    )

    gate_six_binding = "responsibility_targets" in getattr(
        standard_results, "_S02_COMPLEXITY_KEYS", ()
    )
    gate_eight_binding = "review_evidence_binding" in getattr(
        standard_results, "_S02_COMPLEXITY_KEYS", ()
    )
    if not gate_six_binding:
        legacy_source = target / "src/supportability_gate/standard_results.py"
        definition_source = definition / "src/supportability_gate/standard_results.py"
        if (
            hashlib.sha256(legacy_source.read_bytes()).hexdigest() != LEGACY_STANDARD_RESULTS_SHA256
            or hashlib.sha256(definition_source.read_bytes()).hexdigest()
            == LEGACY_STANDARD_RESULTS_SHA256
        ):
            raise RuntimeError("Gate 6 binding is missing outside the exact pre-S08 baseline")
    if not gate_eight_binding:
        legacy_source = target / "src/supportability_gate/standard_results.py"
        definition_source = definition / "src/supportability_gate/standard_results.py"
        if (
            hashlib.sha256(legacy_source.read_bytes()).hexdigest()
            != LEGACY_GATE_EIGHT_STANDARD_RESULTS_SHA256
            or hashlib.sha256(definition_source.read_bytes()).hexdigest()
            == LEGACY_GATE_EIGHT_STANDARD_RESULTS_SHA256
        ):
            raise RuntimeError("Gate 8 binding is missing outside the exact protected S09 baseline")
    if gate_six_binding:
        from supportability_gate import refactor_targets  # noqa: PLC0415

        target_deriver = refactor_targets
    else:
        target_deriver = refactor_policy
    derived_targets = _gate_six_derivation_probe(target_deriver)
    producer_runnable = _refactor_policy_probe(refactor_policy, derived_targets["src/sample.py"])
    _gate_seven_probe(quality_profile, quality_runner, standard_results, target)

    legacy = _legacy_driver(definition)
    original_characterization = legacy._characterization
    original_complexity = legacy._complexity
    original_review_evidence = legacy._review_evidence
    original_refactor = legacy._refactor
    original_run_case = legacy._run_case
    legacy_cases = (
        json.loads(
            (
                definition / "tests/characterization/gate8-standard-results-boundary-v3.golden.json"
            ).read_bytes()
        )["cases"]
        if not gate_eight_binding
        else {}
    )

    def complexity_fixture(
        identity: Any, standard_sha256: str, path: str, status: str
    ) -> dict[str, Any]:
        value = original_complexity(identity, standard_sha256, path, status)
        if not gate_six_binding:
            return value
        targets = [derived_targets[path]] if path in derived_targets else []
        value["responsibility_targets"] = targets
        value["unbounded_production_paths"] = []
        if gate_eight_binding:
            value["review_evidence_binding"] = {
                "base": {"blob_sha": "7" * 40, "sha256": "7" * 64},
                "head": {"blob_sha": "8" * 40, "sha256": "8" * 64},
            }
        return value

    def characterization_fixture(identity: Any, path: str) -> dict[str, Any]:
        value = original_characterization(identity, path)
        if not gate_six_binding:
            return value
        targets = [derived_targets[path]] if path in derived_targets else []
        value["refactor_runnability"] = {
            "base_sha": identity.base_sha,
            "head_sha": identity.head_sha,
            "repository": f"github.com/{identity.repository}",
            "runnable": producer_runnable,
            "schema_version": "refactor-runnability.v1",
            "targets": targets,
            "unbounded_paths": [],
            "workflow_sha": identity.workflow_sha,
        }
        return value

    def review_fixture() -> dict[str, object]:
        value = original_review_evidence()
        if (
            "expected_boundaries"
            in inspect.signature(review_evidence.evaluate_review_evidence).parameters
        ):
            separation = value["separation_of_concerns"]
            if not isinstance(separation, dict):
                raise RuntimeError("invalid legacy review fixture")
            separation["boundaries"] = []
        value["review_handoff"] = {
            "remaining_risks": [HANDOFF_SENTINEL],
            "summary": HANDOFF_SENTINEL,
        }
        return value

    def refactor_fixture(
        identity: Any, characterization: dict[str, Any], path: str
    ) -> dict[str, Any]:
        value = original_refactor(identity, characterization, path)
        if gate_six_binding:
            value["predecessor"] = {
                "authorization": None,
                "authorization_comment_id": None,
                "base_sha": None,
                "block": None,
                "head_sha": None,
                "merge_sha": None,
                "pull_number": None,
            }
        if gate_six_binding and path.startswith("src/"):
            target_identity = derived_targets[path]
            value.update(
                {
                    "applicable": True,
                    "authorization": {
                        "base_sha": identity.base_sha,
                        "broad": False,
                        "head_sha": identity.head_sha,
                        "repository": identity.repository,
                        "scope": [path],
                        "sequence": {"predecessor_sha": identity.base_sha, "step": 1},
                        "targets": [target_identity],
                    },
                    "authorization_comment_id": 11,
                    "targets": [target_identity],
                }
            )
        return value

    def run_case(
        directory: Path,
        name: str,
        inputs: dict[str, dict[str, Any]],
        identity: Any,
        producer: Any,
        enforcer: Any,
    ) -> dict[str, object]:
        active_enforcer = enforcer
        if gate_eight_binding:

            def enforce(arguments: list[str]) -> int:
                return enforcer.main(
                    [
                        *arguments,
                        "--complexity-result",
                        str(directory / f"{name}-complexity.json"),
                        "--quality-provenance",
                        str(directory / f"{name}-quality.json"),
                    ]
                )

            active_enforcer = SimpleNamespace(main=enforce)
        case = original_run_case(directory, name, inputs, identity, producer, active_enforcer)
        source = (
            json.loads((directory / f"{name}-standard-results.json").read_bytes())
            if gate_eight_binding
            else legacy_cases[name]
        )
        case["review_handoff"] = source["review_handoff"]
        case["review_handoff_sha256"] = source["review_handoff_sha256"]
        return case

    legacy._characterization = characterization_fixture
    legacy._complexity = complexity_fixture
    legacy._review_evidence = review_fixture
    legacy._refactor = refactor_fixture
    legacy._run_case = run_case
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        legacy.main()
    payload: dict[str, Any] = json.loads(output.getvalue())
    for case in payload["behavior"]["cases"].values():
        case["applicability_evidence"]["source_sha256"] = "normalized"
        handoff = case["review_handoff"]
        if handoff is not None:
            coverage = handoff["coverage"]
            asset_current = quality_profile.SCHEMA_VERSION in {
                "quality-gates.v6",
                "quality-gates.v7",
            }
            if asset_current:
                if coverage.get("asset_receipts") != [] or coverage.get(
                    "source_files"
                ) != coverage.get("production_files"):
                    raise RuntimeError("Gate 7 v6 asset coverage is incomplete")
            elif "asset_receipts" in coverage or "source_files" in coverage:
                raise RuntimeError("pre-v6 Gate 7 exposed asset coverage")
            coverage["asset_receipts"] = []
            coverage["source_files"] = coverage["production_files"]
            identity = handoff["identity"]
            identity["complexity_result_sha256"] = "normalized"
            quality_artifact = identity["quality_artifact"]
            if quality_artifact is not None:
                quality_artifact["capture_sha256"] = "normalized"
            if identity["quality_provenance_sha256"] is not None:
                identity["quality_provenance_sha256"] = "normalized"
            handoff["sources"]["coverage"]["sha256"] = "normalized"
            handoff["sources"]["identity"]["sha256"] = "normalized"
            case["review_handoff_sha256"] = "normalized"
        evidence_sources = case["rows"][5]["evidence_sources"]
        if gate_six_binding and evidence_sources != GATE_SIX_EVIDENCE_SOURCES:
            raise RuntimeError("Gate 6 evidence sources are incomplete")
        case["rows"][5]["evidence_sources"] = GATE_SIX_EVIDENCE_SOURCES
        case["rows"][7]["evidence_sources"] = GATE_EIGHT_EVIDENCE_SOURCES
    if not gate_eight_binding:
        for name in ("gate-7-technical", "simultaneous"):
            case = payload["behavior"]["cases"][name]
            error = case["lane_failures"][-1]["technical_errors"]
            case["lane_failures"].append(
                {"policy_blocks": [], "standard": 8, "technical_errors": error}
            )
            case["enforcer_exits"][7] = 2
            case["rows"][7]["result"] = "TECHNICAL_FAILURE"
            case["shared_failures"].append(
                {
                    "affected_standards": [7, 8],
                    "code": error[0],
                    "dependency": "quality-profile:artifact-binding",
                    "kind": "TECHNICAL_ERROR",
                }
            )

    content = (target / ".supportability-review.toml").read_bytes()
    if (
        "expected_boundaries"
        in inspect.signature(review_evidence.evaluate_review_evidence).parameters
    ):
        parsed, blocks = review_evidence.evaluate_review_evidence(content, None)
        forged, forged_blocks = review_evidence.evaluate_review_evidence(content, FORGED_BOUNDARIES)
        if forged is not None or forged_blocks != (
            "INSUFFICIENT_REVIEW_EVIDENCE:separation_of_concerns.boundaries",
        ):
            raise RuntimeError("Gate 2 boundary comparison is not enforced")
    else:
        parsed, blocks = review_evidence.evaluate_review_evidence(content)
    payload["behavior"]["review_evidence"] = {
        "accepted": parsed is not None,
        "blocks": list(blocks),
    }
    payload["behavior"]["schema_version"] = "standard-results.v3"
    payload["scenario"] = "gate8-standard-results-boundary-v3"
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
