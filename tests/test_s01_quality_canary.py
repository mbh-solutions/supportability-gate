def test_s01_quality_command_failure_canary() -> None:
    """Deliberately fail in a never-merge protected canary."""
    raise AssertionError("S01_FAILING_QUALITY_COMMAND_CANARY")
