"""Construct fixed quality commands for the GitHub-hosted runner."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from supportability_gate import contract, git_changes, quality_profile

_COVERAGE_CONFIGURATION = b"[report]\nexclude_lines =\n"
CONTAINER_IMAGE = "ubuntu@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4"
CONTAINER_PLATFORM = "linux/amd64"
CONTAINER_MEMORY = "2g"
CONTAINER_CPUS = "2"
CONTAINER_PIDS_LIMIT = "256"
_SAFE_ADAPTER = re.compile(r"[^a-zA-Z0-9_.-]+")
_SYSTEM_LIBRARY_DIRECTORIES = (Path("/lib/x86_64-linux-gnu"), Path("/usr/lib/x86_64-linux-gnu"))
_GIT_SUPPORT_DIRECTORIES = (
    (Path("/usr/lib/git-core"), "/usr/lib/git-core"),
    (Path("/usr/share/git-core"), "/usr/share/git-core"),
)


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


def command_work_directory(output: Path, adapter: str) -> Path:
    """Return the sole host directory writable by one target command."""
    return output / "sandbox" / _SAFE_ADAPTER.sub("-", adapter)


def container_id_file(output: Path, adapter: str) -> Path:
    """Return a supervisor-only cidfile for bounded timeout cleanup."""
    path = output / "container-ids" / f"{_SAFE_ADAPTER.sub('-', adapter)}.cid"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    return path


def trusted_directory(output: Path) -> Path:
    """Return supervisor-owned configuration and dependency storage."""
    return output / "trusted"


def _mount(source: Path, target: str, *, readonly: bool = True) -> tuple[str, str]:
    options = f"type=bind,src={source.resolve()},dst={target}"
    if readonly:
        options += ",readonly"
    return "--mount", options


def _sandbox_arguments(plan: CommandPlan, repository: Path, output: Path) -> tuple[str, ...]:
    replacements = (
        (str(repository.resolve()), "/target"),
        (str((output / "quality-tools").resolve()), "/work/quality-tools"),
        (str(output.resolve()), "/work"),
    )
    arguments: list[str] = []
    for argument in plan.actual:
        translated = argument
        for source, target in replacements:
            translated = translated.replace(source, target)
        arguments.append(translated)
    return tuple(arguments)


def _runtime_mounts(toolcache: Path) -> tuple[str, ...]:
    mounts: tuple[str, ...] = ()
    for directory in _SYSTEM_LIBRARY_DIRECTORIES:
        if directory.is_dir():
            mounts = (*mounts, *_mount(directory, directory.as_posix()))
    git_executable = Path(shutil.which("git") or "/usr/bin/git")
    if git_executable.is_file():
        mounts = (*mounts, *_mount(git_executable, "/usr/bin/git"))
        for source, target in _GIT_SUPPORT_DIRECTORIES:
            if source.is_dir():
                mounts = (*mounts, *_mount(source, target))
    node_executable = Path(shutil.which("node") or "/usr/bin/node")
    if node_executable.is_file() and not node_executable.is_relative_to(toolcache):
        mounts = (*mounts, *_mount(node_executable, node_executable.as_posix()))
    return mounts


def sandbox_command(
    plan: CommandPlan,
    *,
    repository: Path,
    output: Path,
    collector: Path,
    toolcache: Path = Path("/opt/hostedtoolcache"),
    extra_mounts: tuple[tuple[Path, str], ...] = (),
    extra_environment: dict[str, str] | None = None,
    workdir: str | None = None,
) -> tuple[str, ...]:
    """Wrap one fixed command in the immutable no-network target boundary."""
    trusted = trusted_directory(output)
    work = command_work_directory(output, plan.adapter)
    evidence = output / "target-inaccessible-evidence"
    for directory in (trusted, work, evidence):
        directory.mkdir(parents=True, exist_ok=True)
    mounts: tuple[str, ...] = (
        *_mount(repository, "/target"),
        *_mount(trusted, "/trusted"),
        *_mount(collector, "/collector"),
        *_mount(evidence, "/evidence"),
        *_mount(work, "/work", readonly=False),
        *_mount(toolcache, "/opt/hostedtoolcache"),
        *_runtime_mounts(toolcache),
    )
    tools = trusted / "quality-tools"
    if tools.is_dir():
        mounts = (*mounts, *_mount(tools, "/work/quality-tools"))
    for name in (
        "coverage.ini",
        "dependency-cruiser.json",
        "eslint.config.mjs",
        "importlinter.ini",
        "mypy.ini",
        "prettier.ignore",
        "prettier.json",
        "pytest.ini",
        "tsconfig-build.json",
        "tsconfig-check.json",
    ):
        configuration = trusted / name
        if configuration.is_file():
            mounts = (*mounts, *_mount(configuration, f"/work/{name}"))
    target_modules = trusted / "target-dependencies" / "node_modules"
    if target_modules.is_dir():
        mounts = (*mounts, *_mount(target_modules, "/target/node_modules"))
    for source, target in extra_mounts:
        mounts = (*mounts, *_mount(source, target))
    executable_paths = {
        str(Path(sys.executable).resolve().parent),
        str(Path(shutil.which("node") or "/usr/bin/node").resolve().parent),
        "/usr/local/sbin",
        "/usr/local/bin",
        "/usr/sbin",
        "/usr/bin",
        "/sbin",
        "/bin",
    }
    environment: tuple[str, ...] = (
        "--env",
        "CI=true",
        "--env",
        "HOME=/work/home",
        "--env",
        "LANG=C.UTF-8",
        "--env",
        "NO_COLOR=1",
        "--env",
        "PYTHONHASHSEED=0",
        "--env",
        "PYTHONPYCACHEPREFIX=/work/pycache",
        "--env",
        f"PATH={':'.join(sorted(executable_paths))}",
    )
    for name, value in sorted((extra_environment or {}).items()):
        environment = (*environment, "--env", f"{name}={value}")
    container_workdir = workdir or (
        "/target/src" if plan.adapter == "python.import-linter.v1" else "/target"
    )
    return (
        shutil.which("docker") or "docker",
        "run",
        "--init",
        "--rm",
        "--cidfile",
        str(container_id_file(output, plan.adapter).resolve()),
        "--platform",
        CONTAINER_PLATFORM,
        "--read-only",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        CONTAINER_PIDS_LIMIT,
        "--memory",
        CONTAINER_MEMORY,
        "--cpus",
        CONTAINER_CPUS,
        "--stop-timeout",
        "5",
        "--user",
        f"{getattr(os, 'getuid', lambda: 1000)()}:{getattr(os, 'getgid', lambda: 1000)()}",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=256m",
        *mounts,
        *environment,
        "--workdir",
        container_workdir,
        CONTAINER_IMAGE,
        *_sandbox_arguments(plan, repository, output),
    )


def provisioning_command(plan: CommandPlan, output: Path) -> tuple[str, ...]:
    """Translate a fixed provisioning vector to supervisor-owned host paths."""
    trusted = trusted_directory(output)
    translated = tuple(
        item.replace(
            str((output / "quality-tools").resolve()),
            str((trusted / "quality-tools").resolve()),
        )
        for item in plan.actual
    )
    return translated


def _replace_tokens(arguments: tuple[str, ...], values: dict[str, str]) -> tuple[str, ...]:
    replaced: list[str] = []
    for argument in arguments:
        if argument in {"$COVERAGE_FILES", "$SOURCE_FILES", "$SOURCE_PATHS", "$TEST_FILES"}:
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
    parser_url = "file:///work/quality-tools/node_modules/@typescript-eslint/parser/dist/index.js"
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
            "outDir": "/work/build",
            "rootDir": "/target",
        }
    )
    (output / "tsconfig-check.json").write_text(
        json.dumps(check, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (output / "tsconfig-build.json").write_text(
        json.dumps(build, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _write_python_configs(output: Path, source_files: tuple[str, ...]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "mypy.ini").write_text(
        "[mypy]\npython_version = 3.12\nstrict = True\nmypy_path = src\n",
        encoding="utf-8",
        newline="\n",
    )
    (output / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\npythonpath =\n    /target/src\n"
        "    /target\n"
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
    trusted = trusted_directory(output)
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
        _write_typescript_configs(
            trusted / "quality-tools",
            trusted,
            Path("/target"),
            tuple(
                f"/target/{path}"
                for path in source_files
                if path.endswith(quality_profile.SOURCE_SUFFIXES["typescript"])
            ),
        )
    if language in {"python", "mixed"}:
        _write_python_configs(
            output,
            tuple(path for path in source_files if path.endswith((".py", ".pyi"))),
        )
        _write_python_configs(
            trusted,
            tuple(path for path in source_files if path.endswith((".py", ".pyi"))),
        )
    lint_imports = shutil.which("lint-imports") or str(
        Path(sys.executable).with_name("lint-imports")
    )
    values = {
        "$LINT_IMPORTS": lint_imports,
        "$NODE": str(Path(shutil.which("node") or "node").resolve()),
        "$NPM": str(Path(shutil.which("npm") or "npm").resolve()),
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
            "$COVERAGE_FILES": "\0".join(
                f"--test-coverage-include={path}" for path in selected_sources
            ),
            "$SOURCE_FILES": "\0".join(
                str((repository / path).resolve()) for path in selected_sources
            ),
            "$SOURCE_PATHS": "\0".join(selected_sources),
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
