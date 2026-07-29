"""Minimal GitHub App authentication and exact-head check publishing."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from supportability_gate.architecture_policy import source_imports
from supportability_gate.contract import ContractError, parse_contract
from supportability_gate.function_changes import PythonSourceError, responsibility_spans
from supportability_gate.semantic_contract import SHA_PATTERN, EvidencePacket, SemanticReviewError

API = "https://api.github.com"
CHECK_NAME = "Supportability Semantic Review"
REVIEWED_SUFFIXES = frozenset({".py", ".ts", ".tsx"})
HUNK_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
SourceBoundary = dict[str, int | str]


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


def _response_bytes(request: urllib.request.Request, opener: Callable[..., Any]) -> bytes:
    try:
        with opener(request, timeout=30) as result:
            return bytes(result.read())
    except (urllib.error.URLError, TimeoutError) as error:
        raise SemanticReviewError("GITHUB_TRANSPORT_FAILURE") from error


def _decoded_response(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SemanticReviewError("GITHUB_MALFORMED_RESPONSE") from error


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
        return _decoded_response(_response_bytes(request, self.opener))

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
        total = result.get("total_count") if isinstance(result, dict) else None
        if not isinstance(runs, list) or not isinstance(total, int):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        if total >= 100:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
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
        if not isinstance(number, int):
            raise SemanticReviewError("MALFORMED_PULL_REQUEST")
        _, files = self._comparison_evidence(repository, str(base_sha), str(head_sha), token)
        reviewed_sources = self._reviewed_sources(repository, str(base_sha), files, token)
        review_diff = self._review_diff(files)
        return EvidencePacket(
            repository,
            str(base_sha),
            str(head_sha),
            self.app_id,
            {
                "diff": review_diff,
                "pull_request": number,
                "refactor_context": self._refactor_context(pull, files),
                "reviewed_sources": reviewed_sources,
            },
        )

    def _refactor_context(
        self, pull: dict[str, Any], files: tuple[dict[str, Any], ...]
    ) -> dict[str, object]:
        """Return authenticated author identity plus exact changed-file metadata."""
        user = pull.get("user")
        author = user if isinstance(user, dict) else {}
        return {
            "author_association": pull.get("author_association"),
            "author_id": author.get("id"),
            "author_login": author.get("login"),
            "authorization": pull.get("body"),
            "changed_files": [
                {"path": item.get("filename"), "status": item.get("status")}
                for item in sorted(files, key=lambda value: str(value.get("filename")))
            ],
        }

    def _comparison_evidence(
        self, repository: str, base_sha: str, head_sha: str, token: str
    ) -> tuple[str, tuple[dict[str, Any], ...]]:
        path = (
            f"/repos/{repository}/compare/"
            f"{urllib.parse.quote(base_sha)}...{urllib.parse.quote(head_sha)}"
        )
        files = self._comparison_files(path, token)
        diff = self._comparison_diff(path, token)
        self._validate_comparison(diff, files)
        return diff, files

    def _comparison_files(self, path: str, token: str) -> tuple[dict[str, Any], ...]:
        comparison = self._request("GET", path, token)
        files = comparison.get("files") if isinstance(comparison, dict) else None
        if not isinstance(files, list) or len(files) >= 300:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        if any(not isinstance(item, dict) for item in files):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        return tuple(files)

    def _comparison_diff(self, path: str, token: str) -> str:
        request = urllib.request.Request(
            f"{API}{path}",
            headers={
                "Accept": "application/vnd.github.v3.diff",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "supportability-gate-semantic-review",
            },
            method="GET",
        )
        try:
            with self.opener(request, timeout=30) as result:
                diff = cast(str, result.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError) as error:
            raise SemanticReviewError("GITHUB_TRANSPORT_FAILURE") from error
        return diff

    def _validate_comparison(self, diff: str, files: tuple[dict[str, Any], ...]) -> None:
        lines = diff.splitlines()
        binary = "GIT binary patch" in lines or any(
            line.startswith("Binary files ") and line.endswith(" differ") for line in lines
        )
        diff_files = sum(line.startswith("diff --git ") for line in lines)
        if (
            not diff
            or binary
            or diff_files != len(files)
            or len(diff.encode("utf-8")) >= 900_000
            or len(lines) >= 18_000
        ):
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")

    def _reviewed_sources(
        self,
        repository: str,
        base_sha: str,
        files: tuple[dict[str, Any], ...],
        token: str,
    ) -> list[dict[str, Any]]:
        candidates = self._source_candidates(files)
        if not candidates:
            return []
        production_paths = self._production_paths(repository, base_sha, token)
        sources = [
            self._reviewed_source(repository, item, token)
            for item in self._production_candidates(candidates, production_paths)
        ]
        self._validate_review_size(sources)
        return sources

    def _production_candidates(
        self, candidates: tuple[dict[str, Any], ...], production_paths: tuple[str, ...]
    ) -> tuple[dict[str, Any], ...]:
        return tuple(
            item
            for item in candidates
            if any(
                item["filename"] == root or item["filename"].startswith(f"{root}/")
                for root in production_paths
            )
        )

    def _validate_review_size(self, sources: list[dict[str, Any]]) -> None:
        if sum(len(source["lines"]) for source in sources) > 2_000:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")

    def _source_candidates(self, files: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
        candidates: list[dict[str, Any]] = []
        for item in files:
            path, status = item.get("filename"), item.get("status")
            if not isinstance(path, str) or not isinstance(status, str):
                raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
            if status != "removed" and any(path.lower().endswith(ext) for ext in REVIEWED_SUFFIXES):
                candidates.append(item)
        return tuple(sorted(candidates, key=lambda item: str(item["filename"])))

    def _reviewed_source(self, repository: str, item: dict[str, Any], token: str) -> dict[str, Any]:
        path, blob_sha, patch = item["filename"], item.get("sha"), item.get("patch")
        if not isinstance(blob_sha, str) or not SHA_PATTERN.fullmatch(blob_sha):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        if not isinstance(patch, str) or not patch:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        content = self._blob_content(repository, blob_sha, token)
        boundaries = self._source_boundaries(path, content, patch)
        return {
            "boundaries": boundaries,
            "blob_sha": blob_sha,
            "imports": [
                {"line": line, "specifier": specifier}
                for line, specifier in source_imports(path, content.encode())
            ],
            "line_count": len(content.splitlines()),
            "lines": self._source_excerpt(content, boundaries),
            "path": path,
        }

    def _source_boundaries(self, path: str, content: str, patch: str) -> list[SourceBoundary]:
        try:
            spans = responsibility_spans(path, content.encode(), self._changed_lines(patch))
        except PythonSourceError as error:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE") from error
        boundaries: list[SourceBoundary] = [
            {
                "end_line": span.end_line,
                "kind": span.kind,
                "name": span.name,
                "start_line": span.start_line,
            }
            for span in spans
        ]
        if not boundaries:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        return boundaries

    def _changed_lines(self, patch: str) -> set[int]:
        changed: set[int] = set()
        head_line: int | None = None
        for line in patch.splitlines():
            match = HUNK_PATTERN.match(line)
            if match:
                head_line = int(match.group(1))
            elif head_line is not None and line.startswith("+"):
                changed.add(head_line)
                head_line += 1
            elif head_line is not None and not line.startswith("-"):
                head_line += 1
        return changed

    def _source_excerpt(
        self, content: str, boundaries: list[SourceBoundary]
    ) -> list[dict[str, object]]:
        source_lines = content.splitlines()
        selected = {
            number
            for boundary in boundaries
            for number in range(
                cast(int, boundary["start_line"]), cast(int, boundary["end_line"]) + 1
            )
        }
        return [
            {"line": number, "text": source_lines[number - 1]}
            for number in range(min(selected), max(selected) + 1)
        ]

    def _review_diff(self, files: tuple[dict[str, Any], ...]) -> str:
        for item in sorted(files, key=lambda value: str(value.get("filename"))):
            path, patch = item.get("filename"), item.get("patch")
            if path == ".supportability-review.toml":
                if not isinstance(patch, str) or not patch:
                    raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
                return f"path: {path}\n{patch}"
        return "No candidate responsibility declaration changed."

    def _production_paths(self, repository: str, base_sha: str, token: str) -> tuple[str, ...]:
        path = f"/repos/{repository}/contents/.supportability.toml?ref={base_sha}"
        result = self._request("GET", path, token)
        blob_sha = result.get("sha") if isinstance(result, dict) else None
        if not isinstance(blob_sha, str) or not SHA_PATTERN.fullmatch(blob_sha):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        try:
            return parse_contract(
                self._blob_content(repository, blob_sha, token).encode()
            ).production_paths
        except ContractError as error:
            raise SemanticReviewError("INVALID_BASE_CONTRACT") from error

    def _blob_content(self, repository: str, blob_sha: str, token: str) -> str:
        result = self._request("GET", f"/repos/{repository}/git/blobs/{blob_sha}", token)
        if not isinstance(result, dict) or result.get("sha") != blob_sha:
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        encoded = result.get("content")
        if result.get("encoding") != "base64" or not isinstance(encoded, str):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        try:
            raw = base64.b64decode("".join(encoded.split()), validate=True)
            content = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as error:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE") from error
        object_bytes = f"blob {len(raw)}\0".encode() + raw
        verified_sha = hashlib.sha1(object_bytes, usedforsecurity=False).hexdigest()
        if verified_sha != blob_sha or not content.splitlines() or len(raw) >= 300_000:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        return content

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
