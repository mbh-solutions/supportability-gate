"""Build and restore deterministic, self-verifying qualification evidence bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from supportability_gate import standard_results

BUNDLE_SCHEMA = "supportability-qualification-bundle.v1"
CONTEXT_SCHEMA = "supportability-qualification-context.v1"
MANIFEST_NAME = "manifest.json"
CONTEXT_NAME = "qualification-context.json"
EVIDENCE_PREFIX = "evidence"
RESTORE_COMMAND = (
    "python -P -m supportability_gate.qualification_bundle restore "
    "--bundle qualification-bundle.zip"
)
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_SHA40 = frozenset("0123456789abcdef")
_SHA64 = frozenset("0123456789abcdef")
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


class QualificationBundleError(ValueError):
    """One deterministic qualification-bundle validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RestoredBundle:
    """Verified bundle identity and decision."""

    manifest_sha256: str
    repository: str
    head_sha: str
    run_id: int
    decision: str
    profile: str | None
    case_id: str | None


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise QualificationBundleError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json_bytes(value: bytes, code: str) -> object:
    try:
        return json.loads(value, object_pairs_hook=_reject_duplicate_pairs)
    except QualificationBundleError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise QualificationBundleError(code) from None


def _exact_mapping(value: object, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise QualificationBundleError(code)
    return value


def _nonblank(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise QualificationBundleError(code)
    return value


def _sha(value: object, length: int, code: str) -> str:
    text = _nonblank(value, code)
    alphabet = _SHA40 if length == 40 else _SHA64
    if len(text) != length or any(character not in alphabet for character in text):
        raise QualificationBundleError(code)
    return text


def _positive_int(value: object, code: str) -> int:
    if type(value) is not int or value < 1:
        raise QualificationBundleError(code)
    return value


def _identity(payload: Mapping[str, object]) -> standard_results.RunIdentity:
    code = "MALFORMED_STANDARD_RESULTS_IDENTITY"
    try:
        return standard_results.RunIdentity(
            _nonblank(payload["repository"], code),
            _positive_int(payload["repository_id"], code),
            _sha(payload["base_sha"], 40, code),
            _sha(payload["head_sha"], 40, code),
            _sha(payload["workflow_sha"], 40, code),
            _positive_int(payload["run_id"], code),
            _positive_int(payload["run_attempt"], code),
        )
    except KeyError:
        raise QualificationBundleError(code) from None


def _decision(payload: Mapping[str, object]) -> str:
    entries = payload.get("entries")
    if (
        not isinstance(entries, list)
        or len(entries) != 8
        or any(not isinstance(entry, dict) for entry in entries)
    ):
        raise QualificationBundleError("MALFORMED_STANDARD_RESULTS_ARTIFACT")
    results = {entry.get("result") for entry in entries}
    if "TECHNICAL_FAILURE" in results:
        return "TECHNICAL_FAILURE"
    if "BLOCK" in results:
        return "BLOCK"
    return "PASS"


def _profile(complexity: object) -> str | None:
    if not isinstance(complexity, dict):
        return None
    value = complexity.get("language")
    return value if value in {"python", "typescript", "mixed"} else None


def _regular_files(directory: Path, output: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if path.resolve() == output.resolve():
            continue
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise QualificationBundleError("UNSAFE_EVIDENCE_PATH")
        if path.is_file():
            files.append(path)
    if not files:
        raise QualificationBundleError("EMPTY_EVIDENCE_DIRECTORY")
    return files


def _bounded_bytes(path: Path) -> bytes:
    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise QualificationBundleError("BUNDLE_FILE_TOO_LARGE")
    return path.read_bytes()


def _evidence(files: Sequence[Path], directory: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    total = 0
    for path in files:
        relative = path.relative_to(directory).as_posix()
        if not _safe_name(relative):
            raise QualificationBundleError("UNSAFE_EVIDENCE_PATH")
        content = _bounded_bytes(path)
        total += len(content)
        if total > _MAX_TOTAL_BYTES:
            raise QualificationBundleError("BUNDLE_TOO_LARGE")
        result[relative] = content
    return result


def _safe_name(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _required_json(evidence: Mapping[str, bytes], name: str) -> object:
    try:
        content = evidence[name]
    except KeyError:
        raise QualificationBundleError(
            f"MISSING_{name.upper().replace('-', '_').replace('.', '_')}"
        ) from None
    return _json_bytes(content, f"MALFORMED_{name.upper().replace('-', '_').replace('.', '_')}")


def _optional_json(evidence: Mapping[str, bytes], name: str) -> object | None:
    content = evidence.get(name)
    if content is None:
        return None
    try:
        return _json_bytes(content, f"MALFORMED_{name.upper().replace('-', '_').replace('.', '_')}")
    except QualificationBundleError:
        return None


def _validate_standard_evidence(evidence: Mapping[str, bytes]) -> tuple[dict[str, Any], object]:
    payload = _required_json(evidence, "standard-results.json")
    if not isinstance(payload, dict):
        raise QualificationBundleError("MALFORMED_STANDARD_RESULTS_JSON")
    identity = _identity(payload)
    try:
        standard_results.validate_payload(payload, identity)
    except standard_results.StandardResultsError as error:
        raise QualificationBundleError(error.code) from None
    if _decision(payload) == "TECHNICAL_FAILURE":
        return payload, _optional_json(evidence, "complexity-result.json")
    complexity = _required_json(evidence, "complexity-result.json")
    provenance = _required_json(evidence, "quality-provenance.json")
    try:
        standard_results.validate_handoff_sources(payload, complexity, provenance, identity)
    except standard_results.StandardResultsError as error:
        raise QualificationBundleError(error.code) from None
    return payload, complexity


def _string_sequence(value: object, code: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise QualificationBundleError(code)
    result = [_nonblank(item, code) for item in value]
    if len(result) != len(set(result)):
        raise QualificationBundleError(code)
    return result


def _validate_runtime(value: object) -> None:
    code = "MALFORMED_QUALIFICATION_RUNTIME"
    row = _exact_mapping(
        value,
        {"python", "node", "runner_image", "isolation_image", "lock_sha256"},
        code,
    )
    for key in ("python", "node", "runner_image", "isolation_image"):
        _nonblank(row[key], code)
    _sha(row["lock_sha256"], 64, code)
    if "@sha256:" not in row["isolation_image"]:
        raise QualificationBundleError(code)


def _validate_delivery(value: object, identity: standard_results.RunIdentity) -> None:
    code = "MALFORMED_QUALIFICATION_DELIVERY"
    row = _exact_mapping(
        value,
        {
            "pull_request",
            "merge_sha",
            "workflow_run",
            "artifact_id",
            "artifact_digest",
            "artifact_expires_at",
            "deployed_workflow_sha",
        },
        code,
    )
    _positive_int(row["pull_request"], code)
    if row["workflow_run"] != identity.run_id:
        raise QualificationBundleError("QUALIFICATION_DELIVERY_IDENTITY_MISMATCH")
    for key in ("merge_sha", "deployed_workflow_sha"):
        if row[key] is not None:
            _sha(row[key], 40, code)
    if row["artifact_id"] is not None:
        _positive_int(row["artifact_id"], code)
        _sha(row["artifact_digest"], 64, code)
        _nonblank(row["artifact_expires_at"], code)
    elif row["artifact_digest"] is not None or row["artifact_expires_at"] is not None:
        raise QualificationBundleError(code)


def _validate_rulesets(value: object) -> None:
    code = "MALFORMED_QUALIFICATION_RULESETS"
    if not isinstance(value, list) or not value:
        raise QualificationBundleError(code)
    for item in value:
        row = _exact_mapping(item, {"scope", "retrieved_at", "payload"}, code)
        _nonblank(row["scope"], code)
        _nonblank(row["retrieved_at"], code)
        if not isinstance(row["payload"], dict) or not row["payload"]:
            raise QualificationBundleError(code)


def _validate_context(
    value: object,
    payload: Mapping[str, object],
    complexity: object,
) -> tuple[str, str]:
    code = "MALFORMED_QUALIFICATION_CONTEXT"
    row = _exact_mapping(
        value,
        {
            "schema_version",
            "case_id",
            "profile",
            "classification",
            "source_test_references",
            "runtime",
            "delivery",
            "rulesets",
            "limitations",
            "restore_command",
        },
        code,
    )
    if row["schema_version"] != CONTEXT_SCHEMA or row["restore_command"] != RESTORE_COMMAND:
        raise QualificationBundleError(code)
    case_id = _nonblank(row["case_id"], code)
    profile = _nonblank(row["profile"], code)
    observed_profile = _profile(complexity)
    if profile not in {"python", "typescript", "mixed"}:
        raise QualificationBundleError(code)
    if (
        observed_profile not in {None, profile}
        or (observed_profile is None and _decision(payload) != "TECHNICAL_FAILURE")
        or row["classification"] != _decision(payload)
    ):
        raise QualificationBundleError("QUALIFICATION_CONTEXT_EVIDENCE_MISMATCH")
    _string_sequence(row["source_test_references"], code)
    _string_sequence(row["limitations"], code)
    _validate_runtime(row["runtime"])
    _validate_delivery(row["delivery"], _identity(payload))
    _validate_rulesets(row["rulesets"])
    return case_id, profile


def _manifest(
    evidence: Mapping[str, bytes],
    payload: Mapping[str, object],
    complexity: object,
    context: bytes | None,
) -> dict[str, object]:
    files = [
        {
            "path": f"{EVIDENCE_PREFIX}/{name}",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        for name, content in sorted(evidence.items())
    ]
    context_sha = None if context is None else hashlib.sha256(context).hexdigest()
    return {
        "schema_version": BUNDLE_SCHEMA,
        "decision": _decision(payload),
        "profile": _profile(complexity),
        "identity": {
            "repository": payload["repository"],
            "repository_id": payload["repository_id"],
            "base_sha": payload["base_sha"],
            "head_sha": payload["head_sha"],
            "workflow_sha": payload["workflow_sha"],
            "run_id": payload["run_id"],
            "run_attempt": payload["run_attempt"],
        },
        "files": files,
        "context_sha256": context_sha,
        "restore_command": RESTORE_COMMAND,
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o444) << 16
    return info


def _write_zip(output: Path, entries: Mapping[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", allowZip64=False) as archive:
        for name, content in sorted(entries.items()):
            archive.writestr(_zip_info(name), content, compresslevel=9)


def build_bundle(evidence_directory: Path, output: Path, context_path: Path | None = None) -> str:
    """Build a byte-stable qualification bundle and return its SHA-256."""
    directory = evidence_directory.resolve(strict=True)
    evidence = _evidence(_regular_files(directory, output), directory)
    payload, complexity = _validate_standard_evidence(evidence)
    context = None if context_path is None else _bounded_bytes(context_path)
    if context is not None:
        _validate_context(
            _json_bytes(context, "MALFORMED_QUALIFICATION_CONTEXT"), payload, complexity
        )
    manifest = _canonical(_manifest(evidence, payload, complexity, context))
    entries = {f"{EVIDENCE_PREFIX}/{name}": content for name, content in evidence.items()}
    entries[MANIFEST_NAME] = manifest
    if context is not None:
        entries[CONTEXT_NAME] = context
    _write_zip(output, entries)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def _archive_entries(archive: zipfile.ZipFile) -> dict[str, bytes]:
    names = archive.namelist()
    if len(names) != len(set(names)) or any(not _safe_name(name) for name in names):
        raise QualificationBundleError("UNSAFE_BUNDLE_MEMBER")
    entries: dict[str, bytes] = {}
    total = 0
    for info in archive.infolist():
        if info.is_dir() or info.file_size > _MAX_FILE_BYTES:
            raise QualificationBundleError("UNSAFE_BUNDLE_MEMBER")
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise QualificationBundleError("BUNDLE_TOO_LARGE")
        entries[info.filename] = archive.read(info)
    return entries


def _manifest_files(value: object) -> dict[str, tuple[int, str]]:
    code = "MALFORMED_QUALIFICATION_MANIFEST"
    if not isinstance(value, list) or not value:
        raise QualificationBundleError(code)
    result: dict[str, tuple[int, str]] = {}
    for item in value:
        row = _exact_mapping(item, {"path", "sha256", "size"}, code)
        path = _nonblank(row["path"], code)
        if path in result or not path.startswith(f"{EVIDENCE_PREFIX}/") or not _safe_name(path):
            raise QualificationBundleError(code)
        size = row["size"]
        if type(size) is not int or size < 0:
            raise QualificationBundleError(code)
        result[path] = (size, _sha(row["sha256"], 64, code))
    return result


def _verify_entries(entries: Mapping[str, bytes], files: Mapping[str, tuple[int, str]]) -> None:
    expected = set(files) | {MANIFEST_NAME}
    if CONTEXT_NAME in entries:
        expected.add(CONTEXT_NAME)
    if set(entries) != expected:
        raise QualificationBundleError("BUNDLE_MEMBER_SET_MISMATCH")
    for name, (size, digest) in files.items():
        content = entries[name]
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise QualificationBundleError("BUNDLE_FILE_DIGEST_MISMATCH")


def _validate_manifest(value: object) -> dict[str, Any]:
    code = "MALFORMED_QUALIFICATION_MANIFEST"
    row = _exact_mapping(
        value,
        {
            "schema_version",
            "decision",
            "profile",
            "identity",
            "files",
            "context_sha256",
            "restore_command",
        },
        code,
    )
    if row["schema_version"] != BUNDLE_SCHEMA or row["restore_command"] != RESTORE_COMMAND:
        raise QualificationBundleError(code)
    return row


def restore_bundle(bundle: Path) -> RestoredBundle:
    """Verify a bundle without network access and return its bound decision."""
    try:
        with zipfile.ZipFile(bundle, "r") as archive:
            entries = _archive_entries(archive)
    except (OSError, zipfile.BadZipFile, RuntimeError):
        raise QualificationBundleError("MALFORMED_QUALIFICATION_BUNDLE") from None
    manifest_bytes = entries.get(MANIFEST_NAME)
    if manifest_bytes is None:
        raise QualificationBundleError("MISSING_QUALIFICATION_MANIFEST")
    manifest = _validate_manifest(_json_bytes(manifest_bytes, "MALFORMED_QUALIFICATION_MANIFEST"))
    files = _manifest_files(manifest["files"])
    _verify_entries(entries, files)
    evidence = {name.removeprefix(f"{EVIDENCE_PREFIX}/"): entries[name] for name in sorted(files)}
    payload, complexity = _validate_standard_evidence(evidence)
    identity = _identity(payload)
    if (
        manifest["identity"]
        != {
            "repository": identity.repository,
            "repository_id": identity.repository_id,
            "base_sha": identity.base_sha,
            "head_sha": identity.head_sha,
            "workflow_sha": identity.workflow_sha,
            "run_id": identity.run_id,
            "run_attempt": identity.run_attempt,
        }
        or manifest["decision"] != _decision(payload)
        or manifest["profile"] != _profile(complexity)
    ):
        raise QualificationBundleError("QUALIFICATION_MANIFEST_EVIDENCE_MISMATCH")
    case_id = None
    context_profile = None
    context = entries.get(CONTEXT_NAME)
    if context is not None:
        if hashlib.sha256(context).hexdigest() != manifest["context_sha256"]:
            raise QualificationBundleError("QUALIFICATION_CONTEXT_DIGEST_MISMATCH")
        case_id, context_profile = _validate_context(
            _json_bytes(context, "MALFORMED_QUALIFICATION_CONTEXT"), payload, complexity
        )
    elif manifest["context_sha256"] is not None:
        raise QualificationBundleError("MISSING_QUALIFICATION_CONTEXT")
    return RestoredBundle(
        hashlib.sha256(manifest_bytes).hexdigest(),
        identity.repository,
        identity.head_sha,
        identity.run_id,
        _decision(payload),
        context_profile if context_profile is not None else _profile(complexity),
        case_id,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build a deterministic evidence bundle")
    build.add_argument("--evidence-directory", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--context", type=Path)
    restore = subparsers.add_parser("restore", help="verify a bundle without external services")
    restore.add_argument("--bundle", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the deterministic bundle build or restore command."""
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "build":
            digest = build_bundle(arguments.evidence_directory, arguments.output, arguments.context)
            print(json.dumps({"bundle_sha256": digest}, sort_keys=True))
        else:
            restored = restore_bundle(arguments.bundle)
            print(json.dumps(restored.__dict__, sort_keys=True))
    except (OSError, QualificationBundleError) as error:
        code = error.code if isinstance(error, QualificationBundleError) else "BUNDLE_IO_FAILURE"
        print(code, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
