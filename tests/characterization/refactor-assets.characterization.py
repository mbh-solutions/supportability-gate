from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from supportability_gate import refactor_targets


def main() -> None:
    original_blob = refactor_targets.git_changes.read_regular_blob
    original_base_lines = refactor_targets.git_changes.changed_base_lines
    original_head_lines = refactor_targets.git_changes.changed_head_lines
    base_sha, head_sha = "a" * 40, "b" * 40
    source = "src/sample.ts"
    assets = ("src/plugin.json", "src/readme.md")
    broken_source, broken_asset = "src/broken.ts", "src/broken.json"

    def read_blob(_repository: Path, sha: str, path: str, _records: object) -> object:
        if path == source:
            value = 1 if sha == base_sha else 2
            content = (
                "export function calculate(value: number): number {\n"
                f"  return value + {value};\n"
                "}\n"
            ).encode()
        elif path == broken_source:
            content = b"export function broken("
        else:
            content = b"{}\n"
        return SimpleNamespace(content=content)

    refactor_targets.git_changes.read_regular_blob = read_blob
    refactor_targets.git_changes.changed_base_lines = lambda *args, **kwargs: [2]
    refactor_targets.git_changes.changed_head_lines = lambda *args, **kwargs: [2]
    try:
        targets, unbounded = refactor_targets.derive(
            Path("."),
            SimpleNamespace(base_sha=base_sha, head_sha=head_sha),
            SimpleNamespace(
                language="typescript",
                is_production_path=lambda path: path.startswith("src/"),
            ),
            (
                SimpleNamespace(status="M", old_path=source, new_path=source),
                *(SimpleNamespace(status="A", old_path=None, new_path=path) for path in assets),
            ),
            [],
        )
        rename_targets, rename_unbounded = refactor_targets.derive(
            Path("."),
            SimpleNamespace(base_sha=base_sha, head_sha=head_sha),
            SimpleNamespace(
                language="typescript",
                is_production_path=lambda path: path.startswith("src/"),
            ),
            (SimpleNamespace(status="R100", old_path=broken_source, new_path=broken_asset),),
            [],
        )
    finally:
        refactor_targets.git_changes.read_regular_blob = original_blob
        refactor_targets.git_changes.changed_base_lines = original_base_lines
        refactor_targets.git_changes.changed_head_lines = original_head_lines

    expected = ("src/sample.ts::function:calculate:1-3",)
    if targets != expected or unbounded not in (assets, ()):
        raise RuntimeError("mixed assets escaped their fixed quality-gate boundary")
    if rename_targets or rename_unbounded not in (
        (broken_source,),
        (broken_asset, broken_source),
    ):
        raise RuntimeError("source-to-asset rename lost its source failure boundary")
    payload = {
        "behavior": {
            "asset_owner": "quality-gate",
            "refactor_targets": list(expected),
            "unbounded_source_paths": [broken_source],
        },
        "scenario": "refactor-assets",
        "schema_version": "1.0",
    }
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True))


if __name__ == "__main__":
    main()
