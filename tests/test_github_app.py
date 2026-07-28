from __future__ import annotations

import base64
import json
import urllib.error
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from supportability_gate.github_app import CHECK_NAME, GitHubApp, app_jwt
from supportability_gate.semantic_review import EvidencePacket, SemanticReviewError


class _Reply:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def _private_key() -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _decode(segment: str) -> object:
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def test_app_jwt_binds_app_and_short_lifetime() -> None:
    token = app_jwt(42, _private_key(), now=1000)
    header, payload, signature = token.split(".")
    assert _decode(header) == {"alg": "RS256", "typ": "JWT"}
    assert _decode(payload) == {"exp": 1540, "iat": 940, "iss": "42"}
    assert signature


def test_invalid_private_key_blocks_with_stable_error() -> None:
    with pytest.raises(SemanticReviewError, match="INVALID_APP_PRIVATE_KEY"):
        app_jwt(42, b"not a key")


def test_installation_auth_verifies_app_identity() -> None:
    replies = iter([{"id": 42}, {"token": "installation-token"}])
    app = GitHubApp(42, 7, _private_key(), opener=lambda *args, **kwargs: _Reply(next(replies)))
    assert app.installation_token() == "installation-token"


def test_wrong_app_identity_blocks() -> None:
    app = GitHubApp(42, 7, _private_key(), opener=lambda *args, **kwargs: _Reply({"id": 41}))
    with pytest.raises(SemanticReviewError, match="APP_IDENTITY_MISMATCH"):
        app.installation_token()


def test_open_pull_truncation_blocks() -> None:
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply([{}] * 100))
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.open_pulls("mbh-solutions/supportability-gate", "token")


def test_stale_pull_evidence_blocks_before_publication() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    current = {"base": {"sha": "c" * 40}, "head": {"sha": "b" * 40}}
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(current))
    with pytest.raises(SemanticReviewError, match="STALE_EVIDENCE"):
        app.assert_current(packet, 3, "token")


def test_exact_evidence_replay_reuses_app_result() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    runs = {
        "total_count": 1,
        "check_runs": [
            {
                "app": {"id": 42},
                "conclusion": "success",
                "external_id": packet.sha256,
                "status": "completed",
            }
        ],
    }
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply(runs))
    assert app.replay_result(packet, "token") is True


def test_replay_truncation_blocks() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply({"total_count": 100, "check_runs": []}),
    )
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.replay_result(packet, "token")


def test_compare_evidence_and_check_bind_exact_head_app_and_hash() -> None:
    requests: list[Any] = []
    comparison = {
        "files": [{"filename": "src/a.py", "patch": "+safe", "sha": "c" * 40, "status": "modified"}]
    }

    def open_request(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        if "/compare/" in request.full_url:
            return _Reply(comparison)
        body = json.loads(request.data)
        return _Reply({"app": {"id": 42}, "head_sha": body["head_sha"], "id": 99})

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    pull: dict[str, Any] = {
        "number": 3,
        "base": {"sha": "a" * 40},
        "head": {"sha": "b" * 40},
    }
    packet = app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")
    result = app.publish_check(packet, "token", "success", "PASS")
    payload = json.loads(requests[-1].data)
    assert result["id"] == 99
    assert payload["name"] == CHECK_NAME
    assert payload["head_sha"] == "b" * 40
    assert packet.sha256 in payload["output"]["summary"]


@pytest.mark.parametrize(
    "files",
    [
        [{"filename": "large.bin", "patch": None}],
        [{"filename": f"file-{index}", "patch": "+safe"} for index in range(300)],
    ],
)
def test_incomplete_compare_evidence_blocks(files: list[dict[str, object]]) -> None:
    app = GitHubApp(42, 7, b"unused", opener=lambda *args, **kwargs: _Reply({"files": files}))
    pull = {"number": 3, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
    with pytest.raises(SemanticReviewError, match="INCOMPLETE_GITHUB_EVIDENCE"):
        app.evidence_packet("mbh-solutions/supportability-gate", pull, "token")


def test_check_response_from_wrong_app_blocks() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    app = GitHubApp(
        42,
        7,
        b"unused",
        opener=lambda *args, **kwargs: _Reply({"app": {"id": 41}, "head_sha": "b" * 40}),
    )
    with pytest.raises(SemanticReviewError, match="CHECK_APP_IDENTITY_MISMATCH"):
        app.publish_check(packet, "token", "success", "PASS")


def test_pending_check_is_completed_with_same_evidence_binding() -> None:
    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    requests: list[Any] = []

    def open_request(request: Any, **kwargs: object) -> _Reply:
        requests.append(request)
        return _Reply(
            {
                "app": {"id": 42},
                "external_id": packet.sha256,
                "head_sha": packet.head_sha,
                "id": 99,
            }
        )

    app = GitHubApp(42, 7, b"unused", opener=open_request)
    check_id = app.start_check(packet, "token")
    result = app.complete_check(packet, "token", check_id, "success", "PASS")
    assert result["id"] == 99
    assert json.loads(requests[0].data)["status"] == "in_progress"
    assert requests[1].method == "PATCH"


def test_github_outage_leaves_no_check() -> None:
    calls = 0

    def offline(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise urllib.error.URLError("offline")

    packet = EvidencePacket("mbh-solutions/supportability-gate", "a" * 40, "b" * 40, 42, {})
    app = GitHubApp(42, 7, b"unused", opener=offline)
    with pytest.raises(SemanticReviewError, match="GITHUB_TRANSPORT_FAILURE"):
        app.publish_check(packet, "token", "success", "PASS")
    assert calls == 1
