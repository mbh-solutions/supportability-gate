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

    def trace(frame, event, arg):
        filename = Path(frame.f_code.co_filename)
        if event == "line" and filename.is_absolute() and filename.is_relative_to(root):
            path = filename.relative_to(root).as_posix()
            if frame.f_code not in code_ids:
                code_ids[frame.f_code] = hashlib.sha256(
                    marshal.dumps(canonical_code(frame.f_code, root), 2)
                ).hexdigest()
            key = code_ids[frame.f_code]
            file_hits = hits.setdefault(path, {})
            file_hits[key] = file_hits.get(key, 0) + 1
        return trace

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
