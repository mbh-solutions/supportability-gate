"""Content approvals must survive complete result composition and strict binding."""

from __future__ import annotations

import copy
import json
import runpy
from pathlib import Path

import pytest

from supportability_gate import standard_results

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/characterization/asset-results.inputs.json"


def test_content_correction_full_composer_keeps_approval_and_rejects_forgeries(monkeypatch):
    monkeypatch.setenv("SUPPORTABILITY_CHARACTERIZATION_DEFINITION", str(ROOT))
    probe = runpy.run_path(str(FIXTURE.with_name("asset-results.characterization.py")))
    rows = probe["cases"]()
    assert [row["input"] for row in rows] == [
        "approved-content",
        "missing-target",
        "stale-approval",
        "wrong-scope",
        "source-control",
    ]
    assert [entry["result"] for entry in rows[0]["output"]] == ["PASS"] * 8
    assert [entry["result"] for entry in rows[-1]["output"]] == ["PASS"] * 8
    for row in rows[1:-1]:
        assert all(row["output"][index]["result"] != "PASS" for index in (4, 5, 7))
    fixture = json.loads(FIXTURE.read_text())
    payload = probe["compose"](fixture)
    assert payload["result_classification"] == "PASS_AUTHORIZED_BEHAVIOR_CORRECTION"
    standard_results.validate_payload(payload)


@pytest.mark.parametrize("operation", ["add", "modify", "delete", "rename", "mixed"])
@pytest.mark.parametrize("language", ["python", "typescript", "mixed"])
def test_every_changed_asset_side_requires_its_own_bound_target(operation, language):
    old = None if operation == "add" else "src/catalog.json"
    new = (
        None
        if operation == "delete"
        else ("src/renamed.json" if operation == "rename" else "src/catalog.json")
    )
    changed = [
        dict(
            old_path=old,
            new_path=new,
            base_production=bool(old),
            head_production=bool(new),
            complexity_assessed=False,
        )
    ]
    assets = sorted({path for path in (old, new) if path})
    targets = [f"{path}::asset:{path}:whole-file" for path in assets]
    if operation == "mixed":
        source = "src/model.py" if language == "python" else "src/model.js"
        changed.append(
            dict(
                old_path=source,
                new_path=source,
                base_production=True,
                head_production=True,
                complexity_assessed=True,
            )
        )
        targets.append(f"{source}::function:calculate:1-2")
    scope = sorted({path for row in changed for path in (row["old_path"], row["new_path"]) if path})
    row = dict(applicable=True, changed_paths=scope, targets=sorted(targets), unbounded_paths=[])
    standard_results._s02_refactor_shape(row, tuple(changed), tuple(sorted(targets)), (), language)
    for asset in assets:
        missing = copy.deepcopy(row)
        missing["targets"].remove(f"{asset}::asset:{asset}:whole-file")
        with pytest.raises(standard_results.StandardResultsError, match="BINDING_MISMATCH"):
            standard_results._s02_refactor_shape(missing, tuple(changed), None, None, language)


def test_nonproduction_content_does_not_create_approval_requirement():
    changed = (
        dict(
            old_path="docs/example.json",
            new_path="docs/example.json",
            base_production=False,
            head_production=False,
            complexity_assessed=False,
        ),
    )
    assert standard_results._s02_refactor_change_paths(changed, "python") == (
        ["docs/example.json"],
        [],
        [],
    )
