"""Start the fixed pytest profile from the read-only, workflow-bound collector."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_EXECNET_BOOTSTRAP = "import sys;exec(eval(sys.stdin.readline()))"


def trusted_collector(entry_file: str, isolated: int) -> Path:
    collector = Path("/collector").resolve()
    if isolated != 1 or Path(entry_file).resolve() != collector / "quality_pytest_entry.py":
        raise RuntimeError("QUALITY_PYTEST_UNTRUSTED_ENTRY")
    return collector


def load_guard(collector: Path) -> ModuleType:
    specification = importlib.util.find_spec("quality_pytest_guard")
    expected = collector / "quality_pytest_guard.py"
    if specification is None or not specification.origin:
        raise RuntimeError("QUALITY_PYTEST_MISSING_GUARD")
    if Path(specification.origin).resolve() != expected:
        raise RuntimeError("QUALITY_PYTEST_SHADOWED_GUARD")
    return importlib.import_module("quality_pytest_guard")


def preload_plugins() -> None:
    for name in ("xdist.plugin", "pytest_cov.plugin"):
        specification = importlib.util.find_spec(name)
        if specification is None or not specification.origin:
            raise RuntimeError("QUALITY_PYTEST_MISSING_PLUGIN")
        origin = Path(specification.origin).resolve()
        forbidden = (Path("/target"), Path("/work"), Path("/trusted/python-dependencies"))
        if any(origin.is_relative_to(path.resolve()) for path in forbidden):
            raise RuntimeError("QUALITY_PYTEST_SHADOWED_PLUGIN")
        importlib.import_module(name)


def worker_bootstrap(arguments: list[str]) -> int:
    if arguments != ["--worker", "-u", "-c", _EXECNET_BOOTSTRAP]:
        raise RuntimeError("QUALITY_PYTEST_INVALID_WORKER_BOOTSTRAP")
    # execnet's pinned popen protocol requires the real __main__ module namespace.
    exec(_EXECNET_BOOTSTRAP, globals())
    return 0


def main() -> int:
    collector = trusted_collector(__file__, sys.flags.isolated)
    sys.path.insert(0, str(collector))
    guard = load_guard(collector)
    preload_plugins()
    if sys.argv[1:2] == ["--worker"]:
        return worker_bootstrap(sys.argv[1:])
    import pytest

    exit_code: int = guard.finish_controller(int(pytest.main(sys.argv[1:])))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
