"""Never-merge diagnostic hook for the deployed S02 qualification."""

from __future__ import annotations

import pytest


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[object]):
    """Stop the diagnostic qualification after its first retained failure."""
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        item.session.shouldstop = "never-merge diagnostic: first failure retained"
