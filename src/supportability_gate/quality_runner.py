"""Construct fixed quality commands for the GitHub-hosted runner."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from supportability_gate import contract, git_changes, quality_profile

_COVERAGE_CONFIGURATION = b"[report]\nexclude_lines =\n"


@dataclass(frozen=True)
class CommandPlan:
    adapter: str
    actual: tuple[str, ...]
    evidence: tuple[str, ...]
    proof_kind: str
    source_files: tuple[str, ...]


def fixed_environment(output: Path, repository: Path) -> dict[str, str]:
    """Return the fixed target-command environment."""
    keys = ("HOME", "PATH", "SystemRoot", "WINDIR")
    allowed = {key: os.environ[key] for key in keys if key in os.environ}
    allowed.update(
        {
            "CI": "true",
            "NO_COLOR": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONPYCACHEPREFIX": str(output / "pycache"),
        }
    )
    return allowed


def _replace_tokens(arguments: tuple[str, ...], values: dict[str, str]) -> tuple[str, ...]:
    replaced: list[str] = []
    for argument in arguments:
        if argument in {"$SOURCE_FILES", "$TEST_FILES"}:
            replaced.extend(values[argument].split("\0") if values[argument] else ())
        else:
            value = argument
            for token, replacement in values.items():
                value = value.replace(token, replacement)
            replaced.append(value)
    return tuple(replaced)


def _write_typescript_configs(
    tools: Path, output: Path, repository: Path, source_files: tuple[str, ...]
) -> None:
    tools.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    parser_url = (
        tools / "node_modules" / "@typescript-eslint" / "parser" / "dist" / "index.js"
    ).as_uri()
    (tools / "eslint.config.mjs").write_text(
        "import parser from " + json.dumps(parser_url) + ";\n"
        "export default [{ files: ['**/*.{js,jsx,ts,tsx,mjs,cjs,mts,cts}'], "
        "languageOptions: { parser, parserOptions: { ecmaVersion: 'latest', sourceType: 'module' } }, "
        "rules: { 'no-constant-condition': 'error', 'no-debugger': 'error', "
        "'no-duplicate-case': 'error', 'no-unreachable': 'error' } }];\n",
        encoding="utf-8",
        newline="\n",
    )
    (tools / "prettier.json").write_text("{}\n", encoding="utf-8", newline="\n")
    (tools / "prettier.ignore").write_text("\n", encoding="utf-8", newline="\n")
    cruiser = {
        "forbidden": [
            {"name": "no-circular", "severity": "error", "from": {}, "to": {"circular": True}},
            {
                "name": "domain-stays-inward",
                "severity": "error",
                "from": {"path": "^src/domain"},
                "to": {"path": "^src/(application|infrastructure|presentation)"},
            },
            {
                "name": "application-stays-inward",
                "severity": "error",
                "from": {"path": "^src/application"},
                "to": {"path": "^src/(infrastructure|presentation)"},
            },
        ],
        "options": {"doNotFollow": {"path": "node_modules"}},
    }
    (tools / "dependency-cruiser.json").write_text(
        json.dumps(cruiser, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    check = {
        "compilerOptions": {
            "allowImportingTsExtensions": True,
            "allowJs": True,
            "checkJs": True,
            "jsx": "preserve",
            "module": "NodeNext",
            "moduleResolution": "NodeNext",
            "noEmit": True,
            "skipLibCheck": True,
            "strict": True,
            "target": "ES2022",
        },
        "files": list(source_files),
    }
    build = json.loads(json.dumps(check))
    build["compilerOptions"].update(
        {
            "allowImportingTsExtensions": False,
            "declaration": True,
            "noEmit": False,
            "outDir": str(output / "build"),
            "rootDir": str(repository / "src"),
        }
    )
    (output / "tsconfig-check.json").write_text(
        json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (output / "tsconfig-build.json").write_text(
        json.dumps(build, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _write_python_configs(output: Path, repository: Path, source_files: tuple[str, ...]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "mypy.ini").write_text(
        "[mypy]\npython_version = 3.12\nstrict = True\nmypy_path = src\n",
        encoding="utf-8",
        newline="\n",
    )
    (output / "pytest.ini").write_text(
        f"[pytest]\ntestpaths = tests\npythonpath =\n    {repository / 'src'}\n    {repository}\n"
        "addopts = -p no:cacheprovider\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_coverage_config(output)
    roots = tuple(
        sorted(
            {
                Path(path).parts[1]
                for path in source_files
                if len(Path(path).parts) > 2 and Path(path).name == "__init__.py"
            }
        )
    )
    packages = roots or ("missing_quality_root",)
    indented = "\n".join(f"    {item}" for item in packages)
    (output / "importlinter.ini").write_text(
        "[importlinter]\nroot_packages =\n"
        + indented
        + "\n\n[importlinter:contract:quality-acyclic]\n"
        + "name = Fixed quality profile has no sibling cycles\n"
        + "type = acyclic_siblings\nancestors =\n"
        + indented
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_coverage_config(output: Path) -> Path:
    path = output / "coverage.ini"
    path.write_bytes(_COVERAGE_CONFIGURATION)
    if path.read_bytes() != _COVERAGE_CONFIGURATION:
        raise OSError("trusted coverage configuration mismatch")
    return path


def command_plans(
    language: str,
    repository: Path,
    output: Path,
    test_files: tuple[str, ...],
    source_files: tuple[str, ...],
) -> tuple[CommandPlan, ...]:
    """Build executable vectors from the immutable profile templates."""
    tools = output / "quality-tools"
    if language in {"typescript", "mixed"}:
        _write_typescript_configs(
            tools,
            output,
            repository,
            tuple(
                str((repository / path).resolve())
                for path in source_files
                if path.endswith(quality_profile.SOURCE_SUFFIXES["typescript"])
            ),
        )
    if language in {"python", "mixed"}:
        _write_python_configs(
            output,
            repository,
            tuple(path for path in source_files if path.endswith((".py", ".pyi"))),
        )
    lint_imports = shutil.which("lint-imports") or str(
        Path(sys.executable).with_name("lint-imports")
    )
    values = {
        "$LINT_IMPORTS": lint_imports,
        "$NODE": shutil.which("node") or "node",
        "$NPM": shutil.which("npm") or "npm",
        "$OUTPUT": str(output),
        "$PYTHON": sys.executable,
        "$REPOSITORY": str(repository),
        "$TOOLS": str(tools),
    }
    plans: list[CommandPlan] = []
    for adapter, arguments in quality_profile.command_templates(language):
        profile = adapter.split(".", 1)[0]
        selected_sources = tuple(
            path for path in source_files if path.endswith(quality_profile.SOURCE_SUFFIXES[profile])
        )
        selected_tests = tuple(
            path for path in test_files if path.endswith(quality_profile.TEST_SUFFIXES[profile])
        )
        selected = {
            **values,
            "$SOURCE_FILES": "\0".join(
                str((repository / path).resolve()) for path in selected_sources
            ),
            "$TEST_FILES": "\0".join(str((repository / path).resolve()) for path in selected_tests),
        }
        plans.append(
            CommandPlan(
                adapter,
                _replace_tokens(arguments, selected),
                arguments,
                quality_profile.expected_proof_kind(adapter),
                selected_sources,
            )
        )
    return tuple(plans)


def profile_files(
    repository: Path,
    head_sha: str,
    policy: contract.Contract,
    records: list[git_changes.CommandRecord],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Read complete production, source, and test manifests from the head tree."""
    production = quality_profile.production_files(repository, head_sha, policy, records)
    return (
        production,
        quality_profile.source_files(production, policy.language),
        quality_profile.test_files(repository, head_sha, policy.language, records),
    )
