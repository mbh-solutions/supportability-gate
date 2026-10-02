"""Synthetic observer canaries; consumer source is never executed locally."""

from __future__ import annotations

import importlib.util
import json
import marshal
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

_OBSERVER = Path(__file__).with_name("characterization_observer.py")
_SPEC = importlib.util.spec_from_file_location("trusted_characterization_observer", _OBSERVER)
assert _SPEC and _SPEC.loader
observer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(observer)

_SYNTHETIC_RUN = """
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('trusted_observer', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.insert(0, sys.argv[2])
try:
    result = module.observe(Path(sys.argv[2]), sys.argv[4], Path(sys.argv[3]))
except module.ObserverError as error:
    result = {'error': error.code}
print(json.dumps(result, sort_keys=True))
"""


def _observe(tmp_path: Path, source: str, driver: str, name: str = "calculate") -> dict:
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    (target / "observed_fixture.py").write_text(source, encoding="utf-8")
    script = tmp_path / "driver.py"
    script.write_text("import observed_fixture as target\n" + driver, encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            _SYNTHETIC_RUN,
            str(_OBSERVER),
            str(target),
            str(script),
            f"observed_fixture.py::function:{name}",
        ],
        check=True,
        capture_output=True,
        timeout=15,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    return json.loads(completed.stdout)


def test_actual_calls_snapshot_parameters_before_mutation(tmp_path: Path) -> None:
    source = "def calculate(values, /, extra=1, *args, flag=False, **kwargs):\n    values.append(extra)\n    return values, args, flag, kwargs\n"
    driver = "target.calculate([1], 2, 3, flag=True, label='a')\ntarget.calculate([2], 4, 5, flag=False, label='b')\nprint('arbitrary fixture output')\n"
    result = _observe(tmp_path, source, driver)
    replay = _observe(tmp_path, source, driver)
    assert result == replay
    assert result["codec"] == observer.CODEC
    assert len(result["cases"]) == 2
    assert [item["name"] for item in result["cases"][0]["input"]] == [
        "values",
        "extra",
        "flag",
        "args",
        "kwargs",
    ]
    assert result["cases"][0]["input"][0]["value"] == {
        "type": "list",
        "items": [{"type": "int", "value": 1}],
    }
    assert result["cases"][0]["output"]["items"][0]["items"][-1] == {"type": "int", "value": 2}
    assert result["start_line"] == 1
    assert result["end_line"] == 3


@pytest.mark.parametrize("driver", ["", 'print(\'{"cases":[{"output":true}]}\')'])
def test_import_and_printed_fixture_cannot_supply_cases(tmp_path: Path, driver: str) -> None:
    assert _observe(tmp_path, "def calculate(value):\n    return value\n", driver) == {
        "error": "OBSERVER_NO_NORMAL_RETURNS"
    }


def test_exception_unwind_is_not_normal_none_return(tmp_path: Path) -> None:
    source = "def calculate(value):\n    try:\n        raise ValueError('failed')\n    finally:\n        value += 1\n"
    driver = "try:\n    target.calculate(1)\nexcept ValueError:\n    pass\n"
    assert _observe(tmp_path, source, driver) == {"error": "OBSERVER_NO_NORMAL_RETURNS"}


def test_caught_exception_followed_by_none_is_normal_return(tmp_path: Path) -> None:
    source = "def calculate(value):\n    try:\n        raise ValueError('handled')\n    except ValueError:\n        return None\n"
    result = _observe(tmp_path, source, "target.calculate(1)\n")
    assert result["cases"][0]["output"] == {"type": "none", "value": None}


def test_driver_failure_invalidates_captured_returns(tmp_path: Path) -> None:
    result = _observe(
        tmp_path,
        "def calculate(value):\n    return value\n",
        "target.calculate(1)\nraise RuntimeError('driver failed')\n",
    )
    assert result == {"error": "OBSERVER_DRIVER_FAILED"}


def test_spoofed_filename_and_qualname_fail_code_binding(tmp_path: Path) -> None:
    driver = "namespace = {}\nexec(compile('def calculate(value): return 999', target.__file__, 'exec'), namespace)\nnamespace['calculate'](1)\n"
    assert _observe(tmp_path, "def calculate(value):\n    return value\n", driver) == {
        "error": "OBSERVER_CODE_MISMATCH"
    }


def test_wrong_origin_and_descriptor_stdout_cannot_supply_cases(tmp_path: Path) -> None:
    driver = "import os\nnamespace = {}\nexec(compile('def calculate(value): return 999', '/wrong-origin.py', 'exec'), namespace)\nnamespace['calculate'](1)\nos.write(1, b'{\"cases\":[{\"output\":true}]}')\n"
    assert _observe(tmp_path, "def calculate(value):\n    return value\n", driver) == {
        "error": "OBSERVER_NO_NORMAL_RETURNS"
    }


def test_compiled_and_cached_code_fingerprints_are_identical() -> None:
    compiled = compile(
        "def calculate(value): return value + 1",
        "/target/fixture.py",
        "exec",
        dont_inherit=True,
        optimize=0,
    ).co_consts[0]
    assert observer.code_sha256(compiled) == observer.code_sha256(
        marshal.loads(marshal.dumps(compiled))
    )


def test_final_hook_replacement_fails_closed(tmp_path: Path) -> None:
    driver = "import sys\ntarget.calculate(1)\nsys.settrace = lambda value: None\n"
    assert _observe(tmp_path, "def calculate(value):\n    return value\n", driver) == {
        "error": "OBSERVER_HOOK_TAMPERED"
    }


def test_frame_trace_disabling_fails_closed(tmp_path: Path) -> None:
    source = (
        "import sys\ndef calculate(value):\n    sys._getframe().f_trace = None\n    return value\n"
    )
    assert _observe(tmp_path, source, "target.calculate(1)\n") == {
        "error": "OBSERVER_HOOK_TAMPERED"
    }


@pytest.mark.parametrize("hook", ["settrace", "setprofile"])
def test_hook_disable_and_caught_tamper_fail_closed(tmp_path: Path, hook: str) -> None:
    driver = f"import sys\ntry:\n    sys.{hook}(None)\nexcept ValueError:\n    pass\ntarget.calculate(1)\n"
    assert _observe(tmp_path, "def calculate(value):\n    return value\n", driver) == {
        "error": "OBSERVER_HOOK_TAMPERED"
    }


def test_source_bound_slots_and_stored_dataclass_values(tmp_path: Path) -> None:
    source = """from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
@dataclass(frozen=True, slots=True)
class Terms:
    amount: Decimal
    when: datetime
@dataclass(frozen=True)
class Decision:
    permitted: bool
    value: Decimal
def calculate(terms):
    return Decision(terms.amount > 0, terms.amount * 2)
"""
    driver = "target.calculate(target.Terms(target.Decimal('1.20'), target.datetime(2026, 1, 1, tzinfo=target.timezone.utc)))\n"
    result = _observe(tmp_path, source, driver)
    case = result["cases"][0]
    assert case["input"][0]["value"]["fields"][0] == [
        "amount",
        {"type": "decimal", "value": "1.20"},
    ]
    assert case["input"][0]["value"]["fields"][1][1] == {
        "type": "datetime",
        "value": "2026-01-01T00:00:00.000000+00:00",
        "fold": 0,
    }
    assert case["output"] == {
        "type": "dataclass",
        "module": "observed_fixture",
        "name": "Decision",
        "fields": [
            ["permitted", {"type": "bool", "value": True}],
            ["value", {"type": "decimal", "value": "2.40"}],
        ],
    }


def test_dataclass_property_field_is_rejected_without_running_it(tmp_path: Path) -> None:
    source = """from dataclasses import dataclass
@dataclass
class Terms:
    amount: int
def calculate(terms):
    return terms.amount
"""
    driver = "terms = target.Terms(1)\ntarget.Terms.amount = property(lambda self: (_ for _ in ()).throw(RuntimeError('descriptor executed')))\ntarget.calculate(terms)\n"
    assert _observe(tmp_path, source, driver) == {"error": "OBSERVER_DATACLASS_INVALID"}


def test_dataclass_dictionary_property_is_rejected_without_running_it(tmp_path: Path) -> None:
    source = """from dataclasses import dataclass
@dataclass
class Terms:
    amount: int
    @property
    def __dict__(self):
        raise RuntimeError('descriptor executed')
def calculate(terms):
    return terms.amount
"""
    assert _observe(tmp_path, source, "target.calculate(target.Terms(1))\n") == {
        "error": "OBSERVER_DATACLASS_INVALID"
    }


def test_dataclass_metadata_must_match_declared_source_fields(tmp_path: Path) -> None:
    source = """from dataclasses import dataclass
@dataclass
class Terms:
    amount: int
def calculate(terms):
    return terms.amount
"""
    driver = "target.Terms.__dataclass_fields__['amount'].name = 'forged'\ntarget.calculate(target.Terms(1))\n"
    assert _observe(tmp_path, source, driver) == {"error": "OBSERVER_DATACLASS_INVALID"}


def test_dataclass_outside_target_cannot_claim_declared_shape(tmp_path: Path) -> None:
    driver = "from dataclasses import dataclass\n@dataclass\nclass Terms:\n    amount: int\ntarget.calculate(Terms(1))\n"
    assert _observe(tmp_path, "def calculate(terms):\n    return terms.amount\n", driver) == {
        "error": "OBSERVER_DATACLASS_INVALID"
    }


def test_cli_rejects_driver_outside_fixed_mount(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = tmp_path / "untrusted.py"
    driver.write_text("raise AssertionError('must not execute')", encoding="utf-8")
    assert observer.main(["--api", "fixture.py::function:calculate", "--driver", str(driver)]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": "1.0",
        "error": "OBSERVER_DRIVER_INVALID",
    }


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        Decimal("NaN"),
        Decimal("Infinity"),
        datetime(2026, 1, 1),
        object(),
        {1: "wrong key"},
    ],
)
def test_unknown_or_nonfinite_values_are_rejected(tmp_path: Path, value: object) -> None:
    with pytest.raises(observer.ObserverError):
        observer.encode_value(value, tmp_path)


def test_exact_types_and_no_serializer_hooks(tmp_path: Path) -> None:
    class Trick(str):
        def __str__(self) -> str:
            raise AssertionError("must not call")

        def __repr__(self) -> str:
            raise AssertionError("must not call")

    with pytest.raises(observer.ObserverError, match="OBSERVER_UNSUPPORTED_VALUE"):
        observer.encode_value(Trick("value"), tmp_path)
    assert observer.encode_value(b"\x00\xff", tmp_path) == {"type": "bytes", "value": "00ff"}
    assert observer.encode_value(-0.0, tmp_path) != observer.encode_value(0.0, tmp_path)
    assert observer.encode_value([1], tmp_path) != observer.encode_value((1,), tmp_path)
    assert observer.encode_value(datetime(2026, 1, 1, tzinfo=UTC), tmp_path)["type"] == "datetime"


def test_cycles_depth_size_and_node_bounds(tmp_path: Path) -> None:
    cyclic: list = []
    cyclic.append(cyclic)
    with pytest.raises(observer.ObserverError, match="OBSERVER_VALUE_CYCLE"):
        observer.encode_value(cyclic, tmp_path)
    deep: object = 1
    for _ in range(observer.MAX_DEPTH + 1):
        deep = [deep]
    for value in [
        deep,
        "x" * (observer.MAX_BYTES + 1),
        list(range(observer.MAX_ITEMS + 1)),
        1 << 256,
        [[0] * 256] * 32,
    ]:
        with pytest.raises(observer.ObserverError, match="OBSERVER_VALUE_LIMIT"):
            observer.encode_value(value, tmp_path)


@pytest.mark.parametrize(
    "source",
    ["def calculate(value):\n    yield value\n", "async def calculate(value):\n    return value\n"],
)
def test_generator_and_async_target_are_unsupported(tmp_path: Path, source: str) -> None:
    assert _observe(tmp_path, source, "target.calculate(1)\n") == {
        "error": "OBSERVER_FUNCTION_INVALID"
    }


def test_case_bound_is_enforced(tmp_path: Path) -> None:
    assert _observe(
        tmp_path,
        "def calculate(value):\n    return value\n",
        f"for value in range({observer.MAX_CASES + 1}):\n    target.calculate(value)\n",
    ) == {"error": "OBSERVER_CASE_LIMIT"}
