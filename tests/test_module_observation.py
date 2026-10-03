"""Synthetic source-only canaries for the undeployed module-witness proposal."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from supportability_gate import characterization as proof

SOURCE = b"""def validate(value):
    if value < 0:
        raise ValueError('negative')

def double(value):
    return value * 2

def calculate(value):
    validate(value)
    return double(value)
"""
API = "observed_fixture.py::function:calculate"
CASES = [
    {
        "input": [{"name": "value", "value": {"type": "int", "value": value}}],
        "output": {"type": "int", "value": 2 * value},
    }
    for value in (1, 2)
]
ROOT_CASES = [
    {
        "root": "calculate",
        "input": case["input"],
        "arguments_after": case["input"],
        "outcome": "RETURN",
        "output": case["output"],
        "exception": None,
    }
    for case in CASES
] + [
    {
        "root": "calculate",
        "input": [{"name": "value", "value": {"type": "int", "value": -1}}],
        "arguments_after": [{"name": "value", "value": {"type": "int", "value": -1}}],
        "outcome": "EXCEPTION",
        "output": None,
        "exception": "builtins.ValueError",
    }
]
RUN = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
sys.path.insert(0, sys.argv[3])
import module_characterization_observer as collector
try:
    roots = json.loads(sys.argv[6]) if len(sys.argv) > 6 else None
    result = collector.observe_module(Path(sys.argv[3]), sys.argv[5], Path(sys.argv[4]), roots)
except Exception as error:
    result = {'error': getattr(error, 'code', str(error))}
print(json.dumps(result, sort_keys=True))
"""


def observe(
    tmp_path: Path,
    source: bytes = SOURCE,
    driver: str | None = None,
    roots: list[str] | None = None,
) -> dict:
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    (target / "observed_fixture.py").write_bytes(source)
    script = tmp_path / "driver.py"
    script.write_text(
        "import observed_fixture as target\n"
        + (
            driver
            or "target.calculate(1)\ntarget.calculate(2)\ntry:\n    target.calculate(-1)\nexcept ValueError:\n    pass\n"
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            RUN,
            str(Path(__file__).parent),
            str(Path(__file__).parents[1] / "src"),
            str(target),
            str(script),
            API,
            *([json.dumps(roots)] if roots is not None else []),
        ],
        check=True,
        capture_output=True,
        timeout=15,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    return json.loads(completed.stdout)


def verify(value: dict, source: bytes = SOURCE, expected: object = CASES) -> None:
    proof.verify_module_observation(
        source,
        "observed_fixture.py",
        API,
        value,
        expected,
        ROOT_CASES[: len(value.get("executions", []))],
    )


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (
            b"def calculate(value):\n    values = (\n        value * 2\n        for _ in range(1)\n    )\n    return sum(values)\n",
            "UNSUPPORTED_MODULE_GENERATOR_EXPRESSION",
        ),
        (
            b"type Value = int\n\ndef calculate(value):\n    return value * 2\n",
            "UNSUPPORTED_MODULE_COMPILED_FUNCTION",
        ),
    ],
)
def test_real_collector_rejects_unaccounted_executable_bodies(
    tmp_path: Path, source: bytes, error: str
) -> None:
    receipt = observe(tmp_path, source, "target.calculate(1)\ntarget.calculate(2)\n")
    assert receipt == {"error": error}
    with pytest.raises(proof.ModuleObservationError, match=error):
        proof.module_source_inventory(source, "observed_fixture.py")


def test_actual_void_and_exception_bodies_link_to_public_executions(tmp_path: Path) -> None:
    receipt = observe(tmp_path)
    verify(receipt)
    assert receipt["primary"]["cases"] == CASES
    assert receipt["executions"][-1] == {
        "id": 2,
        "root": "calculate",
        "outcome": "EXCEPTION",
        "case": 2,
        "primary_case": None,
    }
    helpers = [row for row in receipt["calls"] if row["function"] == "validate"]
    assert {row["outcome"] for row in helpers} == {"RETURN", "EXCEPTION"}
    assert "output" not in helpers[0]
    assert observe(tmp_path) == receipt


def test_unexecuted_error_body_is_not_covered_by_normal_cases(tmp_path: Path) -> None:
    receipt = observe(tmp_path, driver="target.calculate(1)\ntarget.calculate(2)\n")
    with pytest.raises(proof.ModuleObservationError, match="UNWITNESSED_FUNCTION_BODY:validate"):
        verify(receipt)


def test_direct_helper_call_outside_public_execution_cannot_supply_coverage(tmp_path: Path) -> None:
    receipt = observe(
        tmp_path,
        driver="target.calculate(1)\ntarget.calculate(2)\ntry:\n    target.validate(-1)\nexcept ValueError:\n    pass\n",
    )
    with pytest.raises(proof.ModuleObservationError, match="UNWITNESSED_FUNCTION_BODY:validate"):
        verify(receipt)


def test_unwitnessed_new_helper_refuses_module_scope(tmp_path: Path) -> None:
    source = SOURCE + b"\ndef unused(value):\n    return value + 10\n"
    with pytest.raises(proof.ModuleObservationError, match="UNWITNESSED_FUNCTION_BODY:unused"):
        verify(observe(tmp_path, source), source)


@pytest.mark.parametrize(
    "change",
    [
        "empty_cases",
        "changed_behavior",
        "stale_inventory",
        "omitted_helper",
        "unlinked_call",
        "missing_primary",
        "false_void_output",
        "stale_schema",
        "duplicate_execution",
    ],
)
def test_malformed_or_dishonest_witnesses_fail_closed(tmp_path: Path, change: str) -> None:
    row = copy.deepcopy(observe(tmp_path))
    if change == "empty_cases":
        row["primary"]["cases"] = []
    elif change == "changed_behavior":
        row["primary"]["cases"][0]["output"]["value"] = 3
    elif change == "stale_inventory":
        row["inventory_sha256"] = "0" * 64
    elif change == "omitted_helper":
        row["functions"] = [item for item in row["functions"] if item["name"] != "validate"]
    elif change == "unlinked_call":
        row["calls"][0]["execution"] = 99
    elif change == "missing_primary":
        row["calls"] = [item for item in row["calls"] if item["function"] != "calculate"]
    elif change == "false_void_output":
        row["calls"][0]["output"] = True
    elif change == "stale_schema":
        row["schema_version"] = "module-witness.v0"
    else:
        row["executions"].append(row["executions"][0])
    with pytest.raises(proof.ModuleObservationError):
        verify(row)


def test_source_change_invalidates_inventory_even_with_unchanged_public_answers(
    tmp_path: Path,
) -> None:
    receipt = observe(tmp_path)
    with pytest.raises(proof.ModuleObservationError, match="STALE_MODULE_(INVENTORY|PRIMARY)"):
        verify(receipt, SOURCE.replace(b"'negative'", b"'changed error'"))


def test_driver_prints_do_not_replace_observed_oracle(tmp_path: Path) -> None:
    receipt = observe(tmp_path, driver="print('fake pass')\n")
    assert receipt == {"error": "OBSERVER_NO_NORMAL_RETURNS"}


def test_exception_only_primary_cannot_claim_meaningful_output(tmp_path: Path) -> None:
    receipt = observe(
        tmp_path,
        driver="for value in (-1, -2):\n    try:\n        target.calculate(value)\n    except ValueError:\n        pass\n",
    )
    assert receipt == {"error": "OBSERVER_NO_NORMAL_RETURNS"}


def test_instance_receiver_codec_gap_is_explicit(tmp_path: Path) -> None:
    source = b"class Store:\n    def calculate(self, value):\n        return value * 2\n"
    target = tmp_path / "target"
    target.mkdir()
    (target / "observed_fixture.py").write_bytes(source)
    script = tmp_path / "driver.py"
    script.write_text(
        "import observed_fixture as target\ns = target.Store()\ns.calculate(1)\ns.calculate(2)\n"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            RUN,
            str(Path(__file__).parent),
            str(Path(__file__).parents[1] / "src"),
            str(target),
            str(script),
            "observed_fixture.py::function:Store.calculate",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    assert json.loads(completed.stdout) == {"error": "OBSERVER_UNSUPPORTED_VALUE"}


def test_public_void_root_has_real_argument_mutation_oracle(tmp_path: Path) -> None:
    source = SOURCE + b"\ndef record(values, value):\n    values.append(value)\n"
    driver = "target.calculate(1)\ntarget.calculate(2)\ntry:\n    target.calculate(-1)\nexcept ValueError:\n    pass\ntarget.record([], 9)\n"
    roots = [API, "observed_fixture.py::function:record"]
    receipt = observe(tmp_path, source, driver, roots)
    before = [
        {"name": "values", "value": {"type": "list", "items": []}},
        {"name": "value", "value": {"type": "int", "value": 9}},
    ]
    after = copy.deepcopy(before)
    after[0]["value"]["items"] = [{"type": "int", "value": 9}]
    expected = [
        *ROOT_CASES,
        {
            "root": "record",
            "input": before,
            "arguments_after": after,
            "outcome": "RETURN",
            "output": {"type": "none", "value": None},
            "exception": None,
        },
    ]
    proof.verify_module_observation(source, "observed_fixture.py", API, receipt, CASES, expected)
    assert receipt["root_cases"] == expected
    changed = copy.deepcopy(receipt)
    changed["root_cases"][-1]["arguments_after"][0]["value"]["items"] = []
    with pytest.raises(proof.ModuleObservationError, match="INVALID_MODULE_ROOT_ORACLE"):
        proof.verify_module_observation(
            source, "observed_fixture.py", API, changed, CASES, expected
        )


def test_plain_public_boundary_witnesses_real_class_helpers_without_serializing_self(
    tmp_path: Path,
) -> None:
    source = b"class Store:\n    def __init__(self, value):\n        self.value = value\n    def read(self):\n        return self.value * 2\n\ndef calculate(value):\n    store = Store(value)\n    return store.read()\n"
    receipt = observe(tmp_path, source, "target.calculate(1)\ntarget.calculate(2)\n")
    proof.verify_module_observation(
        source, "observed_fixture.py", API, receipt, CASES, ROOT_CASES[:2]
    )
    assert {call["function"] for call in receipt["calls"]} == {
        "Store.__init__",
        "Store.read",
        "calculate",
    }
