"""Protected S07 canary for an exact ordinary related test."""

from supportability_gate.refactor_targets import related_test_matches


def test_refactor_targets_canary_uses_the_fixed_python_profile() -> None:
    assert related_test_matches(
        "tests/test_refactor_targets.py",
        "src/supportability_gate/refactor_targets.py",
        "python",
    )
