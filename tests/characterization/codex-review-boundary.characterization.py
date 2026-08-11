from __future__ import annotations

import importlib.util
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

HEAD = "a" * 40
RUN_ID = 12345
REQUESTED = "2026-08-11T12:00:00Z"
COMPLETED = "2026-08-11T12:01:00Z"


class Reply:
    def __init__(self, value: object) -> None:
        self.value = value

    def __enter__(self) -> Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.value, bytes):
            return self.value
        return json.dumps(self.value).encode()


def _module() -> Any:
    target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
    definition = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_DEFINITION"])
    relative = Path("src/supportability_gate/codex_review.py")
    source = target / relative
    if not source.is_file():
        source = definition / relative
    spec = importlib.util.spec_from_file_location("characterized_codex_review", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("missing Codex review characterization source")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    module = _module()
    paths: list[str] = []
    reaction_calls = 0

    def opener(request: Any, **kwargs: object) -> Reply:
        nonlocal reaction_calls
        if kwargs != {"timeout": 30}:
            raise RuntimeError("unexpected timeout")
        path = urllib.parse.urlparse(request.full_url).path
        paths.append(path)
        if path.endswith("comments"):
            comments = [
                {
                    "body": (
                        f"@codex review\n\nCodex-Review-Head: {HEAD}\nCodex-Review-Run: {RUN_ID}"
                    ),
                    "created_at": REQUESTED,
                    "id": 1,
                    "updated_at": REQUESTED,
                    "user": {"id": module.REQUESTER_ID},
                }
            ]
            if reaction_calls:
                comments.append(
                    {
                        "body": (
                            "Codex Review: Didn't find any major issues. Delightful!\n\n"
                            f"**Reviewed commit:** `{HEAD[:10]}`"
                        ),
                        "created_at": COMPLETED,
                        "user": {"id": module.CONNECTOR_ID},
                    }
                )
            return Reply(comments)
        if path.endswith("reactions"):
            reaction_calls += 1
            return Reply(
                [
                    {
                        "content": "eyes",
                        "created_at": REQUESTED,
                        "user": {"id": module.CONNECTOR_ID},
                    }
                ]
                if reaction_calls == 1
                else []
            )
        if path.endswith("jobs"):
            return Reply(
                {
                    "jobs": [
                        {
                            "conclusion": "success",
                            "head_sha": HEAD,
                            "id": 10,
                            "name": module.OBSERVER_JOB,
                            "run_id": RUN_ID,
                            "workflow_name": module.WORKFLOW_NAME,
                        }
                    ],
                    "total_count": 1,
                }
            )
        if path.endswith("logs"):
            return Reply(f"2026-08-11T12:00:00Z {module.OBSERVER_MARKER}1\n".encode())
        if path.endswith("reviews"):
            return Reply([])
        raise RuntimeError("unexpected endpoint")

    module.require_completion(
        "example/repository",
        7,
        HEAD,
        RUN_ID,
        "token",
        attempts=2,
        delay=0,
        opener=opener,
        sleeper=lambda _: None,
    )
    print(
        json.dumps(
            {
                "behavior": {"paths": paths, "reaction_calls": reaction_calls, "result": "PASS"},
                "scenario": "codex-review-boundary",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
