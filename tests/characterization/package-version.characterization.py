from __future__ import annotations

import json

from supportability_gate import __version__


def main() -> None:
    print(
        json.dumps(
            {
                "behavior": {"version_is_string": isinstance(__version__, str)},
                "scenario": "package-version",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
