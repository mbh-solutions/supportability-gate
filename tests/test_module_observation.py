"""Synthetic source-only canaries for the undeployed module-witness proposal."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import characterization_observer as ordinary
import module_characterization_observer as collector
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


TYPED_SOURCE = b"""from enum import Enum, StrEnum
from dataclasses import dataclass

class Exercise(StrEnum):
    SALE = "sale"
    CREATE = "create"

class Wrong(StrEnum):
    SALE = "sale"

class Primitive(Enum):
    FLAG = True
    COUNT = 2
    LABEL = "label"

@dataclass(frozen=True)
class Record:
    exercise: Exercise
    count: int

def plain(value):
    for key in value:
        if type(key) is not str:
            raise ValueError("key")
    return value

def calculate(record):
    if type(record.exercise) is not Exercise:
        raise TypeError("enum")
    return record
"""
TYPED_DRIVER = """target.calculate(target.Record(target.Exercise.SALE, 1))
target.calculate(target.Record(target.Exercise.CREATE, 2))
try:
    target.calculate(target.Record(target.Wrong.SALE, 1))
except TypeError:
    pass
target.plain({"x": 1})
target.plain({})
try:
    target.plain({1: "x"})
except ValueError:
    pass
"""


def typed_oracles(
    path: str = "observed_fixture.py", module: str = "observed_fixture"
) -> tuple[list, list]:
    """Authored literal expected objects; do not derive answers from collector output."""

    def record(exercise: str, count: int, enum_class: str = "Exercise") -> dict:
        return {
            "type": "dataclass",
            "module": module,
            "name": "Record",
            "fields": [
                [
                    "exercise",
                    {
                        "type": "enum",
                        "source_path": path,
                        "module": module,
                        "name": enum_class,
                        "member": exercise,
                        "value": {"type": "str", "value": exercise.lower()},
                    },
                ],
                ["count", {"type": "int", "value": count}],
            ],
        }

    def arguments(name: str, value: dict) -> list:
        return [{"name": name, "value": value}]

    primary = [
        {"input": arguments("record", record(member, count)), "output": record(member, count)}
        for member, count in (("SALE", 1), ("CREATE", 2))
    ]
    roots = [
        {
            "root": "calculate",
            "input": case["input"],
            "arguments_after": copy.deepcopy(case["input"]),
            "outcome": "RETURN",
            "output": case["output"],
            "exception": None,
        }
        for case in primary
    ]
    wrong = arguments("record", record("SALE", 1, "Wrong"))
    roots.append(
        {
            "root": "calculate",
            "input": wrong,
            "arguments_after": copy.deepcopy(wrong),
            "outcome": "EXCEPTION",
            "output": None,
            "exception": "builtins.TypeError",
        }
    )
    for value in (
        {"type": "dict", "items": [["x", {"type": "int", "value": 1}]]},
        {"type": "dict", "items": []},
    ):
        roots.append(
            {
                "root": "plain",
                "input": arguments("value", value),
                "arguments_after": arguments("value", copy.deepcopy(value)),
                "outcome": "RETURN",
                "output": value,
                "exception": None,
            }
        )
    invalid = {
        "type": "dict-keyed",
        "items": [[{"type": "int", "value": 1}, {"type": "str", "value": "x"}]],
    }
    roots.append(
        {
            "root": "plain",
            "input": arguments("value", invalid),
            "arguments_after": arguments("value", copy.deepcopy(invalid)),
            "outcome": "EXCEPTION",
            "output": None,
            "exception": "builtins.ValueError",
        }
    )
    return primary, roots


def _typed_module(tmp_path: Path):
    path = tmp_path / "observed_fixture.py"
    path.write_bytes(TYPED_SOURCE)
    specification = importlib.util.spec_from_file_location("observed_fixture", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules["observed_fixture"] = module
    specification.loader.exec_module(module)
    return module, proof.module_enum_declarations(TYPED_SOURCE)


def test_module_v2_real_typed_calls_and_integer_key_exception(tmp_path: Path) -> None:
    row = observe(
        tmp_path, TYPED_SOURCE, TYPED_DRIVER, [API, "observed_fixture.py::function:plain"]
    )
    assert "error" not in row, row
    assert row["schema_version"] == "module-witness.v3"
    assert row["primary"]["codec"] == "python-values-v2"
    assert row["root_cases"][-1]["input"][0]["value"] == {
        "type": "dict-keyed",
        "items": [[{"type": "int", "value": 1}, {"type": "str", "value": "x"}]],
    }
    assert row["root_cases"][-1]["exception"] == "builtins.ValueError"
    assert row["root_cases"][2]["exception"] == "builtins.TypeError"
    enum_value = row["primary"]["cases"][0]["output"]["fields"][0][1]
    assert enum_value == {
        "type": "enum",
        "source_path": "observed_fixture.py",
        "module": "observed_fixture",
        "name": "Exercise",
        "member": "SALE",
        "value": {"type": "str", "value": "sale"},
    }
    primary, expected_roots = typed_oracles()
    assert row["primary"]["cases"] == primary
    assert row["root_cases"] == expected_roots
    proof.verify_module_observation(
        TYPED_SOURCE, "observed_fixture.py", API, row, primary, expected_roots
    )


@pytest.mark.parametrize(
    "value",
    [None, True, False, -3, "text", b"bytes", 1.25, [1, "x"], (1, False), {"b": [2], "a": 1}],
)
def test_module_v2_retains_legacy_value_bytes(tmp_path: Path, value: object) -> None:
    encoded = collector._encode(value, tmp_path, "observed_fixture.py", [])
    assert ordinary.canonical_bytes(encoded) == ordinary.canonical_bytes(
        ordinary.encode_value(value, tmp_path)
    )
    proof.module_validate_value(encoded, "observed_fixture.py", [])


@pytest.mark.parametrize(
    "value",
    [
        b"\x00\xff",
        -0.0,
        Decimal("1.2300"),
        Decimal("1E-10000"),
        datetime(2026, 10, 3, 12, 30, tzinfo=UTC, fold=1),
    ],
)
def test_module_v2_text_primitive_values_use_actual_fixed_encoder(
    tmp_path: Path, value: object
) -> None:
    encoded = collector._encode(value, tmp_path, "observed_fixture.py", [])
    assert encoded == ordinary.encode_value(value, tmp_path)
    proof.module_validate_value(encoded, "observed_fixture.py", [])


@pytest.mark.parametrize(
    "encoded",
    [
        {"type": "bytes", "value": "FF"},
        {"type": "bytes", "value": "not-hex"},
        {"type": "float", "value": "1.0"},
        {"type": "float", "value": "nan"},
        {"type": "float", "value": "inf"},
        {"type": "float", "value": "-inf"},
        {"type": "decimal", "value": "NaN"},
        {"type": "decimal", "value": "Infinity"},
        {"type": "decimal", "value": "1" * 257},
        {"type": "decimal", "value": "1E10001"},
        {"type": "decimal", "value": "invalid"},
        {"type": "datetime", "value": "2026-10-03T12:30:00.000000", "fold": 0},
        {"type": "datetime", "value": "2026-10-03T12:30:00+00:00", "fold": 0},
        {"type": "datetime", "value": "2026-10-03T12:30:00.000000+00:00", "fold": True},
        {"type": "datetime", "value": "2026-10-03T12:30:00.000000+00:00", "fold": 2},
        {"type": "unknown", "value": "text"},
    ],
)
def test_module_v2_text_primitive_tags_reject_noncanonical_or_unsupported_values(
    encoded: object,
) -> None:
    with pytest.raises(proof.ModuleObservationError, match="INVALID_MODULE_VALUE"):
        proof.module_validate_value(encoded, "observed_fixture.py", [])


def test_module_v2_typed_keys_and_primitive_enum_values(tmp_path: Path) -> None:
    module, declarations = _typed_module(tmp_path)
    value = {2: "two", False: "flag", "text": "str"}
    encoded = collector._encode(value, tmp_path, "observed_fixture.py", declarations)
    proof.module_validate_value(encoded, "observed_fixture.py", declarations)
    for member in module.Primitive:
        encoded = collector._encode(member, tmp_path, "observed_fixture.py", declarations)
        proof.module_validate_value(encoded, "observed_fixture.py", declarations)
    with pytest.raises(ordinary.ObserverError, match="OBSERVER_UNSUPPORTED_VALUE"):
        ordinary.encode_value({1: "x"}, tmp_path)


@pytest.mark.parametrize("defect", ["alias", "auto", "method", "decorated", "mixed", "custom-meta"])
def test_static_enum_declarations_reject_unsupported_forms(defect: str) -> None:
    declarations = {
        "alias": "class E(StrEnum):\n A='x'\n B='x'\n",
        "auto": "class E(StrEnum):\n A=auto()\n",
        "method": "class E(StrEnum):\n A='x'\n def __str__(self):\n  return 'x'\n",
        "decorated": "@unique\nclass E(StrEnum):\n A='x'\n",
        "mixed": "class E(str, Enum):\n A='x'\n",
        "custom-meta": "class E(StrEnum, metaclass=Custom):\n A='x'\n",
    }
    with pytest.raises(proof.ModuleObservationError):
        proof.module_enum_declarations(
            ("from enum import Enum, StrEnum, auto, unique\n" + declarations[defect]).encode()
        )


def test_module_v2_unsupported_values_do_not_invoke_hooks(tmp_path: Path) -> None:
    module, declarations = _typed_module(tmp_path)
    calls = []

    class Hook:
        def __getattribute__(self, name):
            calls.append(name)
            raise AssertionError("hook")

        def __repr__(self):
            calls.append("repr")
            raise AssertionError("hook")

        def __str__(self):
            calls.append("str")
            raise AssertionError("hook")

    for value in (Hook(), {module.Exercise.SALE: "x"}, {1.0: "x"}, {None: "x"}):
        with pytest.raises(ordinary.ObserverError):
            collector._encode(value, tmp_path, "observed_fixture.py", declarations)
    module.Exercise.__str__ = Hook.__str__
    with pytest.raises(ordinary.ObserverError, match="OBSERVER_ENUM_INVALID"):
        collector._encode(module.Exercise.SALE, tmp_path, "observed_fixture.py", declarations)
    assert calls == []


@pytest.mark.parametrize(
    "mutation", ["member", "literal", "unregistered", "method", "foreign-file"]
)
def test_module_v2_runtime_enum_declaration_mutation_rejected(
    tmp_path: Path, mutation: str
) -> None:
    module, declarations = _typed_module(tmp_path)
    member = module.Exercise.SALE
    if mutation == "member":
        vars(module.Exercise)["_member_map_"]["EXTRA"] = member
    elif mutation == "literal":
        object.__getattribute__(member, "__dict__")["_value_"] = "forged"
    elif mutation == "unregistered":
        module.Exercise = None
    elif mutation == "method":
        module.Exercise.callback = lambda: None
    else:
        module.__file__ = str(tmp_path / "foreign.py")
    with pytest.raises(ordinary.ObserverError, match="OBSERVER_ENUM_INVALID"):
        collector._encode(member, tmp_path, "observed_fixture.py", declarations)


@pytest.mark.parametrize(
    "storage", ["names", "member-keys", "value-keys", "instance-keys", "module-keys"]
)
def test_module_v2_enum_metadata_hooks_are_rejected_without_execution(
    tmp_path: Path, storage: str
) -> None:
    module, declarations = _typed_module(tmp_path)
    member = module.Exercise.SALE
    calls = []

    class EqualHook:
        def __eq__(self, other):
            calls.append("eq")
            return True

        def __hash__(self):
            calls.append("hash")
            return 1

    hook = EqualHook()
    namespace = vars(module.Exercise)
    if storage == "names":
        module.Exercise._member_names_ = [hook]
    elif storage == "member-keys":
        namespace["_member_map_"][hook] = member
    elif storage == "value-keys":
        namespace["_value2member_map_"][hook] = member
    elif storage == "instance-keys":
        object.__getattribute__(member, "__dict__")[hook] = member
    else:
        vars(module)[hook] = member
    calls.clear()  # Fixture mutation is outside capture; encoding must execute no hook.
    with pytest.raises(ordinary.ObserverError, match="OBSERVER_ENUM_INVALID"):
        collector._encode(member, tmp_path, "observed_fixture.py", declarations)
    assert calls == []


@pytest.mark.parametrize(
    "defect",
    [
        "bool-int-collision",
        "duplicate",
        "order",
        "string-only",
        "unsupported",
        "wrong-type",
        "extra",
        "relabel",
    ],
)
def test_module_v2_key_tags_fail_closed(defect: str) -> None:
    def key(kind, value):
        return {"type": kind, "value": value}

    items = {
        "bool-int-collision": [
            [key("bool", True), key("none", None)],
            [key("int", 1), key("none", None)],
        ],
        "duplicate": [[key("int", 1), key("none", None)], [key("int", 1), key("none", None)]],
        "order": [[key("int", 2), key("none", None)], [key("int", 1), key("none", None)]],
        "string-only": [[key("str", "x"), key("none", None)]],
        "unsupported": [[key("float", "0x1.0000000000000p+0"), key("none", None)]],
        "wrong-type": [[key("int", True), key("none", None)]],
        "extra": [[{"type": "int", "value": 1, "extra": True}, key("none", None)]],
        "relabel": [[1, key("none", None)]],
    }
    with pytest.raises(proof.ModuleObservationError):
        proof.module_validate_value(
            {"type": "dict-keyed", "items": items[defect]}, "observed_fixture.py", []
        )


@pytest.mark.parametrize("field", ["source_path", "module", "name", "member", "value"])
def test_module_v2_enum_tags_reject_forged_nominal_or_literal_identity(field: str) -> None:
    primary, _ = typed_oracles()
    value = primary[0]["output"]["fields"][0][1]
    value[field] = {"type": "str", "value": "create"} if field == "value" else "forged"
    with pytest.raises(proof.ModuleObservationError):
        proof.module_validate_value(
            value, "observed_fixture.py", proof.module_enum_declarations(TYPED_SOURCE)
        )


def test_module_v2_cycles_subclasses_and_key_node_limits_fail_closed(tmp_path: Path) -> None:
    class Integer(int):
        pass

    class String(str):
        pass

    for key in (Integer(1), String("x")):
        with pytest.raises(ordinary.ObserverError, match="OBSERVER_UNSUPPORTED_VALUE"):
            collector._encode({key: "x"}, tmp_path, "observed_fixture.py", [])
    cycle = {}
    cycle[1] = cycle
    with pytest.raises(ordinary.ObserverError, match="OBSERVER_VALUE_CYCLE"):
        collector._encode(cycle, tmp_path, "observed_fixture.py", [])
    string_keys = [{str(key): None for key in range(256)} for _ in range(8)]
    ordinary.encode_value(string_keys, tmp_path)
    typed_keys = [{key: None for key in range(256)} for _ in range(8)]
    with pytest.raises(ordinary.ObserverError, match="OBSERVER_VALUE_LIMIT"):
        collector._encode(typed_keys, tmp_path, "observed_fixture.py", [])


def test_module_v2_plain_dataclass_legacy_bytes_remain_identical(tmp_path: Path) -> None:
    module, declarations = _typed_module(tmp_path)
    value = module.Record("sale", 1)
    assert collector._encode(
        value, tmp_path, "observed_fixture.py", declarations
    ) == ordinary.encode_value(value, tmp_path)


def test_fixed_hosted_parser_rejects_codec_or_schema_relabel(tmp_path: Path) -> None:
    import hosted_characterization as hosted

    row = observe(tmp_path)
    assert hosted._observed_behavior(proof._canonical(row), API, (API,))[1] is None
    assert (
        hosted._observed_behavior(proof._canonical(row["primary"]), API)[1]
        == "MALFORMED_API_OBSERVATION"
    )
    for container, key, value in (
        (row, "schema_version", "module-witness.v1"),
        (row["primary"], "codec", "python-values-v1"),
    ):
        original = container[key]
        container[key] = value
        assert (
            hosted._observed_behavior(proof._canonical(row), API, (API,))[1]
            == "MALFORMED_MODULE_OBSERVATION"
        )
        container[key] = original


@pytest.mark.parametrize(
    "defect",
    [
        "schema",
        "codec",
        "source",
        "declaration",
        "path",
        "class",
        "member",
        "literal",
        "exception",
        "arguments",
    ],
)
def test_module_v2_typed_capture_forgery_rejected(tmp_path: Path, defect: str) -> None:
    roots = [API, "observed_fixture.py::function:plain"]
    row = observe(tmp_path, TYPED_SOURCE, TYPED_DRIVER, roots)
    assert "error" not in row, row
    expected, root_expected = typed_oracles()
    value = row["primary"]["cases"][0]["output"]["fields"][0][1]
    if defect == "schema":
        row["schema_version"] = "module-witness.v1"
    elif defect == "codec":
        row["primary"]["codec"] = "python-values-v1"
    elif defect == "source":
        row["source_sha256"] = "0" * 64
    elif defect == "declaration":
        row["enum_declarations"][0]["members"][0]["value"] = "forged"
    elif defect in {"path", "class", "member", "literal"}:
        field = {"path": "source_path", "class": "name", "member": "member", "literal": "value"}[
            defect
        ]
        value[field] = {"type": "str", "value": "forged"} if defect == "literal" else "forged"
    elif defect == "exception":
        row["root_cases"][-1]["exception"] = "builtins.TypeError"
    else:
        row["root_cases"][-1]["arguments_after"] = []
    with pytest.raises(proof.ModuleObservationError):
        proof.verify_module_observation(
            TYPED_SOURCE, "observed_fixture.py", API, row, expected, root_expected
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


def test_executed_body_reports_unexecuted_error_lines_truthfully(tmp_path: Path) -> None:
    receipt = observe(tmp_path, driver="target.calculate(1)\ntarget.calculate(2)\n")
    verify(receipt)
    validate = next(row for row in receipt["body_coverage"] if row["function"] == "validate")
    assert validate["hit_lines"] == [2]
    assert validate["missing_lines"] == [3]
    assert receipt["body_metric"] == "named-body-execution.v1"


def test_direct_helper_call_outside_public_execution_cannot_supply_coverage(tmp_path: Path) -> None:
    source = SOURCE + b"\ndef unused(value):\n    return value + 10\n"
    receipt = observe(
        tmp_path,
        source,
        driver="target.calculate(1)\ntarget.calculate(2)\ntarget.unused(1)\n",
    )
    with pytest.raises(proof.ModuleObservationError, match="UNWITNESSED_FUNCTION_BODY:unused"):
        verify(receipt, source)


def test_unwitnessed_new_helper_refuses_module_scope(tmp_path: Path) -> None:
    source = SOURCE + b"\ndef unused(value):\n    return value + 10\n"
    with pytest.raises(proof.ModuleObservationError, match="UNWITNESSED_FUNCTION_BODY:unused"):
        verify(observe(tmp_path, source), source)


@pytest.mark.parametrize(
    "change",
    [
        "metric",
        "old_schema",
        "missing_report",
        "concealed_missing",
        "false_hits",
        "omitted_body",
        "extra_body",
    ],
)
def test_body_metric_cannot_relabel_or_conceal_actual_trace(tmp_path: Path, change: str) -> None:
    row = observe(tmp_path, driver="target.calculate(1)\ntarget.calculate(2)\n")
    if change == "metric":
        row["body_metric"] = "complete-line-coverage"
    elif change == "old_schema":
        row["schema_version"] = "module-witness.v2"
    elif change == "missing_report":
        del row["body_coverage"]
    elif change == "concealed_missing":
        next(item for item in row["body_coverage"] if item["function"] == "validate")[
            "missing_lines"
        ] = []
    elif change == "false_hits":
        body = next(item for item in row["body_coverage"] if item["function"] == "validate")
        body["hit_lines"] = body["executable_lines"]
    elif change == "omitted_body":
        row["body_coverage"].pop()
    else:
        row["body_coverage"].append(copy.deepcopy(row["body_coverage"][0]))
    with pytest.raises(proof.ModuleObservationError):
        verify(row)


def test_declared_root_must_execute_as_root_not_merely_nested_helper(tmp_path: Path) -> None:
    row = observe(tmp_path, roots=[API, "observed_fixture.py::function:double"])
    with pytest.raises(proof.ModuleObservationError, match="UNLINKED_MODULE_EXECUTION"):
        verify(row)


def test_unicode_wire_bytes_and_typed_key_order_match_fixed_collector(tmp_path: Path) -> None:
    encoded = collector._encode(
        {1: None, "é": None, "z": None}, tmp_path, "observed_fixture.py", []
    )
    proof.module_validate_value(encoded, "observed_fixture.py", [])
    emoji = collector._encode("😀" * 24000, tmp_path, "observed_fixture.py", [])
    proof.module_validate_value(emoji, "observed_fixture.py", [])
    assert len(ordinary.canonical_bytes(emoji)) < ordinary.MAX_BYTES


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
