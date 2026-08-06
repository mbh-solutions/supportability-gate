from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

from supportability_gate import responses_transport
from supportability_gate.semantic_contract import EvidencePacket

BODY = b'{"error":null,"model":"gpt-5.6-sol","output":[],"status":"completed"}'


class Reply:
    def __enter__(self) -> Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return BODY


def main() -> None:
    packet = EvidencePacket("owner/repository", "a" * 40, "b" * 40, 42, {"diff": ""})

    def open_request(_request: urllib.request.Request, *, timeout: float) -> Reply:
        assert timeout == 1.0
        return Reply()

    with tempfile.TemporaryDirectory() as directory:
        arguments = (
            {"diagnostics_root": Path(directory), "check_id": 7}
            if hasattr(responses_transport, "TransportResponse")
            else {}
        )
        response = responses_transport.request_response(
            packet,
            timeout_seconds=1.0,
            opener=open_request,
            **arguments,
        )
        if arguments:
            assert response.body == BODY
            diagnostic = response.diagnostic
            assert diagnostic is not None
            assert (Path(directory) / diagnostic.filename).read_bytes() == BODY
            attempts = list((Path(directory) / "attempts").glob("*.json"))
            assert len(attempts) == 1
            attempt = json.loads(attempts[0].read_text())
            assert attempt["result"] == "RESPONSE_RECEIVED"
            assert attempt["response_sha256"] == hashlib.sha256(BODY).hexdigest()
        status = response["status"]

    print(
        json.dumps(
            {
                "behavior": {
                    "response_sha256": hashlib.sha256(BODY).hexdigest(),
                    "status": status,
                },
                "scenario": "semantic-diagnostics",
                "schema_version": "1.0",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
