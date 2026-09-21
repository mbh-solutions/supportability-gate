"""Independently verify source controls from a pinned trusted Gate revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supportability_gate import clause_inventory, quality_runner

_SHA = re.compile(r"^[0-9a-f]{40}$")
_ACTION = re.compile(r"uses:\s*([^\s#]+)")


@dataclass(frozen=True)
class IntegrityFailure(Exception):
    decision: str
    code: str
    detail: str


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _required(path: Path, code: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise IntegrityFailure("TECHNICAL_FAILURE", code, path.as_posix()) from error


def _manifest(root: Path) -> tuple[dict[str, Any], str]:
    path = root / "docs/source_integrity_manifest.json"
    content = _required(path, "MISSING_TRUSTED_MANIFEST")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise IntegrityFailure("TECHNICAL_FAILURE", "MALFORMED_TRUSTED_MANIFEST", "json") from error
    if not isinstance(data, dict) or data.get("schema_version") != "source-integrity.v1":
        raise IntegrityFailure("TECHNICAL_FAILURE", "MALFORMED_TRUSTED_MANIFEST", "schema")
    return data, hashlib.sha256(content).hexdigest()


def _verify_identity(repository: Path, expected: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode or completed.stdout.strip() != expected:
        raise IntegrityFailure(
            "TECHNICAL_FAILURE", "HEAD_IDENTITY_MISMATCH", completed.stdout.strip()
        )


def _verify_immutable(repository: Path, manifest: dict[str, Any]) -> dict[str, str]:
    receipts: dict[str, str] = {}
    for name, expected in manifest["immutable_files"].items():
        path = repository / name
        if not path.is_file():
            raise IntegrityFailure("BLOCK", f"IMMUTABLE_SOURCE_MISSING:{name}", name)
        actual = _sha(path)
        receipts[name] = actual
        if actual != expected:
            raise IntegrityFailure("BLOCK", f"IMMUTABLE_SOURCE_MISMATCH:{name}", actual)
    return receipts


def _verify_inventory(repository: Path) -> None:
    try:
        clause_inventory.validate_inventory(
            _required(repository / "docs/supportability_standard.md", "MISSING_STANDARD"),
            _required(repository / "docs/normative_clause_inventory.json", "MISSING_INVENTORY"),
        )
    except clause_inventory.ClauseInventoryError as error:
        raise IntegrityFailure(error.decision, f"INVENTORY:{error.code}", error.location) from error


def _workflow_lines(content: str) -> set[str]:
    return {
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _verify_candidate_workflow(repository: Path, manifest: dict[str, Any]) -> None:
    content = _required(
        repository / ".github/workflows/source-validation.yml", "MISSING_SOURCE_VALIDATION"
    ).decode("utf-8")
    lines = _workflow_lines(content)
    missing = next(
        (line for line in manifest["source_validation_lines"] if line not in lines), None
    )
    if missing:
        raise IntegrityFailure("BLOCK", "SOURCE_VALIDATION_CONTROL_MISSING", missing)
    actions = [match.group(1) for match in _ACTION.finditer(content)]
    for name in manifest["source_validation_actions"]:
        if not any(
            value.startswith(name + "@") and _SHA.fullmatch(value.rsplit("@", 1)[1])
            for value in actions
        ):
            raise IntegrityFailure("BLOCK", "SOURCE_VALIDATION_ACTION_UNPINNED", name)
    if {"if: false", "continue-on-error: true"} & lines:
        raise IntegrityFailure("BLOCK", "SOURCE_VALIDATION_CONTROL_DISABLED", "workflow")


def _verify_trusted_pins(root: Path, manifest: dict[str, Any]) -> None:
    workflow = _required(
        root / ".github/workflows/source-integrity.yml", "MISSING_TRUSTED_WORKFLOW"
    ).decode()
    lock = _required(root / "requirements-dev.lock", "MISSING_TRUSTED_LOCK").decode()
    if any(value not in workflow for value in manifest["trusted_action_uses"]):
        raise IntegrityFailure("TECHNICAL_FAILURE", "TRUSTED_ACTION_PIN_MISMATCH", "workflow")
    if any(
        not re.search(rf"(?m)^{re.escape(value)}\s+\\$", lock)
        for value in manifest["trusted_tool_pins"]
    ):
        raise IntegrityFailure("TECHNICAL_FAILURE", "TRUSTED_TOOL_PIN_MISMATCH", "lock")


def _receipts(repository: Path, manifest: dict[str, Any]) -> dict[str, str]:
    paths = [*manifest["immutable_files"], *manifest["required_candidate_paths"]]
    result: dict[str, str] = {}
    for name in dict.fromkeys(paths):
        result[name] = hashlib.sha256(
            _required(repository / name, "REQUIRED_SOURCE_MISSING")
        ).hexdigest()
    return result


def _run_conformance(repository: Path, trusted: Path, scratch: Path) -> dict[str, Any]:
    adapter = "source-integrity-conformance.v1"
    output = quality_runner.command_work_directory(scratch, adapter) / "conformance.json"
    plan = quality_runner.CommandPlan(
        adapter,
        (
            str(Path(sys.executable).resolve()),
            "-I",
            "/collector",
            "--repository",
            "/target",
            "--output",
            "/work/conformance.json",
        ),
        ("trusted-conformance",),
        "source-integrity",
        (),
    )
    command = quality_runner.sandbox_command(
        plan,
        repository=repository,
        output=scratch,
        collector=trusted / "tests/source_integrity_conformance.py",
        workdir="/target",
    )
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise IntegrityFailure(
            "TECHNICAL_FAILURE", "SOURCE_INTEGRITY_RUNTIME_FAILED", type(error).__name__
        ) from error
    (scratch / "conformance.stdout.log").write_bytes(completed.stdout[-65536:])
    (scratch / "conformance.stderr.log").write_bytes(completed.stderr[-65536:])
    try:
        data = json.loads(output.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrityFailure(
            "TECHNICAL_FAILURE", "MISSING_CONFORMANCE_RESULT", str(completed.returncode)
        ) from error
    if not isinstance(data, dict):
        raise IntegrityFailure("TECHNICAL_FAILURE", "MALFORMED_CONFORMANCE_RESULT", "document")
    expected = {0: "PASS", 1: "BLOCK", 2: "TECHNICAL_FAILURE"}.get(completed.returncode)
    if expected is None or data.get("status") != expected:
        raise IntegrityFailure(
            "TECHNICAL_FAILURE", "MALFORMED_CONFORMANCE_RESULT", str(completed.returncode)
        )
    if expected != "PASS":
        raise IntegrityFailure(expected, str(data.get("code", "CONFORMANCE_FAILED")), "conformance")
    return data


def _payload(args: argparse.Namespace, decision: str, code: str, **facts: Any) -> dict[str, Any]:
    return {
        "schema_version": "source-integrity-result.v1",
        "decision": decision,
        "code": code,
        "repository": args.repository_name,
        "repository_id": args.repository_id,
        "head_sha": args.head_sha,
        "workflow_sha": args.workflow_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        **facts,
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--trusted-root", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository-name", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--workflow-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    args = parser.parse_args(argv)
    facts: dict[str, Any] = {}
    try:
        manifest, manifest_sha = _manifest(args.trusted_root)
        if (
            args.repository_name != manifest["repository"]
            or args.repository_id != manifest["repository_id"]
        ):
            raise IntegrityFailure(
                "TECHNICAL_FAILURE", "REPOSITORY_IDENTITY_MISMATCH", args.repository_name
            )
        if not _SHA.fullmatch(args.head_sha) or not _SHA.fullmatch(args.workflow_sha):
            raise IntegrityFailure("TECHNICAL_FAILURE", "INVALID_SOURCE_IDENTITY", "sha")
        _verify_identity(args.repository, args.head_sha)
        _verify_trusted_pins(args.trusted_root, manifest)
        facts = {
            "manifest_sha256": manifest_sha,
            "immutable_receipts": _verify_immutable(args.repository, manifest),
        }
        _verify_inventory(args.repository)
        _verify_candidate_workflow(args.repository, manifest)
        before = _receipts(args.repository, manifest)
        conformance = _run_conformance(args.repository, args.trusted_root, args.scratch)
        if before != _receipts(args.repository, manifest):
            raise IntegrityFailure(
                "TECHNICAL_FAILURE", "SOURCE_CHANGED_DURING_VALIDATION", "receipts"
            )
        facts.update(
            {
                "source_receipts": before,
                "conformance": conformance,
                "sandbox": {
                    "image": quality_runner.CONTAINER_IMAGE,
                    "platform": quality_runner.CONTAINER_PLATFORM,
                    "network": "none",
                    "read_only": True,
                    "cap_drop": "ALL",
                },
            }
        )
        result = _payload(args, "PASS", "SOURCE_INTEGRITY_PASS", **facts)
        exit_code = 0
    except IntegrityFailure as error:
        result = _payload(args, error.decision, error.code, detail=error.detail, **facts)
        exit_code = 1 if error.decision == "BLOCK" else 2
    _write(args.output, result)
    print(result["code"])
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
