from pathlib import Path

WORKFLOW = (Path(__file__).parents[1] / ".github/workflows/organization-required.yml").read_text(
    encoding="utf-8"
)


def _job(name: str, next_name: str | None) -> str:
    body = WORKFLOW.split(f"\n  {name}:\n", 1)[1]
    return body.split(f"\n  {next_name}:\n", 1)[0] if next_name else body


def test_connector_collection_is_separate_from_deterministic_evidence() -> None:
    connector = _job("collect-codex-review", "characterize-base")
    deterministic = _job("deterministic-evidence", "supportability-gate")

    assert "needs: observe-codex-review" in connector
    assert "require_focused_completion" in connector
    assert "observe-codex-review" not in deterministic
    assert "require_focused_completion" not in deterministic


def test_required_gate_binds_connector_and_deterministic_results() -> None:
    gate = _job("supportability-gate", None)

    assert "name: Supportability Gate" in gate
    assert "needs: [collect-codex-review, deterministic-evidence]" in gate
    assert "CONNECTOR_RESULT: ${{ needs.collect-codex-review.result }}" in gate
    assert "DETERMINISTIC_RESULT: ${{ needs.deterministic-evidence.result }}" in gate
    assert 'for result in "$CONNECTOR_RESULT" "$DETERMINISTIC_RESULT"; do' in gate
    assert 'if [ "$result" != "success" ]; then status=1; fi' in gate
