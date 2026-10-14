"""Fixed Python first-baseline observer; target code runs only in the hosted sandbox."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import marshal
import os
import runpy
import sys
from pathlib import Path
from types import CodeType


def canonical_code(code: CodeType, root: Path) -> CodeType:
    filename = "/target/" + Path(code.co_filename).resolve().relative_to(root).as_posix()
    return code.replace(
        co_filename=filename,
        co_consts=tuple(
            canonical_code(item, root) if isinstance(item, CodeType) else item
            for item in code.co_consts
        ),
    )


def main() -> None:
    hits: dict[str, dict[str, int]] = {}
    code_ids = {}
    root = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"]).resolve()

    locations = {}

    def trace(frame, event, arg):
        code = frame.f_code
        identity = id(code)
        if identity not in locations:
            filename = Path(code.co_filename)
            path = (
                filename.relative_to(root).as_posix()
                if filename.is_absolute() and filename.is_relative_to(root)
                else None
            )
            # Retain the object so its identity cannot be reused during the run.
            locations[identity] = (code, path)
        _, path = locations[identity]
        if path is None:
            return None

        def record(frame, event, arg):
            if event == "line":
                if identity not in code_ids:
                    code_ids[identity] = hashlib.sha256(
                        marshal.dumps(canonical_code(code, root), 2)
                    ).hexdigest()
                key = code_ids[identity]
                file_hits = hits.setdefault(path, {})
                file_hits[key] = file_hits.get(key, 0) + 1
            return record

        return record

    output = io.StringIO()
    sys.settrace(trace)
    try:
        with contextlib.redirect_stdout(output):
            runpy.run_path(sys.argv[1], run_name="__main__")
    finally:
        sys.settrace(None)
    witness = {
        path: {
            "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
            "execution": counts,
        }
        for path, counts in sorted(hits.items())
    }
    print(json.dumps({"driver": json.loads(output.getvalue()), "witness": witness}, sort_keys=True))


if __name__ == "__main__":
    main()
