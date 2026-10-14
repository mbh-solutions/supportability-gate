"""Overlap trusted job setup while awaiting authenticated same-attempt producer artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile


def api(route: str) -> bytes:
    return subprocess.run(["gh", "api", route], check=True, capture_output=True, timeout=30).stdout


def producer_names(mode: str) -> tuple[str, ...]:
    if mode == "publish":
        return ("Deterministic Supportability Evidence",)
    if mode == "quality":
        return tuple(f"Quality Profile (Partition {index})" for index in range(4))
    if mode == "capture":
        return ("Characterize Base", "Characterize Head")
    raise ValueError("invalid producer group")


def completed_jobs(jobs: list[dict], names: tuple[str, ...]) -> dict[str, str] | None:
    outcomes = {}
    for name in names:
        matches = [job for job in jobs if job["name"] == name]
        if len(matches) > 1:
            raise ValueError("duplicate producer job")
        if not matches or matches[0]["status"] != "completed":
            return None
        outcomes[name] = matches[0]["conclusion"]
    return outcomes


def artifact_values(
    items: list[dict], name: str, root: str, head: str, run: str, repository_id: str, capture: bool
) -> dict[str, str]:
    matches = [item for item in items if item["name"] == name]
    if not matches:
        return {}
    if len(matches) != 1:
        raise ValueError("duplicate producer artifact")
    item = matches[0]
    identity = item["workflow_run"]
    if (
        item["expired"]
        or identity["head_sha"] != head
        or (str(identity["id"]) != run or str(identity["repository_id"]) != repository_id)
    ):
        raise ValueError("stale producer artifact")
    digest = item["digest"]
    if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("invalid artifact digest")
    result = {"artifact-id": str(int(item["id"])), "artifact-digest": digest[7:]}
    if capture:
        raw = api(f"{root}/actions/artifacts/{item['id']}/zip")
        if len(raw) > 128 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest[7:]:
            raise ValueError("artifact bytes differ from native digest")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if names.count("characterization.json") == 1:
                if archive.getinfo("characterization.json").file_size > 128 * 1024 * 1024:
                    raise ValueError("oversized characterization capture")
                result["capture-sha256"] = hashlib.sha256(
                    archive.read("characterization.json")
                ).hexdigest()
    return result


def main(mode: str) -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    run = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ["GITHUB_RUN_ATTEMPT"]
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None or not (
        run.isdigit() and attempt.isdigit()
    ):
        raise ValueError("invalid run identity")
    root = f"repos/{repository}"
    deadline = time.monotonic() + 900
    outcomes = None
    while time.monotonic() < deadline:
        jobs = json.loads(api(f"{root}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100"))
        outcomes = completed_jobs(jobs["jobs"], producer_names(mode))
        if outcomes is not None:
            break
        time.sleep(3)
    if outcomes is None:
        raise TimeoutError("producer jobs did not finish")
    if mode == "quality":
        return
    items = json.loads(api(f"{root}/actions/runs/{run}/artifacts?per_page=100"))["artifacts"]
    groups = (
        (("evidence", "supportability-evidence", "Deterministic Supportability Evidence"),)
        if mode == "publish"
        else (
            ("base", "characterization-base", "Characterize Base"),
            ("head", "characterization-head", "Characterize Head"),
        )
    )
    for prefix, artifact, job in groups:
        values = artifact_values(
            items,
            f"{artifact}-{run}-{attempt}",
            root,
            os.environ["HEAD_SHA"],
            run,
            os.environ["GITHUB_REPOSITORY_ID"],
            mode == "capture",
        )
        values["capture-outcome"] = outcomes[job]
        for key, value in values.items():
            print(f"{prefix}-{key}={value}")


if __name__ == "__main__":
    main(sys.argv[1])
