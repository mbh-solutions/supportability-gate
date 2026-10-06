"""Observe the real sandbox command's isolation and argument translation contract."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from supportability_gate import quality_runner


def _case(directory: Path, adapter: str) -> dict[str, object]:
    target = directory / "target"
    output = directory / adapter
    collector = directory / "collector"
    toolcache = directory / "toolcache"
    for path in (target, collector, toolcache):
        path.mkdir(exist_ok=True)
    plans = quality_runner.command_plans(
        "python", target, output, ("tests/probe.py",), ("src/probe.py",)
    )
    plan = next(item for item in plans if item.adapter == adapter)
    command = quality_runner.sandbox_command(
        plan, repository=target, output=output, collector=collector, toolcache=toolcache
    )
    actual_arguments = list(command[command.index(quality_runner.CONTAINER_IMAGE) + 1 :])
    expected_arguments = [
        item.replace(str(target), "/target").replace(str(output), "/work") for item in plan.actual
    ]
    mounts = [
        command[index + 1].split(",") for index, item in enumerate(command) if item == "--mount"
    ]
    writable = sorted(
        next(item.removeprefix("dst=") for item in mount if item.startswith("dst="))
        for mount in mounts
        if "readonly" not in mount
    )
    protected = ("/target", "/trusted", "/collector", "/evidence", "/opt/hostedtoolcache")
    readonly = [
        path
        for path in protected
        if any(f"dst={path}" in mount and "readonly" in mount for mount in mounts)
    ]
    environment = {
        value.split("=", 1)[0]: value.split("=", 1)[1]
        for index, item in enumerate(command)
        if item == "--env"
        for value in (command[index + 1],)
    }
    return {
        "input": {"adapter": adapter},
        "output": {
            "network": command[command.index("--network") + 1],
            "root_read_only": "--read-only" in command,
            "capabilities": command[command.index("--cap-drop") + 1],
            "security": command[command.index("--security-opt") + 1],
            "pids": command[command.index("--pids-limit") + 1],
            "memory": command[command.index("--memory") + 1],
            "cpus": command[command.index("--cpus") + 1],
            "tmpfs": command[command.index("--tmpfs") + 1],
            "workdir": command[command.index("--workdir") + 1],
            "readonly_mounts": readonly,
            "writable_mounts": writable,
            "fixed_environment": {
                name: environment.get(name) for name in ("CI", "NO_COLOR", "PYTHONHASHSEED")
            },
            "fixed_plan_preserved": actual_arguments == expected_arguments,
            "explicit_source_arguments": [
                value.replace("\\", "/")
                for value in actual_arguments
                if value.replace("\\", "/").startswith("/target/src/")
            ],
        },
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        cases = [
            _case(Path(temporary), adapter)
            for adapter in ("python.pytest.v1", "python.ruff-lint.v1")
        ]
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "sandbox-command-boundary",
                "behavior": {"cases": cases},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
