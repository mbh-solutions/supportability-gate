"""Trusted source-integrity cases executed against candidate code in the S02 sandbox."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any


def _write(path: Path, status: str, code: str, cases: list[dict[str, str]]) -> int:
    path.write_text(
        json.dumps(
            {
                "schema_version": "source-integrity-conformance.v1",
                "status": status,
                "code": code,
                "cases": cases,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return {"PASS": 0, "BLOCK": 1, "TECHNICAL_FAILURE": 2}[status]


def _mutated_inventory(content: bytes, field: str) -> bytes:
    payload = json.loads(content)
    clause = payload["clauses"][0]
    if field == "mapping":
        clause["enforcement_owner"] = "Supportability 8 - Review Handoff"
    else:
        applicability = clause["applicability"]
        applicability["condition"] = applicability["condition"] + " changed"
    return (json.dumps(payload, sort_keys=True) + "\n").encode()


def _case(
    module: Any, standard: bytes, inventory: bytes, name: str, expected: str | None
) -> dict[str, str]:
    try:
        clauses = module.validate_inventory(standard, inventory)
    except module.ClauseInventoryError as error:
        actual = error.code
    except Exception as error:  # candidate boundary
        return {"name": name, "status": "TECHNICAL_FAILURE", "code": type(error).__name__}
    else:
        actual = "PASS" if len(clauses) == 218 else f"CLAUSE_COUNT:{len(clauses)}"
    wanted = expected or "PASS"
    return {"name": name, "status": "PASS" if actual == wanted else "BLOCK", "code": actual}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.repository / "src"))
    try:
        module = importlib.import_module("supportability_gate.clause_inventory")
        standard = (args.repository / "docs/supportability_standard.md").read_bytes()
        inventory = (args.repository / "docs/normative_clause_inventory.json").read_bytes()
        cases = [
            _case(module, standard, inventory, "clean", None),
            _case(
                module,
                standard + b"\nchanged\n",
                inventory,
                "standard-tamper",
                "STANDARD_HASH_MISMATCH",
            ),
            _case(
                module,
                standard,
                _mutated_inventory(inventory, "mapping"),
                "mapping-tamper",
                "UNAUTHORIZED_MAPPING_CHANGE",
            ),
            _case(
                module,
                standard,
                _mutated_inventory(inventory, "applicability"),
                "applicability-tamper",
                "UNAUTHORIZED_APPLICABILITY",
            ),
            _case(module, standard, b"{", "malformed-inventory", "MALFORMED_INVENTORY"),
        ]
    except Exception as error:  # candidate import/input boundary
        return _write(
            args.output, "TECHNICAL_FAILURE", f"CONFORMANCE_RUNTIME:{type(error).__name__}", []
        )
    technical = next((item for item in cases if item["status"] == "TECHNICAL_FAILURE"), None)
    blocked = next((item for item in cases if item["status"] == "BLOCK"), None)
    if technical:
        return _write(
            args.output,
            "TECHNICAL_FAILURE",
            f"CONFORMANCE_CASE:{technical['name']}:{technical['code']}",
            cases,
        )
    if blocked:
        return _write(
            args.output, "BLOCK", f"CONFORMANCE_CASE:{blocked['name']}:{blocked['code']}", cases
        )
    return _write(args.output, "PASS", "SOURCE_INTEGRITY_CONFORMANCE_PASS", cases)


if __name__ == "__main__":
    raise SystemExit(main())
