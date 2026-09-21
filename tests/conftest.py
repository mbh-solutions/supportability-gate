"""Never-merge diagnostic hook for the deployed S02 qualification."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Run the known deployed-container failure first for fast diagnosis."""
    target = "test_milestone_two_block_evidence_is_byte_identical"
    match = next((item for item in items if item.name == target), None)
    if match is not None:
        items.remove(match)
        items.insert(0, match)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]):
    """Stop the diagnostic qualification after its first retained failure."""
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        item.session.shouldstop = "never-merge diagnostic: first failure retained"
