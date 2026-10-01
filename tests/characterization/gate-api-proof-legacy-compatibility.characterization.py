from __future__ import annotations

import json
from pathlib import Path

from supportability_gate import refactor_policy


def _case(series: str, broad: bool) -> dict[str, object]:
    request = {
        "schema_version": "2.0",
        "repository": "example-org/order-service",
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "broad": broad,
        "scope": ["src/application/pricing.py", "tests/test_pricing.py"],
        "targets": ["src/application/pricing.py::function:calculate_total:1-20"],
        "related_tests": [
            {
                "path": "tests/test_pricing.py",
                "targets": ["src/application/pricing.py::function:calculate_total:1-20"],
            }
        ],
        "sequence": {"predecessor_sha": "1" * 40, "series_id": series, "step": 1},
    }
    authorization = refactor_policy._parse_authorization(
        refactor_policy.AUTHORIZATION_PREFIX + json.dumps(request)
    )
    return {"input": request, "output": refactor_policy._authorization_payload(authorization)}


def main() -> None:
    source = Path(refactor_policy.__file__).resolve()
    expected = Path.cwd() / "src/supportability_gate/refactor_policy.py"
    if source != expected.resolve():
        raise RuntimeError("authorization responsibility was not loaded from target source")
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "scenario": "gate-api-proof-legacy-compatibility",
                "behavior": [_case("narrow-pricing", False), _case("broad-pricing", True)],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
