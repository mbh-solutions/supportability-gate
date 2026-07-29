from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from supportability_gate import characterization

PYTHON_CONTRACT = """\
schema_version = "1.0"
language = "python"
production_paths = ["src"]
high_risk_paths = ["src/sample.py"]

[[gates]]
adapter = "python.c901-touched.v1"
paths = ["src"]

[complexity]
adapter = "python.c901-touched.v1"
maximum = 10
"""

TYPESCRIPT_CONTRACT = PYTHON_CONTRACT.replace(
    'language = "python"', 'language = "typescript"'
).replace(
    'adapter = "python.c901-touched.v1"',
    'adapter = "typescript.c901-equivalent-touched.v1"',
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _manifest(scenarios: list[dict[str, object]]) -> str:
    return json.dumps({"schema_version": "1.0", "scenarios": scenarios}, indent=2) + "\n"


def _scenario(identifier: str, path: str) -> dict[str, object]:
    return {"id": identifier, "kind": "golden", "covers": [path]}


def _driver(identifier: str, source_path: str, language: str) -> str:
    if language == "python":
        return f'''\
import json
import os
from pathlib import Path

target = Path(os.environ["SUPPORTABILITY_CHARACTERIZATION_TARGET"])
behavior = {{"source": (target / "{source_path}").read_text(encoding="utf-8")}}
print(json.dumps({{"schema_version": "1.0", "scenario": "{identifier}", "behavior": behavior}}, sort_keys=True))
'''
    return f'''\
import {{ readFileSync }} from "node:fs";
import {{ join }} from "node:path";
const target = process.env.SUPPORTABILITY_CHARACTERIZATION_TARGET;
const behavior = {{ source: readFileSync(join(target, "{source_path}"), "utf8") }};
console.log(JSON.stringify({{ schema_version: "1.0", scenario: "{identifier}", behavior }}));
'''


def _add_scenario_files(
    repository: Path,
    identifier: str,
    source_path: str,
    language: str,
    expected: str,
) -> None:
    extension = "py" if language == "python" else "mjs"
    root = repository / "tests" / "characterization"
    _write(
        root / f"{identifier}.characterization.{extension}",
        _driver(identifier, source_path, language),
    )
    _write(root / f"{identifier}.golden.json", json.dumps({"source": expected}) + "\n")


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _repository(
    tmp_path: Path,
    *,
    language: str = "python",
    add_scenario: bool = False,
    changed_source: bool = False,
    changed_golden: bool = False,
    uncovered_high_risk: bool = False,
) -> tuple[Path, Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "core.autocrlf", "false")
    _git(repository, "remote", "add", "origin", "https://github.com/example/fixture.git")
    source_path = "src/sample.py" if language == "python" else "src/sample.ts"
    contract_text = PYTHON_CONTRACT if language == "python" else TYPESCRIPT_CONTRACT
    contract_text = contract_text.replace("src/sample.py", source_path)
    if uncovered_high_risk:
        contract_text = contract_text.replace(
            f'high_risk_paths = ["{source_path}"]',
            f'high_risk_paths = ["{source_path}", "src/risk.py"]',
        )
    _write(repository / ".supportability.toml", contract_text)
    _write(repository / source_path, "base\n")
    if uncovered_high_risk:
        _write(repository / "src/risk.py", "risk\n")
    scenarios = [_scenario("existing", source_path)]
    _write(repository / characterization.MANIFEST_PATH, _manifest(scenarios))
    _add_scenario_files(repository, "existing", source_path, language, "base\n")
    base_sha = _commit(repository, "base")
    _write(repository / "docs/note.md", "head\n")
    if changed_source:
        _write(repository / source_path, "head\n")
    if changed_golden:
        _write(
            repository / "tests/characterization/existing.golden.json",
            json.dumps({"source": "head\n"}) + "\n",
        )
    if add_scenario:
        scenarios.append(_scenario("new-scenario", source_path))
        _write(repository / characterization.MANIFEST_PATH, _manifest(scenarios))
        _add_scenario_files(repository, "new-scenario", source_path, language, "base\n")
    head_sha = _commit(repository, "head")
    base_checkout = tmp_path / "base"
    _git(repository, "worktree", "add", "--detach", str(base_checkout), base_sha)
    return repository, base_checkout, base_sha, head_sha


def _captures(
    repository: Path,
    base_checkout: Path,
    base_sha: str,
    head_sha: str,
) -> tuple[dict[str, object], dict[str, object]]:
    common = {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "repository": "example/fixture",
        "repository_id": "123",
        "workflow_sha": "f" * 40,
        "run_id": "456",
        "run_attempt": "1",
    }
    base = characterization.capture_evidence(
        base_checkout,
        repository,
        side="base",
        job="characterize-base",
        **common,
    )
    head = characterization.capture_evidence(
        repository,
        repository,
        side="head",
        job="characterize-head",
        **common,
    )
    return base, head


def _write_artifacts(
    tmp_path: Path, base: dict[str, object], head: dict[str, object]
) -> tuple[Path, Path]:
    base_path, head_path = tmp_path / "base.json", tmp_path / "head.json"
    _write(base_path, json.dumps(base, sort_keys=True) + "\n")
    _write(head_path, json.dumps(head, sort_keys=True) + "\n")
    return base_path, head_path


def _verify(
    repository: Path,
    base_sha: str,
    head_sha: str,
    base_path: Path,
    head_path: Path,
) -> dict[str, object]:
    return characterization.verify_evidence(
        repository,
        base_sha,
        head_sha,
        base_path,
        head_path,
        repository_name="example/fixture",
        repository_id="123",
        workflow_sha="f" * 40,
        run_id="456",
        run_attempt="1",
        base_artifact_id="10",
        base_artifact_digest="a" * 64,
        head_artifact_id="11",
        head_artifact_digest="b" * 64,
    )


@pytest.mark.parametrize("language", ["python", "typescript"])
def test_existing_characterization_passes_with_exact_identity(
    tmp_path: Path, language: str
) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, language=language)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert result["base_sha"] == base_sha
    assert result["head_sha"] == head_sha
    assert result["scenarios"][0]["compatibility"] == "PASS"
    assert result["coverage"]["required_paths"] == [
        "src/sample.py" if language == "python" else "src/sample.ts"
    ]


def test_new_scenario_runs_against_same_base_and_head(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, add_scenario=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert result["overall_result"] == "PASS"
    assert [item["id"] for item in result["scenarios"]] == ["existing", "new-scenario"]


def test_missing_baseline_and_head_only_claim_block(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    _, head = _captures(repository, base_checkout, base_sha, head_sha)
    missing = tmp_path / "missing.json"
    _, head_path = _write_artifacts(tmp_path, {}, head)

    result = _verify(repository, base_sha, head_sha, missing, head_path)

    assert "MISSING_BASELINE" in result["policy_blocks"]
    assert "HEAD_ONLY_CHARACTERIZATION_CLAIM" in result["policy_blocks"]


def test_changed_golden_output_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, changed_golden=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHANGED_GOLDEN_OUTPUT:existing" in result["policy_blocks"]


def test_unauthenticated_proof_text_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    base["authentication"] = {"claim": "trust me"}
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "UNAUTHENTICATED_CHARACTERIZATION_EVIDENCE" in result["policy_blocks"]


def test_stale_artifact_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    head["target_sha"] = base_sha
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "STALE_POST_CHANGE_ARTIFACT" in result["policy_blocks"]


def test_incompatible_behavior_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, changed_source=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "INCOMPATIBLE_POST_CHANGE_BEHAVIOR:existing" in result["policy_blocks"]


def test_replay_drift_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    head["scenarios"][0]["deterministic"] = False
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHARACTERIZATION_REPLAY_DRIFT:existing" in result["policy_blocks"]


def test_uncovered_high_risk_path_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path, uncovered_high_risk=True)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "MISSING_CHARACTERIZATION_COVERAGE:src/risk.py" in result["policy_blocks"]


def test_wrong_driver_artifact_identity_blocks(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    head["scenarios"][0]["driver_blob_sha"] = "0" * 40
    paths = _write_artifacts(tmp_path, base, head)

    result = _verify(repository, base_sha, head_sha, *paths)

    assert "CHARACTERIZATION_DRIVER_IDENTITY_MISMATCH:existing" in result["policy_blocks"]


@pytest.mark.parametrize("kind", sorted(characterization.KINDS))
def test_all_characterization_types_use_same_manifest_format(kind: str) -> None:
    content = _manifest([{"id": "scenario", "kind": kind, "covers": ["src/sample.py"]}])

    parsed = characterization.parse_manifest(content.encode(), "a" * 40)

    assert parsed.scenarios[0].kind == kind


def test_verification_is_byte_deterministic(tmp_path: Path) -> None:
    repository, base_checkout, base_sha, head_sha = _repository(tmp_path)
    base, head = _captures(repository, base_checkout, base_sha, head_sha)
    paths = _write_artifacts(tmp_path, base, head)

    first = _verify(repository, base_sha, head_sha, *paths)
    second = _verify(repository, base_sha, head_sha, *paths)

    assert characterization._canonical(first) == characterization._canonical(second)
