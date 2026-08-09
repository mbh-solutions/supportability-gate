"""Minimal GitHub App authentication and exact-head check publishing."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from supportability_gate.architecture_policy import source_imports
from supportability_gate.contract import ContractError, parse_contract
from supportability_gate.function_changes import (
    PythonSourceError,
    ResponsibilitySpan,
    changed_responsibility_spans,
    responsibility_spans,
)
from supportability_gate.handoff_policy import (
    HANDOFF_REPORT_PATH,
    CompletionReportError,
    completion_citations,
    parse_completion_report,
)
from supportability_gate.review_state import normalize_review_state
from supportability_gate.semantic_contract import (
    SHA_PATTERN,
    TRUSTED_OWNER_ID,
    EvidencePacket,
    SemanticReviewError,
)

API = "https://api.github.com"
CHECK_NAME = "Supportability Semantic Review"
REVIEWED_SUFFIXES = frozenset({".py", ".pyi", ".cts", ".mts", ".ts", ".tsx"})
HUNK_PATTERN = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
SourceBoundary = dict[str, int | str]
HANDOFF_WORKFLOW_PATH = ".github/workflows/organization-required.yml"
MAX_HANDOFF_BYTES = 5_000_000
MAX_CHECK_SUMMARY_BYTES = 65_535
REVIEW_THREADS_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$name){pullRequest(number:$number){
    reviewThreads(first:100,after:$cursor){nodes{
      id isResolved isOutdated comments(first:100){nodes{id databaseId}
      pageInfo{hasNextPage endCursor}}
    }pageInfo{hasNextPage endCursor}}
  }}
}
"""
THREAD_COMMENTS_QUERY = """
query($thread:ID!,$cursor:String){node(id:$thread){... on PullRequestReviewThread{
  comments(first:100,after:$cursor){nodes{id databaseId}pageInfo{hasNextPage endCursor}}
}}}
"""
CLOSING_ISSUES_QUERY = """
query($owner:String!,$name:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$name){pullRequest(number:$number){
    closingIssuesReferences(first:100,after:$cursor){nodes{
      number title body url updatedAt repository{nameWithOwner}
    }pageInfo{hasNextPage endCursor}}
  }}
}
"""


class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS artifact redirects without leaking the GitHub bearer token."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        if urllib.parse.urlparse(new_url).scheme != "https":
            raise SemanticReviewError("GITHUB_TRANSPORT_FAILURE")
        redirected = super().redirect_request(
            request, file_pointer, code, message, headers, new_url
        )
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


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


def _check_summary(packet: EvidencePacket, summary: str) -> str:
    summary = summary.encode("utf-8", errors="backslashreplace").decode()
    suffix = (
        f"\n\nEvidence SHA-256: `{packet.sha256}`\n"
        f"Instruction SHA-256: `{packet.instruction_sha256}`\n"
        f"Base: `{packet.base_sha}`\nHead: `{packet.head_sha}`"
    )
    full = summary + suffix
    if len(full.encode()) <= MAX_CHECK_SUMMARY_BYTES:
        return full
    marker = (
        "\n\nGitHub output truncated; full summary SHA-256: "
        f"`{hashlib.sha256(summary.encode()).hexdigest()}`"
    )
    budget = MAX_CHECK_SUMMARY_BYTES - len((marker + suffix).encode())
    prefix = summary.encode()[:budget].decode("utf-8", errors="ignore")
    return prefix.rstrip() + marker + suffix


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

    def pull(self, repository: str, pull_number: int, token: str) -> dict[str, Any]:
        """Return current authenticated pull-request metadata for event reconciliation."""
        result = self._request("GET", f"/repos/{repository}/pulls/{pull_number}", token)
        if not isinstance(result, dict) or result.get("number") != pull_number:
            raise SemanticReviewError("MALFORMED_PULL_REQUEST")
        if result.get("state") != "open":
            raise SemanticReviewError("STALE_EVIDENCE")
        return result

    def assert_current(self, packet: EvidencePacket, pull_number: int, token: str) -> None:
        """Reject evidence if pull-request authority, commits, or review state changed."""
        pull = self._request("GET", f"/repos/{packet.repository}/pulls/{pull_number}", token)
        if (
            not isinstance(pull, dict)
            or pull.get("number") != pull_number
            or pull.get("state") != "open"
        ):
            raise SemanticReviewError("STALE_EVIDENCE")
        try:
            base_sha = pull["base"]["sha"]
            head_sha = pull["head"]["sha"]
        except (KeyError, TypeError) as error:
            raise SemanticReviewError("MALFORMED_PULL_REQUEST") from error
        if base_sha != packet.base_sha or head_sha != packet.head_sha:
            raise SemanticReviewError("STALE_EVIDENCE")
        if "authority" in packet.evidence and self._authority(
            packet.repository, pull_number, pull, token
        ) != packet.evidence.get("authority"):
            raise SemanticReviewError("STALE_EVIDENCE")
        current = self._review_state(
            packet.repository,
            pull_number,
            self.issue_comments(packet.repository, pull_number, token),
            token,
        )
        if current != packet.evidence.get("review_state"):
            raise SemanticReviewError("STALE_EVIDENCE")

    def _check_runs(self, packet: EvidencePacket, token: str) -> tuple[dict[str, Any], ...]:
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
        if any(not isinstance(run, dict) for run in runs):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        return tuple(runs)

    def replay_result(self, packet: EvidencePacket, token: str) -> bool | None:
        """Reuse one trusted exact-evidence App verdict instead of recalling the model."""
        conclusions = {
            run.get("conclusion")
            for run in self._check_runs(packet, token)
            if run.get("external_id") == packet.sha256
            and isinstance(run.get("app"), dict)
            and run["app"].get("id") == self.app_id
            and run.get("status") == "completed"
            and run.get("conclusion") in {"success", "failure"}
        }
        if not conclusions:
            return None
        if len(conclusions) != 1:
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
        diff, files = self._comparison_evidence(repository, str(base_sha), str(head_sha), token)
        reviewed_sources, deleted_sources = self._source_evidence(
            repository, str(base_sha), files, token
        )
        comments = self.issue_comments(repository, number, token)
        review_state = self._review_state(repository, number, comments, token)
        return EvidencePacket(
            repository,
            str(base_sha),
            str(head_sha),
            self.app_id,
            {
                "authority": self._authority(repository, number, pull, token),
                "diff": diff,
                "deleted_sources": deleted_sources,
                "pull_request": number,
                "refactor_context": self._refactor_context(pull, files, comments),
                "review_state": review_state,
                "reviewed_sources": reviewed_sources,
            },
        )

    def m10_evidence_packet(
        self,
        repository: str,
        pull: dict[str, Any],
        token: str,
        packet: EvidencePacket | None = None,
    ) -> EvidencePacket:
        """Add authenticated M10 report and workflow evidence to one exact-head packet."""
        packet = packet or self.evidence_packet(repository, pull, token)
        evidence = packet.evidence
        if not evidence.get("reviewed_sources") and not evidence.get("deleted_sources"):
            return packet
        evidence.update(self._handoff_evidence(repository, packet.base_sha, packet.head_sha, token))
        evidence.update(self._completion_report(repository, packet.head_sha, token))
        context = evidence.get("refactor_context")
        changed_files = context.get("changed_files") if isinstance(context, dict) else None
        evidence["completion_sources"] = self._completion_sources(
            repository,
            packet.head_sha,
            evidence.get("completion_report"),
            evidence.get("reviewed_sources"),
            evidence.get("deleted_sources"),
            changed_files,
            token,
        )
        return EvidencePacket(
            packet.repository,
            packet.base_sha,
            packet.head_sha,
            packet.app_id,
            evidence,
            model=packet.model,
            reasoning_effort=packet.reasoning_effort,
        )

    def _handoff_runs(
        self, repository: str, head_sha: str, token: str
    ) -> tuple[dict[str, Any], ...]:
        result = self._request(
            "GET",
            f"/repos/{repository}/actions/runs?head_sha={head_sha}&per_page=100",
            token,
        )
        rows = result.get("workflow_runs") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) >= 100:
            raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE")
        runs = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("path") == HANDOFF_WORKFLOW_PATH
            and row.get("head_sha") == head_sha
            and row.get("event") == "pull_request"
            and row.get("status") == "completed"
        ]
        if not runs:
            raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE")
        ordered = tuple(sorted(runs, key=self._handoff_run_order, reverse=True))
        if ordered[0].get("conclusion") != "success":
            raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE")
        return tuple(run for run in ordered if run.get("conclusion") == "success")

    @staticmethod
    def _handoff_run_order(run: dict[str, Any]) -> tuple[datetime, int]:
        """Validate and order successful exact-head workflow attempts."""
        updated_at, run_id, run_attempt = (
            run.get("updated_at"),
            run.get("id"),
            run.get("run_attempt"),
        )
        if (
            not isinstance(updated_at, str)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", updated_at) is None
            or type(run_id) is not int
            or run_id < 1
            or type(run_attempt) is not int
            or run_attempt < 1
        ):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        try:
            updated = datetime.fromisoformat(updated_at[:-1] + "+00:00")
        except ValueError as error:
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE") from error
        return updated, run_id

    def _handoff_artifact(self, repository: str, run: dict[str, Any], token: str) -> dict[str, Any]:
        run_id, run_attempt = run["id"], run["run_attempt"]
        result = self._request(
            "GET", f"/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100", token
        )
        rows = result.get("artifacts") if isinstance(result, dict) else None
        expected = f"supportability-evidence-{run_id}-{run_attempt}"
        if not isinstance(rows, list) or len(rows) >= 100:
            raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE")
        artifacts = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("name") == expected and not row.get("expired")
        ]
        if len(artifacts) != 1 or not isinstance(artifacts[0].get("id"), int):
            raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE")
        digest = artifacts[0].get("digest")
        if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        return artifacts[0]

    def _artifact_bytes(self, repository: str, artifact_id: int, token: str) -> bytes:
        request = urllib.request.Request(
            f"{API}/repos/{repository}/actions/artifacts/{artifact_id}/zip",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "supportability-gate-semantic-review",
            },
        )
        opener = (
            urllib.request.build_opener(_NoAuthRedirect()).open
            if self.opener is urllib.request.urlopen
            else self.opener
        )
        try:
            with opener(request, timeout=30) as result:
                content = cast(bytes, result.read())
        except (urllib.error.URLError, TimeoutError) as error:
            raise SemanticReviewError("GITHUB_TRANSPORT_FAILURE") from error
        if not content or len(content) > MAX_HANDOFF_BYTES:
            raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE")
        return content

    def _artifact_json(self, archive: bytes) -> dict[str, dict[str, Any]]:
        required = {"complexity-result.json", "quality-provenance.json"}
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                members = bundle.infolist()
                names = [member.filename for member in members]
                indexed = {member.filename: member for member in members}
                if (
                    len(names) > 20
                    or len(names) != len(indexed)
                    or not required <= set(names)
                    or any(indexed[name].file_size > MAX_HANDOFF_BYTES for name in required)
                ):
                    raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE")
                values = {name: json.loads(bundle.read(name)) for name in required}
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE") from error
        if any(not isinstance(value, dict) for value in values.values()):
            raise SemanticReviewError("HANDOFF_EVIDENCE_UNAVAILABLE")
        return values

    def _handoff_evidence(
        self, repository: str, base_sha: str, head_sha: str, token: str
    ) -> dict[str, object]:
        for run in self._handoff_runs(repository, head_sha, token):
            artifact = self._handoff_artifact(repository, run, token)
            archive = self._artifact_bytes(repository, artifact["id"], token)
            archive_sha256 = hashlib.sha256(archive).hexdigest()
            if artifact["digest"] != f"sha256:{archive_sha256}":
                raise SemanticReviewError("HANDOFF_ARTIFACT_DIGEST_MISMATCH")
            files = self._artifact_json(archive)
            result, provenance = files["complexity-result.json"], files["quality-provenance.json"]
            if (result.get("base_sha"), result.get("head_sha")) != (base_sha, head_sha):
                continue
            if provenance.get("run_id") != str(run["id"]):
                raise SemanticReviewError("STALE_HANDOFF_EVIDENCE")
            if provenance.get("run_attempt") != str(run["run_attempt"]):
                raise SemanticReviewError("STALE_HANDOFF_EVIDENCE")
            return {
                "artifact_provenance": {
                    "artifact_digest": artifact["digest"],
                    "artifact_id": artifact["id"],
                    "archive_sha256": archive_sha256,
                    "run_attempt": run["run_attempt"],
                    "run_conclusion": run.get("conclusion"),
                    "run_id": run["id"],
                    "workflow_path": HANDOFF_WORKFLOW_PATH,
                },
                "authoritative_result": result,
            }
        raise SemanticReviewError("STALE_HANDOFF_EVIDENCE")

    def _completion_report(self, repository: str, head_sha: str, token: str) -> dict[str, object]:
        path = (
            f"/repos/{repository}/contents/{HANDOFF_REPORT_PATH}?ref={urllib.parse.quote(head_sha)}"
        )
        result = self._request("GET", path, token)
        blob_sha = result.get("sha") if isinstance(result, dict) else None
        if not isinstance(blob_sha, str) or not SHA_PATTERN.fullmatch(blob_sha):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        content = self._blob_content(repository, blob_sha, token).encode()
        try:
            report = parse_completion_report(content)
            parser_result = "PASS"
        except CompletionReportError as error:
            report = None
            parser_result = str(error)
        return {
            "completion_report": report,
            "completion_report_provenance": {
                "blob_sha": blob_sha,
                "parser_result": parser_result,
                "path": HANDOFF_REPORT_PATH,
                "resolved_head_sha": head_sha,
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        }

    def _completion_sources(
        self,
        repository: str,
        head_sha: str,
        report: object,
        reviewed_sources: object,
        deleted_sources: object,
        changed_files: object,
        token: str,
    ) -> list[dict[str, Any]]:
        """Bind report citations without expanding semantic review boundaries."""
        if not all(
            isinstance(value, list) for value in (reviewed_sources, deleted_sources, changed_files)
        ):
            raise SemanticReviewError("MALFORMED_REVIEW_EVIDENCE")
        reviewed = cast(list[dict[str, Any]], reviewed_sources)
        deleted = cast(list[dict[str, Any]], deleted_sources)
        changed = cast(list[dict[str, Any]], changed_files)
        sources = [
            {key: source[key] for key in ("blob_sha", "line_count", "lines", "path")}
            for source in reviewed
        ]
        reviewed_paths = {source["path"] for source in reviewed}
        changed_paths = {
            item.get("path")
            for item in changed
            if isinstance(item, dict) and item.get("status") != "removed"
        }
        deleted_paths = {
            source.get("path")
            for source in deleted
            if isinstance(source, dict)
            and isinstance(source.get("path"), str)
            and any(source["path"].lower().endswith(ext) for ext in REVIEWED_SUFFIXES)
        }
        allowed = (changed_paths & deleted_paths) - reviewed_paths
        requested: dict[str, list[tuple[int, int]]] = {}
        for path, start, end in completion_citations(report):
            if path in allowed:
                requested.setdefault(path, []).append((start, end))
        for path, ranges in sorted(requested.items()):
            source = self._completion_source(repository, head_sha, path, ranges, token)
            if source is not None:
                sources.append(source)
        return sources

    def _completion_source(
        self,
        repository: str,
        head_sha: str,
        path: str,
        ranges: list[tuple[int, int]],
        token: str,
    ) -> dict[str, Any] | None:
        result = self._request(
            "GET",
            f"/repos/{repository}/contents/{urllib.parse.quote(path)}"
            f"?ref={urllib.parse.quote(head_sha)}",
            token,
        )
        blob_sha = result.get("sha") if isinstance(result, dict) else None
        if not isinstance(blob_sha, str) or not SHA_PATTERN.fullmatch(blob_sha):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        content = self._blob_content(repository, blob_sha, token)
        lines = content.splitlines()
        merged: list[tuple[int, int]] = []
        for start, end in sorted(set(ranges)):
            if not 1 <= start <= end <= len(lines):
                continue
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        selected = [number for start, end in merged for number in range(start, end + 1)]
        if not selected:
            return None
        return {
            "blob_sha": blob_sha,
            "line_count": len(lines),
            "lines": [{"line": number, "text": lines[number - 1]} for number in selected],
            "path": path,
        }

    def _refactor_context(
        self,
        pull: dict[str, Any],
        files: tuple[dict[str, Any], ...],
        comments: tuple[dict[str, Any], ...],
    ) -> dict[str, object]:
        """Return authenticated author identity plus exact changed-file metadata."""
        user = pull.get("user")
        author = user if isinstance(user, dict) else {}
        return {
            "author_association": pull.get("author_association"),
            "author_id": author.get("id"),
            "author_login": author.get("login"),
            "authorization_comments": [
                {
                    "body": item.get("body"),
                    "id": item.get("id"),
                    "user_id": item.get("user", {}).get("id"),
                }
                for item in sorted(comments, key=lambda value: int(value.get("id", 0)))
                if isinstance(item.get("user"), dict)
            ],
            "changed_files": [
                {"path": item.get("filename"), "status": item.get("status")}
                for item in sorted(files, key=lambda value: str(value.get("filename")))
            ],
            "trusted_owner_id": TRUSTED_OWNER_ID,
        }

    def _authority(
        self, repository: str, pull_number: int, pull: dict[str, Any], token: str
    ) -> dict[str, object]:
        """Return authenticated PR and every GitHub-linked closing issue."""
        pull_fields = {
            "body": pull.get("body") or "",
            "number": pull.get("number"),
            "repository": repository,
            "title": pull.get("title"),
            "updated_at": pull.get("updated_at"),
            "url": pull.get("html_url"),
        }
        if pull_fields["number"] != pull_number or any(
            not isinstance(pull_fields[key], str) for key in ("body", "title", "updated_at", "url")
        ):
            raise SemanticReviewError("MALFORMED_PULL_REQUEST")
        owner, name = repository.split("/", 1)
        cursor: str | None = None
        issues: list[dict[str, object]] = []
        while True:
            result = self._request(
                "POST",
                "/graphql",
                token,
                {
                    "query": CLOSING_ISSUES_QUERY,
                    "variables": {
                        "cursor": cursor,
                        "name": name,
                        "number": pull_number,
                        "owner": owner,
                    },
                },
            )
            page, has_next, cursor = self._graphql_connection(
                result, ("data", "repository", "pullRequest", "closingIssuesReferences")
            )
            issues.extend(self._authority_issue(item) for item in page)
            if not has_next:
                return {
                    "closing_issues": sorted(
                        issues,
                        key=lambda item: (
                            str(item["repository"]),
                            cast(int, item["number"]),
                        ),
                    ),
                    "pull_request": pull_fields,
                }

    def _authority_issue(self, item: dict[str, Any]) -> dict[str, object]:
        repository = item.get("repository")
        fields: dict[str, object] = {
            "body": item.get("body") or "",
            "number": item.get("number"),
            "repository": repository.get("nameWithOwner") if isinstance(repository, dict) else None,
            "title": item.get("title"),
            "updated_at": item.get("updatedAt"),
            "url": item.get("url"),
        }
        if type(fields["number"]) is not int or any(
            not isinstance(fields[key], str)
            for key in ("body", "repository", "title", "updated_at", "url")
        ):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        return fields

    def issue_comments(
        self, repository: str, pull_number: int, token: str
    ) -> tuple[dict[str, Any], ...]:
        """Return authenticated issue comments used for owner authorization."""
        return self._rest_pages(
            f"/repos/{repository}/issues/{pull_number}/comments?per_page=100", token
        )

    def _rest_pages(self, path: str, token: str) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            result = self._request("GET", f"{path}&page={page}", token)
            if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
                raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
            rows.extend(result)
            if len(result) < 100:
                return tuple(rows)
            page += 1

    def _graphql_connection(
        self, result: Any, keys: tuple[str, ...]
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        if not isinstance(result, dict) or result.get("errors"):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        value: Any = result
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if not isinstance(value, dict):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        nodes, page_info = value.get("nodes"), value.get("pageInfo")
        if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        if not isinstance(page_info, dict) or not isinstance(page_info.get("hasNextPage"), bool):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        cursor = page_info.get("endCursor")
        if page_info["hasNextPage"] and (not isinstance(cursor, str) or not cursor):
            raise SemanticReviewError("INCOMPLETE_REVIEW_STATE")
        if cursor is not None and not isinstance(cursor, str):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        return nodes, page_info["hasNextPage"], cursor

    def _thread_comments(
        self,
        thread: dict[str, Any],
        token: str,
    ) -> list[dict[str, Any]]:
        nodes, has_next, cursor = self._graphql_connection(thread, ("comments",))
        while has_next:
            result = self._request(
                "POST",
                "/graphql",
                token,
                {
                    "query": THREAD_COMMENTS_QUERY,
                    "variables": {"thread": thread["id"], "cursor": cursor},
                },
            )
            page, has_next, cursor = self._graphql_connection(result, ("data", "node", "comments"))
            nodes.extend(page)
        return nodes

    def _review_threads(
        self, repository: str, pull_number: int, token: str
    ) -> tuple[dict[str, Any], ...]:
        owner, name = repository.split("/", 1)
        cursor: str | None = None
        threads: list[dict[str, Any]] = []
        while True:
            result = self._request(
                "POST",
                "/graphql",
                token,
                {
                    "query": REVIEW_THREADS_QUERY,
                    "variables": {
                        "owner": owner,
                        "name": name,
                        "number": pull_number,
                        "cursor": cursor,
                    },
                },
            )
            page, has_next, cursor = self._graphql_connection(
                result, ("data", "repository", "pullRequest", "reviewThreads")
            )
            for thread in page:
                thread = dict(thread)
                thread["comments"] = self._thread_comments(thread, token)
                threads.append(thread)
            if not has_next:
                return tuple(threads)

    def _review_state(
        self,
        repository: str,
        pull_number: int,
        top_comments: tuple[dict[str, Any], ...],
        token: str,
    ) -> dict[str, object]:
        reviews = self._rest_pages(
            f"/repos/{repository}/pulls/{pull_number}/reviews?per_page=100", token
        )
        inline = self._rest_pages(
            f"/repos/{repository}/pulls/{pull_number}/comments?per_page=100", token
        )
        return normalize_review_state(
            reviews, self._review_threads(repository, pull_number, token), inline, top_comments
        )

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

    def _source_evidence(
        self,
        repository: str,
        base_sha: str,
        files: tuple[dict[str, Any], ...],
        token: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        candidates = self._source_candidates(files)
        if not candidates:
            return [], []
        production_paths = self._production_paths(repository, base_sha, token)
        production = self._production_candidates(candidates, production_paths)
        sources_by_path: dict[str, dict[str, Any]] = {
            str(source["path"]): source
            for item in production
            if (source := self._reviewed_source(repository, item, token)) is not None
        }
        deleted: list[dict[str, Any]] = []
        for item in production:
            surviving, removed = self._deletion_evidence(repository, base_sha, item, token)
            if surviving is not None:
                sources_by_path[str(surviving["path"])] = surviving
            if removed is not None:
                deleted.append(removed)
        sources = [sources_by_path[path] for path in sorted(sources_by_path)]
        return sources, deleted

    def _deletion_evidence(
        self,
        repository: str,
        base_sha: str,
        item: dict[str, Any],
        token: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        patch = item.get("patch")
        if not isinstance(patch, str) or not patch:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        base_lines = self._changed_lines(patch, "-")
        if not base_lines:
            return None, None
        path = item.get("previous_filename", item.get("filename"))
        head_path, head_blob_sha = item.get("filename"), item.get("sha")
        if not isinstance(path, str) or not isinstance(head_path, str):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        result = self._request(
            "GET",
            f"/repos/{repository}/contents/{urllib.parse.quote(path)}"
            f"?ref={urllib.parse.quote(base_sha)}",
            token,
        )
        blob_sha = result.get("sha") if isinstance(result, dict) else None
        if (
            not isinstance(blob_sha, str)
            or not SHA_PATTERN.fullmatch(blob_sha)
            or not isinstance(head_blob_sha, str)
            or not SHA_PATTERN.fullmatch(head_blob_sha)
        ):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        content = self._blob_content(repository, blob_sha, token)
        head_content = self._blob_content(repository, head_blob_sha, token)
        try:
            surviving, removed = changed_responsibility_spans(
                head_path,
                content.encode(),
                head_content.encode(),
                base_lines,
                self._changed_lines(patch),
            )
        except PythonSourceError as error:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE") from error
        if not surviving and not removed:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        source = (
            self._source_record(head_path, head_blob_sha, head_content, surviving)
            if surviving
            else None
        )
        deleted = (
            {
                "blob_sha": blob_sha,
                "boundaries": self._boundaries(removed),
                "line_count": len(content.splitlines()),
                "path": path,
            }
            if removed
            else None
        )
        return source, deleted

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

    def _source_candidates(self, files: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
        candidates: list[dict[str, Any]] = []
        for item in files:
            path, status = item.get("filename"), item.get("status")
            if not isinstance(path, str) or not isinstance(status, str):
                raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
            if status != "removed" and any(path.lower().endswith(ext) for ext in REVIEWED_SUFFIXES):
                candidates.append(item)
        return tuple(sorted(candidates, key=lambda item: str(item["filename"])))

    def _reviewed_source(
        self, repository: str, item: dict[str, Any], token: str
    ) -> dict[str, Any] | None:
        path, blob_sha, patch = item["filename"], item.get("sha"), item.get("patch")
        if not isinstance(blob_sha, str) or not SHA_PATTERN.fullmatch(blob_sha):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        if not isinstance(patch, str) or not patch:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE")
        content = self._blob_content(repository, blob_sha, token)
        spans = self._responsibility_spans(path, content, patch)
        if not spans:
            return None
        return self._source_record(path, blob_sha, content, spans)

    def _source_record(
        self,
        path: str,
        blob_sha: str,
        content: str,
        spans: tuple[ResponsibilitySpan, ...],
    ) -> dict[str, Any]:
        boundaries = self._boundaries(spans)
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

    def _responsibility_spans(
        self, path: str, content: str, patch: str, side: str = "+"
    ) -> tuple[ResponsibilitySpan, ...]:
        try:
            return responsibility_spans(path, content.encode(), self._changed_lines(patch, side))
        except PythonSourceError as error:
            raise SemanticReviewError("INCOMPLETE_GITHUB_EVIDENCE") from error

    def _boundaries(self, spans: tuple[ResponsibilitySpan, ...]) -> list[SourceBoundary]:
        return [
            {
                "end_line": span.end_line,
                "kind": span.kind,
                "name": span.name,
                "start_line": span.start_line,
            }
            for span in spans
        ]

    def _source_boundaries(
        self, path: str, content: str, patch: str, side: str = "+"
    ) -> list[SourceBoundary]:
        return self._boundaries(self._responsibility_spans(path, content, patch, side))

    def _changed_lines(self, patch: str, side: str = "+") -> set[int]:
        changed: set[int] = set()
        line_number: int | None = None
        group = 3 if side == "+" else 1
        opposite = "-" if side == "+" else "+"
        for line in patch.splitlines():
            match = HUNK_PATTERN.match(line)
            if match:
                line_number = int(match.group(group))
            elif line_number is not None:
                if line.startswith(side):
                    changed.add(line_number)
                if not line.startswith(opposite) and not line.startswith("\\"):
                    line_number += 1
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
                    "summary": _check_summary(packet, summary),
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
        pending = [
            run.get("id")
            for run in self._check_runs(packet, token)
            if run.get("external_id") == packet.sha256
            and isinstance(run.get("app"), dict)
            and run["app"].get("id") == self.app_id
            and run.get("status") == "in_progress"
        ]
        if any(not isinstance(check_id, int) for check_id in pending):
            raise SemanticReviewError("MALFORMED_GITHUB_RESPONSE")
        if pending:
            return max(cast(list[int], pending))
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
                    "summary": (
                        f"Evidence SHA-256: `{packet.sha256}`\n"
                        f"Instruction SHA-256: `{packet.instruction_sha256}`"
                    ),
                },
            },
        )
        app = result.get("app") if isinstance(result, dict) else None
        check_id = result.get("id") if isinstance(result, dict) else None
        if (
            not isinstance(check_id, int)
            or result.get("head_sha") != packet.head_sha
            or result.get("external_id") != packet.sha256
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
                    "summary": _check_summary(packet, summary),
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
