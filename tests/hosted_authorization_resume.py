"""Resume owner authorization over immutable hosted evidence, without target execution.

The read-only preparation/evaluation job and the check-writing publication job
use this fixed supervisor. Consumer files are parsed by the existing Gate only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import hosted_authorization_evidence as evidence

from supportability_gate import qualification_bundle, refactor_policy, standard_results

SOURCE_REPOSITORY = "mbh-solutions/supportability-gate"
SOURCE_PATH = ".github/workflows/organization-required.yml"
RESUME_PATH = ".github/workflows/authorization-resume.yml"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def _api(path: str, method: str = "GET", value: object | None = None) -> Any:
    arguments = ["gh", "api", path, "--method", method]
    raw = None
    if value is not None:
        arguments.extend(["--input", "-"])
        raw = json.dumps(value).encode()
    completed = subprocess.run(
        arguments,
        input=raw,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout)


def _pages(path: str, field: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    separator = "&" if "?" in path else "?"
    for page in range(1, 11):
        payload = _api(f"{path}{separator}per_page=100&page={page}")
        batch = payload[field] if field else payload
        if not isinstance(batch, list):
            raise ValueError("MALFORMED_GITHUB_RESPONSE")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise ValueError("GITHUB_RESPONSE_LIMIT")


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, sort_keys=True, indent=2).encode() + b"\n")


def _authorization_snapshot(comments: tuple[dict[str, Any], ...]) -> str:
    rows = [
        {"id": row["id"], "user_id": row["user"]["id"], "body": row["body"]}
        for row in comments
        if any(
            line.startswith(refactor_policy.AUTHORIZATION_PREFIX)
            for line in row.get("body", "").splitlines()
        )
    ]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _context() -> tuple[str, int, str]:
    repository = os.environ["GITHUB_REPOSITORY"]
    revision = os.environ["RESUME_WORKFLOW_SHA"]
    if (
        os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None
        or re.fullmatch(r"[0-9a-f]{40}", revision) is None
    ):
        raise ValueError("UNAUTHENTICATED_RESUME_CONTEXT")
    return repository, int(os.environ["GITHUB_REPOSITORY_ID"]), revision


def _pull_number(event: dict[str, Any]) -> int | None:
    event_name = os.environ["GITHUB_EVENT_NAME"]
    if event_name == "issue_comment" and "pull_request" in event.get("issue", {}):
        bodies = (
            event.get("comment", {}).get("body", ""),
            event.get("changes", {}).get("body", {}).get("from", ""),
        )
        if not any(
            line.startswith(refactor_policy.AUTHORIZATION_PREFIX)
            for body in bodies
            for line in body.splitlines()
        ):
            return None
        return int(event["issue"]["number"])
    if event_name == "workflow_run":
        run = event["workflow_run"]
        pulls = run["pull_requests"]
        if run["event"] == "pull_request" and len(pulls) == 1:
            return int(pulls[0]["number"])
    return None


def _latest_run(repository: str, pull: dict[str, Any]) -> dict[str, Any] | None:
    workflow = _api(f"repos/{SOURCE_REPOSITORY}/actions/workflows/organization-required.yml")
    runs = _pages(
        f"repos/{repository}/actions/runs?event=pull_request&head_sha={pull['head']['sha']}",
        "workflow_runs",
    )
    matches = [
        run
        for run in runs
        if run["workflow_id"] == workflow["id"]
        and run["path"] == SOURCE_PATH
        and run["repository"]["full_name"] == repository
        and any(item["number"] == pull["number"] for item in run["pull_requests"])
    ]
    return max(matches, key=lambda run: run["id"]) if matches else None


def _artifact(repository: str, run: dict[str, Any]) -> dict[str, Any]:
    name = f"supportability-evidence-{run['id']}-{run['run_attempt']}"
    rows = _pages(f"repos/{repository}/actions/runs/{run['id']}/artifacts", "artifacts")
    matches = [row for row in rows if row["name"] == name and not row["expired"]]
    if len(matches) != 1:
        raise ValueError("MISSING_OR_AMBIGUOUS_CAPTURE_ARTIFACT")
    artifact = matches[0]
    if (
        artifact["workflow_run"]["id"] != run["id"]
        or artifact["workflow_run"]["head_sha"] != run["head_sha"]
        or artifact["size_in_bytes"] > MAX_ARCHIVE_BYTES
        or re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", "")) is None
    ):
        raise ValueError("CAPTURE_ARTIFACT_IDENTITY_MISMATCH")
    return artifact


def _download(repository: str, artifact: dict[str, Any], destination: Path) -> None:
    completed = subprocess.run(
        ["gh", "api", f"repos/{repository}/actions/artifacts/{artifact['id']}/zip"],
        capture_output=True,
        check=True,
        timeout=60,
    )
    raw = completed.stdout
    if (
        len(raw) > MAX_ARCHIVE_BYTES
        or "sha256:" + hashlib.sha256(raw).hexdigest() != artifact["digest"]
    ):
        raise ValueError("CAPTURE_ARTIFACT_DIGEST_MISMATCH")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = qualification_bundle._archive_entries(archive)
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in entries.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _validate_capture(
    directory: Path, repository_id: int, revision: str, pull: dict[str, Any], run: dict[str, Any]
) -> dict[str, Any]:
    qualification_bundle.restore_bundle(directory / "qualification-bundle.zip")
    payload = json.loads((directory / "standard-results.json").read_bytes())
    expected = standard_results.RunIdentity(
        run["repository"]["full_name"],
        repository_id,
        pull["base"]["sha"],
        pull["head"]["sha"],
        revision,
        run["id"],
        run["run_attempt"],
    )
    standard_results.validate_payload(payload, expected)
    # This also reproduces the complete decision from its bound original sources.
    evidence.awaiting_standards(directory, expected)
    return payload


def prepare(directory: Path) -> None:
    repository, repository_id, revision = _context()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    number = _pull_number(event)
    if number is None:
        return
    pull = _api(f"repos/{repository}/pulls/{number}")
    run = _latest_run(repository, pull)
    if pull["state"] != "open" or run is None or run["status"] != "completed":
        return  # Completion event closes the comment-before-capture race.
    artifact = _artifact(repository, run)
    _download(repository, artifact, directory / "original")
    payload = _validate_capture(directory / "original", repository_id, revision, pull, run)
    record = {
        "schema_version": "authorization-resume.v1",
        "capture_artifact": {key: artifact[key] for key in ("id", "digest", "name")},
        "capture_identity": vars(evidence.identity(payload)),
        "pull_number": number,
        "resume_run_id": int(os.environ["GITHUB_RUN_ID"]),
        "resume_run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "resume_workflow_sha": revision,
    }
    _write(directory / "resume.json", record)
    _write(directory / "event.json", {"repository": run["repository"], "pull_request": pull})
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        stream.write(f"ready=true\nhead={payload['head_sha']}\n")


def evaluate(directory: Path, target: Path) -> None:
    repository, _, _ = _context()
    record = json.loads((directory / "resume.json").read_bytes())
    event = json.loads((directory / "event.json").read_bytes())
    original = directory / "original"
    payload = json.loads((original / "standard-results.json").read_bytes())
    sources = list(evidence.read_sources(original))
    comments = refactor_policy._github_comments(
        repository, record["pull_number"], os.environ["GH_TOKEN"]
    )
    predecessor = refactor_policy.PredecessorEvidence()
    try:
        refactor_policy._owner_authorization(event, comments)
    except refactor_policy.RefactorPolicyError:
        pass
    else:
        predecessor = refactor_policy._predecessor_authorization(
            repository, payload["base_sha"], os.environ["GH_TOKEN"]
        )
    sources[2] = refactor_policy.verify_refactor(target, event, sources[1], comments, predecessor)
    updated = standard_results.compose_results(
        *sources,
        evidence.identity(payload),
        expected_quality_artifact=evidence.quality_artifact(payload),
        expected_characterization_artifacts=sources[1]["artifacts"],
        source_outcomes={
            **payload["source_outcomes"],
            "refactor": "success" if sources[2]["overall_result"] == "PASS" else "failure",
        },
    )
    output = directory / "evaluated"
    output.mkdir(parents=True, exist_ok=True)
    for index, (name, value) in enumerate(zip(evidence.SOURCE_FILES, sources, strict=True)):
        if index == 2:
            _write(output / name, value)
        else:
            (output / name).write_bytes((original / name).read_bytes())
    record["authorization_comment_id"] = sources[2]["authorization_comment_id"]
    record["authorization_comments_sha256"] = _authorization_snapshot(comments)
    record["original_source_sha256"] = {
        name: hashlib.sha256((original / name).read_bytes()).hexdigest()
        for name in evidence.SOURCE_FILES
    }
    _write(output / "authorization-resume.json", record)
    _write(output / "standard-results.json", updated)
    qualification_bundle.build_bundle(output, output / "qualification-bundle.zip")
    qualification_bundle.restore_bundle(output / "qualification-bundle.zip")


def _current(record: dict[str, Any]) -> bool:
    repository, repository_id, revision = _context()
    identity = record["capture_identity"]
    if (
        identity["repository"] != repository
        or identity["repository_id"] != repository_id
        or identity["workflow_sha"] != revision
        or record["resume_workflow_sha"] != revision
        or record["resume_run_id"] != int(os.environ["GITHUB_RUN_ID"])
        or record["resume_run_attempt"] != int(os.environ["GITHUB_RUN_ATTEMPT"])
    ):
        raise ValueError("RESUME_IDENTITY_MISMATCH")
    pull = _api(f"repos/{repository}/pulls/{record['pull_number']}")
    run = _latest_run(repository, pull)
    comments = refactor_policy._github_comments(
        repository, record["pull_number"], os.environ["GH_TOKEN"]
    )
    return bool(
        pull["state"] == "open"
        and pull["head"]["sha"] == identity["head_sha"]
        and pull["base"]["sha"] == identity["base_sha"]
        and run is not None
        and run["id"] == identity["run_id"]
        and run["run_attempt"] == identity["run_attempt"]
        and run["status"] == "completed"
        and _authorization_snapshot(comments) == record["authorization_comments_sha256"]
    )


def _check_ids(repository: str, identity: standard_results.RunIdentity) -> dict[int, int]:
    rows = _pages(
        f"repos/{repository}/commits/{identity.head_sha}/check-runs?filter=all", "check_runs"
    )
    result: dict[int, int] = {}
    for number, name in enumerate(standard_results.CHECK_CONTEXTS, 1):
        external = f"supportability:{identity.run_id}:{identity.run_attempt}:{number}"
        matches = [
            row
            for row in rows
            if row["name"] == name and row["external_id"] == external and row["app"]["id"] == 15368
        ]
        if len(matches) != 1:
            raise ValueError("MISSING_OR_AMBIGUOUS_ORIGINAL_CHECK")
        result[number] = matches[0]["id"]
    return result


def invalidate() -> None:
    """Withhold a previous authorization success before any fallible reevaluation."""
    repository, _, _ = _context()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    number = _pull_number(event)
    if number is None:
        return
    pull = _api(f"repos/{repository}/pulls/{number}")
    run = _latest_run(repository, pull)
    if pull["state"] != "open" or run is None:
        return
    external = f"supportability:{run['id']}:{run['run_attempt']}:6"
    rows = _pages(
        f"repos/{repository}/commits/{pull['head']['sha']}/check-runs?filter=all", "check_runs"
    )
    matches = [
        row
        for row in rows
        if row["external_id"] == external
        and row["name"] == standard_results.CHECK_CONTEXTS[5]
        and row["app"]["id"] == 15368
    ]
    if not matches:
        return  # The initial publisher has not reached this check yet.
    if len(matches) != 1:
        raise ValueError("AMBIGUOUS_ORIGINAL_AUTHORIZATION_CHECK")
    if matches[0].get("conclusion") != "success":
        return  # Existing failures and authorization waits already prevent merge.
    _api(
        f"repos/{repository}/check-runs/{matches[0]['id']}",
        "PATCH",
        {
            "status": "in_progress",
            "output": {
                "title": "Evaluating current owner authorization",
                "summary": "Rechecking live owner evidence against the retained exact-change capture. Merge remains blocked if continuation cannot complete.",
            },
        },
    )


def publish(directory: Path) -> None:
    record = json.loads((directory / "authorization-resume.json").read_bytes())
    if not _current(record):
        print("STALE_AUTHORIZATION_CONTINUATION_NOT_PUBLISHED")
        return
    payload = json.loads((directory / "standard-results.json").read_bytes())
    identity = standard_results.RunIdentity(**record["capture_identity"])
    waiting = evidence.awaiting_standards(directory, identity)
    checks = _check_ids(identity.repository, identity)
    current_run = record["resume_run_id"]
    for row in payload["entries"]:
        if not _current(record):
            raise ValueError("PR_CHANGED_DURING_AUTHORIZATION_PUBLICATION")
        number = row["standard"]
        value: dict[str, Any] = {
            "status": "in_progress" if number in waiting else "completed",
            "details_url": f"https://github.com/{identity.repository}/actions/runs/{current_run}",
            "output": {
                "title": "Awaiting owner authorization"
                if number in waiting
                else row["check_context"],
                "summary": (
                    f"Capture run {identity.run_id}, attempt {identity.run_attempt}. "
                    f"Authorization evaluation run {current_run}. "
                    f"Owner comment: {record['authorization_comment_id']}.\n\n"
                    + json.dumps(row, sort_keys=True, indent=2)
                ),
            },
        }
        if number not in waiting:
            value["conclusion"] = (
                "success" if row["result"] in {"PASS", "NOT_APPLICABLE_SHORT_TASK"} else "failure"
            )
        _api(f"repos/{identity.repository}/check-runs/{checks[number]}", "PATCH", value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "evaluate", "publish", "invalidate"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--target", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.operation == "invalidate":
            invalidate()
            return 0
        if arguments.directory is None:
            raise ValueError("MISSING_EVIDENCE_DIRECTORY")
        if arguments.operation == "evaluate":
            if arguments.target is None:
                raise ValueError("MISSING_STATIC_TARGET_CHECKOUT")
            evaluate(arguments.directory, arguments.target)
        else:
            {"prepare": prepare, "publish": publish}[arguments.operation](arguments.directory)
        return 0
    except Exception as error:
        print(f"AUTHORIZATION_RESUME_FAILED:{type(error).__name__}:{error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
