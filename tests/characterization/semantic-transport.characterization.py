from __future__ import annotations

import json
import urllib.request

from supportability_gate.responses_transport import request_response
from supportability_gate.semantic_contract import EvidencePacket


class Reply:
    def __enter__(self) -> Reply:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"error":null,"model":"gpt-5.6-sol","output":[],"status":"completed"}'


def main() -> None:
    observed: dict[str, object] = {}

    def open_request(request: urllib.request.Request, *, timeout: float) -> Reply:
        payload = json.loads(bytes(request.data or b"{}"))
        observed.update(
            {
                "authorization": request.get_header("Authorization") == "Bearer sk-dummy",
                "method": request.method,
                "model": payload.get("model"),
                "timeout_seconds": timeout,
                "url": request.full_url,
            }
        )
        return Reply()

    response = request_response(
        EvidencePacket("owner/repository", "a" * 40, "b" * 40, 42, {"diff": ""}),
        timeout_seconds=1.0,
        opener=open_request,
    )
    observed["response_status"] = response.decoded()["status"]
    print(
        json.dumps(
            {"behavior": observed, "scenario": "semantic-transport", "schema_version": "1.0"},
            separators=(",", ":"),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
