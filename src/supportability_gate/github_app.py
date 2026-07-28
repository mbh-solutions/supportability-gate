"""Minimal GitHub App authentication and exact-head check publishing."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from supportability_gate.semantic_review import EvidencePacket, SemanticReviewError

API = "https://api.github.com"
CHECK_NAME = "Supportability Semantic Review"


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def app_jwt(app_id: int, private_key: bytes, now: int | None = None) -> str:
    """Create ten-minute RS256 GitHub App JWT."""
    issued = int(time.time()) if now is None else now
    header = _encoded(b'{"alg":"RS256","typ":"JWT"}')
    payload = _encoded(
        json.dumps(
            {"exp": issued + 540, "iat": issued - 60, "iss": str(app_id)},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    try:
        key = serialization.load_pem_private_key(private_key, password=None)
    except (TypeError, ValueError) as error:
        raise SemanticReviewError("INVALID_APP_PRIVATE_KEY") from error
    if not isinstance(key, rsa.RSAPrivateKey):
        raise SemanticReviewError("INVALID_APP_PRIVATE_KEY")
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_encoded(signature)}"


@dataclass
class GitHubApp:
    """Authenticated outbound-only GitHub App client."""

    app_id: int
    installation_id: int
    private_key: bytes
    opener: Callable[..., Any] = urllib.request.urlopen

    def _request(
        self,
        method: str,
        path: str,
        token: str,
        payload: object | None = None,
    ) -> Any:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"{API}{path}",
            data=data,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "supportability-gate-semantic-review",
            },
            method=method,
        )
        try:
            with self.opener(request, timeout=30) as result:
                return json.loads(result.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise SemanticReviewError("GITHUB_TRANSPORT_FAILURE") from error

    def installation_token(self) -> str:
        """Verify App identity, then obtain one installation token."""
        jwt = app_jwt(self.app_id, self.private_key)
        identity = self._request("GET", "/app", jwt)
        if not isinstance(identity, dict) or identity.get("id") != self.app_id:
            raise SemanticReviewError("APP_IDENTITY_MISMATCH")
        result = self._request(
            "POST", f"/app/installations/{self.installation_id}/access_tokens", jwt, {}
        )
        token = result.get("token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise SemanticReviewError("INSTALLATION_AUTHENTICATION_FAILURE")
        return token

    def open_pulls(self, repository: str, token: str) -> tuple[dict[str, Any], ...]:
        """Return open pull requests from GitHub's immutable SHA metadata."""
        result = self._request("GET", f"/repos/{repository}/pulls?state=open&per_page=100", token)
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        if len(result) >= 100:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        return tuple(result)

    def assert_current(self, packet: EvidencePacket, pull_number: int, token: str) -> None:
        """Reject evidence if the pull request moved before publication."""
        pull = self._request("GET", f"/repos/{packet.repository}/pulls/{pull_number}", token)
        try:
            base_sha = pull["base"]["sha"]
            head_sha = pull["head"]["sha"]
        except (KeyError, TypeError) as error:
            raise SemanticReviewError("MALFORMED_PULL_REQUEST") from error
        if base_sha != packet.base_sha or head_sha != packet.head_sha:
            raise SemanticReviewError("STALE_EVIDENCE")

    def replay_result(self, packet: EvidencePacket, token: str) -> bool | None:
        """Reuse one trusted exact-evidence App verdict instead of recalling the model."""
        result = self._request(
            "GET",
            f"/repos/{packet.repository}/commits/{packet.head_sha}/check-runs"
            f"?check_name={urllib.parse.quote(CHECK_NAME)}&per_page=100",
            token,
        )
        runs = result.get("check_runs") if isinstance(result, dict) else None
        if not isinstance(runs, list):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        conclusions = {
            run.get("conclusion")
            for run in runs
            if isinstance(run, dict)
            and run.get("external_id") == packet.sha256
            and isinstance(run.get("app"), dict)
            and run["app"].get("id") == self.app_id
            and run.get("status") == "completed"
        }
        if not conclusions:
            return None
        if len(conclusions) != 1 or not conclusions <= {"success", "failure"}:
            raise SemanticReviewError("CONFLICTING_REPLAY")
        return conclusions.pop() == "success"

    def evidence_packet(self, repository: str, pull: dict[str, Any], token: str) -> EvidencePacket:
        """Build immutable evidence from exact GitHub base/head comparison."""
        try:
            base_sha = pull["base"]["sha"]
            head_sha = pull["head"]["sha"]
            number = pull["number"]
        except (KeyError, TypeError) as error:
            raise SemanticReviewError("MALFORMED_PULL_REQUEST") from error
        comparison = self._request(
            "GET",
            f"/repos/{repository}/compare/{urllib.parse.quote(base_sha)}...{urllib.parse.quote(head_sha)}",
            token,
        )
        files = comparison.get("files") if isinstance(comparison, dict) else None
        if not isinstance(files, list) or len(files) >= 300:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        evidence_files = []
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
            if not isinstance(item.get("patch"), str):
                raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
            evidence_files.append(
                {
                    "filename": item["filename"],
                    "patch": item.get("patch"),
                    "sha": item.get("sha"),
                    "status": item.get("status"),
                }
            )
        return EvidencePacket(
            repository,
            str(base_sha),
            str(head_sha),
            self.app_id,
            {"files": evidence_files, "pull_request": number},
        )

    def publish_check(
        self,
        packet: EvidencePacket,
        token: str,
        conclusion: str,
        summary: str,
    ) -> dict[str, Any]:
        """Publish exact-head result and verify producing App identity."""
        result = self._request(
            "POST",
            f"/repos/{packet.repository}/check-runs",
            token,
            {
                "name": CHECK_NAME,
                "head_sha": packet.head_sha,
                "external_id": packet.sha256,
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": CHECK_NAME,
                    "summary": (
                        f"{summary}\n\nEvidence SHA-256: `{packet.sha256}`\n"
                        f"Base: `{packet.base_sha}`\nHead: `{packet.head_sha}`"
                    ),
                },
            },
        )
        if not isinstance(result, dict) or result.get("head_sha") != packet.head_sha:
            raise SemanticReviewError("CHECK_HEAD_MISMATCH")
        app = result.get("app")
        if not isinstance(app, dict) or app.get("id") != self.app_id:
            raise SemanticReviewError("CHECK_APP_IDENTITY_MISMATCH")
        return result

    def start_check(self, packet: EvidencePacket, token: str) -> int:
        """Publish a pending exact-evidence check before model transport begins."""
        result = self._request(
            "POST",
            f"/repos/{packet.repository}/check-runs",
            token,
            {
                "name": CHECK_NAME,
                "head_sha": packet.head_sha,
                "external_id": packet.sha256,
                "status": "in_progress",
                "output": {
                    "title": CHECK_NAME,
                    "summary": f"Evidence SHA-256: `{packet.sha256}`",
                },
            },
        )
        app = result.get("app") if isinstance(result, dict) else None
        check_id = result.get("id") if isinstance(result, dict) else None
        if (
            not isinstance(check_id, int)
            or result.get("head_sha") != packet.head_sha
            or not isinstance(app, dict)
            or app.get("id") != self.app_id
        ):
            raise SemanticReviewError("CHECK_APP_IDENTITY_MISMATCH")
        return check_id

    def complete_check(
        self,
        packet: EvidencePacket,
        token: str,
        check_id: int,
        conclusion: str,
        summary: str,
    ) -> dict[str, Any]:
        """Complete one previously started exact-evidence check."""
        result = self._request(
            "PATCH",
            f"/repos/{packet.repository}/check-runs/{check_id}",
            token,
            {
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": CHECK_NAME,
                    "summary": (
                        f"{summary}\n\nEvidence SHA-256: `{packet.sha256}`\n"
                        f"Base: `{packet.base_sha}`\nHead: `{packet.head_sha}`"
                    ),
                },
            },
        )
        app = result.get("app") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("head_sha") != packet.head_sha
            or result.get("external_id") != packet.sha256
            or not isinstance(app, dict)
            or app.get("id") != self.app_id
        ):
            raise SemanticReviewError("CHECK_APP_IDENTITY_MISMATCH")
        return result
